# scanning/admin.py
from django.contrib import admin
from django.utils.html import format_html
from django.utils import timezone
from .models import (
    RFIDScanLog, FaceRecognitionLog, DeviceStatus,
    SystemAlert, DailyReport
)


@admin.register(RFIDScanLog)
class RFIDScanLogAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'scan_time', 'get_student_info', 'rfid_card_id',
        'scan_type', 'status_badge', 'device_name'
    ]
    list_filter = ['status', 'scan_type', 'scan_time', 'device_name']
    search_fields = [
        'rfid_card_id', 'student__student_id',
        'student__first_name', 'student__last_name'
    ]
    date_hierarchy = 'scan_time'
    readonly_fields = ['created_at', 'processing_time']
    
    fieldsets = (
        ('ข้อมูลการสแกน', {
            'fields': ('student', 'rfid_card_id', 'scan_type', 'scan_time', 'status')
        }),
        ('ข้อมูลอุปกรณ์', {
            'fields': ('device_name', 'device_location', 'device_info')
        }),
        ('ผลการประมวลผล', {
            'fields': ('error_message', 'processing_time', 'created_at')
        }),
    )
    
    def get_student_info(self, obj):
        if obj.student:
            return f"{obj.student.get_full_name()} (ป.{obj.student.grade}/{obj.student.classroom})"
        return "-"
    get_student_info.short_description = 'นักเรียน'
    
    def status_badge(self, obj):
        colors = {
            'success': 'green',
            'failed': 'red',
            'pending': 'orange'
        }
        color = colors.get(obj.status, 'gray')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 10px; border-radius: 3px;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = 'สถานะ'


@admin.register(FaceRecognitionLog)
class FaceRecognitionLogAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'created_at', 'get_student_info', 'status_badge',
        'confidence_score', 'processing_time', 'get_image_preview'
    ]
    list_filter = ['status', 'created_at']
    search_fields = ['student__student_id', 'student__first_name', 'student__last_name']
    date_hierarchy = 'created_at'
    readonly_fields = ['created_at', 'processing_time', 'get_image_display']
    
    fieldsets = (
        ('ข้อมูลการตรวจจับ', {
            'fields': ('rfid_scan_log', 'student', 'status', 'confidence_score')
        }),
        ('รูปภาพ', {
            'fields': ('captured_image', 'get_image_display')
        }),
        ('รายละเอียดการประมวลผล', {
            'fields': ('processing_time', 'face_detection_details', 'error_message', 'created_at')
        }),
    )
    
    def get_student_info(self, obj):
        if obj.student:
            return f"{obj.student.get_full_name()} (ป.{obj.student.grade}/{obj.student.classroom})"
        return "-"
    get_student_info.short_description = 'นักเรียน'
    
    def status_badge(self, obj):
        colors = {
            'success': 'green',
            'failed': 'red',
            'no_face': 'orange',
            'multiple_faces': 'orange',
            'mismatch': 'red',
            'error': 'darkred'
        }
        color = colors.get(obj.status, 'gray')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 10px; border-radius: 3px;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = 'สถานะ'
    
    def get_image_preview(self, obj):
        if obj.captured_image:
            return format_html(
                '<a href="{}" target="_blank">ดูรูป</a>',
                obj.captured_image.url
            )
        return "-"
    get_image_preview.short_description = 'รูปภาพ'
    
    def get_image_display(self, obj):
        if obj.captured_image:
            return format_html(
                '<img src="{}" style="max-width: 300px; max-height: 300px;" />',
                obj.captured_image.url
            )
        return "-"
    get_image_display.short_description = 'ตัวอย่างรูปภาพ'


