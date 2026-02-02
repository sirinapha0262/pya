# ระบบหักคะแนนพฤติกรรมอัตโนมัติ
## One Card Smart School V.2

---

## 📋 กฎการหักคะแนน

| สถานะ | เวลา | คะแนนที่หัก |
|-------|------|------------|
| ✅ มาตรงเวลา | 05:30 - 08:20 | 0 คะแนน |
| ⏰ มาสาย | 08:21 - 09:00 | 1 คะแนน |
| ❌ ขาดเรียน | ไม่มาสแกน หรือ มาหลัง 09:00 | 2 คะแนน |
| 🚪 ไม่สแกนออก | ไม่มีการสแกนออกก่อน 17:00 | 1 คะแนน |

- **คะแนนเริ่มต้น**: 100 คะแนน
- **วันเรียน**: จันทร์ - ศุกร์

---

## 📁 โครงสร้างไฟล์

```
my/
└── models.py                    # Model หลัก (User, Student, BehaviorRecord, SchoolSettings)

scanning/
├── views.py                     # ⭐ API หลัก (ScanProcessView + หักคะแนนอัตโนมัติ)
├── urls.py                      # URL routing
├── behavior_scoring.py          # ระบบหักคะแนนอัตโนมัติ
└── management/
    └── commands/
        └── process_behavior_scores.py   # Management command
```

---

## 🔧 วิธีติดตั้ง

### 1. คัดลอกไฟล์
```bash
# คัดลอก my/models.py ไปแทนที่ไฟล์เดิม
cp my/models.py /path/to/your/project/my/models.py

# คัดลอก scanning/views.py ไปแทนที่ไฟล์เดิม
cp scanning/views.py /path/to/your/project/scanning/views.py

# คัดลอก scanning/urls.py ไปแทนที่ไฟล์เดิม
cp scanning/urls.py /path/to/your/project/scanning/urls.py

# คัดลอก scanning/behavior_scoring.py ไปแทนที่ไฟล์เดิม
cp scanning/behavior_scoring.py /path/to/your/project/scanning/behavior_scoring.py

# คัดลอก management command
mkdir -p /path/to/your/project/scanning/management/commands
cp scanning/management/__init__.py /path/to/your/project/scanning/management/
cp scanning/management/commands/__init__.py /path/to/your/project/scanning/management/commands/
cp scanning/management/commands/process_behavior_scores.py /path/to/your/project/scanning/management/commands/
```

### 2. Migrate ฐานข้อมูล
```bash
python manage.py makemigrations
python manage.py migrate
```

### 3. ตั้งค่าเริ่มต้น (ถ้ายังไม่มี)
```python
# ใน Django shell
from my.models import SchoolSettings

SchoolSettings.objects.create(
    school_name='โรงเรียนบ้านหนองหญ้าปล้อง',
    academic_year='2568',
    school_start_time='05:30',
    normal_arrival_time='08:20',
    late_arrival_time='09:00',
    school_end_time='15:25',
    latest_departure_time='17:00',
    default_behavior_score=100,
    late_penalty_points=1,
    absent_penalty_points=2,
    no_checkout_penalty_points=1,
    auto_deduct_enabled=True
)
```

---

## 🚀 วิธีใช้งาน

### การหักคะแนนแบบ Real-time (ตอนสแกน)

ระบบจะหักคะแนนอัตโนมัติทันทีเมื่อนักเรียนสแกนบัตร:
- **มาสาย**: หักทันทีตอนสแกน
- **ขาด (มาหลัง 09:00)**: หักทันทีตอนสแกน

```python
# ใน views.py (ScanProcessView) - มีอยู่แล้ว
from scanning.behavior_scoring import integrate_behavior_scoring_with_scan

# เรียกใช้หลังจากสแกนสำเร็จ
if attendance_status in ['มาสาย', 'ขาด']:
    behavior_result = integrate_behavior_scoring_with_scan(
        student=student,
        scan_type='check_in',
        scan_time=current_time,
        attendance_status=attendance_status
    )
```

### การหักคะแนนแบบ Batch (รันทีหลัง)

