from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse
import os
import logging
from datetime import datetime

from core import state
from core.models import ConnectRequest
from services.modbus_service import (
    discover_serial_ports,
    auto_connect,
    connect_client,
    init_csv_file,
    get_latest_data
)
from core.security import rate_limit

router = APIRouter(prefix="/api/v1")
logger = logging.getLogger("PM2230_API")

@router.get("/tunnel-url")
async def get_tunnel_url():
    """Return the Cloudflare Tunnel public URL (or null if not ready yet)."""
    return {"url": state.tunnel_url, "ready": state.tunnel_ready}

@router.post("/datalog/start")
@rate_limit
async def start_logging(request: Request):
    """Start saving data to CSV every second"""
    state.is_logging = True
    logger.info("Data logging started")
    return {"message": "Data logging started"}

@router.post("/datalog/stop")
@rate_limit
async def stop_logging(request: Request):
    """Stop saving data to CSV"""
    state.is_logging = False
    logger.info("Data logging stopped")
    return {"message": "Data logging stopped"}

@router.get("/datalog/status")
@rate_limit
async def logging_status(request: Request):
    """Check if logging is active and get file size and fault record count"""
    size_bytes = 0
    if os.path.exists(state.log_filename):
        size_bytes = os.path.getsize(state.log_filename)
        
    fault_record_count = 0
    if os.path.exists(state.fault_log_filename):
        try:
            with open(state.fault_log_filename, 'r', encoding='utf-8') as f:
                fault_record_count = max(0, sum(1 for line in f) - 1)
        except Exception as e:
            logger.error(f"Error reading fault log line count: {e}")

    return {
        "is_logging": state.is_logging,
        "file_size_kb": round(size_bytes / 1024, 2),
        "fault_record_count": fault_record_count
    }

@router.get("/datalog/download")
@rate_limit
async def download_log(request: Request, type: str = "normal"):
    """Download the generated CSV file (normal or fault)"""
    target_file = state.fault_log_filename if type == "fault" else state.log_filename
    target_name = "PM2230_Fault_Log.csv" if type == "fault" else "PM2230_Data_Log.csv"
    
    if os.path.exists(target_file):
        return FileResponse(path=target_file, filename=target_name, media_type='text/csv')
    else:
        raise HTTPException(status_code=404, detail="Log file not found")


@router.delete("/datalog/clear")
@rate_limit
async def clear_log(request: Request, type: str = "normal"):
    """Clear the contents of the CSV log file (normal or fault)"""
    if type == "fault":
        if os.path.exists(state.fault_log_filename):
            os.remove(state.fault_log_filename)
            logger.info("Fault log file cleared")
        return {"message": "Fault log file cleared"}
    else:
        if os.path.exists(state.log_filename):
            os.remove(state.log_filename)
            logger.info("Normal log file cleared")
        init_csv_file()
        return {"message": "Normal log file cleared"}

@router.get("/ports")
@rate_limit
async def get_serial_ports(request: Request):
    """List serial port candidates for PM2230 connection."""
    return {
        "ports": discover_serial_ports(),
        "defaults": {
            "port": state.DEFAULT_PORT,
            "baudrate": state.DEFAULT_BAUDRATE,
            "slave_id": state.DEFAULT_SLAVE_ID,
            "parity": state.DEFAULT_PARITY,
        },
    }

@router.get("/status")
@rate_limit
async def get_status(request: Request):
    """Check connection status"""
    connected = bool(state.real_client and state.real_client.connected)
    latest = get_latest_data()

    if state.SIMULATE_MODE:
        effective_mode = "simulating"
    else:
        effective_mode = "real" if connected else "not_connected"

    return {
        "connected": connected,
        "mode": effective_mode,
        "simulate_mode": state.SIMULATE_MODE,
        "status": latest.get("status", "NOT_CONNECTED"),
        "port": state.real_client.port if connected else None,
        "baudrate": state.real_client.baudrate if connected else state.DEFAULT_BAUDRATE,
        "slave_id": state.real_client.slave_id if connected else state.DEFAULT_SLAVE_ID,
        "parity": state.real_client.parity if connected else state.DEFAULT_PARITY,
        "last_poll_error": state.last_poll_error,
        "rust_available": state.RUST_AVAILABLE,
        "use_rust": state.USE_RUST,
    }


@router.post("/mode/toggle")
@rate_limit
async def toggle_simulate_mode(request: Request):
    """Toggle between Real Device Mode and Simulation Mode dynamically"""
    state.SIMULATE_MODE = not state.SIMULATE_MODE
    
    state.cached_data = {}
    state.last_poll_error = None

    if state.SIMULATE_MODE:
        if state.real_client:
            try:
                state.real_client.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting while switching to simulate: {e}")
            state.real_client = None
        logger.info("Switched to SIMULATION Mode")
        return {"message": "Switched to Simulation Mode", "simulate_mode": True}
    else:
        logger.info("Switched to REAL Mode. Attempting auto-connect...")
        client, attempts = auto_connect(validate_reading=True)
        if client:
             logger.info(f"Auto-connected to {client.port} after switching mode.")
             return {"message": f"Switched to Real Mode. Connected to {client.port}", "simulate_mode": False}
        else:
             logger.warning("Auto-connect failed after switching to Real Mode.")
             return {"message": "Switched to Real Mode, but no device found. Please check connection.", "simulate_mode": False}


