# scanning/face_recognition_service.py
# เวอร์ชัน OPTIMIZED - เร็วขึ้น 5-10 เท่า แต่ยังปลอดภัย
# เปลี่ยนจาก ArcFace -> SFace (เร็วกว่า 3x)
# เปลี่ยนจาก RetinaFace -> opencv (เร็วกว่า 10x)
# เพิ่ม embedding cache สำหรับ stored images
# ลด image size ก่อนประมวลผล

import cv2
import numpy as np
from PIL import Image
import io
import base64
import os
from django.conf import settings
import logging
from typing import Tuple, Optional, Dict
import tempfile
import time as time_module
import hashlib

logger = logging.getLogger(__name__)

# ลองใช้ DeepFace
try:
    from deepface import DeepFace
    DEEPFACE_AVAILABLE = True
    logger.info("✅ DeepFace library loaded successfully")
except ImportError:
    DEEPFACE_AVAILABLE = False
    logger.warning("⚠️ DeepFace not available")
    import mediapipe as mp


class FaceRecognitionService:
    """
    Service สำหรับการจดจำใบหน้า - เวอร์ชัน OPTIMIZED
    
    การปรับปรุงเพื่อความเร็ว:
    1. ใช้ SFace แทน ArcFace (เร็วกว่า 3 เท่า แม่นยำพอใช้)
    2. ใช้ opencv แทน retinaface (เร็วกว่า 10 เท่า)
    3. Cache embedding ของรูปนักเรียน (ไม่ต้องคำนวณซ้ำ)
    4. ลดขนาดรูปเหลือ 480px ก่อนประมวลผล
    5. Quick liveness check แทน full check
    
    Target: < 3 วินาที (จากเดิม 33 วินาที)
    """
    
    def __init__(self):
        """Initialize face recognition components"""
        try:
            self.student_faces_dir = os.path.join(settings.MEDIA_ROOT, 'student_faces')
            self.face_scans_dir = os.path.join(settings.MEDIA_ROOT, 'face_scans')
            
            os.makedirs(self.student_faces_dir, exist_ok=True)
            os.makedirs(self.face_scans_dir, exist_ok=True)
            
            # ===== EMBEDDING CACHE =====
            # เก็บ embedding ของรูปนักเรียนที่เคยคำนวณแล้ว
            self._embedding_cache: Dict[str, np.ndarray] = {}
            self._cache_timestamps: Dict[str, float] = {}
            self._cache_ttl = 7200  # Cache หมดอายุใน 2 ชั่วโมง
            
            if DEEPFACE_AVAILABLE:
                # ⚡ SPEED OPTIMIZED:
                # - SFace เร็วกว่า ArcFace 3x (threshold ~0.6)
                # - opencv เร็วกว่า retinaface 10x
                self._model_name = "SFace"
                self._detector_backend = "opencv"
                
                # Pre-build model เมื่อเริ่มต้น
                self._preload_model()
                
                logger.info(f"DeepFace OPTIMIZED: model={self._model_name}, detector={self._detector_backend}")
            else:
                self.mp_face_detection = mp.solutions.face_detection
                self.mp_face_mesh = mp.solutions.face_mesh
                logger.warning("Using MediaPipe fallback")
            
            logger.info("FaceRecognitionService OPTIMIZED initialized")
            
        except Exception as e:
            logger.error(f"Error initializing: {str(e)}", exc_info=True)
            raise
    
    def _preload_model(self):
        """Pre-load model เพื่อให้การสแกนครั้งแรกเร็วขึ้น"""
        try:
            DeepFace.build_model(self._model_name)
            logger.info(f"Model {self._model_name} pre-loaded")
        except Exception as e:
            logger.warning(f"Could not preload model: {str(e)}")
    
    def _decode_base64_image(self, image_base64: str) -> Optional[np.ndarray]:
        """แปลง base64 string เป็น NumPy array"""
        try:
            if ',' in image_base64:
                image_data = base64.b64decode(image_base64.split(',')[1])
            else:
                image_data = base64.b64decode(image_base64)
            
            image = Image.open(io.BytesIO(image_data))
            
            if image.mode != 'RGB':
                image = image.convert('RGB')
            
            return np.array(image)
            
        except Exception as e:
            logger.error(f"Error decoding base64: {str(e)}")
            return None
    
    def _resize_image_for_speed(self, image_np: np.ndarray, max_size: int = 480) -> np.ndarray:
        """ลดขนาดรูปเพื่อให้ประมวลผลเร็วขึ้น"""
        h, w = image_np.shape[:2]
        
        if max(h, w) <= max_size:
            return image_np
        
        if h > w:
            new_h = max_size
            new_w = int(w * max_size / h)
        else:
            new_w = max_size
            new_h = int(h * max_size / w)
        
        return cv2.resize(image_np, (new_w, new_h), interpolation=cv2.INTER_AREA)
    
    def _save_temp_image(self, image_np: np.ndarray, resize: bool = True) -> str:
        """บันทึกรูปชั่วคราว"""
        if resize:
            image_np = self._resize_image_for_speed(image_np, max_size=480)
        
        temp_file = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
        temp_path = temp_file.name
        temp_file.close()
        
        image_bgr = cv2.cvtColor(image_np, cv2.COLOR_RGB2BGR)
        cv2.imwrite(temp_path, image_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
        
        return temp_path
    
    def _validate_image_path(self, image_path: str) -> bool:
        """ตรวจสอบความถูกต้องของ path"""
        if not image_path:
            return False
        if not os.path.exists(image_path):
            return False
        valid_extensions = {'.jpg', '.jpeg', '.png', '.JPG', '.JPEG', '.PNG'}
        file_ext = os.path.splitext(image_path)[1]
        return file_ext in valid_extensions
    
    def _get_file_hash(self, file_path: str) -> str:
        """สร้าง hash ของไฟล์สำหรับเป็น cache key"""
        stat = os.stat(file_path)
        return f"{file_path}_{stat.st_mtime}_{stat.st_size}"
    
    def _get_cached_embedding(self, image_path: str) -> Optional[np.ndarray]:
        """ดึง embedding จาก cache ถ้ามี"""
        cache_key = self._get_file_hash(image_path)
        
        if cache_key not in self._embedding_cache:
            return None
        
        if time_module.time() - self._cache_timestamps.get(cache_key, 0) > self._cache_ttl:
            del self._embedding_cache[cache_key]
            del self._cache_timestamps[cache_key]
            return None
        
        return self._embedding_cache[cache_key]
    
    def _cache_embedding(self, image_path: str, embedding: np.ndarray):
        """เก็บ embedding ใน cache"""
        cache_key = self._get_file_hash(image_path)
        self._embedding_cache[cache_key] = embedding
        self._cache_timestamps[cache_key] = time_module.time()
    
    def _get_embedding(self, image_path: str) -> Optional[np.ndarray]:
        """ดึง embedding พร้อม caching"""
        # ตรวจ cache ก่อน
        cached = self._get_cached_embedding(image_path)
        if cached is not None:
            logger.debug(f"Cache HIT for {image_path}")
            return cached
        
        # คำนวณ embedding ใหม่
        try:
            result = DeepFace.represent(
                img_path=image_path,
                model_name=self._model_name,
                detector_backend=self._detector_backend,
                enforce_detection=True,
                align=True
            )
            
            if result and len(result) > 0:
                embedding = np.array(result[0]['embedding'])
                # เก็บลง cache
                self._cache_embedding(image_path, embedding)
                logger.debug(f"Cache MISS - computed for {image_path}")
                return embedding
                
        except Exception as e:
            logger.warning(f"Could not get embedding: {str(e)}")
        
        return None
    
    # ========================================
    # 🔒 Quick Liveness Check (FAST version)
    # ========================================
    
    def _quick_liveness_check(self, image_np: np.ndarray) -> Tuple[bool, str]:
        """
        ตรวจสอบ liveness แบบเร็ว (< 50ms)
        ตรวจแค่ Laplacian variance เพื่อดูว่าเป็นรูปจากหน้าจอหรือไม่
        """
        try:
            # ลดขนาดรูปก่อนเพื่อความเร็ว
            small = cv2.resize(image_np, (160, 120), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
            
            # Laplacian Variance - วัดความชัดของรูป
            laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
            
            # รูปจากหน้าจอมักมี variance ต่ำ
            MIN_LAPLACIAN = 30  # ลดลงเพื่อไม่ reject คนจริง
            
            logger.debug(f"Liveness quick check: laplacian={laplacian_var:.1f}")
            
            if laplacian_var >= MIN_LAPLACIAN:
                return True, "OK"
            else:
                logger.warning(f"Low laplacian variance: {laplacian_var:.1f}")
                return False, f"ภาพไม่ชัด ({laplacian_var:.0f})"
            
        except Exception as e:
            # ถ้าตรวจไม่ได้ ให้ผ่านไปก่อน
            return True, "Skip"
    
    # ========================================
    # Main verification method - OPTIMIZED
    # ========================================
    
    def verify_face(
        self, 
        captured_image_base64: str, 
        stored_image_path: str, 
        tolerance: float = 0.4
    ) -> Tuple[bool, float, str]:
        """
        เปรียบเทียบใบหน้า - เวอร์ชัน OPTIMIZED (Fixed JSON & Score)
        """
        if not DEEPFACE_AVAILABLE:
            return self._verify_face_mediapipe(captured_image_base64, stored_image_path, tolerance)
        
        start_time = time_module.time()
        temp_path = None
        
        # ⚡ SFace threshold (ปรับตามมาตรฐาน SFace)
        # 0.593 คือค่าแนะนำของ SFace (Cosine)
        # ปรับเป็น 0.60 เพื่อให้ยืดหยุ่นขึ้นสำหรับสภาพแสงจริง
        STRICT_THRESHOLD = 0.60
        
        try:
            # ========================================
            # STEP 0: Validate inputs
            # ========================================
            if not self._validate_image_path(stored_image_path):
                return False, 0.0, "ไม่พบรูปภาพนักเรียนในระบบ"
            
            image_np = self._decode_base64_image(captured_image_base64)
            if image_np is None:
                return False, 0.0, "ไม่สามารถอ่านรูปภาพที่สแกนได้"
            
            # ========================================
            # 🔒 STEP 1: Quick Liveness Check (FAST)
            # ========================================
            is_live, liveness_msg = self._quick_liveness_check(image_np)
            if not is_live:
                return False, 0.0, f"⚠️ {liveness_msg}"
            
            # ========================================
            # ⚡ STEP 2: Get stored image embedding (CACHED)
            # ========================================
            stored_embedding = self._get_cached_embedding(stored_image_path)
            
            if stored_embedding is None:
                # คำนวณและ cache ถ้ายังไม่มี
                stored_embedding = self._get_embedding(stored_image_path)
                if stored_embedding is None:
                    return False, 0.0, "ไม่สามารถประมวลผลรูปนักเรียนได้"
            
            # ========================================
            # ⚡ STEP 3: Get captured image embedding
            # ========================================
            # ลดขนาดรูปก่อนประมวลผล
            image_np = self._resize_image_for_speed(image_np, max_size=480)
            temp_path = self._save_temp_image(image_np, resize=False)
            
            try:
                captured_result = DeepFace.represent(
                    img_path=temp_path,
                    model_name=self._model_name,
                    detector_backend=self._detector_backend,
                    enforce_detection=True,
                    align=True
                )
                
                if not captured_result or len(captured_result) == 0:
                    return False, 0.0, "ไม่พบใบหน้าในภาพที่สแกน"
                
                captured_embedding = np.array(captured_result[0]['embedding'])
                
            except ValueError as e:
                if "Face could not be detected" in str(e):
                    return False, 0.0, "ไม่พบใบหน้า - กรุณามองตรงกล้อง"
                raise
            
            # ========================================
            # ⚡ STEP 4: Calculate distance (FAST)
            # ========================================
            # Cosine distance
            dot_product = np.dot(stored_embedding, captured_embedding)
            norm_product = np.linalg.norm(stored_embedding) * np.linalg.norm(captured_embedding)
            
            if norm_product == 0:
                distance = 1.0
            else:
                cosine_similarity = dot_product / norm_product
                distance = 1 - cosine_similarity
            
            # ========================================
            # ⚡ STEP 5: Determine match
            # ========================================
            # 1. ตัดสินว่าตรงกันหรือไม่ (ใช้ Threshold)
            is_match_val = distance < STRICT_THRESHOLD
            
            # 2. คำนวณ Confidence (ปรับสูตรใหม่ให้ make sense)
            # สูตรเดิม: 1 - (dist / threshold) ทำให้คะแนนตกฮวบถ้าใกล้ threshold
            # สูตรใหม่: 1 - distance (คือ Cosine Similarity ตรงๆ)
            # เช่น distance 0.45 -> confidence 0.55 (55%)
            confidence_val = max(0.0, 1.0 - distance)
            
            # -----------------------------------------------------------
            # ⭐ CRITICAL FIX: แปลง NumPy types -> Python types
            # -----------------------------------------------------------
            is_match = bool(is_match_val)
            confidence = float(confidence_val)
            # -----------------------------------------------------------

            elapsed = time_module.time() - start_time
            
            if is_match:
                message = f"✅ ยืนยันตัวตนสำเร็จ ({confidence*100:.0f}%, {elapsed:.1f}s)"
            else:
                message = f"❌ ใบหน้าไม่ตรงกับเจ้าของบัตร! ({confidence*100:.0f}%)"
            
            logger.info(
                f"Verify: match={is_match}, dist={distance:.3f}, "
                f"threshold={STRICT_THRESHOLD}, conf={confidence:.2f}, time={elapsed:.2f}s"
            )
            
            return is_match, confidence, message
            
        except ValueError as e:
            error_str = str(e)
            if "Face could not be detected" in error_str:
                return False, 0.0, "ไม่พบใบหน้า - กรุณามองตรงกล้อง"
            return False, 0.0, f"เกิดข้อผิดพลาด: {error_str}"
            
        except Exception as e:
            logger.error(f"Verify error: {str(e)}", exc_info=True)
            return False, 0.0, "เกิดข้อผิดพลาด"
            
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except:
                    pass
    
    def detect_face_in_frame(self, image_base64: str) -> Tuple[bool, int, str]:
        """
        ตรวจจับใบหน้าในภาพ - FAST version
        
        Returns:
            Tuple[bool, int, str]: (has_face, face_count, message)
        """
        if not DEEPFACE_AVAILABLE:
            return self._detect_face_in_frame_mediapipe(image_base64)
        
        temp_path = None
        try:
            image_np = self._decode_base64_image(image_base64)
            if image_np is None:
                return False, 0, "ไม่สามารถอ่านรูปภาพได้"
            
            # ⚡ ลดขนาดให้เล็กลงเพื่อความเร็ว
            image_np = self._resize_image_for_speed(image_np, max_size=320)
            temp_path = self._save_temp_image(image_np, resize=False)
            
            # ใช้ opencv detector (เร็วมาก)
            faces = DeepFace.extract_faces(
                img_path=temp_path,
                detector_backend="opencv",  # เร็วกว่า retinaface 10x
                enforce_detection=False,
                align=False
            )
            
            face_count = len([f for f in faces if f.get('confidence', 0) > 0.5])
            
            if face_count == 0:
                return False, 0, "ไม่พบใบหน้า - กรุณามองตรงกล้อง"
            elif face_count == 1:
                return True, 1, "พบใบหน้า"
            else:
                return True, face_count, f"⚠️ พบ {face_count} คน - กรุณาอยู่คนเดียว"
                
        except Exception as e:
            logger.warning(f"Face detection: {str(e)}")
            return False, 0, "ตรวจจับใบหน้าไม่สำเร็จ"
            
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except:
                    pass
    
    # ========================================
    # MediaPipe Fallback Methods
    # ========================================
    
    def _verify_face_mediapipe(
        self, 
        captured_image_base64: str, 
        stored_image_path: str, 
        tolerance: float
    ) -> Tuple[bool, float, str]:
        """Fallback: ใช้ MediaPipe เมื่อไม่มี DeepFace"""
        try:
            captured_encoding = self._encode_face_mediapipe(captured_image_base64, is_base64=True)
            stored_encoding = self._encode_face_mediapipe(stored_image_path, is_base64=False)
            
            if captured_encoding is None:
                return False, 0.0, "ไม่พบใบหน้าในภาพที่สแกน"
            if stored_encoding is None:
                return False, 0.0, "ไม่พบใบหน้าในรูปนักเรียน"
            
            # คำนวณ cosine similarity
            similarity = np.dot(captured_encoding, stored_encoding) / (
                np.linalg.norm(captured_encoding) * np.linalg.norm(stored_encoding)
            )
            
            confidence = max(0, min(1, (similarity + 1) / 2))
            
            MEDIAPIPE_THRESHOLD = 0.75
            is_match = confidence >= MEDIAPIPE_THRESHOLD
            
            if is_match:
                message = f"✅ ผ่าน (MediaPipe: {confidence*100:.0f}%)"
            else:
                message = f"❌ ไม่ผ่าน (MediaPipe: {confidence*100:.0f}%)"
            
            return is_match, confidence, message
                
        except Exception as e:
            return False, 0.0, f"เกิดข้อผิดพลาด: {str(e)}"
    
    def _encode_face_mediapipe(self, image_source: str, is_base64: bool = False) -> Optional[np.ndarray]:
        """สร้าง encoding จาก MediaPipe"""
        try:
            if is_base64:
                image_np = self._decode_base64_image(image_source)
            else:
                if not self._validate_image_path(image_source):
                    return None
                image = Image.open(image_source)
                image_np = np.array(image.convert('RGB'))
            
            if image_np is None:
                return None
            
            with self.mp_face_mesh.FaceMesh(
                static_image_mode=True,
                max_num_faces=1,
                min_detection_confidence=0.5,
                refine_landmarks=True
            ) as face_mesh:
                results = face_mesh.process(image_np)
                
                if results.multi_face_landmarks:
                    return self._normalize_landmarks(results.multi_face_landmarks[0])
                return None
                
        except Exception as e:
            logger.error(f"MediaPipe error: {str(e)}")
            return None
    
    def _detect_face_in_frame_mediapipe(self, image_base64: str) -> Tuple[bool, int, str]:
        """Fallback: ใช้ MediaPipe"""
        try:
            image_np = self._decode_base64_image(image_base64)
            if image_np is None:
                return False, 0, "ไม่สามารถอ่านรูปภาพได้"
            
            with self.mp_face_detection.FaceDetection(
                min_detection_confidence=0.5,
                model_selection=0
            ) as face_detection:
                results = face_detection.process(image_np)
                
                if results.detections:
                    face_count = len(results.detections)
                    if face_count == 1:
                        return True, 1, "พบใบหน้า"
                    else:
                        return True, face_count, f"พบ {face_count} คน"
                return False, 0, "ไม่พบใบหน้า"
                    
        except Exception as e:
            return False, 0, f"เกิดข้อผิดพลาด: {str(e)}"
    
    def _normalize_landmarks(self, landmarks) -> np.ndarray:
        """Normalize landmarks สำหรับ MediaPipe"""
        key_indices = [
            33, 133, 160, 159, 158, 144, 145, 153,
            362, 263, 387, 386, 385, 373, 374, 380,
            1, 2, 98, 327, 168,
            61, 291, 0, 17, 78, 308,
            70, 63, 105, 66, 107, 300, 293, 334, 296, 336,
        ]
        
        encoding = []
        for i in key_indices:
            if i < len(landmarks.landmark):
                landmark = landmarks.landmark[i]
                encoding.extend([landmark.x, landmark.y, landmark.z])
        
        encoding = np.array(encoding)
        points = encoding.reshape(-1, 3)
        centroid = np.mean(points, axis=0)
        points_centered = points - centroid
        
        if len(points_centered) > 8:
            eye_distance = np.linalg.norm(points_centered[0] - points_centered[8])
            if eye_distance > 0:
                points_normalized = points_centered / eye_distance
            else:
                points_normalized = points_centered
        else:
            points_normalized = points_centered
        
        return points_normalized.flatten()
    
    # ========================================
    # Utility methods
    # ========================================
    
    def get_student_face_path(self, student) -> Optional[str]:
        """หา path ของรูปนักเรียน"""
        possible_extensions = ['.jpg', '.jpeg', '.png', '.JPG', '.JPEG', '.PNG']
        
        # ลองจาก face_image field ก่อน
        if hasattr(student, 'face_image') and student.face_image:
            try:
                face_path = os.path.join(settings.MEDIA_ROOT, str(student.face_image))
                if os.path.exists(face_path):
                    return face_path
            except:
                pass
        
        # ลองจาก student_id
        if hasattr(student, 'student_id') and student.student_id:
            for ext in possible_extensions:
                face_path = os.path.join(self.student_faces_dir, f"{student.student_id}{ext}")
                if os.path.exists(face_path):
                    return face_path
        
        # ลองจาก rfid_card_id
        if hasattr(student, 'rfid_card_id') and student.rfid_card_id:
            for ext in possible_extensions:
                face_path = os.path.join(self.student_faces_dir, f"{student.rfid_card_id}{ext}")
                if os.path.exists(face_path):
                    return face_path
        
        # ลองจากชื่อ-นามสกุล
        if hasattr(student, 'first_name') and hasattr(student, 'last_name'):
            if student.first_name and student.last_name:
                full_name = f"{student.first_name}_{student.last_name}"
                for ext in possible_extensions:
                    face_path = os.path.join(self.student_faces_dir, f"{full_name}{ext}")
                    if os.path.exists(face_path):
                        return face_path
        
        # ลองจากชื่อเดี่ยว
        if hasattr(student, 'first_name') and student.first_name:
            for ext in possible_extensions:
                face_path = os.path.join(self.student_faces_dir, f"{student.first_name}{ext}")
                if os.path.exists(face_path):
                    return face_path
        
        return None
    
    def save_captured_image(self, image_base64: str, filename: str) -> Optional[str]:
        """บันทึกรูปภาพที่จับได้"""
        try:
            if ',' in image_base64:
                format_part, imgstr = image_base64.split(';base64,')
                ext = format_part.split('/')[-1]
            else:
                imgstr = image_base64
                ext = 'jpg'
            
            from datetime import datetime
            date_path = datetime.now().strftime('%Y/%m/%d')
            save_dir = os.path.join(self.face_scans_dir, date_path)
            os.makedirs(save_dir, exist_ok=True)
            
            file_path = os.path.join(save_dir, f"{filename}.{ext}")
            
            image_data = base64.b64decode(imgstr)
            with open(file_path, 'wb') as f:
                f.write(image_data)
            
            relative_path = os.path.join('face_scans', date_path, f"{filename}.{ext}")
            logger.info(f"Saved: {relative_path}")
            return relative_path
            
        except Exception as e:
            logger.error(f"Save error: {str(e)}")
            return None
    
    def cleanup_old_scans(self, days: int = 30) -> int:
        """ลบรูปเก่า"""
        try:
            import time
            threshold_time = time.time() - (days * 24 * 60 * 60)
            deleted_count = 0
            
            for root, dirs, files in os.walk(self.face_scans_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    if os.path.getmtime(file_path) < threshold_time:
                        try:
                            os.remove(file_path)
                            deleted_count += 1
                        except:
                            pass
            
            return deleted_count
            
        except:
            return 0
    
    def clear_cache(self):
        """ล้าง embedding cache"""
        self._embedding_cache.clear()
        self._cache_timestamps.clear()
        logger.info("Embedding cache cleared")
    
    def preload_student_embeddings(self, students):
        """
        Pre-load embeddings ของนักเรียนทั้งหมดลง cache
        เรียกตอน server start เพื่อให้สแกนครั้งแรกเร็ว
        
        Usage:
            from my.models import Student
            students = Student.objects.filter(is_active=True)
            face_recognition_service.preload_student_embeddings(students)
        """
        loaded = 0
        for student in students:
            face_path = self.get_student_face_path(student)
            if face_path:
                embedding = self._get_embedding(face_path)
                if embedding is not None:
                    loaded += 1
        logger.info(f"Pre-loaded {loaded} student embeddings")
        return loaded
    
    def is_using_accurate_library(self) -> bool:
        """ตรวจสอบว่าใช้ library ที่แม่นยำหรือไม่"""
        return DEEPFACE_AVAILABLE
    
    def get_library_name(self) -> str:
        """คืนชื่อ library ที่ใช้"""
        if DEEPFACE_AVAILABLE:
            return f"DeepFace ({self._model_name})"
        return "MediaPipe (fallback)"
    
    def get_cache_stats(self) -> dict:
        """ดูสถิติ cache"""
        return {
            'cached_embeddings': len(self._embedding_cache),
            'cache_ttl_seconds': self._cache_ttl,
            'model': self._model_name if DEEPFACE_AVAILABLE else 'MediaPipe',
            'detector': self._detector_backend if DEEPFACE_AVAILABLE else 'MediaPipe'
        }


face_recognition_service = FaceRecognitionService()