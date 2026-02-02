# scanning/tasks.py
# ⭐ Celery Tasks สำหรับประมวลผลอัตโนมัติ (Optional)
# ===========================================
# ถ้าต้องการใช้ Celery ให้ติดตั้ง:
#   pip install celery redis
#
# การตั้งค่าใน settings.py:
#   CELERY_BROKER_URL = 'redis://localhost:6379/0'
#   CELERY_RESULT_BACKEND = 'redis://localhost:6379/0'
#
# การรัน Celery:
#   celery -A project_name worker -l info
#   celery -A project_name beat -l info  # สำหรับ scheduled tasks
# ===========================================

from celery import shared_task
from django.utils import timezone
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


@shared_task(name='process_daily_absent')
def process_daily_absent_task(date_str=None):
    """
    ⭐ Celery Task: ประมวลผลการขาดเรียน
    ควรรันหลัง 09:00 น. ของวันนั้น
    
    Args:
        date_str: วันที่ในรูปแบบ YYYY-MM-DD (optional)
    
    Returns:
        dict: ผลการประมวลผล
    """
    from .behavior_scoring import behavior_scoring_service
    
    try:
        if date_str:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        else:
            target_date = timezone.now().date()
        
        logger.info(f"🚀 เริ่ม Task ประมวลผลขาดเรียน วันที่ {target_date}")
        
        result = behavior_scoring_service.process_daily_absent(target_date)
        
        logger.info(f"✅ เสร็จสิ้น: ขาดเรียน {result.get('absent_count', 0)} คน")
        return result
        
    except Exception as e:
        logger.error(f"❌ Error in process_daily_absent_task: {str(e)}", exc_info=True)
        return {'error': str(e)}


@shared_task(name='process_daily_no_checkout')
def process_daily_no_checkout_task(date_str=None):
    """
    ⭐ Celery Task: ประมวลผลนักเรียนที่ไม่สแกนออก
    ควรรันหลัง 17:00 น. ของวันนั้น
    
    Args:
        date_str: วันที่ในรูปแบบ YYYY-MM-DD (optional)
    
    Returns:
        dict: ผลการประมวลผล
    """
    from .behavior_scoring import behavior_scoring_service
    
    try:
        if date_str:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        else:
            target_date = timezone.now().date()
        
        logger.info(f"🚀 เริ่ม Task ประมวลผลไม่สแกนออก วันที่ {target_date}")
        
        result = behavior_scoring_service.process_daily_no_checkout(target_date)
        
        logger.info(f"✅ เสร็จสิ้น: ไม่สแกนออก {result.get('no_checkout_count', 0)} คน")
        return result
        
    except Exception as e:
        logger.error(f"❌ Error in process_daily_no_checkout_task: {str(e)}", exc_info=True)
        return {'error': str(e)}


@shared_task(name='process_full_daily_attendance')
def process_full_daily_attendance_task(date_str=None):
    """
    ⭐ Celery Task: ประมวลผลการเข้าเรียนทั้งหมดในวันนั้น
    รวม: ขาดเรียน + ไม่สแกนออก
    
    Args:
        date_str: วันที่ในรูปแบบ YYYY-MM-DD (optional)
    
    Returns:
        dict: ผลการประมวลผล
    """
    from .behavior_scoring import behavior_scoring_service
    
    try:
        if date_str:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        else:
            target_date = timezone.now().date()
        
        logger.info(f"🚀 เริ่ม Task ประมวลผลการเข้าเรียนทั้งหมด วันที่ {target_date}")
        
        result = behavior_scoring_service.process_daily_behavior_scores(target_date)
        
        total = result.get('total_deducted', 0)
        logger.info(f"✅ เสร็จสิ้น: รวมหักคะแนน {total} คน")
        return result
        
    except Exception as e:
        logger.error(f"❌ Error in process_full_daily_attendance_task: {str(e)}", exc_info=True)
        return {'error': str(e)}


