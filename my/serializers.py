# my/serializers.py - แก้ไขแล้ว ⭐ เพิ่มฟิลด์ points_deducted และ is_penalty_applied

from rest_framework import serializers
from .models import *
from rest_framework.exceptions import ValidationError
from django.db import transaction
from django.contrib.auth.hashers import make_password
import logging

logger = logging.getLogger(__name__)


class UserSerializer(serializers.ModelSerializer):
    is_homeroom_teacher = serializers.BooleanField(required=False, default=False)
    is_duty_teacher = serializers.BooleanField(required=False, default=False)
    additional_duties_display = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'username', 'password', 'last_name', 'first_name', 'role',
            'is_homeroom_teacher', 'is_duty_teacher', 'additional_duties_display',
            'is_active_staff', 'created_at'
        ]
        extra_kwargs = {'password': {'write_only': True}}

    def get_additional_duties_display(self, obj):
        duties_map = {
            'homeroom_teacher': 'ครูประจำชั้น',
            'duty_teacher': 'ครูเวรประจำวัน'
        }
        
        display_duties = []
        for duty in obj.get_additional_duties_list():
            if duty in duties_map:
                display_duties.append(duties_map[duty])
            else:
                display_duties.append(duty)
        
        return display_duties

    def validate(self, attrs):
        role = attrs.get('role')
        
        if role == 'admin':
            settings = SchoolSettings.objects.first()
            max_admin = settings.max_admin_users if settings else 2
            
            current_count = User.objects.filter(role='admin').count()
            if self.instance and self.instance.role == 'admin':
                current_count -= 1
                
            if current_count >= max_admin:
                raise ValidationError(f'สามารถสร้างผู้ดูแลระบบได้เพียง {max_admin} บัญชีเท่านั้น')

        return attrs

    def create(self, validated_data):
        try:
            is_homeroom = validated_data.pop('is_homeroom_teacher', False)
            is_duty = validated_data.pop('is_duty_teacher', False)

            user = User.objects.create_user(
                username=validated_data['username'],
                password=validated_data['password'],
                first_name=validated_data['first_name'],
                last_name=validated_data['last_name'],
                role=validated_data['role']
            )
            
            duties_list = []
            if is_homeroom:
                duties_list.append('homeroom_teacher')
            if is_duty:
                duties_list.append('duty_teacher')
            
            user.additional_duties = ','.join(duties_list)
            
            if user.role == 'admin':
                user.is_superuser = True
                user.is_staff = True
            
            user.save()
            return user
        except Exception as e:
            raise serializers.ValidationError(str(e))

    def update(self, instance, validated_data):
        is_homeroom = validated_data.pop('is_homeroom_teacher', None)
        is_duty = validated_data.pop('is_duty_teacher', None)
        password = validated_data.pop('password', None)
        
        instance = super().update(instance, validated_data)
        
        if password:
            instance.set_password(password)
            instance.save()
        
        if is_homeroom is not None or is_duty is not None:
            duties_list = []
            if is_homeroom:
                duties_list.append('homeroom_teacher')
            if is_duty:
                duties_list.append('duty_teacher')
            
            instance.additional_duties = ','.join(duties_list)
            instance.save()
        
        return instance
    

class BehaviorRecordSerializer(serializers.ModelSerializer):
    """Serializer สำหรับคะแนนพฤติกรรม (แก้ไขแล้ว รวมข้อมูลนักเรียนมาให้เลย)"""
    student_name = serializers.SerializerMethodField()
    
    # เพิ่มข้อมูลนักเรียนโดยตรง เพื่อให้ Frontend ไม่ต้องไปค้นหาเอง
    student_code = serializers.CharField(source='student.student_id', read_only=True)
    grade = serializers.IntegerField(source='student.grade', read_only=True)
    classroom = serializers.CharField(source='student.classroom', read_only=True)
    face_image_url = serializers.SerializerMethodField()
    
    # Fields เดิมที่ rename
    score = serializers.IntegerField(source='points', read_only=True)
    date = serializers.DateField(source='date_recorded', read_only=True)

    class Meta:
        model = BehaviorRecord
        # ⭐ สำคัญ: ต้องใส่ 'student' (ID) และ fields ข้อมูลนักเรียนที่เราเพิ่ม
        fields = [
            'id', 'student', 'student_name', 'student_code', 
            'grade', 'classroom', 'face_image_url',
            'behavior_type', 'points', 'score', 
            'reason', 'date_recorded', 'date', 
            'is_auto', 'auto_type'
        ]
    
    def get_student_name(self, obj):
        if obj.student:
            return f"{obj.student.first_name} {obj.student.last_name}"
        return "ไม่พบข้อมูล"

    def get_face_image_url(self, obj):
        # ดึงรูปภาพนักเรียนมาแสดง
        if obj.student and obj.student.face_image:
            request = self.context.get('request')
            if request is not None:
                return request.build_absolute_uri(obj.student.face_image.url)
            return obj.student.face_image.url
        return None

