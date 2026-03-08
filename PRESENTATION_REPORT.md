# PM2230 Dashboard: รายงานอธิบายการทำงานของระบบ

เอกสารนี้เขียนสำหรับใช้ประกอบการนำเสนอ โดยอธิบายการทำงานของระบบแบบภาษาคนทั่วไป และแทรกตัวอย่างโค้ดจากโปรเจคจริงเพื่อให้เห็นภาพว่าแต่ละส่วนทำงานอย่างไร

---

## 1. โครงงานนี้คืออะไร

โครงงานนี้เป็นระบบเว็บสำหรับดูค่าทางไฟฟ้าจากมิเตอร์ **Schneider PM2230** แบบเรียลไทม์  
ระบบสามารถ:

- อ่านค่าจากมิเตอร์ผ่านสาย `RS485`
- แสดงผลบนหน้าเว็บ
- แจ้งเตือนเมื่อค่าผิดปกติ
- บันทึกข้อมูลเป็นไฟล์ `CSV`
- ใช้ AI ช่วยสรุปและวิเคราะห์ข้อมูล

สรุปสั้น ๆ:

> ระบบนี้ทำหน้าที่เปลี่ยน "ข้อมูลดิบจากมิเตอร์" ให้กลายเป็น "ข้อมูลที่คนดูแล้วเข้าใจได้"

---

## 2. ภาพรวมการไหลของข้อมูล

ลำดับการทำงานของระบบมีดังนี้

1. มิเตอร์ PM2230 วัดค่าทางไฟฟ้า
2. มิเตอร์ส่งข้อมูลออกมาทาง RS485
3. คอมพิวเตอร์รับข้อมูลผ่าน USB-to-RS485
4. Backend อ่านข้อมูลจากมิเตอร์
5. Backend แปลงข้อมูลดิบเป็นตัวเลขจริง
6. Backend เปิด API ให้หน้าเว็บเรียก
7. Frontend ดึงข้อมูลทุก 1 วินาที
8. ถ้าค่าผิดปกติ ระบบจะแจ้งเตือนบนเว็บและส่ง LINE

ตัวอย่างโค้ดจาก Backend ที่วนอ่านข้อมูลทุก 1 วินาที:

```python
async def poll_modbus_data():
    while True:
        if SIMULATE_MODE:
            data = generate_simulated_data()
            cached_data = {**data, "timestamp": datetime.now().isoformat()}
        elif real_client and real_client.connected:
            data = real_client.read_all_parameters()
            cached_data = copy.deepcopy(data)

        await asyncio.sleep(1.0)
```

แนวคิดของโค้ดชุดนี้คือ:

- ถ้าอยู่โหมดจำลอง ให้สร้างข้อมูลจำลอง
- ถ้าอยู่โหมดใช้งานจริง ให้ไปอ่านจากมิเตอร์
- เก็บข้อมูลล่าสุดไว้
- ทำซ้ำทุก 1 วินาที

---

## 3. RS485 คืออะไร และต่อยังไง

RS485 คือรูปแบบการสื่อสารข้อมูลที่นิยมใช้กับอุปกรณ์วัดและระบบอุตสาหกรรม  
ในโครงงานนี้ RS485 เป็นตัวกลางที่ใช้ส่งข้อมูลจากมิเตอร์ PM2230 เข้ามาที่คอมพิวเตอร์

อุปกรณ์ที่ใช้:

- มิเตอร์ PM2230
- สาย RS485
- ตัวแปลง USB-to-RS485
- คอมพิวเตอร์

เมื่อเสียบเข้าคอมพิวเตอร์แล้ว ระบบจะมองเห็นเป็นพอร์ต เช่น `COM3` หรือ `COM4`

ตัวอย่างโค้ดที่สร้างการเชื่อมต่อ serial:

```python
self.client = ModbusSerialClient(
    port=port,
    baudrate=baudrate,
    parity=parity,
    stopbits=1,
    bytesize=8,
    timeout=1
)
```

ความหมายของโค้ดนี้คือ:

- `port` คือพอร์ตที่คอมเจอ เช่น `COM3`
- `baudrate` คือความเร็วในการสื่อสาร
- `parity` คือรูปแบบตรวจสอบความถูกต้องของข้อมูล

ค่าที่ใช้ในโปรเจคนี้โดยทั่วไปคือ:

