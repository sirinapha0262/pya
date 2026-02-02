# scanning/performance_tracker.py
# ⭐ ระบบติดตามและวัดประสิทธิภาพของระบบสแกน RFID + Face Recognition
#
# การใช้งาน:
#   from scanning.performance_tracker import PerformanceTracker
#   tracker = PerformanceTracker()
#   summary = tracker.get_performance_summary()
#
# ตัวชี้วัดหลัก (KPIs):
#   1. เวลาประมวลผลเฉลี่ย (Target: < 3 วินาที)
#   2. อัตราความสำเร็จ Face Recognition (Target: > 95%)
#   3. Throughput (Target: > 300 คน/ชั่วโมง)
#   4. อัตราความเสถียรอุปกรณ์ (Target: > 99%)
#   5. Cache Hit Rate (Target: > 80%)

from django.utils import timezone
from django.db.models import Avg, Count, Min, Max, Q, F
from django.db.models.functions import TruncHour, TruncDate
from datetime import datetime, timedelta, time
import logging

logger = logging.getLogger(__name__)


class PerformanceTracker:
    """
    ติดตามและวัดประสิทธิภาพของระบบสแกน RFID + Face Recognition
    
    Metrics:
    - Accuracy: อัตราความสำเร็จของการสแกน
    - Processing Time: เวลาประมวลผลเฉลี่ย
    - Throughput: จำนวนคนที่สแกนได้ต่อชั่วโมง
    - Device Reliability: ความเสถียรของอุปกรณ์
    - Cache Performance: ประสิทธิภาพของ cache
    """
    
    # ค่าเป้าหมาย (Targets)
    TARGET_PROCESSING_TIME = 3.0  # วินาที
    TARGET_ACCURACY = 95.0  # เปอร์เซ็นต์
    TARGET_THROUGHPUT = 300  # คน/ชั่วโมง
    TARGET_DEVICE_RELIABILITY = 99.0  # เปอร์เซ็นต์
    TARGET_CACHE_HIT_RATE = 80.0  # เปอร์เซ็นต์
    
    def __init__(self):
        self._import_models()
    
    def _import_models(self):
        """Import models lazily to avoid circular imports"""
        from .models import RFIDScanLog, FaceRecognitionLog, DeviceStatus
        self.RFIDScanLog = RFIDScanLog
        self.FaceRecognitionLog = FaceRecognitionLog
        self.DeviceStatus = DeviceStatus
    
    # ========================================
    # 1. Accuracy Metrics (ความแม่นยำ)
    # ========================================
    
    def calculate_scan_accuracy(self, date=None):
        """
        คำนวณอัตราความสำเร็จของการสแกน RFID
        
        Args:
            date: วันที่ต้องการคำนวณ (default=วันนี้)
            
        Returns:
            dict: {
                'total': จำนวนการสแกนทั้งหมด,
                'success': สแกนสำเร็จ,
                'failed': สแกนล้มเหลว,
                'accuracy_percent': อัตราความสำเร็จ (%)
            }
        """
        if date is None:
            date = timezone.now().date()
        
        logs = self.RFIDScanLog.objects.filter(scan_time__date=date)
        
        total = logs.count()
        success = logs.filter(status='success').count()
        failed = total - success
        
        accuracy = (success / total * 100) if total > 0 else 0
        
        return {
            'date': str(date),
            'total': total,
            'success': success,
            'failed': failed,
            'accuracy_percent': round(accuracy, 2),
            'meets_target': accuracy >= self.TARGET_ACCURACY
        }
    
    def calculate_face_recognition_accuracy(self, date=None):
        """
        คำนวณอัตราความสำเร็จของการจดจำใบหน้า
        
        Args:
            date: วันที่ต้องการคำนวณ (default=วันนี้)
            
        Returns:
            dict: ผลการคำนวณ
        """
        if date is None:
            date = timezone.now().date()
        
        logs = self.FaceRecognitionLog.objects.filter(created_at__date=date)
        
        total = logs.count()
        success = logs.filter(status='success').count()
        no_face = logs.filter(status='no_face').count()
        mismatch = logs.filter(status='mismatch').count()
        error = logs.filter(status='error').count()
        manual = logs.filter(status='manual').count()
        
        # คำนวณ accuracy (ไม่นับ manual)
        non_manual = total - manual
        accuracy = (success / non_manual * 100) if non_manual > 0 else 0
        
        # คำนวณค่า confidence เฉลี่ย
        avg_confidence = logs.filter(
            status='success',
            confidence_score__isnull=False
        ).aggregate(avg=Avg('confidence_score'))['avg'] or 0
        
        return {
            'date': str(date),
            'total': total,
            'success': success,
            'no_face': no_face,
            'mismatch': mismatch,
            'error': error,
            'manual': manual,
            'accuracy_percent': round(accuracy, 2),
            'avg_confidence': round(avg_confidence, 2),
            'meets_target': accuracy >= self.TARGET_ACCURACY
        }
    
    # ========================================
    # 2. Processing Time Metrics (ความเร็ว)
    # ========================================
    
    def calculate_avg_processing_time(self, date=None):
        """
        คำนวณเวลาประมวลผลเฉลี่ย
        
        Args:
            date: วันที่ต้องการคำนวณ (default=วันนี้)
            
        Returns:
            dict: ผลการคำนวณเวลาประมวลผล
        """
        if date is None:
            date = timezone.now().date()
        
        # RFID Processing Time
        rfid_stats = self.RFIDScanLog.objects.filter(
            scan_time__date=date,
            processing_time__isnull=False
        ).aggregate(
            avg=Avg('processing_time'),
            min=Min('processing_time'),
            max=Max('processing_time'),
            count=Count('id')
        )
        
        # Face Recognition Processing Time
        face_stats = self.FaceRecognitionLog.objects.filter(
            created_at__date=date,
            processing_time__isnull=False
        ).aggregate(
            avg=Avg('processing_time'),
            min=Min('processing_time'),
            max=Max('processing_time'),
            count=Count('id')
        )
        
        # รวมเวลาเฉลี่ย (RFID + Face)
        rfid_avg = rfid_stats['avg'] or 0
        face_avg = face_stats['avg'] or 0
        total_avg = rfid_avg + face_avg
        
        return {
            'date': str(date),
            'rfid': {
                'avg_seconds': round(rfid_avg, 3),
                'min_seconds': round(rfid_stats['min'] or 0, 3),
                'max_seconds': round(rfid_stats['max'] or 0, 3),
                'sample_count': rfid_stats['count']
            },
            'face_recognition': {
                'avg_seconds': round(face_avg, 3),
                'min_seconds': round(face_stats['min'] or 0, 3),
                'max_seconds': round(face_stats['max'] or 0, 3),
                'sample_count': face_stats['count']
            },
            'total_avg_seconds': round(total_avg, 3),
            'target_seconds': self.TARGET_PROCESSING_TIME,
            'meets_target': total_avg <= self.TARGET_PROCESSING_TIME
        }
    
    # ========================================
    # 3. Throughput Metrics (ปริมาณงาน)
    # ========================================
    
    def calculate_throughput(self, date=None):
        """
        คำนวณ Throughput (จำนวนคนที่สแกนได้ต่อชั่วโมง)
        
        Args:
            date: วันที่ต้องการคำนวณ (default=วันนี้)
            
        Returns:
            dict: ผลการคำนวณ Throughput
        """
        if date is None:
            date = timezone.now().date()
        
        # นับจำนวนการสแกนที่สำเร็จรายชั่วโมง
        hourly_data = self.RFIDScanLog.objects.filter(
            scan_time__date=date,
            status='success'
        ).annotate(
            hour=TruncHour('scan_time')
        ).values('hour').annotate(
            count=Count('id')
        ).order_by('hour')
        
        hourly_counts = {entry['hour'].hour: entry['count'] for entry in hourly_data}
        
        # หาชั่วโมงที่ busy ที่สุด
        peak_hour = None
        peak_count = 0
        for hour, count in hourly_counts.items():
            if count > peak_count:
                peak_count = count
                peak_hour = hour
        
        # คำนวณค่าเฉลี่ย (เฉพาะชั่วโมงที่มีการสแกน)
        active_hours = len(hourly_counts)
        total_scans = sum(hourly_counts.values())
        avg_per_hour = total_scans / active_hours if active_hours > 0 else 0
        
        return {
            'date': str(date),
            'total_successful_scans': total_scans,
            'active_hours': active_hours,
            'avg_per_hour': round(avg_per_hour, 1),
            'peak_hour': f"{peak_hour:02d}:00" if peak_hour is not None else None,
            'peak_count': peak_count,
            'hourly_breakdown': hourly_counts,
            'target_per_hour': self.TARGET_THROUGHPUT,
            'meets_target': peak_count >= self.TARGET_THROUGHPUT
        }
    
    def get_hourly_stats(self, date=None):
        """
        ดึงสถิติรายชั่วโมงแบบละเอียด
        
        Args:
            date: วันที่ต้องการ (default=วันนี้)
            
        Returns:
            list: รายการสถิติรายชั่วโมง
        """
        if date is None:
            date = timezone.now().date()
        
        stats = []
        
        for hour in range(24):
            start_time = timezone.make_aware(
                datetime.combine(date, time(hour, 0, 0))
            )
            end_time = start_time + timedelta(hours=1)
            
            rfid_logs = self.RFIDScanLog.objects.filter(
                scan_time__gte=start_time,
                scan_time__lt=end_time
            )
            
            face_logs = self.FaceRecognitionLog.objects.filter(
                created_at__gte=start_time,
                created_at__lt=end_time
            )
            
            total_rfid = rfid_logs.count()
            success_rfid = rfid_logs.filter(status='success').count()
            total_face = face_logs.count()
            success_face = face_logs.filter(status='success').count()
            
            avg_processing = rfid_logs.filter(
                processing_time__isnull=False
            ).aggregate(avg=Avg('processing_time'))['avg'] or 0
            
            stats.append({
                'hour': f"{hour:02d}:00",
                'rfid_total': total_rfid,
                'rfid_success': success_rfid,
                'rfid_accuracy': round(success_rfid / total_rfid * 100, 1) if total_rfid > 0 else 0,
                'face_total': total_face,
                'face_success': success_face,
                'face_accuracy': round(success_face / total_face * 100, 1) if total_face > 0 else 0,
                'avg_processing_time': round(avg_processing, 3)
            })
        
        return stats
    
    # ========================================
    # 4. Device Reliability Metrics (ความเสถียร)
    # ========================================
    
    def get_device_reliability(self):
        """
        คำนวณความเสถียรของอุปกรณ์
        
        Returns:
            dict: ผลการคำนวณความเสถียร
        """
        devices = self.DeviceStatus.objects.all()
        
        total_devices = devices.count()
        online_devices = devices.filter(status='online').count()
        offline_devices = devices.filter(status='offline').count()
        maintenance_devices = devices.filter(status='maintenance').count()
        error_devices = devices.filter(status='error').count()
        
        # คำนวณ uptime (เฉลี่ยของทุกอุปกรณ์)
        uptime_percent = (online_devices / total_devices * 100) if total_devices > 0 else 0
        
        # สถิติการสแกนรายอุปกรณ์
        device_stats = []
        for device in devices:
            success_rate = 0
            if device.total_scans > 0:
                success_rate = device.successful_scans / device.total_scans * 100
            
            device_stats.append({
                'device_name': device.device_name,
                'status': device.status,
                'total_scans': device.total_scans,
                'successful_scans': device.successful_scans,
                'failed_scans': device.failed_scans,
                'success_rate': round(success_rate, 2),
                'last_heartbeat': str(device.last_heartbeat) if device.last_heartbeat else None
            })
        
        return {
            'total_devices': total_devices,
            'online': online_devices,
            'offline': offline_devices,
            'maintenance': maintenance_devices,
            'error': error_devices,
            'uptime_percent': round(uptime_percent, 2),
            'target_percent': self.TARGET_DEVICE_RELIABILITY,
            'meets_target': uptime_percent >= self.TARGET_DEVICE_RELIABILITY,
            'devices': device_stats
        }
    
    # ========================================
    # 5. Cache Performance Metrics
    # ========================================
    
    def get_cache_performance(self):
        """
        ดึงข้อมูลประสิทธิภาพของ Cache
        
        Note: ต้อง integrate กับ FaceRecognitionService
        
        Returns:
            dict: ข้อมูล cache performance
        """
        try:
            from .face_recognition_service import face_recognition_service
            cache_stats = face_recognition_service.get_cache_stats()
            
            # ถ้ามีการเก็บ hit/miss stats
            hit_rate = 0
            if hasattr(face_recognition_service, '_cache_hits'):
                total_requests = (
                    face_recognition_service._cache_hits + 
                    face_recognition_service._cache_misses
                )
                if total_requests > 0:
                    hit_rate = face_recognition_service._cache_hits / total_requests * 100
            
            return {
                'cached_embeddings': cache_stats.get('cached_embeddings', 0),
                'cache_ttl_seconds': cache_stats.get('cache_ttl_seconds', 0),
                'model': cache_stats.get('model', 'Unknown'),
                'detector': cache_stats.get('detector', 'Unknown'),
                'hit_rate_percent': round(hit_rate, 2),
                'target_hit_rate': self.TARGET_CACHE_HIT_RATE,
                'meets_target': hit_rate >= self.TARGET_CACHE_HIT_RATE
            }
        except Exception as e:
            logger.warning(f"Could not get cache performance: {str(e)}")
            return {
                'error': str(e),
                'cached_embeddings': 0,
                'hit_rate_percent': 0
            }
    
    # ========================================
    # 6. Comprehensive Summary
    # ========================================
    
    def get_performance_summary(self, date=None):
        """
        สรุปประสิทธิภาพโดยรวมของระบบ
        
        Args:
            date: วันที่ต้องการสรุป (default=วันนี้)
            
        Returns:
            dict: ผลสรุปประสิทธิภาพทั้งหมด
        """
        if date is None:
            date = timezone.now().date()
        
        scan_accuracy = self.calculate_scan_accuracy(date)
        face_accuracy = self.calculate_face_recognition_accuracy(date)
        processing_time = self.calculate_avg_processing_time(date)
        throughput = self.calculate_throughput(date)
        device_reliability = self.get_device_reliability()
        cache_performance = self.get_cache_performance()
        
        # คำนวณ Overall Score (weighted average)
        scores = []
        weights = []
        
        # Scan Accuracy (weight: 25%)
        if scan_accuracy['total'] > 0:
            scores.append(min(scan_accuracy['accuracy_percent'] / self.TARGET_ACCURACY * 100, 100))
            weights.append(25)
        
        # Face Recognition Accuracy (weight: 25%)
        if face_accuracy['total'] - face_accuracy['manual'] > 0:
            scores.append(min(face_accuracy['accuracy_percent'] / self.TARGET_ACCURACY * 100, 100))
            weights.append(25)
        
        # Processing Time (weight: 20%)
        if processing_time['rfid']['sample_count'] > 0:
            # ยิ่งเร็วยิ่งดี (inverse)
            time_score = max(0, (self.TARGET_PROCESSING_TIME - processing_time['total_avg_seconds']) / self.TARGET_PROCESSING_TIME * 100 + 100)
            scores.append(min(time_score, 100))
            weights.append(20)
        
        # Throughput (weight: 15%)
        if throughput['peak_count'] > 0:
            scores.append(min(throughput['peak_count'] / self.TARGET_THROUGHPUT * 100, 100))
            weights.append(15)
        
        # Device Reliability (weight: 15%)
        if device_reliability['total_devices'] > 0:
            scores.append(min(device_reliability['uptime_percent'] / self.TARGET_DEVICE_RELIABILITY * 100, 100))
            weights.append(15)
        
        # คำนวณ Overall Score
        overall_score = 0
        if weights:
            overall_score = sum(s * w for s, w in zip(scores, weights)) / sum(weights)
        
        # สรุปปัญหาที่พบ
        issues = []
        if not scan_accuracy.get('meets_target', True):
            issues.append(f"⚠️ Scan accuracy ต่ำ: {scan_accuracy['accuracy_percent']}% (target: {self.TARGET_ACCURACY}%)")
        if not face_accuracy.get('meets_target', True):
            issues.append(f"⚠️ Face accuracy ต่ำ: {face_accuracy['accuracy_percent']}% (target: {self.TARGET_ACCURACY}%)")
        if not processing_time.get('meets_target', True):
            issues.append(f"⚠️ Processing time สูง: {processing_time['total_avg_seconds']}s (target: {self.TARGET_PROCESSING_TIME}s)")
        if not throughput.get('meets_target', True):
            issues.append(f"⚠️ Throughput ต่ำ: {throughput['peak_count']}/hour (target: {self.TARGET_THROUGHPUT}/hour)")
        if not device_reliability.get('meets_target', True):
            issues.append(f"⚠️ Device uptime ต่ำ: {device_reliability['uptime_percent']}% (target: {self.TARGET_DEVICE_RELIABILITY}%)")
        
        return {
            'date': str(date),
            'generated_at': str(timezone.now()),
            'overall_score': round(overall_score, 1),
            'overall_status': 'Good' if overall_score >= 80 else 'Warning' if overall_score >= 60 else 'Critical',
            'metrics': {
                'scan_accuracy': scan_accuracy,
                'face_recognition_accuracy': face_accuracy,
                'processing_time': processing_time,
                'throughput': throughput,
                'device_reliability': device_reliability,
                'cache_performance': cache_performance
            },
            'issues': issues,
            'targets': {
                'processing_time_seconds': self.TARGET_PROCESSING_TIME,
                'accuracy_percent': self.TARGET_ACCURACY,
                'throughput_per_hour': self.TARGET_THROUGHPUT,
                'device_reliability_percent': self.TARGET_DEVICE_RELIABILITY,
                'cache_hit_rate_percent': self.TARGET_CACHE_HIT_RATE
            }
        }
    
    # ========================================
    # 7. Historical Analysis
    # ========================================
    
    def get_weekly_trend(self):
        """
        ดึงแนวโน้มประสิทธิภาพ 7 วันย้อนหลัง
        
        Returns:
            list: ข้อมูลรายวัน 7 วัน
        """
        today = timezone.now().date()
        trend = []
        
        for i in range(7):
            date = today - timedelta(days=i)
            
            # คำนวณเฉพาะข้อมูลหลัก
            rfid_logs = self.RFIDScanLog.objects.filter(scan_time__date=date)
            face_logs = self.FaceRecognitionLog.objects.filter(created_at__date=date)
            
            total_rfid = rfid_logs.count()
            success_rfid = rfid_logs.filter(status='success').count()
            success_face = face_logs.filter(status='success').count()
            total_face = face_logs.exclude(status='manual').count()
            
            avg_time = rfid_logs.filter(
                processing_time__isnull=False
            ).aggregate(avg=Avg('processing_time'))['avg'] or 0
            
            trend.append({
                'date': str(date),
                'day_name': date.strftime('%A'),
                'total_scans': total_rfid,
                'scan_accuracy': round(success_rfid / total_rfid * 100, 1) if total_rfid > 0 else 0,
                'face_accuracy': round(success_face / total_face * 100, 1) if total_face > 0 else 0,
                'avg_processing_time': round(avg_time, 3)
            })
        
        return trend
    
    def get_comparison_with_previous(self, date=None):
        """
        เปรียบเทียบกับวันก่อนหน้า
        
        Args:
            date: วันที่ต้องการเปรียบเทียบ (default=วันนี้)
            
        Returns:
            dict: ผลการเปรียบเทียบ
        """
        if date is None:
            date = timezone.now().date()
        
        previous_date = date - timedelta(days=1)
        
        current = self.get_performance_summary(date)
        previous = self.get_performance_summary(previous_date)
        
        def calc_change(current_val, previous_val):
            if previous_val == 0:
                return 0
            return round((current_val - previous_val) / previous_val * 100, 1)
        
        return {
            'current_date': str(date),
            'previous_date': str(previous_date),
            'overall_score_change': calc_change(
                current['overall_score'],
                previous['overall_score']
            ),
            'scan_accuracy_change': calc_change(
                current['metrics']['scan_accuracy']['accuracy_percent'],
                previous['metrics']['scan_accuracy']['accuracy_percent']
            ),
            'processing_time_change': calc_change(
                current['metrics']['processing_time']['total_avg_seconds'],
                previous['metrics']['processing_time']['total_avg_seconds']
            ),
            'throughput_change': calc_change(
                current['metrics']['throughput']['avg_per_hour'],
                previous['metrics']['throughput']['avg_per_hour']
            )
        }


# ========================================
# Singleton Instance
# ========================================
performance_tracker = PerformanceTracker()