@admin.register(DeviceStatus)
class DeviceStatusAdmin(admin.ModelAdmin):
    list_display = [
        'device_name', 'device_type', 'location', 'status_badge',
        'ip_address', 'last_heartbeat', 'get_uptime', 'get_success_rate'
    ]
    list_filter = ['status', 'device_type', 'last_heartbeat']
    search_fields = ['device_name', 'location', 'ip_address', 'mac_address']
    readonly_fields = [
        'created_at', 'updated_at', 'last_heartbeat',
        'last_online', 'get_uptime_display', 'get_success_rate_display'
    ]
    
    fieldsets = (
        ('ข้อมูลอุปกรณ์', {
            'fields': ('device_name', 'device_type', 'location', 'status')
        }),
        ('ข้อมูลเครือข่าย', {
            'fields': ('ip_address', 'mac_address', 'firmware_version')
        }),
        ('สถิติการทำงาน', {
            'fields': (
                'total_scans', 'successful_scans', 'failed_scans',
                'get_success_rate_display'
            )
        }),
        ('การเชื่อมต่อ', {
            'fields': ('last_heartbeat', 'last_online', 'get_uptime_display')
        }),
        ('อื่นๆ', {
            'fields': ('notes', 'created_at', 'updated_at')
        }),
    )
    
    def status_badge(self, obj):
        colors = {
            'online': 'green',
            'offline': 'red',
            'maintenance': 'orange',
            'error': 'darkred'
        }
        color = colors.get(obj.status, 'gray')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 10px; border-radius: 3px;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = 'สถานะ'
    
    def get_uptime(self, obj):
        if obj.last_online and obj.created_at:
            if obj.status == 'online':
                delta = timezone.now() - obj.last_online
            else:
                if obj.last_heartbeat:
                    delta = obj.last_heartbeat - obj.created_at
                else:
                    return "0 ชม."
            hours = round(delta.total_seconds() / 3600, 1)
            return f"{hours} ชม."
        return "0 ชม."
    get_uptime.short_description = 'เวลาทำงาน'
    
    def get_uptime_display(self, obj):
        return self.get_uptime(obj)
    get_uptime_display.short_description = 'เวลาทำงานทั้งหมด'
    
    def get_success_rate(self, obj):
        if obj.total_scans == 0:
            return "0%"
        rate = round((obj.successful_scans / obj.total_scans) * 100, 1)
        return f"{rate}%"
    get_success_rate.short_description = 'อัตราความสำเร็จ'
    
    def get_success_rate_display(self, obj):
        if obj.total_scans == 0:
            return "0% (0/0)"
        rate = round((obj.successful_scans / obj.total_scans) * 100, 1)
        return f"{rate}% ({obj.successful_scans}/{obj.total_scans})"
    get_success_rate_display.short_description = 'อัตราความสำเร็จในการสแกน'


@admin.register(SystemAlert)
class SystemAlertAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'created_at', 'severity_badge', 'alert_type',
        'title', 'is_resolved', 'resolved_at'
    ]
    list_filter = ['severity', 'alert_type', 'is_resolved', 'created_at']
    search_fields = ['title', 'message']
    date_hierarchy = 'created_at'
    readonly_fields = ['created_at']
    
    fieldsets = (
        ('ข้อมูลการแจ้งเตือน', {
            'fields': ('alert_type', 'severity', 'title', 'message')
        }),
        ('ข้อมูลอ้างอิง', {
            'fields': ('device', 'student')
        }),
        ('สถานะการแก้ไข', {
            'fields': ('is_resolved', 'resolved_at', 'resolved_by', 'resolution_notes')
        }),
        ('อื่นๆ', {
            'fields': ('created_at',)
        }),
    )
    
    actions = ['mark_as_resolved', 'mark_as_unresolved']
    
    def severity_badge(self, obj):
        colors = {
            'low': 'blue',
            'medium': 'orange',
            'high': 'red',
            'critical': 'darkred'
        }
        color = colors.get(obj.severity, 'gray')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 10px; border-radius: 3px;">{}</span>',
            color,
            obj.get_severity_display()
        )
    severity_badge.short_description = 'ระดับความรุนแรง'
    
    def mark_as_resolved(self, request, queryset):
        updated = queryset.update(
            is_resolved=True,
            resolved_at=timezone.now(),
            resolved_by=request.user
        )
        self.message_user(request, f'ทำเครื่องหมายแก้ไขแล้ว {updated} รายการ')
    mark_as_resolved.short_description = 'ทำเครื่องหมายว่าแก้ไขแล้ว'
    
    def mark_as_unresolved(self, request, queryset):
        updated = queryset.update(
            is_resolved=False,
            resolved_at=None,
            resolved_by=None
        )
        self.message_user(request, f'ทำเครื่องหมายว่ายังไม่ได้แก้ไข {updated} รายการ')
    mark_as_unresolved.short_description = 'ทำเครื่องหมายว่ายังไม่ได้แก้ไข'


