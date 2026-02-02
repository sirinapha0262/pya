# scanning/urls.py
# ⭐ อัปเดต: เพิ่ม URL สำหรับ API ลบ/แก้ไข Manual Entry และจัดการคะแนนพฤติกรรม

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import *


# สร้าง router สำหรับ ViewSets
router = DefaultRouter()
router.register(r'scan-logs', RFIDScanLogViewSet, basename='scan-logs')
router.register(r'face-recognition', FaceRecognitionLogViewSet, basename='face-recognition')
router.register(r'devices', DeviceStatusViewSet, basename='devices')
router.register(r'alerts', SystemAlertViewSet, basename='alerts')
router.register(r'daily-reports', DailyReportViewSet, basename='daily-reports')
router.register(r'behavior', BehaviorRecordViewSet, basename='behavior')
router.register(r'manual-attendance', ManualAttendanceViewSet, basename='manual-attendance')
router.register(r'behavior-management', BehaviorManagementViewSet, basename='behavior-management')

urlpatterns = [
    # ========================================
    # ⭐ เอา path ที่ต้องการไว้ก่อน router
    # ========================================
    
    # API ใหม่: Attendance History (รวม check_in/check_out)
    path('attendance/history/', StudentAttendanceHistoryView.as_view(), name='attendance_history'),
    path('attendance-list/', CombinedAttendanceListView.as_view(), name='attendance_list'),
    
    # API สำหรับการสแกนจริง (เชื่อมต่อกับฮาร์ดแวร์)
    path('scan/', ScanProcessView.as_view(), name='scan_process'),
    path('verify-face/', FaceVerificationView.as_view(), name='face_verification'),
    path('device-heartbeat/', DeviceHeartbeatView.as_view(), name='device_heartbeat'),
    
    # ========================================
    # ⭐ API ครูเวรบันทึกด้วยตนเอง (Manual Entry)
    # ========================================
    path('manual-record/', ManualAttendanceRecordView.as_view(), name='manual_record'),
    
    # ⭐ NEW: ลบ Manual Entry (ส่งไป rfid_scan_logs, face_recognition_logs, attendance_records, behavior_records)
    path('manual-record/delete/', ManualAttendanceDeleteView.as_view(), name='manual_record_delete'),
    
    # ⭐ NEW: แก้ไข Manual Entry (แก้ไขข้อมูลการเข้า-ออก)
    path('manual-record/update/', ManualAttendanceUpdateView.as_view(), name='manual_record_update'),
    
    # ⭐ NEW: ลบหลายรายการพร้อมกัน
    path('bulk-delete/', BulkAttendanceDeleteView.as_view(), name='bulk_delete'),
    
    # ========================================
    # ⭐ API จัดการคะแนนพฤติกรรม
    # ========================================
    # ⭐ NEW: เพิ่ม/ลด/ดู/ลบ คะแนนพฤติกรรม
    path('behavior-score/manage/', BehaviorScoreManagementView.as_view(), name='behavior_score_manage'),
    
    # API สรุปคะแนนพฤติกรรม (เดิม)
    path('behavior-summary/', BehaviorScoreSummaryView.as_view(), name='behavior_summary'),
    path('process-behavior-scores/', ProcessBehaviorScoresView.as_view(), name='process_behavior_scores'),
    
    # ========================================
    # ⭐ API สรุปการเข้าเรียนรายวัน
    # ========================================
    path('attendance-summary/', AttendanceDailySummaryView.as_view(), name='attendance_summary'),
    
    # API สำหรับสถิติและรายงานแบบเรียลไทม์
    path('realtime-stats/', RealtimeStatsView.as_view(), name='realtime_stats'),
    path('today-summary/', TodaySummaryView.as_view(), name='today_summary'),
    path('attendance-report/', AttendanceReportView.as_view(), name='attendance_report'),
    
    # API สำหรับจัดการอุปกรณ์
    path('device-status/', DeviceStatusSummaryView.as_view(), name='device_status_summary'),
    path('daily-check/', DailyCheckView.as_view(), name='daily_check'),
    
    # API สำหรับการแจ้งเตือน
    path('active-alerts/', ActiveAlertsView.as_view(), name='active_alerts'),
    
    # API สำหรับรายงาน
    path('scanning-report/', ScanningReportView.as_view(), name='scanning_report'),
    path('export-scan-logs/', ExportScanLogView.as_view(), name='export_scan_logs'),
    
    # API เพิ่มเติมสำหรับการบำรุงรักษา
    path('maintenance/', MaintenanceView.as_view(), name='maintenance'),
    path('system-health/', SystemHealthView.as_view(), name='system_health'),
    path('backup-data/', BackupDataView.as_view(), name='backup_data'),
    
    # ========================================
    # ⭐ ใส่ router.urls ไว้ท้ายสุด
    # ========================================
    path('', include(router.urls)),
]