class StudentSerializer(serializers.ModelSerializer):
    homeroom_teacher_name = serializers.SerializerMethodField()
    user_username = serializers.CharField(source='user.username', read_only=True, allow_null=True)
    user_id = serializers.CharField(source='user.id', read_only=True, allow_null=True)
    additional_duties_display = serializers.SerializerMethodField()
    
    # เพิ่ม face_image_url สำหรับ read-only
    face_image_url = serializers.SerializerMethodField()
    
    username = serializers.CharField(write_only=True, required=False)
    password = serializers.CharField(write_only=True, required=False)
    face_image = serializers.ImageField(required=False, allow_null=True, write_only=True)
    
    class Meta:
        model = Student
        fields = [
            'id', 'first_name', 'last_name', 'gender', 'grade', 'classroom','homeroom_teacher_name',  # <--- ชื่อครูมาตรงนี้
            'homeroom_teacher',  'student_id', 'face_image', 'face_image_url', # <--- รูปภาพมาตรงนี้
            'rfid_card_id', 'behavior_score', 'is_active',
            'academic_year', 'user_username', 'user_id', 'additional_duties_display',
            'username', 'password', 'created_at', 'updated_at'
        ]
        extra_kwargs = {
            'user': {'read_only': True},
            'homeroom_teacher': {'required': True},
            'user_username': {'read_only': True},
            'user_id': {'read_only': True}
        }
    
    def get_face_image_url(self, obj):
        """แก้ไข: ส่ง URL ของรูปภาพกลับไป"""
        if obj.face_image:
            request = self.context.get('request')
            if request is not None:
                return request.build_absolute_uri(obj.face_image.url)
            return obj.face_image.url
        return None
        
    def get_homeroom_teacher_name(self, obj):
        if obj.homeroom_teacher:
            return f"{obj.homeroom_teacher.first_name} {obj.homeroom_teacher.last_name}"
        return "ไม่ระบุ"

    def get_additional_duties_display(self, obj):
        if not obj.homeroom_teacher:
            return []
        
        duties_display = []
        homeroom_teacher = obj.homeroom_teacher
        duties_map = {
            'homeroom_teacher': 'ครูประจำชั้น',
            'duty_teacher': 'ครูเวรประจำวัน'
        }
        
        for duty in homeroom_teacher.get_additional_duties_list():
            if duty in duties_map:
                duties_display.append(duties_map[duty])
        
        return duties_display

    def validate(self, attrs):
        homeroom_teacher = attrs.get('homeroom_teacher')
        if homeroom_teacher and not homeroom_teacher.has_additional_duty('homeroom_teacher'):
            raise ValidationError('ครูที่เลือกต้องเป็นครูประจำชั้น')
        
        return attrs

    def create(self, validated_data):
        logger.info("=== Creating Student ===")
        logger.info(f"Validated data keys: {validated_data.keys()}")
        
        try:
            with transaction.atomic():
                username = validated_data.pop('username', None)
                password = validated_data.pop('password', None)
                
                user = None
                if username and password:
                    logger.info(f"Creating user account: {username}")
                    user = User.objects.create_user(
                        username=username,
                        password=password,
                        first_name=validated_data['first_name'],
                        last_name=validated_data['last_name'],
                        role='student'
                    )
                    logger.info(f"User created: {user.id}")
                
                student = Student.objects.create(
                    user=user,
                    **validated_data
                )
                
                logger.info(f"=== Student Created Successfully ===")
                logger.info(f"Student ID: {student.id}")
                logger.info(f"Student name: {student.first_name} {student.last_name}")
                logger.info(f"Has face_image: {bool(student.face_image)}")
                
                return student
                
        except Exception as e:
            logger.error(f"=== Error Creating Student ===")
            logger.error(f"Error: {str(e)}", exc_info=True)
            raise serializers.ValidationError(f'เกิดข้อผิดพลาดในการสร้างนักเรียน: {str(e)}')

    def update(self, instance, validated_data):
        logger.info("=== Updating Student ===")
        logger.info(f"Instance ID: {instance.id}")
        logger.info(f"Validated data keys: {validated_data.keys()}")
        
        try:
            with transaction.atomic():
                username = validated_data.pop('username', None)
                password = validated_data.pop('password', None)
                
                instance = super().update(instance, validated_data)
                
                if instance.user:
                    user = instance.user
                    
                    if username:
                        logger.info(f"Updating username to: {username}")
                        user.username = username
                    
                    if password:
                        logger.info("Updating password")
                        user.password = make_password(password)
                    
                    user.first_name = validated_data.get('first_name', user.first_name)
                    user.last_name = validated_data.get('last_name', user.last_name)
                    user.save()
                    logger.info("User updated successfully")
                
                logger.info(f"Student updated successfully. Has face_image: {bool(instance.face_image)}")
                return instance
                
        except Exception as e:
            logger.error(f"=== Error Updating Student ===")
            logger.error(f"Error: {str(e)}", exc_info=True)
            raise serializers.ValidationError(f'เกิดข้อผิดพลาดในการอัปเดตนักเรียน: {str(e)}')


