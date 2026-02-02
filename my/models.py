# my/models.py
# ⭐ แก้ไขแล้ว: เพิ่ม RFIDScanLog และ FaceRecognitionLog
# ⭐ RFID + Face Recognition → AttendanceRecord → BehaviorRecord
# ==========================================================================
# เวลาเข้า: 05:30 - 08:20 = มาปกติ
#           08:21 - 09:00 = มาสาย  
#           ไม่สแกน/ใบหน้าไม่ตรง = ขาด
# เวลาออก: 15:25 - 17:00 = ออกปกติ
#           ต้องสแกนบัตร + ยืนยันใบหน้า
# ==========================================================================

from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.db.models import Q
import logging

logger = logging.getLogger(__name__)


class User(AbstractUser):
    """โมเดลผู้ใช้งานระบบ รองรับ 3 บทบาทหลัก + หน้าที่เพิ่มเติม"""
    ROLE_CHOICES = [
        ('admin', 'ผู้ดูแลระบบ'),
        ('discipline_teacher', 'ครูฝ่ายปกครอง'),
        ('teacher', 'ครูทั่วไป'),
        ('student', 'นักเรียน'),
    ]
    
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='teacher')
    
    ADDITIONAL_DUTIES = [
        ('duty_teacher', 'ครูเวรประจำวัน'),
        ('homeroom_teacher', 'ครูประจำชั้น'),
    ]
    additional_duties = models.CharField(
        max_length=100, 
        blank=True, 
        verbose_name='หน้าที่เพิ่มเติม',
        help_text='เลือกได้หลายหน้าที่ (คั่นด้วยเครื่องหมาย ,)'
    )
    
    is_active_staff = models.BooleanField(default=True, verbose_name='สถานะการเป็นบุคลากร')
    
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def get_additional_duties_list(self):
        if self.additional_duties:
            return [duty.strip() for duty in self.additional_duties.split(',') if duty.strip()]
        return []

    def has_additional_duty(self, duty):
        return duty in self.get_additional_duties_list()

    def can_manage_all_students(self):
        return self.role in ['admin', 'discipline_teacher']

    def can_manage_behavior_scores(self):
        return self.role in ['admin', 'discipline_teacher']
    
    def is_teacher_or_admin(self):
        return self.role in ['admin', 'discipline_teacher', 'teacher']

    def clean(self):
        super().clean()
        if self.role == 'admin' and self.pk is None:
            admin_count = User.objects.filter(role='admin').count()
            if admin_count >= 2:
                raise ValidationError('สามารถสร้างบัญชีผู้ดูแลระบบได้เพียง 2 บัญชีเท่านั้น')

    class Meta:
        db_table = 'users'
        verbose_name = 'ผู้ใช้งาน'
        verbose_name_plural = 'ผู้ใช้งาน'


