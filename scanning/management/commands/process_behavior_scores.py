# scanning/management/commands/process_behavior_scores.py
# ⭐ Management Command สำหรับประมวลผลคะแนนพฤติกรรมอัตโนมัติ
#
# ===========================================
# วิธีใช้งาน:
# ===========================================
#   python manage.py process_behavior_scores                    # ประมวลผลวันนี้
#   python manage.py process_behavior_scores --date=2025-01-15  # ระบุวันที่
#   python manage.py process_behavior_scores --dry-run          # ทดสอบโดยไม่บันทึก
#   python manage.py process_behavior_scores --grade=6          # เฉพาะชั้น ป.6
#   python manage.py process_behavior_scores --classroom=1      # เฉพาะห้อง 1
#   python manage.py process_behavior_scores --week             # ประมวลผล 7 วันย้อนหลัง
#   python manage.py process_behavior_scores --force            # บังคับประมวลผลใหม่
#
# ===========================================
# ตั้งเวลาอัตโนมัติด้วย Cron (Linux/Mac):
# ===========================================
#   # รันทุกวันจันทร์-ศุกร์ เวลา 17:30 น.
#   30 17 * * 1-5 cd /path/to/project && python manage.py process_behavior_scores
#
# ===========================================
# ตั้งเวลาอัตโนมัติด้วย Windows Task Scheduler:
# ===========================================
#   Program: C:\Python\python.exe
#   Arguments: manage.py process_behavior_scores
#   Start in: C:\path\to\your\project
#   Trigger: Daily, 17:30, Monday-Friday

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.db import models
from datetime import datetime, time, timedelta
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = '''ประมวลผลคะแนนพฤติกรรมนักเรียนอัตโนมัติ
    
กฎการหักคะแนน:
  - มาตรงเวลา (05:30 - 08:20): ไม่หักคะแนน
  - มาสาย (08:21 - 09:00): หัก 1 คะแนน  
  - ขาดเรียน (ไม่มาสแกน/มาหลัง 09:00): หัก 2 คะแนน
  - ไม่สแกนออก: หัก 1 คะแนน
'''
    
    def add_arguments(self, parser):
        """กำหนด arguments ที่รับได้"""
        parser.add_argument(
            '--date',
            type=str,
            help='วันที่ต้องการประมวลผล (รูปแบบ YYYY-MM-DD)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='ทดสอบการทำงานโดยไม่บันทึกลงฐานข้อมูล',
        )
        parser.add_argument(
            '--grade',
            type=int,
            help='ระบุระดับชั้นที่ต้องการประมวลผล (1-6)',
            choices=[1, 2, 3, 4, 5, 6],
        )
        parser.add_argument(
            '--classroom',
            type=str,
            help='ระบุห้องเรียน (เช่น 1, 2, 3)',
        )
        parser.add_argument(
            '--week',
            action='store_true',
            help='ประมวลผล 7 วันย้อนหลัง',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='บังคับประมวลผลใหม่แม้จะมีข้อมูลแล้ว',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='แสดงรายละเอียดเพิ่มเติม',
        )
    
    def handle(self, *args, **options):
        """ฟังก์ชันหลักของ command"""
        from my.models import Student, BehaviorRecord
        from scanning.models import FaceRecognitionLog, RFIDScanLog
        from scanning.behavior_scoring import BehaviorScoringConfig, behavior_scoring_service
        
        config = BehaviorScoringConfig()
        
        # แสดงหัวข้อ
        self.stdout.write('')
        self.stdout.write(self.style.NOTICE('=' * 70))
        self.stdout.write(self.style.NOTICE('🎯 ระบบประมวลผลคะแนนพฤติกรรมอัตโนมัติ'))
        self.stdout.write(self.style.NOTICE('   One Card Smart School V.2'))
        self.stdout.write(self.style.NOTICE('=' * 70))
        self.stdout.write('')
        
        # ตรวจสอบว่าเปิดใช้งานหรือไม่
        if not config.AUTO_ENABLED:
            self.stdout.write(self.style.WARNING('⏸️ ระบบหักคะแนนอัตโนมัติถูกปิด'))
            self.stdout.write(self.style.WARNING('   สามารถเปิดได้ที่ การตั้งค่าโรงเรียน'))
            return
        
        # ตรวจสอบโหมด dry-run
        dry_run = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING('🔸 โหมดทดสอบ (DRY-RUN) - ไม่บันทึกข้อมูลจริง'))
            self.stdout.write('')
        
        # กำหนดวันที่
        if options['week']:
            self._process_week(options, config)
            return
        
        if options['date']:
            try:
                target_date = datetime.strptime(options['date'], '%Y-%m-%d').date()
            except ValueError:
                raise CommandError('❌ รูปแบบวันที่ไม่ถูกต้อง กรุณาใช้ YYYY-MM-DD')
        else:
            target_date = timezone.now().date()
        
        # ประมวลผลวันที่ระบุ
        self._process_single_day(target_date, options, config)
    
    def _process_single_day(self, target_date, options, config):
        """ประมวลผลวันเดียว"""
        from my.models import Student, BehaviorRecord
        from scanning.models import FaceRecognitionLog
        
        # แสดงข้อมูลพื้นฐาน
        day_names = ['จันทร์', 'อังคาร', 'พุธ', 'พฤหัสบดี', 'ศุกร์', 'เสาร์', 'อาทิตย์']
        self.stdout.write(f'📅 วันที่ประมวลผล: {target_date.strftime("%d/%m/%Y")}')
        self.stdout.write(f'📆 วัน{day_names[target_date.weekday()]}')
        self.stdout.write('')
        
        # ตรวจสอบว่าเป็นวันเรียนหรือไม่
        if target_date.weekday() not in config.SCHOOL_DAYS:
            self.stdout.write(
                self.style.WARNING(
                    f'⚠️ วันที่ {target_date.strftime("%d/%m/%Y")} ไม่ใช่วันเรียน - ข้ามการประมวลผล'
                )
            )
            return
        
        # แสดงกฎการหักคะแนน
        self._display_scoring_rules(config)
        
        # ดึงรายชื่อนักเรียน
        students = Student.objects.filter(is_active=True)
        
        # กรองตาม grade
        if options['grade']:
            students = students.filter(grade=str(options['grade']))
            self.stdout.write(self.style.SUCCESS(f'🎒 กรองเฉพาะชั้น ป.{options["grade"]}'))
        
        # กรองตาม classroom
        if options['classroom']:
            students = students.filter(classroom=options['classroom'])
            self.stdout.write(self.style.SUCCESS(f'🏫 กรองเฉพาะห้อง {options["classroom"]}'))
        
        total_students = students.count()
        self.stdout.write(f'👥 จำนวนนักเรียน: {total_students} คน')
        self.stdout.write('')
        
        # ตัวแปรสรุป
        stats = {
            'present': 0,
            'late': 0,
            'absent': 0,
            'no_checkout': 0,
            'total_deducted': 0,
            'already_processed': 0,
        }
        
        details = []
        dry_run = options['dry_run']
        force = options['force']
        verbose = options.get('verbose', False)
        
        # แสดง progress
        self.stdout.write(self.style.NOTICE('📊 กำลังประมวลผล...'))
        self.stdout.write('')
        
        # ประมวลผลทีละคน
        for index, student in enumerate(students, 1):
            # แสดง progress ทุก 50 คน
            if index % 50 == 0:
                self.stdout.write(f'  ⏳ ประมวลผลไปแล้ว: {index}/{total_students}')
            
            # ตรวจสอบว่าประมวลผลซ้ำหรือไม่
            if not force:
                existing_absent = BehaviorRecord.objects.filter(
                    student=student,
                    date_recorded=target_date,
                    is_auto=True,
                    auto_type='absent'
                ).exists()
                
                existing_no_checkout = BehaviorRecord.objects.filter(
                    student=student,
                    date_recorded=target_date,
                    is_auto=True,
                    auto_type='no_checkout'
                ).exists()
                
                # ถ้าประมวลผลทั้ง 2 แล้ว ให้ข้าม
                if existing_absent or existing_no_checkout:
                    stats['already_processed'] += 1
                    continue
            
            # ดึงข้อมูลการสแกนเข้า
            check_in = FaceRecognitionLog.objects.filter(
                student=student,
                created_at__date=target_date,
                status='success',
                rfid_scan_log__scan_type='check_in'
            ).order_by('created_at').first()
            
            # ดึงข้อมูลการสแกนออก
            check_out = FaceRecognitionLog.objects.filter(
                student=student,
                created_at__date=target_date,
                status='success',
                rfid_scan_log__scan_type='check_out'
            ).order_by('created_at').first()
            
            points_deducted = 0
            reasons = []
            status_text = 'present'
            auto_types = []
            
            # ========================================
            # วิเคราะห์สถานะและหักคะแนน
            # ========================================
            
            if not check_in:
                # กรณี 1: ขาดเรียน (ไม่มีการสแกนเข้าเลย)
                # ตรวจสอบว่ามี late record หรือไม่ (ถ้ามีแสดงว่าสแกนแล้วแต่สาย)
                has_late_record = BehaviorRecord.objects.filter(
                    student=student,
                    date_recorded=target_date,
                    is_auto=True,
                    auto_type='late'
                ).exists()
                
                if not has_late_record:
                    status_text = 'absent'
                    points_deducted += config.ABSENT_DEDUCTION
                    reasons.append(f'ขาดเรียน (หัก {config.ABSENT_DEDUCTION})')
                    auto_types.append('absent')
                    stats['absent'] += 1
                else:
                    # มี late record แล้ว แสดงว่าสแกนสายไปแล้ว
                    status_text = 'late'
                    stats['late'] += 1
            else:
                scan_time = check_in.created_at.time()
                check_in_on_time = config.CHECK_IN_ON_TIME
                check_in_late_end = config.CHECK_IN_LATE_END
                
                # แปลง time object ถ้าจำเป็น
                if isinstance(check_in_on_time, str):
                    h, m = map(int, check_in_on_time.split(':'))
                    check_in_on_time = time(h, m)
                if isinstance(check_in_late_end, str):
                    h, m = map(int, check_in_late_end.split(':'))
                    check_in_late_end = time(h, m)
                
                if scan_time > check_in_late_end:
                    # กรณี 2: มาสายมาก (หลัง 09:00) = ขาด
                    # ตรวจสอบว่าถูกหักไปแล้วตอน real-time หรือยัง
                    has_absent_record = BehaviorRecord.objects.filter(
                        student=student,
                        date_recorded=target_date,
                        is_auto=True,
                        auto_type='absent'
                    ).exists()
                    
                    if not has_absent_record:
                        status_text = 'absent'
                        points_deducted += config.ABSENT_DEDUCTION
                        reasons.append(f'มาหลัง 09:00 น. (หัก {config.ABSENT_DEDUCTION})')
                        auto_types.append('absent')
                    stats['absent'] += 1
                    
                elif scan_time > check_in_on_time:
                    # กรณี 3: มาสาย (08:21 - 09:00)
                    # มักจะถูกหักไปแล้วตอน real-time
                    status_text = 'late'
                    stats['late'] += 1
                else:
                    # มาตรงเวลา
                    stats['present'] += 1
            
            # ตรวจสอบการสแกนออก (เฉพาะคนที่มาเรียน)
            if check_in and not check_out:
                now = timezone.now()
                check_out_end = config.CHECK_OUT_END
                
                # แปลง time object ถ้าจำเป็น
                if isinstance(check_out_end, str):
                    h, m = map(int, check_out_end.split(':'))
                    check_out_end = time(h, m)
                
                # ตรวจสอบว่าหมดเวลาสแกนออกแล้ว
                if target_date < now.date() or \
                   (target_date == now.date() and now.time() > check_out_end):
                    # ตรวจสอบว่าถูกหักไปแล้วหรือยัง
                    has_no_checkout = BehaviorRecord.objects.filter(
                        student=student,
                        date_recorded=target_date,
                        is_auto=True,
                        auto_type='no_checkout'
                    ).exists()
                    
                    if not has_no_checkout:
                        points_deducted += config.EARLY_LEAVE_DEDUCTION
                        reasons.append(f'ไม่สแกนออก (หัก {config.EARLY_LEAVE_DEDUCTION})')
                        auto_types.append('no_checkout')
                    stats['no_checkout'] += 1
            
            # บันทึกคะแนน
            if points_deducted > 0 and not dry_run:
                for i, auto_type in enumerate(auto_types):
                    reason_text = f'[AUTO] {reasons[i]}'
                    
                    # กำหนดคะแนนตาม auto_type
                    if auto_type == 'absent':
                        pts = config.ABSENT_DEDUCTION
                    elif auto_type == 'late':
                        pts = config.LATE_DEDUCTION
                    else:
                        pts = config.EARLY_LEAVE_DEDUCTION
                    
                    # สร้าง BehaviorRecord
                    BehaviorRecord.objects.create(
                        student=student,
                        behavior_type='deduct',  # ⭐ ใช้ 'deduct' ตาม models.py
                        points=pts,
                        reason=reason_text,
                        date_recorded=target_date,
                        recorded_by=None,
                        is_auto=True,
                        auto_type=auto_type
                    )
                
                # รีเฟรชคะแนน
                student.refresh_from_db()
                stats['total_deducted'] += points_deducted
                
                details.append({
                    'student_id': student.student_id,
                    'name': f'{student.first_name} {student.last_name}',
                    'grade': f'ป.{student.grade}/{student.classroom}',
                    'status': status_text,
                    'points': -points_deducted,
                    'reason': ', '.join(reasons),
                    'new_score': student.behavior_score
                })
            elif points_deducted > 0 and dry_run:
                # Dry-run mode - แสดงแต่ไม่บันทึก
                details.append({
                    'student_id': student.student_id,
                    'name': f'{student.first_name} {student.last_name}',
                    'grade': f'ป.{student.grade}/{student.classroom}',
                    'status': status_text,
                    'points': -points_deducted,
                    'reason': ', '.join(reasons),
                    'new_score': student.behavior_score - points_deducted
                })
                stats['total_deducted'] += points_deducted
        
        # ========================================
        # แสดงผลลัพธ์
        # ========================================
        self._display_results(stats, details, dry_run, force)
    
    def _display_scoring_rules(self, config):
        """แสดงกฎการหักคะแนน"""
        # แปลง time เป็น string
        def time_str(t):
            if hasattr(t, 'strftime'):
                return t.strftime('%H:%M')
            return str(t)
        
        self.stdout.write(self.style.NOTICE('📋 กฎการหักคะแนน:'))
        self.stdout.write(f'   ✅ มาตรงเวลา ({time_str(config.CHECK_IN_START)} - {time_str(config.CHECK_IN_ON_TIME)}): ไม่หักคะแนน')
        self.stdout.write(f'   ⏰ มาสาย ({time_str(config.CHECK_IN_ON_TIME)} - {time_str(config.CHECK_IN_LATE_END)}): หัก {config.LATE_DEDUCTION} คะแนน')
        self.stdout.write(f'   ❌ ขาดเรียน (ไม่มาสแกน หรือ มาหลัง {time_str(config.CHECK_IN_LATE_END)}): หัก {config.ABSENT_DEDUCTION} คะแนน')
        self.stdout.write(f'   🚪 ไม่สแกนออก (ไม่มีการสแกนออกก่อน {time_str(config.CHECK_OUT_END)}): หัก {config.EARLY_LEAVE_DEDUCTION} คะแนน')
        self.stdout.write('')
    
    def _display_results(self, stats, details, dry_run, force):
        """แสดงผลลัพธ์การประมวลผล"""
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=' * 70))
        self.stdout.write(self.style.SUCCESS('📊 สรุปผลการประมวลผล'))
        self.stdout.write(self.style.SUCCESS('=' * 70))
        self.stdout.write('')
        
        self.stdout.write(f'✅ มาตรงเวลา: {stats["present"]} คน')
        self.stdout.write(self.style.WARNING(f'⏰ มาสาย: {stats["late"]} คน'))
        self.stdout.write(self.style.ERROR(f'❌ ขาดเรียน: {stats["absent"]} คน'))
        self.stdout.write(self.style.WARNING(f'🚪 ไม่สแกนออก: {stats["no_checkout"]} คน'))
        
        if not force:
            self.stdout.write(f'🔄 ประมวลผลแล้ว (ข้าม): {stats["already_processed"]} คน')
        
        self.stdout.write('')
        self.stdout.write(
            self.style.ERROR(f'📉 รวมคะแนนที่หัก: {stats["total_deducted"]} คะแนน')
        )
        
        # แสดงรายละเอียด
        if details:
            self.stdout.write('')
            self.stdout.write(self.style.NOTICE('📋 รายละเอียดการหักคะแนน:'))
            self.stdout.write('-' * 70)
            
            for d in details:
                # สีตามสถานะ
                if d['status'] == 'absent':
                    style = self.style.ERROR
                    status_icon = '❌'
                elif d['status'] == 'late':
                    style = self.style.WARNING
                    status_icon = '⏰'
                else:
                    style = self.style.WARNING
                    status_icon = '🚪'
                
                self.stdout.write(
                    style(
                        f"  {status_icon} {d['student_id']:10} | {d['name']:20} | "
                        f"{d['grade']:8} | {d['points']:+3d} คะแนน | "
                        f"คงเหลือ {d['new_score']:3d}"
                    )
                )
        
        self.stdout.write('')
        
        # แสดงข้อความสรุป
        if dry_run:
            self.stdout.write(
                self.style.WARNING('🔸 โหมดทดสอบ - ไม่มีการบันทึกข้อมูลจริง')
            )
        else:
            if stats['total_deducted'] > 0:
                self.stdout.write(
                    self.style.SUCCESS('✅ บันทึกข้อมูลเรียบร้อยแล้ว')
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS('✅ ไม่มีนักเรียนที่ต้องหักคะแนน')
                )
        
        self.stdout.write(self.style.NOTICE('=' * 70))
        self.stdout.write('')
    
    def _process_week(self, options, config):
        """ประมวลผล 7 วันย้อนหลัง"""
        from scanning.behavior_scoring import behavior_scoring_service
        
        end_date = timezone.now().date()
        self.stdout.write(
            self.style.NOTICE(f'📅 ประมวลผล 7 วันย้อนหลัง (ถึง {end_date.strftime("%d/%m/%Y")})')
        )
        self.stdout.write('')
        
        total_deducted = 0
        total_absent = 0
        total_no_checkout = 0
        
        for i in range(7):
            check_date = end_date - timedelta(days=i)
            
            # ข้ามวันหยุด
            if check_date.weekday() not in config.SCHOOL_DAYS:
                continue
            
            day_names = ['จันทร์', 'อังคาร', 'พุธ', 'พฤหัสบดี', 'ศุกร์', 'เสาร์', 'อาทิตย์']
            self.stdout.write(
                self.style.NOTICE(f'📅 {check_date.strftime("%d/%m/%Y")} (วัน{day_names[check_date.weekday()]})')
            )
            
            if not options['dry_run']:
                result = behavior_scoring_service.process_daily_behavior_scores(check_date)
                
                absent = result.get('absent_count', 0)
                no_checkout = result.get('no_checkout_count', 0)
                deducted = result.get('total_points_deducted', 0)
                
                total_absent += absent
                total_no_checkout += no_checkout
                total_deducted += deducted
                
                self.stdout.write(
                    f"   ❌ ขาด: {absent}, "
                    f"🚪 ไม่สแกนออก: {no_checkout}, "
                    f"📉 หักรวม: {deducted} คะแนน"
                )
            else:
                self.stdout.write('   🔸 โหมดทดสอบ - ไม่ประมวลผลจริง')
            
            self.stdout.write('')
        
        self.stdout.write(self.style.SUCCESS('=' * 70))
        self.stdout.write(self.style.SUCCESS('📊 สรุปผลรวม 7 วัน'))
        self.stdout.write(self.style.SUCCESS('=' * 70))
        self.stdout.write(self.style.ERROR(f'❌ ขาดเรียนรวม: {total_absent} คน'))
        self.stdout.write(self.style.WARNING(f'🚪 ไม่สแกนออกรวม: {total_no_checkout} คน'))
        self.stdout.write(self.style.ERROR(f'📉 หักคะแนนรวมทั้งหมด: {total_deducted} คะแนน'))
        self.stdout.write(self.style.SUCCESS('=' * 70))