สำหรับกรณี:
- **ขาดเรียน**: นักเรียนที่ไม่มาสแกนเลย
- **ไม่สแกนออก**: นักเรียนที่มาเรียนแต่ไม่สแกนออก

```bash
# ประมวลผลวันนี้
python manage.py process_behavior_scores

# ระบุวันที่
python manage.py process_behavior_scores --date=2025-01-15

# ทดสอบโดยไม่บันทึก
python manage.py process_behavior_scores --dry-run

# เฉพาะชั้น ป.6
python manage.py process_behavior_scores --grade=6

# เฉพาะห้อง 1
python manage.py process_behavior_scores --classroom=1

# ประมวลผล 7 วันย้อนหลัง
python manage.py process_behavior_scores --week

# บังคับประมวลผลใหม่
python manage.py process_behavior_scores --force
```

---

## ⏰ ตั้งเวลารันอัตโนมัติ

### Linux/Mac (Cron)
```bash
# แก้ไข crontab
crontab -e

# เพิ่มบรรทัดนี้ (รันทุกวันจันทร์-ศุกร์ เวลา 17:30 น.)
30 17 * * 1-5 cd /path/to/your/project && /path/to/venv/bin/python manage.py process_behavior_scores >> /var/log/behavior_scores.log 2>&1
```

### Windows (Task Scheduler)
1. เปิด Task Scheduler
2. สร้าง Task ใหม่
3. ตั้งค่า:
   - **Program**: `C:\path\to\python.exe`
   - **Arguments**: `manage.py process_behavior_scores`
   - **Start in**: `C:\path\to\your\project`
   - **Trigger**: Daily, 17:30, วันจันทร์-ศุกร์

---

## 📊 ดูสรุปคะแนน

```python
from scanning.behavior_scoring import behavior_scoring_service

# สรุปคะแนนของนักเรียน
student = Student.objects.get(student_id='12345')
summary = behavior_scoring_service.get_student_behavior_summary(student)
print(summary)

# สรุปประจำวัน
daily = behavior_scoring_service.get_daily_summary()
print(daily)
```

---

## ⚙️ ปรับแต่งค่า

### ผ่าน Django Admin
1. ไปที่ `การตั้งค่าโรงเรียน` ใน Django Admin
2. แก้ไขค่าที่ต้องการ:
   - เวลาเข้าเรียน
   - คะแนนที่หัก
   - เปิด/ปิดการหักคะแนนอัตโนมัติ

### ผ่าน Code
```python
from my.models import SchoolSettings

settings = SchoolSettings.objects.first()
settings.late_penalty_points = 2  # เปลี่ยนเป็นหัก 2 คะแนน
settings.save()
```

---

## 🔄 รีเซ็ตคะแนน (เริ่มภาคเรียนใหม่)

```python
from scanning.behavior_scoring import reset_all_behavior_scores

# รีเซ็ตคะแนนนักเรียนทุกคนกลับเป็น 100
count = reset_all_behavior_scores()
print(f'รีเซ็ตแล้ว {count} คน')
```

---

## 📝 หมายเหตุ

1. **การหักคะแนนซ้ำ**: ระบบจะตรวจสอบไม่ให้หักคะแนนซ้ำในวันเดียวกัน
2. **วันหยุด**: ระบบจะข้ามวันเสาร์-อาทิตย์โดยอัตโนมัติ
3. **Real-time vs Batch**:
   - มาสาย → หักทันทีตอนสแกน (Real-time)
   - ขาด (มาหลัง 09:00) → หักทันทีตอนสแกน (Real-time)
   - ขาด (ไม่มาเลย) → หักตอนรัน Batch
   - ไม่สแกนออก → หักตอนรัน Batch

---

## 🐛 การแก้ไขปัญหา

### ปัญหา: คะแนนไม่ถูกหัก
1. ตรวจสอบว่าเปิดใช้งาน `auto_deduct_enabled = True`
2. ตรวจสอบว่าเป็นวันเรียน (จันทร์-ศุกร์)
3. ตรวจสอบว่ายังไม่มีการหักซ้ำในวันเดียวกัน

### ปัญหา: ต้องการหักคะแนนใหม่
```bash
python manage.py process_behavior_scores --force
```