class Student(models.Model):
    """โมเดลข้อมูลนักเรียน ป.1-6"""
    GENDER_CHOICES = [
        ('M', 'ชาย'),
        ('F', 'หญิง'),
    ]
    
    GRADE_CHOICES = [
        ('1', 'ประถมศึกษาปีที่ 1'),
        ('2', 'ประถมศึกษาปีที่ 2'),
        ('3', 'ประถมศึกษาปีที่ 3'),
        ('4', 'ประถมศึกษาปีที่ 4'),
        ('5', 'ประถมศึกษาปีที่ 5'),
        ('6', 'ประถมศึกษาปีที่ 6'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='student_profile', null=True, blank=True)
    first_name = models.CharField(max_length=150, verbose_name='ชื่อ')
    last_name = models.CharField(max_length=150, verbose_name='นามสกุล')
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, verbose_name='เพศ')
    
    grade = models.CharField(max_length=1, choices=GRADE_CHOICES, verbose_name='ชั้นเรียน')
    classroom = models.CharField(max_length=10, verbose_name='ห้อง')
    student_id = models.CharField(max_length=20, unique=True, verbose_name='รหัสนักเรียน')
    
    homeroom_teacher = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='homeroom_students',
        limit_choices_to=Q(additional_duties__contains='homeroom_teacher'),
        verbose_name='ครูประจำชั้น'
    )
    
    rfid_card_id = models.CharField(max_length=50, unique=True, verbose_name='รหัสบัตร RFID')
    face_image = models.ImageField(
        upload_to='student_faces/', 
        null=True, 
        blank=True, 
        verbose_name='รูปภาพใบหน้าสำหรับตรวจจับ'
    )
    
    # ⭐ คะแนนพฤติกรรม เริ่มต้น 100 คะแนน
    behavior_score = models.IntegerField(default=100, verbose_name='คะแนนพฤติกรรม')
    
    is_active = models.BooleanField(default=True, verbose_name='สถานะการเป็นนักเรียน')
    academic_year = models.CharField(max_length=10, default='2568', verbose_name='ปีการศึกษา')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.student_id} - {self.first_name} {self.last_name} (ป.{self.grade}/{self.classroom})"

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}"

    def get_grade_room(self):
        return f"ป.{self.grade}/{self.classroom}"

    def can_be_managed_by(self, user):
        if user.role in ['admin', 'discipline_teacher']:
            return True
        if user.has_additional_duty('homeroom_teacher') and self.homeroom_teacher == user:
            return True
        if user.role == 'student' and self.user == user:
            return True
        return False
    
    def reset_behavior_score(self):
        """รีเซ็ตคะแนนพฤติกรรมกลับเป็น 100 (ใช้ตอนเริ่มภาคเรียนใหม่)"""
        settings = SchoolSettings.objects.first()
        default_score = settings.default_behavior_score if settings else 100
        self.behavior_score = default_score
        self.save(update_fields=['behavior_score'])
        return self.behavior_score

    class Meta:
        db_table = 'students'
        verbose_name = 'นักเรียน'
        verbose_name_plural = 'นักเรียน'
        ordering = ['grade', 'classroom', 'student_id']
        indexes = [
            models.Index(fields=['grade', 'classroom']),
            models.Index(fields=['student_id']),
            models.Index(fields=['rfid_card_id']),
            models.Index(fields=['behavior_score']),
        ]


class AttendanceRecord(models.Model):
    """บันทึกการเข้าเรียน"""
    ATTENDANCE_STATUS = [
        ('present', 'มา'),
        ('late', 'มาสาย'),
        ('absent', 'ขาด'),
        ('early_leave', 'ออกก่อนเวลา'),
    ]

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='attendance_records')
    date = models.DateField(verbose_name='วันที่')
    
    check_in_time = models.TimeField(null=True, blank=True, verbose_name='เวลาเข้า')
    check_out_time = models.TimeField(null=True, blank=True, verbose_name='เวลาออก')
    
    status = models.CharField(max_length=20, choices=ATTENDANCE_STATUS, verbose_name='สถานะ')
    
    # ⭐ เชื่อมโยงกับ RFID Scan Logs
    check_in_rfid_log = models.ForeignKey(
        'RFIDScanLog',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='attendance_check_in',
        verbose_name='RFID Log เข้า'
    )
    check_out_rfid_log = models.ForeignKey(
        'RFIDScanLog',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='attendance_check_out',
        verbose_name='RFID Log ออก'
    )
    
    recorded_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        verbose_name='บันทึกโดย'
    )
    notes = models.TextField(null=True, blank=True, verbose_name='หมายเหตุ')
    is_manual_entry = models.BooleanField(default=False, verbose_name='บันทึกด้วยตนเอง')
    
    # ⭐ ฟิลด์สำหรับติดตามการหักคะแนน
    points_deducted = models.IntegerField(default=0, verbose_name='คะแนนที่หักแล้ว')
    is_penalty_applied = models.BooleanField(default=False, verbose_name='หักคะแนนแล้ว')
    
    # ⭐ ยืนยันตัวตนด้วยใบหน้า
    face_verified_in = models.BooleanField(default=False, verbose_name='ยืนยันใบหน้าตอนเข้า')
    face_verified_out = models.BooleanField(default=False, verbose_name='ยืนยันใบหน้าตอนออก')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.student.student_id} - {self.date} - {self.get_status_display()}"

    class Meta:
        db_table = 'attendance_records'
        verbose_name = 'บันทึกการเข้าเรียน'
        verbose_name_plural = 'บันทึกการเข้าเรียน'
        unique_together = ['student', 'date']
        ordering = ['-date', 'student__grade', 'student__classroom']
        indexes = [
            models.Index(fields=['date']),
            models.Index(fields=['student', 'date']),
            models.Index(fields=['status']),
            models.Index(fields=['points_deducted']),
            models.Index(fields=['is_penalty_applied']),
        ]