- `9600 baud`
- `Even parity`
- `8 data bits`
- `1 stop bit`
- `slave id = 1`

---

## 4. ระบบรู้ได้ยังไงว่าต้องอ่านค่าจากตำแหน่งไหน

ภายในมิเตอร์ ข้อมูลแต่ละตัวจะเก็บอยู่ในตำแหน่งที่เรียกว่า **Register Address**

เช่น:

- กระแสไฟฟ้า อยู่ address หนึ่ง
- แรงดันไฟฟ้า อยู่ address หนึ่ง
- ความถี่ไฟฟ้า อยู่ address หนึ่ง

โปรเจคนี้กำหนดแผนที่ของข้อมูลไว้ใน `REGISTER_MAP`

ตัวอย่างจากโค้ด:

```python
REGISTER_MAP = {
    'I_L1': (2999, 2, 1.0, 'A', 'Current L1'),
    'V_LN1': (3027, 2, 1.0, 'V', 'Voltage L1-N'),
    'Freq': (3109, 2, 1.0, 'Hz', 'Frequency'),
    'P_Total': (3059, 2, 1.0, 'kW', 'Total Active Power'),
}
```

อธิบายแบบง่าย:

- `I_L1` อยู่ที่ address `2999`
- `V_LN1` อยู่ที่ address `3027`
- `Freq` อยู่ที่ address `3109`

ดังนั้นเวลาระบบอยากอ่านค่ากระแสเฟส 1 ก็จะไปขอข้อมูลที่ address `2999`

---

## 5. หา Address เหล่านี้เจอได้ยังไง

โปรเจคนี้ไม่ได้เดาค่าเอง แต่ใช้หลายวิธีร่วมกัน

1. อ้างอิงคู่มือ Modbus ของ PM2230
2. สแกน register ทีละช่วง
3. ทดลองเปิด-ปิดโหลดจริง แล้วดูว่าค่าไหนเปลี่ยนตาม

ตัวอย่างโค้ดสำหรับสแกน register:

```python
def scan_registers(self, start_addr: int = 3100, end_addr: int = 3200, step: int = 1) -> Dict[int, int]:
    results = {}
    for addr in range(start_addr, end_addr, step):
        registers = self.read_register(addr, 1)
        if registers and len(registers) > 0:
            value = registers[0]
            if value != 0:
                results[addr] = value
    return results
```

ความหมายคือ:

- ลองอ่านทีละ address
- ถ้า address ไหนมีข้อมูลจริง ก็จดไว้
- จากนั้นนำไปเปรียบเทียบกับพฤติกรรมจริงของมิเตอร์

ตัวอย่างการยืนยัน:

- เปิดโหลด แล้วค่ากระแสควรเพิ่ม
- ถ้า address ไหนเพิ่มตาม แปลว่า address นั้นน่าจะเป็นค่ากระแส

---

## 6. ข้อมูลดิบจากมิเตอร์แปลงเป็นตัวเลขจริงยังไง

ค่าหลายตัวใน PM2230 ไม่ได้ถูกเก็บเป็นเลขธรรมดา แต่เก็บเป็น **32-bit float**

พูดง่าย ๆ คือ:

- ค่าหนึ่งค่า ใช้พื้นที่ 2 ช่อง
- ระบบต้องเอา 2 ช่องนี้มาต่อกัน
- แล้วแปลออกมาเป็นเลขจริง เช่น `230.0 โวลต์`

ตัวอย่างโค้ด:

```python
def _decode_float32(self, registers: List[int]) -> float:
    import struct
    hi, lo = registers[0], registers[1]
    raw_bytes = struct.pack('>HH', hi, lo)
    val = struct.unpack('>f', raw_bytes)[0]
    return round(val, 4)
```

อธิบายทีละบรรทัด:

- `hi, lo` คือค่าจาก register 2 ตัว
- `struct.pack` นำ 2 ตัวนี้มาต่อกันเป็นชุดข้อมูล 32 บิต
- `struct.unpack` แปลข้อมูลนั้นให้เป็นเลขทศนิยม

สรุป:

> Backend ทำหน้าที่เป็น "ตัวแปลภาษา" จากภาษาของมิเตอร์ ให้กลายเป็นตัวเลขที่เราอ่านได้

---

## 7. ทำไมค่าพลังงานถึงอ่านไม่เหมือนแรงดันและกระแส

