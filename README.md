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

## 🏗️ สถาปัตยกรรม

```
backend-server.exe (FastAPI + Uvicorn)
  ├─ /api/v1/*        → API routes (Modbus + AI)
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

## ✨ AI Analysis (Bonus)

ระบบวิเคราะห์ข้อมูลพลังงานด้วย AI (DashScope Qwen)  
คีย์ AI ถูกฝังอยู่ใน `backend-server.exe` เรียบร้อยแล้ว

---

## 🛠️ การ Build ใหม่ (สำหรับ Developer)

```bat
.\build-windows.bat
```

สคริปต์จะ:
1. ถามหา DashScope API Key
2. Build Next.js frontend
3. Pack ทุกอย่างเป็น `backend-server.exe`

---

**Last Updated:** 2026-03-04  
**Status:** Ready for Presentation 🎓
