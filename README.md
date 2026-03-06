# 📊 PM2230 Dashboard

**วิชา:** 01026325 - ระบบควบคุมอัตโนมัติในอาคารและอุตสาหกรรม  
**อาจารย์:** รศ.ดร.เชาว์ ชมภูอินไหว

---

## 📋 ข้อมูล Project

| รายการ | รายละเอียด |
|--------|-----------|
| **Meter** | Schneider PM2230 |
| **Communication** | Modbus RTU over RS485 |
| **Parameters** | 36 ค่า |
| **Dashboard** | 4 หน้า |
| **AI Features** | 4 ฟีเจอร์ (Parallel LLM) |
| **ส่งงาน** | 10 มีนาคม 2568 |
| **นำเสนอ** | 11 มีนาคม 2568 |

---

## 🚀 วิธีใช้งาน (สำหรับ Windows)

> ดูคำแนะนำเพิ่มเติมได้ที่ [README-WINDOWS.md](README-WINDOWS.md)

### ขั้นตอนสั้นๆ:
1. **ดับเบิ้ลคลิก** `start-web.bat`
2. **รอ 3 วินาที** → Browser เปิดอัตโนมัติ
3. เข้า Dashboard ที่: **http://localhost:8003**

---

## 🤖 AI Features (ใหม่!)

ระบบวิเคราะห์ด้วย AI ที่ใช้ **Parallel LLM** (เรียกหลายโมเดลพร้อมกัน) เพื่อความเร็วและความน่าเชื่อถือ

| ฟีเจอร์ | รายละเอียด | LLM |
|---------|-----------|-----|
| **🚀 AI Power Analysis** | วิเคราะห์สถานะไฟฟ้าภาพรวม | Mistral + DashScope |
| **🚨 AI Fault Analysis** | วิเคราะห์สาเหตุ Fault | Parallel LLM |
| **🔮 Predictive Maintenance** | ทำนายการบำรุงรักษาล่วงหน้า | Parallel LLM |
| **⚡ Energy Management** | วิเคราะห์ประสิทธิภาพพลังงาน | Parallel LLM |
| **💬 AI Advisor Chat** | ถามตอบกับ AI แบบ real-time | DashScope |

### Parallel LLM Mode
- เรียก **Mistral AI + DashScope** พร้อมกัน
- เลือกคำตอบที่ดีที่สุดโดยอัตโนมัติ (Quality Scoring)
- เร็วขึ้น ~40-50% เมื่อเทียบกับการเรียกตัวเดียว
- มี Auto-fallback ถ้าตัวใดตัวหนึ่งล่ม

### การใช้งาน AI Advisor Chat
1. กดปุ่ม **"💬 ถามต่อ"** จากผลวิเคราะห์ใดก็ได้
2. Chat จะเปิดอัตโนมัติพร้อมส่ง context ไปให้ AI
3. คุยต่อได้ทันที!

---

## 🏗️ สถาปัตยกรรม

```
backend-server.exe (FastAPI + Uvicorn)
  ├─ /api/v1/*        → API routes (Modbus + AI)
  ├─ /api/v1/ai-*     → AI Analysis endpoints
  ├─ /api/v1/chat     → AI Advisor Chat
  └─ /*               → Frontend (Next.js Static)
```

ตัวโปรแกรมทุกอย่างถูกรวมไว้ใน `backend-server.exe` ไฟล์เดียว  
ไม่ต้องติดตั้ง Python, Node.js หรือ dependency ใดๆ ครับ

---

## 🔌 การตั้งค่า Modbus (PM2230)

| Parameter | ค่า Default |
|-----------|------------|
| **Baud Rate** | 9600 bps |
| **Data Bits** | 8 |
| **Parity** | Even |
| **Stop Bits** | 1 |
| **Slave ID** | 1 |

---

## 📊 Dashboard 4 หน้า

| หน้า | เนื้อหา |
|------|--------|
| **1. ภาพรวม** | Voltage, Current, Frequency |
| **2. กำลังไฟฟ้า** | P, Q, S, Power Factor |
| **3. คุณภาพไฟ** | THD, Unbalance |
| **4. พลังงาน** | kWh, kVAh, kvarh |

---

## ✨ สิ่งที่เพิ่มเข้ามา (Latest Update)

### AI & Analytics
- **Parallel LLM Router**: เรียก Mistral + DashScope พร้อมกัน เลือกคำตอบดีที่สุด
- **AI Advisor Chat**: แชทบอทถามตอบได้แบบ real-time พร้อม context จากผลวิเคราะห์
- **4 AI Panels**: Power Analysis, Fault Analysis, Predictive Maintenance, Energy Management
- **Auto-expand Results**: แสดงผล AI ทันทีหลังวิเคราะห์เสร็จ

### UI/UX
- **Mobile First Optimization**: รองรับการใช้งานบนมือถืออย่างสมบูรณ์
- **Natural Scroll Header**: ซ่อนแถบเมนูตามการเลื่อนเพื่อเพิ่มพื้นที่
- **One-Click Actions**: ปุ่ม Clear, Export, Ask AI ครบทุก panel

### Technical
- **Caching System**: AI responses ถูก cache 5 นาที ลดการเรียก API ซ้ำ
- **Race Condition Fix**: ใช้ asyncio.Lock ป้องกันการเข้าถึงข้อมูลพร้อมกัน
- **Quality Scoring**: AI เลือกคำตอบตาม content length, structure, technical terms

---

## ⚙️ การตั้งค่า Environment Variables

สร้างไฟล์ `backend/.env`:

```env
# AI Providers (ต้องการอย่างน้อย 1 ตัว)
MISTRAL_API_KEY=your_mistral_key_here
MISTRAL_AGENT_ID=your_agent_id_here

DASHSCOPE_API_KEY=your_dashscope_key_here
DASHSCOPE_MODEL=qwen3.5-plus
DASHSCOPE_FALLBACK_MODEL=qwen3-max-2026-01-23

# Line Notify (optional)
LINE_CHANNEL_ACCESS_TOKEN=your_line_token
LINE_USER_ID=your_user_id
```

---

## 🛠️ การ Build ใหม่ (สำหรับ Developer)

```bat
# Windows
.\build-windows.bat

# หรือ manual
cd frontend && npm run build
cd ../backend
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python build.py
```

สคริปต์จะ:
1. ถามหา API Keys
2. Build `backend-server` สำหรับ API
3. Export Next.js frontend
4. Copy frontend ไปไว้ที่ `backend/dist/frontend_web`

---

## 📝 Requirements

- Windows 10/11 (64-bit)
- USB-to-RS485 Adapter (สำหรับเชื่อมต่อ PM2230)
- Python 3.12 (สำหรับ development)
- Node.js 18+ (สำหรับ development)

---

**Last Updated:** 2026-03-06 (05:45)  
**Status:** Presentation Ready 🎓🚀


---

## Web Alerts

- The dashboard shows fault toasts at the bottom-right corner of the page.
- The web alert polling interval is 1 second.
- Active faults repeat by category every approximately 2 seconds while the same fault remains active.
- LINE notifications are independent from the web toast flow.
- Simulator faults and real PM2230 faults use the same /api/v1/alerts category-based web alert logic.

**Last Updated Note:** Web alert timing adjusted on 2026-03-06.