class BehaviorRecord(models.Model):
    """
    บันทึกคะแนนพฤติกรรม
    ⭐ ศูนย์กลางเดียวในการจัดการคะแนนพฤติกรรม - ทั้งหักและเพิ่มคะแนน
    """
    BEHAVIOR_TYPE = [
        ('deduct', 'หักคะแนน'),
        ('add', 'เพิ่มคะแนน'),
    ]
    
    # ⭐ ประเภทการหักคะแนนอัตโนมัติ
    AUTO_TYPE_CHOICES = [
        ('', '-'),
        ('late', 'มาสาย'),
        ('absent', 'ขาด'),
        ('early_leave', 'ออกก่อนเวลา'),
        ('no_checkout', 'ไม่สแกนออก'),
        ('face_mismatch', 'ใบหน้าไม่ตรง'),
    ]

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='behavior_records')
    behavior_type = models.CharField(max_length=10, choices=BEHAVIOR_TYPE, verbose_name='ประเภท')
    points = models.IntegerField(verbose_name='คะแนน')
    reason = models.TextField(verbose_name='เหตุผล')
    
    # อนุญาตให้เป็น null สำหรับการบันทึกอัตโนมัติ
    recorded_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='บันทึกโดย'
    )
    date_recorded = models.DateField(default=timezone.now, verbose_name='วันที่บันทึก')
    
    # ⭐ ฟิลด์สำหรับระบบอัตโนมัติ
    is_auto = models.BooleanField(default=False, verbose_name='ระบบบันทึกอัตโนมัติ')
    auto_type = models.CharField(
        max_length=20, 
        blank=True,
        default='',
        choices=AUTO_TYPE_CHOICES,
        verbose_name='ประเภทการหักอัตโนมัติ'
    )
    
    # เชื่อมโยงกับ AttendanceRecord (ถ้ามี)
    related_attendance = models.ForeignKey(
        AttendanceRecord, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        verbose_name='เกี่ยวข้องกับการเข้าเรียน'
    )
    
    # ⭐ เชื่อมโยงกับ RFIDScanLog
    related_rfid_scan = models.ForeignKey(
        'RFIDScanLog',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='เกี่ยวข้องกับการสแกน RFID'
    )
    
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        
        super().save(*args, **kwargs)
        
        # อัปเดตคะแนนพฤติกรรมของนักเรียน
        if is_new:
            if self.behavior_type == 'deduct':
                self.student.behavior_score = max(0, self.student.behavior_score - abs(self.points))
            else:  # add
                self.student.behavior_score += abs(self.points)
            self.student.save(update_fields=['behavior_score'])
            
            logger.info(f"{'หัก' if self.behavior_type == 'deduct' else 'เพิ่ม'}คะแนน {self.points} คะแนน นักเรียน {self.student.student_id} คะแนนคงเหลือ: {self.student.behavior_score}")

    def delete(self, *args, **kwargs):
        """
        ⭐ Override delete เพื่อคืนคะแนนเมื่อลบบันทึก
        """
        if self.behavior_type == 'deduct':
            self.student.behavior_score += abs(self.points)
        else:  # add
            self.student.behavior_score = max(0, self.student.behavior_score - abs(self.points))
        self.student.save(update_fields=['behavior_score'])
        
        logger.info(f"คืนคะแนน {self.points} คะแนน นักเรียน {self.student.student_id} คะแนนคงเหลือ: {self.student.behavior_score}")
        
        super().delete(*args, **kwargs)

    def __str__(self):
        recorder = self.recorded_by.get_full_name() if self.recorded_by else "ระบบอัตโนมัติ"
        return f"{self.student.student_id} - {self.get_behavior_type_display()} {self.points} คะแนน - {recorder}"

    class Meta:
        db_table = 'behavior_records'
        verbose_name = 'บันทึกพฤติกรรม'
        verbose_name_plural = 'บันทึกพฤติกรรม'
        ordering = ['-date_recorded', '-created_at']
        indexes = [
            models.Index(fields=['student', 'date_recorded']),
            models.Index(fields=['behavior_type']),
            models.Index(fields=['date_recorded']),
            models.Index(fields=['is_auto']),
            models.Index(fields=['auto_type']),
        ]


