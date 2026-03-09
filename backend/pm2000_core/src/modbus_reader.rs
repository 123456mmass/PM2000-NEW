use pyo3::prelude::*;
use pyo3::types::PyDict;
use serialport::SerialPort;
use std::time::Duration;
use byteorder::{BigEndian, ReadBytesExt};
use std::io::{Cursor, Read, Write};

/// Modbus RTU CRC-16 Calculation
fn crc16(data: &[u8]) -> u16 {
    let mut crc: u16 = 0xFFFF;
    for &byte in data {
        crc ^= byte as u16;
        for _ in 0..8 {
            if (crc & 0x0001) != 0 {
                crc >>= 1;
                crc ^= 0xA001;
            } else {
                crc >>= 1;
            }
        }
    }
    crc
}

/// Send Modbus RTU Read Holding Registers Request (Function 03) and read response
fn modbus_read_holding_registers(
    port: &mut Box<dyn SerialPort>,
    slave_id: u8,
    start_addr: u16,
    count: u16,
) -> Result<Vec<u16>, String> {
    // Build request frame: [SlaveID][Func=03][AddrHi][AddrLo][CountHi][CountLo][CRC_Lo][CRC_Hi]
    let mut req = vec![
        slave_id,
        0x03,
        (start_addr >> 8) as u8,
        (start_addr & 0xFF) as u8,
        (count >> 8) as u8,
        (count & 0xFF) as u8,
    ];
    let crc = crc16(&req);
    req.push((crc & 0xFF) as u8);
    req.push((crc >> 8) as u8);

    // Send request
    port.write_all(&req)
        .map_err(|e| format!("Failed to write to serial port: {}", e))?;
    port.flush().map_err(|e| format!("Flush failed: {}", e))?;

    // Read response header: [SlaveID][Func][ByteCount] -> 3 bytes
    let mut header = [0u8; 3];
    port.read_exact(&mut header)
        .map_err(|e| format!("Failed to read Modbus header: {}", e))?;

    if header[0] != slave_id {
        return Err(format!("Invalid slave ID in response: {}", header[0]));
    }
    
    // Exception check (Function Code + 0x80)
    if header[1] == 0x83 {
        let mut err_code = [0u8; 1];
        port.read_exact(&mut err_code).map_err(|e| format!("Failed to read err code: {}", e))?;
        return Err(format!("Modbus Exception Code: {}", err_code[0]));
    }

    if header[1] != 0x03 {
        return Err(format!("Invalid function code in response: {}", header[1]));
    }

    let byte_count = header[2] as usize;
    if byte_count != (count * 2) as usize {
        return Err(format!("Byte count mismatch: expected {}, got {}", count * 2, byte_count));
    }

    // Read payload + 2 CRC bytes
    let mut payload = vec![0u8; byte_count + 2];
    port.read_exact(&mut payload)
        .map_err(|e| format!("Failed to read Modbus payload: {}", e))?;

    // Verify CRC
    let mut full_resp = header.to_vec();
    full_resp.extend_from_slice(&payload[0..byte_count]);
    let calc_crc = crc16(&full_resp);
    
    let received_crc = (payload[byte_count] as u16) | ((payload[byte_count + 1] as u16) << 8);
    if calc_crc != received_crc {
        return Err(format!("CRC Error: calc {:04X}, received {:04X}", calc_crc, received_crc));
    }

    // Parse registers
    let mut registers = Vec::with_capacity(count as usize);
    let mut cursor = Cursor::new(&payload[0..byte_count]);
    for _ in 0..count {
        registers.push(cursor.read_u16::<BigEndian>().unwrap());
    }

    Ok(registers)
}

/// Helper function to perform bulk Modbus reading, releasing Python GIL
/// PyO3 #[pyfunction] to be called from Python
#[pyfunction]
#[pyo3(signature = (port_name, baudrate, slave_id))]
pub fn modbus_read_blocks<'py>(
    py: Python<'py>,
    port_name: String,
    baudrate: u32,
    slave_id: u8,
) -> PyResult<Bound<'py, PyDict>> {
    // Release GIL during blocking serial I/O
    let result: Result<(Vec<u16>, Vec<u16>, Vec<u16>), String> = py.allow_threads(move || {
        // Open serial port
        let mut port = serialport::new(port_name.clone(), baudrate)
            .timeout(Duration::from_millis(500))
            .open()
            .map_err(|e| format!("Failed to open port {}: {}", port_name, e))?;

        // 1. Read Block 1: Address 2999, 125 registers
        let r1 = modbus_read_holding_registers(&mut port, slave_id, 2999, 125)?;
        
        // Let the device breathe a tiny bit between requests
        std::thread::sleep(Duration::from_millis(10));
        
        // 2. Read Block 2: Address 3190, 60 registers
        let r2 = modbus_read_holding_registers(&mut port, slave_id, 3190, 60)?;
        
        std::thread::sleep(Duration::from_millis(10));

        // 3. Read Block 3: Address 21299, 40 registers
        let r3 = modbus_read_holding_registers(&mut port, slave_id, 21299, 40)?;

        Ok((r1, r2, r3))
    });

    let dict = PyDict::new(py);
    match result {
        Ok((r1, r2, r3)) => {
            dict.set_item("status", "OK")?;
            dict.set_item("block_2999", r1)?;
            dict.set_item("block_3190", r2)?;
            dict.set_item("block_21299", r3)?;
        }
        Err(e) => {
            dict.set_item("status", "ERROR")?;
            dict.set_item("error", e)?;
        }
    }
    Ok(dict)
}
