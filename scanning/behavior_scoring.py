# scanning/behavior_scoring.py
# ⭐ แก้ไข: เพิ่มการ Sync ไป BehaviorRecord อัตโนมัติ
# ===========================================
# เมื่อมีการหักคะแนนจาก RFIDScanLog จะสร้าง BehaviorRecord ด้วยอัตโนมัติ
# เพื่อให้ข้อมูลในหน้าบ้าน (MyBehavior) ตรงกับหลังบ้าน
# ===========================================

from django.utils import timezone
from django.db import transaction
from datetime import datetime, date, time
import logging

from .models import RFIDScanLog
from my.models import Student, BehaviorRecord, AttendanceRecord

logger = logging.getLogger(__name__)


class BehaviorScoringConfig:
    """การตั้งค่าการหักคะแนน"""

    AUTO_ENABLED = True           # เปิดใช้งานการหักคะแนนอัตโนมัติ    

    SCHOOL_DAYS = [0, 1, 2, 3, 4]  # วันจันทร์-ศุกร์

    LATE_DEDUCTION = 1          # มาสาย
    ABSENT_DEDUCTION = 2        # ขาด
    EARLY_LEAVE_DEDUCTION = 1   # ออกก่อนเวลา
    NO_CHECKOUT_DEDUCTION = 1   # ไม่สแกนออก
    
    # เวลาตามกฎ
    CHECK_IN_START = time(5, 30)       # <-- ตัวที่ Error เมื่อกี้
    CHECK_IN_ON_TIME = time(8, 20)
    CHECK_IN_LATE_END = time(9, 0)
    CHECK_OUT_START = time(15, 25)
    CHECK_OUT_END = time(17, 0)        # <-- เผื่อไว้สำหรับเวลาสิ้นสุด
    
    def is_school_day(self, check_date):
        """
        ตรวจสอบว่าเป็นวันเรียนหรือไม่
        
        Args:
            check_date: date object
        
        Returns:
            bool: True ถ้าเป็นวันเรียน (จ-ศ), False ถ้าเป็นวันหยุด (ส-อา)
        """
        return check_date.weekday() in self.SCHOOL_DAYS


