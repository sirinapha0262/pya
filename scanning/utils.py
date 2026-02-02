# scanning/utils.py
# ⭐ Utility functions สำหรับระบบสแกน (ปรับปรุงใหม่)
# ===========================================
# กฎการเช็คชื่อ:
#   - แตะบัตร 05:30 - 08:20: มาปกติ
#   - แตะบัตร 08:20 - 09:00: มาสาย
#   - ไม่แตะบัตร หรือ หลัง 09:00: ขาด
#   - ทำงานเฉพาะ จ-ศ
# ===========================================

from datetime import datetime, time
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)

# ========================================
# ⭐ การตั้งค่าเวลา (ตามข้อกำหนดใหม่)
# ========================================

# เวลาสแกนเข้า
CHECK_IN_START = time(5, 30)       # เริ่มเปิดรับสแกนเข้า
CHECK_IN_ON_TIME = time(8, 20)     # มาตรงเวลา (ถึง 08:20 น.)
CHECK_IN_LATE_END = time(9, 0)     # สิ้นสุดช่วงมาสาย (หลังจากนี้ถือว่าขาด)

# เวลาสแกนออก
CHECK_OUT_START = time(15, 25)     # เริ่มเปิดรับสแกนออก
CHECK_OUT_END = time(18, 0)        # สิ้นสุดการสแกนออก

# วันเรียน: จันทร์(0) - ศุกร์(4)
SCHOOL_DAYS = [0, 1, 2, 3, 4]


def is_school_day(check_date=None):
    """
    ⭐ ตรวจสอบว่าเป็นวันเรียน (จ-ศ) หรือไม่
    
    Args:
        check_date: วันที่ต้องการตรวจสอบ (default = วันนี้)
    
    Returns:
        bool: True ถ้าเป็นวันเรียน
    """
    if check_date is None:
        check_date = timezone.now().date()
    return check_date.weekday() in SCHOOL_DAYS


def determine_scan_status(scan_time, is_check_in=True):
    """
    ⭐ กำหนดสถานะการสแกนตามเวลา (ปรับปรุงใหม่)
    
    กฎ:
    - แตะบัตร 05:30 - 08:20: มา (present)
    - แตะบัตร 08:20 - 09:00: มาสาย (late)
    - แตะบัตร หลัง 09:00: ขาด (absent)
    
    Args:
        scan_time: เวลาที่สแกน (datetime object)
        is_check_in: True สำหรับการเข้า, False สำหรับการออก
        
    Returns:
        str: สถานะ ('มา', 'มาสาย', 'ขาด', 'ออกก่อนเวลา', 'ออก')
    """
    if isinstance(scan_time, datetime):
        current_time = scan_time.time()
    else:
        current_time = scan_time
    
    if is_check_in:
        # ⭐ กำหนดเวลาเข้าตามข้อกำหนดใหม่
        # 05:30 - 08:20 = มาปกติ
        if CHECK_IN_START <= current_time <= CHECK_IN_ON_TIME:
            return 'มา'
        # 08:20 - 09:00 = มาสาย
        elif CHECK_IN_ON_TIME < current_time <= CHECK_IN_LATE_END:
            return 'มาสาย'
        # หลัง 09:00 หรือก่อน 05:30 = ขาด (แต่ก่อน 05:30 อาจถือว่ามาเช้ามากก็ได้)
        elif current_time > CHECK_IN_LATE_END:
            return 'ขาด'
        else:
            # ก่อน 05:30 ถือว่ามาเช้ามาก = มาปกติ
            return 'มา'
    else:
        # กำหนดเวลาออก
        if current_time < CHECK_OUT_START:
            return 'ออกก่อนเวลา'
        else:
            return 'ออก'


def get_attendance_status_thai(status_code):
    """
    แปลงสถานะภาษาอังกฤษเป็นภาษาไทย
    
    Args:
        status_code: 'present', 'late', 'absent'
    
    Returns:
        str: สถานะภาษาไทย
    """
    mapping = {
        'present': 'มา',
        'late': 'มาสาย',
        'absent': 'ขาด',
        'early_leave': 'ออกก่อนเวลา',
        'checkout': 'ออก'
    }
    return mapping.get(status_code, status_code)


def get_scan_type_from_status(status):
    """
    แปลงสถานะเป็นประเภทการสแกน
    
    Args:
        status: สถานะ ('มา', 'มาสาย', 'ขาด', 'ออกก่อนเวลา', 'ออก')
        
    Returns:
        str: ประเภทการสแกน ('check_in', 'check_out')
    """
    if status in ['มา', 'มาสาย', 'ขาด', 'present', 'late', 'absent']:
        return 'check_in'
    else:
        return 'check_out'