ค่าพลังงานสะสม เช่น `kWh_Total` ไม่ได้ใช้ 2 ช่อง แต่ใช้ 4 ช่อง  
เพราะเก็บเป็นเลขขนาดใหญ่กว่า

ตัวอย่างโค้ด:

```python
def _decode_int64(self, registers: List[int]) -> int:
    import struct
    raw_bytes = struct.pack('>HHHH', *registers)
    return struct.unpack('>q', raw_bytes)[0]
```

จากนั้นนำไปคูณสเกล:

```python
raw_value = self._decode_int64(registers)
scaled_value = round(raw_value * scale, 3)
```

ความหมายคือ:

- อ่านค่าดิบมาก่อน
- คูณตัวคูณที่กำหนด
- จึงได้ค่า `kWh` ที่ใช้งานจริง

---

## 8. Backend ทำหน้าที่อะไร

Backend เป็นศูนย์กลางของระบบทั้งหมด  
หน้าที่ของมันคือ:

- เชื่อมต่อกับ PM2230
- อ่านค่าทุก 1 วินาที
- เก็บข้อมูลล่าสุดไว้ในหน่วยความจำ
- ตรวจความผิดปกติ
- เปิด API ให้หน้าเว็บเรียก
- บันทึก CSV
- ส่งแจ้งเตือน LINE

ตัวอย่างโค้ดที่เก็บค่าล่าสุด:

```python
def get_latest_data() -> Dict:
    if cached_data:
        data = copy.deepcopy(cached_data)
    else:
        data = {
            'timestamp': datetime.now().isoformat(),
            'status': 'NOT_CONNECTED',
            'V_LN1': 0,
            'I_L1': 0,
            'Freq': 0,
        }
    return data
```

เหตุผลที่ต้องมีฟังก์ชันนี้:

- หน้าเว็บไม่ต้องไปอ่านมิเตอร์เอง
- ทุกหน้าจะใช้ข้อมูลล่าสุดชุดเดียวกัน
- ลดภาระการอ่านซ้ำจากอุปกรณ์จริง

---

## 9. API คืออะไรในระบบนี้

API คือช่องทางที่หน้าเว็บใช้ขอข้อมูลจาก Backend

ตัวอย่าง API จริงในโปรเจค:

```python
@app.get("/api/v1/page1")
async def get_page1(request: Request):
    data = get_latest_data()
    return {
        'timestamp': data['timestamp'],
        'status': data['status'],
        'V_LN1': data['V_LN1'],
        'V_LN2': data['V_LN2'],
        'V_LN3': data['V_LN3'],
        'I_L1': data['I_L1'],
        'I_L2': data['I_L2'],
        'I_L3': data['I_L3'],
        'Freq': data['Freq']
    }
```

ความหมายคือ:

- ถ้าหน้าเว็บเรียก `/api/v1/page1`
- Backend จะส่งข้อมูลหน้าแรกกลับไป
- เช่นแรงดัน กระแส และความถี่

ตัวอย่างคำสั่งจากหน้าเว็บ:

```javascript
const res = await fetch(`${API_BASE_URL}/page1?t=${Date.now()}`, { cache: 'no-store' });
return res.json();
```

ความหมายคือ:

- หน้าเว็บขอข้อมูลหน้า 1 จาก Backend
- เมื่อได้ข้อมูลกลับมา ก็เอาไปแสดงผลบนหน้าจอ

---

## 10. หน้าเว็บแสดงผลยังไง

หน้าเว็บจะดึงข้อมูลใหม่ทุก 1 วินาที เพื่อให้ผู้ใช้เห็นค่าล่าสุดตลอดเวลา

ตัวอย่างโค้ด:

```typescript
const POLLING_INTERVAL = 1000;

pollingRef.current = setInterval(() => {
  refresh(false);
}, POLLING_INTERVAL);
```

ความหมายของโค้ด:

- ทุก 1000 มิลลิวินาที หรือ 1 วินาที
- ให้ดึงข้อมูลใหม่อีกครั้ง

ผลที่ผู้ใช้เห็นคือ:

- ตัวเลขบนหน้าจออัปเดตตลอด
- กราฟเลื่อนไปเรื่อย ๆ
- ถ้ามี fault จะเห็นแจ้งเตือนแทบจะทันที

---

## 11. ระบบแจ้งเตือนทำงานยังไง

