use pyo3::prelude::*;
use once_cell::sync::Lazy;
use std::collections::HashMap;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

struct CacheItem {
    value: PyObject,
    expiry: u64, // Unix timestamp in seconds
}

// Global static cache using Mutex<HashMap> for thread-safety without external crates
static GLOBAL_CACHE: Lazy<Mutex<HashMap<String, CacheItem>>> = Lazy::new(|| Mutex::new(HashMap::new()));

fn get_current_time() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("Time went backwards")
        .as_secs()
}

#[pyfunction]
#[pyo3(signature = (key, value, ttl_seconds=3600))]
pub fn cache_set(key: String, value: PyObject, ttl_seconds: u64) {
    let now = get_current_time();
    let expiry = now + ttl_seconds;
    if let Ok(mut cache) = GLOBAL_CACHE.lock() {
        cache.insert(key, CacheItem { value, expiry });
    }
}

#[pyfunction]
pub fn cache_get(py: Python<'_>, key: String) -> PyResult<Option<PyObject>> {
    let now = get_current_time();
    
    if let Ok(mut cache) = GLOBAL_CACHE.lock() {
        if let Some(item) = cache.get(&key) {
            if item.expiry > now {
                return Ok(Some(item.value.clone_ref(py)));
            } else {
                // Expired
                cache.remove(&key);
            }
        }
    }
    
    Ok(None)
}

#[pyfunction]
pub fn cache_delete(key: String) {
    if let Ok(mut cache) = GLOBAL_CACHE.lock() {
        cache.remove(&key);
    }
}

#[pyfunction]
pub fn cache_clear() {
    if let Ok(mut cache) = GLOBAL_CACHE.lock() {
        cache.clear();
    }
}

#[pyfunction]
pub fn cache_size() -> usize {
    if let Ok(cache) = GLOBAL_CACHE.lock() {
        cache.len()
    } else {
        0
    }
}