class BehaviorScoringService:
    """Service สำหรับคำนวณและบันทึกคะแนนพฤติกรรม"""
    
    def __init__(self):
        self.config = BehaviorScoringConfig()
    
    # ===========================================
    # ⭐ NEW: สร้าง BehaviorRecord จาก RFIDScanLog
    # ===========================================
    
    def _create_behavior_record_from_scan(self, scan_log):
        """
        สร้าง BehaviorRecord จาก RFIDScanLog อัตโนมัติ
        
        Args:
            scan_log: RFIDScanLog instance
        
        Returns:
            BehaviorRecord instance or None
        """
        if not scan_log.student:
            logger.warning(f"ScanLog {scan_log.id} has no student - skip BehaviorRecord creation")
            return None
        
        if scan_log.points_deducted <= 0:
            return None
        
        # Map attendance_status → reason (ภาษาไทย)
        reason_map = {
            'late': 'มาสาย',
            'absent': 'ขาดเรียน',
            'early_leave': 'ออกก่อนเวลา',
            'no_checkout': 'ไม่สแกนออก'
        }
        
        reason = reason_map.get(scan_log.attendance_status, 'หักคะแนนจากการเข้าเรียน')
        
        # เพิ่มรายละเอียดเวลา
        if scan_log.scan_time:
            time_str = scan_log.scan_time.strftime('%H:%M')
            if scan_log.attendance_status == 'late':
                reason = f"มาสาย (สแกนเวลา {time_str})"
            elif scan_log.attendance_status == 'no_checkout':
                reason = f"ไม่สแกนออก (วันที่ {scan_log.scan_time.strftime('%d/%m/%Y')})"
        
        try:
            # ตรวจสอบว่ามี BehaviorRecord นี้อยู่แล้วหรือไม่ (ป้องกัน duplicate)
            existing = BehaviorRecord.objects.filter(
                student=scan_log.student,
                date_recorded=scan_log.scan_time.date(),
                behavior_type='deduct',
                points=scan_log.points_deducted,
                reason=reason
            ).first()
            
            if existing:
                logger.info(f"BehaviorRecord already exists for ScanLog {scan_log.id}")
                return existing
            
            # สร้าง BehaviorRecord ใหม่
            behavior_record = BehaviorRecord.objects.create(
                student=scan_log.student,
                behavior_type='deduct',  # หรือ 'negative' ขึ้นอยู่กับ model
                points=scan_log.points_deducted,
                reason=reason,
                date_recorded=scan_log.scan_time.date(),
                recorded_by=scan_log.recorded_by,  # ครูเวร (ถ้ามี)
                # ⭐ ถ้า BehaviorRecord model มี field สำหรับเชื่อมโยง:
                # related_scan_log=scan_log
            )
            
            logger.info(
                f"✅ Created BehaviorRecord from ScanLog: "
                f"Student {scan_log.student.student_id}, "
                f"Points -{scan_log.points_deducted}, "
                f"Reason: {reason}"
            )
            
            return behavior_record
            
        except Exception as e:
            logger.error(f"❌ Error creating BehaviorRecord from ScanLog {scan_log.id}: {e}")
            return None
    
    # ===========================================
    # ⭐ แก้ไข: เพิ่มการสร้าง BehaviorRecord ในแต่ละฟังก์ชัน
    # ===========================================
    
    @transaction.atomic
    def _deduct_points_for_late(self, student, target_date, scan_log=None):
        """
        หักคะแนนสำหรับมาสาย
        
        Args:
            student: Student instance
            target_date: date object
            scan_log: RFIDScanLog instance (optional)
        
        Returns:
            dict: ผลการหักคะแนน
        """
        try:
            # 1. ตรวจสอบว่ามี BehaviorRecord อยู่แล้วหรือไม่
            existing = BehaviorRecord.objects.filter(
                student=student,
                date_recorded=target_date,
                behavior_type='deduct',
                reason__icontains='มาสาย'
            ).first()
            
            if existing:
                logger.info(f"Already deducted for late: {student.student_id} on {target_date}")
                return {
                    'success': False,
                    'message': 'Already deducted',
                    'points_deducted': existing.points
                }
            
            # 2. หักคะแนน
            old_score = student.behavior_score
            student.behavior_score -= self.config.LATE_DEDUCTION
            student.save()
            
            # 3. บันทึก AttendanceRecord (ถ้ามี)
            AttendanceRecord.objects.update_or_create(
                student=student,
                date=target_date,
                defaults={
                    'status': 'late',
                    'check_in_time': scan_log.scan_time.time() if scan_log else None,
                    'points_deducted': self.config.LATE_DEDUCTION
                }
            )
            
            # 4. ⭐ สร้าง BehaviorRecord
            reason = "มาสาย"
            if scan_log and scan_log.scan_time:
                reason = f"มาสาย (สแกนเวลา {scan_log.scan_time.strftime('%H:%M')})"
            
            behavior_record = BehaviorRecord.objects.create(
                student=student,
                behavior_type='deduct',
                points=self.config.LATE_DEDUCTION,
                reason=reason,
                date_recorded=target_date,
                recorded_by=scan_log.recorded_by if scan_log else None
            )
            
            # 5. ⭐ Update scan_log.points_deducted (ถ้ามี)
            if scan_log:
                scan_log.points_deducted = self.config.LATE_DEDUCTION
                scan_log.save()
            
            logger.info(
                f"✅ Deducted {self.config.LATE_DEDUCTION} points for LATE: "
                f"{student.student_id} on {target_date} "
                f"({old_score} → {student.behavior_score})"
            )
            
            return {
                'success': True,
                'student_id': student.student_id,
                'date': str(target_date),
                'points_deducted': self.config.LATE_DEDUCTION,
                'old_score': old_score,
                'new_score': student.behavior_score,
                'behavior_record_id': behavior_record.id
            }
            
        except Exception as e:
            logger.error(f"❌ Error deducting points for late: {e}")
            return {'success': False, 'error': str(e)}
    
    @transaction.atomic
    def _deduct_points_for_absent(self, student, target_date):
        """
        หักคะแนนสำหรับขาดเรียน
        
        Args:
            student: Student instance
            target_date: date object
        
        Returns:
            dict: ผลการหักคะแนน
        """
        try:
            # ⭐ 1. สร้าง AttendanceRecord ก่อนเสมอ (ไม่ว่าจะหักคะแนนแล้วหรือยัง)
            attendance, att_created = AttendanceRecord.objects.update_or_create(
                student=student,
                date=target_date,
                defaults={
                    'status': 'absent',
                    'points_deducted': self.config.ABSENT_DEDUCTION,
                    'is_penalty_applied': True
                }
            )
            
            if att_created:
                logger.info(f"✅ Created AttendanceRecord (absent) for {student.student_id} on {target_date}")
            else:
                logger.info(f"📝 Updated AttendanceRecord (absent) for {student.student_id} on {target_date}")
            
            # 2. ตรวจสอบว่ามี BehaviorRecord อยู่แล้วหรือไม่ (ป้องกันหักคะแนนซ้ำ)
            existing = BehaviorRecord.objects.filter(
                student=student,
                date_recorded=target_date,
                behavior_type='deduct',
                reason__icontains='ขาดเรียน'
            ).first()
            
            if existing:
                logger.info(f"Already deducted for absent: {student.student_id} on {target_date}")
                return {
                    'success': True,  # ⭐ เปลี่ยนเป็น True เพราะสร้าง AttendanceRecord แล้ว
                    'message': 'Already deducted, AttendanceRecord created/updated',
                    'points_deducted': existing.points,
                    'attendance_record_id': attendance.id,
                    'already_processed': True
                }
            
            # 3. หักคะแนน
            old_score = student.behavior_score
            student.behavior_score -= self.config.ABSENT_DEDUCTION
            student.save()
            
            # 4. ⭐ สร้าง BehaviorRecord
            behavior_record = BehaviorRecord.objects.create(
                student=student,
                behavior_type='deduct',
                points=self.config.ABSENT_DEDUCTION,
                reason='ขาดเรียน',
                date_recorded=target_date,
                recorded_by=None  # ระบบอัตโนมัติ
            )
            
            # 5. ⭐ สร้าง/Update RFIDScanLog (ถ้าต้องการ)
            # สำหรับ tracking ว่ามีการประมวลผลแล้ว
            scan_log, created = RFIDScanLog.objects.get_or_create(
                student=student,
                scan_time__date=target_date,
                scan_type='check_in',
                defaults={
                    'rfid_card_id': student.rfid_card_id or '',
                    'scan_time': timezone.make_aware(
                        datetime.combine(target_date, time(9, 0))
                    ),
                    'status': 'failed',
                    'attendance_status': 'absent',
                    'points_deducted': self.config.ABSENT_DEDUCTION,
                    'is_manual': True,
                    'manual_notes': 'ประมวลผลอัตโนมัติ - ไม่พบการสแกน'
                }
            )
            
            if not created:
                scan_log.attendance_status = 'absent'
                scan_log.points_deducted = self.config.ABSENT_DEDUCTION
                scan_log.save()
            
            logger.info(
                f"✅ Deducted {self.config.ABSENT_DEDUCTION} points for ABSENT: "
                f"{student.student_id} on {target_date} "
                f"({old_score} → {student.behavior_score})"
            )
            
            return {
                'success': True,
                'student_id': student.student_id,
                'date': str(target_date),
                'points_deducted': self.config.ABSENT_DEDUCTION,
                'old_score': old_score,
                'new_score': student.behavior_score,
                'behavior_record_id': behavior_record.id
            }
            
        except Exception as e:
            logger.error(f"❌ Error deducting points for absent: {e}")
            return {'success': False, 'error': str(e)}
    
    @transaction.atomic
    def _deduct_points_for_no_checkout(self, student, target_date, check_in_log=None):
        """
        หักคะแนนสำหรับไม่สแกนออก
        
        Args:
            student: Student instance
            target_date: date object
            check_in_log: RFIDScanLog check_in (optional)
        
        Returns:
            dict: ผลการหักคะแนน
        """
        try:
            # 1. ตรวจสอบว่ามี BehaviorRecord อยู่แล้วหรือไม่
            existing = BehaviorRecord.objects.filter(
                student=student,
                date_recorded=target_date,
                behavior_type='deduct',
                reason__icontains='ไม่สแกนออก'
            ).first()
            
            if existing:
                logger.info(f"Already deducted for no checkout: {student.student_id} on {target_date}")
                return {
                    'success': False,
                    'message': 'Already deducted',
                    'points_deducted': existing.points
                }
            
            # 2. หักคะแนน
            old_score = student.behavior_score
            student.behavior_score -= self.config.NO_CHECKOUT_DEDUCTION
            student.save()
            
            # 3. บันทึก AttendanceRecord
            attendance = AttendanceRecord.objects.filter(
                student=student,
                date=target_date
            ).first()
            
            if attendance:
                attendance.check_out_time = None
                attendance.points_deducted += self.config.NO_CHECKOUT_DEDUCTION
                attendance.save()
            
            # 4. ⭐ สร้าง BehaviorRecord
            reason = f"ไม่สแกนออก (วันที่ {target_date.strftime('%d/%m/%Y')})"
            
            behavior_record = BehaviorRecord.objects.create(
                student=student,
                behavior_type='deduct',
                points=self.config.NO_CHECKOUT_DEDUCTION,
                reason=reason,
                date_recorded=target_date,
                recorded_by=None
            )
            
            # 5. ⭐ สร้าง RFIDScanLog no_checkout
            scan_log = RFIDScanLog.objects.create(
                student=student,
                rfid_card_id=student.rfid_card_id or '',
                scan_type='check_out',
                scan_time=timezone.make_aware(
                    datetime.combine(target_date, time(17, 0))
                ),
                status='failed',
                attendance_status='no_checkout',
                points_deducted=self.config.NO_CHECKOUT_DEDUCTION,
                is_manual=True,
                manual_notes='ประมวลผลอัตโนมัติ - ไม่พบการสแกนออก'
            )
            
            logger.info(
                f"✅ Deducted {self.config.NO_CHECKOUT_DEDUCTION} points for NO CHECKOUT: "
                f"{student.student_id} on {target_date} "
                f"({old_score} → {student.behavior_score})"
            )
            
            return {
                'success': True,
                'student_id': student.student_id,
                'date': str(target_date),
                'points_deducted': self.config.NO_CHECKOUT_DEDUCTION,
                'old_score': old_score,
                'new_score': student.behavior_score,
                'behavior_record_id': behavior_record.id
            }
            
        except Exception as e:
            logger.error(f"❌ Error deducting points for no checkout: {e}")
            return {'success': False, 'error': str(e)}
    
    # ===========================================
    # ประมวลผลรายวัน
    # ===========================================
    
    def process_daily_absent(self, target_date=None):
        """
        ประมวลผลการขาดเรียนรายวัน (ควรรันหลัง 09:00)
        
        Args:
            target_date: date object (default: วันนี้)
        
        Returns:
            dict: สรุปผลการประมวลผล
        """
        if target_date is None:
            target_date = timezone.now().date()
        
        # ตรวจสอบว่าเป็นวันเรียนหรือไม่
        if target_date.weekday() >= 5:  # เสาร์-อาทิตย์
            return {
                'success': False,
                'message': 'Not a school day'
            }
        
        # หานักเรียนทั้งหมดที่ active
        all_students = Student.objects.filter(is_active=True)
        
        # หานักเรียนที่ไม่มีการสแกน check_in วันนี้
        absent_students = []
        for student in all_students:
            has_check_in = RFIDScanLog.objects.filter(
                student=student,
                scan_type='check_in',
                scan_time__date=target_date,
                status='success'
            ).exists()
            
            if not has_check_in:
                absent_students.append(student)
        
        # หักคะแนนทีละคน
        results = []
        for student in absent_students:
            result = self._deduct_points_for_absent(student, target_date)
            if result.get('success'):
                results.append(result)
        
        logger.info(f"📊 Processed {len(results)}/{len(absent_students)} absent students on {target_date}")
        
        return {
            'success': True,
            'date': str(target_date),
            'total_students': all_students.count(),
            'absent_count': len(absent_students),
            'deducted_count': len(results),
            'total_points_deducted': len(results) * self.config.ABSENT_DEDUCTION,
            'results': results
        }
    
    def process_daily_no_checkout(self, target_date=None):
        """
        ประมวลผลนักเรียนที่ไม่สแกนออก (ควรรันหลัง 17:00)
        
        Args:
            target_date: date object (default: วันนี้)
        
        Returns:
            dict: สรุปผลการประมวลผล
        """
        if target_date is None:
            target_date = timezone.now().date()
        
        # หานักเรียนที่สแกนเข้า แต่ไม่สแกนออก
        students_with_check_in = RFIDScanLog.objects.filter(
            scan_type='check_in',
            scan_time__date=target_date,
            status='success'
        ).values_list('student_id', flat=True).distinct()
        
        no_checkout_students = []
        for student_id in students_with_check_in:
            has_check_out = RFIDScanLog.objects.filter(
                student_id=student_id,
                scan_type='check_out',
                scan_time__date=target_date,
                status='success'
            ).exists()
            
            if not has_check_out:
                student = Student.objects.get(id=student_id)
                no_checkout_students.append(student)
        
        # หักคะแนนทีละคน
        results = []
        for student in no_checkout_students:
            result = self._deduct_points_for_no_checkout(student, target_date)
            if result.get('success'):
                results.append(result)
        
        logger.info(f"📊 Processed {len(results)} no-checkout students on {target_date}")
        
        return {
            'success': True,
            'date': str(target_date),
            'no_checkout_count': len(no_checkout_students),
            'deducted_count': len(results),
            'total_points_deducted': len(results) * self.config.NO_CHECKOUT_DEDUCTION,
            'results': results
        }
    
    def process_daily_behavior_scores(self, target_date=None):
        """
        ประมวลผลคะแนนพฤติกรรมทั้งหมดในวันนั้น
        รวม: ขาดเรียน + ไม่สแกนออก
        
        Args:
            target_date: date object (default: วันนี้)
        
        Returns:
            dict: สรุปผลการประมวลผล
        """
        if target_date is None:
            target_date = timezone.now().date()
        
        logger.info(f"🚀 Processing daily behavior scores for {target_date}")
        
        # 1. ประมวลผลขาดเรียน
        absent_result = self.process_daily_absent(target_date)
        
        # 2. ประมวลผลไม่สแกนออก
        no_checkout_result = self.process_daily_no_checkout(target_date)
        
        return {
            'success': True,
            'date': str(target_date),
            'absent': absent_result,
            'no_checkout': no_checkout_result,
            'total_deducted': (
                absent_result.get('deducted_count', 0) + 
                no_checkout_result.get('deducted_count', 0)
            ),
            'total_points': (
                absent_result.get('total_points_deducted', 0) + 
                no_checkout_result.get('total_points_deducted', 0)
            )
        }


# สร้าง singleton instance
behavior_scoring_service = BehaviorScoringService()