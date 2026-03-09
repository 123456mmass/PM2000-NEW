#!/usr/bin/env python3
"""
PM2230 Modbus Scanner & Reader
สแกนหา Register Addresses + แปลงค่าเป็น Human-readable
สำหรับ PM2230 Digital Meter ผ่าน RS485 (BR9600)
"""

from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException
from typing import Dict, List, Optional, Tuple
import logging
from datetime import datetime
import json

import os

if os.getenv("PM2230_NO_RUST", "0") != "1":
    try:
        import pm2000_core
        HAS_RUST_CORE = True
    except ImportError:
        HAS_RUST_CORE = False
else:
    HAS_RUST_CORE = False

# ตั้งค่า Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class PM2230Scanner:
    """PM2230 Modbus Scanner & Reader"""
    
    # Register Map (PM2230 Modbus Protocol)
    # Verified by ON/OFF load comparison scan on real device
    # PM2230 uses 32-bit IEEE 754 float (2 registers per value, Big Endian)
    # NOTE: This PM2230 is connected single-phase, so per-phase L2/L3 power registers are empty.
    #       We read Total values for Power/PF/S/Q.
    REGISTER_MAP = {
        # Voltage (Line-to-Line)
        'V_LL12': (3019, 2, 1.0, 'V', 'Voltage L1-L2'),
        'V_LL23': (3021, 2, 1.0, 'V', 'Voltage L2-L3'),
        'V_LL31': (3023, 2, 1.0, 'V', 'Voltage L3-L1'),
        'V_LL_avg': (3025, 2, 1.0, 'V', 'Voltage L-L Average'),

        # Voltage (Line-to-Neutral) - all verified
        'V_LN1': (3027, 2, 1.0, 'V', 'Voltage L1-N'),
        'V_LN2': (3029, 2, 1.0, 'V', 'Voltage L2-N'),
        'V_LN3': (3031, 2, 1.0, 'V', 'Voltage L3-N'),
        'V_LN_avg': (3035, 2, 1.0, 'V', 'Voltage L-N Average'),

        # Current - all verified
        'I_L1': (2999, 2, 1.0, 'A', 'Current L1'),
        'I_L2': (3001, 2, 1.0, 'A', 'Current L2'),
        'I_L3': (3003, 2, 1.0, 'A', 'Current L3'),
        'I_N': (3005, 2, 1.0, 'A', 'Neutral Current'),
        'I_avg': (3009, 2, 1.0, 'A', 'Current Average'),

        # Frequency - verified
        'Freq': (3109, 2, 1.0, 'Hz', 'Frequency'),

        # Active Power (kW)
        'P_L1': (3053, 2, 1.0, 'kW', 'Active Power L1'),
        'P_L2': (3055, 2, 1.0, 'kW', 'Active Power L2'),
        'P_L3': (3057, 2, 1.0, 'kW', 'Active Power L3'),
        'P_Total': (3059, 2, 1.0, 'kW', 'Total Active Power'),

        # Apparent Power (kVA)
        'S_L1': (3061, 2, 1.0, 'kVA', 'Apparent Power L1'),
        'S_L2': (3063, 2, 1.0, 'kVA', 'Apparent Power L2'),
        'S_L3': (3065, 2, 1.0, 'kVA', 'Apparent Power L3'),
        'S_Total': (3067, 2, 1.0, 'kVA', 'Total Apparent Power'),

        # Reactive Power (kvar)
        'Q_L1': (3069, 2, 1.0, 'kvar', 'Reactive Power L1'),
        'Q_L2': (3071, 2, 1.0, 'kvar', 'Reactive Power L2'),
        'Q_L3': (3073, 2, 1.0, 'kvar', 'Reactive Power L3'),
        'Q_Total': (3075, 2, 1.0, 'kvar', 'Total Reactive Power'),

        # Power Factor
        'PF_L1': (3077, 2, 1.0, '', 'Power Factor L1'),
        'PF_L2': (3079, 2, 1.0, '', 'Power Factor L2'),
        'PF_L3': (3081, 2, 1.0, '', 'Power Factor L3'),
        'PF_Total': (3083, 2, 1.0, '', 'Total Power Factor'),

        # THD Voltage (Phase L1, L2, L3)
        'THDv_L1': (21329, 2, 1.0, '%', 'THD Voltage L1'),
        'THDv_L2': (21331, 2, 1.0, '%', 'THD Voltage L2'),
        'THDv_L3': (21333, 2, 1.0, '%', 'THD Voltage L3'),
        'THDi_L1': (21299, 2, 1.0, '%', 'THD Current L1'),
        'THDi_L2': (21301, 2, 1.0, '%', 'THD Current L2'),
        'THDi_L3': (21303, 2, 1.0, '%', 'THD Current L3'),

        # Unbalance (using Worst Phase registers)
        'V_unb': (3051, 2, 1.0, '%', 'Voltage Unbalance L-N (Worst)'),
        'U_unb': (3039, 2, 1.0, '%', 'Voltage Unbalance L-L (Worst)'),
        'I_unb': (3017, 2, 1.0, '%', 'Current Unbalance (Worst)'),

        # Energy (Int64 -> scaled to match meter display in kWh/kVAh/kvarh)
        'kWh_Total': (3211, 4, 0.001, 'kWh', 'Total Active Energy'),
        'kVAh_Total': (3243, 4, 0.001, 'kVAh', 'Total Apparent Energy'),
        'kvarh_Total': (3227, 4, 0.001, 'kvarh', 'Total Reactive Energy'),
    }
    
    def __init__(self, port: str = 'COM3', baudrate: int = 9600, 
                 slave_id: int = 1, parity: str = 'E'):
        """
        Initialize PM2230 Scanner
        
        Args:
            port: Serial port (COM3 for Windows, /dev/ttyUSB0 for Linux)
            baudrate: Baud rate (default: 9600)
            slave_id: Modbus slave ID (default: 1)
            parity: Parity (E=Even, N=None, O=Odd)
        """
        self.port = port
        self.baudrate = baudrate
        self.slave_id = slave_id
        self.parity = parity
        
        # สร้าง Modbus Client
        self.client = ModbusSerialClient(
            port=port,
            baudrate=baudrate,
            parity=parity,
            stopbits=1,
            bytesize=8,
            timeout=1
        )
        
        self.connected = False
        # store last connection error (text) for diagnostics
        self.last_error: Optional[str] = None
    
    def connect(self) -> bool:
        """เชื่อมต่อไปยัง PM2230"""
        try:
            if self.client.connect():
                self.connected = True
                self.last_error = None
                logger.info(f"✅ Connected to PM2230 at {self.port}")
                return True
            else:
                err = f"failed to open port"
                self.last_error = err
                logger.error(f"❌ Failed to connect at {self.port}")
                return False
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"❌ Connection error: {e}")
            return False
    
    def disconnect(self):
        """ตัดการเชื่อมต่อ"""
        if self.client:
            self.client.close()
            self.connected = False
            logger.info("🔌 Disconnected")
    
    def read_register(self, address: int, quantity: int = 1, retries: int = 3) -> Optional[List[int]]:
        """
        อ่าน Register
        
        Args:
            address: Register address
            quantity: Number of registers to read
            retries: Number of retries if read fails
            
        Returns:
            List of register values or None if error
        """
        if not self.connected:
            logger.error("Not connected!")
            return None
        
        for attempt in range(retries):
            try:
                result = self.client.read_holding_registers(
                    address=address, count=quantity, slave=self.slave_id
                )
                
                if result.isError():
                    logger.error(f"Error reading address {address}: {result}")
                    if attempt < retries - 1:
                        logger.info(f"Retrying {attempt + 1}/{retries}...")
                        continue
                    return None
                
                return result.registers
                
            except ModbusException as e:
                logger.error(f"Modbus error at {address}: {e}")
                if attempt < retries - 1:
                    logger.info(f"Retrying {attempt + 1}/{retries}...")
                    continue
                return None
            except Exception as e:
                logger.error(f"Unexpected error at {address}: {e}")
                if attempt < retries - 1:
                    logger.info(f"Retrying {attempt + 1}/{retries}...")
                    continue
                return None
    
    def convert_value(self, raw_value: int, scale: float, unit: str) -> float:
        """
        แปลงค่า Raw → Human-readable (legacy 16-bit path)
        """
        if raw_value > 32767:
            raw_value = raw_value - 65536
        return round(raw_value * scale, 3)
    
    def _decode_float32(self, registers: List[int]) -> float:
        """Decode two 16-bit registers as Big Endian IEEE 754 float."""
        import struct
        hi, lo = registers[0], registers[1]
        if HAS_RUST_CORE:
            try:
                val = pm2000_core.decode_float32(hi, lo)
                return round(val, 4)
            except Exception:
                pass
        
        raw_bytes = struct.pack('>HH', hi, lo)
        val = struct.unpack('>f', raw_bytes)[0]
        # Handle NaN / Inf
        import math
        if math.isnan(val) or math.isinf(val):
            return 0.0
        return round(val, 4)

    def _decode_int64(self, registers: List[int]) -> int:
        """Decode four 16-bit registers as Big Endian Signed 64-bit Integer."""
        import struct
        if HAS_RUST_CORE:
            try:
                # pm2000_core.decode_int64 returns (val, bytes), we only need val
                val, _ = pm2000_core.decode_int64(registers)
                return val
            except Exception:
                pass

        raw_bytes = struct.pack('>HHHH', *registers)
        return struct.unpack('>q', raw_bytes)[0]

    def read_parameter(self, param_name: str) -> Optional[Dict]:
        """
        อ่าน Parameter เดียว
        """
        if param_name not in self.REGISTER_MAP:
            logger.error(f"Unknown parameter: {param_name}")
            return None
        
        address, quantity, scale, unit, description = self.REGISTER_MAP[param_name]
        
        registers = self.read_register(address, quantity)
        
        if registers is None:
            return None
        
        if quantity == 2 and len(registers) >= 2:
            # 32-bit IEEE 754 float
            scaled_value = self._decode_float32(registers)
            raw_value = (registers[0] << 16) | registers[1]
        elif quantity == 4 and len(registers) >= 4:
            # 64-bit Integer
            raw_value = self._decode_int64(registers)
            scaled_value = round(raw_value * scale, 3)
        else:
            # Legacy 16-bit integer
            raw_value = registers[0]
            scaled_value = self.convert_value(raw_value, scale, unit)
        
        # PM2230 PF lead/lag conversion:
        # PF > 1.0 means leading (capacitive), actual PF = 2.0 - stored_value
        # PF <= 1.0 means lagging (inductive), value is direct
        if param_name.startswith('PF_') and scaled_value > 1.0:
            scaled_value = round(2.0 - scaled_value, 4)
        
        return {
            'name': param_name,
            'value': scaled_value,
            'unit': unit,
            'description': description,
            'address': address,
            'raw_value': raw_value
        }
    
    def read_all_parameters(self) -> Dict:
        """
        อ่านค่าทั้งหมด 36 Parameters แบบ Bulk Read เพื่อลด Latency เหลือ < 1 วินาที
        แทนการอ่านทีละตัวที่ใช้เวลา 5-6 วินาที
        
        Returns:
            Dict with all parameters
        """
        data = {
            'timestamp': datetime.now().isoformat(),
            'status': 'OK',
            'parameters': {}
        }
        
        if not self.connected:
            data['status'] = 'NOT_CONNECTED'
            return data
            
        errors = []
        
        # 1. อ่าน Block หลัก (Volt, Current, Power, Energy, Unbalance, PF บางส่วน)
        # Register 2999 - 3250
        retries = 3
        rust_success = False
        rust_blocks = {}
        
        if HAS_RUST_CORE:
            # Before attempting Rust, ensure pymodbus lets go of the COM port
            # In a real system, multiple concurrent accesses can corrupt the buffer
            if hasattr(self.client, 'is_socket_open') and self.client.is_socket_open():
                self.client.close()
                
            try:
                rust_res = pm2000_core.modbus_read_blocks(self.port, self.baudrate, self.slave_id)
                if rust_res.get("status") == "OK":
                    rust_blocks['r1'] = rust_res.get("block_2999", [])
                    rust_blocks['r2'] = rust_res.get("block_3190", [])
                    rust_blocks['r3'] = rust_res.get("block_21299", [])
                    rust_success = True
                else:
                    logger.warning(f"Rust modbus read returned ERROR: {rust_res.get('error')}")
            except Exception as e:
                logger.warning(f"Rust modbus exception fallback to pymodbus: {e}")
                
            # If Rust failed, re-open python client for fallback
            if not rust_success:
                self.client.connect()

        r1, r2, r3 = None, None, None # Initialize for scope
        if not rust_success:
            for attempt in range(retries):
                try:
                    r1 = self.client.read_holding_registers(address=2999, count=125, slave=self.slave_id)
                    r2 = self.client.read_holding_registers(address=3190, count=60, slave=self.slave_id)
                    r3 = self.client.read_holding_registers(address=21299, count=40, slave=self.slave_id)
                    break
                except Exception as e:
                    logger.error(f"Bulk read Exception: {e}")
                    if attempt < retries - 1:
                        logger.info(f"Retrying bulk read {attempt + 1}/{retries}...")
                        continue
                    data['status'] = 'ERROR'
                    return data

        # Helper function to extract registers from the bulk blocks
        def get_registers(address: int, quantity: int) -> Optional[List[int]]:
            if rust_success:
                if 2999 <= address < 2999 + 125:
                    offset = address - 2999
                    return rust_blocks['r1'][offset:offset+quantity]
                if 3190 <= address < 3190 + 60:
                    offset = address - 3190
                    return rust_blocks['r2'][offset:offset+quantity]
                if 21299 <= address < 21299 + 40:
                    offset = address - 21299
                    return rust_blocks['r3'][offset:offset+quantity]
                # Fallback to single read if not in block
                self.client.connect() # ensure Python client is up if doing individual reads
                return self.read_register(address, quantity)
                
            if r1 and not r1.isError() and 2999 <= address < 2999 + 125:
                offset = address - 2999
                return r1.registers[offset:offset+quantity]
            if r2 and not r2.isError() and 3190 <= address < 3190 + 60:
                offset = address - 3190
                return r2.registers[offset:offset+quantity]
            if r3 and not r3.isError() and 21299 <= address < 21299 + 40:
                offset = address - 21299
                return r3.registers[offset:offset+quantity]
                
            # Fallback to single read if not in block
            return self.read_register(address, quantity)

        for param_name, (address, quantity, scale, unit, description) in self.REGISTER_MAP.items():
            registers = get_registers(address, quantity)
            
            if registers:
                if quantity == 2 and len(registers) >= 2:
                    scaled_value = self._decode_float32(registers)
                    raw_value = (registers[0] << 16) | registers[1]
                elif quantity == 4 and len(registers) >= 4:
                    raw_value = self._decode_int64(registers)
                    scaled_value = round(raw_value * scale, 3)
                else:
                    raw_value = registers[0]
                    scaled_value = self.convert_value(raw_value, scale, unit)
                
                # PM2230 PF lead/lag conversion
                if param_name.startswith('PF_') and scaled_value > 1.0:
                    scaled_value = round(2.0 - scaled_value, 4)
                    
                data['parameters'][param_name] = {
                    'name': param_name,
                    'value': scaled_value,
                    'unit': unit,
                    'description': description,
                    'address': address,
                    'raw_value': raw_value
                }
            else:
                errors.append(param_name)
                data['parameters'][param_name] = {
                    'name': param_name,
                    'value': None,
                    'unit': unit,
                    'error': 'Read failed'
                }
        
        if errors:
            data['status'] = f'PARTIAL ({len(errors)} errors)'
            logger.warning(f"Failed to read: {', '.join(errors)}")
        
        return data
    
    def scan_registers(self, start_addr: int = 3100, end_addr: int = 3200, 
                       step: int = 1) -> Dict[int, int]:
        """
        สแกน Register Addresses (หาว่า Address ไหนมีข้อมูล)
        
        Args:
            start_addr: Start address
            end_addr: End address
            step: Step size
            
        Returns:
            Dict mapping address → value
        """
        logger.info(f"🔍 Scanning registers from {start_addr} to {end_addr}...")
        
        results = {}
        
        for addr in range(start_addr, end_addr, step):
            registers = self.read_register(addr, 1)
            
            if registers and len(registers) > 0:
                value = registers[0]
                if value != 0:  # ข้ามค่า 0 (อาจไม่มีข้อมูล)
                    results[addr] = value
                    logger.info(f"  Address {addr}: {value}")
        
        logger.info(f"✅ Found {len(results)} non-zero registers")
        return results
    
    def print_readable_data(self, data: Dict):
        """
        แสดงผลข้อมูลแบบ Human-readable
        
        Args:
            data: Data from read_all_parameters()
        """
        print("\n" + "=" * 70)
        print("📊 PM2230 - Power Monitoring Data")
        print("=" * 70)
        print(f"Timestamp: {data['timestamp']}")
        print(f"Status: {data['status']}")
        print("=" * 70)
        
        # จัดกลุ่ม Parameters
        groups = {
            '🔌 Voltage': ['V_LN1', 'V_LN2', 'V_LN3'],
            '⚡ Current': ['I_L1', 'I_L2', 'I_L3', 'I_N'],
            '📊 Frequency': ['Freq'],
            '💡 Active Power': ['P_L1', 'P_L2', 'P_L3', 'P_Total'],
            '📈 Apparent Power': ['S_L1', 'S_L2', 'S_L3', 'S_Total'],
            '⚡ Reactive Power': ['Q_L1', 'Q_L2', 'Q_L3', 'Q_Total'],
            '🌊 THD Voltage': ['THDv_L1', 'THDv_L2', 'THDv_L3'],
            '🌊 THD Current': ['THDi_L1', 'THDi_L2', 'THDi_L3'],
            '⚖️ Unbalance': ['V_unb', 'I_unb'],
            '📐 Power Factor': ['PF_L1', 'PF_L2', 'PF_L3', 'PF_Total'],
            '🔋 Energy': ['kWh_Total', 'kVAh_Total', 'kvarh_Total'],
        }
        
        for group_name, params in groups.items():
            print(f"\n{group_name}")
            print("-" * 50)
            
            for param in params:
                if param in data['parameters']:
                    p = data['parameters'][param]
                    value = p.get('value', 'N/A')
                    unit = p.get('unit', '')
                    
                    if value is not None:
                        print(f"  {param:15} = {value:>12} {unit}")
                    else:
                        print(f"  {param:15} = {'N/A':>12}")
        
        print("\n" + "=" * 70)