ถ้าค่าทางไฟฟ้าผิดปกติ ระบบจะตรวจจับอัตโนมัติ แล้วแจ้งเตือน 2 ทาง

- แจ้งเตือนบนหน้าเว็บ
- แจ้งเตือนผ่าน LINE

ตัวอย่างโค้ดฝั่งวิเคราะห์ fault:

```python
if v_avg > 250:
    alerts.append({
        "category": "voltage_swell",
        "severity": "high",
        "message": f"Voltage Swell: {v_avg:.1f}V",
    })
elif 0 < v_avg < 190:
    alerts.append({
        "category": "voltage_sag",
        "severity": "high",
        "message": f"Voltage Sag: {v_avg:.1f}V",
    })
```

ความหมายคือ:

- ถ้าแรงดันสูงเกินเกณฑ์ -> แจ้งเตือนแรงดันเกิน
- ถ้าแรงดันต่ำเกินเกณฑ์ -> แจ้งเตือนแรงดันตก

ตัวอย่างโค้ดฝั่งหน้าเว็บ:

```typescript
const ALERT_POLL_INTERVAL_MS = 1000;
const ALERT_REPEAT_INTERVAL_MS = 2000;
```

อธิบาย:

- หน้าเว็บจะเช็ก alert ทุก 1 วินาที
- ถ้า fault เดิมยังไม่หาย จะเตือนซ้ำทุก 2 วินาที

ตัวอย่างโค้ดที่ดึง alerts:

```typescript
const response: any = await fetchAlerts();
if (response?.status === 'ALERT' && Array.isArray(response?.alerts)) {
    playBeep();
    setAlerts(prev => [...prev, ...newAlerts]);
}
```

ผลที่เกิดขึ้น:

- มี toast เด้งที่มุมขวาล่าง
- มีเสียงเตือน
- ผู้ใช้รู้ทันทีว่าระบบมีปัญหา

---

## 12. ระบบส่ง LINE ทำงานยังไง

เมื่อพบ fault ระบบจะส่งข้อความไปยัง LINE โดยอัตโนมัติ

ตัวอย่างโค้ด:

```python
payload = {
    "to": LINE_USER_ID,
    "messages": [
        {
            "type": "text",
            "text": message
        }
    ]
}
```

ความหมายคือ:

- สร้างข้อความ
- ส่งไปยังผู้ใช้ LINE ที่ตั้งค่าไว้ใน `.env`

ดังนั้น ถ้าผู้ใช้ไม่ได้เปิดหน้าเว็บ ก็ยังได้รับการแจ้งเตือนผ่านโทรศัพท์ได้

---

## 13. ระบบจำลอง Fault มีไว้ทำไม

ระบบมีโหมดจำลองเพื่อใช้ทดสอบโดยไม่ต้องทำให้ระบบจริงเกิดปัญหา

ตัวอย่าง fault ที่จำลองได้:

- แรงดันตก
- แรงดันเกิน
- ไฟขาดเฟส
- โหลดเกิน
- ฮาร์มอนิกสูง
- แรงดันไม่สมดุล

ตัวอย่างโค้ด:

```python
if simulator_state.get("phase_loss"):
    v1 = random.uniform(5.0, 15.0)

if simulator_state.get("overload"):
    i_mult = 5.5
    i1 *= i_mult
    i2 *= i_mult
    i3 *= i_mult
```

อธิบาย:

- ถ้าเปิด `phase_loss` ระบบจะทำให้แรงดันเฟสหนึ่งตกลงมาก
- ถ้าเปิด `overload` ระบบจะทำให้กระแสเพิ่มสูงมาก

ประโยชน์:

- ใช้สาธิตระบบ
- ใช้ทดสอบการแจ้งเตือน
- ใช้ทดสอบ AI โดยไม่ต้องรอเหตุการณ์จริง

---

## 14. AI ในระบบนี้ช่วยอะไร

AI ในระบบนี้ไม่ได้เป็นคนอ่านมิเตอร์เอง  
แต่ทำหน้าที่ช่วย "ตีความข้อมูล" ที่ Backend อ่านมาแล้ว

AI ถูกใช้กับงานต่อไปนี้:

- สรุปภาพรวมระบบไฟฟ้า
- วิเคราะห์สาเหตุของ fault
- ทำนายความต้องการบำรุงรักษา
- วิเคราะห์การใช้พลังงาน
- ตอบคำถามผู้ใช้ในแชต