### ปัญหา: ต้องการดูแต่ไม่อยากบันทึก
```bash
python manage.py process_behavior_scores --dry-run
```

---

**พัฒนาโดย**: One Card Smart School Team
**เวอร์ชัน**: 2.0
**วันที่อัปเดต**: มกราคม 2568

*******************************
# ⭐ ระบบเช็คชื่ออัตโนมัติ

## 📋 กฎการเช็คชื่อ

| เวลา | สถานะ | การหักคะแนน |
|------|-------|-------------|
| 05:30 - 08:20 | มาปกติ (present) | ไม่หักคะแนน |
| 08:20 - 09:00 | มาสาย (late) | หัก **1** คะแนน |
| หลัง 09:00 หรือไม่แตะบัตร | ขาด (absent) | หัก **2** คะแนน |
| ไม่สแกนออก | - | หัก **1** คะแนน |

📅 **ทำงานเฉพาะวันจันทร์ - ศุกร์**

---

## 📁 ไฟล์ที่รวมอยู่

```
auto_attendance_system/
├── behavior_scoring.py      # ระบบคำนวณและหักคะแนนพฤติกรรม
├── utils.py                  # Utility functions
├── tasks.py                  # Celery tasks (optional)
├── attendance_auto_detector.py  # ระบบตรวจจับอัตโนมัติ (สำหรับ my app)
├── management/
│   └── commands/
│       └── auto_attendance.py   # Management command
└── README.md                 # คู่มือนี้
```

---

## 🚀 วิธีการติดตั้ง

### 1. คัดลอกไฟล์

```bash
# คัดลอกไฟล์ไปยัง scanning app
cp behavior_scoring.py /path/to/project/scanning/
cp utils.py /path/to/project/scanning/
cp tasks.py /path/to/project/scanning/

# คัดลอกไฟล์ไปยัง my app
cp attendance_auto_detector.py /path/to/project/my/

# คัดลอก management command
mkdir -p /path/to/project/scanning/management/commands
cp management/__init__.py /path/to/project/scanning/management/
cp management/commands/__init__.py /path/to/project/scanning/management/commands/
cp management/commands/auto_attendance.py /path/to/project/scanning/management/commands/
```

### 2. ตั้งค่า Cron Job (แนะนำ)

```bash
# เปิด crontab editor
crontab -e

# เพิ่มบรรทัดต่อไปนี้:

# ประมวลผลขาดเรียน - รัน 09:30 น. ทุกวัน จ-ศ
30 9 * * 1-5 /path/to/venv/bin/python /path/to/manage.py auto_attendance --absent-only >> /var/log/attendance.log 2>&1

# ประมวลผลไม่สแกนออก - รัน 17:30 น. ทุกวัน จ-ศ  
30 17 * * 1-5 /path/to/venv/bin/python /path/to/manage.py auto_attendance --no-checkout-only >> /var/log/attendance.log 2>&1
```

### 3. (Optional) ตั้งค่า Celery

ถ้าต้องการใช้ Celery แทน Cron:

```python
# settings.py
CELERY_BROKER_URL = 'redis://localhost:6379/0'
CELERY_RESULT_BACKEND = 'redis://localhost:6379/0'

CELERY_BEAT_SCHEDULE = {
    'process-daily-absent': {
        'task': 'process_daily_absent',
        'schedule': crontab(hour=9, minute=30, day_of_week='1-5'),
    },
    'process-daily-no-checkout': {
        'task': 'process_daily_no_checkout',
        'schedule': crontab(hour=17, minute=30, day_of_week='1-5'),
    },
}
```

---

## 🛠️ วิธีใช้งาน Management Command

### ประมวลผลวันนี้
```bash
python manage.py auto_attendance
```

### ประมวลผลวันที่กำหนด
```bash
python manage.py auto_attendance --date 2024-01-15
```

### ประมวลผลเฉพาะขาดเรียน
```bash
python manage.py auto_attendance --absent-only
```

### ประมวลผลเฉพาะไม่สแกนออก
```bash
python manage.py auto_attendance --no-checkout-only
```