# === Main Test ===
if __name__ == "__main__":
    print("🔌 PM2230 Modbus Scanner & Reader")
    print("=" * 50)
    
    # สร้าง Scanner
    scanner = PM2230Scanner(
        port='COM3',      # ← แก้ COM Port ที่ใช้
        baudrate=9600,
        parity='E'        # Even parity (ตามรูป)
    )
    
    # เชื่อมต่อ
    if not scanner.connect():
        print("❌ Failed to connect! Check COM port and wiring.")
        exit(1)
    
    try:
        # วิธีที่ 1: อ่านค่าทั้งหมด
        print("\n📊 Reading all parameters...")
        data = scanner.read_all_parameters()
        scanner.print_readable_data(data)
        
        # วิธีที่ 2: สแกนหา Registers (ถ้าต้องการหา Address ใหม่)
        # print("\n🔍 Scanning registers...")
        # results = scanner.scan_registers(3100, 3200)
        
        # วิธีที่ 3: อ่าน Parameter เดียว
        # freq = scanner.read_parameter('Freq')
        # print(f"\nFrequency: {freq['value']} {freq['unit']}")
        
    finally:
        scanner.disconnect()


# === Module-level REGISTER_MAP for import ===
REGISTER_MAP = {
    name: {'address': info[0], 'scale': info[2], 'unit': info[3]}
    for name, info in PM2230Scanner.REGISTER_MAP.items()
}


