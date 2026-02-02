# scanning/management/commands/auto_attendance.py
# ⭐ คำสั่งสำหรับประมวลผลการเข้าเรียนอัตโนมัติ
# ===========================================
# การใช้งาน:
#   python manage.py auto_attendance              # ประมวลผลวันนี้
#   python manage.py auto_attendance --date 2024-01-15  # ระบุวันที่
#   python manage.py auto_attendance --absent-only      # ประมวลผลเฉพาะขาด
#   python manage.py auto_attendance --no-checkout-only # ประมวลผลเฉพาะไม่สแกนออก
#   python manage.py auto_attendance --dry-run          # ทดสอบ (ไม่บันทึกจริง)
#
# ⭐ ตั้งค่า Cron Job:
#   # ประมวลผลขาดเรียน - รันเวลา 09:30 น. ทุกวัน จ-ศ
#   30 9 * * 1-5 /path/to/venv/bin/python /path/to/manage.py auto_attendance --absent-only
#
#   # ประมวลผลไม่สแกนออก - รันเวลา 17:30 น. ทุกวัน จ-ศ
#   30 17 * * 1-5 /path/to/venv/bin/python /path/to/manage.py auto_attendance --no-checkout-only
# ===========================================

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = '''
ประมวลผลการเข้าเรียนอัตโนมัติ
กฎ:
  - แตะบัตร 05:30-08:20 = มาปกติ
  - แตะบัตร 08:20-09:00 = มาสาย (หัก 1 คะแนน)
  - ไม่แตะบัตร = ขาด (หัก 2 คะแนน)
  - ทำงานเฉพาะ จ-ศ
'''
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            type=str,
            help='วันที่ต้องการประมวลผล (YYYY-MM-DD) default=วันนี้'
        )
        parser.add_argument(
            '--absent-only',
            action='store_true',
            help='ประมวลผลเฉพาะการขาดเรียน'
        )
        parser.add_argument(
            '--no-checkout-only',
            action='store_true',
            help='ประมวลผลเฉพาะการไม่สแกนออก'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='ทดสอบโดยไม่บันทึกจริง'
        )
        parser.add_argument(
            '--days-back',
            type=int,
            default=0,
            help='จำนวนวันย้อนหลังที่ต้องการประมวลผล'
        )
    
    def handle(self, *args, **options):
        from scanning.behavior_scoring import behavior_scoring_service, BehaviorScoringConfig
        
        self.stdout.write(self.style.NOTICE('='*60))
        self.stdout.write(self.style.NOTICE('⭐ ระบบประมวลผลการเข้าเรียนอัตโนมัติ'))
        self.stdout.write(self.style.NOTICE('='*60))
        
        config = BehaviorScoringConfig()
        dry_run = options['dry_run']
        
        # แสดงการตั้งค่า
        self.stdout.write(f"\n📋 การตั้งค่าปัจจุบัน:")
        self.stdout.write(f"   - เวลามาตรงเวลา: {config.CHECK_IN_START.strftime('%H:%M')} - {config.CHECK_IN_ON_TIME.strftime('%H:%M')}")
        self.stdout.write(f"   - เวลามาสาย: {config.CHECK_IN_ON_TIME.strftime('%H:%M')} - {config.CHECK_IN_LATE_END.strftime('%H:%M')}")
        self.stdout.write(f"   - คะแนนหักมาสาย: {config.LATE_DEDUCTION} คะแนน")
        self.stdout.write(f"   - คะแนนหักขาด: {config.ABSENT_DEDUCTION} คะแนน")
        self.stdout.write(f"   - คะแนนหักไม่สแกนออก: {config.NO_CHECKOUT_DEDUCTION} คะแนน")
        self.stdout.write(f"   - วันเรียน: จันทร์ - ศุกร์")
        
        if dry_run:
            self.stdout.write(self.style.WARNING('\n🔍 โหมดทดสอบ (DRY RUN) - ไม่บันทึกจริง'))
        
        # กำหนดวันที่
        if options['date']:
            try:
                target_date = datetime.strptime(options['date'], '%Y-%m-%d').date()
            except ValueError:
                raise CommandError('รูปแบบวันที่ไม่ถูกต้อง (ใช้ YYYY-MM-DD)')
        else:
            target_date = timezone.now().date()
        
        # ถ้าระบุ days_back
        days_back = options['days_back']
        if days_back > 0:
            dates_to_process = []
            for i in range(days_back + 1):
                date = target_date - timedelta(days=i)
                dates_to_process.append(date)
            dates_to_process.reverse()
        else:
            dates_to_process = [target_date]
        
        total_results = {
            'dates_processed': 0,
            'total_absent': 0,
            'total_no_checkout': 0,
            'weekends_skipped': 0
        }
        
        for process_date in dates_to_process:
            self.stdout.write(f"\n📅 ประมวลผลวันที่: {process_date}")
            self.stdout.write(f"   วัน: {self._get_thai_day(process_date.weekday())}")
            
            # ตรวจสอบวันเรียน
            if not config.is_school_day(process_date):
                self.stdout.write(self.style.WARNING(f"   ⏭️ ข้าม - ไม่ใช่วันเรียน (เสาร์-อาทิตย์)"))
                total_results['weekends_skipped'] += 1
                continue
            
            total_results['dates_processed'] += 1
            
            if dry_run:
                # โหมดทดสอบ - แสดงข้อมูลแต่ไม่บันทึก
                self._dry_run_process(process_date, options, config)
            else:
                # ประมวลผลจริง
                absent_only = options['absent_only']
                no_checkout_only = options['no_checkout_only']
                
                if absent_only:
                    result = behavior_scoring_service.process_daily_absent(process_date)
                    self._display_absent_result(result)
                    total_results['total_absent'] += result.get('absent_count', 0)
                elif no_checkout_only:
                    result = behavior_scoring_service.process_daily_no_checkout(process_date)
                    self._display_no_checkout_result(result)
                    total_results['total_no_checkout'] += result.get('no_checkout_count', 0)
                else:
                    # ประมวลผลทั้งหมด
                    result = behavior_scoring_service.process_daily_behavior_scores(process_date)
                    if 'absent' in result:
                        self._display_absent_result(result['absent'])
                        total_results['total_absent'] += result['absent'].get('absent_count', 0)
                    if 'no_checkout' in result:
                        self._display_no_checkout_result(result['no_checkout'])
                        total_results['total_no_checkout'] += result['no_checkout'].get('no_checkout_count', 0)
        
        # สรุปผล
        self.stdout.write(self.style.SUCCESS('\n' + '='*60))
        self.stdout.write(self.style.SUCCESS('📊 สรุปผลการประมวลผล'))
        self.stdout.write(self.style.SUCCESS('='*60))
        self.stdout.write(f"   วันที่ประมวลผล: {total_results['dates_processed']} วัน")
        self.stdout.write(f"   วันหยุดที่ข้าม: {total_results['weekends_skipped']} วัน")
        self.stdout.write(f"   นักเรียนขาดเรียน: {total_results['total_absent']} คน")
        self.stdout.write(f"   นักเรียนไม่สแกนออก: {total_results['total_no_checkout']} คน")
        
        if dry_run:
            self.stdout.write(self.style.WARNING('\n🔍 นี่คือโหมดทดสอบ - ไม่มีข้อมูลถูกบันทึกจริง'))
        else:
            self.stdout.write(self.style.SUCCESS('\n✅ ประมวลผลเสร็จสิ้น!'))
    
    def _get_thai_day(self, weekday):
        """แปลงเลขวันเป็นชื่อวันภาษาไทย"""
        days = ['จันทร์', 'อังคาร', 'พุธ', 'พฤหัสบดี', 'ศุกร์', 'เสาร์', 'อาทิตย์']
        return days[weekday]
    
    def _dry_run_process(self, target_date, options, config):
        """ทดสอบการประมวลผลโดยไม่บันทึกจริง"""
        from my.models import Student
        from scanning.models import RFIDScanLog
        from datetime import time
        
        start_of_day = datetime.combine(target_date, time.min)
        end_of_day = datetime.combine(target_date, time.max)
        
        active_students = Student.objects.filter(is_active=True)
        
        absent_only = options['absent_only']
        no_checkout_only = options['no_checkout_only']
        
        if not no_checkout_only:
            # ตรวจสอบการขาดเรียน
            self.stdout.write(f"\n   🔍 ตรวจสอบการขาดเรียน:")
            absent_count = 0
            for student in active_students:
                has_checkin = RFIDScanLog.objects.filter(
                    student=student,
                    scan_type='check_in',
                    scan_time__range=(start_of_day, end_of_day),
                    status='success'
                ).exists()
                
                if not has_checkin:
                    absent_count += 1
                    if absent_count <= 10:  # แสดงแค่ 10 คนแรก
                        self.stdout.write(f"      - {student.student_id} {student.get_full_name()} (จะหัก {config.ABSENT_DEDUCTION} คะแนน)")
            
            if absent_count > 10:
                self.stdout.write(f"      ... และอีก {absent_count - 10} คน")
            self.stdout.write(self.style.WARNING(f"      รวมขาดเรียน: {absent_count} คน"))
        
        if not absent_only:
            # ตรวจสอบไม่สแกนออก
            self.stdout.write(f"\n   🔍 ตรวจสอบไม่สแกนออก:")
            no_checkout_count = 0
            
            students_with_checkin = RFIDScanLog.objects.filter(
                scan_type='check_in',
                scan_time__range=(start_of_day, end_of_day),
                status='success'
            ).values_list('student_id', flat=True).distinct()
            
            for student_id in students_with_checkin:
                has_checkout = RFIDScanLog.objects.filter(
                    student_id=student_id,
                    scan_type='check_out',
                    scan_time__range=(start_of_day, end_of_day),
                    status='success'
                ).exists()
                
                if not has_checkout:
                    no_checkout_count += 1
                    if no_checkout_count <= 10:
                        student = Student.objects.get(id=student_id)
                        self.stdout.write(f"      - {student.student_id} {student.get_full_name()} (จะหัก {config.NO_CHECKOUT_DEDUCTION} คะแนน)")
            
            if no_checkout_count > 10:
                self.stdout.write(f"      ... และอีก {no_checkout_count - 10} คน")
            self.stdout.write(self.style.WARNING(f"      รวมไม่สแกนออก: {no_checkout_count} คน"))
    
    def _display_absent_result(self, result):
        """แสดงผลการประมวลผลขาดเรียน"""
        self.stdout.write(f"\n   📋 ผลการประมวลผลขาดเรียน:")
        self.stdout.write(f"      - นักเรียนทั้งหมด: {result.get('total_students', 0)} คน")
        self.stdout.write(f"      - ขาดเรียน: {result.get('absent_count', 0)} คน")
        self.stdout.write(f"      - ประมวลผลไปแล้ว: {result.get('already_processed', 0)} คน")
        
        if result.get('details'):
            self.stdout.write(f"\n      รายชื่อนักเรียนขาดเรียน:")
            for detail in result['details'][:10]:  # แสดงแค่ 10 คนแรก
                self.stdout.write(f"         - {detail['student_id']} {detail['name']} (หัก {detail['points_deducted']} คะแนน)")
            if len(result['details']) > 10:
                self.stdout.write(f"         ... และอีก {len(result['details']) - 10} คน")
    
    def _display_no_checkout_result(self, result):
        """แสดงผลการประมวลผลไม่สแกนออก"""
        self.stdout.write(f"\n   📋 ผลการประมวลผลไม่สแกนออก:")
        self.stdout.write(f"      - ไม่สแกนออก: {result.get('no_checkout_count', 0)} คน")
        self.stdout.write(f"      - ประมวลผลไปแล้ว: {result.get('already_processed', 0)} คน")
        
        if result.get('details'):
            self.stdout.write(f"\n      รายชื่อนักเรียนไม่สแกนออก:")
            for detail in result['details'][:10]:
                self.stdout.write(f"         - {detail['student_id']} {detail['name']} (หัก {detail['points_deducted']} คะแนน)")
            if len(result['details']) > 10:
                self.stdout.write(f"         ... และอีก {len(result['details']) - 10} คน")


