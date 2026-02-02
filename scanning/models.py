# scanning/models.py
# ⭐ แก้ไข: เพิ่ม attendance_status และ points_deducted ใน RFIDScanLog
# ⭐ แก้ไข related_name เพื่อไม่ให้ clash กับ my/models.py
from django.db import models
from my.models import User, Student
from django.utils import timezone


class RFIDScanLog(models.Model):
    """บันทึกการสแกนบัตร RFID"""
    SCAN_TYPE_CHOICES = [
        ('check_in', 'เข้า'),
        ('check_out', 'ออก'),
        ('manual', 'บันทึกด้วยตนเอง'),
    ]
    
    STATUS_CHOICES = [
        ('success', 'สำเร็จ'),
        ('failed', 'ล้มเหลว'),
        ('pending', 'รอตรวจสอบ'),
    ]
    
    # ⭐ เพิ่ม: สถานะการเข้าเรียน (มาปกติ, มาสาย, ขาด)
    ATTENDANCE_STATUS_CHOICES = [
        ('present', 'มาปกติ'),
        ('late', 'มาสาย'),
        ('absent', 'ขาด'),
        ('early_leave', 'ออกก่อนเวลา'),
        ('checkout', 'ออก'),
        ('no_checkout', 'ไม่สแกนออก'),
        ('pending', 'รอตรวจสอบ'),
    ]
    
    # ⭐ แก้ไข: เปลี่ยน related_name เป็น 'scanning_rfid_scan_logs'
    student = models.ForeignKey(
        Student, 
        on_delete=models.CASCADE, 
        related_name='scanning_rfid_scan_logs',  # ⭐ เปลี่ยนจาก 'rfid_scan_logs'
        null=True, 
        blank=True
    )
    rfid_card_id = models.CharField(max_length=50, verbose_name='รหัสบัตร RFID')
    scan_type = models.CharField(max_length=20, choices=SCAN_TYPE_CHOICES, verbose_name='ประเภทการสแกน')
    scan_time = models.DateTimeField(default=timezone.now, verbose_name='เวลาสแกน')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', verbose_name='สถานะการสแกน')
    
    # ⭐ เพิ่ม fields ใหม่
    attendance_status = models.CharField(
        max_length=20, 
        choices=ATTENDANCE_STATUS_CHOICES, 
        default='pending',
        verbose_name='สถานะการเข้าเรียน'
    )
    points_deducted = models.IntegerField(default=0, verbose_name='คะแนนที่หัก')
    
    device_name = models.CharField(max_length=100, blank=True, verbose_name='ชื่ออุปกรณ์')
    device_location = models.CharField(max_length=200, blank=True, verbose_name='ตำแหน่งอุปกรณ์')
    device_info = models.TextField(blank=True, verbose_name='ข้อมูลอุปกรณ์')
    error_message = models.TextField(blank=True, verbose_name='ข้อความแสดงข้อผิดพลาด')
    processing_time = models.FloatField(null=True, blank=True, verbose_name='เวลาประมวลผล (วินาที)')
    
    # ⭐ เพิ่ม: ข้อมูลครูเวร (สำหรับ manual record)
    is_manual = models.BooleanField(default=False, verbose_name='บันทึกด้วยตนเอง')
    recorded_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='scanning_manual_scan_logs',  # ⭐ เปลี่ยนจาก 'manual_scan_logs'
        verbose_name='บันทึกโดย'
    )
    manual_notes = models.TextField(blank=True, verbose_name='หมายเหตุ')
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'rfid_scan_logs'
        verbose_name = 'บันทึกการสแกน RFID'
        ordering = ['-scan_time']
        indexes = [
            models.Index(fields=['rfid_card_id', 'scan_time']),
            models.Index(fields=['student', 'scan_time']),
            models.Index(fields=['scan_type', 'status']),
            models.Index(fields=['attendance_status', 'scan_time']),  # ⭐ เพิ่ม index ใหม่
        ]
    
    def __str__(self):
        student_name = self.student.get_full_name() if self.student else 'ไม่ทราบ'
        return f"{self.rfid_card_id} - {student_name} - {self.get_attendance_status_display()}"
    
    # ⭐ เพิ่ม method สำหรับคำนวณสถานะ
    def calculate_attendance_status(self):
        """คำนวณสถานะการเข้าเรียนจากเวลาสแกน"""
        from datetime import time
        
        if self.scan_type == 'check_out':
            check_out_start = time(15, 25)
            if self.scan_time.time() < check_out_start:
                return 'early_leave'
            return 'checkout'
        
        # สำหรับ check_in
        check_in_on_time = time(8, 20)
        check_in_late_end = time(9, 0)
        
        scan_hour = self.scan_time.time()
        
        if scan_hour <= check_in_on_time:
            return 'present'
        elif scan_hour <= check_in_late_end:
            return 'late'
        else:
            return 'absent'
    
    def get_points_deducted(self):
        """คำนวณคะแนนที่หัก"""
        from .behavior_scoring import BehaviorScoringConfig
        config = BehaviorScoringConfig()
        
        if self.attendance_status == 'late':
            return config.LATE_DEDUCTION
        elif self.attendance_status == 'absent':
            return config.ABSENT_DEDUCTION
        elif self.attendance_status == 'early_leave':
            return config.EARLY_LEAVE_DEDUCTION
        elif self.attendance_status == 'no_checkout':
            return config.NO_CHECKOUT_DEDUCTION
        return 0