def calculate_confidence_level(confidence_score):
    """
    แปลงค่า confidence score เป็นระดับความมั่นใจ
    
    Args:
        confidence_score: ค่าความมั่นใจ (0-1)
        
    Returns:
        str: ระดับความมั่นใจ ('สูง', 'ปานกลาง', 'ต่ำ')
    """
    if confidence_score >= 0.8:
        return 'สูง'
    elif confidence_score >= 0.6:
        return 'ปานกลาง'
    else:
        return 'ต่ำ'


def format_scan_time(scan_time):
    """
    จัดรูปแบบเวลาการสแกน
    
    Args:
        scan_time: เวลาที่สแกน (datetime object)
        
    Returns:
        str: เวลาในรูปแบบที่อ่านง่าย
    """
    return scan_time.strftime('%d/%m/%Y %H:%M:%S')


def get_today_date_range():
    """
    ⭐ คืนค่าช่วงเวลาของวันนี้
    
    Returns:
        tuple: (start_of_day, end_of_day)
    """
    today = timezone.now().date()
    start_of_day = datetime.combine(today, time.min)
    end_of_day = datetime.combine(today, time.max)
    return start_of_day, end_of_day


def get_date_range(date_from_str, date_to_str=None):
    """
    ⭐ Helper function สำหรับสร้าง datetime range
    
    Args:
        date_from_str: วันที่เริ่มต้น (YYYY-MM-DD)
        date_to_str: วันที่สิ้นสุด (YYYY-MM-DD), ถ้าไม่ระบุจะใช้เท่ากับ date_from
        
    Returns:
        tuple: (start_datetime, end_datetime) หรือ (None, None) ถ้าผิดพลาด
    """
    try:
        start_date = datetime.strptime(date_from_str, '%Y-%m-%d').date()
        
        if date_to_str:
            end_date = datetime.strptime(date_to_str, '%Y-%m-%d').date()
        else:
            end_date = start_date
        
        start_datetime = datetime.combine(start_date, time.min)
        end_datetime = datetime.combine(end_date, time.max)
        
        return start_datetime, end_datetime
    except ValueError:
        return None, None


def is_duplicate_scan(student, scan_type, time_threshold_minutes=5):
    """
    ตรวจสอบว่าเป็นการสแกนซ้ำหรือไม่
    
    Args:
        student: Student object
        scan_type: ประเภทการสแกน ('check_in', 'check_out')
        time_threshold_minutes: ระยะเวลาที่ถือว่าเป็นการสแกนซ้ำ (นาที)
        
    Returns:
        bool: True ถ้าเป็นการสแกนซ้ำ
    """
    from .models import RFIDScanLog
    from datetime import timedelta
    
    threshold_time = timezone.now() - timedelta(minutes=time_threshold_minutes)
    
    recent_scan = RFIDScanLog.objects.filter(
        student=student,
        scan_type=scan_type,
        scan_time__gte=threshold_time,
        status='success'
    ).first()
    
    return recent_scan is not None


def create_system_alert(alert_type, severity, title, message, device=None, student=None):
    """สร้างการแจ้งเตือนของระบบ"""
    from .models import SystemAlert
    
    try:
        alert = SystemAlert.objects.create(
            alert_type=alert_type,
            severity=severity,
            title=title,
            message=message,
            device=device,
            student=student
        )
        logger.info(f"System alert created: {title}")
        return alert
    except Exception as e:
        logger.error(f"Error creating system alert: {str(e)}", exc_info=True)
        return None


def update_device_statistics(device, scan_success):
    """
    อัพเดทสถิติของอุปกรณ์
    
    Args:
        device: DeviceStatus object
        scan_success: True ถ้าสแกนสำเร็จ, False ถ้าล้มเหลว
    """
    try:
        device.total_scans += 1
        if scan_success:
            device.successful_scans += 1
        else:
            device.failed_scans += 1
        device.save()
        logger.info(f"Device statistics updated: {device.device_name}")
    except Exception as e:
        logger.error(f"Error updating device statistics: {str(e)}", exc_info=True)


