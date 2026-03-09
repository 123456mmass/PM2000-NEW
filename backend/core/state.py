from typing import Dict, Any, List, Optional
import asyncio
import os

from pm2230_client import PM2230Client
from predictive_maintenance import PredictiveMaintenance
from predictive_maintenance_external import ExternalPredictiveMaintenance
from energy_management import EnergyManagement

# === Rust Core Availability ===
try:
    import pm2000_core  # noqa: F401
    RUST_AVAILABLE: bool = True
except ImportError:
    RUST_AVAILABLE = False

# If PM2230_NO_RUST=1 is set (e.g. from start-web.bat), disable Rust even if available
USE_RUST: bool = RUST_AVAILABLE and os.getenv("PM2230_NO_RUST", "0") != "1"

def has_rust_core() -> bool:
    """Central check: is Rust core both available AND enabled?"""
    return RUST_AVAILABLE and USE_RUST

# === Global State Variables ===
real_client: Optional[PM2230Client] = None
cached_data: Dict = {}
# Fast track: V/I/P/PF/Freq — updated every ~300ms
fast_data: Dict = {}
# Slow track: THD/Energy/Unbalance — updated every ~1s
slow_data: Dict = {}
polling_task: Optional[asyncio.Task] = None
tunnel_url: Optional[str] = None
tunnel_ready: bool = False

pm_model: Optional[PredictiveMaintenance] = None
external_pm_model: Optional[ExternalPredictiveMaintenance] = None
em_model: Optional[EnergyManagement] = None

current_alerts: Dict = {"status": "OK", "alerts": []}
alerts_lock = asyncio.Lock()
last_active_alerts: Optional[Dict] = None
last_alert_seen_at: float = 0.0
ALERT_RETENTION_SECONDS: float = float(os.getenv("ALERT_RETENTION_SECONDS", "10"))

# === Logging State ===
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

# === Configuration & Defaults ===
DEFAULT_BAUDRATE: int = int(os.getenv("PM2230_BAUDRATE", "9600"))
DEFAULT_SLAVE_ID: int = int(os.getenv("PM2230_SLAVE_ID", "1"))
DEFAULT_PARITY: str = os.getenv("PM2230_PARITY", "E").upper()
DEFAULT_PORT: Optional[str] = os.getenv("PM2230_PORT", "").strip() or None
DEFAULT_API_PORT: int = int(os.getenv("PM2230_API_PORT", "8003"))
SIMULATE_MODE: bool = os.getenv("PM2230_SIMULATE", "0") == "1"

simulator_state = {
    "voltage_sag": False,
    "voltage_swell": False,
    "phase_loss": False,
    "overload": False,
    "unbalance_high": False,
    "harmonics_high": False
}
last_poll_error: Optional[str] = None