class FaceRecognitionLog(models.Model):
    """บันทึกการตรวจจับใบหน้า"""
    STATUS_CHOICES = [
        ('success', 'ตรวจจับสำเร็จ'),
        ('failed', 'ตรวจจับล้มเหลว'),
        ('no_face', 'ไม่พบใบหน้า'),
        ('multiple_faces', 'พบหลายใบหน้า'),
        ('mismatch', 'ใบหน้าไม่ตรง'),
        ('error', 'เกิดข้อผิดพลาด'),
        ('manual', 'บันทึกด้วยตนเอง'),
    ]
    
    rfid_scan_log = models.ForeignKey(RFIDScanLog, on_delete=models.CASCADE, related_name='face_recognition_logs')
    
    # ⭐ แก้ไข: เปลี่ยน related_name เป็น 'scanning_face_recognition_logs'
    student = models.ForeignKey(
        Student, 
        on_delete=models.CASCADE, 
        related_name='scanning_face_recognition_logs',  # ⭐ เปลี่ยนจาก 'face_recognition_logs'
        null=True, 
        blank=True
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, verbose_name='สถานะ')
    confidence_score = models.FloatField(null=True, blank=True, verbose_name='ค่าความมั่นใจ (0-100)')
    captured_image = models.ImageField(upload_to='face_scans/%Y/%m/%d/', null=True, blank=True)
    processing_time = models.FloatField(null=True, blank=True, verbose_name='เวลาประมวลผล (วินาที)')
    face_detection_details = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    
    is_manual = models.BooleanField(default=False, verbose_name='บันทึกด้วยตนเอง')
    manual_recorded_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='scanning_manual_face_logs'  # ⭐ เปลี่ยนจาก 'manual_face_logs'
    )
    manual_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'face_recognition_logs'
        verbose_name = 'บันทึกการตรวจจับใบหน้า'
        ordering = ['-created_at']
    
    def __str__(self):
        student_name = self.student.get_full_name() if self.student else 'ไม่ทราบ'
        record_type = "Manual" if self.is_manual else "Auto"
        return f"{student_name} - {self.get_status_display()} ({record_type})"


class DeviceStatus(models.Model):
    """สถานะอุปกรณ์"""
    STATUS_CHOICES = [
        ('online', 'ออนไลน์'),
        ('offline', 'ออฟไลน์'),
        ('maintenance', 'ปิดปรับปรุง'),
        ('error', 'ขัดข้อง'),
    ]
    
    device_name = models.CharField(max_length=100, unique=True)
    device_type = models.CharField(max_length=50, default='RFID_READER')
    location = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='offline')
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    firmware_version = models.CharField(max_length=50, blank=True)
    
    total_scans = models.IntegerField(default=0)
    successful_scans = models.IntegerField(default=0)
    failed_scans = models.IntegerField(default=0)
    
    last_heartbeat = models.DateTimeField(null=True, blank=True)
    last_online = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'device_status'
    
    def __str__(self):
        return f"{self.device_name} - {self.get_status_display()}"


