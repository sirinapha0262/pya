# my/attendance_auto_detector.py
# ⭐ โมดูลสำหรับตรวจจับและบันทึกการขาดเรียนอัตโนมัติ (ปรับปรุงใหม่)
# ===========================================
# กฎการเช็คชื่อ:
#   - แตะบัตร 05:30 - 08:20: มาปกติ
#   - แตะบัตร 08:20 - 09:00: มาสาย (หัก 1 คะแนน)
#   - ไม่แตะบัตร: ขาด (หัก 2 คะแนน)
#   - ทำงานเฉพาะ จ-ศ
# ===========================================

from django.utils import timezone
from django.db.models import Q
from datetime import datetime, time, timedelta
import logging

logger = logging.getLogger(__name__)

# วันเรียน: จันทร์(0) - ศุกร์(4)
SCHOOL_DAYS = [0, 1, 2, 3, 4]


def is_school_day(check_date=None):
    """ตรวจสอบว่าเป็นวันเรียน (จ-ศ) หรือไม่"""
    if check_date is None:
        check_date = timezone.now().date()
    return check_date.weekday() in SCHOOL_DAYS


def auto_mark_absent_for_missing_records(target_date=None):
    """
    ⭐ ตรวจสอบและบันทึกการขาดเรียนสำหรับนักเรียนที่ไม่มีบันทึกการเข้าเรียนในวันนั้น
    ⭐ การหักคะแนนจะทำอัตโนมัติผ่าน BehaviorRecord
    
    Args:
        target_date: วันที่ต้องการประมวลผล (default = วันนี้)
    
    Returns:
        dict: ผลการประมวลผล
    """
    from .models import Student, AttendanceRecord, BehaviorRecord, SchoolSettings
    
    try:
        if target_date is None:
            target_date = timezone.now().date()
        
        result = {
            'date': str(target_date),
            'success': True,
            'absent_marked': 0,
            'already_processed': 0,
            'weekend_skip': False
        }
        
        # ⭐ ตรวจสอบว่าเป็นวันเรียนหรือไม่
        if not is_school_day(target_date):
            logger.info(f"⏭️ ข้าม: วันที่ {target_date} ไม่ใช่วันเรียน (เสาร์-อาทิตย์)")
            result['weekend_skip'] = True
            return result
        
        active_students = Student.objects.filter(is_active=True)
        students_with_records = AttendanceRecord.objects.filter(
            date=target_date
        ).values_list('student_id', flat=True)
        
        students_without_records = active_students.exclude(
            id__in=students_with_records
        )
        
        # ดึงค่าคะแนนหักจาก settings
        settings = SchoolSettings.objects.first()
        absent_penalty = settings.absent_penalty_points if settings else 2
        
        for student in students_without_records:
            # ตรวจสอบว่ามี BehaviorRecord ขาดแล้วหรือยัง
            existing = BehaviorRecord.objects.filter(
                student=student,
                date_recorded=target_date,
                is_auto=True,
                auto_type='absent'
            ).exists()
            
            if existing:
                result['already_processed'] += 1
                continue
            
            # สร้าง AttendanceRecord สถานะ absent
            AttendanceRecord.objects.create(
                student=student,
                date=target_date,
                status='absent',
                is_manual_entry=False,
                notes='ระบบบันทึกอัตโนมัติ - ไม่มีการแตะบัตรเข้า'
            )
            
            # สร้าง BehaviorRecord หักคะแนน
            BehaviorRecord.objects.create(
                student=student,
                behavior_type='deduct',
                points=absent_penalty,
                reason=f'[AUTO] ขาดเรียน - ไม่มีการแตะบัตรเข้าวันที่ {target_date} (หัก {absent_penalty} คะแนน)',
                date_recorded=target_date,
                recorded_by=None,
                is_auto=True,
                auto_type='absent'
            )
            
            result['absent_marked'] += 1
            logger.info(f"❌ บันทึกขาดเรียนอัตโนมัติสำหรับ {student.student_id}")
        
        logger.info(f"📊 ประมวลผลขาดเรียนวันที่ {target_date}: {result['absent_marked']} คน")
        return result
        
    except Exception as e:
        logger.error(f"เกิดข้อผิดพลาด: {str(e)}", exc_info=True)
        return {'success': False, 'error': str(e)}