class RFIDScanLog(models.Model):
    """
    ⭐ บันทึกการสแกนบัตร RFID
    - สถานะ pending = รอยืนยันใบหน้า
    - สถานะ success = ยืนยันใบหน้าสำเร็จ
    - สถานะ failed = ยืนยันใบหน้าไม่ผ่าน
    """
    SCAN_TYPE_CHOICES = [
        ('check_in', 'สแกนเข้า'),
        ('check_out', 'สแกนออก'),
    ]
    
    STATUS_CHOICES = [
        ('pending', 'รอยืนยันใบหน้า'),
        ('success', 'สำเร็จ'),
        ('failed', 'ล้มเหลว'),
    ]
    
    ATTENDANCE_STATUS_CHOICES = [
        ('pending', 'รอตรวจสอบ'),
        ('present', 'มาปกติ'),
        ('late', 'มาสาย'),
        ('absent', 'ขาด'),
        ('checkout', 'ออก'),
        ('early_leave', 'ออกก่อนเวลา'),
    ]

    student = models.ForeignKey(
        Student, 
        on_delete=models.CASCADE, 
        null=True, 
        blank=True,
        related_name='rfid_scan_logs',
        verbose_name='นักเรียน'
    )
    rfid_card_id = models.CharField(max_length=50, verbose_name='รหัสบัตร RFID')
    
    scan_type = models.CharField(max_length=20, choices=SCAN_TYPE_CHOICES, verbose_name='ประเภทการสแกน')
    scan_time = models.DateTimeField(verbose_name='เวลาสแกน')
    
    # ⭐ สถานะการสแกน
    status = models.CharField(
        max_length=20, 
        choices=STATUS_CHOICES, 
        default='pending',
        verbose_name='สถานะ'
    )
    
    # ⭐ สถานะการเข้าเรียน (คำนวณจากเวลา)
    attendance_status = models.CharField(
        max_length=20,
        choices=ATTENDANCE_STATUS_CHOICES,
        default='pending',
        verbose_name='สถานะการเข้าเรียน'
    )
    
    device_name = models.CharField(max_length=100, default='', blank=True, verbose_name='ชื่ออุปกรณ์')
    device_location = models.CharField(max_length=200, default='', blank=True, verbose_name='ตำแหน่งอุปกรณ์')
    device_info = models.TextField(default='', blank=True, verbose_name='ข้อมูลอุปกรณ์')
    
    error_message = models.TextField(default='', blank=True, verbose_name='ข้อความผิดพลาด')
    processing_time = models.FloatField(null=True, blank=True, verbose_name='เวลาประมวลผล (วินาที)')
    
    # ⭐ การบันทึกแบบ Manual
    is_manual = models.BooleanField(default=False, verbose_name='บันทึกด้วยตนเอง')
    recorded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='my_rfid_scan_logs',
        verbose_name='บันทึกโดย'
    )
    
    # ⭐ คะแนนที่หัก (เก็บไว้อ้างอิง)
    points_deducted = models.IntegerField(default=0, verbose_name='คะแนนที่หัก')
    
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.rfid_card_id} - {self.get_scan_type_display()} - {self.scan_time.strftime('%d/%m/%Y %H:%M')}"

    class Meta:
        db_table = 'my_rfid_scan_logs'
        verbose_name = 'บันทึกการสแกน RFID'
        verbose_name_plural = 'บันทึกการสแกน RFID'
        ordering = ['-scan_time']
        indexes = [
            models.Index(fields=['rfid_card_id']),
            models.Index(fields=['scan_time']),
            models.Index(fields=['student', 'scan_time']),
            models.Index(fields=['status']),
            models.Index(fields=['scan_type']),
            models.Index(fields=['attendance_status']),
        ]


