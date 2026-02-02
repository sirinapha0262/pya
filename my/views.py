# my/views.py - แก้ไขแล้ว ⭐ เพิ่ม AutoAttendanceProcessingView

from django.shortcuts import render
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import NotFound, PermissionDenied
from django.db.models import Q, Count, Avg, Min, Max
from django.utils import timezone
from datetime import datetime, timedelta
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
import logging

from .models import *
from .serializers import *
from .attendance_auto_detector import *

# Setup logger
logger = logging.getLogger(__name__)

class MyTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token['role'] = user.role
        token['first_name'] = user.first_name
        token['last_name'] = user.last_name
        token['user_id'] = user.id
        
        return token


class MyTokenObtainPairView(TokenObtainPairView):
    serializer_class = MyTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == 200:
            try:
                user = User.objects.get(username=request.data['username'])
                data = response.data
                
                data['user_id'] = user.id
                data['role'] = user.role
                data['first_name'] = user.first_name
                data['last_name'] = user.last_name
                
                # ส่ง additional_duties กลับไปด้วย
                data['additional_duties'] = user.get_additional_duties_list()
                
                # ⭐ เพิ่ม is_homeroom_teacher
                data['is_homeroom_teacher'] = user.has_additional_duty('homeroom_teacher')
                data['is_duty_teacher'] = user.has_additional_duty('duty_teacher')
                
                # Add student-specific data if user is a student
                if user.role == 'student':
                    try:
                        student = user.student_profile
                        data['student_id'] = student.student_id
                        data['rfid_code'] = student.rfid_card_id
                        data['class_name'] = student.grade
                        data['room'] = student.classroom
                        if student.homeroom_teacher:
                            data['homeroom_teacher'] = f"{student.homeroom_teacher.first_name} {student.homeroom_teacher.last_name}"
                    except Student.DoesNotExist:
                        logger.warning(f"Student profile not found for user {user.id}")
                        data['student_id'] = ''
                        data['rfid_code'] = ''
                        data['class_name'] = ''
                        data['room'] = ''
                
                logger.info(f"Login successful for user: {user.username}, role: {user.role}")
                logger.info(f"Additional duties: {data['additional_duties']}")
                logger.info(f"is_homeroom_teacher: {data['is_homeroom_teacher']}")
                
                response.data = data
            except User.DoesNotExist:
                logger.error(f"User not found: {request.data.get('username')}")
                raise NotFound('ไม่พบผู้ใช้ที่ระบุ')
        return response