def auto_deduct_no_checkout(target_date=None):
    """
    ⭐ หักคะแนนสำหรับนักเรียนที่ไม่สแกนออก
    
    Args:
        target_date: วันที่ต้องการประมวลผล (default = วันนี้)
    
    Returns:
        dict: ผลการประมวลผล
    """
    from .models import Student, AttendanceRecord, BehaviorRecord, SchoolSettings
    
    try:
        if target_date is None:
            target_date = timezone.now().date()
        
        result = {
            'date': str(target_date),
            'success': True,
            'no_checkout_count': 0,
            'already_processed': 0,
            'weekend_skip': False
        }
        
        # ตรวจสอบว่าเป็นวันเรียนหรือไม่
        if not is_school_day(target_date):
            result['weekend_skip'] = True
            return result
        
        settings = SchoolSettings.objects.first()
        
        if not settings or not settings.auto_deduct_enabled:
            return result
        
        no_checkout_penalty = settings.no_checkout_penalty_points
        
        # หา AttendanceRecord ที่มี check_in แต่ไม่มี check_out
        no_checkout_records = AttendanceRecord.objects.filter(
            date=target_date,
            check_in_time__isnull=False,
            check_out_time__isnull=True,
            is_penalty_applied=False
        )
        
        for record in no_checkout_records:
            # ตรวจสอบว่าหักคะแนนไม่สแกนออกแล้วหรือยัง
            existing = BehaviorRecord.objects.filter(
                student=record.student,
                date_recorded=target_date,
                is_auto=True,
                auto_type='no_checkout'
            ).exists()
            
            if existing:
                result['already_processed'] += 1
                continue
            
            # สร้าง BehaviorRecord หักคะแนน
            BehaviorRecord.objects.create(
                student=record.student,
                behavior_type='deduct',
                points=no_checkout_penalty,
                reason=f'[AUTO] ไม่สแกนออก - วันที่ {target_date} (หัก {no_checkout_penalty} คะแนน)',
                recorded_by=None,
                date_recorded=target_date,
                is_auto=True,
                auto_type='no_checkout',
                related_attendance=record
            )
            
            record.points_deducted = record.points_deducted + no_checkout_penalty
            record.is_penalty_applied = True
            record.save(update_fields=['points_deducted', 'is_penalty_applied'])
            
            result['no_checkout_count'] += 1
            logger.info(f"🚪 หักคะแนนไม่สแกนออกสำหรับ {record.student.student_id}")
        
        return result
        
    except Exception as e:
        logger.error(f"เกิดข้อผิดพลาด: {str(e)}", exc_info=True)
        return {'success': False, 'error': str(e)}


def auto_calculate_attendance_status(target_date=None):
    """
    ⭐ คำนวณสถานะการเข้าเรียนตามเวลาที่สแกน
    
    กฎ:
    - 05:30 - 08:20 = มาปกติ (present)
    - 08:20 - 09:00 = มาสาย (late)
    - หลัง 09:00 = ขาด (absent)
    
    Args:
        target_date: วันที่ต้องการประมวลผล (default = วันนี้)
    
    Returns:
        dict: ผลการประมวลผล
    """
    from .models import AttendanceRecord, SchoolSettings
    
    try:
        if target_date is None:
            target_date = timezone.now().date()
        
        result = {
            'date': str(target_date),
            'success': True,
            'updated_count': 0,
            'weekend_skip': False
        }
        
        # ตรวจสอบว่าเป็นวันเรียนหรือไม่
        if not is_school_day(target_date):
            result['weekend_skip'] = True
            return result
        
        settings = SchoolSettings.objects.first()
        
        if not settings:
            # ใช้ค่า default
            normal_arrival_time = time(8, 20)
            late_arrival_time = time(9, 0)
        else:
            normal_arrival_time = settings.normal_arrival_time
            late_arrival_time = settings.late_arrival_time
        
        # หาบันทึกวันนี้ที่มี check_in_time
        records = AttendanceRecord.objects.filter(
            date=target_date,
            check_in_time__isnull=False
        )
        
        for record in records:
            old_status = record.status
            check_in_time = record.check_in_time
            
            # คำนวณสถานะใหม่ตามเวลา
            if check_in_time <= normal_arrival_time:
                new_status = 'present'
            elif check_in_time <= late_arrival_time:
                new_status = 'late'
            else:
                new_status = 'absent'
            
            # อัปเดตถ้าสถานะเปลี่ยน
            if old_status != new_status:
                record.status = new_status
                record.save()
                result['updated_count'] += 1
                logger.info(f"📝 อัปเดตสถานะ {record.student.student_id} จาก {old_status} เป็น {new_status}")
        
        return result
        
    except Exception as e:
        logger.error(f"เกิดข้อผิดพลาดในการคำนวณสถานะ: {str(e)}", exc_info=True)
        return {'success': False, 'error': str(e)}


def run_daily_attendance_processing(target_date=None):
    """
    ⭐ รันกระบวนการประมวลผลการเข้าเรียนรายวันทั้งหมด
    
    Args:
        target_date: วันที่ต้องการประมวลผล (default = วันนี้)
    
    Returns:
        dict: ผลการประมวลผล
    """
    try:
        if target_date is None:
            target_date = timezone.now().date()
        
        logger.info(f"🚀 เริ่มประมวลผลการเข้าเรียน วันที่ {target_date}")
        
        # ตรวจสอบวันเรียน
        if not is_school_day(target_date):
            return {
                'success': True,
                'date': str(target_date),
                'message': 'ข้ามวันหยุด (เสาร์-อาทิตย์)',
                'weekend_skip': True
            }
        
        result = {
            'success': True,
            'date': str(target_date),
            'absent_result': auto_mark_absent_for_missing_records(target_date),
            'no_checkout_result': auto_deduct_no_checkout(target_date),
            'status_update_result': auto_calculate_attendance_status(target_date)
        }
        
        logger.info(f"✅ ประมวลผลอัตโนมัติเสร็จสิ้น: {result}")
        return result
        
    except Exception as e:
        logger.error(f"เกิดข้อผิดพลาด: {str(e)}", exc_info=True)
        return {'success': False, 'error': str(e)}


