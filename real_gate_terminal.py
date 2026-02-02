import requests
import time
import sys
import json
import cv2          # เรียกใช้งานกล้อง
import base64       # สำหรับแปลงรูปภาพส่งให้ Server

# ==========================================
# ⚙️ ตั้งค่าการเชื่อมต่อ (Configuration)
# ==========================================
SERVER_URL = "http://127.0.0.1:8000/scanning/scan/" 
DEVICE_NAME = "GATE_01"          
DEVICE_LOCATION = "ประตูหน้า"      
CAMERA_INDEX = 0  # 0 = กล้อง Webcam ในเครื่อง, 1 = กล้องตัวนอก

def print_banner():
    print("=" * 50)
    print("🚀 ระบบเช็คชื่อ One Card Smart School (โหมดใช้งานจริง + กล้อง)")
    print(f"📡 เชื่อมต่อ Server: {SERVER_URL}")
    print("📷 สถานะกล้อง: กำลังเชื่อมต่อ...")
    print("=" * 50)

# เริ่มต้นเปิดกล้อง
cap = cv2.VideoCapture(CAMERA_INDEX)

# ปรับความละเอียดกล้อง (ลดขนาดเพื่อให้ส่งไวขึ้น)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

if cap.isOpened():
    print("✅ กล้องพร้อมใช้งาน!")
else:
    print("❌ ไม่เจอกล้อง (จะทำงานแบบไม่มีรูป)")

def capture_image_base64():
    """ฟังก์ชันถ่ายรูปและแปลงเป็น Base64"""
    if not cap.isOpened():
        return None
    
    # อ่านภาพจากกล้อง
    ret, frame = cap.read()
    if ret:
        # แปลงภาพเป็น JPEG
        _, buffer = cv2.imencode('.jpg', frame)
        # แปลงเป็น Base64 String
        img_str = base64.b64encode(buffer).decode('utf-8')
        # เติม Header ให้ Server รู้ว่าเป็นรูปภาพ
        return f"data:image/jpeg;base64,{img_str}"
    return None

def process_card(card_id):
    print(f"⚡ ได้รับรหัสบัตร: '{card_id}'")
    
    # 📸 ถ่ายรูปทันทีที่สแกน
    print("📷 กำลังบันทึกภาพ...")
    image_data = capture_image_base64()
    
    print("📡 กำลังส่งข้อมูล...")

    payload = {
        "rfid_card_id": card_id,
        "device_name": DEVICE_NAME,
        "device_location": DEVICE_LOCATION,
        "scan_type": "auto",
        "image": image_data  # ✅ ส่งรูปภาพไปด้วย (ถ้ามี)
    }

    try:
        response = requests.post(SERVER_URL, json=payload, timeout=8) # เพิ่มเวลา timeout เผื่อส่งรูปช้า
        
        if response.status_code == 200:
            data = response.json()
            student_name = data.get('student', {}).get('name', 'ไม่ระบุชื่อ')
            status_text = data.get('attendance_status_thai', 'บันทึกสำเร็จ')
            
            print(f"✅ Server ตอบกลับ: บันทึกสำเร็จ")
            print(f"   👤 นักเรียน: {student_name}")
            print(f"   🕒 สถานะ: {status_text}")
            
            if data.get('points_deducted', 0) > 0:
                print(f"   ⚠️ ถูกหักคะแนน: {data['points_deducted']} คะแนน")
                
        elif response.status_code == 400:
            print(f"❌ Server ตอบกลับผิดพลาด (400)")
            try:
                print(f"   Error: {response.json().get('error', '')}")
            except: pass
        elif response.status_code == 404:
            print(f"❌ ไม่พบข้อมูลนักเรียน (404)")
        else:
            print(f"❌ Server Error ({response.status_code})")

    except requests.exceptions.ConnectionError:
        print("❌ ไม่สามารถเชื่อมต่อ Server ได้")
    except Exception as e:
        print(f"❌ Error: {str(e)}")

def main():
    print_banner()
    
    try:
        while True:
            # เพื่อไม่ให้โปรแกรมค้าง เราจะอ่าน input แบบรอ
            # หมายเหตุ: ในโหมด Terminal ปกติจะไม่เห็นภาพ Preview สดๆ 
            # แต่จะถ่ายตอนกด Enter (สแกน) ทันที
            
            card_input = input("\n👉 กรุณาแตะบัตร (Waiting): ").strip()
            
            if not card_input:
                continue
            
            process_card(card_input)
            print("\nพร้อมสแกนคนต่อไป...")
            
    except KeyboardInterrupt:
        print("\n\n🔴 ปิดโปรแกรม")
    finally:
        # ปิดกล้องเมื่อจบโปรแกรม
        if cap.isOpened():
            cap.release()

if __name__ == "__main__":
    main()