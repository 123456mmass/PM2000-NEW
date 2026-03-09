use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use std::collections::HashMap;

#[derive(Debug, Clone)]
pub struct Alert {
    pub category: String,
    pub severity: String,
    pub message: String,
    pub detail: String,
}

impl Alert {
    pub fn to_py_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new(py);
        dict.set_item("category", &self.category)?;
        dict.set_item("severity", &self.severity)?;
        dict.set_item("message", &self.message)?;
        dict.set_item("detail", &self.detail)?;
        Ok(dict)
    }
}

pub fn calculate_unbalance(v1: f64, v2: f64, v3: f64) -> f64 {
    let avg = (v1 + v2 + v3) / 3.0;
    if avg == 0.0 {
        return 0.0;
    }
    let dev1 = (v1 - avg).abs();
    let dev2 = (v2 - avg).abs();
    let dev3 = (v3 - avg).abs();
    let max_dev = dev1.max(dev2).max(dev3);
    (max_dev / avg) * 100.0
}

#[pyfunction]
#[pyo3(signature = (data))]
pub fn diagnose_faults<'py>(py: Python<'py>, data: Bound<'py, PyDict>) -> PyResult<Bound<'py, PyDict>> {
    let mut alerts: Vec<Alert> = Vec::new();

    // Helper to get float from dict
    let get_f64 = |key: &str| -> f64 {
        if let Ok(Some(val)) = data.get_item(key) {
            if let Ok(f) = val.extract::<f64>() {
                return f;
            }
        }
        0.0
    };

    let v1 = get_f64("V_LN1");
    let v2 = get_f64("V_LN2");
    let v3 = get_f64("V_LN3");
    let v_avg = get_f64("V_LN_avg");

    let i1 = {
        let mut i = get_f64("I1");
        if i == 0.0 { i = get_f64("I_L1"); }
        i
    };
    let i2 = {
        let mut i = get_f64("I2");
        if i == 0.0 { i = get_f64("I_L2"); }
        i
    };
    let i3 = {
        let mut i = get_f64("I3");
        if i == 0.0 { i = get_f64("I_L3"); }
        i
    };
    let i_avg = get_f64("I_avg");

    let freq = get_f64("Freq");
    let pf = get_f64("PF_Total").abs();

    // 2. Phase Loss Detection
    let phases_v = [v1, v2, v3];
    let mut missing_phases = Vec::new();
    for (idx, &v) in phases_v.iter().enumerate() {
        if v < 50.0 && v_avg > 100.0 {
            missing_phases.push(idx + 1);
        }
    }

    if !missing_phases.is_empty() {
        let phase_str = missing_phases.iter()
            .map(|p| format!("L{}", p))
            .collect::<Vec<_>>()
            .join(", ");
        alerts.push(Alert {
            category: "phase_loss".into(),
            severity: "critical".into(),
            message: format!("Phase Loss Detected: {} disconnected", phase_str),
            detail: "Possible fuse blown or primary side failure.".into(),
        });
    }

    // 3. Voltage Unbalance
    if v_avg > 100.0 {
        let v_unb = calculate_unbalance(v1, v2, v3);
        if v_unb > 5.0 {
            alerts.push(Alert {
                category: "unbalance".into(),
                severity: "high".into(),
                message: format!("แรงดันไฟฟ้าไม่สมดุล {:.1}% เกินมาตรฐาน วสท. (2-5%) มอเตอร์ 3 เฟสจะเกิดความร้อนสะสมและกินกระแสเกิน", v_unb),
                detail: "ตรวจสอบการกระจายโหลด 1 เฟส ให้สมดุลในทุกเฟส หรือเช็กจุดต่อสายไฟ/ขั้วหลวม (Loose Connection)".to_string(),
            });
        }
    }

    // 4. Overvoltage / Overload / Sag
    if v_avg > 250.0 {
        alerts.push(Alert {
            category: "voltage_swell".into(),
            severity: "high".into(),
            message: "แรงดันไฟฟ้าเกินมาตรฐาน กฟภ./กฟน. (สูงกว่า 253V) เสี่ยงต่อการทะลุของฉนวน (Insulation Breakdown) ในอุปกรณ์อิเล็กทรอนิกส์".to_string(),
            detail: "ตรวจสอบระบบปรับแรงดันไฟฟ้า (AVR) ตัดวงจรอุปกรณ์ที่ไวต่อแรงดัน หรือปรับแทปหม้อแปลงลง".to_string(),
        });
    } else if v_avg > 0.0 && v_avg < 190.0 {
        // 5. Harmonics (moved here and combined with overload check)
        let thd_v1 = get_f64("THDv_L1");
        let thd_v2 = get_f64("THDv_L2");
        let thd_v3 = get_f64("THDv_L3");
        let max_thd = thd_v1.max(thd_v2).max(thd_v3);

        if max_thd > 8.0 {
            alerts.push(Alert {
                category: "power_quality".into(),
                severity: "high".into(),
                message: format!("ค่าเพี้ยนฮาร์มอนิกแรงดัน (THDv) สูงถึง {:.1}% เกินมาตรฐาน วสท. ที่กำหนดไว้ไม่เกิน 5% เสี่ยงต่อหม้อแปลงร้อนจัด", max_thd),
                detail: "ตรวจสอบการทำงานของ VSD/Inverter หรืออุปกรณ์ Non-linear และพิจารณาติดตั้ง Harmonic Filter (Active/Passive)".into(),
            });
        } else {
            alerts.push(Alert {
                category: "voltage_sag".into(),
                severity: "high".into(),
                message: "เกิดปัญหาไฟตก (Voltage Sag) ต่ำกว่ามาตรฐาน กฟภ./กฟน. (207V) อาจส่งผลให้มอเตอร์ไหม้หรืออุปกรณ์อิเล็กทรอนิกส์รีเซ็ต".to_string(),
                detail: "ตรวจสอบแทปหม้อแปลง (Transformer Tap) หรือติดต่อการไฟฟ้าเพื่อตรวจสอบแรงดันตกคร่อมในสายชอร์ต".to_string(),
            });
        }
    }

    // 5. Harmonics
    let thd_v1 = get_f64("THDv_L1");
    let thd_v2 = get_f64("THDv_L2");
    let thd_v3 = get_f64("THDv_L3");
    let max_thd = thd_v1.max(thd_v2).max(thd_v3);

    if max_thd > 8.0 {
        alerts.push(Alert {
            category: "harmonics".into(),
            severity: "medium".into(),
            message: format!("High Harmonics Distortion: {:.1}%", max_thd),
            detail: "May cause electronic equipment malfunction.".into(),
        });
    }

    // 6. Frequency Anomaly
    if freq > 0.0 && (freq < 49.0 || freq > 51.0) {
        alerts.push(Alert {
            category: "frequency".into(),
            severity: "high".into(),
            message: format!("Frequency Anomaly: {:.2} Hz", freq),
            detail: "Grid instability detected.".into(),
        });
    }

    // 7. Short Circuit
    if i_avg > 100.0 {
        alerts.push(Alert {
            category: "short_circuit".into(),
            severity: "critical".into(),
            message: format!("Short Circuit Detected: {:.1}A", i_avg),
            detail: "Immediate action required to prevent damage.".into(),
        });
    }

    // 8. Ground Fault
    let i_n = get_f64("I_N");
    if i_n > 5.0 {
        alerts.push(Alert {
            category: "ground_fault".into(),
            severity: "critical".into(),
            message: format!("Ground Fault Detected: {:.1}A", i_n),
            detail: "Immediate action required to prevent damage.".into(),
        });
    }

    // 9. Build final dictionary
    let result = PyDict::new(py);
    result.set_item("count", alerts.len())?;
    result.set_item("status", if alerts.is_empty() { "OK" } else { "ALERT" })?;
    
    let py_alerts = PyList::empty(py);
    for alert in alerts {
        py_alerts.append(alert.to_py_dict(py)?)?;
    }
    result.set_item("alerts", py_alerts)?;
    
    if let Ok(Some(ts)) = data.get_item("timestamp") {
        result.set_item("timestamp", ts)?;
    }

    Ok(result)
}
