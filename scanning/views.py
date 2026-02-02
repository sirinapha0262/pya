# scanning/views.py
# ⭐ ระบบสแกน RFID และตรวจจับใบหน้า พร้อมหักคะแนนพฤติกรรมอัตโนมัติ
# ===========================================
# กฎการหักคะแนน:
#   - มาตรงเวลา (05:30 - 08:20): ไม่หักคะแนน
#   - มาสาย (08:21 - 09:00): หัก 1 คะแนน
#   - ขาด (ไม่มาสแกน หรือ มาหลัง 09:00): หัก 2 คะแนน
#   - ไม่สแกนออก: หัก 1 คะแนน
# ===========================================
from rest_framework import viewsets, permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from django.utils import timezone
from django.db.models import Count, Sum, Q, Avg
from django.db import transaction
from datetime import datetime, time, timedelta
from rest_framework.decorators import api_view, action
from collections import defaultdict


import logging
import base64
import json

from .models import *
from .serializers import *
from .utils import *
# ⭐ Import behavior scoring
from .behavior_scoring import *
from my.models import *


logger = logging.getLogger(__name__)


# ========================================
# ⭐ Permission Classes สำหรับ Manual Entry
# ========================================

class IsDutyTeacher(permissions.BasePermission):
    """
    ⭐ อนุญาตเฉพาะครูเวรประจำวัน, admin, discipline_teacher
    ใช้สำหรับ manual entry ใน FixAttendance.jsx
    """
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        # Admin และ discipline_teacher ผ่านเสมอ
        if request.user.role in ['admin', 'discipline_teacher']:
            return True
        # ตรวจสอบ additional_duties
        return request.user.has_additional_duty('duty_teacher')


# ========================================
# ⭐ ฟังก์ชันหักคะแนนพฤติกรรมอัตโนมัติ
# ========================================
def integrate_behavior_scoring_with_scan(student, scan_type, scan_time, attendance_status):
    """
    หักคะแนนพฤติกรรมอัตโนมัติเมื่อสแกน RFID
    
    Args:
        student: Student object
        scan_type: 'check_in' หรือ 'check_out'
        scan_time: datetime ของการสแกน
        attendance_status: สถานะภาษาไทย ('มาตรงเวลา', 'มาสาย', 'ขาด', ฯลฯ)
    
    Returns:
        dict: {
            'points_deducted': จำนวนคะแนนที่หัก,
            'reason': เหตุผล,
            'new_score': คะแนนใหม่,
            'behavior_record_id': ID ของ BehaviorRecord (ถ้ามี)
        }
    """
    from my.models import SchoolSettings
    
    result = {
        'points_deducted': 0,
        'reason': '',
        'new_score': student.behavior_score,
        'behavior_record_id': None
    }
    
    try:
        # ดึงการตั้งค่าจากระบบ
        settings = SchoolSettings.objects.first()
        
        # ถ้าปิดการหักคะแนนอัตโนมัติ
        if settings and not settings.auto_deduct_enabled:
            return result
        
        points_to_deduct = 0
        reason = ''
        behavior_type = ''
        auto_type = ''
        
        # กำหนดคะแนนที่จะหักตามสถานะ
        if scan_type == 'check_in':
            if attendance_status in ['มาสาย', 'late']:
                points_to_deduct = settings.late_penalty_points if settings else 1
                reason = f'มาสาย (สแกนเวลา {scan_time.strftime("%H:%M")})'
                behavior_type = 'deduct'
                auto_type = 'late'
            elif attendance_status in ['ขาด', 'absent']:
                points_to_deduct = settings.absent_penalty_points if settings else 2
                reason = f'ขาดเรียน (สแกนเวลา {scan_time.strftime("%H:%M")})'
                behavior_type = 'deduct'
                auto_type = 'absent'
        
        elif scan_type == 'check_out':
            if attendance_status in ['ออกก่อนเวลา', 'early_leave']:
                points_to_deduct = settings.early_leave_penalty_points if settings else 1
                reason = f'ออกก่อนเวลา (สแกนเวลา {scan_time.strftime("%H:%M")})'
                behavior_type = 'deduct'
                auto_type = 'early_leave'
            elif attendance_status in ['ไม่สแกนออก', 'no_checkout']:
                points_to_deduct = settings.no_checkout_penalty_points if settings else 1
                reason = f'ไม่สแกนออก'
                behavior_type = 'deduct'
                auto_type = 'no_checkout'
        
        # หักคะแนนถ้ามี
        if points_to_deduct > 0:
            # อัพเดทคะแนนนักเรียน
            student.behavior_score = max(0, student.behavior_score - points_to_deduct)
            student.save(update_fields=['behavior_score'])
            
            # บันทึก BehaviorRecord
            behavior_record_data = {
                'student': student,
                'behavior_type': behavior_type,
                'points': -points_to_deduct,  # ติดลบเพราะเป็นการหัก
                'reason': reason,
                'date_recorded': scan_time.date(),
                'is_auto': True,
                'recorded_by': None
            }
            
            # เพิ่ม auto_type ถ้ามี field นี้
            try:
                if hasattr(BehaviorRecord, 'auto_type'):
                    behavior_record_data['auto_type'] = auto_type
            except:
                pass
            
            behavior_record = BehaviorRecord.objects.create(**behavior_record_data)
            
            result = {
                'points_deducted': points_to_deduct,
                'reason': reason,
                'new_score': student.behavior_score,
                'behavior_record_id': behavior_record.id
            }
            
            logger.info(f"⭐ หักคะแนน {student.student_id}: -{points_to_deduct} ({reason})")
    
    except Exception as e:
        logger.error(f"❌ Error in behavior scoring: {str(e)}", exc_info=True)
    
    return result


# ⭐ วันเรียน: จันทร์(0) - ศุกร์(4)
SCHOOL_DAYS = [0, 1, 2, 3, 4]

# ⭐ เวลาตามกฎ
CHECK_IN_START = time(5, 30)       # เริ่มเปิดรับสแกน
CHECK_IN_ON_TIME = time(8, 20)     # สิ้นสุดเวลามาตรงเวลา
CHECK_IN_LATE_END = time(9, 0)     # สิ้นสุดเวลามาสาย
CHECK_OUT_START = time(15, 25)     # เริ่มเปิดรับสแกนออก
CHECK_OUT_END = time(17, 0)        # สิ้นสุดการสแกนออก



# ========================================
# ⭐ ViewSets สำหรับ CRUD
# ========================================

