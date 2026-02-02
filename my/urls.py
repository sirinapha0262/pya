# my/urls.py - ⭐ แก้ไขลำดับ URL patterns
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import *
from rest_framework_simplejwt.views import TokenRefreshView

# สร้าง router สำหรับ ViewSets
router = DefaultRouter()
router.register(r'users', UserViewSet, basename='user')
router.register(r'students', StudentViewSet, basename='student')
router.register(r'attendance', AttendanceRecordViewSet, basename='attendance')
router.register(r'behavior', BehaviorRecordViewSet, basename='behavior')
router.register(r'manual-entry', ManualEntryViewSet, basename='manual-entry')

urlpatterns = [
    # Authentication endpoints
    path('login/', MyTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    
    # ⭐ สำคัญ: ต้องใส่ path ที่เฉพาะเจาะจงไว้ก่อน router
    # เพราะ router จะ match /students/<pk>/ ซึ่งจะทำให้ /students/me/ กลายเป็น pk="me"
    path('students/me/', StudentMeView.as_view(), name='student_me'),
    path('students/me/behavior-history/', StudentBehaviorHistoryView.as_view(), name='student_behavior_history'),
    path('students/me/attendance-history/', StudentAttendanceHistoryView.as_view(), name='student_attendance_history'),

    # Include router URLs - ⭐ ต้องอยู่หลัง students/me/
    path('', include(router.urls)),

    # Additional endpoints
    path('admin-count/', AdminCountView.as_view(), name='admin_count'),
    path('homeroom-teachers/', HomeroomTeacherListView.as_view(), name='homeroom_teachers'),
    
    # Academic Year endpoints
    path('academic-year/current/', CurrentAcademicYearView.as_view(), name='current_academic_year'),
    path('academic-year/update/', UpdateAcademicYearView.as_view(), name='update_academic_year'),
    
    # Dashboard
    path('dashboard/stats/', DashboardStatsView.as_view(), name='dashboard_stats'),
    
    # Auto Processing endpoints
    path('attendance/auto-process/', AutoAttendanceProcessingView.as_view(), name='auto_process_attendance'),
    
    # Behavior permission check
    path('check-behavior-permission/', CheckBehaviorPermissionView.as_view(), name='check-behavior-permission'),
]