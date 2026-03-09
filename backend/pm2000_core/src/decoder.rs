use pyo3::prelude::*;

#[pyfunction]
pub fn decode_float32(hi: u16, lo: u16) -> f32 {
    let bits = ((hi as u32) << 16) | (lo as u32);
    f32::from_bits(bits)
}

#[pyfunction]
pub fn decode_int64(regs: [u16; 4]) -> (i64, Vec<u8>) {
    let mut bytes = [0u8; 8];
    bytes[0..2].copy_from_slice(&regs[0].to_be_bytes());
    bytes[2..4].copy_from_slice(&regs[1].to_be_bytes());
    bytes[4..6].copy_from_slice(&regs[2].to_be_bytes());
    bytes[6..8].copy_from_slice(&regs[3].to_be_bytes());
    let val = i64::from_be_bytes(bytes);
    (val, bytes.to_vec())
}
