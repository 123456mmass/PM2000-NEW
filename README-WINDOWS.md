# 🪟 PM2230 Dashboard - คู่มือสำหรับ Windows

## 🚀 วิธีใช้งาน (ง่ายที่สุด)

### ขั้นที่ 1: แตกไฟล์ ZIP
แตก ZIP ที่ได้รับออกมา จะได้ไฟล์ดังนี้:
```
📁 PM2230-Dashboard/
  ├── backend-server.exe   ← ตัวโปรแกรมหลัก
  └── start-web.bat        ← ไฟล์เริ่มต้นโปรแกรม
```

### ขั้นที่ 2: รันโปรแกรม
**ดับเบิ้ลคลิก** ที่ `start-web.bat`

### ขั้นที่ 3: เปิด Dashboard
Browser จะเปิดอัตโนมัติไปที่:  
**http://localhost:8003**

**เสร็จแล้ว!** 🎉 ไม่ต้องติดตั้งอะไรเพิ่มเติมทั้งนั้น

---

## 🎯 วิธีใช้ Dashboard

| Tab | เนื้อหา |
|-----|--------|
| **📊 ภาพรวม** | Voltage, Current, Frequency |
| **⚡ กำลังไฟฟ้า** | Active/Reactive/Apparent Power |
| **📈 คุณภาพไฟฟ้า** | THD, Unbalance, Power Factor |
| **🔋 พลังงาน** | kWh, kVAh, kvarh |
| **✨ AI Analysis** | วิเคราะห์แนวโน้มพลังงานด้วย AI |

---

## 🔌 การเชื่อมต่อ PM2230

1. ต่อสายสัญญาณ RS485 จาก PM2230 เข้า PC
2. เปิดโปรแกรมแล้วกดปุ่ม **"Auto Connect"** ใน Dashboard
3. โปรแกรมจะค้นหาพอร์ต COM อัตโนมัติ

**Setting เริ่มต้น:** Baud Rate 9600, Parity Even, Slave ID 1

---

## 🛑 วิธีปิดโปรแกรม

กด **Ctrl+C** ในหน้าต่าง Terminal  
หรือปิดหน้าต่าง Terminal โดยตรงครับ

---

## ❓ แก้ปัญหาเบื้องต้น

### โปรแกรมไม่เปิด / ค้าง
→ ลองดับเบิ้ลคลิก `start-web.bat` ใหม่อีกครั้ง

### Port 8003 ถูกใช้งานอยู่
→ `start-web.bat` จะเคลียร์ port ให้อัตโนมัติครับ

### Browser ไม่เปิดอัตโนมัติ
→ เปิด Browser แล้วพิมพ์ `http://localhost:8003` ด้วยตัวเองครับ

### ไม่เจอ Device PM2230
→ ตรวจสอบสาย RS485 และ Driver ของ USB-RS485 Adapter

---

**สำหรับ:** Windows 10 / 11  
**Last Updated:** 2026-03-04
