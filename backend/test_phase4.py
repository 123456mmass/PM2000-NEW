"""
=== Phase 4: Comprehensive Test Suite ===
Tests anomaly.rs (Phase 4A) and modbus_reader.rs (Phase 4B)
"""
import sys
import time
import json

passed = 0
failed = 0

def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  [PASS] {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label} — {detail}")

# ============================================================
print("\n" + "=" * 60)
print("=== Phase 4A: Anomaly Detection (anomaly.rs) ===")
print("=" * 60)

try:
    import pm2000_core
    check("pm2000_core imported", True)
except ImportError as e:
    check("pm2000_core imported", False, str(e))
    sys.exit(1)

# 1. Check detect_anomalies exists
check("detect_anomalies function exists", hasattr(pm2000_core, 'detect_anomalies'))

# 2. Normal data → no alerts
normal_data = {
    "V_LN1": 230.0, "V_LN2": 229.5, "V_LN3": 230.5, "V_LN_avg": 230.0,
    "I_L1": 10.0, "I_L2": 10.1, "I_L3": 9.9, "I_avg": 10.0,
    "THDv_L1": 2.0, "THDv_L2": 2.1, "THDv_L3": 1.9,
    "THDi_L1": 3.0, "THDi_L2": 3.1, "THDi_L3": 2.9,
    "PF_L1": 0.95, "PF_L2": 0.96, "PF_L3": 0.94, "PF_Total": 0.95,
    "Freq": 50.0,
}
config = {
    "voltage_nominal": 230.0,
    "voltage_tolerance_pct": 10.0,
    "voltage_unbalance_pct": 2.0,
    "current_unbalance_pct": 2.0,
    "current_unbalance_critical_pct": 5.0,
    "thd_v_warning_pct": 5.0,
    "thd_v_critical_pct": 8.0,
    "thd_i_warning_pct": 8.0,
    "thd_i_critical_pct": 12.0,
    "pf_warning": 0.9,
    "pf_critical": 0.85,
    "freq_nominal": 50.0,
    "freq_tolerance": 1.0,
}

alerts = pm2000_core.detect_anomalies(normal_data, [], config)
check("Normal data → 0 alerts", len(alerts) == 0, f"got {len(alerts)}")

# 3. Overvoltage → should trigger
ov_data = dict(normal_data)
ov_data["V_LN_avg"] = 260.0
alerts = pm2000_core.detect_anomalies(ov_data, [], config)
check("Overvoltage 260V → alert", len(alerts) > 0)
if alerts:
    check("  category = voltage", alerts[0]["category"] == "voltage")

# 4. Undervoltage → should trigger
uv_data = dict(normal_data)
uv_data["V_LN_avg"] = 200.0
alerts = pm2000_core.detect_anomalies(uv_data, [], config)
check("Undervoltage 200V → alert", len(alerts) > 0)

# 5. THD Voltage warning → should trigger
thd_data = dict(normal_data)
thd_data["THDv_L1"] = 6.0
thd_data["THDv_L2"] = 6.5
thd_data["THDv_L3"] = 7.0
alerts = pm2000_core.detect_anomalies(thd_data, [], config)
thd_alerts = [a for a in alerts if a["category"] == "thdv"]
check("High THDv (7%) → warning", len(thd_alerts) > 0)
if thd_alerts:
    check("  severity = warning", thd_alerts[0]["severity"] == "warning")

# 6. THD Voltage critical → should trigger
thd_crit_data = dict(normal_data)
thd_crit_data["THDv_L1"] = 10.0
thd_crit_data["THDv_L2"] = 9.0
thd_crit_data["THDv_L3"] = 11.0
alerts = pm2000_core.detect_anomalies(thd_crit_data, [], config)
thd_alerts = [a for a in alerts if a["category"] == "thdv"]
check("Critical THDv (11%) → critical", len(thd_alerts) > 0 and thd_alerts[0]["severity"] == "critical")

# 7. Low Power Factor → should trigger
lpf_data = dict(normal_data)
lpf_data["PF_L1"] = 0.82
lpf_data["PF_L2"] = 0.83
lpf_data["PF_L3"] = 0.84
alerts = pm2000_core.detect_anomalies(lpf_data, [], config)
pf_alerts = [a for a in alerts if a["category"] == "power_factor"]
check("Low PF (0.82) → critical", len(pf_alerts) > 0 and pf_alerts[0]["severity"] == "critical")

# 8. Frequency out of range → should trigger
freq_data = dict(normal_data)
freq_data["Freq"] = 52.0
alerts = pm2000_core.detect_anomalies(freq_data, [], config)
freq_alerts = [a for a in alerts if a["category"] == "frequency"]
check("Frequency 52Hz → alert", len(freq_alerts) > 0)

