# scanning/auto_scheduler.py
# ⭐ Background Scheduler สำหรับประมวลผลการเข้าเรียนอัตโนมัติ
# ===========================================================
# 
# ทำงานอัตโนมัติ:
#   - 09:30 น. → ประมวลผลขาดเรียน (absent)
#   - 17:30 น. → ประมวลผลไม่สแกนออก (no_checkout)
#
# วิธีใช้:
#   แค่รัน python manage.py runserver
#   Scheduler จะทำงานอัตโนมัติ
# ===========================================================

import threading
import time
import logging
from datetime import datetime, time as dt_time, timedelta
from django.utils import timezone

logger = logging.getLogger(__name__)

# ⭐ การตั้งค่าเวลา
SCHEDULE_CONFIG = {
    'absent_processing': {
        'hour': 9,
        'minute': 30,
        'description': 'ประมวลผลขาดเรียน'
    },
    'no_checkout_processing': {
        'hour': 17,
        'minute': 30,
        'description': 'ประมวลผลไม่สแกนออก'
    }
}

# ⭐ วันเรียน (จันทร์=0 ถึง ศุกร์=4)
SCHOOL_DAYS = [0, 1, 2, 3, 4]


class AttendanceScheduler:
    """Scheduler สำหรับประมวลผลการเข้าเรียนอัตโนมัติ"""
    
    def __init__(self):
        self.running = True
        self.last_absent_run = None
        self.last_no_checkout_run = None
        self._lock = threading.Lock()
    
    def is_school_day(self, date=None):
        """ตรวจสอบว่าเป็นวันเรียนหรือไม่"""
        if date is None:
            date = timezone.now().date()
        return date.weekday() in SCHOOL_DAYS
    
    def should_run_absent(self, now):
        """ตรวจสอบว่าควรรัน absent processing หรือไม่"""
        config = SCHEDULE_CONFIG['absent_processing']
        target_time = dt_time(config['hour'], config['minute'])
        
        # เช็คว่าถึงเวลาหรือยัง
        if now.time() >= target_time:
            today = now.date()
            # เช็คว่ารันวันนี้แล้วหรือยัง
            if self.last_absent_run != today:
                return True
        return False
    
    def should_run_no_checkout(self, now):
        """ตรวจสอบว่าควรรัน no_checkout processing หรือไม่"""
        config = SCHEDULE_CONFIG['no_checkout_processing']
        target_time = dt_time(config['hour'], config['minute'])
        
        if now.time() >= target_time:
            today = now.date()
            if self.last_no_checkout_run != today:
                return True
        return False
    
    def process_absent(self):
        """ประมวลผลขาดเรียน"""
        from .behavior_scoring import behavior_scoring_service
        
        today = timezone.now().date()
        
        logger.info(f"🔄 [Scheduler] เริ่มประมวลผลขาดเรียน วันที่ {today}")
        
        try:
            result = behavior_scoring_service.process_daily_absent(today)
            
            logger.info(
                f"✅ [Scheduler] ประมวลผลขาดเรียนเสร็จ: "
                f"ขาด {result.get('absent_count', 0)} คน, "
                f"หักคะแนน {result.get('deducted_count', 0)} คน"
            )
            
            with self._lock:
                self.last_absent_run = today
                
            return result
            
        except Exception as e:
            logger.error(f"❌ [Scheduler] Error processing absent: {e}", exc_info=True)
            return None
    
    def process_no_checkout(self):
        """ประมวลผลไม่สแกนออก"""
        from .behavior_scoring import behavior_scoring_service
        
        today = timezone.now().date()
        
        logger.info(f"🔄 [Scheduler] เริ่มประมวลผลไม่สแกนออก วันที่ {today}")
        
        try:
            result = behavior_scoring_service.process_daily_no_checkout(today)
            
            logger.info(
                f"✅ [Scheduler] ประมวลผลไม่สแกนออกเสร็จ: "
                f"ไม่สแกนออก {result.get('no_checkout_count', 0)} คน, "
                f"หักคะแนน {result.get('deducted_count', 0)} คน"
            )
            
            with self._lock:
                self.last_no_checkout_run = today
                
            return result
            
        except Exception as e:
            logger.error(f"❌ [Scheduler] Error processing no_checkout: {e}", exc_info=True)
            return None
    
    def run(self):
        """Main loop ของ scheduler"""
        logger.info("🚀 [Scheduler] Auto Attendance Scheduler กำลังทำงาน...")
        logger.info(f"📋 [Scheduler] กำหนดการ:")
        logger.info(f"   - ประมวลผลขาดเรียน: {SCHEDULE_CONFIG['absent_processing']['hour']:02d}:{SCHEDULE_CONFIG['absent_processing']['minute']:02d} น.")
        logger.info(f"   - ประมวลผลไม่สแกนออก: {SCHEDULE_CONFIG['no_checkout_processing']['hour']:02d}:{SCHEDULE_CONFIG['no_checkout_processing']['minute']:02d} น.")
        logger.info(f"   - วันเรียน: จันทร์ - ศุกร์")
        
        while self.running:
            try:
                now = timezone.now()
                
                # ตรวจสอบว่าเป็นวันเรียนหรือไม่
                if self.is_school_day(now.date()):
                    
                    # ตรวจสอบ absent processing
                    if self.should_run_absent(now):
                        logger.info(f"⏰ [Scheduler] ถึงเวลาประมวลผลขาดเรียน ({now.strftime('%H:%M')})")
                        self.process_absent()
                    
                    # ตรวจสอบ no_checkout processing
                    if self.should_run_no_checkout(now):
                        logger.info(f"⏰ [Scheduler] ถึงเวลาประมวลผลไม่สแกนออก ({now.strftime('%H:%M')})")
                        self.process_no_checkout()
                
                # รอ 60 วินาที แล้วเช็คใหม่
                time.sleep(60)
                
            except Exception as e:
                logger.error(f"❌ [Scheduler] Error in main loop: {e}", exc_info=True)
                time.sleep(60)
        
        logger.info("🛑 [Scheduler] Scheduler stopped")
    
    def stop(self):
        """หยุด scheduler"""
        self.running = False


# Global scheduler instance
_scheduler = None


def start_scheduler():
    """เริ่ม scheduler"""
    global _scheduler
    
    if _scheduler is not None:
        logger.warning("⚠️ [Scheduler] Scheduler already running")
        return
    
    _scheduler = AttendanceScheduler()
    _scheduler.run()


def stop_scheduler():
    """หยุด scheduler"""
    global _scheduler
    
    if _scheduler is not None:
        _scheduler.stop()
        _scheduler = None


def get_scheduler_status():
    """ดึงสถานะ scheduler"""
    global _scheduler
    
    if _scheduler is None:
        return {'running': False}
    
    return {
        'running': _scheduler.running,
        'last_absent_run': str(_scheduler.last_absent_run) if _scheduler.last_absent_run else None,
        'last_no_checkout_run': str(_scheduler.last_no_checkout_run) if _scheduler.last_no_checkout_run else None,
        'schedule': SCHEDULE_CONFIG
    }


def run_now(task_type='all'):
    """
    รันประมวลผลทันที (สำหรับทดสอบหรือ manual trigger)
    
    Args:
        task_type: 'absent', 'no_checkout', หรือ 'all'
    """
    global _scheduler
    
    if _scheduler is None:
        _scheduler = AttendanceScheduler()
    
    results = {}
    
    if task_type in ['absent', 'all']:
        results['absent'] = _scheduler.process_absent()
    
    if task_type in ['no_checkout', 'all']:
        results['no_checkout'] = _scheduler.process_no_checkout()
    
    return results