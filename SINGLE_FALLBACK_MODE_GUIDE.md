# 🤖 Single Fallback Mode & AI Proxy - คู่มือการใช้งาน

(อัปเดตแทนระบบ Parallel LLM เดิมเพื่อความเสถียรและความปลอดภัย)

---

## 🎯 ทำไมถึงเปลี่ยนจาก Parallel เป็น Single Fallback?

| คุณสมบัติ | Parallel (ระบบเก่า) | Single Fallback (ระบบใหม่ล่าสุด) |
|-----------|------------------|--------------------------------|
| **Architecture** | Client ยิงตรงไป Mistral + DashScope | Client ยิงไป Proxy → Proxy สลับไปโมเดลต่างๆ ให้อัตโนมัติ |
| **API Keys** | ต้องฝัง API Key ทุกตัวไว้ในแอป | ใช้ Proxy App Key เดียว ซ่อน API Provider ไว้หลังบ้านทั้งหมด |
| **Resource** | กินเน็ตเวิร์ก/bandwidth สองเท่า | ใช้แบนด์วิดท์น้อยมาก (ยิงตัวเดียว) |
| **ความเสถียร** | ควบคุมยากหาก rate limit โดนแบนพร้อมกัน | จัดการง่าย ตัวหลักตายตัวสำรองทำงานต่อทันที |

---

## 🚀 สถาปัตยกรรมใหม่ (AI Proxy Network)

ระบบ Dashboard ของเราจะเชื่อมต่อกับ **AI Proxy Server** (`proxy.pichat.me`) 

1. **Dashboard** ขอให้วิเคราะห์ไฟฟ้ารวม
2. **Dashboard** ยิง request ไปที่ Proxy
3. **Proxy** จะส่งไปหา **Mistral AI (ตัวหลัก)** ก่อนเสมอ (เร็วและฉลาดที่สุดสำหรับวิเคราะห์ภาษาไทย)
4. ถ้า **Mistral** มีปัญหา (เช่น 403, 422, Rate Limit) **Proxy หรือ Backend จะสลับไปเรียก DashScope (ตัวสำรอง)** อัตโนมัติทันทีแบบไร้รอยต่อ

ผู้ใช้ฝั่งหน้าเว็บจะไม่รู้เลยว่าหลังบ้านมีการสลับโมเดล จะรู้แค่ผลลัพธ์มาตามปกติ

---

## ⚙️ Environment Variables (.env)

ใช้แค่นี้แทนระบบเก่าที่ต้องกรอกทุกอัน:

```env
# AI Proxy (หลัก)
PROXY_URL=https://proxy.pichat.me
PROXY_APP_KEY=friend1_abc123

# Dashscope สำรอง (ใช้กรณีไม่เปิด Proxy Mode)
DASHSCOPE_API_KEY=your_dashscope_key_here
```

---

## 💡 ประโยชน์ที่ได้รับกับโปรเจค PM2230

1. **ความปลอดภัย 100%**: ไม่ต้องกลัวคนดึง API Key Mistral ไปใช้ เพราะถูกซ่อนไว้ที่ Proxy หมด
2. **ประหยัดค่าใช้จ่ายและเรตลิมิต**: Mistral ได้ถูกย้ายไปใช้ Endpoint ที่เปิดให้ใช้ Free-tier ได้
3. **เสถียรภาพ**: การ Streaming แชทกลับมาจะไม่กระตุกหรือขาดตอนแบบระบบยิงตรง
4. **โค้ดสะอาดขึ้น**: ตัดโค้ด scoring และ parallel threading ที่หนักเครื่องออกไป ทำให้แอปเบาลง