### ทดสอบโดยไม่บันทึกจริง (Dry Run)
```bash
python manage.py auto_attendance --dry-run
```

### ประมวลผลย้อนหลัง 7 วัน
```bash
python manage.py auto_attendance --days-back 7
```

---

## 📊 API Endpoints

### ประมวลผลคะแนนพฤติกรรม
```
POST /scanning/process-behavior-scores/
Authorization: Bearer <token>

{
    "date": "2024-01-15",  // optional
    "dry_run": false
}
```

### ดูสรุปคะแนนพฤติกรรม
```
GET /scanning/behavior-summary/
Authorization: Bearer <token>
```

---

## ⚙️ การปรับแต่งเวลา

แก้ไขค่าใน `SchoolSettings` model หรือแก้ไขค่า default ใน `behavior_scoring.py`:

```python
# behavior_scoring.py - _set_defaults()

# เวลาสแกนเข้า
self.CHECK_IN_START = time(5, 30)       # เริ่มเปิดรับสแกนเข้า
self.CHECK_IN_ON_TIME = time(8, 20)     # มาตรงเวลา (ถึง 08:20)
self.CHECK_IN_LATE_END = time(9, 0)     # สิ้นสุดช่วงมาสาย

# คะแนนที่หัก
self.LATE_DEDUCTION = 1       # มาสาย
self.ABSENT_DEDUCTION = 2     # ขาด
self.NO_CHECKOUT_DEDUCTION = 1  # ไม่สแกนออก
```

---

## 📝 Flow การทำงาน

### Real-time (ตอนแตะบัตร)
```
นักเรียนแตะบัตร
    ↓
ScanProcessView รับข้อมูล
    ↓
ตรวจสอบเวลา
    ↓
กำหนดสถานะ (มา/มาสาย/ขาด)
    ↓
ถ้ามาสาย/ขาด → สร้าง BehaviorRecord → หักคะแนนอัตโนมัติ
    ↓
บันทึก RFIDScanLog
    ↓
ส่ง Response กลับ
```

### Batch Processing (Cron/Celery)
```
09:30 น. - process_daily_absent
    ↓
ตรวจสอบนักเรียนที่ไม่มาแตะบัตร
    ↓
สร้าง BehaviorRecord (absent) → หัก 2 คะแนน

17:30 น. - process_daily_no_checkout
    ↓
ตรวจสอบนักเรียนที่สแกนเข้าแต่ไม่สแกนออก
    ↓
สร้าง BehaviorRecord (no_checkout) → หัก 1 คะแนน
```

---

## 🔍 การดีบัก

### ดู Log
```bash
tail -f /var/log/attendance.log
```

### ตรวจสอบ BehaviorRecord
```python
from my.models import BehaviorRecord
from datetime import date

# ดูการหักคะแนนวันนี้
today = date.today()
records = BehaviorRecord.objects.filter(date_recorded=today, is_auto=True)
for r in records:
    print(f"{r.student.student_id}: {r.auto_type} - {r.points} คะแนน")
```

### ตรวจสอบนักเรียนที่ไม่มาวันนี้
```python
from scanning.behavior_scoring import behavior_scoring_service

summary = behavior_scoring_service.get_daily_summary()
print(summary)
```

---

## ❓ FAQ

**Q: ถ้านักเรียนมาสายแล้วระบบหักคะแนนไปแล้ว จะยกเลิกได้ไหม?**
A: ได้ โดยลบ BehaviorRecord ที่เกี่ยวข้องออก และเพิ่มคะแนนกลับให้นักเรียน

**Q: ถ้าวันหยุดพิเศษ (นอกจากเสาร์-อาทิตย์) ระบบจะทำอย่างไร?**
A: ปัจจุบันระบบยังไม่รองรับวันหยุดพิเศษ สามารถรัน --dry-run ก่อนและตรวจสอบ

**Q: ถ้าต้องการปรับเวลามาตรงเวลา/มาสาย?**
A: แก้ไขใน SchoolSettings หรือ _set_defaults() ใน behavior_scoring.py

---

## 📞 ติดต่อ

หากพบปัญหาหรือต้องการความช่วยเหลือ กรุณาติดต่อผู้ดูแลระบบ
