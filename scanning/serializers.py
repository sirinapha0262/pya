# scanning/serializers.py
# ⭐ แก้ไข: เพิ่ม attendance_status, attendance_status_display และ points_deducted
# ⭐ เพิ่ม StudentAttendanceHistorySerializer สำหรับ Frontend

from rest_framework import serializers
from .models import *
from my.models import *


class StudentBasicSerializer(serializers.ModelSerializer):
    """Serializer สำหรับข้อมูลนักเรียนพื้นฐาน"""
    full_name = serializers.SerializerMethodField()
    grade_display = serializers.SerializerMethodField()
    face_image = serializers.ImageField(read_only=True)
    
    class Meta:
        model = Student
        fields = [
            'id', 'student_id', 'first_name', 'last_name',
            'full_name', 'grade', 'classroom', 'grade_display',
            'face_image', 'behavior_score'
        ]
    
    def get_full_name(self, obj):
        return obj.get_full_name()
    
    def get_grade_display(self, obj):
        return f'ป.{obj.grade}/{obj.classroom}'


class BehaviorRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = BehaviorRecord
        fields = '__all__'


class RFIDScanLogSerializer(serializers.ModelSerializer):
    """
    ⭐ Serializer สำหรับบันทึกการสแกน RFID
    """
    student_detail = StudentBasicSerializer(source='student', read_only=True)
    
    student = serializers.PrimaryKeyRelatedField(
        queryset=Student.objects.all(),
        required=False,
        allow_null=True
    )
    scan_type_display = serializers.CharField(source='get_scan_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    
    # ⭐ Fields สำหรับสถานะการเข้าเรียน
    attendance_status_display = serializers.CharField(source='get_attendance_status_display', read_only=True)
    attendance_status_thai = serializers.SerializerMethodField()
    points_deducted_calculated = serializers.SerializerMethodField()
    
    # ⭐ ข้อมูลครูเวร (สำหรับ manual record)
    recorded_by_name = serializers.SerializerMethodField()
    
    # ⭐ face_verification_status
    face_verification_status = serializers.SerializerMethodField()
    
    # ⭐⭐⭐ เพิ่ม fields ที่ Frontend ต้องการ (ชื่อเดิม)
    # Frontend ใช้ status แทน attendance_status
    status_for_frontend = serializers.SerializerMethodField()
    date = serializers.SerializerMethodField()
    check_in_time = serializers.SerializerMethodField()
    check_out_time = serializers.SerializerMethodField()
    notes = serializers.SerializerMethodField()
    
    class Meta:
        model = RFIDScanLog
        fields = [
            'id', 'student', 'student_detail', 'rfid_card_id', 
            'scan_type', 'scan_type_display',
            'scan_time', 'status', 'status_display', 
            
            # ⭐ fields ใหม่
            'attendance_status', 'attendance_status_display', 'attendance_status_thai',
            'points_deducted', 'points_deducted_calculated',
            
            # ⭐ manual record fields
            'is_manual', 'recorded_by', 'recorded_by_name', 'manual_notes',
            
            # device info
            'device_name', 'device_location', 'device_info', 
            'error_message', 'processing_time', 'created_at',
            
            # ⭐ face verification
            'face_verification_status',
            
            # ⭐⭐⭐ Fields ที่ Frontend ต้องการ
            'status_for_frontend', 'date', 'check_in_time', 'check_out_time', 'notes'
        ]
        read_only_fields = ['id', 'created_at']
    
    def get_attendance_status_thai(self, obj):
        """แปลง attendance_status เป็นภาษาไทย"""
        status_mapping = {
            'present': 'มาปกติ',
            'late': 'มาสาย',
            'absent': 'ขาด',
            'early_leave': 'ออกก่อนเวลา',
            'checkout': 'ออก',
            'no_checkout': 'ไม่สแกนออก',
            'pending': 'รอตรวจสอบ',
        }
        return status_mapping.get(obj.attendance_status, obj.attendance_status)
    
    def get_points_deducted_calculated(self, obj):
        """คำนวณคะแนนที่หัก (ถ้ายังไม่มีค่า)"""
        if obj.points_deducted > 0:
            return obj.points_deducted
        
        try:
            from .behavior_scoring import BehaviorScoringConfig
            config = BehaviorScoringConfig()
            
            if obj.attendance_status == 'late':
                return config.LATE_DEDUCTION
            elif obj.attendance_status == 'absent':
                return config.ABSENT_DEDUCTION
            elif obj.attendance_status == 'early_leave':
                return config.EARLY_LEAVE_DEDUCTION
            elif obj.attendance_status == 'no_checkout':
                return config.NO_CHECKOUT_DEDUCTION
        except:
            pass
        
        return 0
    
    def get_recorded_by_name(self, obj):
        """ชื่อครูเวรที่บันทึก (สำหรับ manual record)"""
        if obj.recorded_by:
            return f"{obj.recorded_by.first_name} {obj.recorded_by.last_name}"
        return None
    
    def get_face_verification_status(self, obj):
        """สถานะการตรวจจับใบหน้า"""
        face_log = obj.face_recognition_logs.first()
        if face_log:
            return {
                'status': face_log.status,
                'status_display': face_log.get_status_display(),
                'confidence_score': face_log.confidence_score,
                'is_manual': face_log.is_manual
            }
        return None
    
    # ⭐⭐⭐ Methods สำหรับ Frontend compatibility
    def get_status_for_frontend(self, obj):
        """ส่ง attendance_status ในชื่อ status สำหรับ Frontend"""
        return obj.attendance_status
    
    def get_date(self, obj):
        """ดึงวันที่จาก scan_time"""
        if obj.scan_time:
            return obj.scan_time.date().isoformat()
        return None
    
    def get_check_in_time(self, obj):
        """ดึงเวลาเข้า"""
        if obj.scan_type == 'check_in' and obj.scan_time:
            return obj.scan_time.strftime('%H:%M:%S')
        return None
    
    def get_check_out_time(self, obj):
        """ดึงเวลาออก"""
        if obj.scan_type == 'check_out' and obj.scan_time:
            return obj.scan_time.strftime('%H:%M:%S')
        return None
    
    def get_notes(self, obj):
        """ดึงหมายเหตุ"""
        return obj.manual_notes or ''


# ⭐⭐⭐ NEW: Serializer สำหรับ Attendance History (รวม check_in และ check_out ในแถวเดียว)
class StudentAttendanceHistorySerializer(serializers.Serializer):
    """
    ⭐ Serializer สำหรับประวัติการเข้าเรียนของนักเรียน
    รวมข้อมูล check_in และ check_out ในแถวเดียว (แบบที่ Frontend ต้องการ)
    """
    date = serializers.DateField()
    status = serializers.CharField()  # present, late, absent
    status_thai = serializers.CharField()
    check_in_time = serializers.CharField(allow_null=True)
    check_out_time = serializers.CharField(allow_null=True)
    points_deducted = serializers.IntegerField()
    is_manual = serializers.BooleanField()
    recorded_by = serializers.CharField(allow_null=True)
    notes = serializers.CharField(allow_blank=True)
    
    # ⭐ เพิ่มข้อมูล Face Recognition
    face_verification = serializers.DictField(allow_null=True)


class FaceRecognitionLogSerializer(serializers.ModelSerializer):
    """Serializer สำหรับบันทึกการตรวจจับใบหน้า"""
    student_detail = StudentBasicSerializer(source='student', read_only=True)
    student = serializers.PrimaryKeyRelatedField(
        queryset=Student.objects.all(),
        required=False,
        allow_null=True
    )
    
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    captured_image_url = serializers.SerializerMethodField()
    manual_recorded_by_name = serializers.SerializerMethodField()
    recorded_date = serializers.SerializerMethodField()
    
    # ⭐ ข้อมูลจาก rfid_scan_log
    attendance_status = serializers.SerializerMethodField()
    attendance_status_thai = serializers.SerializerMethodField()
    points_deducted = serializers.SerializerMethodField()

    class Meta:
        model = FaceRecognitionLog
        fields = [
            'id', 'rfid_scan_log', 'student', 'student_detail', 
            'status', 'status_display',
            'confidence_score', 'captured_image', 'captured_image_url',
            'processing_time', 'face_detection_details', 'error_message',
            'created_at', 'is_manual', 'manual_recorded_by', 'manual_recorded_by_name',
            'manual_notes', 'recorded_date',
            
            # ⭐ ข้อมูลจาก rfid_scan_log
            'attendance_status', 'attendance_status_thai', 'points_deducted'
        ]
        read_only_fields = ['id', 'created_at']
    
    def get_captured_image_url(self, obj):
        if obj.captured_image:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.captured_image.url)
            return obj.captured_image.url
        return None
    
    def get_manual_recorded_by_name(self, obj):
        """ชื่อครูเวรประจำวันที่ลงบันทึก"""
        if obj.manual_recorded_by:
            return f"{obj.manual_recorded_by.first_name} {obj.manual_recorded_by.last_name}"
        return None
    
    def get_recorded_date(self, obj):
        """วันที่บันทึก - ใช้ scan_time จาก rfid_scan_log แทน created_at"""
        if obj.rfid_scan_log and obj.rfid_scan_log.scan_time:
            return obj.rfid_scan_log.scan_time.date().isoformat()
        elif obj.created_at:
            return obj.created_at.date().isoformat()
        return None
    
    def get_attendance_status(self, obj):
        """ดึง attendance_status จาก rfid_scan_log"""
        if obj.rfid_scan_log:
            return obj.rfid_scan_log.attendance_status
        return None
    
    def get_attendance_status_thai(self, obj):
        """แปลง attendance_status เป็นภาษาไทย"""
        status_mapping = {
            'present': 'มาปกติ',
            'late': 'มาสาย',
            'absent': 'ขาด',
            'early_leave': 'ออกก่อนเวลา',
            'checkout': 'ออก',
            'no_checkout': 'ไม่สแกนออก',
            'pending': 'รอตรวจสอบ',
        }
        if obj.rfid_scan_log:
            return status_mapping.get(obj.rfid_scan_log.attendance_status, '')
        return ''
    
    def get_points_deducted(self, obj):
        """ดึง points_deducted จาก rfid_scan_log"""
        if obj.rfid_scan_log:
            return obj.rfid_scan_log.points_deducted
        return 0