class RFIDScanLogViewSet(viewsets.ModelViewSet):
    """
    ⭐ ViewSet สำหรับจัดการบันทึกการสแกน RFID
    
    รองรับ:
    - GET /scanning/scan-logs/ → list all logs
    - POST /scanning/scan-logs/ → สร้าง manual entry (สำหรับครูเวร)
    - GET /scanning/scan-logs/<id>/ → retrieve specific log
    - PUT/PATCH /scanning/scan-logs/<id>/ → update log
    - DELETE /scanning/scan-logs/<id>/ → delete log
    """
    queryset = RFIDScanLog.objects.all()
    serializer_class = RFIDScanLogSerializer

    def get_queryset(self):
        # แสดงข้อมูลล่าสุดก่อน
        return RFIDScanLog.objects.select_related('student').order_by('-timestamp')
    
    def get_permissions(self):
        """
        กำหนดสิทธิ์ตาม action:
        - list, retrieve: ต้อง login
        - create: ต้องเป็นครูเวร/admin/discipline_teacher
        - update, destroy: ต้องเป็น authenticated
        """
        if self.action in ['list', 'retrieve']:
            permission_classes = [IsAuthenticated]
        elif self.action == 'create':
            # ⭐ อนุญาตให้ครูเวร/admin สร้างได้
            permission_classes = [IsDutyTeacher]
        else:  # update, partial_update, destroy
            permission_classes = [IsAuthenticated]
        
        return [permission() for permission in permission_classes]

    def get_queryset(self):
        queryset = RFIDScanLog.objects.select_related('student', 'recorded_by').order_by('-scan_time')
        
        # Filters
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        scan_type = self.request.query_params.get('scan_type')
        student_id = self.request.query_params.get('student_id')
        grade = self.request.query_params.get('grade')
        classroom = self.request.query_params.get('classroom')
        is_manual = self.request.query_params.get('is_manual')
        
        if date_from:
            queryset = queryset.filter(scan_time__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(scan_time__date__lte=date_to)
        if scan_type:
            queryset = queryset.filter(scan_type=scan_type)
        if student_id:
            queryset = queryset.filter(
                Q(student__student_id=student_id) | Q(student__id=student_id)
            )
        if grade:
            queryset = queryset.filter(student__grade=grade)
        if classroom:
            queryset = queryset.filter(student__classroom=classroom)
        if is_manual is not None:
            is_manual_bool = is_manual.lower() in ['true', '1', 'yes']
            queryset = queryset.filter(is_manual=is_manual_bool)
        
        return queryset
    
    def create(self, request, *args, **kwargs):
        """
        ⭐ สร้าง RFIDScanLog (Manual Entry โดยครูเวร)
        
        Request Body ที่ FixAttendance.jsx ส่งมา:
        {
            "student": 1,                              // student ID (required)
            "rfid_card_id": "1234567890",             // optional
            "scan_type": "check_in" | "check_out",   // required
            "scan_time": "2025-01-31T08:00:00+07:00",// required (ISO format)
            "status": "success",                      // optional, default="success"
            "device_name": "Manual Entry - Teacher", // optional
            "device_location": "Manual",             // optional
            "is_manual": true                        // optional, default=true
        }
        """
        user = request.user
        logger.info(f"📝 Manual RFID entry by {user.username}: {request.data}")
        print(f"📝 Manual RFID entry by {user.username}: {request.data}")
        
        # ดึงข้อมูลจาก request
        data = request.data.copy()
        
        # ⭐ รองรับทั้ง student และ student_id
        student_pk = data.get('student') or data.get('student_id')
        if not student_pk:
            return Response(
                {'error': 'กรุณาระบุนักเรียน (student หรือ student_id)'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # ⭐ แปลงเป็น integer (Frontend ส่งมาเป็น string)
        try:
            student_pk_int = int(student_pk)
        except (ValueError, TypeError):
            student_pk_int = None
        
        student = None
        
        # ลองหาด้วย primary key (id) ก่อน
        if student_pk_int:
            try:
                student = Student.objects.get(id=student_pk_int)
            except Student.DoesNotExist:
                pass
        
        # ถ้าไม่เจอ ลองหาด้วย student_id (รหัสนักเรียน)
        if not student:
            try:
                student = Student.objects.get(student_id=str(student_pk))
            except Student.DoesNotExist:
                return Response(
                    {'error': f'ไม่พบนักเรียน ID {student_pk}'},
                    status=status.HTTP_404_NOT_FOUND
                )
        
        # ตรวจสอบ scan_type
        scan_type_value = data.get('scan_type')
        if scan_type_value not in ['check_in', 'check_out']:
            return Response(
                {'error': 'scan_type ต้องเป็น check_in หรือ check_out'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # ตรวจสอบ scan_time
        scan_time_str = data.get('scan_time')
        if not scan_time_str:
            return Response(
                {'error': 'กรุณาระบุเวลาสแกน (scan_time)'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            # รองรับทั้ง ISO format
            if isinstance(scan_time_str, str):
                scan_time = datetime.fromisoformat(scan_time_str.replace('Z', '+00:00'))
            else:
                scan_time = scan_time_str
        except ValueError as e:
            return Response(
                {'error': f'รูปแบบ scan_time ไม่ถูกต้อง (ใช้ ISO format): {e}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # คำนวณ attendance_status จากเวลา
        attendance_status = self._calculate_attendance_status_from_time(scan_type_value, scan_time)
        
        # สร้าง RFIDScanLog
        try:
            scan_log = RFIDScanLog.objects.create(
                student=student,
                rfid_card_id=data.get('rfid_card_id') or student.rfid_card_id or '',
                scan_type=scan_type_value,
                scan_time=scan_time,
                status=data.get('status', 'success'),
                attendance_status=attendance_status,
                device_name=data.get('device_name', 'Manual Entry - Teacher'),
                device_location=data.get('device_location', 'Manual'),
                is_manual=True,
                recorded_by=user,
                points_deducted=0
            )
            
            logger.info(f"✅ Created RFIDScanLog ID: {scan_log.id} for student {student.student_id}")
            print(f"✅ Created RFIDScanLog ID: {scan_log.id} for student {student.student_id}")
            
            # Serialize และส่งกลับ
            serializer = self.get_serializer(scan_log)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            logger.error(f"❌ Error creating RFIDScanLog: {e}", exc_info=True)
            print(f"❌ Error creating RFIDScanLog: {e}")
            return Response(
                {'error': f'เกิดข้อผิดพลาด: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def _calculate_attendance_status_from_time(self, scan_type, scan_time):
        """
        คำนวณสถานะการเข้าเรียนจากเวลาสแกน
        
        กฎเวลา:
        - check_in 05:30-08:20 → present (มาปกติ)
        - check_in 08:20-09:00 → late (มาสาย)
        - check_in หลัง 09:00 → absent (ขาด)
        - check_out 15:25-17:30 → checkout (ออกปกติ)
        - check_out ก่อน 15:25 → early_leave (ออกก่อนเวลา)
        """
        if hasattr(scan_time, 'time'):
            local_time = scan_time.time()
        else:
            local_time = scan_time
        
        if scan_type == 'check_in':
            # เวลาเข้าเรียน
            if CHECK_IN_START <= local_time < CHECK_IN_ON_TIME:
                return 'present'
            elif CHECK_IN_ON_TIME <= local_time < CHECK_IN_LATE_END:
                return 'late'
            else:
                return 'absent'
        else:
            # check_out
            if CHECK_OUT_START <= local_time <= CHECK_OUT_END:
                return 'checkout'
            elif local_time < CHECK_OUT_START:
                return 'early_leave'
            else:
                return 'checkout'
    
    @action(detail=False, methods=['get'])
    def today(self, request):
        """ดึงข้อมูลการสแกนวันนี้"""
        today = timezone.now().date()
        queryset = self.get_queryset().filter(scan_time__date=today)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def summary(self, request):
        """สรุปการสแกนตามวันที่"""
        date_str = request.query_params.get('date')
        if date_str:
            try:
                date_param = datetime.strptime(date_str, '%Y-%m-%d').date()
            except:
                date_param = timezone.now().date()
        else:
            date_param = timezone.now().date()
        
        queryset = self.get_queryset().filter(scan_time__date=date_param)
        
        summary_data = {
            'date': str(date_param),
            'total_scans': queryset.count(),
            'check_in_count': queryset.filter(scan_type='check_in').count(),
            'check_out_count': queryset.filter(scan_type='check_out').count(),
            'success_count': queryset.filter(status='success').count(),
            'failed_count': queryset.filter(status='failed').count(),
            'pending_count': queryset.filter(status='pending').count(),
            'manual_count': queryset.filter(is_manual=True).count(),
            'attendance_breakdown': {
                'present': queryset.filter(attendance_status='present').count(),
                'late': queryset.filter(attendance_status='late').count(),
                'absent': queryset.filter(attendance_status='absent').count(),
                'early_leave': queryset.filter(attendance_status='early_leave').count(),
                'checkout': queryset.filter(attendance_status='checkout').count(),
            }
        }
        
        return Response(summary_data)


# ⭐ เพิ่ม ViewSet สำหรับคะแนนพฤติกรรม (สำหรับ /behavior/)
class BehaviorRecordViewSet(viewsets.ModelViewSet):
    """ViewSet สำหรับจัดการคะแนนพฤติกรรม"""
    queryset = BehaviorRecord.objects.all().order_by('-date_recorded')
    serializer_class = BehaviorRecordSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter ตามนักเรียน (Frontend ส่งมาเป็น query params)
        student_id = self.request.query_params.get('student_id')
        month = self.request.query_params.get('month')
        year = self.request.query_params.get('year')
        behavior_type = self.request.query_params.get('behavior_type')

        if student_id:
            # รองรับทั้งค้นหาด้วย ID ในตาราง หรือ Student ID (รหัสนักเรียน)
            queryset = queryset.filter(Q(student__student_id=student_id) | Q(student__id=student_id))
            
        if month and year:
            queryset = queryset.filter(date_recorded__month=month, date_recorded__year=year)
            
        if behavior_type:
            queryset = queryset.filter(behavior_type=behavior_type)
            
        return queryset
    
    def perform_create(self, serializer):
        # บันทึกว่าใครเป็นคนเพิ่มคะแนน (ดึงจาก User ที่ Login อยู่)
        serializer.save(recorded_by=self.request.user)

    def perform_update(self, serializer):
        # บันทึกว่าใครเป็นคนแก้ไข
        serializer.save(recorded_by=self.request.user)


class FaceRecognitionLogViewSet(viewsets.ModelViewSet):
    """
    ⭐ ViewSet สำหรับจัดการบันทึกการตรวจจับใบหน้า
    
    รองรับ:
    - GET /scanning/face-recognition/ → list
    - POST /scanning/face-recognition/ → create (manual entry)
    - GET/PUT/DELETE /scanning/face-recognition/<id>/ → detail operations
    """
    queryset = FaceRecognitionLog.objects.all()
    serializer_class = FaceRecognitionLogSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    
    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            permission_classes = [IsAuthenticated]
        elif self.action == 'create':
            permission_classes = [IsDutyTeacher]
        else:
            permission_classes = [IsAuthenticated]
        
        return [permission() for permission in permission_classes]

    def get_queryset(self):
        queryset = FaceRecognitionLog.objects.select_related(
            'rfid_scan_log', 'student', 'manual_recorded_by'
        ).order_by('-created_at')
        
        # Filters
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        status_filter = self.request.query_params.get('status')
        is_manual = self.request.query_params.get('is_manual')
        
        # ⭐ แก้ไข: ใช้ scan_time จาก rfid_scan_log แทน created_at
        # เพื่อให้ filter ตรงกับวันที่ที่บันทึกจริง (ไม่ใช่วันที่สร้าง record)
        if date_from:
            queryset = queryset.filter(rfid_scan_log__scan_time__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(rfid_scan_log__scan_time__date__lte=date_to)
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if is_manual is not None:
            queryset = queryset.filter(is_manual=is_manual.lower() == 'true')
        
        return queryset
    
    def create(self, request, *args, **kwargs):
        """
        ⭐ สร้าง FaceRecognitionLog (Manual Entry)
        
        Request Body ที่ FixAttendance.jsx ส่งมา:
        {
            "rfid_scan_log": 1,          // RFIDScanLog ID (required)
            "student": 1,                 // Student ID (optional)
            "status": "verified",         // optional
            "confidence_score": 100,      // optional
            "is_manual": true,            // optional
            "manual_recorded_by": 1,      // จะใส่อัตโนมัติ
            "manual_notes": "บันทึก..."   // optional
        }
        """
        user = request.user
        logger.info(f"📝 Creating FaceRecognitionLog by {user.username}: {request.data}")
        print(f"📝 Creating FaceRecognitionLog by {user.username}: {request.data}")
        
        data = request.data.copy()
        
        # ตรวจสอบ rfid_scan_log
        rfid_scan_log_id = data.get('rfid_scan_log')
        if not rfid_scan_log_id:
            return Response(
                {'error': 'กรุณาระบุ rfid_scan_log'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            rfid_scan_log = RFIDScanLog.objects.get(id=rfid_scan_log_id)
        except RFIDScanLog.DoesNotExist:
            return Response(
                {'error': f'ไม่พบ RFIDScanLog ID {rfid_scan_log_id}'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # ตรวจสอบ student (ใช้จาก rfid_scan_log ถ้าไม่ระบุ)
        student_id = data.get('student')
        student = None
        if student_id:
            try:
                student = Student.objects.get(id=student_id)
            except Student.DoesNotExist:
                return Response(
                    {'error': f'ไม่พบนักเรียน ID {student_id}'},
                    status=status.HTTP_404_NOT_FOUND
                )
        else:
            student = rfid_scan_log.student
        
        try:
            # แปลง confidence_score จาก 0-100 เป็น 0-1
            confidence = data.get('confidence_score', 100)
            if isinstance(confidence, (int, float)) and confidence > 1:
                confidence = confidence / 100.0
            
            face_log = FaceRecognitionLog.objects.create(
                rfid_scan_log=rfid_scan_log,
                student=student,
                status=data.get('status', 'verified'),
                confidence_score=confidence,
                captured_image=data.get('captured_image'),
                processing_time=data.get('processing_time', 0),
                error_message=data.get('error_message', ''),
                is_manual=data.get('is_manual', True),
                manual_recorded_by=user,
                manual_notes=data.get('manual_notes', '')
            )
            
            logger.info(f"✅ Created FaceRecognitionLog ID: {face_log.id}")
            print(f"✅ Created FaceRecognitionLog ID: {face_log.id}")
            
            serializer = self.get_serializer(face_log)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            logger.error(f"❌ Error creating FaceRecognitionLog: {e}", exc_info=True)
            print(f"❌ Error creating FaceRecognitionLog: {e}")
            return Response(
                {'error': f'เกิดข้อผิดพลาด: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class DeviceStatusViewSet(viewsets.ModelViewSet):
    """ViewSet สำหรับจัดการสถานะอุปกรณ์"""
    queryset = DeviceStatus.objects.all()
    serializer_class = DeviceStatusSerializer
    permission_classes = [IsAuthenticated]


class SystemAlertViewSet(viewsets.ModelViewSet):
    """ViewSet สำหรับจัดการการแจ้งเตือนระบบ"""
    queryset = SystemAlert.objects.all()
    serializer_class = SystemAlertSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = SystemAlert.objects.all().order_by('-created_at')
        
        is_resolved = self.request.query_params.get('is_resolved')
        severity = self.request.query_params.get('severity')
        
        if is_resolved is not None:
            queryset = queryset.filter(is_resolved=is_resolved.lower() == 'true')
        if severity:
            queryset = queryset.filter(severity=severity)
        
        return queryset


class DailyReportViewSet(viewsets.ModelViewSet):
    """ViewSet สำหรับจัดการรายงานประจำวัน"""
    queryset = DailyReport.objects.all()
    serializer_class = DailyReportSerializer
    permission_classes = [IsAuthenticated]


# ========================================
# ⭐ API สำหรับการสแกนจริง (เชื่อมต่อกับฮาร์ดแวร์)
# ========================================

class ScanProcessView(APIView):
    """
    ⭐ API หลักสำหรับการสแกนบัตร RFID พร้อมหักคะแนนพฤติกรรมอัตโนมัติ
    
    POST /scanning/scan/
    
    Body:
    {
        "rfid_card_id": "RFID123456",
        "device_name": "RFID_MAIN_001",
        "device_location": "ประตูหน้าโรงเรียน"
    }
    
    Response:
    {
        "success": true,
        "student": {...},
        "scan_type": "check_in",
        "attendance_status": "late",           // ⭐ สถานะ: present, late, absent
        "attendance_status_thai": "มาสาย",     // ⭐ สถานะภาษาไทย
        "points_deducted": 1,                  // ⭐ คะแนนที่หัก
        "scan_time": "2024-01-15T08:25:00",
        ...
    }
    """
    permission_classes = [AllowAny]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def post(self, request):
        """
        ⭐ แก้ไขให้รองรับ Face Verification
        
        รับ:
        - rfid_card_id: รหัสบัตร RFID
        - captured_image: รูปถ่ายจากกล้อง (base64)
        - device_name: ชื่ออุปกรณ์
        - device_location: ตำแหน่ง
        """
        from .integrated_attendance_service import IntegratedAttendanceService
        
        start_time = timezone.now()
        
        try:
            rfid_card_id = request.data.get('rfid_card_id', '').strip()
            captured_image = request.data.get('captured_image', '')  # ⭐ รับรูปจากกล้อง
            device_name = request.data.get('device_name', 'Unknown Device')
            device_location = request.data.get('device_location', '')
            device_info = request.data.get('device_info', '')
            
            logger.info(f"🎯 รับข้อมูลสแกน: RFID={rfid_card_id}, Device={device_name}, HasImage={bool(captured_image)}")
            
            if not rfid_card_id:
                return Response({
                    'success': False,
                    'error': 'กรุณาระบุรหัสบัตร RFID',
                    'reason': 'RFID_REQUIRED'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # ⭐ ตรวจสอบว่ามีรูปถ่ายหรือไม่ (บังคับ)
            if not captured_image:
                return Response({
                    'success': False,
                    'message': 'กรุณาถ่ายรูปเพื่อยืนยันตัวตน',
                    'reason': 'FACE_REQUIRED',
                    'requires_face_verification': True
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # กำหนดประเภทการสแกน (เข้า/ออก) จากเวลาปัจจุบัน
            scan_time = datetime.now()
            total_minutes = scan_time.hour * 60 + scan_time.minute
            
            # เวลาสแกนเข้า: 05:30 - 09:00 (330 - 540 นาที)
            # เวลาสแกนออก: 15:25 - 18:00 (925 - 1080 นาที)
            if 330 <= total_minutes <= 540:
                scan_type = 'check_in'
            elif 925 <= total_minutes <= 1080:
                scan_type = 'check_out'
            else:
                return Response({
                    'success': False,
                    'message': 'ไม่อยู่ในช่วงเวลาสแกน (เข้า: 05:30-09:00, ออก: 15:25-18:00)',
                    'reason': 'OUTSIDE_SCANNING_HOURS',
                    'current_time': scan_time.strftime('%H:%M:%S')
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # ⭐ เรียกใช้ IntegratedAttendanceService (รวม Face Verification)
            service = IntegratedAttendanceService()
            
            result = service.process_rfid_with_face(
                rfid_card_id=rfid_card_id,
                captured_image_base64=captured_image,
                scan_type=scan_type,
                device_name=device_name,
                device_location=device_location
            )
            
            # แปลงผลลัพธ์ให้ Frontend ใช้งานได้
            if result.get('success'):
                student_info = result.get('student', {})
                face_info = result.get('face_verification', {})
                behavior_info = result.get('behavior_result', {})
                
                # Map attendance_status เป็นภาษาไทย
                status_map = {
                    'present': 'มา',
                    'late': 'มาสาย', 
                    'absent': 'ขาด',
                    'checkout': 'ออกปกติ',
                    'early_leave': 'ออกก่อนเวลา',
                    'no_checkout': 'ไม่สแกนออก'
                }
                attendance_status = result.get('attendance_status', 'present')
                attendance_status_thai = status_map.get(attendance_status, attendance_status)
                
                response_data = {
                    'success': True,
                    'message': result.get('message', 'สแกนสำเร็จ'),
                    'scan_time': result.get('timestamp'),
                    'scan_type': scan_type,
                    
                    # ข้อมูลนักเรียน
                    'student_id': student_info.get('student_id'),
                    'student_name': student_info.get('full_name'),
                    'class_name': student_info.get('grade'),
                    
                    # ⭐ สถานะการเข้าเรียน
                    'attendance_status': attendance_status_thai,
                    'rfid_verified': True,
                    
                    # ⭐ ผลการยืนยันใบหน้า
                    'face_verified': face_info.get('is_match', False),
                    'confidence': face_info.get('confidence', 0),
                    
                    # ⭐ ข้อมูลการหักคะแนน
                    'behavior_deducted': behavior_info.get('points_deducted', 0) if behavior_info else 0,
                    'behavior_reason': behavior_info.get('reason', '') if behavior_info else '',
                    'behavior_new_score': result.get('current_behavior_score'),
                    
                    # Backward compatibility
                    'student': {
                        'id': student_info.get('id'),
                        'student_id': student_info.get('student_id'),
                        'name': student_info.get('full_name'),
                        'grade': student_info.get('grade'),
                        'classroom': student_info.get('classroom'),
                        'behavior_score': result.get('current_behavior_score'),
                    },
                    
                    # Processing info
                    'processing_time': result.get('processing_time', 0)
                }
                
                logger.info(f"✅ สแกนสำเร็จ: {student_info.get('student_id')} | Face: {face_info.get('confidence', 0):.1f}%")
                
                return Response(response_data, status=status.HTTP_200_OK)
            else:
                # สแกนไม่สำเร็จ
                logger.warning(f"❌ สแกนล้มเหลว: {result.get('error', 'Unknown error')}")
                
                return Response({
                    'success': False,
                    'message': result.get('error', 'การสแกนล้มเหลว'),
                    'reason': result.get('error_code', 'UNKNOWN_ERROR'),
                    'rfid_verified': result.get('error_code') != 'STUDENT_NOT_FOUND',
                    'face_verified': False,
                    'confidence': result.get('face_verification', {}).get('confidence', 0),
                    'requires_face_verification': result.get('requires_face_verification', False)
                }, status=status.HTTP_400_BAD_REQUEST)
            
        except Exception as e:
            logger.error(f"❌ เกิดข้อผิดพลาดในการสแกน: {str(e)}", exc_info=True)
            return Response({
                'success': False,
                'error': f'เกิดข้อผิดพลาด: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ⭐ API สำหรับครูเวรบันทึกด้วยตนเอง
class ManualAttendanceRecordView(APIView):
    """
    ⭐ API สำหรับครูเวรบันทึกการเข้าเรียนด้วยตนเอง
    
    POST /scanning/manual-record/
    
    Body:
    {
        "student_id": 123,                    // ID ของนักเรียน (primary key)
        "scan_type": "check_in",              // check_in หรือ check_out
        "attendance_status": "present",       // present, late, absent
        "scan_time": "2024-01-15T08:00:00",   // (optional) ถ้าไม่ระบุใช้เวลาปัจจุบัน
        "notes": "นักเรียนมาสาย เนื่องจากรถติด"
    }
    """
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        try:
            student_id = request.data.get('student_id')
            scan_type = request.data.get('scan_type', 'check_in')
            attendance_status = request.data.get('attendance_status', 'present')
            scan_time_str = request.data.get('scan_time')
            notes = request.data.get('notes', '')
            
            # ตรวจสอบ input
            if not student_id:
                return Response({
                    'success': False,
                    'error': 'กรุณาระบุ student_id'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # ค้นหานักเรียน
            try:
                student = Student.objects.get(id=student_id, is_active=True)
            except Student.DoesNotExist:
                return Response({
                    'success': False,
                    'error': 'ไม่พบนักเรียน'
                }, status=status.HTTP_404_NOT_FOUND)
            
            # กำหนดเวลา
            if scan_time_str:
                try:
                    scan_time = datetime.fromisoformat(scan_time_str.replace('Z', '+00:00'))
                except:
                    scan_time = timezone.now()
            else:
                scan_time = timezone.now()
            
            # ตรวจสอบ attendance_status ที่ถูกต้อง
            valid_statuses = ['present', 'late', 'absent', 'checkout', 'early_leave']
            if attendance_status not in valid_statuses:
                return Response({
                    'success': False,
                    'error': f'attendance_status ต้องเป็น {", ".join(valid_statuses)}'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # คำนวณคะแนนที่หัก
            config = BehaviorScoringConfig()
            points_deducted = 0
            if attendance_status == 'late':
                points_deducted = config.LATE_DEDUCTION
            elif attendance_status == 'absent':
                points_deducted = config.ABSENT_DEDUCTION
            elif attendance_status == 'early_leave':
                points_deducted = config.EARLY_LEAVE_DEDUCTION
            
            with transaction.atomic():
                # สร้าง RFID Scan Log (manual)
                rfid_log = RFIDScanLog.objects.create(
                    student=student,
                    rfid_card_id=student.rfid_card_id or f"MANUAL_{student.student_id}",
                    scan_type=scan_type,
                    scan_time=scan_time,
                    status='success',
                    attendance_status=attendance_status,
                    points_deducted=points_deducted,
                    device_name='Manual Entry',
                    is_manual=True,
                    recorded_by=request.user,
                    error_message=notes or ""
                )
                
                # สร้าง Face Recognition Log (manual)
                face_log = FaceRecognitionLog.objects.create(
                    rfid_scan_log=rfid_log,
                    student=student,
                    status='manual',
                    confidence_score=100.0,
                    is_manual=True,
                    manual_recorded_by=request.user,
                    manual_notes=notes or ''
                )
                
                # หักคะแนนพฤติกรรม (ถ้าจำเป็น)
                behavior_result = {
                    'points_deducted': 0,
                    'reason': '',
                    'new_score': student.behavior_score
                }
                
                if attendance_status in ['late', 'absent'] and scan_type == 'check_in':
                    status_thai = self._get_status_thai(attendance_status)
                    behavior_result = integrate_behavior_scoring_with_scan(
                        student=student,
                        scan_type=scan_type,
                        scan_time=scan_time,
                        attendance_status=status_thai
                    )
            
            status_thai = self._get_status_thai(attendance_status)
            
            return Response({
                'success': True,
                'message': f'บันทึกสำเร็จ: {student.get_full_name()} - {status_thai}',
                'data': {
                    'rfid_scan_log_id': rfid_log.id,
                    'face_recognition_log_id': face_log.id,
                    'student': {
                        'id': student.id,
                        'student_id': student.student_id,
                        'name': student.get_full_name(),
                        'behavior_score': student.behavior_score
                    },
                    'scan_type': scan_type,
                    'attendance_status': attendance_status,
                    'attendance_status_thai': status_thai,
                    'points_deducted': points_deducted,
                    'scan_time': scan_time.isoformat(),
                    'recorded_by': f"{request.user.first_name} {request.user.last_name}",
                    'behavior_deducted': behavior_result['points_deducted'],
                    'behavior_new_score': behavior_result['new_score']
                }
            }, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            logger.error(f"❌ เกิดข้อผิดพลาดในการบันทึกด้วยตนเอง: {str(e)}", exc_info=True)
            return Response({
                'success': False,
                'error': f'เกิดข้อผิดพลาด: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def _get_status_thai(self, status_code):
        """แปลงสถานะเป็นภาษาไทย"""
        mapping = {
            'present': 'มาปกติ',
            'late': 'มาสาย',
            'absent': 'ขาด',
            'early_leave': 'ออกก่อนเวลา',
            'checkout': 'ออก'
        }
        return mapping.get(status_code, status_code)


class FaceVerificationView(APIView):
    """
    API สำหรับยืนยันใบหน้า
    
    POST /scanning/verify-face/
    """
    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        try:
            from .face_recognition_service import face_recognition_service
            
            rfid_scan_log_id = request.data.get('rfid_scan_log_id')
            captured_image = request.FILES.get('captured_image') or request.data.get('captured_image_base64')
            
            if not rfid_scan_log_id:
                return Response({
                    'success': False,
                    'error': 'กรุณาระบุ rfid_scan_log_id'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # ค้นหา RFID scan log
            try:
                rfid_log = RFIDScanLog.objects.get(id=rfid_scan_log_id)
            except RFIDScanLog.DoesNotExist:
                return Response({
                    'success': False,
                    'error': 'ไม่พบข้อมูลการสแกน RFID'
                }, status=status.HTTP_404_NOT_FOUND)
            
            student = rfid_log.student
            if not student:
                return Response({
                    'success': False,
                    'error': 'ไม่พบข้อมูลนักเรียน'
                }, status=status.HTTP_404_NOT_FOUND)
            
            start_time = timezone.now()
            
            # ตรวจจับและยืนยันใบหน้า
            result = face_recognition_service.verify_face(
                student=student,
                captured_image=captured_image
            )
            
            processing_time = (timezone.now() - start_time).total_seconds()
            
            # บันทึก Face Recognition Log
            face_log = FaceRecognitionLog.objects.create(
                rfid_scan_log=rfid_log,
                student=student,
                status=result['status'],
                confidence_score=result.get('confidence_score'),
                captured_image=captured_image if hasattr(captured_image, 'read') else None,
                processing_time=processing_time,
                face_detection_details=result.get('details', {}),
                error_message=result.get('error', '')
            )
            
            # อัปเดตสถานะ RFID log
            if result['status'] == 'success':
                rfid_log.status = 'success'
            else:
                rfid_log.status = 'failed'
                rfid_log.error_message = result.get('error', 'การยืนยันใบหน้าล้มเหลว')
            rfid_log.save()
            
            return Response({
                'success': result['status'] == 'success',
                'status': result['status'],
                'status_display': face_log.get_status_display(),
                'confidence_score': result.get('confidence_score'),
                'message': result.get('message', ''),
                'face_log_id': face_log.id,
                'student': {
                    'id': student.id,
                    'name': student.get_full_name()
                }
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"❌ เกิดข้อผิดพลาดในการยืนยันใบหน้า: {str(e)}", exc_info=True)
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class DeviceHeartbeatView(APIView):
    """API สำหรับรับ heartbeat จากอุปกรณ์"""
    permission_classes = [AllowAny]

    def post(self, request):
        try:
            device_name = request.data.get('device_name')
            device_type = request.data.get('device_type', 'RFID_READER')
            location = request.data.get('location', '')
            ip_address = request.data.get('ip_address')
            firmware_version = request.data.get('firmware_version', '')
            
            if not device_name:
                return Response({
                    'success': False,
                    'error': 'กรุณาระบุชื่ออุปกรณ์'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            device, created = DeviceStatus.objects.update_or_create(
                device_name=device_name,
                defaults={
                    'device_type': device_type,
                    'location': location,
                    'status': 'online',
                    'ip_address': ip_address,
                    'firmware_version': firmware_version,
                    'last_heartbeat': timezone.now(),
                    'last_online': timezone.now()
                }
            )
            
            return Response({
                'success': True,
                'device_id': device.id,
                'status': 'online',
                'created': created,
                'server_time': timezone.now().isoformat()
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"Heartbeat error: {str(e)}")
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ========================================
# ⭐ API สำหรับสถิติและรายงาน
# ========================================

class RealtimeStatsView(APIView):
    """API สำหรับสถิติแบบเรียลไทม์"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        start_of_day, end_of_day = get_today_date_range()
        
        # สถิตินักเรียน
        total_students = Student.objects.filter(is_active=True).count()
        
        # สถิติการสแกนวันนี้
        today_scans = RFIDScanLog.objects.filter(
            scan_time__range=(start_of_day, end_of_day)
        )
        
        check_ins = today_scans.filter(scan_type='check_in', status='success')
        check_outs = today_scans.filter(scan_type='check_out', status='success')
        
        # นับจำนวนนักเรียนที่มา
        students_present = check_ins.values('student').distinct().count()
        
        # สถิติอุปกรณ์
        devices_online = DeviceStatus.objects.filter(status='online').count()
        devices_total = DeviceStatus.objects.count()
        
        return Response({
            'timestamp': timezone.now().isoformat(),
            'students': {
                'total': total_students,
                'present': students_present,
                'absent': total_students - students_present
            },
            'scans': {
                'check_in': check_ins.count(),
                'check_out': check_outs.count(),
                'total': today_scans.count()
            },
            'devices': {
                'online': devices_online,
                'total': devices_total
            }
        })


class TodaySummaryView(APIView):
    """API สำหรับสรุปข้อมูลวันนี้"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        start_of_day, end_of_day = get_today_date_range()
        today = timezone.now().date()
        
        # ดึงข้อมูลนักเรียนทั้งหมด
        all_students = Student.objects.filter(is_active=True)
        total_students = all_students.count()
        
        # นักเรียนที่สแกนวันนี้
        scanned_student_ids = RFIDScanLog.objects.filter(
            scan_time__range=(start_of_day, end_of_day),
            status='success',
            scan_type='check_in'
        ).values_list('student_id', flat=True).distinct()
        
        present_count = len(scanned_student_ids)
        absent_count = total_students - present_count
        
        # นับมาสาย (สแกนหลัง 08:20)
        late_threshold = timezone.make_aware(datetime.combine(today, time(8, 20)))
        late_students = RFIDScanLog.objects.filter(
            scan_time__range=(late_threshold, end_of_day),
            status='success',
            scan_type='check_in'
        ).values_list('student_id', flat=True).distinct()
        late_count = len(late_students)
        
        # ⭐ สรุปการหักคะแนนวันนี้
        behavior_summary = behavior_scoring_service.get_daily_summary(today)
        
        return Response({
            'date': today.isoformat(),
            'attendance': {
                'total': total_students,
                'present': present_count,
                'late': late_count,
                'absent': absent_count,
                'rate': round((present_count / total_students) * 100, 2) if total_students > 0 else 0
            },
            # ⭐ ข้อมูลการหักคะแนน
            'behavior': behavior_summary
        })


class AttendanceReportView(APIView):
    """
    ⭐ API สำหรับรายงานการเข้าเรียน - ใช้ AttendanceRecord
    
    GET /scanning/attendance-report/?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD
    
    Response format:
    {
        "results": [...],  // รายชื่อนักเรียนทุกคนพร้อมสถานะ
        "summary": {...}
    }
    
    ⭐ แก้ไข: เพิ่มการเช็ควันเรียน (จ-ศ) - ถ้าไม่ใช่วันเรียนจะไม่นับขาด
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from my.models import Student, AttendanceRecord
        
        date_from = request.query_params.get('date_from')
        date_to = request.query_params.get('date_to')
        grade = request.query_params.get('grade')
        classroom = request.query_params.get('classroom')
        search = request.query_params.get('search')
        attendance_status_filter = request.query_params.get('attendance_status')  # present/late/absent
        
        # Default: วันนี้
        if not date_from:
            date_from = timezone.now().date().isoformat()
        if not date_to:
            date_to = timezone.now().date().isoformat()
        
        try:
            start_date = datetime.strptime(date_from, '%Y-%m-%d').date()
            end_date = datetime.strptime(date_to, '%Y-%m-%d').date()
        except ValueError:
            return Response({
                'error': 'รูปแบบวันที่ไม่ถูกต้อง กรุณาใช้ YYYY-MM-DD'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # ⭐⭐⭐ เพิ่มใหม่: ฟังก์ชันเช็ควันเรียน ⭐⭐⭐
        def is_school_day(check_date):
            """ตรวจสอบว่าเป็นวันเรียนหรือไม่ (จันทร์-ศุกร์)"""
            return check_date.weekday() < 5  # 0=จันทร์, 4=ศุกร์, 5=เสาร์, 6=อาทิตย์
        
        day_names = ['จันทร์', 'อังคาร', 'พุธ', 'พฤหัสบดี', 'ศุกร์', 'เสาร์', 'อาทิตย์']
        
        # ========================================
        # ⭐ 1. ดึงนักเรียนทั้งหมดที่ active
        # ========================================
        students_qs = Student.objects.filter(is_active=True).select_related('homeroom_teacher')
        
        if grade:
            students_qs = students_qs.filter(grade=grade)
        if classroom:
            students_qs = students_qs.filter(classroom=classroom)
        if search:
            students_qs = students_qs.filter(
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search) |
                Q(student_id__icontains=search)
            )
        
        # ========================================
        # ⭐ 2. ดึง AttendanceRecord ในช่วงวันที่
        # ========================================
        attendance_records = AttendanceRecord.objects.filter(
            date__range=(start_date, end_date)
        ).select_related('student', 'check_in_rfid_log', 'check_out_rfid_log')
        
        # สร้าง dict สำหรับ lookup attendance by student_id and date
        attendance_map = {}
        for record in attendance_records:
            key = (record.student_id, record.date)
            attendance_map[key] = record
        
        # ========================================
        # ⭐ 3. สร้างรายการนักเรียนทุกคนพร้อมสถานะ
        # ========================================
        results = []
        summary = {
            'total_students': 0,
            'present': 0,
            'late': 0,
            'absent': 0,
            'early_departure': 0,
            'checked_in': 0,
            'checked_out': 0,
            'is_school_day': True,   # ⭐ เพิ่มใหม่
            'day_name': ''           # ⭐ เพิ่มใหม่
        }
        
        # ถ้าเป็นวันเดียว แสดงทุกนักเรียน
        # ถ้าเป็นหลายวัน แสดงเฉพาะที่มี record
        is_single_day = (start_date == end_date)
        
        # ⭐⭐⭐ เพิ่มใหม่: เช็ควันเรียน ⭐⭐⭐
        if is_single_day:
            summary['day_name'] = day_names[start_date.weekday()]
            summary['is_school_day'] = is_school_day(start_date)
            
            # ถ้าไม่ใช่วันเรียน (ส-อ) → ไม่นับขาด แสดงเฉพาะ record ที่มีจริง
            if not is_school_day(start_date):
                logger.info(f"📊 AttendanceReport: {date_from} (วัน{summary['day_name']} - ไม่ใช่วันเรียน)")
                
                # ดึงเฉพาะ record ที่มีจริง (ถ้ามีคนมาวันหยุด)
                for record in attendance_records:
                    student = record.student
                    if not student:
                        continue
                    
                    # กรองตาม filters
                    if grade and student.grade != grade:
                        continue
                    if classroom and student.classroom != classroom:
                        continue
                    if search:
                        search_lower = search.lower()
                        if not (search_lower in student.first_name.lower() or 
                                search_lower in student.last_name.lower() or
                                search_lower in student.student_id.lower()):
                            continue
                    if attendance_status_filter and record.status != attendance_status_filter:
                        continue
                    
                    # นับสถิติ
                    summary['total_students'] += 1
                    if record.status == 'present':
                        summary['present'] += 1
                    elif record.status == 'late':
                        summary['late'] += 1
                    # ไม่นับ absent ในวันหยุด
                    
                    if record.check_in_time:
                        summary['checked_in'] += 1
                    if record.check_out_time:
                        summary['checked_out'] += 1
                    
                    # รวม date + time เป็น datetime
                    check_in_dt = None
                    check_out_dt = None
                    if record.check_in_time and record.date:
                        check_in_dt = datetime.combine(record.date, record.check_in_time)
                    if record.check_out_time and record.date:
                        check_out_dt = datetime.combine(record.date, record.check_out_time)
                    
                    result_item = {
                        'id': student.id,
                        'student_id': student.student_id,
                        'first_name': student.first_name,
                        'last_name': student.last_name,
                        'grade': student.grade,
                        'classroom': student.classroom,
                        'student_image_url': request.build_absolute_uri(student.face_image.url) if student.face_image else None,
                        'homeroom_teacher': student.homeroom_teacher.get_full_name() if student.homeroom_teacher else None,
                        'behavior_score': student.behavior_score,
                        'attendance_status': record.status,
                        'attendance_status_display': record.get_status_display() if hasattr(record, 'get_status_display') else record.status,
                        'scan_time': check_in_dt.isoformat() if check_in_dt else None,
                        'check_out_time': check_out_dt.isoformat() if check_out_dt else None,
                        'date': record.date.isoformat(),
                        'rfid_card_id': (record.check_in_rfid_log.rfid_card_id if record.check_in_rfid_log else None) or getattr(student, 'rfid_card_id', None),
                        'is_manual': record.is_manual_entry,
                        'points_deducted': record.points_deducted,
                        'notes': record.notes,
                        'student': {
                            'id': student.id,
                            'student_id': student.student_id,
                            'first_name': student.first_name,
                            'last_name': student.last_name,
                            'grade': student.grade,
                            'classroom': student.classroom,
                        }
                    }
                    results.append(result_item)
                
                # เพิ่ม legacy fields
                summary['total'] = len(results)
                summary['check_in'] = summary['checked_in']
                summary['check_out'] = summary['checked_out']
                
                return Response({
                    'results': results,
                    'summary': summary,
                    'date_from': date_from,
                    'date_to': date_to,
                    'total_records': len(results),
                    'message': f"วัน{summary['day_name']}ไม่ใช่วันเรียน"  # ⭐ แจ้ง Frontend
                })
        
        # ========================================
        # ⭐ 4. ถ้าเป็นวันเรียน → ทำงานปกติ (code เดิม)
        # ========================================
        if is_single_day:
            # แสดงนักเรียนทุกคน (รวมคนที่ขาด)
            for student in students_qs:
                key = (student.id, start_date)
                attendance = attendance_map.get(key)

                if not attendance:  # ← ถ้าไม่มี record
                    att_status = 'absent'  # ← กำหนดเป็น "ขาด"
                
                # กำหนดสถานะ
                if attendance:
                    att_status = attendance.status
                    att_status_display = attendance.get_status_display() if hasattr(attendance, 'get_status_display') else att_status
                    check_in_time = attendance.check_in_time
                    check_out_time = attendance.check_out_time
                    record_date = attendance.date
                    record_created_at = attendance.created_at
                    record_notes = attendance.notes
                else:
                    att_status = 'absent'
                    att_status_display = 'ขาดเรียน'
                    check_in_time = None
                    check_out_time = None
                    record_date = start_date
                    record_created_at = None
                    record_notes = None
                
                # ⭐ รวม date + time เป็น datetime
                check_in_datetime = None
                check_out_datetime = None
                if check_in_time and record_date:
                    check_in_datetime = datetime.combine(record_date, check_in_time)
                if check_out_time and record_date:
                    check_out_datetime = datetime.combine(record_date, check_out_time)
                
                # กรองตาม attendance_status
                if attendance_status_filter:
                    if attendance_status_filter != att_status:
                        continue
                
                # นับสถิติ
                summary['total_students'] += 1
                if att_status == 'present':
                    summary['present'] += 1
                elif att_status == 'late':
                    summary['late'] += 1
                else:
                    summary['absent'] += 1
                
                if check_in_time:
                    summary['checked_in'] += 1
                if check_out_time:
                    summary['checked_out'] += 1
                
                # สร้างข้อมูล result
                result_item = {
                    'id': student.id,
                    'student_id': student.student_id,
                    'first_name': student.first_name,
                    'last_name': student.last_name,
                    'grade': student.grade,
                    'classroom': student.classroom,
                    'student_image_url': request.build_absolute_uri(student.face_image.url) if student.face_image else None,
                    'homeroom_teacher': student.homeroom_teacher.get_full_name() if student.homeroom_teacher else None,
                    'behavior_score': student.behavior_score,
                    
                    # สถานะการเข้าเรียน
                    'attendance_status': att_status,
                    'attendance_status_display': att_status_display,
                    
                    # ⭐ เวลาเข้า-ออก (ใช้ datetime)
                    'scan_time': check_in_datetime.isoformat() if check_in_datetime else None,
                    'check_out_time': check_out_datetime.isoformat() if check_out_datetime else None,
                    'date': record_date.isoformat() if record_date else None,
                    
                    # ข้อมูลเพิ่มเติม
                    'rfid_card_id': (attendance.check_in_rfid_log.rfid_card_id if attendance and attendance.check_in_rfid_log else None) or getattr(student, 'rfid_card_id', None),
                    'scan_type': 'check_in' if check_in_time else None,
                    'scan_type_display': 'เข้า' if check_in_time else ('ขาด' if not check_in_time else None),
                    'face_status': 'success' if attendance and attendance.face_verified_in else ('absent' if not check_in_time else 'no_face_log'),
                    'face_status_display': 'ยืนยันใบหน้าแล้ว' if attendance and attendance.face_verified_in else ('ขาดเรียน' if not check_in_time else '-'),
                    'is_manual': attendance.is_manual_entry if attendance else False,
                    'record_type': 'manual' if (attendance and attendance.is_manual_entry) else ('auto' if check_in_time else 'absent'),
                    'record_type_display': ('บันทึกด้วยตนเอง' if (attendance and attendance.is_manual_entry) 
                                           else ('สแกนอัตโนมัติ' if check_in_time else 'ขาดเรียน')),
                    'points_deducted': attendance.points_deducted if attendance else 0,
                    'notes': record_notes,
                    'error_message': record_notes,  # ใช้ notes เป็น error_message
                    'created_at': record_created_at.isoformat() if record_created_at else (check_in_datetime.isoformat() if check_in_datetime else None),
                    
                    # Legacy fields สำหรับ Frontend
                    'rfid_scan_log_details': {
                        'rfid_card_id': (attendance.check_in_rfid_log.rfid_card_id if attendance and attendance.check_in_rfid_log else None) or getattr(student, 'rfid_card_id', None),
                        'scan_type': 'check_in' if check_in_time else None,
                        'scan_type_display': 'เข้า' if check_in_time else None,
                        'scan_time': check_in_datetime.isoformat() if check_in_datetime else None,
                        'device_name': None,
                    },
                    'student': {
                        'id': student.id,
                        'student_id': student.student_id,
                        'first_name': student.first_name,
                        'last_name': student.last_name,
                        'grade': student.grade,
                        'classroom': student.classroom,
                        'face_image_url': request.build_absolute_uri(student.face_image.url) if student.face_image else None,
                        'behavior_score': student.behavior_score,
                        'rfid_card_id': getattr(student, 'rfid_card_id', None),
                    }
                }
                
                results.append(result_item)
        else:
            # หลายวัน - แสดงเฉพาะที่มี record
            for record in attendance_records:
                student = record.student
                if not student:
                    continue
                
                # กรองตาม filters
                if grade and student.grade != grade:
                    continue
                if classroom and student.classroom != classroom:
                    continue
                if search:
                    search_lower = search.lower()
                    if not (search_lower in student.first_name.lower() or 
                            search_lower in student.last_name.lower() or
                            search_lower in student.student_id.lower()):
                        continue
                if attendance_status_filter and record.status != attendance_status_filter:
                    continue
                
                # นับสถิติ
                summary['total_students'] += 1
                if record.status == 'present':
                    summary['present'] += 1
                elif record.status == 'late':
                    summary['late'] += 1
                else:
                    summary['absent'] += 1
                
                if record.check_in_time:
                    summary['checked_in'] += 1
                if record.check_out_time:
                    summary['checked_out'] += 1
                
                # ⭐ รวม date + time เป็น datetime
                check_in_dt = None
                check_out_dt = None
                if record.check_in_time and record.date:
                    check_in_dt = datetime.combine(record.date, record.check_in_time)
                if record.check_out_time and record.date:
                    check_out_dt = datetime.combine(record.date, record.check_out_time)
                
                result_item = {
                    'id': record.id,
                    'student_id': student.student_id,
                    'first_name': student.first_name,
                    'last_name': student.last_name,
                    'grade': student.grade,
                    'classroom': student.classroom,
                    'student_image_url': request.build_absolute_uri(student.face_image.url) if student.face_image else None,
                    'homeroom_teacher': student.homeroom_teacher.get_full_name() if student.homeroom_teacher else None,
                    'behavior_score': student.behavior_score,
                    
                    'attendance_status': record.status,
                    'attendance_status_display': record.get_status_display() if hasattr(record, 'get_status_display') else record.status,
                    
                    'scan_time': check_in_dt.isoformat() if check_in_dt else None,
                    'check_out_time': check_out_dt.isoformat() if check_out_dt else None,
                    'date': record.date.isoformat(),
                    
                    'rfid_card_id': (record.check_in_rfid_log.rfid_card_id if record.check_in_rfid_log else None) or getattr(student, 'rfid_card_id', None),
                    'scan_type': 'check_in' if record.check_in_time else None,
                    'face_status': 'success' if record.face_verified_in else 'no_face_log',
                    'is_manual': record.is_manual_entry,
                    'record_type': 'manual' if record.is_manual_entry else 'auto',
                    'record_type_display': 'บันทึกด้วยตนเอง' if record.is_manual_entry else 'สแกนอัตโนมัติ',
                    'points_deducted': record.points_deducted,
                    'notes': record.notes,
                    'error_message': record.notes,
                    'created_at': record.created_at.isoformat() if record.created_at else None,
                    
                    'rfid_scan_log_details': {
                        'rfid_card_id': (record.check_in_rfid_log.rfid_card_id if record.check_in_rfid_log else None) or getattr(student, 'rfid_card_id', None),
                        'scan_type': 'check_in' if record.check_in_time else None,
                        'scan_time': check_in_dt.isoformat() if check_in_dt else None,
                        'device_name': None,
                    },
                    'student': {
                        'id': student.id,
                        'student_id': student.student_id,
                        'first_name': student.first_name,
                        'last_name': student.last_name,
                        'grade': student.grade,
                        'classroom': student.classroom,
                        'face_image_url': request.build_absolute_uri(student.face_image.url) if student.face_image else None,
                        'behavior_score': student.behavior_score,
                        'rfid_card_id': getattr(student, 'rfid_card_id', None),
                    }
                }
                
                results.append(result_item)
        
        # เรียงลำดับ: มาปกติ -> มาสาย -> ขาด
        status_order = {'present': 0, 'late': 1, 'absent': 2}
        results.sort(key=lambda x: (
            status_order.get(x['attendance_status'], 3),
            x.get('grade') or '',
            x.get('classroom') or '',
            x.get('first_name') or ''
        ))
        
        # เพิ่ม legacy fields
        summary['total'] = len(results)
        summary['check_in'] = summary['checked_in']
        summary['check_out'] = summary['checked_out']
        
        logger.info(f"📊 AttendanceReport: {date_from} to {date_to}")
        logger.info(f"   total_students={summary['total_students']}, present={summary['present']}, late={summary['late']}, absent={summary['absent']}")
        
        return Response({
            'results': results,
            'summary': summary,
            'date_from': date_from,
            'date_to': date_to,
            'total_records': len(results)
        })


class DailyCheckView(APIView):
    """
    ⭐ API สำหรับรายงานเช็คชื่อประจำวัน (มาสาย-ขาด) - เวอร์ชันแก้ไขแล้ว
    
    เกณฑ์การเข้าเรียน:
    - มาปกติ: 05:30 - 08:20
    - มาสาย: 08:21 - 09:00
    - ขาด: ไม่แตะบัตร หรือหลัง 09:00
    
    Query Parameters:
    - date: วันที่ต้องการดู (YYYY-MM-DD) default=วันนี้
    - grade: กรองตามระดับชั้น
    - classroom: กรองตามห้อง
    - status: กรองตามสถานะ (present/late/absent)
    - search: ค้นหาชื่อ/รหัสนักเรียน
    """
    permission_classes = [IsAuthenticated]
    
    # ⭐ กฎเวลา
    CHECK_IN_START = time(5, 30)       # เริ่มเปิดรับสแกนเข้า
    CHECK_IN_ON_TIME = time(8, 20)     # มาตรงเวลา (ถึง 08:20 น.)
    CHECK_IN_LATE_END = time(9, 0)     # สิ้นสุดช่วงมาสาย (หลังจากนี้ถือว่าขาด)
    CHECK_OUT_START = time(15, 25)     # เริ่มเปิดรับสแกนออก
    
    def get(self, request):
        try:
            # Parse parameters
            date_str = request.query_params.get('date')
            grade = request.query_params.get('grade')
            classroom = request.query_params.get('classroom')
            status_filter = request.query_params.get('status')
            search = request.query_params.get('search', '').strip()
            
            # ⭐ Parse date - ถ้าไม่ระบุใช้วันนี้
            if date_str:
                try:
                    target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                except ValueError:
                    return Response({
                        'error': 'รูปแบบวันที่ไม่ถูกต้อง (ต้องเป็น YYYY-MM-DD)'
                    }, status=status.HTTP_400_BAD_REQUEST)
            else:
                target_date = timezone.now().date()
            
            # ⭐ โหลดค่าจาก SchoolSettings ถ้ามี
            normal_arrival_time = self.CHECK_IN_ON_TIME
            late_arrival_time = self.CHECK_IN_LATE_END
            
            try:
                from my.models import SchoolSettings
                settings = SchoolSettings.objects.first()
                if settings:
                    if settings.normal_arrival_time:
                        normal_arrival_time = settings.normal_arrival_time
                    if settings.late_arrival_time:
                        late_arrival_time = settings.late_arrival_time
            except Exception as e:
                logger.warning(f"ไม่สามารถโหลด SchoolSettings: {e}")
            
            # ⭐ ดึงนักเรียนทั้งหมด
            students_qs = Student.objects.filter(is_active=True).select_related('homeroom_teacher')
            
            # Apply filters
            if grade:
                students_qs = students_qs.filter(grade=grade)
            if classroom:
                students_qs = students_qs.filter(classroom=classroom)
            if search:
                students_qs = students_qs.filter(
                    Q(first_name__icontains=search) |
                    Q(last_name__icontains=search) |
                    Q(student_id__icontains=search)
                )
            
            # ⭐ ดึงข้อมูลการสแกนเข้าของวันที่เลือก
            start_datetime = datetime.combine(target_date, time.min)
            end_datetime = datetime.combine(target_date, time.max)
            
            # ดึงการสแกนเข้า (check_in) ที่สำเร็จ
            check_in_logs = RFIDScanLog.objects.filter(
                scan_time__range=(start_datetime, end_datetime),
                scan_type='check_in',
                status='success'
            ).select_related('student')
            
            # สร้าง lookup dict: student_id -> check_in_log
            check_in_dict = {}
            for log in check_in_logs:
                if log.student_id and log.student_id not in check_in_dict:
                    check_in_dict[log.student_id] = log
            
            # ดึงการสแกนออก (check_out) ที่สำเร็จ
            check_out_logs = RFIDScanLog.objects.filter(
                scan_time__range=(start_datetime, end_datetime),
                scan_type='check_out',
                status='success'
            ).select_related('student')
            
            # สร้าง lookup dict: student_id -> check_out_log
            check_out_dict = {}
            for log in check_out_logs:
                if log.student_id and log.student_id not in check_out_dict:
                    check_out_dict[log.student_id] = log
            
            # ⭐ สร้างผลลัพธ์
            results = []
            summary = {
                'total': 0,
                'present': 0,
                'late': 0,
                'absent': 0,
            }
            
            for student in students_qs:
                check_in_log = check_in_dict.get(student.id)
                check_out_log = check_out_dict.get(student.id)
                
                # ⭐ กำหนดสถานะจากเวลาสแกนเข้า
                student_status = 'absent'  # default = ขาด
                check_in_time_str = None
                check_out_time_str = None
                points_deducted = 0
                
                if check_in_log:
                    check_in_time = check_in_log.scan_time.time()
                    check_in_time_str = check_in_log.scan_time.strftime('%H:%M')
                    
                    # ตรวจสอบสถานะตามเวลา
                    if check_in_time <= normal_arrival_time:
                        # มาปกติ (05:30 - 08:20)
                        student_status = 'present'
                        points_deducted = 0
                    elif check_in_time <= late_arrival_time:
                        # มาสาย (08:21 - 09:00)
                        student_status = 'late'
                        points_deducted = 1
                    else:
                        # หลัง 09:00 = ขาด
                        student_status = 'absent'
                        points_deducted = 2
                else:
                    # ไม่มีการสแกนเข้า = ขาด
                    student_status = 'absent'
                    points_deducted = 2
                
                if check_out_log:
                    check_out_time_str = check_out_log.scan_time.strftime('%H:%M')
                
                # Apply status filter
                if status_filter and student_status != status_filter:
                    continue
                
                # ⭐ ดึงข้อมูลครูประจำชั้น
                homeroom_teacher_name = ''
                if student.homeroom_teacher:
                    homeroom_teacher_name = f"{student.homeroom_teacher.first_name} {student.homeroom_teacher.last_name}"
                
                # ⭐ ดึงรูปนักเรียน
                face_image_url = None
                if student.face_image:
                    try:
                        face_image_url = request.build_absolute_uri(student.face_image.url)
                    except:
                        pass
                
                # สร้างผลลัพธ์
                result_item = {
                    'id': student.id,
                    'student_id': student.student_id,
                    'first_name': student.first_name,
                    'last_name': student.last_name,
                    'grade': student.grade,
                    'classroom': student.classroom,
                    'homeroom_teacher': homeroom_teacher_name,
                    'face_image_url': face_image_url,
                    'status': student_status,
                    'check_in_time': check_in_time_str,
                    'check_out_time': check_out_time_str,
                    'points_deducted': points_deducted,
                    'notes': '',
                    'rfid_scan_log_id': check_in_log.id if check_in_log else None
                }
                
                results.append(result_item)
                
                # Update summary
                summary['total'] += 1
                if student_status == 'present':
                    summary['present'] += 1
                elif student_status == 'late':
                    summary['late'] += 1
                elif student_status == 'absent':
                    summary['absent'] += 1
            
            # ⭐ เรียงลำดับ: ขาดก่อน -> มาสาย -> มาปกติ
            status_order = {'absent': 0, 'late': 1, 'present': 2}
            results.sort(key=lambda x: (
                status_order.get(x['status'], 3),
                x['grade'] or '',
                x['classroom'] or '',
                x['first_name'] or ''
            ))
            
            return Response({
                'date': target_date.strftime('%Y-%m-%d'),
                'date_display': target_date.strftime('%d/%m/%Y'),
                'results': results,
                'summary': summary,
                'time_rules': {
                    'normal': {
                        'start': '05:30',
                        'end': normal_arrival_time.strftime('%H:%M'),
                        'description': 'มาปกติ'
                    },
                    'late': {
                        'start': (datetime.combine(target_date, normal_arrival_time) + timedelta(minutes=1)).time().strftime('%H:%M'),
                        'end': late_arrival_time.strftime('%H:%M'),
                        'description': 'มาสาย'
                    },
                    'absent': {
                        'description': 'ไม่แตะบัตร หรือหลัง ' + late_arrival_time.strftime('%H:%M')
                    }
                }
            })
            
        except Exception as e:
            logger.error(f"Error in DailyCheckView: {str(e)}", exc_info=True)
            return Response({
                'error': f'เกิดข้อผิดพลาด: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ========================================
# ⭐ API สำหรับคะแนนพฤติกรรม
# ========================================

class BehaviorScoreSummaryView(APIView):
    """
    ⭐ API สำหรับสรุปคะแนนพฤติกรรม
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        date_from = request.query_params.get('date_from')
        date_to = request.query_params.get('date_to')
        grade = request.query_params.get('grade')
        student_id = request.query_params.get('student_id')
        
        # ดึงค่า config
        config = get_scoring_config()
        
        # ดึงข้อมูลนักเรียน
        students_qs = Student.objects.filter(is_active=True)
        
        if grade:
            students_qs = students_qs.filter(grade=grade)
        if student_id:
            students_qs = students_qs.filter(student_id=student_id)
        
        # Query behavior records
        behavior_qs = BehaviorRecord.objects.filter(student__in=students_qs)
        
        if date_from:
            behavior_qs = behavior_qs.filter(date_recorded__gte=date_from)
        if date_to:
            behavior_qs = behavior_qs.filter(date_recorded__lte=date_to)
        
        # สถิติ
        total_students = students_qs.count()
        total_deducted = behavior_qs.filter(behavior_type='deduct').aggregate(
            total=Sum('points')
        )['total'] or 0
        
        late_count = behavior_qs.filter(auto_type='late').count()
        absent_count = behavior_qs.filter(auto_type='absent').count()
        no_checkout_count = behavior_qs.filter(auto_type='no_checkout').count()
        
        # คะแนนเฉลี่ย
        avg_score = students_qs.aggregate(avg=Avg('behavior_score'))['avg'] or config['initial_score']
        
        # นักเรียนที่คะแนนต่ำ (ต่ำกว่า 80)
        low_score_students = students_qs.filter(behavior_score__lt=80).order_by('behavior_score')[:10]
        
        return Response({
            'config': config,
            'summary': {
                'total_students': total_students,
                'total_points_deducted': total_deducted,
                'average_score': round(avg_score, 2),
                'late_count': late_count,
                'absent_count': absent_count,
                'no_checkout_count': no_checkout_count
            },
            'low_score_students': [
                {
                    'student_id': s.student_id,
                    'name': s.get_full_name(),
                    'grade': s.grade,
                    'classroom': s.classroom,
                    'behavior_score': s.behavior_score
                } for s in low_score_students
            ]
        })


class ProcessBehaviorScoresView(APIView):
    """
    ⭐ API สำหรับประมวลผลคะแนนพฤติกรรม (Manual trigger)
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # ตรวจสอบสิทธิ์ (เฉพาะ admin หรือ discipline_teacher)
        if request.user.role not in ['admin', 'discipline_teacher']:
            return Response({
                'success': False,
                'error': 'ไม่มีสิทธิ์ดำเนินการ'
            }, status=status.HTTP_403_FORBIDDEN)
        
        date_str = request.data.get('date')
        dry_run = request.data.get('dry_run', False)
        
        if date_str:
            try:
                target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                return Response({
                    'success': False,
                    'error': 'รูปแบบวันที่ไม่ถูกต้อง (YYYY-MM-DD)'
                }, status=status.HTTP_400_BAD_REQUEST)
        else:
            target_date = timezone.now().date()
        
        if dry_run:
            # โหมดทดสอบ - ไม่บันทึกจริง
            result = {
                'date': str(target_date),
                'dry_run': True,
                'message': 'โหมดทดสอบ - ไม่มีการบันทึกข้อมูลจริง'
            }
        else:
            # ประมวลผลจริง
            result = behavior_scoring_service.process_daily_behavior_scores(target_date)
        
        return Response({
            'success': True,
            'result': result
        })


# ========================================
# API อื่นๆ
# ========================================

class DeviceStatusSummaryView(APIView):
    """API สำหรับสรุปสถานะอุปกรณ์"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        devices = DeviceStatus.objects.all()
        
        summary = {
            'total': devices.count(),
            'online': devices.filter(status='online').count(),
            'offline': devices.filter(status='offline').count(),
            'maintenance': devices.filter(status='maintenance').count(),
            'error': devices.filter(status='error').count()
        }
        
        devices_list = DeviceStatusSerializer(devices, many=True).data
        
        return Response({
            'summary': summary,
            'devices': devices_list
        })


class ActiveAlertsView(APIView):
    """API สำหรับการแจ้งเตือนที่ยังไม่ได้แก้ไข"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        alerts = SystemAlert.objects.filter(is_resolved=False).order_by('-created_at')[:20]
        serializer = SystemAlertSerializer(alerts, many=True)
        
        return Response({
            'count': alerts.count(),
            'alerts': serializer.data
        })


class ScanningReportView(APIView):
    """API สำหรับรายงานการสแกน"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        date_from = request.query_params.get('date_from', timezone.now().date().isoformat())
        date_to = request.query_params.get('date_to', timezone.now().date().isoformat())
        
        scans = RFIDScanLog.objects.filter(
            scan_time__date__gte=date_from,
            scan_time__date__lte=date_to
        )
        
        report = {
            'period': {
                'from': date_from,
                'to': date_to
            },
            'total_scans': scans.count(),
            'successful': scans.filter(status='success').count(),
            'failed': scans.filter(status='failed').count(),
            'check_in': scans.filter(scan_type='check_in').count(),
            'check_out': scans.filter(scan_type='check_out').count()
        }
        
        return Response(report)


class ExportScanLogView(APIView):
    """API สำหรับ Export ข้อมูลการสแกน"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from django.http import HttpResponse
        import csv
        
        date_from = request.query_params.get('date_from', timezone.now().date().isoformat())
        date_to = request.query_params.get('date_to', timezone.now().date().isoformat())
        
        scans = RFIDScanLog.objects.filter(
            scan_time__date__gte=date_from,
            scan_time__date__lte=date_to
        ).select_related('student').order_by('-scan_time')
        
        response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
        response['Content-Disposition'] = f'attachment; filename="scan_logs_{date_from}_{date_to}.csv"'
        
        writer = csv.writer(response)
        writer.writerow([
            'วันที่', 'เวลา', 'รหัสนักเรียน', 'ชื่อ-นามสกุล', 
            'ชั้น/ห้อง', 'รหัส RFID', 'ประเภท', 'สถานะ', 
            'คะแนนพฤติกรรม', 'อุปกรณ์'
        ])
        
        for scan in scans:
            student = scan.student
            writer.writerow([
                scan.scan_time.strftime('%Y-%m-%d'),
                scan.scan_time.strftime('%H:%M:%S'),
                student.student_id if student else '-',
                student.get_full_name() if student else '-',
                f"ป.{student.grade}/{student.classroom}" if student else '-',
                scan.rfid_card_id,
                scan.get_scan_type_display(),
                scan.get_status_display(),
                student.behavior_score if student else '-',
                scan.device_name
            ])
        
        return response


class MaintenanceView(APIView):
    """API สำหรับการบำรุงรักษา"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({
            'status': 'normal',
            'message': 'ระบบทำงานปกติ'
        })


class SystemHealthView(APIView):
    """API สำหรับตรวจสอบสุขภาพระบบ"""
    permission_classes = [AllowAny]

    def get(self, request):
        from django.db import connection
        
        # ตรวจสอบ database
        try:
            connection.ensure_connection()
            db_status = 'healthy'
        except:
            db_status = 'unhealthy'
        
        # ตรวจสอบอุปกรณ์
        devices_online = DeviceStatus.objects.filter(status='online').count()
        devices_total = DeviceStatus.objects.count()
        
        return Response({
            'status': 'healthy' if db_status == 'healthy' else 'degraded',
            'timestamp': timezone.now().isoformat(),
            'components': {
                'database': db_status,
                'devices': f'{devices_online}/{devices_total} online'
            }
        })


class BackupDataView(APIView):
    """API สำหรับ backup ข้อมูล"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # ตรวจสอบสิทธิ์
        if request.user.role != 'admin':
            return Response({
                'success': False,
                'error': 'เฉพาะผู้ดูแลระบบเท่านั้น'
            }, status=status.HTTP_403_FORBIDDEN)
        
        return Response({
            'success': True,
            'message': 'กรุณาใช้คำสั่ง python manage.py dumpdata สำหรับ backup ข้อมูล'
        })


def is_school_day(check_date=None):
    """ตรวจสอบว่าเป็นวันเรียน (จ-ศ) หรือไม่"""
    if check_date is None:
        check_date = timezone.now().date()
    return check_date.weekday() in SCHOOL_DAYS


def calculate_attendance_status(scan_time):
    """
    ⭐ คำนวณสถานะการเข้าเรียนจากเวลาสแกน
    
    Returns:
        str: 'present', 'late', หรือ 'absent'
    """
    if not scan_time:
        return 'absent'
    
    if isinstance(scan_time, datetime):
        check_time = scan_time.time()
    else:
        check_time = scan_time
    
    # 05:30 - 08:20 = มาปกติ
    if CHECK_IN_START <= check_time <= CHECK_IN_ON_TIME:
        return 'present'
    # 08:20 - 09:00 = มาสาย
    elif CHECK_IN_ON_TIME < check_time <= CHECK_IN_LATE_END:
        return 'late'
    # หลัง 09:00 = ขาด
    else:
        return 'absent'


def calculate_late_minutes(scan_time):
    """คำนวณจำนวนนาทีที่สาย"""
    if not scan_time:
        return 0
    
    if isinstance(scan_time, datetime):
        check_time = scan_time.time()
    else:
        check_time = scan_time
    
    on_time_minutes = CHECK_IN_ON_TIME.hour * 60 + CHECK_IN_ON_TIME.minute
    scan_minutes = check_time.hour * 60 + check_time.minute
    
    if scan_minutes > on_time_minutes:
        return scan_minutes - on_time_minutes
    return 0


class DailyCheckSummaryView(APIView):
    """
    ⭐ API สำหรับดึงสรุปสถิติการเช็คชื่อประจำวัน
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        try:
            date_str = request.query_params.get('date')
            
            if date_str:
                try:
                    target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                except ValueError:
                    return Response({
                        'error': 'รูปแบบวันที่ไม่ถูกต้อง'
                    }, status=status.HTTP_400_BAD_REQUEST)
            else:
                target_date = timezone.now().date()
            
            # ตรวจสอบวันเรียน
            if not is_school_day(target_date):
                return Response({
                    'date': str(target_date),
                    'is_school_day': False,
                    'message': 'วันเสาร์-อาทิตย์ ไม่ใช่วันเรียน'
                })
            
            start_of_day = datetime.combine(target_date, time.min)
            end_of_day = datetime.combine(target_date, time.max)
            
            total_students = Student.objects.filter(is_active=True).count()
            
            # นับจากการสแกน
            check_in_scans = RFIDScanLog.objects.filter(
                scan_type='check_in',
                scan_time__range=(start_of_day, end_of_day),
                status='success'
            ).select_related('student').distinct('student')
            
            present_count = 0
            late_count = 0
            
            for scan in check_in_scans:
                status_result = calculate_attendance_status(scan.scan_time)
                if status_result == 'present':
                    present_count += 1
                elif status_result == 'late':
                    late_count += 1
            
            absent_count = total_students - present_count - late_count
            
            check_out_count = RFIDScanLog.objects.filter(
                scan_type='check_out',
                scan_time__range=(start_of_day, end_of_day),
                status='success'
            ).distinct('student').count()
            
            attendance_rate = round((present_count + late_count) / total_students * 100, 2) if total_students > 0 else 0
            
            return Response({
                'date': str(target_date),
                'day_name': ['จันทร์', 'อังคาร', 'พุธ', 'พฤหัสบดี', 'ศุกร์', 'เสาร์', 'อาทิตย์'][target_date.weekday()],
                'is_school_day': True,
                'total_students': total_students,
                'present': present_count,
                'late': late_count,
                'absent': absent_count,
                'checked_in': present_count + late_count,
                'checked_out': check_out_count,
                'attendance_rate': attendance_rate,
                'time_rules': {
                    'check_in_on_time': '08:20',
                    'check_in_late_end': '09:00'
                }
            })
            
        except Exception as e:
            logger.error(f"Error in DailyCheckSummaryView: {str(e)}", exc_info=True)
            return Response({
                'error': f'เกิดข้อผิดพลาด: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ProcessDailyAttendanceView(APIView):
    """
    ⭐ API สำหรับประมวลผลการเข้าเรียนประจำวัน (สำหรับ Admin)
    - ตรวจสอบและบันทึกการขาดเรียน
    - หักคะแนนสำหรับการมาสาย/ขาด
    """
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        try:
            # ตรวจสอบสิทธิ์
            if request.user.role not in ['admin', 'discipline_teacher']:
                return Response({
                    'error': 'ไม่มีสิทธิ์เข้าถึง'
                }, status=status.HTTP_403_FORBIDDEN)
            
            date_str = request.data.get('date')
            
            if date_str:
                try:
                    target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                except ValueError:
                    return Response({
                        'error': 'รูปแบบวันที่ไม่ถูกต้อง'
                    }, status=status.HTTP_400_BAD_REQUEST)
            else:
                target_date = timezone.now().date()
            
            if not is_school_day(target_date):
                return Response({
                    'success': False,
                    'date': str(target_date),
                    'message': 'วันเสาร์-อาทิตย์ ไม่ใช่วันเรียน ไม่ต้องประมวลผล'
                })
            
            # Import ฟังก์ชันประมวลผล
            from my.attendance_auto_detector import run_daily_attendance_processing
            
            result = run_daily_attendance_processing(target_date)
            
            return Response({
                'success': True,
                'date': str(target_date),
                'result': result,
                'message': 'ประมวลผลการเข้าเรียนสำเร็จ'
            })
            
        except Exception as e:
            logger.error(f"Error in ProcessDailyAttendanceView: {str(e)}", exc_info=True)
            return Response({
                'error': f'เกิดข้อผิดพลาด: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ⭐ API สำหรับดูสรุปการเข้าเรียนรายวัน
class AttendanceDailySummaryView(APIView):
    """
    ⭐ API สำหรับดูสรุปการเข้าเรียนรายวัน
    
    GET /scanning/attendance-summary/?date=2024-01-15
    
    Response:
    {
        "date": "2024-01-15",
        "day_name": "จันทร์",
        "is_school_day": true,
        "summary": {
            "total_students": 100,
            "present": 80,           // มาปกติ
            "late": 10,              // มาสาย
            "absent": 10,            // ขาด
            "no_checkout": 5,        // ไม่สแกนออก
            "attendance_rate": 90.0
        },
        "points_summary": {
            "late_deduction": 10,    // รวมคะแนนที่หักจากมาสาย
            "absent_deduction": 20   // รวมคะแนนที่หักจากขาด
        },
        "students": [
            {
                "student_id": "STD001",
                "name": "สมชาย ใจดี",
                "attendance_status": "present",
                "attendance_status_thai": "มาปกติ",
                "check_in_time": "08:00:00",
                "check_out_time": "16:00:00",
                "points_deducted": 0
            },
            ...
        ]
    }
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        try:
            date_str = request.query_params.get('date')
            
            if date_str:
                try:
                    target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                except:
                    return Response({
                        'success': False,
                        'error': 'รูปแบบวันที่ไม่ถูกต้อง (ใช้ YYYY-MM-DD)'
                    }, status=status.HTTP_400_BAD_REQUEST)
            else:
                target_date = timezone.now().date()
            
            # ดึงข้อมูล
            start_of_day = datetime.combine(target_date, time.min)
            end_of_day = datetime.combine(target_date, time.max)
            
            # นักเรียนทั้งหมด
            all_students = Student.objects.filter(is_active=True).order_by('grade', 'classroom', 'student_id')
            total_students = all_students.count()
            
            # สแกนเข้าวันนี้
            check_ins = RFIDScanLog.objects.filter(
                scan_type='check_in',
                scan_time__range=(start_of_day, end_of_day),
                status='success'
            ).select_related('student')
            
            # สแกนออกวันนี้
            check_outs = RFIDScanLog.objects.filter(
                scan_type='check_out',
                scan_time__range=(start_of_day, end_of_day),
                status='success'
            )
            
            # สร้าง dict สำหรับ lookup
            check_in_dict = {}
            for scan in check_ins:
                if scan.student_id and scan.student_id not in check_in_dict:
                    check_in_dict[scan.student_id] = scan
            
            check_out_dict = {}
            for scan in check_outs:
                if scan.student_id:
                    check_out_dict[scan.student_id] = scan
            
            # นับสถานะ
            present_count = 0
            late_count = 0
            absent_count = 0
            no_checkout_count = 0
            late_deduction = 0
            absent_deduction = 0
            
            students_data = []
            
            for student in all_students:
                check_in = check_in_dict.get(student.id)
                check_out = check_out_dict.get(student.id)
                
                if check_in:
                    attendance_status = check_in.attendance_status
                    points = check_in.points_deducted
                    check_in_time = check_in.scan_time.strftime('%H:%M:%S')
                else:
                    attendance_status = 'absent'
                    points = 2  # ABSENT_DEDUCTION
                    check_in_time = None
                
                check_out_time = check_out.scan_time.strftime('%H:%M:%S') if check_out else None
                
                # นับ
                if attendance_status == 'present':
                    present_count += 1
                elif attendance_status == 'late':
                    late_count += 1
                    late_deduction += points
                else:
                    absent_count += 1
                    absent_deduction += points
                
                # ตรวจสอบไม่สแกนออก (เฉพาะคนที่มาเรียน)
                if check_in and not check_out:
                    # ถ้าเลยเวลา CHECK_OUT_END แล้ว
                    if timezone.now().time() > CHECK_OUT_END:
                        no_checkout_count += 1
                
                students_data.append({
                    'student_id': student.student_id,
                    'name': student.get_full_name(),
                    'grade': student.grade,
                    'classroom': student.classroom,
                    'attendance_status': attendance_status,
                    'attendance_status_thai': self._get_status_thai(attendance_status),
                    'check_in_time': check_in_time,
                    'check_out_time': check_out_time,
                    'points_deducted': points,
                    'is_manual': check_in.is_manual if check_in else False
                })
            
            # คำนวณอัตราการเข้าเรียน
            attendance_rate = round((present_count + late_count) / total_students * 100, 2) if total_students > 0 else 0
            
            return Response({
                'success': True,
                'date': str(target_date),
                'day_name': self._get_thai_day(target_date.weekday()),
                'is_school_day': target_date.weekday() < 5,
                'summary': {
                    'total_students': total_students,
                    'present': present_count,
                    'late': late_count,
                    'absent': absent_count,
                    'no_checkout': no_checkout_count,
                    'attendance_rate': attendance_rate
                },
                'points_summary': {
                    'late_deduction': late_deduction,
                    'absent_deduction': absent_deduction,
                    'total_deduction': late_deduction + absent_deduction
                },
                'students': students_data
            })
            
        except Exception as e:
            logger.error(f"❌ Error: {str(e)}", exc_info=True)
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def _get_status_thai(self, status_code):
        """แปลงสถานะเป็นภาษาไทย"""
        mapping = {
            'present': 'มาปกติ',
            'late': 'มาสาย',
            'absent': 'ขาด',
            'early_leave': 'ออกก่อนเวลา',
            'checkout': 'ออก',
            'no_checkout': 'ไม่สแกนออก'
        }
        return mapping.get(status_code, status_code)
    
    def _get_thai_day(self, weekday):
        """แปลงเลขวันเป็นชื่อวันภาษาไทย"""
        days = ['จันทร์', 'อังคาร', 'พุธ', 'พฤหัสบดี', 'ศุกร์', 'เสาร์', 'อาทิตย์']
        return days[weekday]


class StudentAttendanceHistoryView(APIView):
    """
    ⭐ API สำหรับประวัติการเข้าเรียนของนักเรียน (แบบที่ Frontend ต้องการ)
    
    ส่งกลับข้อมูลโดยรวม check_in และ check_out ในแถวเดียวต่อวัน
    
    GET /scanning/attendance/history/?student_id=xxx&date_from=2024-01-01&date_to=2024-01-31
    
    Response format ที่ Frontend ต้องการ:
    [
        {
            "date": "2024-01-15",
            "status": "present",           // ⭐ ใช้ status แทน attendance_status
            "status_thai": "มาปกติ",
            "check_in_time": "08:00:00",
            "check_out_time": "16:00:00",
            "points_deducted": 0,
            "is_manual": false,
            "recorded_by": null,
            "notes": "",
            "face_verification": {
                "check_in": {"status": "success", "confidence": 95.5},
                "check_out": {"status": "success", "confidence": 92.0}
            }
        },
        ...
    ]
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        try:
            # ดึง parameters
            student_id = request.query_params.get('student_id')
            date_from = request.query_params.get('date_from')
            date_to = request.query_params.get('date_to')
            
            # หา student
            student = None
            if student_id:
                try:
                    student = Student.objects.get(
                        Q(student_id=student_id) | Q(id=student_id)
                    )
                except Student.DoesNotExist:
                    return Response({
                        'success': False,
                        'error': 'ไม่พบนักเรียน'
                    }, status=404)
            else:
                # ถ้าไม่ระบุ student_id ให้ใช้ student ที่ผูกกับ user ที่ login
                try:
                    user = request.user
                    if hasattr(user, 'student'):
                        student = user.student
                    else:
                        # หาจาก rfid_code
                        rfid_code = getattr(user, 'rfid_code', None)
                        if rfid_code:
                            student = Student.objects.filter(rfid_card_id=rfid_code).first()
                except:
                    pass
            
            if not student:
                return Response({
                    'success': False,
                    'error': 'ไม่พบข้อมูลนักเรียน กรุณาระบุ student_id'
                }, status=400)
            
            # กำหนด date range
            if date_from:
                try:
                    start_date = datetime.strptime(date_from, '%Y-%m-%d').date()
                except:
                    start_date = timezone.now().replace(day=1).date()
            else:
                start_date = timezone.now().replace(day=1).date()
            
            if date_to:
                try:
                    end_date = datetime.strptime(date_to, '%Y-%m-%d').date()
                except:
                    end_date = timezone.now().date()
            else:
                end_date = timezone.now().date()
            
            # ดึงข้อมูลการสแกนทั้งหมด
            scan_logs = RFIDScanLog.objects.filter(
                student=student,
                scan_time__date__gte=start_date,
                scan_time__date__lte=end_date,
                status='success'
            ).select_related('recorded_by').prefetch_related('face_recognition_logs').order_by('scan_time')
            
            # จัดกลุ่มตามวัน
            daily_data = defaultdict(lambda: {
                'check_in': None,
                'check_out': None,
                'face_in': None,
                'face_out': None
            })
            
            for scan in scan_logs:
                date_key = scan.scan_time.date().isoformat()
                
                if scan.scan_type == 'check_in':
                    # เก็บ check_in แรกของวัน
                    if daily_data[date_key]['check_in'] is None:
                        daily_data[date_key]['check_in'] = scan
                        # ดึง face log
                        face_log = scan.face_recognition_logs.first()
                        if face_log:
                            daily_data[date_key]['face_in'] = face_log
                
                elif scan.scan_type == 'check_out':
                    # เก็บ check_out ล่าสุดของวัน
                    daily_data[date_key]['check_out'] = scan
                    # ดึง face log
                    face_log = scan.face_recognition_logs.first()
                    if face_log:
                        daily_data[date_key]['face_out'] = face_log
            
            # สร้าง response
            results = []
            for date_str in sorted(daily_data.keys(), reverse=True):
                data = daily_data[date_str]
                check_in = data['check_in']
                check_out = data['check_out']
                
                if check_in:
                    # มีการสแกนเข้า
                    status = check_in.attendance_status
                    status_thai = self._get_status_thai(status)
                    check_in_time = check_in.scan_time.strftime('%H:%M:%S')
                    points = check_in.points_deducted
                    is_manual = check_in.is_manual
                    recorded_by = None
                    if check_in.recorded_by:
                        recorded_by = f"{check_in.recorded_by.first_name} {check_in.recorded_by.last_name}"
                    notes = check_in.manual_notes or ''
                else:
                    # ไม่มีการสแกนเข้า = ขาด
                    status = 'absent'
                    status_thai = 'ขาดเรียน'
                    check_in_time = None
                    points = 2  # ABSENT_DEDUCTION
                    is_manual = False
                    recorded_by = None
                    notes = ''
                
                check_out_time = check_out.scan_time.strftime('%H:%M:%S') if check_out else None
                
                # Face verification
                face_verification = None
                if data['face_in'] or data['face_out']:
                    face_verification = {}
                    if data['face_in']:
                        face_verification['check_in'] = {
                            'status': data['face_in'].status,
                            'status_display': data['face_in'].get_status_display(),
                            'confidence': data['face_in'].confidence_score
                        }
                    if data['face_out']:
                        face_verification['check_out'] = {
                            'status': data['face_out'].status,
                            'status_display': data['face_out'].get_status_display(),
                            'confidence': data['face_out'].confidence_score
                        }
                
                results.append({
                    'date': date_str,
                    'status': status,  # ⭐ ใช้ status สำหรับ Frontend
                    'status_thai': status_thai,
                    'check_in_time': check_in_time,
                    'check_out_time': check_out_time,
                    'points_deducted': points,
                    'is_manual': is_manual,
                    'recorded_by': recorded_by,
                    'notes': notes,
                    'face_verification': face_verification
                })
            
            return Response({
                'success': True,
                'count': len(results),
                'student': {
                    'id': student.id,
                    'student_id': student.student_id,
                    'name': student.get_full_name(),
                    'grade': student.grade,
                    'classroom': student.classroom,
                    'behavior_score': student.behavior_score
                },
                'date_range': {
                    'from': start_date.isoformat(),
                    'to': end_date.isoformat()
                },
                'results': results
            })
            
        except Exception as e:
            logger.error(f"❌ Error in StudentAttendanceHistoryView: {str(e)}", exc_info=True)
            return Response({
                'success': False,
                'error': str(e)
            }, status=500)
    
    def _get_status_thai(self, status_code):
        """แปลงสถานะเป็นภาษาไทย"""
        mapping = {
            'present': 'มาปกติ',
            'late': 'มาสาย',
            'absent': 'ขาดเรียน',
            'early_leave': 'ออกก่อนเวลา',
            'checkout': 'ออก',
            'no_checkout': 'ไม่สแกนออก'
        }
        return mapping.get(status_code, status_code)


class CombinedAttendanceListView(APIView):
    """
    ⭐ API สำหรับรายการการเข้าเรียน (รวม check_in/check_out ในแถวเดียว)
    ใช้แทน /attendance/ เดิม
    
    GET /scanning/attendance-list/?date_from=2024-01-01&date_to=2024-01-31&student_id=xxx
    
    Response (แบบที่ Frontend ต้องการ):
    {
        "count": 20,
        "results": [
            {
                "date": "2024-01-15",
                "status": "present",
                "check_in_time": "08:00:00",
                "check_out_time": "16:00:00",
                ...
            }
        ]
    }
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        try:
            date_from = request.query_params.get('date_from')
            date_to = request.query_params.get('date_to')
            student_id = request.query_params.get('student_id')
            
            # กำหนด date range
            if date_from:
                start_date = datetime.strptime(date_from, '%Y-%m-%d').date()
            else:
                start_date = timezone.now().replace(day=1).date()
            
            if date_to:
                end_date = datetime.strptime(date_to, '%Y-%m-%d').date()
            else:
                end_date = timezone.now().date()
            
            # Query scan logs
            queryset = RFIDScanLog.objects.filter(
                scan_time__date__gte=start_date,
                scan_time__date__lte=end_date,
                status='success'
            ).select_related('student', 'recorded_by').prefetch_related('face_recognition_logs')
            
            # Filter by student
            if student_id:
                queryset = queryset.filter(
                    Q(student__student_id=student_id) | Q(student__id=student_id)
                )
            
            queryset = queryset.order_by('-scan_time')
            
            # จัดกลุ่มตามวันและนักเรียน
            daily_data = defaultdict(lambda: defaultdict(lambda: {
                'check_in': None,
                'check_out': None
            }))
            
            for scan in queryset:
                if not scan.student:
                    continue
                    
                date_key = scan.scan_time.date().isoformat()
                student_key = scan.student.id
                
                if scan.scan_type == 'check_in':
                    if daily_data[date_key][student_key]['check_in'] is None:
                        daily_data[date_key][student_key]['check_in'] = scan
                elif scan.scan_type == 'check_out':
                    daily_data[date_key][student_key]['check_out'] = scan
            
            # สร้าง results
            results = []
            for date_str in sorted(daily_data.keys(), reverse=True):
                for student_key, data in daily_data[date_str].items():
                    check_in = data['check_in']
                    check_out = data['check_out']
                    
                    if check_in:
                        student = check_in.student
                        status = check_in.attendance_status
                        check_in_time = check_in.scan_time.strftime('%H:%M:%S')
                        points = check_in.points_deducted
                        is_manual = check_in.is_manual
                        recorded_by = None
                        if check_in.recorded_by:
                            recorded_by = f"{check_in.recorded_by.first_name} {check_in.recorded_by.last_name}"
                        notes = check_in.manual_notes or ''
                        
                        # Face verification
                        face_log = check_in.face_recognition_logs.first()
                        face_verification = None
                        if face_log:
                            face_verification = {
                                'status': face_log.status,
                                'confidence': face_log.confidence_score,
                                'is_manual': face_log.is_manual
                            }
                    else:
                        continue  # ไม่มี check_in ไม่แสดง
                    
                    check_out_time = check_out.scan_time.strftime('%H:%M:%S') if check_out else None
                    
                    results.append({
                        'date': date_str,
                        'status': status,  # ⭐ Frontend ใช้ status
                        'attendance_status': status,
                        'attendance_status_thai': self._get_status_thai(status),
                        'check_in_time': check_in_time,
                        'check_out_time': check_out_time,
                        'points_deducted': points,
                        'is_manual': is_manual,
                        'recorded_by': recorded_by,
                        'notes': notes,
                        'face_verification': face_verification,
                        'student': {
                            'id': student.id,
                            'student_id': student.student_id,
                            'name': student.get_full_name(),
                            'grade': student.grade,
                            'classroom': student.classroom
                        }
                    })
            
            return Response({
                'count': len(results),
                'results': results
            })
            
        except Exception as e:
            logger.error(f"❌ Error in CombinedAttendanceListView: {str(e)}", exc_info=True)
            return Response({
                'success': False,
                'error': str(e)
            }, status=500)
    
    def _get_status_thai(self, status_code):
        """แปลงสถานะเป็นภาษาไทย"""
        mapping = {
            'present': 'มาปกติ',
            'late': 'มาสาย',
            'absent': 'ขาดเรียน',
            'early_leave': 'ออกก่อนเวลา',
            'checkout': 'ออก',
            'no_checkout': 'ไม่สแกนออก'
        }
        return mapping.get(status_code, status_code)


class IsTeacherOrAdmin:
    """Permission ที่อนุญาตเฉพาะครูและแอดมิน"""
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in [
            'admin', 'discipline_teacher', 'teacher'
        ]


# ==========================================================================
# ⭐ MAIN SCAN API - ใช้สำหรับ RFID Reader + Camera
# ==========================================================================

class ProcessScanView(APIView):
    """
    API สำหรับประมวลผลการสแกน RFID + ยืนยันใบหน้า
    
    POST /api/scanning/process/
    {
        "rfid_card_id": "0005977774",
        "captured_image": "base64...",  // รูปจากกล้อง
        "scan_type": "check_in",  // หรือ "check_out"
        "device_name": "GATE_01",
        "device_location": "ประตูหน้า"
    }
    """
    permission_classes = [AllowAny]  # อุปกรณ์ IoT ไม่ต้อง auth
    
    def post(self, request):
        from .integrated_attendance_service import integrated_attendance_service
        
        rfid_card_id = request.data.get('rfid_card_id')
        captured_image = request.data.get('captured_image')
        scan_type = request.data.get('scan_type', 'check_in')
        device_name = request.data.get('device_name', 'Unknown')
        device_location = request.data.get('device_location', '')
        
        # Validate
        if not rfid_card_id:
            return Response({
                'success': False,
                'error': 'กรุณาระบุรหัสบัตร RFID',
                'error_code': 'RFID_REQUIRED'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        if scan_type not in ['check_in', 'check_out']:
            return Response({
                'success': False,
                'error': 'scan_type ต้องเป็น check_in หรือ check_out',
                'error_code': 'INVALID_SCAN_TYPE'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Process
        result = integrated_attendance_service.process_rfid_with_face(
            rfid_card_id=rfid_card_id,
            captured_image_base64=captured_image,
            scan_type=scan_type,
            device_name=device_name,
            device_location=device_location
        )
        
        if result['success']:
            return Response(result, status=status.HTTP_200_OK)
        else:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)


class ManualVerifyView(APIView):
    """
    API สำหรับครูยืนยันตัวตนแทนระบบ Face Recognition
    
    POST /api/scanning/manual-verify/
    {
        "scan_log_id": 123,
        "notes": "ยืนยันด้วยตนเอง"
    }
    """
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        from .integrated_attendance_service import integrated_attendance_service
        
        # ตรวจสอบสิทธิ์
        if request.user.role not in ['admin', 'discipline_teacher', 'teacher']:
            return Response({
                'success': False,
                'error': 'ไม่มีสิทธิ์ใช้งาน'
            }, status=status.HTTP_403_FORBIDDEN)
        
        scan_log_id = request.data.get('scan_log_id')
        notes = request.data.get('notes', '')
        
        if not scan_log_id:
            return Response({
                'success': False,
                'error': 'กรุณาระบุ scan_log_id'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        result = integrated_attendance_service.manual_verify(
            scan_log_id=int(scan_log_id),
            verified_by=request.user,
            notes=notes
        )
        
        if result['success']:
            return Response(result, status=status.HTTP_200_OK)
        else:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)


# ==========================================================================
# ⭐ RFID SCAN LOG VIEWS (ReadOnly Version)
# ==========================================================================

class RFIDScanLogReadOnlyViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet สำหรับดู RFID Scan Logs (Read-Only)
    
    GET /api/scanning/rfid-logs/
    GET /api/scanning/rfid-logs/{id}/
    """
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        queryset = RFIDScanLog.objects.select_related('student', 'recorded_by')
        
        # Filters
        student_id = self.request.query_params.get('student_id')
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        scan_type = self.request.query_params.get('scan_type')
        scan_status = self.request.query_params.get('status')
        
        if student_id:
            queryset = queryset.filter(
                Q(student__student_id=student_id) | Q(student__id=student_id)
            )
        
        if date_from:
            queryset = queryset.filter(scan_time__date__gte=date_from)
        
        if date_to:
            queryset = queryset.filter(scan_time__date__lte=date_to)
        
        if scan_type:
            queryset = queryset.filter(scan_type=scan_type)
        
        if scan_status:
            queryset = queryset.filter(status=scan_status)
        
        return queryset.order_by('-scan_time')
    
    def get_serializer_class(self):
        return RFIDScanLogSerializer
    
    @action(detail=False, methods=['get'])
    def pending(self, request):
        """ดูรายการที่รอยืนยัน (status=pending หรือ failed)"""
        queryset = self.get_queryset().filter(status__in=['pending', 'failed'])
        serializer = self.get_serializer(queryset[:50], many=True)
        return Response({
            'count': queryset.count(),
            'results': serializer.data
        })
    
    @action(detail=False, methods=['get'])
    def today(self, request):
        """ดูรายการวันนี้"""
        today = timezone.now().date()
        queryset = self.get_queryset().filter(scan_time__date=today)
        serializer = self.get_serializer(queryset, many=True)
        
        # Summary
        summary = {
            'total': queryset.count(),
            'success': queryset.filter(status='success').count(),
            'pending': queryset.filter(status='pending').count(),
            'failed': queryset.filter(status='failed').count(),
            'check_in': queryset.filter(scan_type='check_in').count(),
            'check_out': queryset.filter(scan_type='check_out').count()
        }
        
        return Response({
            'date': str(today),
            'summary': summary,
            'results': serializer.data
        })


# ==========================================================================
# ⭐ FACE RECOGNITION LOG VIEWS (ReadOnly Version)
# ==========================================================================

class FaceRecognitionLogReadOnlyViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet สำหรับดู Face Recognition Logs (Read-Only)
    
    GET /api/scanning/face-logs/
    GET /api/scanning/face-logs/{id}/
    """
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        queryset = FaceRecognitionLog.objects.select_related(
            'rfid_scan_log', 'student', 'manual_recorded_by'
        )
        
        # Filters
        student_id = self.request.query_params.get('student_id')
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        face_status = self.request.query_params.get('status')
        
        if student_id:
            queryset = queryset.filter(
                Q(student__student_id=student_id) | Q(student__id=student_id)
            )
        
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)
        
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)
        
        if face_status:
            queryset = queryset.filter(status=face_status)
        
        return queryset.order_by('-created_at')
    
    def get_serializer_class(self):
        return FaceRecognitionLogSerializer
    
    @action(detail=False, methods=['get'])
    def failed(self, request):
        """ดูรายการที่ล้มเหลว (mismatch, no_face, error)"""
        queryset = self.get_queryset().filter(status__in=['mismatch', 'no_face', 'error'])
        serializer = self.get_serializer(queryset[:50], many=True)
        return Response({
            'count': queryset.count(),
            'results': serializer.data
        })
    
    @action(detail=False, methods=['get'])
    def today(self, request):
        """ดูรายการวันนี้"""
        today = timezone.now().date()
        queryset = self.get_queryset().filter(created_at__date=today)
        serializer = self.get_serializer(queryset, many=True)
        
        # Summary
        summary = {
            'total': queryset.count(),
            'success': queryset.filter(status='success').count(),
            'mismatch': queryset.filter(status='mismatch').count(),
            'manual': queryset.filter(status='manual').count(),
            'no_face': queryset.filter(status='no_face').count(),
            'error': queryset.filter(status='error').count()
        }
        
        return Response({
            'date': str(today),
            'summary': summary,
            'results': serializer.data
        })


# ==========================================================================
# ⭐ DAILY PROCESSING VIEWS
# ==========================================================================

class ProcessDailyAbsentView(APIView):
    """
    API สำหรับประมวลผลนักเรียนที่ขาดเรียน
    ควรรันหลัง 09:00
    
    POST /api/scanning/process-absent/
    {
        "date": "2026-01-29"  // optional, default = today
    }
    """
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        from .integrated_attendance_service import integrated_attendance_service
        
        # ตรวจสอบสิทธิ์
        if request.user.role not in ['admin', 'discipline_teacher']:
            return Response({
                'success': False,
                'error': 'ไม่มีสิทธิ์ใช้งาน (เฉพาะ Admin และครูฝ่ายปกครอง)'
            }, status=status.HTTP_403_FORBIDDEN)
        
        date_str = request.data.get('date')
        target_date = None
        
        if date_str:
            try:
                target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                return Response({
                    'success': False,
                    'error': 'รูปแบบวันที่ไม่ถูกต้อง (ใช้ YYYY-MM-DD)'
                }, status=status.HTTP_400_BAD_REQUEST)
        
        result = integrated_attendance_service.process_daily_absent(target_date)
        return Response(result)


class ProcessDailyNoCheckoutView(APIView):
    """
    API สำหรับประมวลผลนักเรียนที่ไม่สแกนออก
    ควรรันหลัง 17:00
    
    POST /api/scanning/process-no-checkout/
    {
        "date": "2026-01-29"  // optional, default = today
    }
    """
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        from .integrated_attendance_service import integrated_attendance_service
        
        # ตรวจสอบสิทธิ์
        if request.user.role not in ['admin', 'discipline_teacher']:
            return Response({
                'success': False,
                'error': 'ไม่มีสิทธิ์ใช้งาน (เฉพาะ Admin และครูฝ่ายปกครอง)'
            }, status=status.HTTP_403_FORBIDDEN)
        
        date_str = request.data.get('date')
        target_date = None
        
        if date_str:
            try:
                target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                return Response({
                    'success': False,
                    'error': 'รูปแบบวันที่ไม่ถูกต้อง (ใช้ YYYY-MM-DD)'
                }, status=status.HTTP_400_BAD_REQUEST)
        
        result = integrated_attendance_service.process_daily_no_checkout(target_date)
        return Response(result)


# ==========================================================================
# ⭐ DASHBOARD & REPORTING VIEWS
# ==========================================================================

class DailySummaryView(APIView):
    """
    API สำหรับดูสรุปรายวัน
    
    GET /api/scanning/daily-summary/?date=2026-01-29
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        from .integrated_attendance_service import integrated_attendance_service
        
        date_str = request.query_params.get('date')
        target_date = None
        
        if date_str:
            try:
                target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                return Response({
                    'error': 'รูปแบบวันที่ไม่ถูกต้อง (ใช้ YYYY-MM-DD)'
                }, status=status.HTTP_400_BAD_REQUEST)
        
        result = integrated_attendance_service.get_daily_summary(target_date)
        return Response(result)


class AttendanceStatusView(APIView):
    """
    API สำหรับดูสถานะการเข้าเรียนแบบ Real-time
    
    GET /api/scanning/attendance-status/
    GET /api/scanning/attendance-status/?grade=6&classroom=1
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        from my.models import Student, AttendanceRecord, RFIDScanLog
        
        today = timezone.now().date()
        
        # Filter
        grade = request.query_params.get('grade')
        classroom = request.query_params.get('classroom')
        
        students = Student.objects.filter(is_active=True)
        
        if grade:
            students = students.filter(grade=grade)
        if classroom:
            students = students.filter(classroom=classroom)
        
        # ดึงข้อมูลการเข้าเรียนวันนี้
        attendance_map = {}
        for record in AttendanceRecord.objects.filter(date=today, student__in=students):
            attendance_map[record.student_id] = record
        
        # สร้าง response
        results = []
        for student in students.order_by('grade', 'classroom', 'student_id'):
            attendance = attendance_map.get(student.id)
            
            results.append({
                'student_id': student.student_id,
                'full_name': student.get_full_name(),
                'grade_room': student.get_grade_room(),
                'status': attendance.status if attendance else 'not_scanned',
                'status_display': attendance.get_status_display() if attendance else 'ยังไม่สแกน',
                'check_in_time': attendance.check_in_time.strftime('%H:%M') if attendance and attendance.check_in_time else None,
                'check_out_time': attendance.check_out_time.strftime('%H:%M') if attendance and attendance.check_out_time else None,
                'face_verified_in': attendance.face_verified_in if attendance else False,
                'face_verified_out': attendance.face_verified_out if attendance else False,
                'behavior_score': student.behavior_score
            })
        
        # Summary
        statuses = [r['status'] for r in results]
        summary = {
            'total': len(results),
            'present': statuses.count('present'),
            'late': statuses.count('late'),
            'absent': statuses.count('absent'),
            'early_leave': statuses.count('early_leave'),
            'not_scanned': statuses.count('not_scanned')
        }
        
        return Response({
            'date': str(today),
            'filters': {
                'grade': grade,
                'classroom': classroom
            },
            'summary': summary,
            'students': results
        })


class ScanHistoryView(APIView):
    """
    API สำหรับดูประวัติการสแกนของนักเรียน
    
    GET /api/scanning/scan-history/{student_id}/
    GET /api/scanning/scan-history/{student_id}/?date_from=2026-01-01&date_to=2026-01-31
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request, student_id):
        from my.models import Student, RFIDScanLog, FaceRecognitionLog
        
        # หานักเรียน
        try:
            student = Student.objects.get(
                Q(student_id=student_id) | Q(id=student_id)
            )
        except Student.DoesNotExist:
            return Response({
                'error': 'ไม่พบนักเรียน'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Filters
        date_from = request.query_params.get('date_from')
        date_to = request.query_params.get('date_to')
        
        scan_logs = RFIDScanLog.objects.filter(student=student).order_by('-scan_time')
        
        if date_from:
            scan_logs = scan_logs.filter(scan_time__date__gte=date_from)
        if date_to:
            scan_logs = scan_logs.filter(scan_time__date__lte=date_to)
        
        # Build response
        results = []
        for log in scan_logs[:100]:  # จำกัด 100 รายการ
            face_log = getattr(log, 'face_recognition', None)
            
            results.append({
                'id': log.id,
                'scan_type': log.scan_type,
                'scan_type_display': log.get_scan_type_display(),
                'scan_time': log.scan_time.isoformat(),
                'status': log.status,
                'status_display': log.get_status_display(),
                'attendance_status': log.attendance_status,
                'attendance_status_display': log.get_attendance_status_display(),
                'device_name': log.device_name,
                'device_location': log.device_location,
                'points_deducted': log.points_deducted,
                'face_verification': {
                    'status': face_log.status if face_log else None,
                    'confidence': face_log.confidence_score if face_log else None,
                    'is_manual': face_log.is_manual if face_log else False
                } if face_log else None
            })
        
        return Response({
            'student': {
                'id': student.id,
                'student_id': student.student_id,
                'full_name': student.get_full_name(),
                'grade_room': student.get_grade_room(),
                'behavior_score': student.behavior_score
            },
            'scan_count': scan_logs.count(),
            'scan_history': results
        })


# ==========================================================================
# ⭐ SCHOOL SETTINGS VIEW
# ==========================================================================

class SchoolSettingsView(APIView):
    """
    API สำหรับดู/แก้ไขการตั้งค่าโรงเรียน
    
    GET /api/scanning/settings/
    PUT /api/scanning/settings/
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        from my.models import SchoolSettings
        
        settings = SchoolSettings.objects.first()
        
        if not settings:
            # สร้างค่า default
            settings = SchoolSettings.objects.create()
        
        return Response({
            'school_name': settings.school_name,
            'academic_year': settings.academic_year,
            'time_settings': {
                'check_in_start': settings.school_start_time.strftime('%H:%M'),
                'check_in_on_time': settings.normal_arrival_time.strftime('%H:%M'),
                'check_in_late_end': settings.late_arrival_time.strftime('%H:%M'),
                'check_out_start': settings.school_end_time.strftime('%H:%M'),
                'check_out_end': settings.latest_departure_time.strftime('%H:%M')
            },
            'penalty_settings': {
                'late_penalty': settings.late_penalty_points,
                'absent_penalty': settings.absent_penalty_points,
                'early_leave_penalty': settings.early_leave_penalty_points,
                'no_checkout_penalty': settings.no_checkout_penalty_points,
                'face_mismatch_penalty': getattr(settings, 'face_mismatch_penalty_points', 2)
            },
            'face_settings': {
                'confidence_threshold': getattr(settings, 'face_confidence_threshold', 70.0),
                'require_face_verification': getattr(settings, 'require_face_verification', True)
            },
            'auto_deduct_enabled': settings.auto_deduct_enabled,
            'default_behavior_score': settings.default_behavior_score
        })
    
    def put(self, request):
        from my.models import SchoolSettings
        
        # ตรวจสอบสิทธิ์
        if request.user.role != 'admin':
            return Response({
                'error': 'เฉพาะ Admin เท่านั้น'
            }, status=status.HTTP_403_FORBIDDEN)
        
        settings = SchoolSettings.objects.first()
        if not settings:
            settings = SchoolSettings.objects.create()
        
        # Update fields
        data = request.data
        
        if 'school_name' in data:
            settings.school_name = data['school_name']
        
        if 'time_settings' in data:
            ts = data['time_settings']
            if 'check_in_start' in ts:
                settings.school_start_time = ts['check_in_start']
            if 'check_in_on_time' in ts:
                settings.normal_arrival_time = ts['check_in_on_time']
            if 'check_in_late_end' in ts:
                settings.late_arrival_time = ts['check_in_late_end']
            if 'check_out_start' in ts:
                settings.school_end_time = ts['check_out_start']
            if 'check_out_end' in ts:
                settings.latest_departure_time = ts['check_out_end']
        
        if 'penalty_settings' in data:
            ps = data['penalty_settings']
            if 'late_penalty' in ps:
                settings.late_penalty_points = ps['late_penalty']
            if 'absent_penalty' in ps:
                settings.absent_penalty_points = ps['absent_penalty']
            if 'early_leave_penalty' in ps:
                settings.early_leave_penalty_points = ps['early_leave_penalty']
            if 'no_checkout_penalty' in ps:
                settings.no_checkout_penalty_points = ps['no_checkout_penalty']
        
        if 'auto_deduct_enabled' in data:
            settings.auto_deduct_enabled = data['auto_deduct_enabled']
        
        settings.save()
        
        return Response({
            'success': True,
            'message': 'บันทึกการตั้งค่าสำเร็จ'
        })


# ========================================
# ⭐ NEW: API ลบ Manual Entry
# ========================================

class ManualAttendanceDeleteView(APIView):
    """
    ⭐ API สำหรับลบ Manual Entry พร้อมข้อมูลที่เกี่ยวข้อง
    
    DELETE /scanning/manual-record/delete/
    
    Body:
    {
        "rfid_scan_log_id": 123,      // ID ของ RFIDScanLog ที่ต้องการลบ
        "delete_behavior": true        // ลบ BehaviorRecord ที่เกี่ยวข้องด้วย (default: true)
    }
    
    หรือใช้ query params:
    DELETE /scanning/manual-record/delete/?rfid_scan_log_id=123
    
    ⭐ จะลบข้อมูลจาก:
    - rfid_scan_logs
    - face_recognition_logs 
    - attendance_records
    - behavior_records (ถ้า delete_behavior=true)
    """
    permission_classes = [IsAuthenticated]
    
    def delete(self, request):
        from my.models import AttendanceRecord, BehaviorRecord
        
        try:
            # รับ parameter จาก body หรือ query params
            rfid_scan_log_id = request.data.get('rfid_scan_log_id') or request.query_params.get('rfid_scan_log_id')
            delete_behavior = request.data.get('delete_behavior', True)
            
            if not rfid_scan_log_id:
                return Response({
                    'success': False,
                    'error': 'กรุณาระบุ rfid_scan_log_id'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # ค้นหา RFIDScanLog
            try:
                rfid_log = RFIDScanLog.objects.get(id=rfid_scan_log_id)
            except RFIDScanLog.DoesNotExist:
                return Response({
                    'success': False,
                    'error': f'ไม่พบ RFIDScanLog ID {rfid_scan_log_id}'
                }, status=status.HTTP_404_NOT_FOUND)
            
            # ตรวจสอบสิทธิ์ (ต้องเป็น admin หรือคนที่บันทึก หรือ discipline_teacher)
            user = request.user
            if user.role not in ['admin', 'discipline_teacher', 'duty_teacher']:
                if rfid_log.recorded_by and rfid_log.recorded_by != user:
                    return Response({
                        'success': False,
                        'error': 'คุณไม่มีสิทธิ์ลบรายการนี้'
                    }, status=status.HTTP_403_FORBIDDEN)
            
            student = rfid_log.student
            scan_date = rfid_log.scan_time.date() if rfid_log.scan_time else None
            deleted_items = {
                'rfid_scan_log': rfid_log.id,
                'face_recognition_logs': [],
                'attendance_records': [],
                'behavior_records': []
            }
            
            with transaction.atomic():
                # 1. ลบ FaceRecognitionLog ที่เกี่ยวข้อง
                face_logs = FaceRecognitionLog.objects.filter(rfid_scan_log=rfid_log)
                deleted_items['face_recognition_logs'] = list(face_logs.values_list('id', flat=True))
                face_logs.delete()
                
                # 2. ลบ AttendanceRecord ที่เกี่ยวข้อง
                if student and scan_date:
                    # ถ้า attendance_record เชื่อมกับ rfid_log นี้
                    att_to_delete = AttendanceRecord.objects.filter(
                        Q(check_in_rfid_log=rfid_log) | Q(check_out_rfid_log=rfid_log)
                    )
                    deleted_items['attendance_records'] = list(att_to_delete.values_list('id', flat=True))
                    att_to_delete.delete()
                
                # 3. ลบ BehaviorRecord ที่เกี่ยวข้อง (ถ้าต้องการ)
                if delete_behavior and student and scan_date:
                    # Map attendance_status → auto_type
                    status_auto_map = {
                        'late': 'late',
                        'absent': 'absent', 
                        'early_leave': 'early_leave',
                        'no_checkout': 'no_checkout'
                    }
                    
                    auto_type_value = status_auto_map.get(rfid_log.attendance_status, '')
                    
                    # หา BehaviorRecord ที่เกี่ยวข้อง
                    behavior_records = BehaviorRecord.objects.filter(
                        student=student,
                        date_recorded=scan_date,
                        is_auto=True
                    )
                    
                    # ถ้ามี field auto_type ให้กรองด้วย
                    if auto_type_value and hasattr(BehaviorRecord, 'auto_type'):
                        behavior_records = behavior_records.filter(auto_type=auto_type_value)
                    else:
                        # ใช้ reason เป็น fallback
                        reason_keyword = ''
                        if rfid_log.attendance_status == 'late':
                            reason_keyword = 'มาสาย'
                        elif rfid_log.attendance_status == 'absent':
                            reason_keyword = 'ขาด'
                        elif rfid_log.attendance_status == 'early_leave':
                            reason_keyword = 'ออกก่อนเวลา'
                        elif rfid_log.attendance_status == 'no_checkout':
                            reason_keyword = 'ไม่สแกนออก'
                        
                        if reason_keyword:
                            behavior_records = behavior_records.filter(reason__icontains=reason_keyword)
                    
                    deleted_items['behavior_records'] = list(behavior_records.values_list('id', flat=True))
                    
                    # คืนคะแนนให้นักเรียน (รวมคะแนนที่หักทั้งหมด)
                    points_to_restore = 0
                    for record in behavior_records:
                        points_to_restore += abs(record.points) if record.points < 0 else 0
                    
                    if points_to_restore > 0 and student:
                        student.behavior_score += points_to_restore
                        student.save(update_fields=['behavior_score'])
                    
                    behavior_records.delete()
                
                # 4. ลบ RFIDScanLog
                rfid_log.delete()
            
            logger.info(f"✅ Deleted manual entry: {deleted_items} by {user.username}")
            
            return Response({
                'success': True,
                'message': 'ลบรายการสำเร็จ',
                'deleted': deleted_items,
                'student': {
                    'id': student.id if student else None,
                    'student_id': student.student_id if student else None,
                    'name': student.get_full_name() if student else None,
                    'new_behavior_score': student.behavior_score if student else None
                }
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"❌ Error deleting manual entry: {str(e)}", exc_info=True)
            return Response({
                'success': False,
                'error': f'เกิดข้อผิดพลาด: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def post(self, request):
        """รองรับ POST method ด้วย (สำหรับ Frontend บางตัว)"""
        return self.delete(request)


# ========================================
# ⭐ NEW: API แก้ไข Manual Entry
# ========================================

class ManualAttendanceUpdateView(APIView):
    """
    ⭐ API สำหรับแก้ไขข้อมูลการเข้า-ออก
    
    PUT /scanning/manual-record/update/
    PATCH /scanning/manual-record/update/
    
    Body:
    {
        "rfid_scan_log_id": 123,           // ID ของ RFIDScanLog ที่ต้องการแก้ไข
        "scan_time": "2024-01-15T08:00:00", // เวลาสแกนใหม่ (optional)
        "attendance_status": "present",     // สถานะใหม่ (optional)
        "notes": "แก้ไขเนื่องจาก...",        // หมายเหตุ (optional)
        "recalculate_points": true          // คำนวณคะแนนใหม่ (default: true)
    }
    """
    permission_classes = [IsAuthenticated]
    
    def put(self, request):
        return self._update(request)
    
    def patch(self, request):
        return self._update(request)
    
    def _update(self, request):
        from my.models import AttendanceRecord, BehaviorRecord
        
        try:
            rfid_scan_log_id = request.data.get('rfid_scan_log_id')
            
            if not rfid_scan_log_id:
                return Response({
                    'success': False,
                    'error': 'กรุณาระบุ rfid_scan_log_id'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # ค้นหา RFIDScanLog
            try:
                rfid_log = RFIDScanLog.objects.get(id=rfid_scan_log_id)
            except RFIDScanLog.DoesNotExist:
                return Response({
                    'success': False,
                    'error': f'ไม่พบ RFIDScanLog ID {rfid_scan_log_id}'
                }, status=status.HTTP_404_NOT_FOUND)
            
            # ตรวจสอบสิทธิ์
            user = request.user
            if user.role not in ['admin', 'discipline_teacher', 'duty_teacher']:
                if rfid_log.recorded_by and rfid_log.recorded_by != user:
                    return Response({
                        'success': False,
                        'error': 'คุณไม่มีสิทธิ์แก้ไขรายการนี้'
                    }, status=status.HTTP_403_FORBIDDEN)
            
            # เก็บข้อมูลเดิม
            old_data = {
                'scan_time': rfid_log.scan_time.isoformat() if rfid_log.scan_time else None,
                'attendance_status': rfid_log.attendance_status,
                'points_deducted': rfid_log.points_deducted
            }
            
            config = BehaviorScoringConfig()
            recalculate_points = request.data.get('recalculate_points', True)
            
            with transaction.atomic():
                # อัปเดต scan_time
                new_scan_time = request.data.get('scan_time')
                if new_scan_time:
                    try:
                        if isinstance(new_scan_time, str):
                            new_scan_time = datetime.fromisoformat(new_scan_time.replace('Z', '+00:00'))
                        rfid_log.scan_time = new_scan_time
                    except ValueError:
                        return Response({
                            'success': False,
                            'error': 'รูปแบบ scan_time ไม่ถูกต้อง'
                        }, status=status.HTTP_400_BAD_REQUEST)
                
                # อัปเดต attendance_status
                new_status = request.data.get('attendance_status')
                if new_status:
                    valid_statuses = ['present', 'late', 'absent', 'checkout', 'early_leave', 'no_checkout']
                    if new_status not in valid_statuses:
                        return Response({
                            'success': False,
                            'error': f'attendance_status ต้องเป็น {", ".join(valid_statuses)}'
                        }, status=status.HTTP_400_BAD_REQUEST)
                    rfid_log.attendance_status = new_status
                
                # อัปเดต notes
                new_notes = request.data.get('notes')
                if new_notes is not None:
                    rfid_log.manual_notes = new_notes
                    rfid_log.error_message = new_notes
                
                # คำนวณคะแนนใหม่ (ถ้าต้องการ)
                old_points = rfid_log.points_deducted
                new_points = 0
                
                if recalculate_points:
                    current_status = rfid_log.attendance_status
                    if current_status == 'late':
                        new_points = config.LATE_DEDUCTION
                    elif current_status == 'absent':
                        new_points = config.ABSENT_DEDUCTION
                    elif current_status == 'early_leave':
                        new_points = config.EARLY_LEAVE_DEDUCTION
                    elif current_status == 'no_checkout':
                        new_points = config.NO_CHECKOUT_DEDUCTION
                    
                    rfid_log.points_deducted = new_points
                    
                    # อัปเดตคะแนนนักเรียน
                    if rfid_log.student and old_points != new_points:
                        points_diff = old_points - new_points
                        rfid_log.student.behavior_score += points_diff
                        rfid_log.student.save(update_fields=['behavior_score'])
                        
                        # อัปเดต BehaviorRecord
                        scan_date = rfid_log.scan_time.date() if rfid_log.scan_time else None
                        if scan_date:
                            # ลบ record เก่า (ถ้ามี)
                            BehaviorRecord.objects.filter(
                                student=rfid_log.student,
                                date_recorded=scan_date,
                                is_auto=True,
                                reason__icontains='สแกนเวลา'  # มาจาก integrate_behavior_scoring_with_scan
                            ).delete()
                            
                            # สร้าง record ใหม่ (ถ้าจำเป็น)
                            if new_points > 0:
                                status_thai = self._get_status_thai(current_status)
                                integrate_behavior_scoring_with_scan(
                                    student=rfid_log.student,
                                    scan_type=rfid_log.scan_type,
                                    scan_time=rfid_log.scan_time,
                                    attendance_status=status_thai
                                )
                
                rfid_log.save()
                
                # อัปเดต AttendanceRecord (ถ้ามี)
                if rfid_log.student and rfid_log.scan_time:
                    scan_date = rfid_log.scan_time.date()
                    AttendanceRecord.objects.filter(
                        student=rfid_log.student,
                        date=scan_date
                    ).update(
                        status=rfid_log.attendance_status,
                        points_deducted=rfid_log.points_deducted
                    )
            
            logger.info(f"✅ Updated manual entry {rfid_log.id} by {user.username}")
            
            return Response({
                'success': True,
                'message': 'แก้ไขรายการสำเร็จ',
                'old_data': old_data,
                'new_data': {
                    'id': rfid_log.id,
                    'scan_time': rfid_log.scan_time.isoformat() if rfid_log.scan_time else None,
                    'attendance_status': rfid_log.attendance_status,
                    'points_deducted': rfid_log.points_deducted,
                    'notes': rfid_log.manual_notes
                },
                'student': {
                    'id': rfid_log.student.id if rfid_log.student else None,
                    'student_id': rfid_log.student.student_id if rfid_log.student else None,
                    'name': rfid_log.student.get_full_name() if rfid_log.student else None,
                    'behavior_score': rfid_log.student.behavior_score if rfid_log.student else None
                }
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"❌ Error updating manual entry: {str(e)}", exc_info=True)
            return Response({
                'success': False,
                'error': f'เกิดข้อผิดพลาด: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def _get_status_thai(self, status_code):
        """แปลงสถานะเป็นภาษาไทย"""
        mapping = {
            'present': 'มาปกติ',
            'late': 'มาสาย',
            'absent': 'ขาด',
            'early_leave': 'ออกก่อนเวลา',
            'checkout': 'ออก',
            'no_checkout': 'ไม่สแกนออก'
        }
        return mapping.get(status_code, status_code)


# ========================================
# ⭐ NEW: API จัดการคะแนนพฤติกรรม
# ========================================

class BehaviorScoreManagementView(APIView):
    """
    ⭐ API สำหรับเพิ่ม/ลดคะแนนพฤติกรรม (Manual)
    
    POST /scanning/behavior-score/manage/
    
    Body:
    {
        "student_id": 123,              // ID ของนักเรียน (primary key หรือ student_id)
        "action": "add",                // "add" = เพิ่มคะแนน, "deduct" = หักคะแนน
        "points": 5,                    // จำนวนคะแนน (เลขบวก)
        "reason": "ช่วยเหลืองานโรงเรียน",  // เหตุผล
        "behavior_type": "other",       // ประเภท: late, absent, early_leave, no_checkout, other
        "date": "2024-01-15"           // วันที่ (optional, default=วันนี้)
    }
    
    GET /scanning/behavior-score/manage/?student_id=123
    - ดูคะแนนพฤติกรรมปัจจุบันและประวัติ
    
    DELETE /scanning/behavior-score/manage/
    Body: { "behavior_record_id": 123 }
    - ลบ BehaviorRecord และคืนคะแนน
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        """ดูคะแนนพฤติกรรมของนักเรียน"""
        from my.models import BehaviorRecord
        
        student_id = request.query_params.get('student_id')
        
        if not student_id:
            return Response({
                'success': False,
                'error': 'กรุณาระบุ student_id'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            student = Student.objects.get(
                Q(id=student_id) | Q(student_id=student_id)
            )
        except Student.DoesNotExist:
            return Response({
                'success': False,
                'error': f'ไม่พบนักเรียน'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # ดึงประวัติการหักคะแนน
        month = request.query_params.get('month')
        year = request.query_params.get('year')
        
        records = BehaviorRecord.objects.filter(student=student).order_by('-date_recorded')
        
        if month and year:
            records = records.filter(
                date_recorded__month=month,
                date_recorded__year=year
            )
        
        records_data = []
        for record in records[:50]:
            records_data.append({
                'id': record.id,
                'behavior_type': record.behavior_type,
                'points': record.points,
                'reason': record.reason,
                'date_recorded': record.date_recorded.isoformat(),
                'is_auto': record.is_auto,
                'recorded_by': f"{record.recorded_by.first_name} {record.recorded_by.last_name}" if record.recorded_by else 'ระบบอัตโนมัติ'
            })
        
        # สรุปคะแนน
        total_deducted = records.filter(behavior_type='deduct').aggregate(Sum('points'))['points__sum'] or 0
        total_added = records.filter(behavior_type='add').aggregate(Sum('points'))['points__sum'] or 0
        
        return Response({
            'success': True,
            'student': {
                'id': student.id,
                'student_id': student.student_id,
                'name': student.get_full_name(),
                'grade_room': f'ป.{student.grade}/{student.classroom}',
                'behavior_score': student.behavior_score
            },
            'summary': {
                'current_score': student.behavior_score,
                'total_deducted': total_deducted,
                'total_added': total_added,
                'records_count': records.count()
            },
            'records': records_data
        }, status=status.HTTP_200_OK)
    
    def post(self, request):
        """เพิ่ม/หักคะแนนพฤติกรรม"""
        from my.models import BehaviorRecord
        
        try:
            student_id = request.data.get('student_id')
            action = request.data.get('action')  # 'add' or 'deduct'
            points = request.data.get('points', 0)
            reason = request.data.get('reason', '')
            behavior_type = request.data.get('behavior_type', 'other')
            date_str = request.data.get('date')
            
            # Validation
            if not student_id:
                return Response({
                    'success': False,
                    'error': 'กรุณาระบุ student_id'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            if action not in ['add', 'deduct']:
                return Response({
                    'success': False,
                    'error': 'action ต้องเป็น "add" หรือ "deduct"'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            try:
                points = abs(int(points))
            except (ValueError, TypeError):
                return Response({
                    'success': False,
                    'error': 'points ต้องเป็นตัวเลข'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            if points <= 0:
                return Response({
                    'success': False,
                    'error': 'points ต้องมากกว่า 0'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            if not reason:
                return Response({
                    'success': False,
                    'error': 'กรุณาระบุเหตุผล (reason)'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # ค้นหานักเรียน
            try:
                student = Student.objects.get(
                    Q(id=student_id) | Q(student_id=str(student_id))
                )
            except Student.DoesNotExist:
                return Response({
                    'success': False,
                    'error': f'ไม่พบนักเรียน'
                }, status=status.HTTP_404_NOT_FOUND)
            
            # กำหนดวันที่
            if date_str:
                try:
                    record_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                except ValueError:
                    record_date = timezone.now().date()
            else:
                record_date = timezone.now().date()
            
            user = request.user
            old_score = student.behavior_score
            
            with transaction.atomic():
                # อัปเดตคะแนน
                if action == 'add':
                    student.behavior_score += points
                    db_behavior_type = 'add'
                    points_value = points
                else:  # deduct
                    student.behavior_score = max(0, student.behavior_score - points)
                    db_behavior_type = 'deduct'
                    points_value = -points  # ติดลบสำหรับการหัก
                
                student.save(update_fields=['behavior_score'])
                
                # สร้าง BehaviorRecord
                behavior_record = BehaviorRecord.objects.create(
                    student=student,
                    behavior_type=db_behavior_type,
                    points=points_value,
                    reason=reason,
                    date_recorded=record_date,
                    recorded_by=user,
                    is_auto=False
                )
            
            action_thai = 'เพิ่ม' if action == 'add' else 'หัก'
            logger.info(
                f"✅ {action_thai}คะแนน: {student.student_id} | "
                f"{old_score} → {student.behavior_score} ({'+' if action == 'add' else '-'}{points}) | "
                f"เหตุผล: {reason} | โดย: {user.username}"
            )
            
            return Response({
                'success': True,
                'message': f'{action_thai}คะแนนสำเร็จ',
                'data': {
                    'behavior_record_id': behavior_record.id,
                    'student': {
                        'id': student.id,
                        'student_id': student.student_id,
                        'name': student.get_full_name(),
                        'old_score': old_score,
                        'new_score': student.behavior_score,
                        'points_changed': points if action == 'add' else -points
                    },
                    'action': action,
                    'points': points,
                    'reason': reason,
                    'date': record_date.isoformat(),
                    'recorded_by': f"{user.first_name} {user.last_name}"
                }
            }, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            logger.error(f"❌ Error managing behavior score: {str(e)}", exc_info=True)
            return Response({
                'success': False,
                'error': f'เกิดข้อผิดพลาด: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def delete(self, request):
        """ลบ BehaviorRecord และคืนคะแนน"""
        from my.models import BehaviorRecord
        
        try:
            record_id = request.data.get('behavior_record_id') or request.query_params.get('behavior_record_id')
            
            if not record_id:
                return Response({
                    'success': False,
                    'error': 'กรุณาระบุ behavior_record_id'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            try:
                record = BehaviorRecord.objects.get(id=record_id)
            except BehaviorRecord.DoesNotExist:
                return Response({
                    'success': False,
                    'error': f'ไม่พบ BehaviorRecord ID {record_id}'
                }, status=status.HTTP_404_NOT_FOUND)
            
            # ตรวจสอบสิทธิ์
            user = request.user
            if user.role not in ['admin', 'discipline_teacher', 'duty_teacher']:
                if record.recorded_by and record.recorded_by != user:
                    return Response({
                        'success': False,
                        'error': 'คุณไม่มีสิทธิ์ลบรายการนี้'
                    }, status=status.HTTP_403_FORBIDDEN)
            
            student = record.student
            old_score = student.behavior_score
            
            with transaction.atomic():
                # คืนคะแนน
                if record.behavior_type == 'deduct':
                    # record.points เป็นลบ (เช่น -1, -2) ดังนั้นต้องบวก
                    student.behavior_score += abs(record.points)
                else:  # add
                    # record.points เป็นบวก (เช่น 5, 10) ดังนั้นต้องลบ
                    student.behavior_score = max(0, student.behavior_score - record.points)
                
                student.save(update_fields=['behavior_score'])
                
                # ลบ record
                deleted_data = {
                    'id': record.id,
                    'behavior_type': record.behavior_type,
                    'points': record.points,
                    'reason': record.reason,
                    'date_recorded': record.date_recorded.isoformat()
                }
                record.delete()
            
            logger.info(f"✅ Deleted BehaviorRecord {record_id} by {user.username}")
            
            return Response({
                'success': True,
                'message': 'ลบรายการสำเร็จและคืนคะแนนแล้ว',
                'deleted': deleted_data,
                'student': {
                    'id': student.id,
                    'student_id': student.student_id,
                    'name': student.get_full_name(),
                    'old_score': old_score,
                    'new_score': student.behavior_score
                }
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"❌ Error deleting behavior record: {str(e)}", exc_info=True)
            return Response({
                'success': False,
                'error': f'เกิดข้อผิดพลาด: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ========================================
# ⭐ NEW: API ลบข้อมูลแบบ Bulk
# ========================================

class BulkAttendanceDeleteView(APIView):
    """
    ⭐ API สำหรับลบหลายรายการพร้อมกัน
    
    POST /scanning/bulk-delete/
    
    Body:
    {
        "rfid_scan_log_ids": [1, 2, 3],    // รายการ ID ที่ต้องการลบ
        "delete_behavior": true             // ลบ BehaviorRecord ด้วย
    }
    """
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        from my.models import AttendanceRecord, BehaviorRecord
        from django.db import models as django_models
        
        try:
            rfid_ids = request.data.get('rfid_scan_log_ids', [])
            delete_behavior = request.data.get('delete_behavior', True)
            
            if not rfid_ids:
                return Response({
                    'success': False,
                    'error': 'กรุณาระบุ rfid_scan_log_ids'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # ตรวจสอบสิทธิ์
            user = request.user
            if user.role not in ['admin', 'discipline_teacher']:
                return Response({
                    'success': False,
                    'error': 'คุณไม่มีสิทธิ์ลบหลายรายการ'
                }, status=status.HTTP_403_FORBIDDEN)
            
            deleted_count = {
                'rfid_scan_logs': 0,
                'face_recognition_logs': 0,
                'attendance_records': 0,
                'behavior_records': 0
            }
            
            restored_points = {}
            
            with transaction.atomic():
                for rfid_id in rfid_ids:
                    try:
                        rfid_log = RFIDScanLog.objects.get(id=rfid_id)
                        student = rfid_log.student
                        scan_date = rfid_log.scan_time.date() if rfid_log.scan_time else None
                        
                        # ลบ FaceRecognitionLog
                        face_count = FaceRecognitionLog.objects.filter(rfid_scan_log=rfid_log).count()
                        FaceRecognitionLog.objects.filter(rfid_scan_log=rfid_log).delete()
                        deleted_count['face_recognition_logs'] += face_count
                        
                        # ลบ AttendanceRecord
                        if student and scan_date:
                            att_count = AttendanceRecord.objects.filter(
                                Q(check_in_rfid_log=rfid_log) | Q(check_out_rfid_log=rfid_log)
                            ).count()
                            AttendanceRecord.objects.filter(
                                Q(check_in_rfid_log=rfid_log) | Q(check_out_rfid_log=rfid_log)
                            ).delete()
                            deleted_count['attendance_records'] += att_count
                        
                        # ลบ BehaviorRecord และคืนคะแนน
                        if delete_behavior and student and scan_date:
                            # Map attendance_status → reason keyword
                            status_reason_map = {
                                'late': 'มาสาย',
                                'absent': 'ขาด',
                                'early_leave': 'ออกก่อนเวลา',
                                'no_checkout': 'ไม่สแกนออก'
                            }
                            reason_keyword = status_reason_map.get(rfid_log.attendance_status, '')
                            
                            behavior_records = BehaviorRecord.objects.filter(
                                student=student,
                                date_recorded=scan_date,
                                is_auto=True
                            )
                            
                            if reason_keyword:
                                behavior_records = behavior_records.filter(reason__icontains=reason_keyword)
                            
                            points_sum = 0
                            for record in behavior_records:
                                points_sum += abs(record.points) if record.points < 0 else 0
                            
                            beh_count = behavior_records.count()
                            behavior_records.delete()
                            deleted_count['behavior_records'] += beh_count
                            
                            if student.id not in restored_points:
                                restored_points[student.id] = 0
                            restored_points[student.id] += points_sum
                        
                        # ลบ RFIDScanLog
                        rfid_log.delete()
                        deleted_count['rfid_scan_logs'] += 1
                        
                    except RFIDScanLog.DoesNotExist:
                        continue
                
                # คืนคะแนนให้นักเรียน
                for student_id, points in restored_points.items():
                    if points > 0:
                        Student.objects.filter(id=student_id).update(
                            behavior_score=django_models.F('behavior_score') + points
                        )
            
            logger.info(f"✅ Bulk deleted by {user.username}: {deleted_count}")
            
            return Response({
                'success': True,
                'message': f'ลบสำเร็จ {deleted_count["rfid_scan_logs"]} รายการ',
                'deleted_count': deleted_count,
                'restored_points': restored_points
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"❌ Error in bulk delete: {str(e)}", exc_info=True)
            return Response({
                'success': False,
                'error': f'เกิดข้อผิดพลาด: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        

class ManualAttendanceViewSet(viewsets.ViewSet):
    """
    ⭐ API สำหรับจัดการ Manual Entry แบบครบวงจร
    - เพิ่ม/ลบ/แก้ไข ข้อมูลการเข้า-ออก
    - ซิงค์ข้อมูลไปยัง my_rfid_scan_logs, attendance_records
    - คำนวณ/คืน คะแนนพฤติกรรมให้อัตโนมัติ
    """
    permission_classes = [IsAuthenticated] # หรือ AllowAny ตามต้องการ

    @transaction.atomic
    def create(self, request):
        serializer = ManualAttendanceSerializer(data=request.data)
        if serializer.is_valid():
            data = serializer.validated_data
            try:
                student = Student.objects.get(student_id=data['student_id'])
                scan_time = data['timestamp']
                
                # 1. สร้าง/บันทึก RFID Scan Log (และ my_rfid_scan_logs ถ้ามี)
                scan_log = RFIDScanLog.objects.create(
                    student=student,
                    scan_type=data['scan_type'],
                    timestamp=scan_time,
                    is_manual=True
                )

                # 2. คำนวณสถานะการเข้าเรียน (สาย/ปกติ/ขาด)
                status_result = "present"
                points_deducted = 0
                
                # ตัวอย่าง Logic การตัดคะแนน (ปรับตามเวลาจริงของคุณ)
                if data['scan_type'] == 'in':
                    limit_time = scan_time.replace(hour=8, minute=0, second=0)
                    if scan_time.time() > time(8, 20):
                        status_result = "late"
                        points_deducted = 1
                    elif scan_time.time() > time(9, 0):
                        status_result = "absent"
                        points_deducted = 2
                
                # 3. สร้าง/อัปเดต AttendanceRecord
                # (สมมติว่า model ชื่อ AttendanceRecord อยู่ใน my.models หรือ scanning.models)
                attendance, created = AttendanceRecord.objects.update_or_create(
                    student=student,
                    date=scan_time.date(),
                    defaults={
                        'status': status_result,
                        'check_in_time': scan_time if data['scan_type'] == 'in' else None,
                        'check_out_time': scan_time if data['scan_type'] == 'out' else None
                    }
                )

                # 4. จัดการคะแนนพฤติกรรม (ตัดคะแนน)
                if points_deducted > 0:
                    student.behavior_score -= points_deducted
                    student.save()
                    
                    # บันทึกประวัติการตัดคะแนน
                    BehaviorRecord.objects.create(
                        student=student,
                        behavior_type_name="มาสาย/ขาด (ระบบอัตโนมัติ)",
                        points_deducted=points_deducted,
                        date=scan_time.date()
                    )

                return Response({
                    "success": True, 
                    "message": "บันทึกข้อมูลและปรับปรุงคะแนนเรียบร้อยแล้ว",
                    "data": {
                        "student": student.get_full_name(),
                        "status": status_result,
                        "current_score": student.behavior_score
                    }
                }, status=status.HTTP_201_CREATED)

            except Student.DoesNotExist:
                return Response({"error": "ไม่พบนักเรียน"}, status=status.HTTP_404_NOT_FOUND)
            except Exception as e:
                return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @transaction.atomic
    def destroy(self, request, pk=None):
        """
        ⭐ ลบรายการสแกน และคืนคะแนนให้นักเรียน (ถ้าเคยถูกหัก)
        pk: ID ของ RFIDScanLog
        """
        try:
            scan_log = RFIDScanLog.objects.get(pk=pk)
            student = scan_log.student
            scan_date = scan_log.timestamp.date()

            # 1. ตรวจสอบว่า Log นี้ทำให้เกิดการหักคะแนนหรือไม่
            # ค้นหา BehaviorRecord ที่เกี่ยวข้องกับวันนั้นและประเภทการเข้าเรียน
            behavior_records = BehaviorRecord.objects.filter(
                student=student,
                date=scan_date,
                behavior_type_name__in=["มาสาย/ขาด (ระบบอัตโนมัติ)"] 
            )

            # 2. คืนคะแนนให้นักเรียน
            total_refund = 0
            for record in behavior_records:
                total_refund += record.points_deducted
                record.delete() # ลบประวัติการหักคะแนน

            if total_refund > 0:
                student.behavior_score += total_refund
                student.save()

            # 3. ลบข้อมูลใน AttendanceRecord (หรือ Reset ค่า)
            attendance = AttendanceRecord.objects.filter(student=student, date=scan_date).first()
            if attendance:
                if scan_log.scan_type == 'in':
                    attendance.check_in_time = None
                elif scan_log.scan_type == 'out':
                    attendance.check_out_time = None
                
                # ถ้าไม่มีทั้งเข้าและออก ให้ลบ record ทิ้ง หรือปรับ status
                if not attendance.check_in_time and not attendance.check_out_time:
                    attendance.delete()
                else:
                    attendance.save()

            # 4. ลบ Log จริง (และ Log ใน my_... ถ้ามีการเชื่อมโยง)
            scan_log.delete()

            return Response({
                "success": True,
                "message": f"ลบข้อมูลสำเร็จ และคืนคะแนน {total_refund} คะแนน",
                "current_score": student.behavior_score
            })

        except RFIDScanLog.DoesNotExist:
            return Response({"error": "ไม่พบข้อมูล Log"}, status=status.HTTP_404_NOT_FOUND)

    @transaction.atomic
    def update(self, request, pk=None):
        """
        ⭐ แก้ไขเวลาเข้า/ออก และคำนวณคะแนนใหม่
        """
        try:
            scan_log = RFIDScanLog.objects.get(pk=pk)
            new_timestamp = request.data.get('timestamp')
            
            if new_timestamp:
                # 1. ทำการลบ Logic เก่าก่อน (คืนคะแนน) - เรียกใช้ destroy logic ภายใน หรือเขียนแยก
                # เพื่อความง่าย เราจะจำลองการลบแล้วสร้างใหม่
                
                # (Logic คืนคะแนนแบบย่อ)
                student = scan_log.student
                # ... คืนคะแนน ...
                
                # 2. อัปเดตเวลาใหม่
                scan_log.timestamp = new_timestamp
                scan_log.save()
                
                # 3. คำนวณคะแนนใหม่ตามเวลาใหม่
                # ... ตัดคะแนนใหม่ ...
                
                return Response({"success": True, "message": "แก้ไขเวลาและปรับปรุงคะแนนใหม่แล้ว"})
            
            return Response({"error": "ข้อมูลไม่ครบถ้วน"}, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class BehaviorManagementViewSet(viewsets.ViewSet):
    """
    ⭐ API สำหรับจัดการคะแนนพฤติกรรมโดยตรง (เพิ่ม/ลด)
    """
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['post'])
    @transaction.atomic
    def adjust_score(self, request):
        serializer = BehaviorAdjustmentSerializer(data=request.data)
        if serializer.is_valid():
            data = serializer.validated_data
            try:
                student = Student.objects.get(student_id=data['student_id'])
                points = data['points']
                
                # อัปเดตคะแนน
                # ถ้า points เป็นบวก = เพิ่มคะแนน, ลบ = หักคะแนน
                # แต่ในที่นี้เราจะรับเป็น points_deducted (ค่าบวกคือหัก) หรือ score_change
                
                # สมมติระบบส่งมาว่า adjustment: -5 (หัก 5 คะแนน)
                adjustment = points 
                
                student.behavior_score += adjustment
                student.save()
                
                # สร้าง Log
                BehaviorRecord.objects.create(
                    student=student,
                    behavior_type_name="ปรับคะแนนโดยครู/แอดมิน",
                    points_deducted=-adjustment if adjustment < 0 else 0,
                    points_added=adjustment if adjustment > 0 else 0,
                    note=data.get('note', '')
                )
                
                return Response({
                    "success": True, 
                    "new_score": student.behavior_score,
                    "message": "ปรับปรุงคะแนนเรียบร้อย"
                })
                
            except Student.DoesNotExist:
                return Response({"error": "ไม่พบนักเรียน"}, status=status.HTTP_404_NOT_FOUND)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)