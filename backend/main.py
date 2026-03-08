#!/usr/bin/env python3
"""
PM2230 Dashboard Backend API
FastAPI server สำหรับอ่านค่าจาก PM2230 และส่งให้ Dashboard (Modular Structure)
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import os
import sys
import asyncio
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
if getattr(sys, 'frozen', False):
    # When frozen via PyInstaller, it is unpacked to _MEIPASS
    _env_path = os.path.join(sys._MEIPASS, '.env')
else:
    # When running normally, it is in the current directory
    _env_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(_env_path, override=True)

# ============================================================================
# Logging Configuration
# ============================================================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format=LOG_FORMAT
)
logger = logging.getLogger("PM2230_API")

# ============================================================================
# Import Core modules and Routers
# ============================================================================
from core import state
from routes import meter, system, ai, line_webhook
from services.modbus_service import poll_modbus_data, auto_connect
from predictive_maintenance import PredictiveMaintenance
from predictive_maintenance_external import ExternalPredictiveMaintenance
from energy_management import EnergyManagement

# ============================================================================
# Application Lifespan
# ============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Auto-connect PM2230 and start background polling."""
    # Initialize Predictive Maintenance model
    state.pm_model = PredictiveMaintenance()
    logger.info("🤖 Predictive Maintenance model initialized")

    # Initialize External Predictive Maintenance model
    state.external_pm_model = ExternalPredictiveMaintenance()
    logger.info("🌐 External Predictive Maintenance model initialized")

    # Initialize Energy Management model
    state.em_model = EnergyManagement()
    logger.info("⚡ Energy Management model initialized")

    # ── Start Cloudflare Tunnel in background ──────────────────────────────
    def _start_tunnel():
        try:
            from pycloudflared import try_cloudflare
            logger.info("🌐 Starting Cloudflare Tunnel...")
            result = try_cloudflare(port=state.DEFAULT_API_PORT, metrics_port=0)
            state.tunnel_url = result.tunnel
            state.tunnel_ready = True
            logger.info(f"🌐 Tunnel ready: {state.tunnel_url}")
            logger.info(f"📱 Webhook Target: {state.tunnel_url}/api/line/webhook")
            
            # รอให้ Server พร้อมรับ Request ก่อน แล้วค่อยอัปเดต Webhook
            import time
            from routes.line_webhook import set_line_webhook
            webhook = f"{state.tunnel_url.strip()}/api/line/webhook"
            for attempt in range(1, 4):
                time.sleep(10)  # รอ 10 วินาทีให้แน่ใจว่า Server + Tunnel พร้อม
                logger.info(f"📱 Auto-update LINE Webhook attempt {attempt}/3...")
                try:
                    success = asyncio.run(set_line_webhook(webhook))
                    if success:
                        break
                except Exception as e:
                    logger.warning(f"📱 Attempt {attempt} failed: {e}")
        except Exception as e:
            logger.warning(f"🌐 Tunnel failed to start: {e}")
            state.tunnel_ready = True

    import threading
    threading.Thread(target=_start_tunnel, daemon=True).start()

    logger.info(
        f"Auto-connecting PM2230 (baud={state.DEFAULT_BAUDRATE}, "
        f"slave={state.DEFAULT_SLAVE_ID}, parity={state.DEFAULT_PARITY})..."
    )
    client, attempts = auto_connect(validate_reading=True)
    if client:
        state.real_client = client
        logger.info(f"Connected with live values on {state.real_client.port}")
    else:
        if attempts:
            logger.warning("Auto-connect failed, attempts:")
            for a in attempts:
                logger.warning(f"    {a['port']}: {a['result']}")
        else:
            logger.warning("Auto-connect failed (no candidate ports)")

    state.polling_task = asyncio.create_task(poll_modbus_data())

    try:
        yield
    finally:
        if state.polling_task:
            state.polling_task.cancel()
        if state.real_client:
            state.real_client.disconnect()
        if state.external_pm_model:
            await state.external_pm_model.close()
        if state.em_model:
            await state.em_model.close()
        logger.info("Application shutdown complete")

# ============================================================================
# FastAPI Application setup
# ============================================================================
app = FastAPI(
    title="PM2230 Dashboard API",
    description="API สำหรับอ่านค่าจาก PM2230 Digital Meter (Modular)",
    version="1.0.0",
    lifespan=lifespan
)

# CORS configuration
NODE_ENV = os.getenv("NODE_ENV", "development")
api_url = os.getenv("NEXT_PUBLIC_API_URL", "http://localhost:8003")

allowed_origins_str = os.getenv("ALLOWED_ORIGINS")
if allowed_origins_str:
    origins = [origin.strip() for origin in allowed_origins_str.split(',') if origin.strip()]
else:
    if NODE_ENV == "production":
        origins = [
            api_url,
            "http://localhost:3000",
            "http://localhost:8003",
        ]
    else:
        origins = ["*"]

# We can append tunnel URL dynamically, but CORS middleware evaluates statically at startup
# Ideally origins include "*" in dev mode and tunnel url in prod when ready
if state.tunnel_ready and state.tunnel_url and state.tunnel_url not in origins:
    origins.append(state.tunnel_url)
    
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API Routers
app.include_router(meter.router)
app.include_router(system.router)
app.include_router(ai.router)
app.include_router(line_webhook.router)

# ============================================================================
# Build & Mount Frontend Static Files
# ============================================================================
if getattr(sys, 'frozen', False):
    FRONTEND_DIR = os.path.join(sys._MEIPASS, 'frontend_build')
else:
    FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'frontend', 'out')

if os.path.isdir(FRONTEND_DIR):
    logger.info(f"Mounting frontend from: {FRONTEND_DIR}")
    
    class SPAStaticFiles(StaticFiles):
        async def get_response(self, path: str, scope):
            from fastapi import HTTPException
            try:
                return await super().get_response(path, scope)
            except HTTPException as ex:
                if ex.status_code == 404:
                    return await super().get_response("index.html", scope)
                raise
                
    app.mount("/", SPAStaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:
    logger.warning(f"Frontend build directory not found at {FRONTEND_DIR}")
    logger.warning("Please build the frontend: 'cd frontend && npm run build'")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=state.DEFAULT_API_PORT, reload=True)