def get_behavior_summary_for_student(student_id, start_date=None, end_date=None):
    """
    ⭐ ฟังก์ชันสำหรับดูสรุปคะแนนพฤติกรรมของนักเรียน
    
    Args:
        student_id: รหัสนักเรียน
        start_date: วันที่เริ่มต้น (optional)
        end_date: วันที่สิ้นสุด (optional)
    
    Returns:
        dict: สรุปคะแนนพฤติกรรม
    """
    from .models import Student, BehaviorRecord
    
    try:
        student = Student.objects.get(student_id=student_id)
        
        records = BehaviorRecord.objects.filter(student=student)
        
        if start_date:
            records = records.filter(date_recorded__gte=start_date)
        if end_date:
            records = records.filter(date_recorded__lte=end_date)
        
        # นับจำนวนแต่ละประเภท
        summary = {
            'student_id': student.student_id,
            'student_name': student.get_full_name(),
            'current_score': student.behavior_score,
            'total_deducted': 0,
            'total_added': 0,
            'deduction_details': {
                'late': {'count': 0, 'points': 0},
                'absent': {'count': 0, 'points': 0},
                'early_leave': {'count': 0, 'points': 0},
                'no_checkout': {'count': 0, 'points': 0},
                'manual': {'count': 0, 'points': 0},
            },
            'addition_details': {
                'count': 0,
                'points': 0
            }
        }
        
        for record in records:
            if record.behavior_type == 'deduct':
                summary['total_deducted'] += record.points
                
                if record.is_auto and record.auto_type:
                    key = record.auto_type
                    if key in summary['deduction_details']:
                        summary['deduction_details'][key]['count'] += 1
                        summary['deduction_details'][key]['points'] += record.points
                else:
                    summary['deduction_details']['manual']['count'] += 1
                    summary['deduction_details']['manual']['points'] += record.points
            else:
                summary['total_added'] += record.points
                summary['addition_details']['count'] += 1
                summary['addition_details']['points'] += record.points
        
        return summary
        
    except Student.DoesNotExist:
        return None
    except Exception as e:
        logger.error(f"เกิดข้อผิดพลาดในการสรุปคะแนน: {str(e)}", exc_info=True)
        return None


def reset_all_behavior_scores():
    """
    ⭐ รีเซ็ตคะแนนพฤติกรรมทุกคนกลับเป็นค่าเริ่มต้น (ใช้ตอนเริ่มภาคเรียนใหม่)
    
    Returns:
        int: จำนวนนักเรียนที่ถูกรีเซ็ต
    """
    from .models import Student, SchoolSettings
    
    try:
        settings = SchoolSettings.objects.first()
        default_score = settings.default_behavior_score if settings else 100
        
        count = Student.objects.filter(is_active=True).update(behavior_score=default_score)
        
        logger.info(f"✅ รีเซ็ตคะแนนพฤติกรรม {count} คน กลับเป็น {default_score} คะแนน")
        return count
        
    except Exception as e:
        logger.error(f"เกิดข้อผิดพลาดในการรีเซ็ตคะแนน: {str(e)}", exc_info=True)
        return 0


def get_daily_attendance_stats(target_date=None):
    """
    ⭐ สรุปสถิติการเข้าเรียนประจำวัน
    
    Args:
        target_date: วันที่ต้องการสรุป (default = วันนี้)
    
    Returns:
        dict: สถิติการเข้าเรียน
    """
    from .models import Student, AttendanceRecord, BehaviorRecord
    
    try:
        if target_date is None:
            target_date = timezone.now().date()
        
        total_students = Student.objects.filter(is_active=True).count()
        
        # นับจาก AttendanceRecord
        attendance = AttendanceRecord.objects.filter(date=target_date)
        
        present_count = attendance.filter(status='present').count()
        late_count = attendance.filter(status='late').count()
        absent_count = attendance.filter(status='absent').count()
        
        # นับจาก BehaviorRecord
        behavior_stats = {
            'late_deductions': BehaviorRecord.objects.filter(
                date_recorded=target_date,
                is_auto=True,
                auto_type='late'
            ).count(),
            'absent_deductions': BehaviorRecord.objects.filter(
                date_recorded=target_date,
                is_auto=True,
                auto_type='absent'
            ).count(),
            'no_checkout_deductions': BehaviorRecord.objects.filter(
                date_recorded=target_date,
                is_auto=True,
                auto_type='no_checkout'
            ).count()
        }
        
        return {
            'date': str(target_date),
            'is_school_day': is_school_day(target_date),
            'total_students': total_students,
            'present': present_count,
            'late': late_count,
            'absent': absent_count,
            'attendance_rate': round((present_count + late_count) / total_students * 100, 2) if total_students > 0 else 0,
            'behavior_deductions': behavior_stats
        }
        
    except Exception as e:
        logger.error(f"เกิดข้อผิดพลาดในการสรุปสถิติ: {str(e)}", exc_info=True)
        return None
