import asyncio
import copy
import logging
import time
import os
import csv
import platform
import glob
import math
import random
from typing import Dict, List, Optional, Tuple
from datetime import datetime

import httpx

from core import state
from pm2230_client import PM2230Client
from fault_engine import diagnose_faults

from ai_analyzer import generate_line_fault_analysis

logger = logging.getLogger("PM2230_API")

# ============================================================================
# Core Polling Helpers
# ============================================================================

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
    if state.DEFAULT_PORT:
        ports.append(state.DEFAULT_PORT)

    sys_platform = platform.system().lower()
    if "windows" in sys_platform:
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
    baudrate: int = state.DEFAULT_BAUDRATE,
    slave_id: int = state.DEFAULT_SLAVE_ID,
    parity: str = state.DEFAULT_PARITY,
) -> Tuple[Optional[PM2230Client], List[Dict[str, str]]]:
    attempts: List[Dict[str, str]] = []
    for port in discover_serial_ports():
        client, reason = connect_client(port, baudrate, slave_id, parity, validate_reading)
        attempts.append({"port": port, "result": reason})
        if client:
            return client, attempts
    return None, attempts

# ============================================================================
# Alerts & Log Helpers
# ============================================================================

def update_current_alerts(alerts: Optional[Dict], now_ts: Optional[float] = None) -> Dict:
    now_ts = time.time() if now_ts is None else now_ts

    if alerts and alerts.get("status") == "ALERT" and alerts.get("alerts"):
        state.current_alerts = copy.deepcopy(alerts)
        state.current_alerts["active"] = True
        state.current_alerts["retained"] = False
        state.last_active_alerts = copy.deepcopy(state.current_alerts)
        state.last_alert_seen_at = now_ts
    elif state.last_active_alerts and now_ts - state.last_alert_seen_at < state.ALERT_RETENTION_SECONDS:
        state.current_alerts = copy.deepcopy(state.last_active_alerts)
        state.current_alerts["active"] = False
        state.current_alerts["retained"] = True
    else:
        state.current_alerts = {"status": "OK", "alerts": [], "active": False, "retained": False}
        state.last_active_alerts = None

    return copy.deepcopy(state.current_alerts)

LINE_USER_ID = os.getenv("LINE_USER_ID", "")
LINE_MESSAGING_URL = "https://api.line.me/v2/bot/message/push"
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
last_line_notify_time = 0
last_sent_fault_categories = set()
LINE_NOTIFY_COOLDOWN = 60

async def send_line_message(message: str) -> None:
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID:
        logger.warning("Line Messaging API Token or User ID missing. Cannot send push message.")
        return

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
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(LINE_MESSAGING_URL, headers=headers, json=payload)
            if response.status_code == 200:
                logger.info(f"Line notification sent successfully for faults.")
            else:
                logger.error(f"Failed to send Line notification. Status: {response.status_code}, Response: {response.text}")
    except Exception as e:
        logger.error(f"Exception while sending Line notification: {e}")

def check_limits(data: Dict) -> Dict:
    return diagnose_faults(data)

