#!/usr/bin/env python3
"""
PM2230 Dashboard Backend API
FastAPI server สำหรับอ่านค่าจาก PM2230 และส่งให้ Dashboard
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from fastapi.responses import FileResponse
import os
import sys
import asyncio
import copy
import csv
import glob
import platform
import random
import math
import time
import logging
from functools import wraps
from collections import defaultdict
import re
import httpx

# Load environment variables from .env file
from dotenv import load_dotenv
if getattr(sys, 'frozen', False):
    # When frozen via PyInstaller, it is unpacked to _MEIPASS
    _env_path = os.path.join(sys._MEIPASS, '.env')
else:
    # When running normally, it is in the current directory
    _env_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(_env_path, override=True)

# Import PM2230 Client
from pm2230_client import PM2230Client
from ai_analyzer import generate_power_summary, generate_english_report

from contextlib import asynccontextmanager

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
# Rate Limiting Implementation
# ============================================================================
class RateLimiter:
    """Simple in-memory rate limiter using sliding window algorithm."""

    def __init__(self, max_requests: int = 10, window_seconds: float = 1.0):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: Dict[str, List[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def is_allowed(self, client_ip: str) -> bool:
        """Check if request is allowed for the given IP."""
        async with self._lock:
            now = time.time()
            window_start = now - self.window_seconds

            # Clean old requests outside the window
            self.requests[client_ip] = [
                req_time for req_time in self.requests[client_ip]
                if req_time > window_start
            ]

            # Check if under limit
            if len(self.requests[client_ip]) >= self.max_requests:
                logger.warning(f"Rate limit exceeded for IP: {client_ip}")
                return False

            # Record this request
            self.requests[client_ip].append(now)
            return True

    def get_client_ip(self, request: Request) -> str:
        """Extract client IP from request, handling proxies."""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

# Global rate limiter instance
rate_limiter = RateLimiter(max_requests=10, window_seconds=1.0)

# AI-specific rate limiter (stricter - 2 requests per second)
ai_rate_limiter = RateLimiter(max_requests=2, window_seconds=1.0)

# LINE Messaging API Configuration
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID = os.getenv("LINE_USER_ID", "")
LINE_MESSAGING_URL = "https://api.line.me/v2/bot/message/push"
last_line_notify_time = 0
last_sent_fault_categories = set()
LINE_NOTIFY_COOLDOWN = 60  # seconds

async def send_line_message(message: str):
    """Send a push message via LINE Messaging API."""
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID:
        logger.warning("LINE Messaging API credentials not fully configured. Skipping.")
        return False
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {
        "to": LINE_USER_ID,
        "messages": [
            {
                "type": "text",
                "text": message
            }
        ]
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(LINE_MESSAGING_URL, headers=headers, json=payload)
            if response.status_code == 200:
                logger.info("LINE push message sent successfully")
                return True
            else:
                logger.error(f"Failed to send LINE message: {response.status_code} {response.text}")
                return False
    except Exception as e:
        logger.error(f"Error sending LINE message: {e}")
        return False

def rate_limit(func):
    """Decorator for rate limiting endpoints (DISABLED)."""
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        return await func(request, *args, **kwargs)
    return wrapper


def ai_rate_limit(func):
    """Decorator for AI-specific rate limiting (DISABLED)."""
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        return await func(request, *args, **kwargs)
    return wrapper

# ============================================================================
# Input Validation Models
# ============================================================================
class ConnectRequest(BaseModel):
    """Request model for connecting to PM2230 with validation."""
    port: str = Field(..., min_length=1, max_length=50, description="Serial port name")
    baudrate: int = Field(..., ge=9600, le=115200, description="Baud rate (9600-115200)")
    slave_id: int = Field(..., ge=1, le=247, description="Modbus slave ID (1-247)")
    parity: str = Field(..., pattern="^[ENO]$", description="Parity: E (Even), N (None), O (Odd)")

    @field_validator('parity')
    @classmethod
    def validate_parity(cls, v):
        v = v.upper()
        if v not in ('E', 'N', 'O'):
            raise ValueError('Parity must be E, N, or O')
        return v

    @field_validator('baudrate')
    @classmethod
    def validate_baudrate(cls, v):
        if v < 9600 or v > 115200:
            raise ValueError('Baudrate must be between 9600 and 115200')
        return v

    @field_validator('slave_id')
    @classmethod
    def validate_slave_id(cls, v):
        if v < 1 or v > 247:
            raise ValueError('Slave ID must be between 1 and 247')
        return v


class AutoConnectRequest(BaseModel):
    """Request model for auto-connect with optional validation."""
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Auto-connect PM2230 and start background polling."""
    global tunnel_url, tunnel_ready

    # ── Start Cloudflare Tunnel in background ──────────────────────────────
    def _start_tunnel():
        global tunnel_url, tunnel_ready
        try:
            from pycloudflared import try_cloudflare
            logger.info("🌐 Starting Cloudflare Tunnel...")
            result = try_cloudflare(port=DEFAULT_API_PORT, metrics_port=0)
            tunnel_url = result.tunnel
            tunnel_ready = True
            logger.info(f"🌐 Tunnel ready: {tunnel_url}")
        except Exception as e:
            logger.warning(f"🌐 Tunnel failed to start: {e}")
            tunnel_ready = True  # mark ready even on failure so bat doesn't hang

    import threading
    threading.Thread(target=_start_tunnel, daemon=True).start()
    global real_client, polling_task
    logger.info(
        f"Auto-connecting PM2230 (baud={DEFAULT_BAUDRATE}, "
        f"slave={DEFAULT_SLAVE_ID}, parity={DEFAULT_PARITY})..."
    )
    real_client, attempts = auto_connect(validate_reading=True)
    if real_client:
        logger.info(f"Connected with live values on {real_client.port}")
    else:
        if attempts:
            logger.warning("Auto-connect failed, attempts:")
            for a in attempts:
                logger.warning(f"    {a['port']}: {a['result']}")
        else:
            logger.warning("Auto-connect failed (no candidate ports)")

    polling_task = asyncio.create_task(poll_modbus_data())

    yield  # Application runs here

    # Shutdown events
    if polling_task:
        polling_task.cancel()
    if real_client:
        real_client.disconnect()
    logger.info("Application shutdown complete")


app = FastAPI(
    title="PM2230 Dashboard API",
    description="API สำหรับอ่านค่าจาก PM2230 Digital Meter",
    version="1.0.0",
    lifespan=lifespan
)

# === Global Variables ===
real_client: Optional[PM2230Client] = None
cached_data: Dict = {}
polling_task: Optional[asyncio.Task] = None
tunnel_url: Optional[str] = None
tunnel_ready: bool = False

