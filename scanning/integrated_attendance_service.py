# scanning/integrated_attendance_service.py
# ⭐ Service หลักสำหรับเชื่อม RFID + Face Recognition → Attendance + Behavior
# ==========================================================================
# 📌 กฎเวลา:
# เวลาเข้า: 05:30 - 08:20 = มาปกติ (present)
#           08:21 - 09:00 = มาสาย (late) - หัก 1 คะแนน
#           ไม่สแกน หรือ ใบหน้าไม่ตรง = ขาด (absent) - หัก 2 คะแนน
# เวลาออก: 15:25 - 17:00 = ออกปกติ (checkout)
#           ก่อน 15:25 = ออกก่อนเวลา - หัก 1 คะแนน
#           ไม่สแกน = ไม่สแกนออก - หัก 1 คะแนน
#
# 📌 เงื่อนไขสำคัญ:
# - ต้องสแกนบัตร RFID + ยืนยันใบหน้าพร้อมกัน
# - ใบหน้าต้องตรงกับเจ้าของบัตร ถึงจะบันทึกสำเร็จ
# ==========================================================================

from django.utils import timezone
from django.db import transaction
from datetime import datetime, date, time, timedelta
import logging

logger = logging.getLogger(__name__)


class AttendanceTimeConfig:
    """การตั้งค่าเวลาสแกนเริ่มต้น"""
    
    # เวลาเข้า
    CHECK_IN_START = time(5, 30)       # เปิดรับสแกนเข้า
    CHECK_IN_ON_TIME = time(8, 20)     # ก่อนเวลานี้ = มาปกติ
    CHECK_IN_LATE_END = time(9, 0)     # หลังเวลานี้ = ขาด
    
    # เวลาออก
    CHECK_OUT_START = time(15, 25)     # เปิดรับสแกนออก
    CHECK_OUT_END = time(18, 0)        # ปิดรับสแกนออก
    
    # คะแนนที่หัก
    LATE_DEDUCTION = 1
    ABSENT_DEDUCTION = 2
    EARLY_LEAVE_DEDUCTION = 1
    NO_CHECKOUT_DEDUCTION = 1
    FACE_MISMATCH_DEDUCTION = 2
    
    # Face Recognition
    CONFIDENCE_THRESHOLD = 40.0    # <--- ลดจาก 70.0 เหลือ 40.0