# 9. Voltage Unbalance → should trigger
unb_data = dict(normal_data)
unb_data["V_LN1"] = 230.0
unb_data["V_LN2"] = 215.0  # ~4.3% unbalance
unb_data["V_LN3"] = 230.0
unb_data["V_LN_avg"] = 225.0
alerts = pm2000_core.detect_anomalies(unb_data, [], config)
unb_alerts = [a for a in alerts if a["category"] == "voltage_unbalance"]
check("Voltage Unbalance (4.3%) → alert", len(unb_alerts) > 0)

# 10. Dynamic Config → change threshold, retest
strict_config = dict(config)
strict_config["thd_v_warning_pct"] = 1.5  # Very strict THD limit (normal data THDv=2.0%)
alerts = pm2000_core.detect_anomalies(normal_data, [], strict_config)
thd_alerts = [a for a in alerts if a["category"] == "thdv"]
check("Strict config THDv>1.5% triggers on normal data (2.0%)", len(thd_alerts) > 0)

# 11. Trend Analysis (Stateless with History)
history = [
    {"V_LN_avg": 225.0, "THDv_L1": 3.0},
    {"V_LN_avg": 227.0, "THDv_L1": 3.5},
    {"V_LN_avg": 229.0, "THDv_L1": 4.0},
    {"V_LN_avg": 231.0, "THDv_L1": 4.5},
    {"V_LN_avg": 233.0, "THDv_L1": 5.0},
]
rising_thd_data = dict(normal_data)
rising_thd_data["THDv_L1"] = 6.0
rising_thd_data["THDv_L2"] = 6.0
rising_thd_data["THDv_L3"] = 6.0
alerts = pm2000_core.detect_anomalies(rising_thd_data, history, config)
thd_alerts = [a for a in alerts if a["category"] == "thdv"]
check("Trend analysis with history → has trend field", len(thd_alerts) > 0 and "trend" in thd_alerts[0])
if thd_alerts:
    check(f"  trend = '{thd_alerts[0].get('trend', 'N/A')}'", thd_alerts[0].get("trend") in ["rising", "falling", "stable", "none"])

# 12. Performance benchmark
print("\n--- Performance Benchmark ---")
t0 = time.perf_counter()
for _ in range(10000):
    pm2000_core.detect_anomalies(normal_data, history, config)
t1 = time.perf_counter()
elapsed_ms = (t1 - t0) * 1000
per_call_us = elapsed_ms / 10000 * 1000
print(f"  10,000 detect_anomalies calls: {elapsed_ms:.1f}ms ({per_call_us:.1f}µs/call)")
check("Performance: < 100µs per call", per_call_us < 100, f"actual: {per_call_us:.1f}µs")

# ============================================================
print("\n" + "=" * 60)
print("=== Phase 4B: Modbus Reader (modbus_reader.rs) ===")
print("=" * 60)

# 13. Check modbus_read_blocks exists
check("modbus_read_blocks function exists", hasattr(pm2000_core, 'modbus_read_blocks'))

# 14. Test with non-existent port → should return ERROR gracefully (not crash)
try:
    result = pm2000_core.modbus_read_blocks("COM99", 9600, 1)
    check("COM99 (non-existent) → returns dict", isinstance(result, dict))
    check("  status = ERROR (graceful)", result.get("status") == "ERROR")
    check("  has error message", "error" in result and len(result["error"]) > 0)
except Exception as e:
    check("COM99 graceful error handling", False, f"Exception: {e}")

# 15. Verify energy_config.json has anomaly_thresholds
print("\n" + "=" * 60)
print("=== Config Verification ===")
print("=" * 60)
try:
    with open("energy_config.json", "r") as f:
        cfg = json.load(f)
    check("energy_config.json → valid JSON", True)
    check("  has anomaly_thresholds section", "anomaly_thresholds" in cfg)
    thresholds = cfg.get("anomaly_thresholds", {})
    check("  voltage_nominal present", "voltage_nominal" in thresholds)
    check("  freq_nominal present", "freq_nominal" in thresholds)
    check("  pf_warning present", "pf_warning" in thresholds)
except Exception as e:
    check("energy_config.json readable", False, str(e))

# ============================================================
print("\n" + "=" * 60)
print(f"=== RESULTS: {passed} PASSED / {failed} FAILED ===")
print("=" * 60)
if failed == 0:
    print("🎉 ALL TESTS PASSED!")
else:
    print(f"⚠️  {failed} test(s) need attention")