# Logging attributes
is_logging: bool = False
log_filename: str = "pm2230_log.csv"
fault_log_filename: str = "pm2230_fault_log.csv"
log_headers = [
    "timestamp", "status", "V_LN1", "V_LN2", "V_LN3", "V_LN_avg", "V_LL12", "V_LL23", "V_LL31", "V_LL_avg",
    "I_L1", "I_L2", "I_L3", "I_N", "I_avg", "Freq",
    "P_L1", "P_L2", "P_L3", "P_Total", "S_L1", "S_L2", "S_L3", "S_Total",
    "Q_L1", "Q_L2", "Q_L3", "Q_Total",
    "THDv_L1", "THDv_L2", "THDv_L3", "THDi_L1", "THDi_L2", "THDi_L3",
    "V_unb", "U_unb", "I_unb",
    "PF_L1", "PF_L2", "PF_L3", "PF_Total",
    "kWh_Total", "kVAh_Total", "kvarh_Total"
]

DEFAULT_BAUDRATE: int = int(os.getenv("PM2230_BAUDRATE", "9600"))
DEFAULT_SLAVE_ID: int = int(os.getenv("PM2230_SLAVE_ID", "1"))
DEFAULT_PARITY: str = os.getenv("PM2230_PARITY", "E").upper()
DEFAULT_PORT: Optional[str] = os.getenv("PM2230_PORT", "").strip() or None
# port for the HTTP API (configurable via environment)
DEFAULT_API_PORT: int = int(os.getenv("PM2230_API_PORT", "8003"))
SIMULATE_MODE: bool = os.getenv("PM2230_SIMULATE", "0") == "1"
last_poll_error: Optional[str] = None