class FaceRecognitionLog(models.Model):
    """
    ⭐ บันทึกการตรวจจับใบหน้า
    - เชื่อมโยงกับ RFIDScanLog
    - ตรวจสอบว่าคนที่สแกนบัตรตรงกับเจ้าของบัตรหรือไม่
    """
    STATUS_CHOICES = [
        ('success', 'สำเร็จ - ใบหน้าตรง'),
        ('mismatch', 'ใบหน้าไม่ตรง'),
        ('no_face', 'ไม่พบใบหน้า'),
        ('error', 'เกิดข้อผิดพลาด'),
        ('manual', 'ยืนยันโดยครู'),
    ]

    # ⭐ เชื่อมโยงกับ RFIDScanLog (บังคับ)
    rfid_scan_log = models.OneToOneField(
        RFIDScanLog,
        on_delete=models.CASCADE,
        related_name='face_recognition',
        verbose_name='การสแกน RFID'
    )
    
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='face_recognition_logs',
        verbose_name='นักเรียน'
    )
    
    status = models.CharField(
        max_length=20, 
        choices=STATUS_CHOICES,
        verbose_name='สถานะ'
    )
    
    # ⭐ ความมั่นใจในการตรวจจับ (0-100%)
    confidence_score = models.FloatField(
        null=True, 
        blank=True,
        verbose_name='ความมั่นใจ (%)'
    )
    
    # รูปที่ถ่ายตอนสแกน
    captured_image = models.ImageField(
        upload_to='face_captures/', 
        null=True, 
        blank=True,
        verbose_name='รูปที่ถ่าย'
    )
    
    processing_time = models.FloatField(
        null=True, 
        blank=True,
        verbose_name='เวลาประมวลผล (วินาที)'
    )
    
    # ข้อมูลเพิ่มเติม
    face_detection_details = models.JSONField(
        default=dict, 
        blank=True,
        verbose_name='รายละเอียดการตรวจจับ'
    )
    
    error_message = models.TextField(
        default='', 
        blank=True,
        verbose_name='ข้อความผิดพลาด'
    )
    
    # ⭐ การยืนยันแบบ Manual โดยครู
    is_manual = models.BooleanField(default=False, verbose_name='ยืนยันด้วยตนเอง')
    manual_recorded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='my_face_recognition_logs',
        verbose_name='ครูที่ยืนยัน'
    )
    manual_notes = models.TextField(default='', blank=True, verbose_name='หมายเหตุการยืนยัน')
    
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        student_name = self.student.get_full_name() if self.student else "Unknown"
        return f"{student_name} - {self.get_status_display()} - {self.created_at.strftime('%d/%m/%Y %H:%M')}"
    
    def is_verified(self):
        """ตรวจสอบว่ายืนยันตัวตนสำเร็จหรือไม่"""
        return self.status in ['success', 'manual']

    class Meta:
        db_table = 'my_face_recognition_logs'
        verbose_name = 'บันทึกการตรวจจับใบหน้า'
        verbose_name_plural = 'บันทึกการตรวจจับใบหน้า'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['student', 'created_at']),
            models.Index(fields=['confidence_score']),
        ]