@shared_task(name='generate_daily_report')
def generate_daily_report_task(date_str=None):
    """
    ⭐ Celery Task: สร้างรายงานประจำวัน
    ควรรันตอนสิ้นสุดวันเรียน
    
    Args:
        date_str: วันที่ในรูปแบบ YYYY-MM-DD (optional)
    
    Returns:
        dict: ผลการสร้างรายงาน
    """
    from .utils import generate_daily_report
    
    try:
        if date_str:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        else:
            target_date = timezone.now().date()
        
        logger.info(f"📊 สร้างรายงานประจำวัน วันที่ {target_date}")
        
        report = generate_daily_report(target_date)
        
        if report:
            return {
                'success': True,
                'date': str(target_date),
                'report_id': report.id,
                'total_students': report.total_students,
                'present': report.students_present,
                'late': report.students_late,
                'absent': report.students_absent
            }
        else:
            return {
                'success': False,
                'date': str(target_date),
                'message': 'ไม่สามารถสร้างรายงานได้ (อาจเป็นวันหยุด)'
            }
        
    except Exception as e:
        logger.error(f"❌ Error in generate_daily_report_task: {str(e)}", exc_info=True)
        return {'error': str(e)}


@shared_task(name='check_offline_devices')
def check_offline_devices_task():
    """
    ⭐ Celery Task: ตรวจสอบอุปกรณ์ที่ออฟไลน์
    ควรรันทุก 5-10 นาที
    """
    from .models import DeviceStatus
    from .utils import create_system_alert
    
    try:
        threshold = timezone.now() - timedelta(minutes=10)
        
        # หาอุปกรณ์ที่ออฟไลน์
        offline_devices = DeviceStatus.objects.filter(
            status='online',
            last_heartbeat__lt=threshold
        )
        
        for device in offline_devices:
            device.status = 'offline'
            device.save()
            
            # สร้างการแจ้งเตือน
            create_system_alert(
                alert_type='device_offline',
                severity='high',
                title=f'อุปกรณ์ {device.device_name} ออฟไลน์',
                message=f'ไม่ได้รับสัญญาณจากอุปกรณ์ {device.device_name} ตั้งแต่ {device.last_heartbeat}',
                device=device
            )
            
            logger.warning(f"⚠️ Device {device.device_name} marked as offline")
        
        return {
            'success': True,
            'offline_count': offline_devices.count()
        }
        
    except Exception as e:
        logger.error(f"❌ Error in check_offline_devices_task: {str(e)}", exc_info=True)
        return {'error': str(e)}


# ========================================
# ⭐ Celery Beat Schedule Configuration
# ========================================
# ใส่ใน settings.py หรือ celery.py:
#
# from celery.schedules import crontab
#
# CELERY_BEAT_SCHEDULE = {
#     # ประมวลผลขาดเรียน - รัน 09:30 น. ทุกวัน จ-ศ
#     'process-daily-absent': {
#         'task': 'process_daily_absent',
#         'schedule': crontab(hour=9, minute=30, day_of_week='1-5'),
#     },
#     
#     # ประมวลผลไม่สแกนออก - รัน 17:30 น. ทุกวัน จ-ศ
#     'process-daily-no-checkout': {
#         'task': 'process_daily_no_checkout',
#         'schedule': crontab(hour=17, minute=30, day_of_week='1-5'),
#     },
#     
#     # สร้างรายงานประจำวัน - รัน 18:00 น. ทุกวัน จ-ศ
#     'generate-daily-report': {
#         'task': 'generate_daily_report',
#         'schedule': crontab(hour=18, minute=0, day_of_week='1-5'),
#     },
#     
#     # ตรวจสอบอุปกรณ์ออฟไลน์ - รันทุก 5 นาที
#     'check-offline-devices': {
#         'task': 'check_offline_devices',
#         'schedule': crontab(minute='*/5'),
#     },
# }
# ========================================