def init_csv_file():
    if not os.path.exists(state.log_filename):
        with open(state.log_filename, mode='w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(state.log_headers)

def calculate_unbalance(v1: float, v2: float, v3: float) -> float:
    avg = (v1 + v2 + v3) / 3
    if avg == 0: return 0.0
    max_diff = max(abs(v1 - avg), abs(v2 - avg), abs(v3 - avg))
    return (max_diff / avg) * 100

sim_energy_kwh = 1000.0
sim_energy_kvah = 1200.0
sim_energy_kvarh = 500.0

def generate_simulated_data():
    global sim_energy_kwh, sim_energy_kvah, sim_energy_kvarh
    sim_energy_kwh += 0.02
    sim_energy_kvah += 0.025
    sim_energy_kvarh += 0.01

    t = time.time()
    v1 = 230 + math.sin(t * 0.1) * 1.5 + random.uniform(-0.2, 0.2)
    v2 = 229 + math.sin(t * 0.1 + 2) * 1.5 + random.uniform(-0.2, 0.2)
    v3 = 231 + math.sin(t * 0.1 + 4) * 1.5 + random.uniform(-0.2, 0.2)
    i1 = 10 + math.sin(t * 0.2) * 0.8 + random.uniform(-0.1, 0.1)
    i2 = 9.5 + math.sin(t * 0.2 + 2) * 0.8 + random.uniform(-0.1, 0.1)
    i3 = 10.2 + math.sin(t * 0.2 + 4) * 0.8 + random.uniform(-0.1, 0.1)

    if state.simulator_state.get("voltage_sag"):
        v_mult = 0.82
        v1 *= v_mult; v2 *= v_mult; v3 *= v_mult
    if state.simulator_state.get("voltage_swell"):
        v_mult = 1.15
        v1 *= v_mult; v2 *= v_mult; v3 *= v_mult
    if state.simulator_state.get("phase_loss"):
        v1 = random.uniform(5.0, 15.0)
    if state.simulator_state.get("overload"):
        i_mult = 5.5
        i1 *= i_mult; i2 *= i_mult; i3 *= i_mult
        v1 *= 0.80; v2 *= 0.80; v3 *= 0.80
    if state.simulator_state.get("unbalance_high"):
        v1 *= 1.08; v2 *= 0.92
        i1 *= 1.15; i2 *= 0.85

    thdv1 = 2.1 + random.uniform(-0.1, 0.1)
    thdv2 = 2.2 + random.uniform(-0.1, 0.1)
    thdv3 = 2.0 + random.uniform(-0.1, 0.1)
    if state.simulator_state.get("harmonics_high"):
        mult = 5.0
        thdv1 *= mult; thdv2 *= mult; thdv3 *= mult

    thdi1 = 5.5 + random.uniform(-0.5, 0.5)
    thdi2 = 5.8 + random.uniform(-0.5, 0.5)
    thdi3 = 5.4 + random.uniform(-0.5, 0.5)
    v_avg = (v1 + v2 + v3) / 3
    i_avg = (i1 + i2 + i3) / 3

    v_unb = calculate_unbalance(v1, v2, v3)
    i_unb = 1.2 + random.uniform(-0.2, 0.2)

    p1 = (v1 * i1 * 0.9) / 1000.0
    p2 = (v2 * i2 * 0.9) / 1000.0
    p3 = (v3 * i3 * 0.9) / 1000.0

    s1 = (v1 * i1) / 1000.0
    s2 = (v2 * i2) / 1000.0
    s3 = (v3 * i3) / 1000.0

    q1 = math.sqrt(max(0, s1**2 - p1**2))
    q2 = math.sqrt(max(0, s2**2 - p2**2))
    q3 = math.sqrt(max(0, s3**2 - p3**2))

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

def get_latest_data() -> Dict:
    if state.cached_data:
        data = copy.deepcopy(state.cached_data)
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


# ============================================================================
# Fast/Slow split helpers
# ============================================================================

# Fast-track parameter names — critical for page 1 display
_FAST_PARAMS = {
    "V_LN1", "V_LN2", "V_LN3", "V_LN_avg",
    "V_LL12", "V_LL23", "V_LL31", "V_LL_avg",
    "I_L1", "I_L2", "I_L3", "I_N", "I_avg",
    "Freq",
    "P_L1", "P_L2", "P_L3", "P_Total",
    "S_L1", "S_L2", "S_L3", "S_Total",
    "Q_L1", "Q_L2", "Q_L3", "Q_Total",
    "PF_L1", "PF_L2", "PF_L3", "PF_Total",
}

def _read_fast_block(client) -> dict:
    """
    Read only Block r1 (registers 2999-3123) which covers V/I/P/PF/Freq.
    Called in a worker thread via asyncio.to_thread.
    Returns a partial flat dict (fast params + status/timestamp).
    """
    from pm2230_client import PM2230Scanner
    import struct, math

    scanner = client._scanner
    try:
        r1 = scanner.client.read_holding_registers(
            address=2999, count=125, slave=scanner.slave_id
        )
    except Exception as e:
        return {"status": f"ERROR: {e}", "timestamp": datetime.now().isoformat()}

    if r1.isError():
        return {"status": "ERROR: bulk_read_failed", "timestamp": datetime.now().isoformat()}

    result: dict = {"status": "OK", "timestamp": datetime.now().isoformat()}

    def _decode_f32(regs):
        hi, lo = regs[0], regs[1]
        raw = struct.pack(">HH", hi, lo)
        val = struct.unpack(">f", raw)[0]
        return 0.0 if (math.isnan(val) or math.isinf(val)) else round(val, 4)

    for param_name, (address, quantity, scale, unit, _) in PM2230Scanner.REGISTER_MAP.items():
        if param_name not in _FAST_PARAMS:
            continue
        if not (2999 <= address < 2999 + 125):
            continue
        offset = address - 2999
        if quantity == 2 and offset + 2 <= len(r1.registers):
            val = _decode_f32(r1.registers[offset:offset + 2])
            # PM2230 PF lead/lag
            if param_name.startswith("PF_") and val > 1.0:
                val = round(2.0 - val, 4)
            result[param_name] = val

    return result


async def _send_smart_line(alerts: List[Dict], data: Dict, base_msg: str):
    """
    Helper to fetch AI analysis and append to LINE message without blocking.
    """
    try:
        # Prompt AI for analysis with an 8s timeout to ensure prompt delivery
        ai_advice = await asyncio.wait_for(
            generate_line_fault_analysis(alerts, data),
            timeout=8.0
        )
        if ai_advice:
            base_msg += f"\n\n🤖 AI วิเคราะห์:\n{ai_advice}"
    except asyncio.TimeoutError:
        logger.warning("LINE AI analysis timed out (8s)")
    except Exception as e:
        logger.error(f"Error in _send_smart_line AI call: {e}")

    await send_line_message(base_msg)

async def poll_modbus_data():
    global last_line_notify_time, last_sent_fault_categories
    init_csv_file()

    fast_counter = 0          # every 3 fast cycles → run slow read (~1 s total)
    SLOW_EVERY_N = 3          # fast cadence 300ms × 3 ≈ 1s for slow path

    while True:
        loop_start = asyncio.get_event_loop().time()
        try:
            if state.SIMULATE_MODE:
                # Simulation: update at fast cadence, full data each time
                data = generate_simulated_data()
                state.cached_data = {**data, "timestamp": datetime.now().isoformat()}
                state.fast_data = copy.deepcopy(state.cached_data)
                state.slow_data = copy.deepcopy(state.cached_data)
                state.last_poll_error = None

            elif state.real_client and state.real_client.connected:
                # ── Fast track (every cycle ~300ms) ──────────────────────────
                fast = await asyncio.to_thread(_read_fast_block, state.real_client)
                fast_status = str(fast.get("status", "ERROR"))

                if fast_status == "NOT_CONNECTED" or fast_status.startswith("ERROR"):
                    state.last_poll_error = fast_status
                    base = state.cached_data or {}
                    state.cached_data = {
                        **base,
                        "timestamp": datetime.now().isoformat(),
                        "status": fast_status,
                    }
                    async with state.alerts_lock:       # P1-B
                        update_current_alerts(None)
                else:
                    state.fast_data = fast
                    # Merge fast on top of existing cached_data (preserves slow fields)
                    state.cached_data = {
                        **state.cached_data,
                        **fast,
                        "status": "OK",
                    }
                    state.last_poll_error = None

                # ── Slow track (every SLOW_EVERY_N fast cycles ≈ 1s) ─────────
                fast_counter += 1
                if fast_counter >= SLOW_EVERY_N and state.real_client and state.real_client.connected:
                    fast_counter = 0
                    slow_full = await asyncio.to_thread(state.real_client.read_all_parameters)
                    slow_status = str(slow_full.get("status", "ERROR"))
                    if not (slow_status == "NOT_CONNECTED" or slow_status.startswith("ERROR")):
                        # Extract slow-only params (THD, Energy, Unbalance)
                        slow_fields = {
                            k: v for k, v in slow_full.items()
                            if k not in _FAST_PARAMS and k not in ("status", "timestamp")
                        }
                        state.slow_data = slow_fields
                        # Merge slow into cached_data without overwriting fresh fast fields
                        state.cached_data = {
                            **state.cached_data,
                            **slow_fields,
                        }

            elif state.real_client and not state.real_client.connected:
                # P1-C: client handle exists but serial line dropped
                state.real_client = None
                state.last_poll_error = "disconnected"
                state.fast_data = {}
                state.slow_data = {}
                state.cached_data = {
                    "timestamp": datetime.now().isoformat(),
                    "status": "NOT_CONNECTED",
                }
                async with state.alerts_lock:
                    update_current_alerts(None)

            elif not state.cached_data:
                state.cached_data = {
                    "timestamp": datetime.now().isoformat(),
                    "status": "NOT_CONNECTED",
                }

            # ── Alert evaluation (every fast cycle) ──────────────────────────
            has_alert = False
            _alerts = {}
            if state.cached_data and state.cached_data.get("status") not in ["NOT_CONNECTED", "ERROR"]:
                _alerts = check_limits(state.cached_data)
                has_alert = _alerts.get("status") == "ALERT"

            async with state.alerts_lock:
                update_current_alerts(_alerts if has_alert else None)

            # ── CSV logging (every fast cycle if enabled) ─────────────────────
            if state.is_logging:
                try:
                    with open(state.log_filename, mode='a', newline='', encoding='utf-8') as file:
                        writer = csv.writer(file)
                        flat_data = get_latest_data()
                        row = [flat_data.get(header, "") for header in state.log_headers]
                        writer.writerow(row)
                except Exception as log_err:
                    logger.error(f"Error writing to Normal log file: {log_err}")
                    try:
                        with open(state.log_filename + ".backup", mode='a', newline='', encoding='utf-8') as file:
                            writer = csv.writer(file)
                            flat_data = get_latest_data()
                            row = [flat_data.get(header, "") for header in state.log_headers]
                            writer.writerow(row)
                    except Exception:
                        pass

            # ── Fault logging + LINE notify ───────────────────────────────────
            if has_alert:
                try:
                    fault_log_exists = os.path.exists(state.fault_log_filename)
                    if not fault_log_exists:
                        with open(state.fault_log_filename, mode='w', newline='', encoding='utf-8') as file:
                            writer = csv.writer(file)
                            writer.writerow(state.log_headers + ["Fault_Details"])

                    with open(state.fault_log_filename, mode='a', newline='', encoding='utf-8') as file:
                        writer = csv.writer(file)
                        flat_data = get_latest_data()
                        row = [flat_data.get(header, "") for header in state.log_headers]
                        if len(row) > 1:
                            row[1] = "Fault"
                        fault_details = " | ".join(
                            [f"{a['category'].upper()}: {a['message']} ({a.get('detail', '')})"
                             for a in _alerts.get("alerts", [])]
                        )
                        row.append(fault_details)
                        writer.writerow(row)

                        now = time.time()
                        current_fault_categories = set(a['category'] for a in _alerts.get("alerts", []))
                        if current_fault_categories != last_sent_fault_categories or \
                                now - last_line_notify_time > LINE_NOTIFY_COOLDOWN:
                            alert_msgs = [
                                f"⚠️ {a['category'].upper()}: {a['message']}\n💡 {a.get('detail', '')}"
                                for a in _alerts.get("alerts", [])
                            ]
                            full_msg = "\n🚨 [FAULT DETECTED] PM2000\n" + "\n".join(alert_msgs)
                            full_msg += f"\n⏰ เวลา: {datetime.now().strftime('%H:%M:%S')}"
                            
                            # Use smart helper to add AI analysis before sending
                            asyncio.create_task(_send_smart_line(_alerts.get("alerts", []), flat_data, full_msg))
                            
                            last_line_notify_time = now
                            last_sent_fault_categories = current_fault_categories

                except Exception as log_err:
                    logger.error(f"Error writing to Fault log file: {log_err}")
                    try:
                        with open(state.fault_log_filename + ".backup", mode='a', newline='', encoding='utf-8') as file:
                            writer = csv.writer(file)
                            flat_data = get_latest_data()
                            row = [flat_data.get(header, "") for header in state.log_headers]
                            if len(row) > 1:
                                row[1] = "Fault"
                            fault_details = " | ".join(
                                [f"{a['category'].upper()}: {a['message']} ({a.get('detail', '')})"
                                 for a in _alerts.get("alerts", [])]
                            )
                            row.append(fault_details)
                            writer.writerow(row)
                    except Exception:
                        pass

        except Exception as e:
            state.last_poll_error = str(e)
            base = state.cached_data or {}
            state.cached_data = {
                **base,
                "timestamp": datetime.now().isoformat(),
                "status": "ERROR",
            }
            # P1-B: clear stale alerts on any uncaught exception
            async with state.alerts_lock:
                update_current_alerts(None)
            logger.error(f"Polling error: {e}")

        # Fixed fast cadence: 300ms (not 1s)
        elapsed = asyncio.get_event_loop().time() - loop_start
        await asyncio.sleep(max(0.0, 0.3 - elapsed))







