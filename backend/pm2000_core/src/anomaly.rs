use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use std::collections::HashMap;

#[derive(Debug, Clone)]
pub struct AnomalyAlert {
    pub category: String,
    pub severity: String,
    pub message: String,
    pub value: f64,
    pub threshold: f64,
    pub trend: String,
}

impl AnomalyAlert {
    pub fn to_py_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new(py);
        dict.set_item("category", &self.category)?;
        dict.set_item("severity", &self.severity)?;
        dict.set_item("message", &self.message)?;
        dict.set_item("value", self.value)?;
        dict.set_item("threshold", self.threshold)?;
        dict.set_item("trend", &self.trend)?;
        Ok(dict)
    }
}

// Calculate slope (trend) from history. Returns "rising", "falling", "stable", or "none"
fn analyze_trend(history: &[f64]) -> String {
    if history.len() < 2 {
        return "none".to_string();
    }
    
    // Simple linear regression (slope) over the last N points
    let n = history.len() as f64;
    let sum_x: f64 = (0..history.len()).map(|x| x as f64).sum();
    let sum_y: f64 = history.iter().sum();
    let sum_xy: f64 = history.iter().enumerate().map(|(i, &y)| (i as f64) * y).sum();
    let sum_xx: f64 = (0..history.len()).map(|x| (x as f64) * (x as f64)).sum();
    
    let denominator = n * sum_xx - sum_x * sum_x;
    if denominator == 0.0 {
        return "stable".to_string();
    }
    
    let slope = (n * sum_xy - sum_x * sum_y) / denominator;
    
    if slope > 0.1 {
        "rising".to_string()
    } else if slope < -0.1 {
        "falling".to_string()
    } else {
        "stable".to_string()
    }
}

// Helper: Calculate Maximum Deviation Unbalance (IEEE)
fn calculate_unbalance(v1: f64, v2: f64, v3: f64) -> f64 {
    let avg = (v1 + v2 + v3) / 3.0;
    if avg == 0.0 { return 0.0; }
    let max_dev = (v1 - avg).abs().max((v2 - avg).abs()).max((v3 - avg).abs());
    (max_dev / avg) * 100.0
}

