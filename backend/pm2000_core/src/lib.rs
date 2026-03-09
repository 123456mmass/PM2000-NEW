use pyo3::prelude::*;

mod decoder;
mod fault_engine;
mod cache;
mod anomaly;
mod modbus_reader;

/// A Python module implemented in Rust.
#[pymodule]
fn pm2000_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(decoder::decode_float32, m)?)?;
    m.add_function(wrap_pyfunction!(decoder::decode_int64, m)?)?;
    m.add_function(wrap_pyfunction!(fault_engine::diagnose_faults, m)?)?;
    m.add_function(wrap_pyfunction!(anomaly::detect_anomalies, m)?)?;
    
    // Modbus Reader
    m.add_function(wrap_pyfunction!(modbus_reader::modbus_read_blocks, m)?)?;
    
    // Cache functions
    m.add_function(wrap_pyfunction!(cache::cache_set, m)?)?;
    m.add_function(wrap_pyfunction!(cache::cache_get, m)?)?;
    m.add_function(wrap_pyfunction!(cache::cache_delete, m)?)?;
    m.add_function(wrap_pyfunction!(cache::cache_clear, m)?)?;
    m.add_function(wrap_pyfunction!(cache::cache_size, m)?)?;
    
    Ok(())
}