@router.post("/simulator/state")
@rate_limit
async def update_simulator_state(request: Request):
    """Update simulator fault toggles"""
    try:
        body = await request.json()
        for key, value in body.items():
            if key in state.simulator_state:
                state.simulator_state[key] = bool(value)
        return {"status": "success", "simulator_state": state.simulator_state}
    except Exception as e:
        logger.error(f"Error updating simulator state: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/simulator/status")
@rate_limit
async def simulator_status(request: Request):
    """Get current simulator status"""
    return {
        "is_simulating": state.SIMULATE_MODE,
        "state": state.simulator_state
    }

@router.post("/simulator/inject")
@rate_limit
async def inject_fault(request: Request):
    """Inject a specific fault state into the simulator"""
    try:
        body = await request.json()
        fault_type = body.get("type")
        
        # If value is not provided, toggle the current state
        if "value" in body:
            value = bool(body.get("value"))
        else:
            value = not state.simulator_state.get(fault_type, False)
            
        if fault_type in state.simulator_state:
            state.simulator_state[fault_type] = value
            mode = "Activated" if value else "Deactivated"
            logger.info(f"Simulator Fault Injection: {mode} {fault_type}")
            return {
                "status": "success", 
                "message": f"Fault {fault_type} {mode.lower()}",
                "state": state.simulator_state
            }
        else:
            raise HTTPException(status_code=400, detail=f"Unknown fault type: {fault_type}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error injecting fault: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/simulator/reset")
@rate_limit
async def reset_simulator(request: Request):
    """Reset all simulated faults to normal state"""
    try:
        # Reset all keys in simulator_state to False
        for key in state.simulator_state:
            state.simulator_state[key] = False
            
        logger.info("Simulator faults fully reset to normal state.")
        return {
            "status": "success", 
            "message": "All faults cleared",
            "state": state.simulator_state
        }
    except Exception as e:
        logger.error(f"Error resetting simulator: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/auto-connect")
@rate_limit
async def auto_connect_real_device(request: Request, validate: bool = True):
    """Try all discovered serial ports and connect to the first working PM2230."""
    if state.real_client:
        try:
            state.real_client.disconnect()
        except Exception:
            pass
        state.real_client = None

    client, attempts = auto_connect(validate_reading=validate)
    if client:
        state.real_client = client
        logger.info(f"Connected to PM2230 on {state.real_client.port}")
        return {
            "status": "connected",
            "port": state.real_client.port,
            "baudrate": state.real_client.baudrate,
            "slave_id": state.real_client.slave_id,
            "parity": state.real_client.parity,
            "mode": "real",
            "validated": validate,
            "attempts": attempts,
            "message": f"Connected to PM2230 on {state.real_client.port}",
        }

    logger.error(f"Auto-connect failed. Attempts: {attempts}")
    raise HTTPException(
        status_code=500,
        detail={
            "message": "Auto-connect failed. Please verify RS485 wiring/settings.",
            "attempts": attempts,
        },
    )


@router.get("/connect")
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

    if state.real_client:
        try:
            state.real_client.disconnect()
        except Exception:
            pass
        state.real_client = None

    client, reason = connect_client(
        port=connect_params.port,
        baudrate=connect_params.baudrate,
        slave_id=connect_params.slave_id,
        parity=connect_params.parity,
        validate_reading=validate,
    )
    if client:
        state.real_client = client
        logger.info(f"Connected to PM2230 on {state.real_client.port}")
        return {
            "status": "connected",
            "port": state.real_client.port,
            "baudrate": state.real_client.baudrate,
            "slave_id": state.real_client.slave_id,
            "parity": state.real_client.parity,
            "mode": "real",
            "validated": validate,
            "probe_result": reason,
            "message": f"Connected to PM2230 on {state.real_client.port}"
        }

    logger.error(f"Cannot connect to PM2230 on {port}: {reason}")
    raise HTTPException(
        status_code=500,
        detail=f"Cannot connect to PM2230 on {port} ({reason}). Check wiring, parity, slave ID.",
    )


@router.get("/disconnect")
@rate_limit
async def disconnect_real_device(request: Request):
    """Disconnect PM2230"""
    if state.real_client:
        state.real_client.disconnect()
        state.real_client = None
    state.cached_data = {
        "timestamp": datetime.now().isoformat(),
        "status": "NOT_CONNECTED",
    }
    state.last_poll_error = None
    logger.info("Disconnected from PM2230")
    return {"status": "disconnected", "message": "Disconnected from PM2230."}