def _unique_order(values: List[str]) -> List[str]:
    seen = set()
    output: List[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            output.append(value)
    return output


def discover_serial_ports() -> List[str]:
    ports: List[str] = []
    if DEFAULT_PORT:
        ports.append(DEFAULT_PORT)

    system = platform.system().lower()
    if "windows" in system:
        ports.extend([f"COM{i}" for i in range(1, 21)])
    else:
        patterns = [
            "/dev/cu.usbserial*",
            "/dev/tty.usbserial*",
            "/dev/cu.usbmodem*",
            "/dev/tty.usbmodem*",
            "/dev/ttyUSB*",
            "/dev/ttyACM*",
        ]
        for pattern in patterns:
            ports.extend(sorted(glob.glob(pattern)))

    return _unique_order(ports)


def has_live_reading(data: Dict) -> bool:
    # Voltage and frequency should never be exactly 0 on a healthy PM2230 line.
    probes = [data.get("V_LN1", 0), data.get("V_LL12", 0), data.get("Freq", 0)]
    for probe in probes:
        try:
            if abs(float(probe)) > 0.5:
                return True
        except (TypeError, ValueError):
            continue
    return False


def connect_client(
    port: str,
    baudrate: int,
    slave_id: int,
    parity: str,
    validate_reading: bool,
) -> Tuple[Optional[PM2230Client], str]:
    client = PM2230Client(port=port, baudrate=baudrate, slave_id=slave_id, parity=parity.upper())
    if not client.connect():
        # try to surface underlying error text from scanner
        err = getattr(client._scanner, "last_error", None)
        logger.error(f"Connection failed: {err or 'serial_open_failed'}")
        return None, err or "serial_open_failed"

    if not validate_reading:
        logger.info(f"Serial connected to {port}")
        return client, "serial_connected"

    try:
        sample = client.read_all_parameters()
        sample_status = str(sample.get("status", "ERROR"))
    except Exception as exc:
        client.disconnect()
        logger.error(f"Probe exception: {exc}")
        return None, f"probe_exception: {exc}"

    if sample_status in {"NOT_CONNECTED", "ERROR"} or sample_status.startswith("ERROR"):
        client.disconnect()
        return None, f"probe_failed ({sample_status})"

    if not has_live_reading(sample):
        client.disconnect()
        return None, "probe_failed (no_live_values)"

    logger.info(f"Connected with live values on {port}")
    return client, "connected_with_live_values"


def auto_connect(
    validate_reading: bool,
    baudrate: int = DEFAULT_BAUDRATE,
    slave_id: int = DEFAULT_SLAVE_ID,
    parity: str = DEFAULT_PARITY,
) -> Tuple[Optional[PM2230Client], List[Dict[str, str]]]:
    attempts: List[Dict[str, str]] = []
    for port in discover_serial_ports():
        client, reason = connect_client(port, baudrate, slave_id, parity, validate_reading)
        attempts.append({"port": port, "result": reason})
        if client:
            return client, attempts
    return None, attempts

def init_csv_file():
    """Create CSV file with headers if it doesn't exist."""
    if not os.path.exists(log_filename):
        with open(log_filename, mode='w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(log_headers)

def generate_simulated_data():
    """Generate realistic fluctuating data for PM2230 using smooth sine waves."""
    global sim_energy_kwh, sim_energy_kvah, sim_energy_kvarh
    if 'sim_energy_kwh' not in globals():
        sim_energy_kwh = 1000.0
        sim_energy_kvah = 1200.0
        sim_energy_kvarh = 500.0

    # Increment energy smoothly
    sim_energy_kwh += 0.02
    sim_energy_kvah += 0.025
    sim_energy_kvarh += 0.01

    # Time-based smooth variations
    t = time.time()
    
    # --- FAULT INJECTION LOGIC ---
    # Cycle resets every 25 seconds. Inject fault between seconds 15-20 of that cycle.
    in_fault = (t % 25) > 15 and (t % 25) < 20
    
    # Base voltage around 230V, with slow sine waves + small noise
    v1 = 230 + math.sin(t * 0.1) * 1.5 + random.uniform(-0.2, 0.2)
    v2 = 229 + math.sin(t * 0.1 + 2) * 1.5 + random.uniform(-0.2, 0.2)
    v3 = 231 + math.sin(t * 0.1 + 4) * 1.5 + random.uniform(-0.2, 0.2)

    # Base current around 10A, faster sine waves + small noise
    i1 = 10 + math.sin(t * 0.2) * 0.8 + random.uniform(-0.1, 0.1)
    i2 = 9.5 + math.sin(t * 0.2 + 2) * 0.8 + random.uniform(-0.1, 0.1)
    i3 = 10.2 + math.sin(t * 0.2 + 4) * 0.8 + random.uniform(-0.1, 0.1)

    if in_fault:
        # INJECT FAULT:
        # 1. Voltage Sag on Phase 1 (Drops below 207V limit to trigger alert)
        v1 = v1 * 0.85  # ~195V
        # 2. Current Unbalance Spike (Phase 3 spikes, causing I_unb > 10%)
        i3 = i3 * 1.8   # ~18A

    # Calculate powers realistically from smooth V and I
    p1, p2, p3 = v1*i1*0.9/1000, v2*i2*0.9/1000, v3*i3*0.9/1000
    s1, s2, s3 = v1*i1/1000, v2*i2/1000, v3*i3/1000
    # Avoid negative sqrt due to floating point precision
    q1 = math.sqrt(max(0, s1**2 - p1**2))
    q2 = math.sqrt(max(0, s2**2 - p2**2))
    q3 = math.sqrt(max(0, s3**2 - p3**2))

    # THD with very slow changes
    thdv1 = 2.5 + math.sin(t * 0.05) * 0.3 + random.uniform(-0.05, 0.05)
    thdv2 = 2.4 + math.sin(t * 0.05 + 2) * 0.3 + random.uniform(-0.05, 0.05)
    thdv3 = 2.6 + math.sin(t * 0.05 + 4) * 0.3 + random.uniform(-0.05, 0.05)

    thdi1 = 25.0 + math.sin(t * 0.08) * 5.0 + random.uniform(-1.0, 1.0)
    thdi2 = 25.0 + math.sin(t * 0.08 + 2) * 5.0 + random.uniform(-1.0, 1.0)
    # Simulate high unbalanced THDi on Phase 3
    thdi3 = 130.0 + math.sin(t * 0.08 + 4) * 10.0 + random.uniform(-2.0, 2.0)
    
    # Calculate real unbalance dynamically based on generated values
    v_avg = (v1 + v2 + v3) / 3
    v_unb = max(abs(v1-v_avg), abs(v2-v_avg), abs(v3-v_avg)) / v_avg * 100
    
    i_avg = (i1 + i2 + i3) / 3
    i_unb = max(abs(i1-i_avg), abs(i2-i_avg), abs(i3-i_avg)) / i_avg * 100

    return {
        "status": "OK",
        "V_LN1": round(v1, 2), "V_LN2": round(v2, 2), "V_LN3": round(v3, 2), "V_LN_avg": round(v_avg, 2),
        "V_LL12": round(v1*1.732, 2), "V_LL23": round(v2*1.732, 2), "V_LL31": round(v3*1.732, 2), "V_LL_avg": round(v_avg*1.732, 2),
        "I_L1": round(i1, 3), "I_L2": round(i2, 3), "I_L3": round(i3, 3), "I_N": round(0.5 + math.sin(t * 0.5) * 0.1, 3), "I_avg": round(i_avg, 3),
        "Freq": round(50.0 + math.sin(t * 0.15) * 0.08, 2),
        "P_L1": round(p1, 3), "P_L2": round(p2, 3), "P_L3": round(p3, 3), "P_Total": round(p1+p2+p3, 3),
        "S_L1": round(s1, 3), "S_L2": round(s2, 3), "S_L3": round(s3, 3), "S_Total": round(s1+s2+s3, 3),
        "Q_L1": round(q1, 3), "Q_L2": round(q2, 3), "Q_L3": round(q3, 3), "Q_Total": round(q1+q2+q3, 3),
        "THDv_L1": round(thdv1, 2), "THDv_L2": round(thdv2, 2), "THDv_L3": round(thdv3, 2),
        "THDi_L1": round(thdi1, 2), "THDi_L2": round(thdi2, 2), "THDi_L3": round(thdi3, 2),
        "V_unb": round(v_unb, 2), "U_unb": round(v_unb*0.8, 2), "I_unb": round(i_unb, 2),
        "PF_L1": 0.9, "PF_L2": 0.9, "PF_L3": 0.9, "PF_Total": 0.9,
        "kWh_Total": round(sim_energy_kwh, 2), "kVAh_Total": round(sim_energy_kvah, 2), "kvarh_Total": round(sim_energy_kvarh, 2)
    }

async def poll_modbus_data():
    """Background task to poll Modbus data every 1 second and cache it."""
    global cached_data, is_logging, last_poll_error

    # Initialize the CSV file once on startup
    init_csv_file()

    while True:
        try:
            if SIMULATE_MODE:
                data = generate_simulated_data()
                cached_data = {**data, "timestamp": datetime.now().isoformat()}
                last_poll_error = None
            elif real_client and real_client.connected:
                data = real_client.read_all_parameters()
                status = str(data.get("status", "ERROR"))

                if status == "NOT_CONNECTED" or status.startswith("ERROR"):
                    last_poll_error = status
                    if cached_data:
                        cached_data = {
                            **cached_data,
                            "timestamp": datetime.now().isoformat(),
                            "status": status,
                        }
                    else:
                        cached_data = {
                            "timestamp": datetime.now().isoformat(),
                            "status": status,
                        }
                else:
                    # Store the latest data safely only when read is usable.
                    cached_data = copy.deepcopy(data)
                    last_poll_error = None
            elif not cached_data:
                cached_data = {
                    "timestamp": datetime.now().isoformat(),
                    "status": "NOT_CONNECTED",
                }

            # Check for alerts to auto-trigger logging
            has_alert = False
            if cached_data and cached_data.get("status") != "NOT_CONNECTED" and cached_data.get("status") != "ERROR":
                current_alerts = check_limits(cached_data)
                has_alert = current_alerts.get("status") == "ALERT"

            # 1. Normal Data Logging (Only when user starts it)
            if is_logging:
                try:
                    with open(log_filename, mode='a', newline='') as file:
                        writer = csv.writer(file)
                        flat_data = get_latest_data()
                        row = [flat_data.get(header, "") for header in log_headers]
                        writer.writerow(row)
                except Exception as log_err:
                    logger.error(f"Error writing to Normal log file: {log_err}")

            # 2. Fault Auto-Logging (Always records when a fault is detected)
            if has_alert:
                try:
                    # Create fault log file with headers if it doesn't exist
                    fault_log_exists = os.path.exists(fault_log_filename)
                    if not fault_log_exists:
                        with open(fault_log_filename, mode='w', newline='') as file:
                            writer = csv.writer(file)
                            writer.writerow(log_headers + ["Fault_Details"])
                            
                    with open(fault_log_filename, mode='a', newline='') as file:
                        writer = csv.writer(file)
                        flat_data = get_latest_data()
                        row = [flat_data.get(header, "") for header in log_headers]
                        
                        # Set status explicitly to "Fault" as requested
                        if len(row) > 1:
                            row[1] = "Fault"
                        
                        # Add fault details to the end of the row
                        fault_details = " | ".join([f"{a['category'].upper()}: {a['message']}" for a in current_alerts.get("alerts", [])])
                        row.append(fault_details)
                        
                        writer.writerow(row)
                        
                        # Send LINE Notification (Smart Logic: Immediate if new fault types, else use cooldown)
                        global last_line_notify_time, last_sent_fault_categories
                        now = time.time()
                        current_fault_categories = set(a['category'] for a in current_alerts.get("alerts", []))
                        
                        is_new_fault_pattern = current_fault_categories != last_sent_fault_categories
                        is_cooldown_expired = now - last_line_notify_time > LINE_NOTIFY_COOLDOWN
                        
                        if is_new_fault_pattern or is_cooldown_expired:
                            alert_msgs = [f"⚠️ {a['category'].upper()}: {a['message']}" for a in current_alerts.get("alerts", [])]
                            full_msg = "\n🚨 [FAULT DETECTED] PM2000\n" + "\n".join(alert_msgs)
                            full_msg += f"\n⏰ เวลา: {datetime.now().strftime('%H:%M:%S')}"
                            
                            # Use create_task to avoid blocking the polling loop
                            asyncio.create_task(send_line_message(full_msg))
                            last_line_notify_time = now
                            last_sent_fault_categories = current_fault_categories
                            
                except Exception as log_err:
                    logger.error(f"Error writing to Fault log file: {log_err}")

        except Exception as e:
            last_poll_error = str(e)
            if cached_data:
                cached_data = {
                    **cached_data,
                    "timestamp": datetime.now().isoformat(),
                    "status": "ERROR",
                }
            else:
                cached_data = {
                    "timestamp": datetime.now().isoformat(),
                    "status": "ERROR",
                }
            logger.error(f"Polling error: {e}")

        # Ensure we poll exactly every 1.0 seconds
        await asyncio.sleep(1.0)

# ============================================================================
# CORS Configuration - Using environment variable for allowed origins
# ============================================================================
ALLOWED_ORIGINS_ENV = os.getenv("ALLOWED_ORIGINS", "")

def parse_allowed_origins() -> List[str]:
    """Parse allowed origins from environment variable.

    Expected format: comma-separated list of origins
    Example: "http://localhost:3000,http://localhost:3002,https://example.com"

    Returns list of origins, or ["*"] if empty (for backward compatibility).
    """
    # "null" is the origin sent by Electron when loading from file://
    origins = ["*", "null", "file://"]
    logger.info(f"Allowed origins configured: {origins}")
    return origins


app.add_middleware(
    CORSMiddleware,
    allow_origins=parse_allowed_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"]
)

# ============================================================================
# === Models ===
# ============================================================================
class ParameterData(BaseModel):
    timestamp: str
    status: str
    V_LN1: float
    V_LN2: float
    V_LN3: float
    V_LN_avg: float = 0.0
    V_LL12: float = 0.0
    V_LL23: float = 0.0
    V_LL31: float = 0.0
    V_LL_avg: float = 0.0
    # Current
    I_L1: float
    I_L2: float
    I_L3: float
    I_N: float
    I_avg: float = 0.0
    # Frequency
    Freq: float
    # Active Power
    P_L1: float
    P_L2: float
    P_L3: float
    P_Total: float
    # Apparent Power
    S_L1: float
    S_L2: float
    S_L3: float
    S_Total: float
    # Reactive Power
    Q_L1: float
    Q_L2: float
    Q_L3: float
    Q_Total: float
    # THD Voltage
    THDv_L1: float
    THDv_L2: float
    THDv_L3: float
    # THD Current
    THDi_L1: float
    THDi_L2: float
    THDi_L3: float
    # Unbalance
    V_unb: float
    I_unb: float
    # Power Factor
    PF_L1: float
    PF_L2: float
    PF_L3: float
    PF_Total: float
    # Energy
    kWh_Total: float
    kVAh_Total: float
    kvarh_Total: float


class DashboardPage1(BaseModel):
    """Page 1: Overview & Basic"""
    timestamp: str
    status: str
    V_LN1: float
    V_LN2: float
    V_LN3: float
    V_LN_avg: float = 0.0
    V_LL12: float = 0.0
    V_LL23: float = 0.0
    V_LL31: float = 0.0
    V_LL_avg: float = 0.0
    I_L1: float
    I_L2: float
    I_L3: float
    I_N: float
    I_avg: float = 0.0
    Freq: float


class DashboardPage2(BaseModel):
    """Page 2: Power"""
    timestamp: str
    status: str
    # Active Power
    P_L1: float
    P_L2: float
    P_L3: float
    P_Total: float
    # Apparent Power
    S_L1: float
    S_L2: float
    S_L3: float
    S_Total: float
    # Reactive Power
    Q_L1: float
    Q_L2: float
    Q_L3: float
    Q_Total: float


class DashboardPage3(BaseModel):
    """Page 3: Power Quality"""
    timestamp: str
    status: str
    # THD Voltage
    THDv_L1: float
    THDv_L2: float
    THDv_L3: float
    # THD Current
    THDi_L1: float
    THDi_L2: float
    THDi_L3: float
    # Unbalance
    V_unb: float
    U_unb: float
    I_unb: float
    # Power Factor
    PF_L1: float
    PF_L2: float
    PF_L3: float
    PF_Total: float


class DashboardPage4(BaseModel):
    """Page 4: Energy"""
    timestamp: str
    status: str
    kWh_Total: float
    kVAh_Total: float
    kvarh_Total: float


# ============================================================================
# === Helper Functions ===
# ============================================================================
def get_latest_data() -> Dict:
    """ดึงข้อมูลล่าสุดจาก Background Memory Cache"""
    if cached_data:
        data = copy.deepcopy(cached_data)
    else:
        data = {
            'timestamp': datetime.now().isoformat(),
            'status': 'NOT_CONNECTED',
            'V_LN1': 0, 'V_LN2': 0, 'V_LN3': 0, 'V_LN_avg': 0,
            'V_LL12': 0, 'V_LL23': 0, 'V_LL31': 0, 'V_LL_avg': 0,
            'I_L1': 0, 'I_L2': 0, 'I_L3': 0, 'I_N': 0, 'I_avg': 0,
            'Freq': 0,
            'P_L1': 0, 'P_L2': 0, 'P_L3': 0, 'P_Total': 0,
            'S_L1': 0, 'S_L2': 0, 'S_L3': 0, 'S_Total': 0,
            'Q_L1': 0, 'Q_L2': 0, 'Q_L3': 0, 'Q_Total': 0,
            'THDv_L1': 0, 'THDv_L2': 0, 'THDv_L3': 0,
            'THDi_L1': 0, 'THDi_L2': 0, 'THDi_L3': 0,
            'V_unb': 0, 'U_unb': 0, 'I_unb': 0,
            'PF_L1': 0, 'PF_L2': 0, 'PF_L3': 0,
            'kWh_Total': 0, 'kVAh_Total': 0, 'kvarh_Total': 0,
        }

    # Calculate Total Power Factor if not available from a specific register
    try:
        p_tot = float(data.get('P_Total', 0))
        s_tot = float(data.get('S_Total', 0))
        if s_tot > 0:
            pf_total = p_tot / s_tot
        else:
            pf_values = [float(data.get('PF_L1', 0)), float(data.get('PF_L2', 0)), float(data.get('PF_L3', 0))]
            active_pfs = [pf for pf in pf_values if abs(pf) > 0.001]
            pf_total = sum(active_pfs) / len(active_pfs) if active_pfs else 0.0
    except (ValueError, TypeError):
        pf_total = 0.0

    return {
        'timestamp': data.get('timestamp', datetime.now().isoformat()),
        # Some Modbus timeout paths can return only timestamp/status.
        # Keep API stable with safe defaults instead of raising KeyError.
        'status': data.get('status', 'NOT_CONNECTED'),
        'V_LN1': data.get('V_LN1', 0),
        'V_LN2': data.get('V_LN2', 0),
        'V_LN3': data.get('V_LN3', 0),
        'V_LN_avg': data.get('V_LN_avg', 0),
        'V_LL12': data.get('V_LL12', 0),
        'V_LL23': data.get('V_LL23', 0),
        'V_LL31': data.get('V_LL31', 0),
        'V_LL_avg': data.get('V_LL_avg', 0),
        'I_L1': data.get('I_L1', 0),
        'I_L2': data.get('I_L2', 0),
        'I_L3': data.get('I_L3', 0),
        'I_N': data.get('I_N', 0),
        'I_avg': data.get('I_avg', 0),
        'Freq': data.get('Freq', 0),
        'P_L1': data.get('P_L1', 0),
        'P_L2': data.get('P_L2', 0),
        'P_L3': data.get('P_L3', 0),
        'P_Total': data.get('P_Total', 0),
        'S_L1': data.get('S_L1', 0),
        'S_L2': data.get('S_L2', 0),
        'S_L3': data.get('S_L3', 0),
        'S_Total': data.get('S_Total', 0),
        'Q_L1': data.get('Q_L1', 0),
        'Q_L2': data.get('Q_L2', 0),
        'Q_L3': data.get('Q_L3', 0),
        'Q_Total': data.get('Q_Total', 0),
        'THDv_L1': data.get('THDv_L1', 0),
        'THDv_L2': data.get('THDv_L2', 0),
        'THDv_L3': data.get('THDv_L3', 0),
        'THDi_L1': data.get('THDi_L1', 0),
        'THDi_L2': data.get('THDi_L2', 0),
        'THDi_L3': data.get('THDi_L3', 0),
        'V_unb': data.get('V_unb', 0),
        'U_unb': data.get('U_unb', 0),
        'I_unb': data.get('I_unb', 0),
        'PF_L1': data.get('PF_L1', 0),
        'PF_L2': data.get('PF_L2', 0),
        'PF_L3': data.get('PF_L3', 0),
        'PF_Total': data.get('PF_Total', pf_total),
        'kWh_Total': data.get('kWh_Total', 0),
        'kVAh_Total': data.get('kVAh_Total', 0),
        'kvarh_Total': data.get('kvarh_Total', 0),
    }


def check_limits(data: Dict) -> Dict:
    """Simple threshold checker used by /api/alerts."""
    alerts: List[Dict[str, str]] = []

    voltage_avg = float(data.get("V_LN_avg", 0) or 0)
    freq = float(data.get("Freq", 0) or 0)
    pf_total = abs(float(data.get("PF_Total", 0) or 0))
    thdv_avg = (
        float(data.get("THDv_L1", 0) or 0)
        + float(data.get("THDv_L2", 0) or 0)
        + float(data.get("THDv_L3", 0) or 0)
    ) / 3.0
    i_unb = float(data.get("I_unb", 0) or 0)

    if voltage_avg and (voltage_avg < 207 or voltage_avg > 253):
        alerts.append({
            "category": "voltage",
            "severity": "high",
            "message": f"Voltage average out of range: {voltage_avg:.1f} V",
        })
    if freq and (freq < 49.5 or freq > 50.5):
        alerts.append({
            "category": "frequency",
            "severity": "high",
            "message": f"Frequency out of range: {freq:.2f} Hz",
        })
    if pf_total and pf_total < 0.9:
        alerts.append({
            "category": "power_factor",
            "severity": "medium",
            "message": f"Power factor low: {pf_total:.3f}",
        })
    if thdv_avg > 5:
        alerts.append({
            "category": "harmonics",
            "severity": "medium",
            "message": f"THDv average high: {thdv_avg:.2f}%",
        })
    if i_unb > 10:
        alerts.append({
            "category": "unbalance",
            "severity": "medium",
            "message": f"Current unbalance high: {i_unb:.2f}%",
        })

    return {
        "count": len(alerts),
        "status": "ALERT" if alerts else "OK",
        "alerts": alerts,
    }


# ============================================================================
# API Endpoints (Versioned: /api/v1/*)
# ============================================================================

@app.get("/api/v1/tunnel-url")
async def get_tunnel_url():
    """Return the Cloudflare Tunnel public URL (or null if not ready yet)."""
    return {"url": tunnel_url, "ready": tunnel_ready}


@app.get("/api/v1/data", response_model=ParameterData)
@rate_limit
async def get_all_data(request: Request):
    """อ่านค่าทั้งหมด 36 Parameters"""
    try:
        data = get_latest_data()
        return data
    except Exception as e:
        logger.error(f"Error in get_all_data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/page1", response_model=DashboardPage1)
@rate_limit
async def get_page1(request: Request):
    """Page 1: Overview & Basic Parameters"""
    try:
        data = get_latest_data()
        return {
            'timestamp': data['timestamp'],
            'status': data['status'],
            'V_LN1': data['V_LN1'],
            'V_LN2': data['V_LN2'],
            'V_LN3': data['V_LN3'],
            'V_LN_avg': data.get('V_LN_avg', 0),
            'V_LL12': data.get('V_LL12', 0),
            'V_LL23': data.get('V_LL23', 0),
            'V_LL31': data.get('V_LL31', 0),
            'V_LL_avg': data.get('V_LL_avg', 0),
            'I_L1': data['I_L1'],
            'I_L2': data['I_L2'],
            'I_L3': data['I_L3'],
            'I_N': data['I_N'],
            'I_avg': data.get('I_avg', 0),
            'Freq': data['Freq']
        }
    except Exception as e:
        logger.error(f"Error in get_page1: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/page2", response_model=DashboardPage2)
@rate_limit
async def get_page2(request: Request):
    """Page 2: Power Parameters"""
    try:
        data = get_latest_data()
        return {
            'timestamp': data['timestamp'],
            'status': data['status'],
            'P_L1': data.get('P_L1', 0),
            'P_L2': data.get('P_L2', 0),
            'P_L3': data.get('P_L3', 0),
            'P_Total': data.get('P_Total', 0),
            'S_L1': data.get('S_L1', 0),
            'S_L2': data.get('S_L2', 0),
            'S_L3': data.get('S_L3', 0),
            'S_Total': data.get('S_Total', 0),
            'Q_L1': data.get('Q_L1', 0),
            'Q_L2': data.get('Q_L2', 0),
            'Q_L3': data.get('Q_L3', 0),
            'Q_Total': data.get('Q_Total', 0)
        }
    except Exception as e:
        logger.error(f"Error in get_page2: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/page3", response_model=DashboardPage3)
@rate_limit
async def get_page3(request: Request):
    """Page 3: Power Quality"""
    try:
        data = get_latest_data()
        return {
            'timestamp': data['timestamp'],
            'status': data['status'],
            'THDv_L1': data.get('THDv_L1', 0),
            'THDv_L2': data.get('THDv_L2', 0),
            'THDv_L3': data.get('THDv_L3', 0),
            'THDi_L1': data.get('THDi_L1', 0),
            'THDi_L2': data.get('THDi_L2', 0),
            'THDi_L3': data.get('THDi_L3', 0),
            'V_unb': data.get('V_unb', 0),
            'U_unb': data.get('U_unb', 0),
            'I_unb': data.get('I_unb', 0),
            'PF_L1': data.get('PF_L1', 0),
            'PF_L2': data.get('PF_L2', 0),
            'PF_L3': data.get('PF_L3', 0),
            'PF_Total': data.get('PF_Total', 0)
        }
    except Exception as e:
        logger.error(f"Error in get_page3: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/page4", response_model=DashboardPage4)
@rate_limit
async def get_page4(request: Request):
    """Page 4: Energy"""
    try:
        data = get_latest_data()
        return {
            'timestamp': data['timestamp'],
            'status': data['status'],
            'kWh_Total': data.get('kWh_Total', 0),
            'kVAh_Total': data.get('kVAh_Total', 0),
            'kvarh_Total': data.get('kvarh_Total', 0)
        }
    except Exception as e:
        logger.error(f"Error in get_page4: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# === Logging API Endpoints ===
# ============================================================================

@app.post("/api/v1/datalog/start")
@rate_limit
async def start_logging(request: Request):
    """Start saving data to CSV every second"""
    global is_logging
    is_logging = True
    logger.info("Data logging started")
    return {"message": "Data logging started"}

@app.post("/api/v1/datalog/stop")
@rate_limit
async def stop_logging(request: Request):
    """Stop saving data to CSV"""
    global is_logging
    is_logging = False
    logger.info("Data logging stopped")
    return {"message": "Data logging stopped"}

@app.get("/api/v1/datalog/status")
@rate_limit
async def logging_status(request: Request):
    """Check if logging is active and get file size and fault record count"""
    global is_logging, log_filename, fault_log_filename
    size_bytes = 0
    if os.path.exists(log_filename):
        size_bytes = os.path.getsize(log_filename)
        
    fault_record_count = 0
    if os.path.exists(fault_log_filename):
        try:
            with open(fault_log_filename, 'r', encoding='utf-8') as f:
                # Subtract 1 for the header row
                fault_record_count = max(0, sum(1 for line in f) - 1)
        except Exception as e:
            logger.error(f"Error reading fault log line count: {e}")

    return {
        "is_logging": is_logging,
        "file_size_kb": round(size_bytes / 1024, 2),
        "fault_record_count": fault_record_count
    }

@app.get("/api/v1/datalog/download")
@rate_limit
async def download_log(request: Request, type: str = "normal"):
    """Download the generated CSV file (normal or fault)"""
    global log_filename, fault_log_filename
    
    target_file = fault_log_filename if type == "fault" else log_filename
    target_name = "PM2230_Fault_Log.csv" if type == "fault" else "PM2230_Data_Log.csv"
    
    if os.path.exists(target_file):
        return FileResponse(path=target_file, filename=target_name, media_type='text/csv')
    else:
        raise HTTPException(status_code=404, detail="Log file not found")


@app.delete("/api/v1/datalog/clear")
@rate_limit
async def clear_log(request: Request, type: str = "normal"):
    """Clear the contents of the CSV log file (normal or fault)"""
    global log_filename, fault_log_filename
    
    if type == "fault":
        if os.path.exists(fault_log_filename):
            os.remove(fault_log_filename)
            logger.info("Fault log file cleared")
        return {"message": "Fault log file cleared"}
    else:
        if os.path.exists(log_filename):
            os.remove(log_filename)
            logger.info("Normal log file cleared")
        init_csv_file()
        return {"message": "Normal log file cleared"}


@app.get("/api/v1/alerts")
@rate_limit
async def get_alerts(request: Request):
    """ตรวจสอบค่าเกินกำหนด"""
    try:
        data = get_latest_data()
        return check_limits(data)
    except Exception as e:
        logger.error(f"Error in get_alerts: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/ports")
@rate_limit
async def get_serial_ports(request: Request):
    """List serial port candidates for PM2230 connection."""
    return {
        "ports": discover_serial_ports(),
        "defaults": {
            "port": DEFAULT_PORT,
            "baudrate": DEFAULT_BAUDRATE,
            "slave_id": DEFAULT_SLAVE_ID,
            "parity": DEFAULT_PARITY,
        },
    }


@app.get("/api/v1/status")
@rate_limit
async def get_status(request: Request):
    """Check connection status"""
    connected = bool(real_client and real_client.connected)
    latest = get_latest_data()

    # Determine exact operational mode
    if SIMULATE_MODE:
        effective_mode = "simulating"
    else:
        effective_mode = "real" if connected else "not_connected"

    return {
        "connected": connected,
        "mode": effective_mode,
        "simulate_mode": SIMULATE_MODE,
        "status": latest.get("status", "NOT_CONNECTED"),
        "port": real_client.port if connected else None,
        "baudrate": real_client.baudrate if connected else DEFAULT_BAUDRATE,
        "slave_id": real_client.slave_id if connected else DEFAULT_SLAVE_ID,
        "parity": real_client.parity if connected else DEFAULT_PARITY,
        "last_poll_error": last_poll_error,
    }


@app.post("/api/v1/mode/toggle")
@rate_limit
async def toggle_simulate_mode(request: Request):
    """Toggle between Real Device Mode and Simulation Mode dynamically"""
    global SIMULATE_MODE, real_client, cached_data, last_poll_error

    SIMULATE_MODE = not SIMULATE_MODE
    
    # Reset states for clean transition
    cached_data = {}
    last_poll_error = None

    if SIMULATE_MODE:
        # Disconnect any real devices if switching to simulate
        if real_client:
            try:
                real_client.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting while switching to simulate: {e}")
            real_client = None
        logger.info("Switched to SIMULATION Mode")
        return {"message": "Switched to Simulation Mode", "simulate_mode": True}
    else:
        # Auto connect to a real device if switching to real mode
        logger.info("Switched to REAL Mode. Attempting auto-connect...")
        client, attempts = auto_connect(validate_reading=True)
        if client:
             logger.info(f"Auto-connected to {client.port} after switching mode.")
             return {"message": f"Switched to Real Mode. Connected to {client.port}", "simulate_mode": False}
        else:
             logger.warning("Auto-connect failed after switching to Real Mode.")
             return {"message": "Switched to Real Mode, but no device found. Please check connection.", "simulate_mode": False}


@app.get("/api/v1/auto-connect")
@rate_limit
async def auto_connect_real_device(request: Request, validate: bool = True):
    """Try all discovered serial ports and connect to the first working PM2230."""
    global real_client

    if real_client:
        try:
            real_client.disconnect()
        except Exception:
            pass
        real_client = None

    client, attempts = auto_connect(validate_reading=validate)
    if client:
        real_client = client
        logger.info(f"Connected to PM2230 on {real_client.port}")
        return {
            "status": "connected",
            "port": real_client.port,
            "baudrate": real_client.baudrate,
            "slave_id": real_client.slave_id,
            "parity": real_client.parity,
            "mode": "real",
            "validated": validate,
            "attempts": attempts,
            "message": f"Connected to PM2230 on {real_client.port}",
        }

    logger.error(f"Auto-connect failed. Attempts: {attempts}")
    raise HTTPException(
        status_code=500,
        detail={
            "message": "Auto-connect failed. Please verify RS485 wiring/settings.",
            "attempts": attempts,
        },
    )


@app.get("/api/v1/connect")
@rate_limit
async def connect_real_device(
    request: Request,
    port: str = "COM3",
    baudrate: int = 9600,
    slave_id: int = 1,
    parity: str = "E",
    validate: bool = True,
):
    """เชื่อมต่อ PM2230 จริงผ่าน RS485"""
    global real_client

    # Input validation using Pydantic model
    try:
        connect_params = ConnectRequest(
            port=port,
            baudrate=baudrate,
            slave_id=slave_id,
            parity=parity
        )
    except ValueError as e:
        logger.warning(f"Invalid connection parameters: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid parameters: {str(e)}")

    # Disconnect existing client if any
    if real_client:
        try:
            real_client.disconnect()
        except Exception:
            pass
        real_client = None

    client, reason = connect_client(
        port=connect_params.port,
        baudrate=connect_params.baudrate,
        slave_id=connect_params.slave_id,
        parity=connect_params.parity,
        validate_reading=validate,
    )
    if client:
        real_client = client
        logger.info(f"Connected to PM2230 on {real_client.port}")
        return {
            "status": "connected",
            "port": real_client.port,
            "baudrate": real_client.baudrate,
            "slave_id": real_client.slave_id,
            "parity": real_client.parity,
            "mode": "real",
            "validated": validate,
            "probe_result": reason,
            "message": f"Connected to PM2230 on {real_client.port}"
        }

    logger.error(f"Cannot connect to PM2230 on {port}: {reason}")
    raise HTTPException(
        status_code=500,
        detail=f"Cannot connect to PM2230 on {port} ({reason}). Check wiring, parity, slave ID, and COM/serial port.",
    )


@app.get("/api/v1/disconnect")
@rate_limit
async def disconnect_real_device(request: Request):
    """Disconnect PM2230"""
    global real_client, cached_data, last_poll_error
    if real_client:
        real_client.disconnect()
        real_client = None
    cached_data = {
        "timestamp": datetime.now().isoformat(),
        "status": "NOT_CONNECTED",
    }
    last_poll_error = None
    logger.info("Disconnected from PM2230")
    return {"status": "disconnected", "message": "Disconnected from PM2230."}


@app.get("/api/v1/parameters")
@rate_limit
async def get_parameters_list(request: Request):
    """แสดงรายการ Parameters ทั้งหมด"""
    from pm2230_client import REGISTER_MAP

    params = []
    for i, (name, info) in enumerate(REGISTER_MAP.items(), 1):
        params.append({
            'no': i,
            'name': name,
            'address': hex(info['address']),
            'scale': info['scale'],
            'unit': info['unit']
        })

    return {'total': len(params), 'parameters': params}

async def get_aggregated_data(samples: int = 6, interval: float = 1.0) -> Dict:
    """รวบรวมข้อมูลตามจำนวนตัวอย่างที่กำหนดแล้วหาค่าเฉลี่ย เพื่อลดความผันผวนของข้อมูล"""
    data_list = []
    logger.info(f"AI: Starting data aggregation ({samples} samples)...")
    
    for i in range(samples):
        data_list.append(get_latest_data())
        if i < samples - 1:
            await asyncio.sleep(interval)
            
    if not data_list:
        return get_latest_data()
        
    # ใช้ตัวอย่างแรกเป็น base
    avg_data = copy.deepcopy(data_list[0])
    
    # รายชื่อฟิลด์ตัวเลขที่ต้องการเฉลี่ย
    numeric_fields = [
        'V_LN1', 'V_LN2', 'V_LN3', 'V_LN_avg', 'V_LL12', 'V_LL23', 'V_LL31', 'V_LL_avg',
        'I_L1', 'I_L2', 'I_L3', 'I_N', 'I_avg', 'Freq',
        'P_L1', 'P_L2', 'P_L3', 'P_Total', 'S_L1', 'S_L2', 'S_L3', 'S_Total',
        'Q_L1', 'Q_L2', 'Q_L3', 'Q_Total',
        'THDv_L1', 'THDv_L2', 'THDv_L3', 'THDi_L1', 'THDi_L2', 'THDi_L3',
        'V_unb', 'U_unb', 'I_unb', 'PF_L1', 'PF_L2', 'PF_L3', 'PF_Total'
    ]
    
    for field in numeric_fields:
        vals = [s.get(field, 0) for s in data_list if isinstance(s.get(field), (int, float))]
        if vals:
            avg_data[field] = round(sum(vals) / len(vals), 3)
            
    avg_data['timestamp'] = datetime.now().isoformat()
    # เก็บสถานะจริงจากตัวอย่างล่าสุด (หรือตัวอย่างแรกถ้าไม่มี)
    actual_status = data_list[-1].get('status', 'ERROR') if data_list else 'ERROR'
    avg_data['status'] = actual_status
    avg_data['is_aggregated'] = True
    avg_data['samples_count'] = len(data_list)
    
    logger.info(f"AI: Data aggregation complete ({len(data_list)} samples)")
    return avg_data

@app.post("/api/v1/ai-summary")
@ai_rate_limit
async def get_ai_summary(request: Request):
    """ส่งข้อมูลล่าสุดให้ AI (Qwen) วิเคราะห์และสรุปผล โดยมีการเฉลี่ยข้อมูล 6 วินาทีก่อนส่ง"""
    
    # Aggregate data for 6 seconds to improve accuracy
    aggregated_data = await get_aggregated_data(samples=6, interval=1.0)
    
    result = await generate_power_summary(aggregated_data)

    # Log cache status
    if result.get("is_cached"):
        logger.info(f"AI Summary returned from cache (key: {result.get('cache_key')})")
    else:
        logger.info(f"AI Summary generated fresh (key: {result.get('cache_key')})")

    return {
        "summary": result.get("summary", ""),
        "is_cached": result.get("is_cached", False),
        "cache_key": result.get("cache_key", ""),
        "is_aggregated": True,
        "samples": 6
    }

@app.delete("/api/v1/ai-summary")
async def clear_ai_summary_cache():
    """ลบ Cache ของ AI เพื่อบังคับให้วิเคราะห์ใหม่"""
    from ai_analyzer import clear_all_cache
    count = clear_all_cache()
    return {"message": "Cache cleared successfully", "entries_removed": count}

@app.post("/api/v1/ai-report/english")
@ai_rate_limit
async def get_ai_english_report(request: Request):
    """ส่งข้อมูลล่าสุดให้ AI เขียนรายงานภาษาอังกฤษแบบเป็นทางการ A4"""
    latest_data = get_latest_data()
    result = await generate_english_report(latest_data)

    if result.get("is_cached"):
        logger.info(f"AI English Report returned from cache (key: {result.get('cache_key')})")
    else:
        logger.info(f"AI English Report generated fresh (key: {result.get('cache_key')})")

    return {
        "summary": result.get("summary", ""),
        "is_cached": result.get("is_cached", False),
        "cache_key": result.get("cache_key", "")
    }

@app.post("/api/v1/test-line-notify")
@rate_limit
async def test_line_notify(request: Request):
    """Test LINE Messaging API configuration."""
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID:
        return {"status": "error", "message": "LINE credentials (TOKEN or USER_ID) are not configured in .env file"}
    
    success = await send_line_message("\n🔔 [TEST] PM2000 System\nทดสอบการเชื่อมต่อระบบ LINE Messaging API สำเร็จ!")
    if success:
        return {"status": "success", "message": "Test message sent"}
    else:
        return {"status": "error", "message": "Failed to send test message. Check your credentials or connection."}

@app.post("/api/v1/ai-fault-summary")
@ai_rate_limit
async def get_ai_fault_summary(request: Request):
    """อ่านข้อมูลจาก Fault Log (10 บรรทัดล่าสุด) แล้วส่งให้ AI วิเคราะห์สาเหตุ"""
    global fault_log_filename
    from ai_analyzer import generate_fault_summary
    
    if not os.path.exists(fault_log_filename):
        return {
            "summary": "❌ ไม่พบไฟล์ประวัติการเกิด Fault (ยังไม่มีข้อมูลฟอลต์ในระบบ)",
            "is_cached": False,
            "cache_key": ""
        }
        
    try:
        fault_records = []
        with open(fault_log_filename, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            if len(lines) <= 1:
                return {
                    "summary": "❌ ไฟล์ประวัติการเกิด Fault ว่างเปล่า",
                    "is_cached": False,
                    "cache_key": ""
                }
            
            # Get header and last 10 lines
            header = lines[0].strip().split(',')
            last_records = lines[-10:] if len(lines) > 10 else lines[1:]
            
            for line in last_records:
                values = line.strip().split(',')
                # Create a dict mapping header to value
                record = {header[i]: values[i] if i < len(values) else "" for i in range(len(header))}
                fault_records.append(record)
                
        # Send the records to the AI analyzer
        result = await generate_fault_summary(fault_records)
        
        if result.get("is_cached"):
            logger.info(f"AI Fault Summary returned from cache")
        else:
            logger.info(f"AI Fault Summary generated fresh")
            
        return result
        
    except Exception as e:
        logger.error(f"Error in get_ai_fault_summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Main Entry Point
# ============================================================================
if __name__ == "__main__":
    import uvicorn
    port = DEFAULT_API_PORT
    logger.info(f"Starting PM2230 Dashboard API on port {port}...")
    logger.info(f"Dashboard: http://localhost:3002")
    logger.info(f"API: http://localhost:{port}")
    logger.info(f"API Docs: http://localhost:{port}/docs")
    logger.info("Mode: Real Device")
    logger.info("Auto connect: /api/v1/auto-connect")
    logger.info("Serial ports: /api/v1/ports")
    # make sure the desired port is free before invoking uvicorn
    import socket

    def _port_free(p: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("0.0.0.0", p))
                return True
            except OSError:
                return False

    if not _port_free(port):
        logger.error(f"Cannot start server: port {port} is already in use.")
        logger.error("Either terminate the process listening on that port or set"
              " PM2230_API_PORT to a different value.")
        sys.exit(1)

    # Serve frontend static files if 'frontend_web' folder exists next to the exe
    # This enables "web mode": run the backend, open browser, done.
    _base_dir = os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else __file__)
    _frontend_web = os.path.join(_base_dir, 'frontend_web')
    if os.path.exists(_frontend_web):
        app.mount("/", StaticFiles(directory=_frontend_web, html=True), name="frontend")
        logger.info(f"\U0001f310 Serving frontend from: {_frontend_web}")
        logger.info(f"\U0001f310 Open browser at: http://localhost:{port}")

    try:
        uvicorn.run(app, host="0.0.0.0", port=port)
    except OSError as exc:
        logger.error(f"Failed to start server: {exc}")
        raise