class SchoolSettings(models.Model):
    """
    การตั้งค่าระบบโรงเรียน
    ⭐ รองรับการตั้งค่าเวลาและคะแนนพฤติกรรมได้ตามต้องการ
    """
    school_name = models.CharField(max_length=200, default='โรงเรียนบ้านหนองหญ้าปล้อง', verbose_name='ชื่อโรงเรียน')
    academic_year = models.CharField(max_length=10, default='2568', verbose_name='ปีการศึกษาปัจจุบัน')
    
    # ⭐ เวลาสแกนเข้า
    school_start_time = models.TimeField(default='05:30', verbose_name='เวลาเปิดรับสแกนเข้า')
    normal_arrival_time = models.TimeField(default='08:20', verbose_name='เวลาเข้าปกติ (มาก่อนเวลานี้ = มาตรงเวลา)')
    late_arrival_time = models.TimeField(default='09:00', verbose_name='เวลาสิ้นสุดมาสาย (หลังเวลานี้ = ขาด)')
    
    # ⭐ เวลาสแกนออก
    school_end_time = models.TimeField(default='15:25', verbose_name='เวลาเปิดรับสแกนออก')
    latest_departure_time = models.TimeField(default='17:00', verbose_name='เวลาปิดรับสแกนออก')
    
    # คะแนนเริ่มต้น
    default_behavior_score = models.IntegerField(default=100, verbose_name='คะแนนพฤติกรรมเริ่มต้น')
    
    # คะแนนที่หัก
    late_penalty_points = models.IntegerField(default=1, verbose_name='หักคะแนนเมื่อมาสาย')
    absent_penalty_points = models.IntegerField(default=2, verbose_name='หักคะแนนเมื่อขาด')
    early_leave_penalty_points = models.IntegerField(default=1, verbose_name='หักคะแนนเมื่อออกก่อนเวลา')
    no_checkout_penalty_points = models.IntegerField(default=1, verbose_name='หักคะแนนเมื่อไม่สแกนออก')
    face_mismatch_penalty_points = models.IntegerField(default=2, verbose_name='หักคะแนนเมื่อใบหน้าไม่ตรง')
    
    # ⭐ Threshold สำหรับ Face Recognition
    face_confidence_threshold = models.FloatField(default=70.0, verbose_name='ค่าความมั่นใจขั้นต่ำ (%)')
    
    # เปิด/ปิดการหักคะแนนอัตโนมัติ
    auto_deduct_enabled = models.BooleanField(default=True, verbose_name='เปิดหักคะแนนอัตโนมัติ')
    
    # ⭐ เปิด/ปิดการบังคับยืนยันใบหน้า
    require_face_verification = models.BooleanField(default=True, verbose_name='บังคับยืนยันใบหน้า')

    # จำนวนผู้ดูแลระบบสูงสุด
    max_admin_users = models.IntegerField(default=2, verbose_name='จำนวนผู้ดูแลระบบสูงสุด')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"การตั้งค่า {self.school_name} - ปีการศึกษา {self.academic_year}"
    
    def get_scoring_config(self):
        """ส่งค่าการตั้งค่าคะแนนพฤติกรรมเป็น dict"""
        return {
            'default_score': self.default_behavior_score,
            'late_penalty': self.late_penalty_points,
            'absent_penalty': self.absent_penalty_points,
            'early_leave_penalty': self.early_leave_penalty_points,
            'no_checkout_penalty': self.no_checkout_penalty_points,
            'face_mismatch_penalty': self.face_mismatch_penalty_points,
            'auto_enabled': self.auto_deduct_enabled,
        }
    
    def get_time_config(self):
        """ส่งค่าการตั้งค่าเวลาเป็น dict"""
        return {
            'check_in_start': self.school_start_time,
            'check_in_on_time': self.normal_arrival_time,
            'check_in_late_end': self.late_arrival_time,
            'check_out_start': self.school_end_time,
            'check_out_end': self.latest_departure_time,
        }

    class Meta:
        db_table = 'school_settings'
        verbose_name = 'การตั้งค่าโรงเรียน'
        verbose_name_plural = 'การตั้งค่าโรงเรียน'


# ⭐ Device Status (สำหรับติดตามสถานะอุปกรณ์)
class DeviceStatus(models.Model):
    """สถานะอุปกรณ์"""
    DEVICE_TYPE_CHOICES = [
        ('RFID_READER', 'เครื่องอ่านบัตร RFID'),
        ('CAMERA', 'กล้องจับใบหน้า'),
        ('COMBO', 'เครื่องรวม RFID + กล้อง'),
    ]
    
    STATUS_CHOICES = [
        ('online', 'ออนไลน์'),
        ('offline', 'ออฟไลน์'),
        ('error', 'มีปัญหา'),
    ]

    device_name = models.CharField(max_length=100, verbose_name='ชื่ออุปกรณ์')
    device_type = models.CharField(max_length=50, choices=DEVICE_TYPE_CHOICES, verbose_name='ประเภท')
    location = models.CharField(max_length=200, default='', blank=True, verbose_name='ตำแหน่ง')
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='offline', verbose_name='สถานะ')
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name='IP Address')
    firmware_version = models.CharField(max_length=50, default='', blank=True, verbose_name='เวอร์ชัน')
    
    total_scans = models.IntegerField(default=0, verbose_name='จำนวนการสแกนทั้งหมด')
    successful_scans = models.IntegerField(default=0, verbose_name='สแกนสำเร็จ')
    failed_scans = models.IntegerField(default=0, verbose_name='สแกนล้มเหลว')
    
    last_heartbeat = models.DateTimeField(null=True, blank=True, verbose_name='Heartbeat ล่าสุด')
    last_online = models.DateTimeField(null=True, blank=True, verbose_name='ออนไลน์ล่าสุด')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.device_name} ({self.get_device_type_display()}) - {self.get_status_display()}"

    class Meta:
        db_table = 'my_device_status'
        verbose_name = 'สถานะอุปกรณ์'
        verbose_name_plural = 'สถานะอุปกรณ์'


