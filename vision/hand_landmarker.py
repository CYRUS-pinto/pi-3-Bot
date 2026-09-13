"""MediaPipe Hand Landmarker wrapper — TFLite, async LIVE_STREAM mode.
Runs ~8fps on Pi 3, ~15fps on Pi 4. Non-blocking: feed frames, read latest landmarks.
"""
import cv2
import numpy as np
import threading
import time
from typing import Optional, List, Tuple

try:
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision
    from mediapipe import Image as MPImage
    from mediapipe import ImageFormat as MPImageFormat
    MEDIAPIPE_AVAILABLE = True
except ImportError:
    MEDIAPIPE_AVAILABLE = False


class HandLandmarker:
    """Async hand landmarker using MediaPipe TFLite model."""
    
    def __init__(
        self,
        model_path: str = "hand_landmarker.task",
        num_hands: int = 2,
        min_detection_conf: float = 0.6,
        min_presence_conf: float = 0.6,
        min_tracking_conf: float = 0.6,
    ):
        if not MEDIAPIPE_AVAILABLE:
            raise RuntimeError("mediapipe not installed. pip install mediapipe")
        
        base_options = mp_python.BaseOptions(model_asset_path=model_path)
        options = mp_vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=model_path),
            num_hands=num_hands,
            min_hand_detection_confidence=min_detection_conf,
            min_hand_presence_confidence=min_presence_conf,
            min_tracking_confidence=min_tracking_conf,
            running_mode=mp_vision.RunningMode.LIVE_STREAM,
            result_callback=self._callback
        )
        self._landmarker = mp_vision.HandLandmarker.create_from_options(options)
        self._latest_result = None
        self._lock = threading.Lock()
        self._mp_vision = mp_vision
        self._MPImage = MPImage
        self._MPImageFormat = MPImageFormat

    def _callback(self, result, output_image, timestamp_ms):
        with self._lock:
            self._latest_result = result

    def process(self, frame_bgr: np.ndarray) -> None:
        """Non-blocking: feed frame to landmarker. Call get_latest() to retrieve."""
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        mp_image = self._MPImage(
            image_format=self._MPImageFormat.SRGB,
            data=rgb
        )
        timestamp_ms = int(time.time() * 1000)
        self._landmarker.detect_async(mp_image, timestamp_ms)

    def get_latest(self) -> Optional[object]:
        """Returns latest HandLandmarkerResult or None."""
        with self._lock:
            return self._latest_result

    def get_landmarks(self, result, hand_idx: int = 0) -> Optional[List[Tuple[float, float]]]:
        """Extract normalized (x, y) for 21 landmarks of hand_idx."""
        if not result or not result.hand_landmarks:
            return None
        if hand_idx >= len(result.hand_landmarks):
            return None
        return [(lm.x, lm.y) for lm in result.hand_landmarks[hand_idx]]

    def get_handedness(self, result) -> List[str]:
        """Returns list of 'Left'/'Right' for each detected hand."""
        if not result or not result.handedness:
            return []
        return [h[0].category_name for h in result.handedness]

    def close(self):
        if self._landmarker:
            self._landmarker.close()
            self._landmarker = None


def count_fingers(landmarks: List[Tuple[float, float]]) -> int:
    """Count extended fingers from 21 landmarks. Thumb handled separately."""
    if not landmarks or len(landmarks) < 21:
        return 0
    # Finger tip vs PIP indices: (tip, pip)
    finger_pairs = [(8, 6), (12, 10), (16, 14), (20, 18)]  # index, middle, ring, pinky
    count = 0
    for tip_idx, pip_idx in finger_pairs:
        if landmarks[tip_idx][1] < landmarks[pip_idx][1]:  # tip above pip = extended
            count += 1
    # Thumb: tip (4) vs IP (3) — simplified
    if landmarks[4][0] > landmarks[3][0]:  # thumb tip right of IP = extended (right hand)
        count += 1
    return count


def pinch_distance(landmarks: List[Tuple[float, float]]) -> float:
    """Distance between thumb tip (4) and index tip (8). Normalized 0-1."""
    if not landmarks or len(landmarks) < 9:
        return 1.0
    dx = landmarks[4][0] - landmarks[8][0]
    dy = landmarks[4][1] - landmarks[8][1]
    return (dx*dx + dy*dy) ** 0.5


def is_pinch(landmarks: List[Tuple[float, float]], threshold: float = 0.04) -> bool:
    return pinch_distance(landmarks) < threshold


def is_pointing(landmarks: List[Tuple[float, float]]) -> bool:
    """Index extended, others folded."""
    if not landmarks or len(landmarks) < 21:
        return False
    # Index extended
    index_ext = landmarks[8][1] < landmarks[6][1]
    # Others folded
    middle_folded = landmarks[12][1] > landmarks[10][1]
    ring_folded = landmarks[16][1] > landmarks[14][1]
    pinky_folded = landmarks[20][1] > landmarks[18][1]
    return index_ext and middle_folded and ring_folded and pinky_folded


def is_fist(landmarks: List[Tuple[float, float]]) -> bool:
    """All fingers folded."""
    if not landmarks or len(landmarks) < 21:
        return False
    for tip, pip in [(8, 6), (12, 10), (16, 14), (20, 18)]:
        if landmarks[tip][1] < landmarks[pip][1]:
            return False
    return True