class IntegratedAttendanceService:
    """
    ⭐ Service หลักสำหรับประมวลผลการเข้าเรียน
    
    Flow การทำงาน:
    ================
    1. นักเรียนสแกนบัตร RFID → สร้าง RFIDScanLog (status='pending')
    2. กล้องถ่ายรูป → เปรียบเทียบใบหน้ากับรูปในฐานข้อมูล
    3. ถ้าใบหน้าตรงกัน (confidence >= threshold):
       - อัพเดท RFIDScanLog (status='success')
       - สร้าง FaceRecognitionLog (status='success')
       - คำนวณสถานะ (present/late) ตามเวลา
       - บันทึก AttendanceRecord
       - หักคะแนนอัตโนมัติ (ถ้าสาย)
    4. ถ้าใบหน้าไม่ตรง:
       - อัพเดท RFIDScanLog (status='failed')
       - สร้าง FaceRecognitionLog (status='mismatch')
       - ถือว่าขาด + หักคะแนน
    """
    
    def __init__(self):
        self.config = AttendanceTimeConfig()
    
    def get_school_settings(self):
        """ดึงการตั้งค่าจาก SchoolSettings (ถ้ามี)"""
        try:
            from my.models import SchoolSettings
            settings = SchoolSettings.objects.first()
            if settings:
                return {
                    'check_in_start': settings.school_start_time,
                    'check_in_on_time': settings.normal_arrival_time,
                    'check_in_late_end': settings.late_arrival_time,
                    'check_out_start': settings.school_end_time,
                    'check_out_end': settings.latest_departure_time,
                    'late_penalty': settings.late_penalty_points,
                    'absent_penalty': settings.absent_penalty_points,
                    'early_leave_penalty': settings.early_leave_penalty_points,
                    'no_checkout_penalty': settings.no_checkout_penalty_points,
                    'face_mismatch_penalty': getattr(settings, 'face_mismatch_penalty_points', 2),
                    'face_confidence_threshold': getattr(settings, 'face_confidence_threshold', 70.0),
                    'auto_enabled': settings.auto_deduct_enabled,
                    'require_face': getattr(settings, 'require_face_verification', True),
                }
        except Exception as e:
            logger.warning(f"Could not load SchoolSettings: {e}")
        
        # Default config
        return {
            'check_in_start': self.config.CHECK_IN_START,
            'check_in_on_time': self.config.CHECK_IN_ON_TIME,
            'check_in_late_end': self.config.CHECK_IN_LATE_END,
            'check_out_start': self.config.CHECK_OUT_START,
            'check_out_end': self.config.CHECK_OUT_END,
            'late_penalty': self.config.LATE_DEDUCTION,
            'absent_penalty': self.config.ABSENT_DEDUCTION,
            'early_leave_penalty': self.config.EARLY_LEAVE_DEDUCTION,
            'no_checkout_penalty': self.config.NO_CHECKOUT_DEDUCTION,
            'face_mismatch_penalty': self.config.FACE_MISMATCH_DEDUCTION,
            'face_confidence_threshold': self.config.CONFIDENCE_THRESHOLD,
            'auto_enabled': True,
            'require_face': True,
        }
    
    # ==========================================================================
    # ⭐ MAIN ENTRY POINT: process_rfid_with_face
    # ==========================================================================
    
    @transaction.atomic
    def process_rfid_with_face(
        self,
        rfid_card_id: str,
        captured_image_base64: str = None,
        scan_type: str = 'check_in',
        device_name: str = 'Unknown',
        device_location: str = '',
        recorded_by=None
    ):
        """
        ⭐ ประมวลผลการสแกน RFID พร้อมยืนยันใบหน้า
        
        Args:
            rfid_card_id: รหัสบัตร RFID
            captured_image_base64: รูปภาพที่ถ่ายจากกล้อง (base64)
            scan_type: 'check_in' หรือ 'check_out'
            device_name: ชื่ออุปกรณ์
            device_location: ตำแหน่งอุปกรณ์
            recorded_by: ครูเวร (ถ้าเป็น manual)
        
        Returns:
            dict: ผลการประมวลผล
        """
        from my.models import Student, AttendanceRecord, BehaviorRecord, RFIDScanLog, FaceRecognitionLog
        
        import time as time_module
        start_time = time_module.time()
        
        settings = self.get_school_settings()
        now = timezone.now()
        current_time = now.time()
        today = now.date()
        
        result = {
            'success': False,
            'scan_type': scan_type,
            'timestamp': now.isoformat(),
            'current_time': current_time.strftime('%H:%M:%S'),
        }
        
        # ==========================================================================
        # STEP 1: ตรวจสอบช่วงเวลาสแกน
        # ==========================================================================
        time_check = self._validate_scan_time(scan_type, current_time, settings)
        if not time_check['allowed']:
            result['error'] = time_check['message']
            result['error_code'] = 'INVALID_TIME'
            return result
        
        # ==========================================================================
        # STEP 2: หานักเรียนจากบัตร RFID
        # ==========================================================================
        try:
            student = Student.objects.get(rfid_card_id=rfid_card_id, is_active=True)
        except Student.DoesNotExist:
            # สร้าง failed scan log
            RFIDScanLog.objects.create(
                student=None,
                rfid_card_id=rfid_card_id,
                scan_type=scan_type,
                scan_time=now,
                status='failed',
                attendance_status='pending',
                device_name=device_name,
                device_location=device_location,
                error_message='ไม่พบนักเรียนในระบบ'
            )
            result['error'] = 'ไม่พบนักเรียนในระบบ - กรุณาติดต่อครู'
            result['error_code'] = 'STUDENT_NOT_FOUND'
            result['rfid_card_id'] = rfid_card_id
            return result
        
        result['student'] = self._student_to_dict(student)
        
        # ==========================================================================
        # STEP 3: ตรวจสอบการสแกนซ้ำ
        # ==========================================================================
        duplicate_check = self._check_duplicate_scan(student, scan_type, today)
        if duplicate_check['is_duplicate']:
            result['error'] = duplicate_check['message']
            result['error_code'] = 'DUPLICATE_SCAN'
            result['previous_scan_time'] = duplicate_check.get('previous_time')
            return result
        
        # ==========================================================================
        # STEP 4: สร้าง RFIDScanLog (pending - รอยืนยันใบหน้า)
        # ==========================================================================
        scan_log = RFIDScanLog.objects.create(
            student=student,
            rfid_card_id=rfid_card_id,
            scan_type=scan_type,
            scan_time=now,
            status='pending',
            attendance_status='pending',
            device_name=device_name,
            device_location=device_location,
            is_manual=(recorded_by is not None),
            recorded_by=recorded_by
        )
        result['scan_log_id'] = scan_log.id
        
        # ==========================================================================
        # STEP 5: ตรวจสอบใบหน้า (บังคับต้องมีรูป)
        # ==========================================================================
        if settings['require_face'] and not captured_image_base64:
            # ไม่มีรูปภาพ - บันทึกเป็น failed และถือว่าขาด
            scan_log.status = 'failed'
            scan_log.attendance_status = 'absent' if scan_type == 'check_in' else 'pending'
            scan_log.error_message = 'ไม่มีข้อมูลใบหน้าสำหรับยืนยันตัวตน'
            scan_log.save()
            
            # สร้าง FaceRecognitionLog แบบ no_face
            FaceRecognitionLog.objects.create(
                rfid_scan_log=scan_log,
                student=student,
                status='no_face',
                error_message='ไม่มีข้อมูลใบหน้าสำหรับยืนยันตัวตน'
            )
            
            # ถ้าเป็น check_in → ขาด + หักคะแนน
            if scan_type == 'check_in':
                self._mark_as_absent(student, today, scan_log, settings, 'ไม่ยืนยันใบหน้า')
            
            result['error'] = 'กรุณาถ่ายรูปเพื่อยืนยันตัวตน'
            result['error_code'] = 'FACE_REQUIRED'
            result['requires_face_verification'] = True
            return result
        
        # ==========================================================================
        # STEP 6: ยืนยันใบหน้า
        # ==========================================================================
        face_result = self._verify_face(
            scan_log=scan_log,
            student=student,
            captured_image_base64=captured_image_base64,
            settings=settings
        )
        result['face_verification'] = face_result
        
        # ถ้าใบหน้าไม่ตรง
        if not face_result['is_match']:
            scan_log.status = 'failed'
            scan_log.attendance_status = 'absent' if scan_type == 'check_in' else 'pending'
            scan_log.error_message = face_result['message']
            scan_log.save()
            
            # ถ้าเป็น check_in → ขาด + หักคะแนน (ใบหน้าไม่ตรง)
            if scan_type == 'check_in':
                self._mark_as_absent(
                    student, today, scan_log, settings, 
                    f"ใบหน้าไม่ตรง (ความมั่นใจ: {face_result['confidence']:.1f}%)"
                )
            
            result['error'] = face_result['message']
            result['error_code'] = 'FACE_MISMATCH'
            return result
        
        # ==========================================================================
        # STEP 7: ใบหน้าตรง ✅ - คำนวณสถานะการเข้าเรียน
        # ==========================================================================
        attendance_status = self._calculate_attendance_status(
            scan_type=scan_type,
            scan_time=current_time,
            settings=settings
        )
        
        # อัพเดท scan_log
        scan_log.status = 'success'
        scan_log.attendance_status = attendance_status
        scan_log.processing_time = time_module.time() - start_time
        scan_log.save()
        
        # ==========================================================================
        # STEP 8: บันทึก AttendanceRecord
        # ==========================================================================
        attendance_record = self._create_or_update_attendance(
            student=student,
            date=today,
            scan_type=scan_type,
            scan_time=current_time,
            attendance_status=attendance_status,
            scan_log=scan_log,
            settings=settings
        )
        result['attendance_record_id'] = attendance_record.id
        result['attendance_status'] = attendance_status
        result['attendance_status_display'] = self._get_status_display(attendance_status)
        
        # ==========================================================================
        # STEP 9: หักคะแนนพฤติกรรม (ถ้าจำเป็น)
        # ==========================================================================
        behavior_result = None
        if settings['auto_enabled']:
            behavior_result = self._process_behavior_deduction(
                student=student,
                attendance_status=attendance_status,
                attendance_record=attendance_record,
                scan_log=scan_log,
                settings=settings
            )
            result['behavior_result'] = behavior_result
        
        # ==========================================================================
        # RETURN SUCCESS ✅
        # ==========================================================================
        processing_time = time_module.time() - start_time
        
        # Refresh student เพื่อดึงคะแนนล่าสุด
        student.refresh_from_db()
        
        result['success'] = True
        result['message'] = self._get_success_message(scan_type, attendance_status)
        result['student'] = self._student_to_dict(student)
        result['current_behavior_score'] = student.behavior_score
        result['processing_time'] = round(processing_time, 2)
        
        logger.info(
            f"✅ SCAN SUCCESS: {student.student_id} | {scan_type} | "
            f"status={attendance_status} | score={student.behavior_score}"
        )
        
        return result
    
    # ==========================================================================
    # ⭐ MANUAL VERIFICATION BY TEACHER
    # ==========================================================================
    
    @transaction.atomic
    def manual_verify(
        self,
        scan_log_id: int,
        verified_by,
        notes: str = ''
    ):
        """
        ครูยืนยันตัวตนแทนระบบ Face Recognition
        ใช้เมื่อระบบ Face Recognition มีปัญหา หรือนักเรียนไม่มีรูปในระบบ
        """
        from my.models import Student, AttendanceRecord, BehaviorRecord, RFIDScanLog, FaceRecognitionLog
        
        settings = self.get_school_settings()
        now = timezone.now()
        today = now.date()
        
        try:
            scan_log = RFIDScanLog.objects.get(id=scan_log_id)
        except RFIDScanLog.DoesNotExist:
            return {
                'success': False,
                'error': 'ไม่พบบันทึกการสแกน',
                'error_code': 'SCAN_LOG_NOT_FOUND'
            }
        
        if scan_log.status == 'success':
            return {
                'success': False,
                'error': 'การสแกนนี้ยืนยันแล้ว',
                'error_code': 'ALREADY_VERIFIED'
            }
        
        student = scan_log.student
        if not student:
            return {
                'success': False,
                'error': 'ไม่พบข้อมูลนักเรียน',
                'error_code': 'NO_STUDENT'
            }
        
        # สร้างหรืออัพเดท FaceRecognitionLog
        face_log, created = FaceRecognitionLog.objects.get_or_create(
            rfid_scan_log=scan_log,
            defaults={
                'student': student,
                'status': 'manual',
                'confidence_score': 100.0,
                'is_manual': True,
                'manual_recorded_by': verified_by,
                'manual_notes': notes or f'ยืนยันโดย {verified_by.get_full_name()}'
            }
        )
        
        if not created:
            face_log.status = 'manual'
            face_log.confidence_score = 100.0
            face_log.is_manual = True
            face_log.manual_recorded_by = verified_by
            face_log.manual_notes = notes or f'ยืนยันโดย {verified_by.get_full_name()}'
            face_log.save()
        
        # คำนวณสถานะจากเวลาสแกน
        scan_time = scan_log.scan_time.time()
        attendance_status = self._calculate_attendance_status(
            scan_type=scan_log.scan_type,
            scan_time=scan_time,
            settings=settings
        )
        
        # อัพเดท scan_log
        scan_log.status = 'success'
        scan_log.attendance_status = attendance_status
        scan_log.recorded_by = verified_by
        scan_log.save()
        
        # บันทึก AttendanceRecord
        attendance_record = self._create_or_update_attendance(
            student=student,
            date=scan_log.scan_time.date(),
            scan_type=scan_log.scan_type,
            scan_time=scan_time,
            attendance_status=attendance_status,
            scan_log=scan_log,
            settings=settings
        )
        
        # หักคะแนน (ถ้าจำเป็น)
        behavior_result = None
        if settings['auto_enabled']:
            behavior_result = self._process_behavior_deduction(
                student=student,
                attendance_status=attendance_status,
                attendance_record=attendance_record,
                scan_log=scan_log,
                settings=settings
            )
        
        # Refresh
        student.refresh_from_db()
        
        logger.info(
            f"✅ MANUAL VERIFY: {student.student_id} by {verified_by.username} | "
            f"status={attendance_status}"
        )
        
        return {
            'success': True,
            'message': f'ยืนยันตัวตนสำเร็จ - {self._get_status_display(attendance_status)}',
            'student': self._student_to_dict(student),
            'attendance_status': attendance_status,
            'attendance_status_display': self._get_status_display(attendance_status),
            'attendance_record_id': attendance_record.id,
            'behavior_result': behavior_result,
            'current_behavior_score': student.behavior_score
        }
    
    # ==========================================================================
    # HELPER METHODS
    # ==========================================================================
    
    def _validate_scan_time(self, scan_type: str, current_time, settings: dict) -> dict:
        """ตรวจสอบว่าอยู่ในช่วงเวลาที่อนุญาตหรือไม่"""
        from datetime import time as time_type
        
        # ⭐ DEBUG: แสดงข้อมูลการเปรียบเทียบเวลา
        logger.info(f"🕐 [TIME CHECK] scan_type={scan_type}")
        logger.info(f"🕐 [TIME CHECK] current_time={current_time} (type={type(current_time)})")
        
        # ⭐ แปลง current_time ให้เป็น time object (ถ้ายังไม่ใช่)
        if not isinstance(current_time, time_type):
            if hasattr(current_time, 'time'):
                current_time = current_time.time()
            else:
                logger.error(f"❌ current_time is not a time object: {type(current_time)}")
        
        if scan_type == 'check_in':
            start = settings['check_in_start']
            end = settings['check_in_late_end']
            
            logger.info(f"🕐 [CHECK_IN] start={start}, end={end}, current={current_time}")
            logger.info(f"🕐 [CHECK_IN] {current_time} < {start} ? {current_time < start}")
            logger.info(f"🕐 [CHECK_IN] {current_time} > {end} ? {current_time > end}")
            
            if current_time < start:
                return {
                    'allowed': False,
                    'message': f'⏰ ยังไม่ถึงเวลาสแกนเข้า (เปิดรับ {start.strftime("%H:%M")})'
                }
            if current_time > end:
                return {
                    'allowed': False,
                    'message': f'⏰ เลยเวลาสแกนเข้าแล้ว (ปิดรับ {end.strftime("%H:%M")})'
                }
        
        elif scan_type == 'check_out':
            start = settings['check_out_start']
            end = settings['check_out_end']
            
            logger.info(f"🕐 [CHECK_OUT] start={start}, end={end}, current={current_time}")
            
            if current_time < start:
                return {
                    'allowed': False,
                    'message': f'⏰ ยังไม่ถึงเวลาสแกนออก (เปิดรับ {start.strftime("%H:%M")})'
                }
            if current_time > end:
                return {
                    'allowed': False,
                    'message': f'⏰ เลยเวลาสแกนออกแล้ว (ปิดรับ {end.strftime("%H:%M")})'
                }
        
        return {'allowed': True}
    
    def _check_duplicate_scan(self, student, scan_type: str, date) -> dict:
        """ตรวจสอบว่าสแกนซ้ำหรือไม่"""
        from my.models import RFIDScanLog
        
        existing = RFIDScanLog.objects.filter(
            student=student,
            scan_type=scan_type,
            scan_time__date=date,
            status='success'
        ).first()
        
        if existing:
            return {
                'is_duplicate': True,
                'message': f'⚠️ คุณสแกน{("เข้า" if scan_type == "check_in" else "ออก")}แล้ววันนี้ เวลา {existing.scan_time.strftime("%H:%M")}',
                'previous_time': existing.scan_time.strftime('%H:%M:%S')
            }
        
        return {'is_duplicate': False}
    
    def _verify_face(self, scan_log, student, captured_image_base64, settings):
        """ตรวจสอบใบหน้าและบันทึก FaceRecognitionLog"""
        from my.models import FaceRecognitionLog
        
        import time as time_module
        start = time_module.time()
        
        # ตรวจสอบว่านักเรียนมีรูปใบหน้าในระบบหรือไม่
        if not student.face_image:
            FaceRecognitionLog.objects.create(
                rfid_scan_log=scan_log,
                student=student,
                status='error',
                error_message='ไม่พบรูปใบหน้านักเรียนในระบบ'
            )
            return {
                'is_match': False,
                'confidence': 0,
                'message': '❌ ไม่พบรูปใบหน้านักเรียนในระบบ - กรุณาติดต่อครู'
            }
        
        # เรียกใช้ Face Recognition Service
        try:
            from .face_recognition_service import face_recognition_service
            
            # รับค่าจาก Service
            raw_match, raw_conf, message = face_recognition_service.verify_face(
                captured_image_base64=captured_image_base64,
                stored_image_path=student.face_image.path
            )

            # -----------------------------------------------------------
            # ✅ แก้ไข: แปลง NumPy type เป็น Python type เพื่อป้องกัน JSON Error
            # -----------------------------------------------------------
            is_match = bool(raw_match)      # แปลง np.bool_ -> bool
            confidence = float(raw_conf)    # แปลง np.float -> float
            
        except Exception as e:
            logger.error(f"Face recognition error: {e}")
            # ถ้า Face Recognition มีปัญหา → ให้ครูยืนยันแทน
            FaceRecognitionLog.objects.create(
                rfid_scan_log=scan_log,
                student=student,
                status='error',
                error_message=str(e)
            )
            return {
                'is_match': False,
                'confidence': 0,
                'message': f'❌ ระบบตรวจจับใบหน้ามีปัญหา - กรุณาให้ครูยืนยัน'
            }
        
        processing_time = time_module.time() - start
        
        # บันทึกรูปที่ถ่าย
        captured_image_path = None
        try:
            from .face_recognition_service import face_recognition_service as frs
            filename = f"{student.student_id}_{timezone.now().strftime('%Y%m%d_%H%M%S')}"
            captured_image_path = frs.save_captured_image(captured_image_base64, filename)
        except Exception as e:
            logger.warning(f"Could not save captured image: {e}")
        
        # ตรวจสอบ threshold
        # confidence มาเป็น 0.0-1.0 หรือ 0-100 ต้องเช็คให้ดี (สมมติว่าเป็น 0.0-1.0)
        conf_percent = confidence * 100
        threshold = settings.get('face_confidence_threshold', 50.0)
        
        is_verified = is_match and (conf_percent >= threshold)
        
        # บันทึก FaceRecognitionLog
        face_log = FaceRecognitionLog.objects.create(
            rfid_scan_log=scan_log,
            student=student,
            status='success' if is_verified else 'mismatch',
            confidence_score=conf_percent,
            captured_image=captured_image_path,
            processing_time=processing_time,
            face_detection_details={
                'raw_confidence': confidence,   # ✅ ปลอดภัยแล้ว เป็น float
                'threshold': threshold,         # ✅ ปลอดภัยแล้ว เป็น float
                'is_match': is_match,           # ✅ ปลอดภัยแล้ว เป็น bool
                'is_verified': bool(is_verified), # ✅ ปลอดภัยแล้ว เป็น bool
                'message': str(message)
            }
        )
        
        if is_verified:
            return {
                'is_match': True,
                'confidence': round(conf_percent, 1),
                'message': f'✅ ยืนยันตัวตนสำเร็จ (ความมั่นใจ: {conf_percent:.1f}%)',
                'face_log_id': face_log.id,
                'processing_time': round(processing_time, 2)
            }
        else:
            return {
                'is_match': False,
                'confidence': round(conf_percent, 1),
                'message': f'❌ ใบหน้าไม่ตรงกับเจ้าของบัตร (ความมั่นใจ: {conf_percent:.1f}%)',
                'face_log_id': face_log.id,
                'processing_time': round(processing_time, 2)
            }
    
    def _calculate_attendance_status(self, scan_type: str, scan_time: time, settings: dict) -> str:
        """คำนวณสถานะการเข้าเรียนจากเวลาสแกน"""
        
        if scan_type == 'check_in':
            # 05:30 - 08:20 = present (มาปกติ)
            if scan_time <= settings['check_in_on_time']:
                return 'present'
            # 08:21 - 09:00 = late (มาสาย)
            elif scan_time <= settings['check_in_late_end']:
                return 'late'
            # หลัง 09:00 = absent (ขาด)
            else:
                return 'absent'
        
        elif scan_type == 'check_out':
            # ก่อน 15:25 = early_leave (ออกก่อนเวลา)
            if scan_time < settings['check_out_start']:
                return 'early_leave'
            # 15:25 - 17:00 = checkout (ออกปกติ)
            else:
                return 'checkout'
        
        return 'pending'
    
    def _create_or_update_attendance(
        self, student, date, scan_type, scan_time, attendance_status, scan_log, settings
    ):
        """สร้างหรืออัพเดท AttendanceRecord"""
        from my.models import AttendanceRecord
        
        attendance, created = AttendanceRecord.objects.get_or_create(
            student=student,
            date=date,
            defaults={
                'status': 'pending',
                'is_manual_entry': scan_log.is_manual
            }
        )
        
        if scan_type == 'check_in':
            attendance.check_in_time = scan_time
            attendance.check_in_rfid_log = scan_log
            attendance.face_verified_in = True
            
            # กำหนดสถานะ
            if attendance.status in ['pending', ''] or created:
                attendance.status = attendance_status
        
        elif scan_type == 'check_out':
            attendance.check_out_time = scan_time
            attendance.check_out_rfid_log = scan_log
            attendance.face_verified_out = True
            
            # ถ้าออกก่อนเวลา
            if attendance_status == 'early_leave':
                attendance.status = 'early_leave'
        
        attendance.save()
        return attendance
    
    def _mark_as_absent(self, student, date, scan_log, settings, reason):
        """บันทึกว่าขาดเรียนและหักคะแนน"""
        from my.models import AttendanceRecord, BehaviorRecord
        
        # สร้าง AttendanceRecord
        attendance, created = AttendanceRecord.objects.get_or_create(
            student=student,
            date=date,
            defaults={
                'status': 'absent',
                'check_in_rfid_log': scan_log,
                'is_manual_entry': scan_log.is_manual
            }
        )
        
        if not created and attendance.status != 'absent':
            attendance.status = 'absent'
            attendance.check_in_rfid_log = scan_log
            attendance.save()
        
        # หักคะแนน
        if settings['auto_enabled']:
            existing = BehaviorRecord.objects.filter(
                student=student,
                date_recorded=date,
                is_auto=True,
                auto_type='absent'
            ).exists()
            
            if not existing:
                BehaviorRecord.objects.create(
                    student=student,
                    behavior_type='deduct',
                    points=settings['absent_penalty'],
                    reason=f'ขาดเรียน: {reason}',
                    date_recorded=date,
                    is_auto=True,
                    auto_type='absent',
                    related_attendance=attendance,
                    related_rfid_scan=scan_log
                )
                
                # อัพเดท attendance
                attendance.points_deducted = settings['absent_penalty']
                attendance.is_penalty_applied = True
                attendance.save()
                
                # อัพเดท scan_log
                scan_log.points_deducted = settings['absent_penalty']
                scan_log.save()
    
    def _process_behavior_deduction(
        self, student, attendance_status, attendance_record, scan_log, settings
    ):
        """หักคะแนนพฤติกรรมตามสถานะ"""
        from my.models import BehaviorRecord
        
        # กำหนดคะแนนที่ต้องหัก
        points_map = {
            'late': settings['late_penalty'],
            'absent': settings['absent_penalty'],
            'early_leave': settings['early_leave_penalty'],
        }
        
        points = points_map.get(attendance_status, 0)
        
        if points <= 0:
            return None
        
        # ตรวจสอบว่าหักไปแล้วหรือยัง
        existing = BehaviorRecord.objects.filter(
            student=student,
            date_recorded=attendance_record.date,
            is_auto=True,
            auto_type=attendance_status
        ).first()
        
        if existing:
            logger.info(f"Already deducted for {attendance_status}: {student.student_id}")
            return {
                'already_deducted': True,
                'points': existing.points
            }
        
        # สร้าง reason
        reason_map = {
            'late': f'มาสาย (สแกนเวลา {scan_log.scan_time.strftime("%H:%M")})',
            'absent': 'ขาดเรียน',
            'early_leave': f'ออกก่อนเวลา (สแกนเวลา {scan_log.scan_time.strftime("%H:%M")})'
        }
        reason = reason_map.get(attendance_status, 'หักคะแนนอัตโนมัติ')
        
        # สร้าง BehaviorRecord (จะหักคะแนนใน model.save())
        behavior_record = BehaviorRecord.objects.create(
            student=student,
            behavior_type='deduct',
            points=points,
            reason=reason,
            date_recorded=attendance_record.date,
            recorded_by=scan_log.recorded_by,
            is_auto=True,
            auto_type=attendance_status,
            related_attendance=attendance_record,
            related_rfid_scan=scan_log
        )
        
        # อัพเดท scan_log และ attendance_record
        scan_log.points_deducted = points
        scan_log.save(update_fields=['points_deducted'])
        
        attendance_record.points_deducted = points
        attendance_record.is_penalty_applied = True
        attendance_record.save(update_fields=['points_deducted', 'is_penalty_applied'])
        
        logger.info(
            f"✅ Deducted {points} points from {student.student_id} "
            f"for {attendance_status}."
        )
        
        return {
            'deducted': True,
            'points': points,
            'reason': reason,
            'behavior_record_id': behavior_record.id
        }
    
    def _student_to_dict(self, student) -> dict:
        """แปลง Student object เป็น dict"""
        return {
            'id': student.id,
            'student_id': student.student_id,
            'first_name': student.first_name,
            'last_name': student.last_name,
            'full_name': student.get_full_name(),
            'grade': student.grade,
            'classroom': student.classroom,
            'grade_room': student.get_grade_room(),
            'behavior_score': student.behavior_score,
            'has_face_image': bool(student.face_image)
        }
    
    def _get_status_display(self, status: str) -> str:
        """แปลงสถานะเป็นภาษาไทย"""
        displays = {
            'present': '✅ มาปกติ',
            'late': '⚠️ มาสาย',
            'absent': '❌ ขาด',
            'early_leave': '⚠️ ออกก่อนเวลา',
            'checkout': '✅ ออก',
            'no_checkout': '⚠️ ไม่สแกนออก',
            'pending': '⏳ รอตรวจสอบ'
        }
        return displays.get(status, status)
    
    def _get_success_message(self, scan_type: str, attendance_status: str) -> str:
        """สร้างข้อความสำเร็จ"""
        if scan_type == 'check_in':
            if attendance_status == 'present':
                return '✅ สแกนเข้าสำเร็จ - มาตรงเวลา!'
            elif attendance_status == 'late':
                return '⚠️ สแกนเข้าสำเร็จ - มาสาย (หัก 1 คะแนน)'
            else:
                return '❌ สแกนเข้าสำเร็จ - ขาดเรียน (หัก 2 คะแนน)'
        else:
            if attendance_status == 'checkout':
                return '✅ สแกนออกสำเร็จ'
            else:
                return '⚠️ สแกนออกสำเร็จ - ออกก่อนเวลา (หัก 1 คะแนน)'
    
    # ==========================================================================
    # ⭐ DAILY PROCESSING METHODS
    # ==========================================================================
    
    def process_daily_absent(self, target_date=None):
        """
        ประมวลผลนักเรียนที่ขาดเรียน (ไม่มีการสแกนเข้าทั้งวัน)
        ควรรันหลัง 09:00
        """
        from my.models import Student, AttendanceRecord, BehaviorRecord, RFIDScanLog
        
        if target_date is None:
            target_date = timezone.now().date()
        
        # ตรวจสอบว่าเป็นวันเรียน (จันทร์-ศุกร์)
        if target_date.weekday() >= 5:
            return {
                'success': False,
                'message': 'ไม่ใช่วันเรียน (เสาร์-อาทิตย์)'
            }
        
        settings = self.get_school_settings()
        
        # หานักเรียนที่ active ทั้งหมด
        all_students = Student.objects.filter(is_active=True)
        
        # หานักเรียนที่มี check_in สำเร็จ
        students_with_checkin = RFIDScanLog.objects.filter(
            scan_type='check_in',
            scan_time__date=target_date,
            status='success'
        ).values_list('student_id', flat=True).distinct()
        
        absent_students = all_students.exclude(id__in=students_with_checkin)
        
        results = []
        for student in absent_students:
            # ตรวจสอบว่าหักคะแนนไปแล้วหรือยัง
            existing = BehaviorRecord.objects.filter(
                student=student,
                date_recorded=target_date,
                is_auto=True,
                auto_type='absent'
            ).exists()
            
            if existing:
                continue
            
            # สร้าง AttendanceRecord
            attendance, _ = AttendanceRecord.objects.get_or_create(
                student=student,
                date=target_date,
                defaults={
                    'status': 'absent',
                    'points_deducted': settings['absent_penalty'],
                    'is_penalty_applied': True
                }
            )
            
            if attendance.status != 'absent':
                attendance.status = 'absent'
                attendance.points_deducted = settings['absent_penalty']
                attendance.is_penalty_applied = True
                attendance.save()
            
            # สร้าง BehaviorRecord
            BehaviorRecord.objects.create(
                student=student,
                behavior_type='deduct',
                points=settings['absent_penalty'],
                reason='ขาดเรียน (ไม่มีการสแกนเข้า)',
                date_recorded=target_date,
                recorded_by=None,
                is_auto=True,
                auto_type='absent',
                related_attendance=attendance
            )
            
            # Refresh student
            student.refresh_from_db()
            
            results.append({
                'student_id': student.student_id,
                'student_name': student.get_full_name(),
                'points_deducted': settings['absent_penalty'],
                'new_score': student.behavior_score
            })
        
        logger.info(f"📊 Processed {len(results)} absent students for {target_date}")
        
        return {
            'success': True,
            'date': str(target_date),
            'total_students': all_students.count(),
            'absent_count': len(results),
            'results': results
        }
    
    def process_daily_no_checkout(self, target_date=None):
        """
        ประมวลผลนักเรียนที่ไม่สแกนออก
        ควรรันหลัง 17:00
        """
        from my.models import Student, AttendanceRecord, BehaviorRecord, RFIDScanLog
        
        if target_date is None:
            target_date = timezone.now().date()
        
        settings = self.get_school_settings()
        
        # หานักเรียนที่สแกนเข้าแต่ไม่สแกนออก
        students_with_checkin = RFIDScanLog.objects.filter(
            scan_type='check_in',
            scan_time__date=target_date,
            status='success'
        ).values_list('student_id', flat=True).distinct()
        
        students_with_checkout = RFIDScanLog.objects.filter(
            scan_type='check_out',
            scan_time__date=target_date,
            status='success'
        ).values_list('student_id', flat=True).distinct()
        
        no_checkout_ids = set(students_with_checkin) - set(students_with_checkout)
        
        results = []
        for student_id in no_checkout_ids:
            student = Student.objects.get(id=student_id)
            
            # ตรวจสอบว่าหักคะแนนไปแล้วหรือยัง
            existing = BehaviorRecord.objects.filter(
                student=student,
                date_recorded=target_date,
                is_auto=True,
                auto_type='no_checkout'
            ).exists()
            
            if existing:
                continue
            
            # หา AttendanceRecord
            attendance = AttendanceRecord.objects.filter(
                student=student,
                date=target_date
            ).first()
            
            if attendance:
                attendance.points_deducted += settings['no_checkout_penalty']
                attendance.save()
            
            # สร้าง BehaviorRecord
            BehaviorRecord.objects.create(
                student=student,
                behavior_type='deduct',
                points=settings['no_checkout_penalty'],
                reason=f'ไม่สแกนออก (วันที่ {target_date.strftime("%d/%m/%Y")})',
                date_recorded=target_date,
                recorded_by=None,
                is_auto=True,
                auto_type='no_checkout',
                related_attendance=attendance
            )
            
            # Refresh student
            student.refresh_from_db()
            
            results.append({
                'student_id': student.student_id,
                'student_name': student.get_full_name(),
                'points_deducted': settings['no_checkout_penalty'],
                'new_score': student.behavior_score
            })
        
        logger.info(f"📊 Processed {len(results)} no-checkout students for {target_date}")
        
        return {
            'success': True,
            'date': str(target_date),
            'no_checkout_count': len(results),
            'results': results
        }
    
    # ==========================================================================
    # ⭐ REPORTING METHODS
    # ==========================================================================
    
    def get_daily_summary(self, target_date=None):
        """ดึงสรุปรายวัน"""
        from my.models import Student, AttendanceRecord, RFIDScanLog, FaceRecognitionLog
        
        if target_date is None:
            target_date = timezone.now().date()
        
        total_students = Student.objects.filter(is_active=True).count()
        
        attendance_summary = AttendanceRecord.objects.filter(date=target_date).values('status').annotate(
            count=models.Count('id')
        )
        
        status_counts = {item['status']: item['count'] for item in attendance_summary}
        
        # นับการสแกน
        total_scans = RFIDScanLog.objects.filter(scan_time__date=target_date).count()
        successful_scans = RFIDScanLog.objects.filter(scan_time__date=target_date, status='success').count()
        failed_scans = RFIDScanLog.objects.filter(scan_time__date=target_date, status='failed').count()
        
        # นับ Face Recognition
        face_success = FaceRecognitionLog.objects.filter(
            created_at__date=target_date,
            status__in=['success', 'manual']
        ).count()
        face_failed = FaceRecognitionLog.objects.filter(
            created_at__date=target_date,
            status__in=['mismatch', 'no_face', 'error']
        ).count()
        
        return {
            'date': str(target_date),
            'total_students': total_students,
            'present': status_counts.get('present', 0),
            'late': status_counts.get('late', 0),
            'absent': status_counts.get('absent', 0),
            'early_leave': status_counts.get('early_leave', 0),
            'not_scanned': total_students - sum(status_counts.values()),
            'total_scans': total_scans,
            'successful_scans': successful_scans,
            'failed_scans': failed_scans,
            'face_recognition_success': face_success,
            'face_recognition_failed': face_failed
        }


# Singleton instance
integrated_attendance_service = IntegratedAttendanceService()