# Simulator removed - using real device only


class PM2230Client:
    """Wrapper around PM2230Scanner that returns flat dicts for the API."""

    def __init__(self, port: str = 'COM3', baudrate: int = 9600,
                 slave_id: int = 1, parity: str = 'E'):
        self._scanner = PM2230Scanner(port, baudrate, slave_id, parity)

    @property
    def connected(self) -> bool:
        return self._scanner.connected

    @property
    def port(self) -> str:
        return self._scanner.port

    @property
    def baudrate(self) -> int:
        return self._scanner.baudrate

    @property
    def slave_id(self) -> int:
        return self._scanner.slave_id

    @property
    def parity(self) -> str:
        return self._scanner.parity

    def connect(self) -> bool:
        return self._scanner.connect()

    def disconnect(self):
        self._scanner.disconnect()

    def close(self):
        # Backward-compatible alias for older callers.
        self.disconnect()

    def read_all_parameters(self) -> Dict:
        """Read all parameters and return a flat dict."""
        raw = self._scanner.read_all_parameters()

        flat: Dict = {
            'timestamp': raw.get('timestamp', datetime.now().isoformat()),
            'status': raw.get('status', 'ERROR'),
        }

        parameters = raw.get('parameters', {})
        for param_name in self._scanner.REGISTER_MAP.keys():
            param_data = parameters.get(param_name, {})
            value = param_data.get('value') if isinstance(param_data, dict) else None
            flat[param_name] = value if value is not None else 0.0

        return flat