class SystemAlert(models.Model):
    """การแจ้งเตือนของระบบ"""
    ALERT_TYPE_CHOICES = [
        ('device_offline', 'อุปกรณ์ออฟไลน์'),
        ('face_recognition_failed', 'ตรวจจับใบหน้าล้มเหลว'),
        ('unauthorized_access', 'พยายามเข้าถึงโดยไม่ได้รับอนุญาต'),
        ('system_error', 'ข้อผิดพลาดของระบบ'),
        ('maintenance', 'แจ้งเตือนบำรุงรักษา'),
        ('other', 'อื่นๆ'),
    ]
    
    SEVERITY_CHOICES = [
        ('low', 'ต่ำ'),
        ('medium', 'ปานกลาง'),
        ('high', 'สูง'),
        ('critical', 'วิกฤต'),
    ]
    
    alert_type = models.CharField(max_length=50, choices=ALERT_TYPE_CHOICES, verbose_name='ประเภท')
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default='medium', verbose_name='ระดับความรุนแรง')
    title = models.CharField(max_length=200, verbose_name='หัวข้อ')
    message = models.TextField(verbose_name='ข้อความ')
    
    device = models.ForeignKey(
        DeviceStatus, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        verbose_name='อุปกรณ์'
    )
    # ⭐ แก้ไข: เพิ่ม related_name ที่ไม่ซ้ำ
    student = models.ForeignKey(
        Student, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='scanning_system_alerts',  # ⭐ เพิ่ม related_name
        verbose_name='นักเรียน'
    )
    
    is_resolved = models.BooleanField(default=False, verbose_name='แก้ไขแล้ว')
    resolved_at = models.DateTimeField(null=True, blank=True, verbose_name='แก้ไขเมื่อ')
    resolved_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='scanning_resolved_alerts',  # ⭐ เพิ่ม related_name
        verbose_name='แก้ไขโดย'
    )
    resolution_notes = models.TextField(blank=True, verbose_name='หมายเหตุการแก้ไข')
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'system_alerts'
        verbose_name = 'การแจ้งเตือนระบบ'
        verbose_name_plural = 'การแจ้งเตือนระบบ'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['is_resolved', 'created_at']),
            models.Index(fields=['severity', 'is_resolved']),
        ]
    
    def __str__(self):
        return f"[{self.get_severity_display()}] {self.title}"


class DailyReport(models.Model):
    """รายงานประจำวัน"""
    report_date = models.DateField(unique=True, verbose_name='วันที่')
    
    total_students = models.IntegerField(default=0, verbose_name='นักเรียนทั้งหมด')
    students_present = models.IntegerField(default=0, verbose_name='มา')
    students_late = models.IntegerField(default=0, verbose_name='มาสาย')
    students_absent = models.IntegerField(default=0, verbose_name='ขาด')
    students_early_leave = models.IntegerField(default=0, verbose_name='ออกก่อนเวลา')
    
    total_scans = models.IntegerField(default=0, verbose_name='จำนวนการสแกนทั้งหมด')
    successful_scans = models.IntegerField(default=0, verbose_name='สแกนสำเร็จ')
    failed_scans = models.IntegerField(default=0, verbose_name='สแกนล้มเหลว')
    
    face_recognition_success = models.IntegerField(default=0, verbose_name='ตรวจจับใบหน้าสำเร็จ')
    face_recognition_failed = models.IntegerField(default=0, verbose_name='ตรวจจับใบหน้าล้มเหลว')
    
    devices_online = models.IntegerField(default=0, verbose_name='อุปกรณ์ออนไลน์')
    devices_offline = models.IntegerField(default=0, verbose_name='อุปกรณ์ออฟไลน์')
    
    system_alerts_count = models.IntegerField(default=0, verbose_name='จำนวนการแจ้งเตือน')
    
    notes = models.TextField(blank=True, verbose_name='หมายเหตุ')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'daily_reports'
        verbose_name = 'รายงานประจำวัน'
        verbose_name_plural = 'รายงานประจำวัน'
        ordering = ['-report_date']
    
    def __str__(self):
        return f"รายงานวันที่ {self.report_date}"
    
    def get_attendance_rate(self):
        """คำนวณอัตราการเข้าเรียน"""
        if self.total_students == 0:
            return 0
        return round((self.students_present / self.total_students) * 100, 2)