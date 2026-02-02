# scanning/apps.py
# ⭐ รวม Preload Face Embeddings + Auto Attendance Scheduler
# ===========================================================

from django.apps import AppConfig
import threading
import logging
import os

logger = logging.getLogger(__name__)


class ScanningConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'scanning'
    verbose_name = 'ระบบสแกน RFID และตรวจจับใบหน้า'
    
    def ready(self):
        """เรียกเมื่อ Django app พร้อมใช้งาน"""
        
        # ป้องกันรันซ้ำใน dev server (รัน 2 ครั้ง)
        if os.environ.get('RUN_MAIN') != 'true':
            return
        
        # ⭐ 1. Preload Face Embeddings (โค้ดเดิม)
        try:
            from my.models import Student
            from .face_recognition_service import face_recognition_service
            
            students = Student.objects.filter(is_active=True)
            face_recognition_service.preload_student_embeddings(students)
            logger.info(f"✅ Preloaded face embeddings for {students.count()} students")
        except Exception as e:
            logger.warning(f"⚠️ Could not preload face embeddings: {e}")
        
        # ⭐ 2. Start Auto Attendance Scheduler
        try:
            from .auto_scheduler import start_scheduler
            
            logger.info("🚀 Starting Auto Attendance Scheduler...")
            
            # รันใน thread แยก
            scheduler_thread = threading.Thread(
                target=start_scheduler,
                daemon=True,  # ปิดตามตัว server
                name='AttendanceScheduler'
            )
            scheduler_thread.start()
            
            logger.info("✅ Auto Attendance Scheduler started!")
        except Exception as e:
            logger.error(f"❌ Could not start scheduler: {e}")