def get_attendance_status_from_scan(student, today=None):
    """
    ⭐ ดึงสถานะการเข้าเรียนของนักเรียนในวันที่ระบุ
    
    Args:
        student: Student object
        today: วันที่ต้องการตรวจสอบ (optional, default=วันนี้)
        
    Returns:
        dict: สถานะการเข้าเรียน
    """
    from .models import RFIDScanLog
    
    if today is None:
        today = timezone.now().date()
    
    start_of_day = datetime.combine(today, time.min)
    end_of_day = datetime.combine(today, time.max)
    
    # หาการสแกนเข้าและออก
    check_in = RFIDScanLog.objects.filter(
        student=student,
        scan_type='check_in',
        scan_time__range=(start_of_day, end_of_day),
        status='success'
    ).first()
    
    check_out = RFIDScanLog.objects.filter(
        student=student,
        scan_type='check_out',
        scan_time__range=(start_of_day, end_of_day),
        status='success'
    ).first()
    
    # กำหนดสถานะ
    if check_in:
        attendance_status = determine_scan_status(check_in.scan_time, True)
    else:
        attendance_status = 'ไม่มาเรียน'
    
    return {
        'date': today,
        'is_school_day': is_school_day(today),
        'has_checked_in': check_in is not None,
        'has_checked_out': check_out is not None,
        'check_in_time': check_in.scan_time if check_in else None,
        'check_out_time': check_out.scan_time if check_out else None,
        'attendance_status': attendance_status,
    }


def generate_daily_report(date=None):
    """⭐ สร้างรายงานประจำวัน"""
    from .models import DailyReport, RFIDScanLog, FaceRecognitionLog, DeviceStatus
    from my.models import Student
    
    if date is None:
        date = timezone.now().date()
    
    # ตรวจสอบวันเรียน
    if not is_school_day(date):
        logger.info(f"Skipping daily report for {date} - not a school day")
        return None
    
    start_of_day = datetime.combine(date, time.min)
    end_of_day = datetime.combine(date, time.max)
    
    try:
        total_students = Student.objects.filter(is_active=True).count()
        
        present = 0
        late = 0
        absent = 0
        
        for student in Student.objects.filter(is_active=True):
            check_in = RFIDScanLog.objects.filter(
                student=student,
                scan_type='check_in',
                scan_time__range=(start_of_day, end_of_day),
                status='success'
            ).first()
            
            if check_in:
                status = determine_scan_status(check_in.scan_time, True)
                if status == 'มา':
                    present += 1
                elif status == 'มาสาย':
                    late += 1
                else:
                    absent += 1
            else:
                absent += 1
        
        total_scans = RFIDScanLog.objects.filter(
            scan_time__range=(start_of_day, end_of_day)
        ).count()
        
        successful_scans = RFIDScanLog.objects.filter(
            scan_time__range=(start_of_day, end_of_day),
            status='success'
        ).count()
        
        report, created = DailyReport.objects.update_or_create(
            report_date=date,
            defaults={
                'total_students': total_students,
                'students_present': present,
                'students_late': late,
                'students_absent': absent,
                'total_scans': total_scans,
                'successful_scans': successful_scans,
                'failed_scans': total_scans - successful_scans,
            }
        )
        
        logger.info(f"Daily report {'created' if created else 'updated'} for {date}")
        return report
        
    except Exception as e:
        logger.error(f"Error generating daily report: {str(e)}", exc_info=True)
        return None


def get_attendance_summary(date=None):
    """
    ⭐ สรุปสถานะการเข้าเรียนของวันที่กำหนด
    
    Args:
        date: วันที่ต้องการสรุป (default = วันนี้)
    
    Returns:
        dict: สรุปสถานะการเข้าเรียน
    """
    from .models import RFIDScanLog
    from my.models import Student
    
    if date is None:
        date = timezone.now().date()
    
    start_of_day = datetime.combine(date, time.min)
    end_of_day = datetime.combine(date, time.max)
    
    total_students = Student.objects.filter(is_active=True).count()
    
    # นับจากการสแกนจริง
    check_ins = RFIDScanLog.objects.filter(
        scan_type='check_in',
        scan_time__range=(start_of_day, end_of_day),
        status='success'
    )
    
    present_count = 0
    late_count = 0
    absent_from_late_arrival = 0
    
    for scan in check_ins:
        status = determine_scan_status(scan.scan_time, True)
        if status == 'มา':
            present_count += 1
        elif status == 'มาสาย':
            late_count += 1
        else:
            absent_from_late_arrival += 1
    
    # นักเรียนที่ไม่มาสแกนเลย
    students_with_scans = check_ins.values_list('student_id', flat=True).distinct()
    no_show_count = total_students - len(set(students_with_scans))
    
    total_absent = absent_from_late_arrival + no_show_count
    
    return {
        'date': str(date),
        'day_name': get_thai_day_name(date.weekday()),
        'is_school_day': is_school_day(date),
        'total_students': total_students,
        'present': present_count,
        'late': late_count,
        'absent': total_absent,
        'attendance_rate': round((present_count + late_count) / total_students * 100, 2) if total_students > 0 else 0
    }


def get_thai_day_name(weekday):
    """แปลงเลขวันเป็นชื่อวันภาษาไทย"""
    days = ['จันทร์', 'อังคาร', 'พุธ', 'พฤหัสบดี', 'ศุกร์', 'เสาร์', 'อาทิตย์']
    return days[weekday]