ตัวอย่างโค้ดที่เรียก AI สรุปผล:

```python
aggregated_data = await get_aggregated_data(samples=6, interval=1.0)
result = await generate_power_summary(aggregated_data)
```

ความหมาย:

- เก็บข้อมูล 6 รอบ
- เอามาเฉลี่ยก่อน
- ส่งให้ AI สรุปผล

เหตุผลที่ต้องเฉลี่ยก่อน:

- ทำให้ข้อมูลนิ่งขึ้น
- ลดความผันผวนชั่วคราว
- ทำให้ AI วิเคราะห์แม่นขึ้น

---

## 15. AI Chat ทำงานยังไง

ผู้ใช้สามารถพิมพ์คำถามบนหน้าเว็บ แล้วระบบจะส่งคำถามไปให้ AI ตอบกลับ

ตัวอย่างโค้ดฝั่งหน้าเว็บ:

```typescript
const response = await apiClient.postChat(newMessages);
setMessages(prev => [...prev, { role: 'assistant', content: response.response }]);
```

ตัวอย่างโค้ดฝั่ง Backend:

```python
body = await request.json()
messages = body.get("messages", [])
current_context = cached_data if cached_data else {}
response_text = await generate_chat_response(messages, current_context, recent_faults)
return {"response": response_text}
```

ความหมาย:

- หน้าเว็บส่งประวัติการสนทนาไป
- Backend แนบข้อมูลล่าสุดของระบบไปด้วย
- AI จึงตอบแบบมีบริบทของเครื่องจริง ไม่ได้ตอบลอย ๆ

---

## 16. ระบบจัดการข้อผิดพลาด (Fallback Mode)

ระบบมีการสลับโมเดลอัตโนมัติ (Fallback) และใช้งานผ่าน Proxy เพื่อความเสถียรและความปลอดภัย

แนวคิดคือ:

- เรียกใช้ AI ตัวหลัก (Mistral) ผ่าน **AI Proxy Server** ก่อน
- หาก API ตัวหลักช้า, ล่ม, หรือเครดิตหมด
- ระบบจะสลับไปเรียก AI ตัวสำรอง (DashScope) ทันทีแบบไร้รอยต่อ

ตัวอย่างโค้ดใน Backend:

```python
# พยายามเรียกตัวหลัก (Mistral ผ่าน Proxy / Local) ก่อนเสมอ
try:
    return await _call_mistral(messages)
except Exception as e:
    logger.warning(f"Mistral failed, falling back to Dashscope: {e}")
    # ถ้าพัง ให้สลับไปเรียกตัวสำรองทันที
    return await _call_dashscope(messages)
```

ความหมาย:

- พยายามยิงไปที่ตัวที่เก่งที่สุดก่อน
- ถ้ามีปัญหาระหว่างทาง โปรแกรมจะไม่ค้างหรือเด้ง Error ใส่หน้าผู้ใช้
- แต่จะสลับไปหาตัวสำรองแทน

ประโยชน์:

- ผู้ใช้งานจะไม่รู้สึกสะดุด (Seamless UX)
- ลดความเสี่ยงในการพึ่งพา Provider เจ้าเดียว
- การใช้ Proxy ช่วยซ่อน API Key ไม่ให้หลุดไปอยู่ที่เครื่องของผู้ใช้

---

## 17. ระบบบันทึกข้อมูลยังไง

ระบบสามารถบันทึกข้อมูลลงไฟล์ `CSV` ได้ 2 แบบ

- บันทึกค่าทั่วไป
- บันทึกเฉพาะตอนเกิด fault

ตัวอย่างโค้ด:

```python
with open(log_filename, mode='a', newline='', encoding='utf-8') as file:
    writer = csv.writer(file)
    flat_data = get_latest_data()
    row = [flat_data.get(header, "") for header in log_headers]
    writer.writerow(row)
```

ความหมาย:

- ดึงข้อมูลล่าสุด
- เรียงตามหัวตาราง
- เขียนลงไฟล์ CSV

ประโยชน์:

- ใช้ทำรายงานย้อนหลัง
- ใช้ฝึกโมเดล
- ใช้วิเคราะห์เหตุการณ์ที่เคยเกิดขึ้น

---