class AttendanceRecordSerializer(serializers.ModelSerializer):
    """ส่งข้อมูลประวัติการเข้าเรียน"""
    class Meta:
        model = AttendanceRecord
        fields = [
            'id', 'date', 'check_in_time', 'check_out_time', 
            'status', 'points_deducted', 'is_manual_entry', 'notes'
        ]

class BehaviorRecordSerializer(serializers.ModelSerializer):
    """
    Serializer นี้สำคัญสำหรับหน้ารายงานพฤติกรรม
    เราจะดึงข้อมูลจาก Student มาแสดงที่นี่เลย จะได้ไม่ต้องไป Query ซ้ำซ้อน
    """
    student_name = serializers.SerializerMethodField()
    
    # ⭐ ดึงข้อมูลนักเรียนมาแสดงโดยตรง
    student_code = serializers.CharField(source='student.student_id', read_only=True)
    grade = serializers.IntegerField(source='student.grade', read_only=True)          # <--- ชั้น
    classroom = serializers.CharField(source='student.classroom', read_only=True)    # <--- ห้อง
    
    # ⭐ เพิ่มรูปภาพและครูที่ปรึกษา
    face_image_url = serializers.SerializerMethodField()                             # <--- รูป
    homeroom_teacher_name = serializers.SerializerMethodField()                      # <--- ครูที่ปรึกษา
    
    score = serializers.IntegerField(source='points', read_only=True)
    date = serializers.DateField(source='date_recorded', read_only=True)

    class Meta:
        model = BehaviorRecord
        fields = [
            'id', 'student', 
            'student_name', 'student_code', 
            'grade', 'classroom', 
            'face_image_url', 'homeroom_teacher_name',  # อย่าลืมใส่ใน fields
            'behavior_type', 'points', 'score', 
            'reason', 'date_recorded', 'date', 
            'is_auto', 'auto_type'
        ]
    
    def get_student_name(self, obj):
        if obj.student:
            return f"{obj.student.first_name} {obj.student.last_name}"
        return "ไม่พบข้อมูล"

    def get_face_image_url(self, obj):
        if obj.student and obj.student.face_image:
            request = self.context.get('request')
            if request is not None:
                return request.build_absolute_uri(obj.student.face_image.url)
            return obj.student.face_image.url
        return None
        
    def get_homeroom_teacher_name(self, obj):
        if obj.student and obj.student.homeroom_teacher:
            return f"{obj.student.homeroom_teacher.first_name} {obj.student.homeroom_teacher.last_name}"
        return "ไม่ระบุ"

class StudentSummarySerializer(serializers.ModelSerializer):
    homeroom_teacher_name = serializers.SerializerMethodField()
    attendance_summary = serializers.SerializerMethodField()
    user_username = serializers.CharField(source='user.username', read_only=True, allow_null=True)
    face_image_url = serializers.SerializerMethodField()

    class Meta:
        model = Student
        fields = [
            'id', 'first_name', 'last_name', 'gender', 'grade', 'classroom',
            'homeroom_teacher_name', 'student_id', 'behavior_score',
            'is_active', 'attendance_summary', 'user_username', 'face_image_url'
        ]

    def get_face_image_url(self, obj):
        if obj.face_image:
            request = self.context.get('request')
            if request is not None:
                return request.build_absolute_uri(obj.face_image.url)
            return obj.face_image.url
        return None

    def get_homeroom_teacher_name(self, obj):
        if obj.homeroom_teacher:
            return f"{obj.homeroom_teacher.first_name} {obj.homeroom_teacher.last_name}"
        return None

    def get_attendance_summary(self, obj):
        from django.utils import timezone
        current_month = timezone.now().date().replace(day=1)
        records = obj.attendance_records.filter(date__gte=current_month)
        
        return {
            'total_days': records.count(),
            'present': records.filter(status='present').count(),
            'late': records.filter(status='late').count(),
            'absent': records.filter(status='absent').count(),
            'leave': records.filter(status='early_leave').count(),
        }


class SchoolSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = SchoolSettings
        fields = '__all__'

class ManualEntrySerializer(serializers.Serializer):
    """
    Serializer สำหรับการเพิ่ม/แก้ไขข้อมูลด้วยมือ (Manual Entry)
    รองรับทั้งการเช็คชื่อและการตัดคะแนนไปพร้อมกัน
    """
    student_id = serializers.CharField(required=True)
    scan_type = serializers.ChoiceField(choices=['in', 'out'], default='in')
    timestamp = serializers.DateTimeField(required=True)
    note = serializers.CharField(required=False, allow_blank=True, default="Manual Entry by Admin")
    
    # ถ้า true ระบบจะสร้าง Face Log หลอกๆ ขึ้นมาด้วยเพื่อให้ data ครบตามที่คุณขอ
    sync_face_log = serializers.BooleanField(default=True)