@admin.register(DailyReport)
class DailyReportAdmin(admin.ModelAdmin):
    list_display = [
        'report_date', 'total_students', 'students_present',
        'students_late', 'students_absent', 'get_attendance_rate',
        'total_scans', 'get_scan_success_rate'
    ]
    list_filter = ['report_date']
    search_fields = ['report_date', 'notes']
    date_hierarchy = 'report_date'
    readonly_fields = [
        'created_at', 'updated_at',
        'get_attendance_rate_display', 'get_scan_success_rate_display'
    ]
    
    fieldsets = (
        ('ข้อมูลทั่วไป', {
            'fields': ('report_date',)
        }),
        ('สถิตินักเรียน', {
            'fields': (
                'total_students', 'students_present', 'students_late',
                'students_absent', 'students_early_leave',
                'get_attendance_rate_display'
            )
        }),
        ('สถิติการสแกน', {
            'fields': (
                'total_scans', 'successful_scans', 'failed_scans',
                'get_scan_success_rate_display'
            )
        }),
        ('สถิติการตรวจจับใบหน้า', {
            'fields': ('face_recognition_success', 'face_recognition_failed')
        }),
        ('สถิติอุปกรณ์', {
            'fields': ('devices_online', 'devices_offline')
        }),
        ('การแจ้งเตือน', {
            'fields': ('system_alerts_count',)
        }),
        ('อื่นๆ', {
            'fields': ('notes', 'created_at', 'updated_at')
        }),
    )
    
    def get_attendance_rate(self, obj):
        if obj.total_students == 0:
            return "0%"
        rate = round((obj.students_present / obj.total_students) * 100, 1)
        color = 'green' if rate >= 90 else 'orange' if rate >= 80 else 'red'
        return format_html(
            '<span style="color: {}; font-weight: bold;">{:.1f}%</span>',
            color,
            rate
        )
    get_attendance_rate.short_description = 'อัตราการเข้าเรียน'
    
    def get_attendance_rate_display(self, obj):
        if obj.total_students == 0:
            return "0%"
        rate = round((obj.students_present / obj.total_students) * 100, 1)
        return f"{rate}% ({obj.students_present}/{obj.total_students})"
    get_attendance_rate_display.short_description = 'อัตราการเข้าเรียน'
    
    def get_scan_success_rate(self, obj):
        if obj.total_scans == 0:
            return "0%"
        rate = round((obj.successful_scans / obj.total_scans) * 100, 1)
        return f"{rate}%"
    get_scan_success_rate.short_description = 'อัตราความสำเร็จการสแกน'
    
    def get_scan_success_rate_display(self, obj):
        if obj.total_scans == 0:
            return "0%"
        rate = round((obj.successful_scans / obj.total_scans) * 100, 1)
        return f"{rate}% ({obj.successful_scans}/{obj.total_scans})"
    get_scan_success_rate_display.short_description = 'อัตราความสำเร็จในการสแกน'


# กำหนดชื่อแสดงใน Admin Site
admin.site.site_header = "One Card Smart School V.2 - ระบบจัดการ"
admin.site.site_title = "One Card Smart School V.2"
admin.site.index_title = "ยินดีต้อนรับสู่ระบบจัดการ One Card Smart School V.2"