## 18. ระบบแบ่งหน้า Dashboard อย่างไร

เพื่อให้ดูง่าย หน้าเว็บถูกแบ่งเป็น 4 ส่วนหลัก

1. ภาพรวม
2. กำลังไฟฟ้า
3. คุณภาพไฟฟ้า
4. พลังงาน

ตัวอย่างโค้ด API:

```python
@app.get("/api/v1/page1")
@app.get("/api/v1/page2")
@app.get("/api/v1/page3")
@app.get("/api/v1/page4")
```

แนวคิดคือ:

- แต่ละหน้าใช้ข้อมูลต่างกัน
- แยก API จะทำให้หน้าเว็บจัดการง่าย
- เวลาปรับปรุงหรือเพิ่มส่วนใหม่ก็ทำได้ชัดเจน

---

## 19. จุดเด่นของโครงงานนี้

- อ่านค่าจากมิเตอร์จริงผ่าน RS485 ได้
- มีระบบเว็บดูข้อมูลแบบเรียลไทม์
- มีระบบแจ้งเตือนทั้งบนเว็บและ LINE
- มี simulator สำหรับสาธิตและทดสอบ
- มี AI ช่วยวิเคราะห์ข้อมูล
- มีระบบบันทึกข้อมูลย้อนหลัง

---

## 20. สรุปท้ายรายงาน

โครงงานนี้มีเป้าหมายเพื่อทำให้การอ่านค่าจากมิเตอร์ PM2230 ง่ายขึ้น และใช้งานได้จริงในรูปแบบเว็บ

หัวใจสำคัญของระบบคือ:

- อ่านข้อมูลดิบจากมิเตอร์
- แปลงเป็นตัวเลขที่เข้าใจได้
- วิเคราะห์ความผิดปกติ
- แจ้งเตือนผู้ใช้ได้ทันเวลา
- ใช้ AI ช่วยสรุปผลและวิเคราะห์ต่อยอด

สรุปเป็นประโยคเดียว:

> ระบบนี้เปลี่ยนข้อมูลดิบจาก PM2230 ให้กลายเป็นระบบเฝ้าดูไฟฟ้าที่ดูง่าย แจ้งเตือนได้ และวิเคราะห์ต่อได้

---

## ภาคผนวก: ตัวอย่าง API สำคัญในระบบ

| API | หน้าที่ |
|---|---|
| `/api/v1/page1` | ดึงข้อมูลแรงดัน กระแส ความถี่ |
| `/api/v1/page2` | ดึงข้อมูลกำลังไฟฟ้า |
| `/api/v1/page3` | ดึงข้อมูลคุณภาพไฟฟ้า |
| `/api/v1/page4` | ดึงข้อมูลพลังงาน |
| `/api/v1/alerts` | ดึงรายการ fault ล่าสุด |
| `/api/v1/ai-summary` | ให้ AI สรุปภาพรวมระบบ |
| `/api/v1/ai-fault-summary` | ให้ AI วิเคราะห์ fault log |
| `/api/v1/chat` | คุยกับ AI |
| `/api/v1/datalog/start` | เริ่มบันทึกข้อมูล |
| `/api/v1/datalog/download` | ดาวน์โหลดไฟล์ log |

## ภาคผนวก: ตัวอย่างโค้ดการเรียก API จากหน้าเว็บ

```typescript
const res = await fetch(`${API_BASE_URL}/page1?t=${Date.now()}`, { cache: 'no-store' });
return res.json();
```

ความหมาย:

- เรียกข้อมูลล่าสุดจาก Backend
- ขอแบบไม่ใช้ cache เดิมของ browser
- นำผลลัพธ์มาแสดงบนหน้าเว็บ

## ภาคผนวก: ตัวอย่างโค้ดการเชื่อมต่ออุปกรณ์จริง

```python
client, reason = connect_client(
    port=connect_params.port,
    baudrate=connect_params.baudrate,
    slave_id=connect_params.slave_id,
    parity=connect_params.parity,
    validate_reading=validate,
)
```

ความหมาย:

- ระบุพอร์ตที่ต้องการเชื่อมต่อ
- ระบุความเร็วและรูปแบบการสื่อสาร
- ทดสอบว่าอ่านค่าจริงได้หรือไม่

ถ้าอ่านได้ ระบบจะถือว่าเชื่อมต่อสำเร็จ