class DailyReport(models.Model):
    """รายงานประจำวัน"""
    report_date = models.DateField(unique=True, verbose_name='วันที่')
    
    total_students = models.IntegerField(default=0, verbose_name='นักเรียนทั้งหมด')
    students_present = models.IntegerField(default=0, verbose_name='มาเรียน')
    students_late = models.IntegerField(default=0, verbose_name='มาสาย')
    students_absent = models.IntegerField(default=0, verbose_name='ขาด')
    students_early_leave = models.IntegerField(default=0, verbose_name='ออกก่อนเวลา')
    
    total_scans = models.IntegerField(default=0, verbose_name='การสแกนทั้งหมด')
    successful_scans = models.IntegerField(default=0, verbose_name='สแกนสำเร็จ')
    failed_scans = models.IntegerField(default=0, verbose_name='สแกนล้มเหลว')
    
    face_recognition_success = models.IntegerField(default=0, verbose_name='ยืนยันใบหน้าสำเร็จ')
    face_recognition_failed = models.IntegerField(default=0, verbose_name='ยืนยันใบหน้าล้มเหลว')
    
    devices_online = models.IntegerField(default=0, verbose_name='อุปกรณ์ออนไลน์')
    devices_offline = models.IntegerField(default=0, verbose_name='อุปกรณ์ออฟไลน์')
    
    system_alerts_count = models.IntegerField(default=0, verbose_name='การแจ้งเตือน')
    
    notes = models.TextField(default='', blank=True, verbose_name='หมายเหตุ')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"รายงานวันที่ {self.report_date.strftime('%d/%m/%Y')}"

    class Meta:
        db_table = 'my_daily_reports'
        verbose_name = 'รายงานประจำวัน'
        verbose_name_plural = 'รายงานประจำวัน'
        ordering = ['-report_date']


class SystemAlert(models.Model):
    """การแจ้งเตือนระบบ"""
    ALERT_TYPE_CHOICES = [
        ('face_mismatch', 'ใบหน้าไม่ตรงกัน'),
        ('suspicious_activity', 'กิจกรรมน่าสงสัย'),
        ('device_offline', 'อุปกรณ์ออฟไลน์'),
        ('system_error', 'ข้อผิดพลาดระบบ'),
        ('attendance_anomaly', 'ความผิดปกติการเข้าเรียน'),
    ]
    
    SEVERITY_CHOICES = [
        ('low', 'ต่ำ'),
        ('medium', 'ปานกลาง'),
        ('high', 'สูง'),
        ('critical', 'วิกฤต'),
    ]

    alert_type = models.CharField(max_length=50, choices=ALERT_TYPE_CHOICES, verbose_name='ประเภท')
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default='medium', verbose_name='ความรุนแรง')
    
    title = models.CharField(max_length=200, verbose_name='หัวข้อ')
    description = models.TextField(verbose_name='รายละเอียด')
    
    student = models.ForeignKey(
        Student, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='my_systemalerts', 
        verbose_name='นักเรียนที่เกี่ยวข้อง'
    )
    device = models.ForeignKey(
        DeviceStatus, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        verbose_name='อุปกรณ์ที่เกี่ยวข้อง'
    )
    
    is_resolved = models.BooleanField(default=False, verbose_name='แก้ไขแล้ว')
    resolved_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='my_resolved_alerts',  # เพิ่ม my_ นำหน้า
        verbose_name='แก้ไขโดย'
    )
    resolved_at = models.DateTimeField(null=True, blank=True, verbose_name='แก้ไขเมื่อ')
    resolution_notes = models.TextField(default='', blank=True, verbose_name='หมายเหตุการแก้ไข')
    
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"[{self.get_severity_display()}] {self.title}"

    class Meta:
        db_table = 'my_system_alerts'
        verbose_name = 'การแจ้งเตือนระบบ'
        verbose_name_plural = 'การแจ้งเตือนระบบ'
        ordering = ['-created_at']