class ManualScanLogSerializer(serializers.Serializer):
    """Serializer สำหรับการเพิ่มบันทึกด้วยตนเอง (Manual Entry)"""
    student_id = serializers.CharField(required=True)
    scan_type = serializers.ChoiceField(choices=[('in', 'Check-in'), ('out', 'Check-out')])
    timestamp = serializers.DateTimeField(format="%Y-%m-%d %H:%M:%S", required=True)
    note = serializers.CharField(required=False, allow_blank=True)

class DeviceStatusSerializer(serializers.ModelSerializer):
    """Serializer สำหรับสถานะอุปกรณ์"""
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    uptime = serializers.SerializerMethodField()
    success_rate = serializers.SerializerMethodField()
    
    class Meta:
        model = DeviceStatus
        fields = [
            'id', 'device_name', 'device_type', 'location', 'status',
            'status_display', 'ip_address', 'firmware_version',
            'total_scans', 'successful_scans', 'failed_scans', 'success_rate',
            'last_heartbeat', 'last_online', 'uptime',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def get_uptime(self, obj):
        """คำนวณเวลาที่ออนไลน์ (ในชั่วโมง)"""
        if obj.last_online and obj.created_at:
            from django.utils import timezone
            if obj.status == 'online':
                delta = timezone.now() - obj.last_online
            else:
                if obj.last_heartbeat:
                    delta = obj.last_heartbeat - obj.created_at
                else:
                    return 0
            
            return round(delta.total_seconds() / 3600, 2)
        return 0
    
    def get_success_rate(self, obj):
        """คำนวณอัตราความสำเร็จ"""
        if obj.total_scans == 0:
            return 0
        return round((obj.successful_scans / obj.total_scans) * 100, 2)


class SystemAlertSerializer(serializers.ModelSerializer):
    """Serializer สำหรับการแจ้งเตือนระบบ"""
    alert_type_display = serializers.CharField(source='get_alert_type_display', read_only=True)
    severity_display = serializers.CharField(source='get_severity_display', read_only=True)
    device_name = serializers.CharField(source='device.device_name', read_only=True, allow_null=True)
    student_name = serializers.SerializerMethodField()
    resolved_by_name = serializers.SerializerMethodField()
    
    class Meta:
        model = SystemAlert
        fields = [
            'id', 'alert_type', 'alert_type_display', 'severity', 'severity_display',
            'title', 'message', 'device', 'device_name', 'student', 'student_name',
            'is_resolved', 'resolved_at', 'resolved_by', 'resolved_by_name',
            'resolution_notes', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']
    
    def get_student_name(self, obj):
        if obj.student:
            return obj.student.get_full_name()
        return None
    
    def get_resolved_by_name(self, obj):
        if obj.resolved_by:
            return f"{obj.resolved_by.first_name} {obj.resolved_by.last_name}"
        return None


class DailyReportSerializer(serializers.ModelSerializer):
    """Serializer สำหรับรายงานประจำวัน"""
    attendance_rate = serializers.SerializerMethodField()
    scan_success_rate = serializers.SerializerMethodField()
    face_recognition_rate = serializers.SerializerMethodField()
    
    class Meta:
        model = DailyReport
        fields = [
            'id', 'report_date', 
            'total_students', 'students_present', 'students_late', 
            'students_absent', 'students_early_leave', 'attendance_rate',
            'total_scans', 'successful_scans', 'failed_scans', 'scan_success_rate',
            'face_recognition_success', 'face_recognition_failed', 'face_recognition_rate',
            'devices_online', 'devices_offline', 'system_alerts_count',
            'notes', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def get_attendance_rate(self, obj):
        """คำนวณอัตราการเข้าเรียน"""
        if obj.total_students == 0:
            return 0
        return round((obj.students_present / obj.total_students) * 100, 2)
    
    def get_scan_success_rate(self, obj):
        """คำนวณอัตราความสำเร็จของการสแกน"""
        if obj.total_scans == 0:
            return 0
        return round((obj.successful_scans / obj.total_scans) * 100, 2)
    
    def get_face_recognition_rate(self, obj):
        """คำนวณอัตราความสำเร็จของการตรวจจับใบหน้า"""
        total_face = obj.face_recognition_success + obj.face_recognition_failed
        if total_face == 0:
            return 0
        return round((obj.face_recognition_success / total_face) * 100, 2)


class AttendanceSummarySerializer(serializers.Serializer):
    """Serializer สำหรับสรุปข้อมูลการเข้าเรียน"""
    date = serializers.DateField()
    day_name = serializers.CharField()
    is_school_day = serializers.BooleanField()
    total_students = serializers.IntegerField()
    present = serializers.IntegerField()
    late = serializers.IntegerField()
    absent = serializers.IntegerField()
    no_checkout = serializers.IntegerField(required=False, default=0)
    attendance_rate = serializers.FloatField()
    
    # ⭐ breakdown คะแนนหัก
    total_late_deduction = serializers.IntegerField(required=False, default=0)
    total_absent_deduction = serializers.IntegerField(required=False, default=0)



class StudentMiniSerializer(serializers.Serializer):
    """Serializer ย่อสำหรับข้อมูลนักเรียน"""
    id = serializers.IntegerField()
    student_id = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    full_name = serializers.SerializerMethodField()
    grade = serializers.CharField()
    classroom = serializers.CharField()
    grade_room = serializers.SerializerMethodField()
    behavior_score = serializers.IntegerField()
    
    def get_full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}"
    
    def get_grade_room(self, obj):
        return f"ป.{obj.grade}/{obj.classroom}"


class UserMiniSerializer(serializers.Serializer):
    """Serializer ย่อสำหรับข้อมูลผู้ใช้"""
    id = serializers.IntegerField()
    username = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    full_name = serializers.SerializerMethodField()
    role = serializers.CharField()
    
    def get_full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}"


class FaceRecognitionLogSerializer(serializers.Serializer):
    """Serializer สำหรับ Face Recognition Logs"""
    id = serializers.IntegerField()
    rfid_scan_log_id = serializers.IntegerField(source='rfid_scan_log.id', allow_null=True)
    student = StudentMiniSerializer(allow_null=True)
    
    status = serializers.CharField()
    status_display = serializers.SerializerMethodField()
    
    confidence_score = serializers.FloatField(allow_null=True)
    captured_image = serializers.ImageField(allow_null=True)
    processing_time = serializers.FloatField(allow_null=True)
    
    face_detection_details = serializers.JSONField()
    error_message = serializers.CharField()
    
    is_manual = serializers.BooleanField()
    manual_recorded_by = UserMiniSerializer(allow_null=True)
    manual_notes = serializers.CharField()
    
    is_verified = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()
    
    def get_status_display(self, obj):
        displays = {
            'success': '✅ ยืนยันสำเร็จ',
            'mismatch': '❌ ใบหน้าไม่ตรง',
            'no_face': '❓ ไม่พบใบหน้า',
            'error': '⚠️ เกิดข้อผิดพลาด',
            'manual': '👤 ครูยืนยัน'
        }
        return displays.get(obj.status, obj.status)
    
    def get_is_verified(self, obj):
        return obj.status in ['success', 'manual']


class RFIDScanLogSerializer(serializers.Serializer):
    """Serializer สำหรับ RFID Scan Logs"""
    id = serializers.IntegerField()
    student = StudentMiniSerializer(allow_null=True)
    rfid_card_id = serializers.CharField()
    
    scan_type = serializers.CharField()
    scan_type_display = serializers.SerializerMethodField()
    scan_time = serializers.DateTimeField()
    
    status = serializers.CharField()
    status_display = serializers.SerializerMethodField()
    
    attendance_status = serializers.CharField()
    attendance_status_display = serializers.SerializerMethodField()
    
    device_name = serializers.CharField()
    device_location = serializers.CharField()
    device_info = serializers.CharField()
    
    error_message = serializers.CharField()
    processing_time = serializers.FloatField(allow_null=True)
    
    is_manual = serializers.BooleanField()
    recorded_by = UserMiniSerializer(allow_null=True)
    
    points_deducted = serializers.IntegerField()
    
    # เชื่อมโยงกับ Face Recognition
    face_recognition = FaceRecognitionLogSerializer(allow_null=True, read_only=True)
    
    created_at = serializers.DateTimeField()
    
    def get_scan_type_display(self, obj):
        displays = {
            'check_in': '📥 สแกนเข้า',
            'check_out': '📤 สแกนออก'
        }
        return displays.get(obj.scan_type, obj.scan_type)
    
    def get_status_display(self, obj):
        displays = {
            'pending': '⏳ รอยืนยันใบหน้า',
            'success': '✅ สำเร็จ',
            'failed': '❌ ล้มเหลว'
        }
        return displays.get(obj.status, obj.status)
    
    def get_attendance_status_display(self, obj):
        displays = {
            'pending': '⏳ รอตรวจสอบ',
            'present': '✅ มาปกติ',
            'late': '⚠️ มาสาย',
            'absent': '❌ ขาด',
            'checkout': '✅ ออก',
            'early_leave': '⚠️ ออกก่อนเวลา'
        }
        return displays.get(obj.attendance_status, obj.attendance_status)


class AttendanceRecordSerializer(serializers.Serializer):
    """Serializer สำหรับ Attendance Records"""
    id = serializers.IntegerField()
    student = StudentMiniSerializer()
    date = serializers.DateField()
    
    check_in_time = serializers.TimeField(allow_null=True)
    check_out_time = serializers.TimeField(allow_null=True)
    
    status = serializers.CharField()
    status_display = serializers.SerializerMethodField()
    
    check_in_rfid_log_id = serializers.IntegerField(source='check_in_rfid_log.id', allow_null=True)
    check_out_rfid_log_id = serializers.IntegerField(source='check_out_rfid_log.id', allow_null=True)
    
    recorded_by = UserMiniSerializer(allow_null=True)
    notes = serializers.CharField(allow_null=True)
    is_manual_entry = serializers.BooleanField()
    
    points_deducted = serializers.IntegerField()
    is_penalty_applied = serializers.BooleanField()
    
    face_verified_in = serializers.BooleanField()
    face_verified_out = serializers.BooleanField()
    
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    
    def get_status_display(self, obj):
        displays = {
            'present': '✅ มา',
            'late': '⚠️ มาสาย',
            'absent': '❌ ขาด',
            'early_leave': '⚠️ ออกก่อนเวลา'
        }
        return displays.get(obj.status, obj.status)


class BehaviorRecordSerializer(serializers.Serializer):
    """Serializer สำหรับ Behavior Records"""
    id = serializers.IntegerField()
    student = StudentMiniSerializer()
    
    behavior_type = serializers.CharField()
    behavior_type_display = serializers.SerializerMethodField()
    
    points = serializers.IntegerField()
    reason = serializers.CharField()
    
    recorded_by = UserMiniSerializer(allow_null=True)
    date_recorded = serializers.DateField()
    
    is_auto = serializers.BooleanField()
    auto_type = serializers.CharField()
    auto_type_display = serializers.SerializerMethodField()
    
    related_attendance_id = serializers.IntegerField(source='related_attendance.id', allow_null=True)
    related_rfid_scan_id = serializers.IntegerField(source='related_rfid_scan.id', allow_null=True)
    
    created_at = serializers.DateTimeField()
    
    def get_behavior_type_display(self, obj):
        displays = {
            'deduct': '➖ หักคะแนน',
            'add': '➕ เพิ่มคะแนน'
        }
        return displays.get(obj.behavior_type, obj.behavior_type)
    
    def get_auto_type_display(self, obj):
        displays = {
            'late': 'มาสาย',
            'absent': 'ขาด',
            'early_leave': 'ออกก่อนเวลา',
            'no_checkout': 'ไม่สแกนออก',
            'face_mismatch': 'ใบหน้าไม่ตรง'
        }
        return displays.get(obj.auto_type, obj.auto_type)


# ==========================================================================
# ⭐ INPUT SERIALIZERS (สำหรับ validate request data)
# ==========================================================================

class ProcessScanInputSerializer(serializers.Serializer):
    """Serializer สำหรับ validate input ของ ProcessScanView"""
    rfid_card_id = serializers.CharField(required=True, max_length=50)
    captured_image = serializers.CharField(required=False, allow_blank=True)
    scan_type = serializers.ChoiceField(
        choices=['check_in', 'check_out'],
        default='check_in'
    )
    device_name = serializers.CharField(required=False, default='Unknown')
    device_location = serializers.CharField(required=False, default='')


class ManualVerifyInputSerializer(serializers.Serializer):
    """Serializer สำหรับ validate input ของ ManualVerifyView"""
    scan_log_id = serializers.IntegerField(required=True)
    notes = serializers.CharField(required=False, allow_blank=True, default='')


class DateRangeFilterSerializer(serializers.Serializer):
    """Serializer สำหรับ filter ตามช่วงวันที่"""
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    student_id = serializers.CharField(required=False)
    grade = serializers.CharField(required=False)
    classroom = serializers.CharField(required=False)


# ==========================================================================
# ⭐ RESPONSE SERIALIZERS (สำหรับ format response)
# ==========================================================================

class ScanResultSerializer(serializers.Serializer):
    """Serializer สำหรับผลลัพธ์การสแกน"""
    success = serializers.BooleanField()
    message = serializers.CharField(required=False)
    error = serializers.CharField(required=False)
    error_code = serializers.CharField(required=False)
    
    scan_type = serializers.CharField()
    timestamp = serializers.CharField()
    current_time = serializers.CharField()
    
    student = serializers.DictField(required=False)
    
    scan_log_id = serializers.IntegerField(required=False)
    attendance_record_id = serializers.IntegerField(required=False)
    
    attendance_status = serializers.CharField(required=False)
    attendance_status_display = serializers.CharField(required=False)
    
    face_verification = serializers.DictField(required=False)
    behavior_result = serializers.DictField(required=False)
    
    current_behavior_score = serializers.IntegerField(required=False)
    processing_time = serializers.FloatField(required=False)


class DailySummarySerializer(serializers.Serializer):
    """Serializer สำหรับสรุปรายวัน"""
    date = serializers.CharField()
    total_students = serializers.IntegerField()
    present = serializers.IntegerField()
    late = serializers.IntegerField()
    absent = serializers.IntegerField()
    early_leave = serializers.IntegerField()
    not_scanned = serializers.IntegerField()
    total_scans = serializers.IntegerField()
    successful_scans = serializers.IntegerField()
    failed_scans = serializers.IntegerField()
    face_recognition_success = serializers.IntegerField()
    face_recognition_failed = serializers.IntegerField()

class ManualAttendanceSerializer(serializers.Serializer):
    """Serializer สำหรับการเพิ่ม/แก้ไขข้อมูลการเข้าเรียนด้วยมือ"""
    student_id = serializers.CharField()  # รหัสนักเรียน
    timestamp = serializers.DateTimeField() # เวลาที่ต้องการบันทึก
    scan_type = serializers.ChoiceField(choices=['in', 'out']) # เข้าหรือออก
    is_manual = serializers.BooleanField(default=True)
    note = serializers.CharField(required=False, allow_blank=True) # เหตุผลการแก้ไข

class BehaviorAdjustmentSerializer(serializers.Serializer):
    """Serializer สำหรับปรับคะแนนพฤติกรรมโดยตรง"""
    student_id = serializers.CharField()
    behavior_type_id = serializers.IntegerField() # ID ของประเภทความประพฤติ
    points = serializers.IntegerField(required=False) # ระบุคะแนนเอง (ถ้าไม่ใช้ตามประเภท)
    note = serializers.CharField(required=False)