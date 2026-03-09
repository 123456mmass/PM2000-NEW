import time
import logging
from pm2230_client import PM2230Client

def test_usb_connection():
    print("=========================================")
    print("🔌 PM2230 USB-RS485 Connection Test")
    print("=========================================")
    
    # User instructions
    print("กรุณาตรวจสอบก่อนเริ่มเทส:")
    print("1. เสียบสาย USB to RS485 เข้าคอมพิวเตอร์แล้ว")
    print("2. เช็คเบอร์ COM Port ใน Device Manager (เช่น COM3, COM4)")
    print("3. ต่อสาย A(+) และ B(-) เข้ามิเตอร์ PM2230 แน่นหนา")
    print("=========================================")
    
    com_port = input("\n👉 กรอกเบอร์ COM Port (เช่น COM3) แล้วกด Enter: ").strip().upper()
    
    if not com_port.startswith("COM"):
        print("❌ รูปแบบ COM Port ไม่ถูกต้อง ต้องขึ้นต้นด้วย COM")
        return

    print(f"\n⏳ กำลังเชื่อมต่อไปที่ {com_port}...")
    
    # Initialize client with user's COM port
    client = PM2230Client(
        port=com_port,
        baudrate=9600, # Default PM2230
        slave_id=1     # Default PM2230 slave ID
    )
    
    try:
        logging.basicConfig(level=logging.INFO)
        logging.getLogger().info("Initializing Core Modbus Engine...")
        
        print("\n📡 กำลังดึงข้อมูลจากมิเตอร์...")
        
        # Connect to COM port
        if not client.connect():
            print(f"\n❌ ไม่สามารถเปิดพอร์ต {com_port} ได้ (พอร์ตอาจจะถูกโปรแกรมอื่นใช้งานอยู่ หรือสายหลุด)")
            return
            
        # Synchronous data pull
        data = client.read_all_parameters()
        
        if data:
            if data.get("status") == "ERROR":
                print("\n❌ เชื่อมต่อได้ แต่ดึงข้อมูลล้มเหลว!")
                print("สาเหตุที่เป็นไปได้:")
                print("1. สลับสาย A/B ผิด (ลองสลับสายดู)")
                print("2. ตั้ง Slave ID ที่หน้าปัดมิเตอร์เป็นเบอร์อื่น (ไม่ใช่เบอร์ 1)")
                print("3. ตั้ง Baudrate ที่มิเตอร์ไม่ตรงกับ 9600")
                print(f"Error Log: {data.get('error_message')}")
            else:
                print("\n✅ เชื่อมต่อมิเตอร์สำเร็จ!! ข้อมูลส่งเข้า Rust Engine ได้สมบูรณ์")
                print("--- ตัวอย่างค่าที่อ่านได้ ---")
                print(f"⚡ แรงดัน (V_LN_avg): {data.get('V_LN_avg', 0):.2f} V")
                print(f"🌊 กระแส (I_avg): {data.get('I_avg', 0):.2f} A")
                print(f"🔄 ความถี่ (Freq): {data.get('Freq', 0):.2f} Hz")
                print(f"🔋 พาวเวอร์แฟคเตอร์ (PF): {data.get('PF_Total', 0):.2f}")
                
                alerts = client.get_active_alerts()
                if alerts:
                    print(f"\n⚠️ พบความผิดปกติ {len(alerts)} รายการ (วิเคราะห์รวดเร็วโดย Rust):")
                    for alert in alerts:
                        print(f" - [{alert.get('severity', 'high').upper()}] {alert.get('message', '')}")
                        print(f"   คำแนะนำ: {alert.get('detail', '')}")
                else:
                    print("\n🟢 ระบบไฟฟกติ ไม่มีแจ้งเตือนตามมาตรฐาน กฟภ./วสท.")
                    
        else:
            print("\n❌ ไม่ได้รับข้อมูลใดๆ จากมิเตอร์เลย")
            
    except Exception as e:
        print(f"\n❌ เกิดข้อผิดพลาดร้ายแรงระหว่างรัน: {e}")
        
    finally:
        print("\n🛑 กำลังปิดการเชื่อมต่อ...")
        client.close()
        print("ปิดโปรแกรมเรียบร้อยแล้วครับ")

if __name__ == "__main__":
    try:
        test_usb_connection()
    except KeyboardInterrupt:
        print("\n\nยกเลิกโดยผู้ใช้")