# Permission Classes
class IsAdminUser(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'admin'


class IsAdminOrDisciplineTeacher(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in ['admin', 'discipline_teacher']


class IsTeacherOrAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in [
            'admin', 'discipline_teacher', 'teacher'
        ]


class IsOwnerOrTeacher(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        if request.user.role in ['admin', 'discipline_teacher', 'teacher']:
            return True
        
        if request.user.role == 'student':
            return hasattr(request.user, 'student_profile') and request.user.student_profile == obj
        
        return False


# ViewSets
class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.exclude(role='student')
    serializer_class = UserSerializer
    
    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'me']:
            permission_classes = [IsAuthenticated]
        else:
            permission_classes = [IsAdminUser]
        
        return [permission() for permission in permission_classes]

    def get_queryset(self):
        queryset = User.objects.exclude(role='student')

        duty = self.request.query_params.get('duty')
        if duty:
            queryset = queryset.filter(
                Q(role=duty) | 
                Q(additional_duties__contains=duty)
            )

        return queryset.order_by('role', 'first_name')

    @action(detail=False, methods=['get'], permission_classes=[IsAuthenticated])
    def me(self, request):
        """ดึงข้อมูล user ที่ล็อกอินอยู่"""
        user = request.user
        serializer = self.get_serializer(user)
        data = serializer.data
        
        data['is_homeroom_teacher'] = user.has_additional_duty('homeroom_teacher')
        data['is_duty_teacher'] = user.has_additional_duty('duty_teacher')
        data['additional_duties'] = user.get_additional_duties_list()
        
        logger.info(f"User me: {user.username}, is_homeroom_teacher: {data['is_homeroom_teacher']}")
        
        return Response(data)

    @action(detail=False, methods=['get'])
    def role_counts(self, request):
        counts = {}
        for role, display in User.ROLE_CHOICES:
            if role != 'student':
                counts[role] = {
                    'count': User.objects.filter(role=role).count(),
                    'display_name': display
                }
        return Response(counts)


class AttendanceRecordViewSet(viewsets.ModelViewSet):
    serializer_class = AttendanceRecordSerializer
    permission_classes = [IsAuthenticated]
    queryset = AttendanceRecord.objects.all().order_by('-date')

    def get_queryset(self):
        queryset = super().get_queryset()
        
        # รับค่าจาก URL
        student_id = self.request.query_params.get('student_id')
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        
        # 1. กรองนักเรียน (รองรับทั้ง ID ตาราง และ รหัสนักเรียน)
        if student_id:
            queryset = queryset.filter(
                Q(student__student_id=student_id) | 
                Q(student__id=student_id)
            )
        
        # 2. กรองวันที่ (สำคัญมากสำหรับหน้าประวัติ)
        if date_from and date_to:
            queryset = queryset.filter(date__range=[date_from, date_to])
            
        return queryset

class BehaviorRecordViewSet(viewsets.ModelViewSet):
    serializer_class = BehaviorRecordSerializer
    permission_classes = [IsAuthenticated]
    queryset = BehaviorRecord.objects.all().order_by('-date_recorded')

    def get_queryset(self):
        queryset = super().get_queryset()
        
        student_id = self.request.query_params.get('student_id')
        behavior_type = self.request.query_params.get('behavior_type')
        
        if student_id:
            queryset = queryset.filter(
                Q(student__student_id=student_id) | 
                Q(student__id=student_id)
            )
            
        if behavior_type:
            queryset = queryset.filter(behavior_type=behavior_type)
            
        return queryset

class StudentViewSet(viewsets.ModelViewSet):
    queryset = Student.objects.all()
    serializer_class = StudentSerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        # ⭐ แก้ไข: อนุญาตให้ดู (list/retrieve) ได้ถ้า Login แล้ว (IsAuthenticated)
        # เพราะใน get_queryset เรากรองให้ดูได้แค่ตัวเองอยู่แล้ว ปลอดภัยครับ
        if self.action in ['list', 'retrieve']:
            permission_classes = [IsAuthenticated]
            
        # ส่วน Report/Export ให้ดูได้เฉพาะครูเหมือนเดิม
        elif self.action in ['summary', 'export']:
            permission_classes = [IsTeacherOrAdmin]
            
        # การแก้ไข/ลบข้อมูล ต้องเป็น Admin หรือฝ่ายปกครองเท่านั้น
        elif self.action in ['create', 'update', 'partial_update', 'destroy', 
                            'deactivate', 'activate', 'bulk_activate', 
                            'bulk_deactivate', 'bulk_delete']:
            permission_classes = [IsAdminOrDisciplineTeacher]
        else:
            permission_classes = [IsAuthenticated]
        
        return [permission() for permission in permission_classes]

    def get_queryset(self):
        queryset = Student.objects.select_related('user', 'homeroom_teacher').order_by('grade', 'classroom', 'student_id')
        
        # Log query for debugging
        logger.info(f"User role: {self.request.user.role}")
        logger.info(f"User additional_duties: {self.request.user.additional_duties}")
        
        if self.request.user.role in ['admin', 'discipline_teacher']:
            logger.info(f"Admin/Discipline teacher - showing all students: {queryset.count()}")
        elif self.request.user.has_additional_duty('homeroom_teacher'):
            queryset = queryset.filter(homeroom_teacher=self.request.user)
            logger.info(f"Filtered for homeroom teacher: {queryset.count()} students")
        elif self.request.user.has_additional_duty('duty_teacher'):
            queryset = queryset.filter(is_active=True)
            logger.info(f"Filtered for duty teacher: {queryset.count()} students")
        elif self.request.user.role == 'teacher':
            queryset = queryset.filter(is_active=True)
            logger.info(f"Filtered for teacher: {queryset.count()} students")
        elif self.request.user.role == 'student':
            try:
                queryset = queryset.filter(user=self.request.user)
                logger.info(f"Filtered for student: {queryset.count()} students")
            except:
                queryset = queryset.none()
                logger.warning("Student profile not found")

        # Apply filters from query params
        grade = self.request.query_params.get('grade', None)
        classroom = self.request.query_params.get('classroom', None)
        is_active = self.request.query_params.get('is_active', None)
        search = self.request.query_params.get('search', None)
        homeroom_teacher = self.request.query_params.get('homeroom_teacher', None)
        
        if grade:
            queryset = queryset.filter(grade=grade)
            
        if classroom:
            queryset = queryset.filter(classroom=classroom)
            
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')
            
        if search:
            queryset = queryset.filter(
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search) |
                Q(student_id__icontains=search)
            )
        if homeroom_teacher:
            queryset = queryset.filter(homeroom_teacher__id=homeroom_teacher)

        return queryset

    @action(detail=False, methods=['get'])
    def summary(self, request):
        queryset = self.get_queryset()
        serializer = StudentSummarySerializer(
            queryset, 
            many=True,
            context={'request': request}
        )
        return Response(serializer.data)

    @action(detail=False, methods=['post'])
    def deactivate(self, request):
        student_ids = request.data.get('student_ids', [])
        if not student_ids:
            return Response(
                {'error': 'ไม่พบรายการนักเรียน'}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        students = Student.objects.filter(id__in=student_ids)
        count = students.update(is_active=False)
        
        return Response({
            'message': f'ปิดการใช้งานนักเรียน {count} คนสำเร็จ',
            'count': count
        })

    @action(detail=False, methods=['post'])
    def activate(self, request):
        student_ids = request.data.get('student_ids', [])
        if not student_ids:
            return Response(
                {'error': 'ไม่พบรายการนักเรียน'}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        students = Student.objects.filter(id__in=student_ids)
        count = students.update(is_active=True)
        
        return Response({
            'message': f'เปิดการใช้งานนักเรียน {count} คนสำเร็จ',
            'count': count
        })

    @action(detail=False, methods=['post'])
    def bulk_activate(self, request):
        grade = request.data.get('grade')
        classroom = request.data.get('classroom')
        
        queryset = Student.objects.all()
        if grade:
            queryset = queryset.filter(grade=grade)
        if classroom:
            queryset = queryset.filter(classroom=classroom)
            
        count = queryset.update(is_active=True)
        
        return Response({
            'message': f'เปิดการใช้งานนักเรียน {count} คนสำเร็จ',
            'count': count
        })

    @action(detail=False, methods=['post'])
    def bulk_deactivate(self, request):
        grade = request.data.get('grade')
        classroom = request.data.get('classroom')
        
        queryset = Student.objects.all()
        if grade:
            queryset = queryset.filter(grade=grade)
        if classroom:
            queryset = queryset.filter(classroom=classroom)
            
        count = queryset.update(is_active=False)
        
        return Response({
            'message': f'ปิดการใช้งานนักเรียน {count} คนสำเร็จ',
            'count': count
        })

    @action(detail=False, methods=['delete'])
    def bulk_delete(self, request):
        student_ids = request.data.get('student_ids', [])
        if not student_ids:
            return Response(
                {'error': 'ไม่พบรายการนักเรียน'}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        students = Student.objects.filter(id__in=student_ids)
        count = students.count()
        students.delete()
        
        return Response({
            'message': f'ลบนักเรียน {count} คนสำเร็จ',
            'count': count
        })

    @action(detail=False, methods=['get'])
    def export(self, request):
        queryset = self.get_queryset()
        students = queryset.values(
            'student_id', 'first_name', 'last_name', 'grade', 
            'classroom', 'rfid_card_id', 'behavior_score', 
            'is_active', 'homeroom_teacher__first_name', 
            'homeroom_teacher__last_name'
        )
        
        return Response(list(students))


class AttendanceRecordViewSet(viewsets.ModelViewSet):
    queryset = AttendanceRecord.objects.all()
    serializer_class = AttendanceRecordSerializer

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            permission_classes = [IsTeacherOrAdmin]
        else:
            permission_classes = [IsAuthenticated]
        
        return [permission() for permission in permission_classes]

    def get_queryset(self):
        queryset = AttendanceRecord.objects.select_related('student', 'recorded_by').order_by('-date', 'student__grade', 'student__classroom')
        
        # Filter by query params
        date_from = self.request.query_params.get('date_from', None)
        date_to = self.request.query_params.get('date_to', None)
        status_filter = self.request.query_params.get('status', None)
        student_id = self.request.query_params.get('student_id', None)
        grade = self.request.query_params.get('grade', None)

        if date_from:
            queryset = queryset.filter(date__gte=date_from)
        if date_to:
            queryset = queryset.filter(date__lte=date_to)
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if student_id:
            queryset = queryset.filter(student__student_id__icontains=student_id)
        if grade:
            queryset = queryset.filter(student__grade=grade)

        return queryset

    def perform_create(self, serializer):
        serializer.save(recorded_by=self.request.user)

    @action(detail=False, methods=['get'])
    def today_summary(self, request):
        today = timezone.now().date()
        queryset = self.get_queryset().filter(date=today)
        
        summary = {
            'total': queryset.count(),
            'present': queryset.filter(status='present').count(),
            'late': queryset.filter(status='late').count(),
            'absent': queryset.filter(status='absent').count(),
            'early_leave': queryset.filter(status='early_leave').count(),
        }
        
        return Response(summary)


class BehaviorRecordViewSet(viewsets.ModelViewSet):
    queryset = BehaviorRecord.objects.all()
    serializer_class = BehaviorRecordSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            permission_classes = [IsAdminOrDisciplineTeacher]
        else:
            permission_classes = [IsAuthenticated]
        
        return [permission() for permission in permission_classes]

    def get_queryset(self):
        queryset = BehaviorRecord.objects.select_related('student', 'recorded_by').order_by('-date_recorded')
        
        if self.request.user.role in ['admin', 'discipline_teacher']:
            pass
        elif self.request.user.has_additional_duty('homeroom_teacher'):
            queryset = queryset.filter(student__homeroom_teacher=self.request.user)
        elif self.request.user.role == 'student':
            try:
                queryset = queryset.filter(student=self.request.user.student_profile)
            except:
                queryset = queryset.none()

        return queryset

    def perform_create(self, serializer):
        # รับค่า student หรือ student_id จากข้อมูลที่ส่งมา
        student_id = self.request.data.get('student') or self.request.data.get('student_id')
        
        # สั่งบันทึก โดยยัดเยียดค่า student_id ลงไปตรงๆ เพื่อกัน Error
        serializer.save(recorded_by=self.request.user, student_id=student_id)

    def perform_update(self, serializer):
        serializer.save(recorded_by=self.request.user)

class DashboardStatsView(APIView):
    permission_classes = [IsTeacherOrAdmin]

    def get(self, request):
        today = timezone.now().date()
        
        total_students = Student.objects.filter(is_active=True).count()
        total_teachers = User.objects.exclude(role__in=['admin', 'student']).count()
        
        today_attendance = AttendanceRecord.objects.filter(date=today)
        attendance_stats = {
            'total': today_attendance.count(),
            'present': today_attendance.filter(status='present').count(),
            'late': today_attendance.filter(status='late').count(),
            'absent': today_attendance.filter(status='absent').count(),
        }
        
        behavior_stats = Student.objects.filter(is_active=True).aggregate(
            avg_score=Avg('behavior_score'),
            min_score=Min('behavior_score'),
            max_score=Max('behavior_score')
        )

        return Response({
            'total_students': total_students,
            'total_teachers': total_teachers,
            'attendance_today': attendance_stats,
            'behavior_stats': behavior_stats,
        })


class AdminCountView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        admin_count = User.objects.filter(role='admin').count()
        return Response({'count': admin_count})


class HomeroomTeacherListView(APIView):
    """API สำหรับดึงรายชื่อครูประจำชั้น"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        teachers = User.objects.filter(
            additional_duties__contains='homeroom_teacher'
        ).values('id', 'first_name', 'last_name', 'role', 'additional_duties')
        
        logger.info(f"Found {len(teachers)} homeroom teachers")
        
        result = [{
            'id': teacher['id'],
            'first_name': teacher['first_name'],
            'last_name': teacher['last_name'],
            'name': f"{teacher['first_name']} {teacher['last_name']}",
            'role': teacher['role']
        } for teacher in teachers]
        
        return Response(result)
    

class CurrentAcademicYearView(APIView):
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        try:
            settings = SchoolSettings.objects.first()
            if settings:
                return Response({
                    'year': settings.academic_year,
                    'school_name': settings.school_name
                })
            else:
                current_thai_year = timezone.now().year + 543
                return Response({
                    'year': str(current_thai_year),
                    'school_name': '',
                    'default': True
                }, status=status.HTTP_200_OK)
                
        except Exception as e:
            current_thai_year = timezone.now().year + 543
            return Response({
                'year': str(current_thai_year),
                'school_name': '',
                'error': 'ใช้ค่าปีปัจจุบันแทน'
            }, status=status.HTTP_200_OK)


class UpdateAcademicYearView(APIView):
    permission_classes = [IsAdminUser]
    
    def post(self, request):
        try:
            new_year = request.data.get('year')
            
            if not new_year or len(new_year) != 4 or not new_year.isdigit():
                return Response({
                    'error': 'ปีการศึกษาต้องเป็นตัวเลข 4 หลัก'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            year_num = int(new_year)
            if year_num < 2560 or year_num > 2600:
                return Response({
                    'error': 'ปีการศึกษาต้องอยู่ระหว่าง 2560 - 2600'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            settings, created = SchoolSettings.objects.get_or_create(
                id=1,
                defaults={
                    'school_name': '',
                    'academic_year': new_year
                }
            )
            
            if not created:
                settings.academic_year = new_year
                settings.save()
            
            return Response({
                'year': settings.academic_year,
                'school_name': settings.school_name,
                'message': f'อัปเดตปีการศึกษาเป็น {new_year} สำเร็จ'
            })
            
        except Exception as e:
            return Response({
                'error': f'เกิดข้อผิดพลาด: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class AutoAttendanceProcessingView(APIView):
    """
    ⭐ API สำหรับเรียกใช้งานฟังก์ชันตรวจจับและบันทึกการขาดเรียนอัตโนมัติ
    """
    permission_classes = [IsAdminOrDisciplineTeacher]
    
    @method_decorator(never_cache)
    def post(self, request):
        try:
            action = request.data.get('action', 'run_all')
            
            if action == 'check_absent':
                # ตรวจจับนักเรียนที่ขาดเรียน
                count = auto_mark_absent_for_missing_records()
                message = f"ตรวจพบและบันทึกการขาดเรียน {count} คน"
                result = {'absent_marked': count}
                
            elif action == 'deduct_no_checkout':
                # หักคะแนนไม่สแกนออก
                count = auto_deduct_no_checkout()
                message = f"หักคะแนนไม่สแกนออก {count} คน"
                result = {'no_checkout_deducted': count}
                
            elif action == 'calculate_status':
                # คำนวณสถานะตามเวลา
                count = auto_calculate_attendance_status()
                message = f"คำนวณสถานะการเข้าเรียน {count} รายการ"
                result = {'status_updated': count}
                
            elif action == 'run_all':
                # รันทั้งหมด
                result = run_daily_attendance_processing()
                if result['success']:
                    message = f"ประมวลผลอัตโนมัติเสร็จสิ้น: อัปเดตสถานะ {result.get('status_updated', 0)} รายการ, บันทึกขาดเรียน {result.get('absent_marked', 0)} คน, หักคะแนนไม่สแกนออก {result.get('no_checkout_deducted', 0)} คน"
                else:
                    return Response({
                        'error': result.get('error', 'เกิดข้อผิดพลาด'),
                        'success': False
                    }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            else:
                return Response(
                    {'error': 'action ไม่ถูกต้อง'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            return Response({
                'message': message,
                'success': True,
                **result
            })
            
        except Exception as e:
            logger.error(f"เกิดข้อผิดพลาดในการประมวลผลอัตโนมัติ: {str(e)}", exc_info=True)
            return Response({
                'error': f'เกิดข้อผิดพลาด: {str(e)}',
                'success': False
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @method_decorator(never_cache)
    def get(self, request):
        """
        ⭐ API สำหรับตรวจสอบสถานะการประมวลผลอัตโนมัติ
        """
        try:
            today = timezone.now().date()
            
            # นับจำนวนนักเรียนทั้งหมดที่ยัง Active
            total_active_students = Student.objects.filter(is_active=True).count()
            
            # นับจำนวนบันทึกการเข้าเรียนวันนี้
            today_attendance_count = AttendanceRecord.objects.filter(date=today).count()
            
            # นับจำนวนนักเรียนที่ขาดเรียนวันนี้
            absent_today = AttendanceRecord.objects.filter(
                date=today,
                status='absent',
                is_manual_entry=False
            ).count()
            
            # นับจำนวนการหักคะแนนอัตโนมัติวันนี้
            auto_deductions_today = BehaviorRecord.objects.filter(
                date_recorded=today,
                is_auto=True
            ).count()
            
            # ตรวจสอบเวลาปัจจุบัน
            now = timezone.now()
            settings = SchoolSettings.objects.first()
            
            # ตรวจสอบว่ารันฟังก์ชันอัตโนมัติแล้วหรือยัง
            should_run_auto = False
            if settings:
                # ถ้าผ่านเวลา 17:00 แล้ว ให้ประมวลผล
                latest_departure = datetime.combine(today, settings.latest_departure_time)
                if now.time() > latest_departure.time():
                    should_run_auto = True
            
            return Response({
                'today': today.strftime('%Y-%m-%d'),
                'current_time': now.strftime('%H:%M:%S'),
                'total_active_students': total_active_students,
                'attendance_recorded_today': today_attendance_count,
                'auto_absent_marked_today': absent_today,
                'auto_deductions_today': auto_deductions_today,
                'should_run_auto_processing': should_run_auto,
                'school_end_time': settings.latest_departure_time.strftime('%H:%M') if settings else '17:00'
            })
            
        except Exception as e:
            logger.error(f"เกิดข้อผิดพลาดในการตรวจสอบสถานะ: {str(e)}", exc_info=True)
            return Response({
                'error': f'เกิดข้อผิดพลาด: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
class CheckBehaviorPermissionView(APIView):
    """API สำหรับตรวจสอบสิทธิ์จัดการคะแนนพฤติกรรม"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        user = request.user
        can_manage = user.role in ['admin', 'discipline_teacher']
        
        return Response({
            'can_manage_behavior': can_manage,
            'role': user.role,
            'additional_duties': user.get_additional_duties_list()
        })
    
class StudentMeView(APIView):
    """API ดึงข้อมูลนักเรียนปัจจุบัน + คะแนนจริงจาก DB"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        try:
            user = request.user
            
            if user.role != 'student':
                return Response({'error': 'ไม่ใช่นักเรียน'}, status=status.HTTP_403_FORBIDDEN)
            
            from .models import Student
            student = Student.objects.get(user=user)
            
            data = {
                'id': student.id,
                'student_id': student.student_id,
                'first_name': student.first_name,
                'last_name': student.last_name,
                'full_name': student.get_full_name(),
                'grade': student.grade,
                'classroom': student.classroom,
                'behavior_score': student.behavior_score,  # ⭐ จุดสำคัญ
                'rfid_card_id': student.rfid_card_id,
                'is_active': student.is_active,
            }
            
            print(f"✅ /students/me/ → student_id={student.student_id}, score={student.behavior_score}")
            return Response(data, status=status.HTTP_200_OK)
            
        except Exception as e:
            print(f"❌ Error: {e}")
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class StudentBehaviorHistoryView(APIView):
    """
    ⭐ API สำหรับดูประวัติคะแนนพฤติกรรมของนักเรียน
    
    GET /students/me/behavior-history/?month=1&year=2025
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        try:
            user = request.user
            
            if user.role != 'student':
                return Response({
                    'error': 'เฉพาะนักเรียนเท่านั้น'
                }, status=status.HTTP_403_FORBIDDEN)
            
            from .models import Student, BehaviorRecord
            
            try:
                student = user.student_profile
            except Student.DoesNotExist:
                return Response({
                    'error': 'ไม่พบข้อมูลนักเรียน'
                }, status=status.HTTP_404_NOT_FOUND)
            
            # รับพารามิเตอร์
            month = request.query_params.get('month')
            year = request.query_params.get('year')
            
            records = BehaviorRecord.objects.filter(student=student).order_by('-date_recorded')
            
            if month and year:
                records = records.filter(
                    date_recorded__month=int(month),
                    date_recorded__year=int(year)
                )
            
            # สร้าง response
            result = []
            for record in records[:50]:  # จำกัด 50 รายการ
                result.append({
                    'id': record.id,
                    'behavior_type': record.behavior_type,
                    'behavior_type_display': record.get_behavior_type_display(),
                    'points': record.points,
                    'reason': record.reason,
                    'date_recorded': record.date_recorded.isoformat(),
                    'is_auto': record.is_auto,
                    'auto_type': record.auto_type,
                    'auto_type_display': record.get_auto_type_display() if record.auto_type else None,
                    'recorded_by': (
                        f"{record.recorded_by.first_name} {record.recorded_by.last_name}"
                        if record.recorded_by else "ระบบอัตโนมัติ"
                    )
                })
            
            return Response({
                'student_id': student.student_id,
                'student_name': student.get_full_name(),
                'current_score': student.behavior_score,
                'records': result,
                'total_count': records.count()
            })
            
        except Exception as e:
            logger.error(f"เกิดข้อผิดพลาด: {str(e)}", exc_info=True)
            return Response({
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class StudentAttendanceHistoryView(APIView):
    """
    ⭐ API สำหรับดูประวัติการเข้าเรียนของนักเรียน
    
    GET /students/me/attendance-history/?month=1&year=2025
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        try:
            user = request.user
            
            if user.role != 'student':
                return Response({
                    'error': 'เฉพาะนักเรียนเท่านั้น'
                }, status=status.HTTP_403_FORBIDDEN)
            
            from .models import Student, AttendanceRecord
            
            try:
                student = user.student_profile
            except Student.DoesNotExist:
                return Response({
                    'error': 'ไม่พบข้อมูลนักเรียน'
                }, status=status.HTTP_404_NOT_FOUND)
            
            # รับพารามิเตอร์
            month = request.query_params.get('month')
            year = request.query_params.get('year')
            date_from = request.query_params.get('date_from')
            date_to = request.query_params.get('date_to')
            
            records = AttendanceRecord.objects.filter(student=student).order_by('-date')
            
            if month and year:
                records = records.filter(
                    date__month=int(month),
                    date__year=int(year)
                )
            elif date_from and date_to:
                records = records.filter(date__range=[date_from, date_to])
            
            # สร้าง response
            result = []
            for record in records:
                result.append({
                    'id': record.id,
                    'date': record.date.isoformat(),
                    'status': record.status,
                    'status_display': record.get_status_display(),
                    'check_in_time': record.check_in_time.strftime('%H:%M:%S') if record.check_in_time else None,
                    'check_out_time': record.check_out_time.strftime('%H:%M:%S') if record.check_out_time else None,
                    'points_deducted': record.points_deducted,
                    'is_manual_entry': record.is_manual_entry,
                    'notes': record.notes
                })
            
            # คำนวณสรุป
            summary = {
                'total': records.count(),
                'present': records.filter(status='present').count(),
                'late': records.filter(status='late').count(),
                'absent': records.filter(status='absent').count(),
                'early_leave': records.filter(status='early_leave').count()
            }
            
            return Response({
                'student_id': student.student_id,
                'student_name': student.get_full_name(),
                'current_behavior_score': student.behavior_score,
                'summary': summary,
                'records': result
            })
            
        except Exception as e:
            logger.error(f"เกิดข้อผิดพลาด: {str(e)}", exc_info=True)
            return Response({
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        


class ManualEntryViewSet(viewsets.ViewSet):
    """
    API จัดการข้อมูลด้วยมือ (Manual Entry) แบบเบ็ดเสร็จ
    จัดการทั้ง RFID, Face, Attendance และ Behavior Score ในที่เดียว
    """
    permission_classes = [IsAuthenticated] # หรือ AllowAny ชั่วคราวถ้าจะเทส

    def _calculate_status_and_penalty(self, scan_time):
        """Helper คำนวณสถานะและคะแนนที่จะหัก"""
        scan_time_only = scan_time.time()
        status = 'present'
        penalty = 0
        
        # กฎเวลาเข้าเรียน (ปรับเวลาตามจริงของโรงเรียนคุณได้ที่นี่)
        if scan_time_only > time(9, 0): # สายเกิน 09:00 หรือขาด
            status = 'absent'
            penalty = 2
        elif scan_time_only > time(8, 20): # สาย
            status = 'late'
            penalty = 1
            
        return status, penalty

    @transaction.atomic
    def create(self, request):
        """เพิ่มรายการใหม่ (Add Manual)"""
        serializer = ManualEntrySerializer(data=request.data)
        if serializer.is_valid():
            data = serializer.validated_data
            try:
                student = Student.objects.get(student_id=data['student_id'])
                scan_time = data['timestamp']
                scan_date = scan_time.date()
                
                # 1. คำนวณสถานะและคะแนน
                status_result, penalty = self._calculate_status_and_penalty(scan_time)
                
                # 2. สร้าง RFID Log (ตาราง my_rfid_scan_logs)
                rfid_log = RFIDScanLog.objects.create(
                    student=student,
                    scan_type=data['scan_type'],
                    timestamp=scan_time,
                    is_manual=True  # สมมติว่ามี field นี้ หรือใส่ note แทน
                )
                
                # 3. สร้าง Face Log (ตาราง my_face_recognition_logs) ตามที่คุณขอ
                if data['sync_face_log']:
                    FaceRecognitionLog.objects.create(
                        student=student,
                        timestamp=scan_time,
                        status='success',
                        similarity_score=1.0, # ค่าสมมติเพราะเป็น Manual
                        image_path='' # ไม่รูปเพราะเป็น Manual
                    )

                # 4. อัปเดต/สร้าง AttendanceRecord (ตาราง attendance_records)
                attendance, created = AttendanceRecord.objects.get_or_create(
                    student=student,
                    date=scan_date
                )
                
                if data['scan_type'] == 'in':
                    attendance.check_in_time = scan_time
                    attendance.status = status_result
                elif data['scan_type'] == 'out':
                    attendance.check_out_time = scan_time
                
                # บันทึกคะแนนที่หักลงใน record เพื่อใช้ตอนลบ (Rollback)
                if data['scan_type'] == 'in':
                    attendance.points_deducted = penalty 
                attendance.save()

                # 5. ตัดคะแนนนักเรียน (Behavior Score)
                if penalty > 0 and data['scan_type'] == 'in':
                    student.behavior_score -= penalty
                    student.save()
                    
                    # สร้าง Log การหักคะแนน
                    BehaviorRecord.objects.create(
                        student=student,
                        behavior_type_name="System Auto-Deduct (Manual Entry)",
                        points_deducted=penalty,
                        date=scan_date,
                        note=f"Manual entry: {data['note']}"
                    )

                return Response({"success": True, "message": "บันทึกข้อมูลและปรับปรุงคะแนนเรียบร้อย"}, status=status.HTTP_201_CREATED)

            except Student.DoesNotExist:
                return Response({"error": "ไม่พบรหัสนักเรียน"}, status=status.HTTP_404_NOT_FOUND)
            except Exception as e:
                return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @transaction.atomic
    def destroy(self, request, pk=None):
        """
        ลบรายการ (Delete Manual)
        pk: ID ของ RFIDScanLog ที่ต้องการลบ
        """
        try:
            # ค้นหาจาก RFID Log เป็นหลัก
            rfid_log = RFIDScanLog.objects.get(pk=pk)
            student = rfid_log.student
            scan_date = rfid_log.timestamp.date()
            scan_type = rfid_log.scan_type
            
            # --- 1. คืนคะแนน (Rollback Score) ---
            # เช็คว่าวันนั้นมีการหักคะแนนไหม จาก AttendanceRecord หรือ BehaviorRecord
            attendance = AttendanceRecord.objects.filter(student=student, date=scan_date).first()
            
            if attendance and scan_type == 'in':
                points_to_refund = attendance.points_deducted
                
                if points_to_refund > 0:
                    # คืนคะแนนให้นักเรียน
                    student.behavior_score += points_to_refund
                    student.save()
                    
                    # ลบประวัติการหักคะแนน (BehaviorRecord) ของวันนั้น
                    BehaviorRecord.objects.filter(
                        student=student, 
                        date=scan_date,
                        points_deducted=points_to_refund
                    ).delete()
                    
                    # Reset ค่า points_deducted ใน attendance
                    attendance.points_deducted = 0
                    attendance.save()

            # --- 2. ลบข้อมูล Face Logs ที่เวลาใกล้เคียงกัน ---
            # (ลบ my_face_recognition_logs)
            time_threshold = timedelta(seconds=5) # หา log ที่เกิดพร้อมๆ กัน
            FaceRecognitionLog.objects.filter(
                student=student,
                timestamp__range=(rfid_log.timestamp - time_threshold, rfid_log.timestamp + time_threshold)
            ).delete()

            # --- 3. อัปเดต AttendanceRecord (ลบเวลาออก) ---
            if attendance:
                if scan_type == 'in':
                    attendance.check_in_time = None
                    attendance.status = 'absent' # หรือสถานะเริ่มต้น
                elif scan_type == 'out':
                    attendance.check_out_time = None
                
                # ถ้าไม่มีทั้งเข้าและออก ให้ลบ Record ทิ้งไปเลยก็ได้
                if not attendance.check_in_time and not attendance.check_out_time:
                    attendance.delete()
                else:
                    attendance.save()

            # --- 4. ลบ RFID Log (ตัวต้นเรื่อง) ---
            rfid_log.delete()

            return Response({
                "success": True, 
                "message": "ลบข้อมูลสำเร็จ และคืนคะแนนให้เรียบร้อยแล้ว",
                "current_score": student.behavior_score
            })

        except RFIDScanLog.DoesNotExist:
            return Response({"error": "ไม่พบข้อมูล Log นี้"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"error": f"เกิดข้อผิดพลาด: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