#[pyfunction]
#[pyo3(signature = (data_dict, history_list, config_dict))]
pub fn detect_anomalies<'py>(
    py: Python<'py>, 
    data_dict: Bound<'py, PyDict>, 
    history_list: Bound<'py, PyList>, 
    config_dict: Bound<'py, PyDict>
) -> PyResult<Bound<'py, PyList>> {
    let mut alerts: Vec<AnomalyAlert> = Vec::new();
    
    // Extractor helper
    let get_f64 = |dict: &Bound<'py, PyDict>, key: &str, default: f64| -> f64 {
        if let Ok(Some(val)) = dict.get_item(key) {
            if let Ok(f) = val.extract::<f64>() {
                return f;
            }
        }
        default
    };

    // Load Configs (with defaults if missing)
    let v_nom = get_f64(&config_dict, "voltage_nominal", 230.0);
    let v_tol = get_f64(&config_dict, "voltage_tolerance_pct", 10.0);
    let v_unb_limit = get_f64(&config_dict, "voltage_unbalance_pct", 2.0);
    let i_unb_warn = get_f64(&config_dict, "current_unbalance_pct", 2.0);
    let i_unb_crit = get_f64(&config_dict, "current_unbalance_critical_pct", 5.0);
    let thdv_warn = get_f64(&config_dict, "thd_v_warning_pct", 5.0);
    let thdv_crit = get_f64(&config_dict, "thd_v_critical_pct", 8.0);
    let thdi_warn = get_f64(&config_dict, "thd_i_warning_pct", 8.0);
    let thdi_crit = get_f64(&config_dict, "thd_i_critical_pct", 12.0);
    let pf_warn = get_f64(&config_dict, "pf_warning", 0.9);
    let pf_crit = get_f64(&config_dict, "pf_critical", 0.85);
    let f_nom = get_f64(&config_dict, "freq_nominal", 50.0);
    let f_tol = get_f64(&config_dict, "freq_tolerance", 1.0);

    // Current Data
    let v_avg = get_f64(&data_dict, "V_LN_avg", 0.0);
    let v1 = get_f64(&data_dict, "V_LN1", 0.0);
    let v2 = get_f64(&data_dict, "V_LN2", 0.0);
    let v3 = get_f64(&data_dict, "V_LN3", 0.0);
    let i_avg = get_f64(&data_dict, "I_avg", 0.0);
    let thdv_max = get_f64(&data_dict, "THDv_L1", 0.0)
        .max(get_f64(&data_dict, "THDv_L2", 0.0))
        .max(get_f64(&data_dict, "THDv_L3", 0.0));
    let thdi_max = get_f64(&data_dict, "THDi_L1", 0.0)
        .max(get_f64(&data_dict, "THDi_L2", 0.0))
        .max(get_f64(&data_dict, "THDi_L3", 0.0));
    let freq = get_f64(&data_dict, "Freq", 0.0);
    
    // Calculate PFs safely
    let mut pf_vals: Vec<f64> = vec![
        get_f64(&data_dict, "PF_L1", 0.0).abs(),
        get_f64(&data_dict, "PF_L2", 0.0).abs(),
        get_f64(&data_dict, "PF_L3", 0.0).abs()
    ];
    pf_vals.retain(|&x| x > 0.01); // Filter out zero or almost zero PFs (inactive phases)
    let pf_min = if pf_vals.is_empty() { 1.0 } else { pf_vals.iter().copied().fold(1.0, f64::min) };


    // History parsing
    let parse_history = |key: &str| -> Vec<f64> {
        let mut hist = Vec::new();
        for i in 0..history_list.len() {
            if let Ok(item) = history_list.get_item(i) {
                if let Ok(dict) = item.downcast::<PyDict>() {
                    hist.push(get_f64(&dict, key, 0.0));
                }
            }
        }
        hist
    };

    // 1. Voltage Nominal Check (Out of Bounds)
    if v_avg > 0.0 {
        let v_trend = analyze_trend(&parse_history("V_LN_avg"));
        let max_v = v_nom * (1.0 + v_tol / 100.0);
        let min_v = v_nom * (1.0 - v_tol / 100.0);
        
        if v_avg > max_v {
            alerts.push(AnomalyAlert {
                category: "voltage".into(),
                severity: "warning".into(),
                message: format!("Overvoltage detected ({:.1}V)", v_avg),
                value: v_avg,
                threshold: max_v,
                trend: v_trend.clone(),
            });
        } else if v_avg < min_v {
            alerts.push(AnomalyAlert {
                category: "voltage".into(),
                severity: "warning".into(),
                message: format!("Undervoltage detected ({:.1}V)", v_avg),
                value: v_avg,
                threshold: min_v,
                trend: v_trend,
            });
        }
    }

    // 2. Unbalance Checks
    if v_avg > 50.0 { // System is active
        let v_unb = calculate_unbalance(v1, v2, v3);
        if v_unb > v_unb_limit {
            let v1_hist = parse_history("V_LN1");
            let v2_hist = parse_history("V_LN2");
            let v3_hist = parse_history("V_LN3");
            let unb_hist: Vec<f64> = (0..v1_hist.len())
                .map(|i| calculate_unbalance(v1_hist[i], v2_hist[i], v3_hist[i]))
                .collect();
                
            alerts.push(AnomalyAlert {
                category: "voltage_unbalance".into(),
                severity: "warning".into(),
                message: format!("Voltage unbalance exceeds limit ({:.1}%)", v_unb),
                value: v_unb,
                threshold: v_unb_limit,
                trend: analyze_trend(&unb_hist),
            });
        }
    }

    if i_avg > 5.0 { // Load is active
        let i1 = get_f64(&data_dict, "I_L1", get_f64(&data_dict, "I1", 0.0));
        let i2 = get_f64(&data_dict, "I_L2", get_f64(&data_dict, "I2", 0.0));
        let i3 = get_f64(&data_dict, "I_L3", get_f64(&data_dict, "I3", 0.0));
        
        let i_unb = calculate_unbalance(i1, i2, i3);
        if i_unb > i_unb_crit {
            alerts.push(AnomalyAlert {
                category: "current_unbalance".into(),
                severity: "critical".into(),
                message: format!("Critical Current unbalance ({:.1}%)", i_unb),
                value: i_unb,
                threshold: i_unb_crit,
                trend: "none".into(), // Critical usually implies immediate action
            });
        } else if i_unb > i_unb_warn {
            alerts.push(AnomalyAlert {
                category: "current_unbalance".into(),
                severity: "warning".into(),
                message: format!("Current unbalance exceeds warning limit ({:.1}%)", i_unb),
                value: i_unb,
                threshold: i_unb_warn,
                trend: "none".into(),
            });
        }
    }

    // 3. Harmonics (THD)
    if thdv_max > thdv_warn {
        let severity = if thdv_max > thdv_crit { "critical" } else { "warning" };
        let thdv_hist = parse_history("THDv_L1"); // Approximation via L1 for trend
        alerts.push(AnomalyAlert {
            category: "thdv".into(),
            severity: severity.into(),
            message: format!("Voltage Harmonics High ({:.1}%)", thdv_max),
            value: thdv_max,
            threshold: thdv_warn,
            trend: analyze_trend(&thdv_hist),
        });
    }

    if i_avg > 5.0 && thdi_max > thdi_warn {
        let severity = if thdi_max > thdi_crit { "critical" } else { "warning" };
        alerts.push(AnomalyAlert {
            category: "thdi".into(),
            severity: severity.into(),
            message: format!("Current Harmonics High ({:.1}%)", thdi_max),
            value: thdi_max,
            threshold: thdi_warn,
            trend: "none".into(),
        });
    }

    // 4. Power Factor
    if i_avg > 5.0 && pf_min < pf_warn { // Only check PF under load
        let severity = if pf_min < pf_crit { "critical" } else { "warning" };
        let pf_hist = parse_history("PF_Total");
        alerts.push(AnomalyAlert {
            category: "power_factor".into(),
            severity: severity.into(),
            message: format!("Low Power Factor Detected ({:.2})", pf_min),
            value: pf_min,
            threshold: pf_warn,
            trend: analyze_trend(&pf_hist),
        });
    }

    // 5. Frequency
    if freq > 0.0 && (freq < f_nom - f_tol || freq > f_nom + f_tol) {
        alerts.push(AnomalyAlert {
            category: "frequency".into(),
            severity: "critical".into(),
            message: format!("Grid frequency out of bounds ({:.2} Hz)", freq),
            value: freq,
            threshold: f_nom,
            trend: "none".into(),
        });
    }

    // Convert to Python List of Dicts
    let py_out = PyList::empty(py);
    for alert in alerts {
        py_out.append(alert.to_py_dict(py)?)?;
    }

    Ok(py_out)
}
