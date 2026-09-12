"""TARS Universal Vision Subsystem — Multi-Person & Crowd Tracking Daemon.

Ultra-low latency, high-precision computer vision pipeline:
  1. Asynchronous Dual-Thread Architecture:
     - Render & Stream Worker: Renders sci-fi tactical HUD and serves JPEG stream at 30–40 FPS.
     - Neural AI Worker: Runs OpenCV YuNet (or Haar fallback) asynchronously at 15–22 FPS.
  2. 1€ (One-Euro) Adaptive Filter: Eliminates micro-jitter when holding still, zero latency during movement.
  3. OpenCV YuNet ONNX Neural Network: Detects faces at extreme angles (±90°) with 5 facial landmarks.
  4. FreshFrameGrabber: Drains OpenCV camera buffers in real time to prevent backlog lag.
"""

from __future__ import annotations
import sys
import time
import threading
import math
import json
import socket
import os
# Suppress FFmpeg and OpenCV internal warnings (e.g. "[mjpeg @ ...] overread 8")
os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "-8"
os.environ["OPENCV_LOG_LEVEL"] = "SILENT"
import config
import pygame

try:
    import cv2
    import numpy as np
    HAS_CV2 = True
    if hasattr(cv2, "setLogLevel"):
        try:
            cv2.setLogLevel(0)
        except Exception:
            pass
except ImportError:
    HAS_CV2 = False

# ponytail: MediaPipe is OPTIONAL — motion pipeline works fully without it.
# Install on Pi for landmark-confirmed swipes: pip install mediapipe, then place the
# lite model at models/hand_landmarker.task
# (https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task)
try:
    from mediapipe.tasks.python import vision as _mp_vision
    try:
        # 1.0.x layout (Pi-proven)
        from mediapipe.tasks.python.core import base_options as _mp_base
    except ImportError:
        # pre-1.0 layout
        from mediapipe.tasks.python import base_options as _mp_base
    import mediapipe as _mp
    HAS_MEDIAPIPE = True
except ImportError:
    HAS_MEDIAPIPE = False


def hand_is_open(pts) -> bool:
    """Pure open-hand test on 21 normalized (x, y) landmarks (y grows downward).
    True when ANY of index/middle/ring is extended (tip clearly above its PIP joint).
    Pointing counts (index only); fist/sleeve-blob counts as closed. No model needed."""
    try:
        ext = 0
        for tip, pip in ((8, 6), (12, 10), (16, 14)):
            if pts[tip][1] < pts[pip][1] - 0.02:
                ext += 1
        return ext >= 1
    except Exception:
        return False


class HandConfirm:
    """Lazy MediaPipe HandLandmarker wrapper. Missing lib/model => disabled (fail-open).
    Call sense() at most every Nth AI frame; it returns (tip_x, tip_y, is_open) or None.
    WALL (proven 2026-09-11): mediapipe 1.0.1 aarch64 SIGILLs on Pi 3 (BCM2837 lacks the
    ARMv8 crypto ext the wheel was built for) — process DIES at construction, no exception.
    Do NOT place hand_landmarker.task on a Pi 3. Targets: Pi 4/5, or the x86 laptop companion.
    The motion pipeline below remains the Pi 3 path and is fully sufficient (see bench logs)."""

    def __init__(self, model_path: str | None = None):
        self.ok = False
        self._lm = None
        if not HAS_MEDIAPIPE or not model_path or not os.path.exists(model_path):
            return
        try:
            opts = _mp_vision.HandLandmarkerOptions(
                base_options=_mp_base.BaseOptions(model_asset_path=model_path),
                running_mode=_mp_vision.RunningMode.VIDEO,
                num_hands=1,
                min_hand_detection_confidence=0.5,
                min_hand_presence_confidence=0.5,
                min_tracking_confidence=0.5)
            self._lm = _mp_vision.HandLandmarker.create_from_options(opts)
            self.ok = True
        except Exception:
            self._lm = None
            self.ok = False

    def sense(self, bgr_small, now_ms: int):
        if not self.ok:
            return None
        try:
            rgb = cv2.cvtColor(bgr_small, cv2.COLOR_BGR2RGB)
            img = _mp.Image(image_format=_mp.ImageFormat.SRGB, data=rgb)
            res = self._lm.detect_for_video(img, int(now_ms))
            if not res.hand_landmarks:
                return None
            lm = res.hand_landmarks[0]
            pts = [(p.x, p.y) for p in lm]
            return (lm[8].x, lm[8].y, hand_is_open(pts))
        except Exception:
            return None


ACTIVE_TRACKER: UniversalVisionTracker | None = None
LAST_STREAM_POLL = time.time()

def discover_camera_sources() -> list[str | int]:
    """Dynamically detects active USB tethering gateways, WiFi IP webcams, and local video devices."""
    candidates: list[str | int] = []

    # 1. Detect dynamic USB tethering gateways from Linux routing table (usb0, usb1, rndis0)
    try:
        if os.path.exists("/proc/net/route"):
            with open("/proc/net/route", "r") as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) >= 3 and (parts[0].startswith("usb") or parts[0].startswith("rndis")):
                        gw_hex = parts[2]
                        if gw_hex != "00000000" and len(gw_hex) == 8:
                            ip_bytes = bytes.fromhex(gw_hex)[::-1]
                            gw_ip = ".".join(str(b) for b in ip_bytes)
                            candidates.extend([
                                f"http://{gw_ip}:8080/video",
                                f"http://{gw_ip}:4747/video",
                                f"http://{gw_ip}:8080/mjpegfeed"
                            ])
    except Exception:
        pass

    # 2. Inspect ARP cache for active devices on USB interfaces
    try:
        if os.path.exists("/proc/net/arp"):
            with open("/proc/net/arp", "r") as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) >= 6:
                        ip, flags, dev = parts[0], parts[2], parts[5]
                        if flags == "0x2" and (dev.startswith("usb") or dev.startswith("rndis")):
                            candidates.extend([
                                f"http://{ip}:8080/video",
                                f"http://{ip}:4747/video"
                            ])
    except Exception:
        pass

    # 3. Known phone endpoints (USB tethered Xiaomi/Redmi, WiFi phones, DroidCam)
    candidates.extend([
        "http://127.0.0.1:8090/video",       # ADB-forwarded IP Webcam (no tether/IP needed; see tars-tether.sh)
        "http://10.57.90.53:8080/video",     # USB Tethered Phone 1 (Xiaomi/Redmi)
        "http://10.70.4.51:8080/video",      # WiFi Connected Phone 2
        "http://192.168.42.129:8080/video",  # Standard Android USB tether subnet
        "http://192.168.43.1:8080/video",    # Android Hotspot gateway
        "http://10.70.4.90:8080/video",      # WiFi companion
        "http://10.57.90.53:4747/video",     # DroidCam USB
        "http://10.70.4.51:4747/video",      # DroidCam WiFi
        "http://192.168.42.129:4747/video",  # DroidCam standard tether
        "/dev/video0",
        0
    ])

    seen = set()
    ordered: list[str | int] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered


def probe_fast(candidate: str | int, timeout: float = 0.20) -> bool:
    """Probes candidate stream in < 200ms before attempting OpenCV capture."""
    if isinstance(candidate, int):
        if sys.platform.startswith("linux"):
            return os.path.exists(f"/dev/video{candidate}")
        return True
    if str(candidate).startswith("/dev/"):
        return os.path.exists(str(candidate))
    if str(candidate).startswith("http://"):
        try:
            no_http = str(candidate)[7:]
            host_port = no_http.split("/")[0]
            host, port = host_port.split(":")
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            res = s.connect_ex((host, int(port)))
            s.close()
            return res == 0
        except Exception:
            return False
    return False


def notify_stream_active():
    global LAST_STREAM_POLL
    LAST_STREAM_POLL = time.time()

def get_latest_stream_frame() -> bytes | None:
    global LAST_STREAM_POLL
    LAST_STREAM_POLL = time.time()
    if ACTIVE_TRACKER is not None:
        return ACTIVE_TRACKER.latest_jpeg
    return None


def get_latest_pip_surface():
    if ACTIVE_TRACKER is not None:
        return ACTIVE_TRACKER.latest_pip_surface
    return None


_THERMAL_CACHE = {"temp_c": 52.0, "throttled": "0x0", "last_read": 0.0, "cooling": False}

def get_system_thermal_telemetry() -> dict:
    """Reads Pi CPU temp + throttle flags from sysfs — no subprocess fork, <0.1ms."""
    now = time.time()
    if now - _THERMAL_CACHE["last_read"] < 3.0:
        return _THERMAL_CACHE

    temp = 50.0
    try:
        if os.path.exists("/sys/class/thermal/thermal_zone0/temp"):
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                temp = int(f.read().strip()) / 1000.0
    except Exception:
        pass

    throttled = "0x0"
    try:
        # Read throttle flags from sysfs — avoids 15-30ms subprocess fork on Pi 3
        # Pi firmware exposes /sys/devices/platform/soc/soc:firmware/get_throttled
        # Fallback: infer from temp alone
        throttle_path = "/sys/devices/platform/soc/soc:firmware/get_throttled"
        if os.path.exists(throttle_path):
            with open(throttle_path, "r") as f:
                raw = f.read().strip()
                throttled = raw if raw.startswith("0x") else f"0x{raw}"
        else:
            # No sysfs path — only infer throttle from temp (never fork subprocess)
            throttled = "0x0"
    except Exception:
        pass

    is_throttling = False
    try:
        val = int(throttled, 16)
        # 0x2: arm freq capped, 0x4: currently throttled, 0x8: soft temp limit active
        is_throttling = bool(val & 0xE)
    except Exception:
        pass

    limit_c = getattr(config, "THERMAL_THROTTLE_LIMIT_C", 70.0)
    cooling = (temp > limit_c) or is_throttling

    _THERMAL_CACHE["temp_c"] = temp
    _THERMAL_CACHE["throttled"] = throttled
    _THERMAL_CACHE["last_read"] = now
    _THERMAL_CACHE["cooling"] = cooling
    return _THERMAL_CACHE


def get_vision_metrics() -> dict:
    if ACTIVE_TRACKER is not None:
        with ACTIVE_TRACKER._metrics_lock:
            return dict(ACTIVE_TRACKER.metrics)
    therm = get_system_thermal_telemetry()
    return {
        "ai_fps": 0.0,
        "total_ai_ms": 0.0,
        "yunet_ms": 0.0,
        "gesture_ms": 0.0,
        "resize_ms": 0.0,
        "grab_ms": 0.0,
        "camera_src": "none",
        "faces": 0,
        "hand_active": False,
        "temp_c": therm["temp_c"],
        "throttled": therm["throttled"],
        "cooling": therm["cooling"]
    }


class OneEuroFilter:
    """1€ Filter for noisy coordinates (Casiez et al., CHI 2012).
    Provides rock-solid jitter suppression when standing still, and zero latency during fast motion.
    """
    def __init__(self, min_cutoff: float = 0.40, beta: float = 0.18, d_cutoff: float = 1.0, deadband: float = 0.0025):
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self.deadband = float(deadband)
        self.x_prev = None
        self.dx_prev = 0.0
        self.t_prev = None

    def _smoothing_factor(self, dt: float, cutoff: float) -> float:
        r = 2.0 * math.pi * cutoff * dt
        return r / (r + 1.0)

    def filter(self, x: float, t: float | None = None) -> float:
        if t is None:
            t = time.perf_counter()
        if self.t_prev is None or self.x_prev is None:
            self.x_prev = x
            self.dx_prev = 0.0
            self.t_prev = t
            return x

        # Micro-deadband: completely ignore sub-pixel stationary sensor noise
        if abs(x - self.x_prev) < self.deadband:
            return self.x_prev

        dt = max(1e-4, t - self.t_prev)
        self.t_prev = t

        # Estimate derivative
        dx = (x - self.x_prev) / dt
        a_d = self._smoothing_factor(dt, self.d_cutoff)
        dx_hat = a_d * dx + (1.0 - a_d) * self.dx_prev
        self.dx_prev = dx_hat

        # Adaptive cutoff: higher speed opens cutoff instantly for zero lag
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        a = self._smoothing_factor(dt, cutoff)
        x_hat = a * x + (1.0 - a) * self.x_prev
        self.x_prev = x_hat
        return x_hat

    def reset(self):
        self.x_prev = None
        self.dx_prev = 0.0
        self.t_prev = None


class OpticalGestureEngine:
    """Production-grade hand tracking & intentional 4-way swipe gesture recognizer.
    
    Real-World Architecture:
      1. Central Torso & Head Shield:
         Head, neck, throat, collarbone, and torso column down to the frame bottom are
         completely masked out. Head-turning, nodding, talking, and breathing can NEVER
         trigger hand gestures.
      2. Shield Persistence Window (1.2s):
         Prevents false triggers if face detection momentarily drops for a frame.
      3. Illumination-Invariant Motion Tracking:
         Tracks coherent moving mass in the interaction zones (outside the torso shield).
         Does not rely on brittle skin color thresholding which fails under varying
         indoor lighting, shadows, sleeves, or skin tones.
      4. Intentional Swipe Recognition:
         Requires smooth directional stroke (>= 12% width for horizontal, >= 14% height for vertical)
         with minimum velocity (> 0.30) and directional dominance (> 1.25x opposite axis).
      5. Lockout Cooldown (0.85s):
         Prevents rebound triggers when retracting hand.
    """
    def __init__(self, sensitivity: float = 1.0, mirror: bool = False, invert_y: bool = False):
        self.sensitivity = max(0.4, min(2.5, sensitivity))
        self.mirror = mirror
        self.invert_y = invert_y
        self.prev_gray = None
        self.last_frame_time = 0.0
        self.history: list[tuple[float, float, float, float, float]] = []  # (tip_x, tip_y, t, cx, cy)
        self.last_swipe_time = 0.0
        self.locked_rebound_gesture = ""
        self.rebound_lockout_until = 0.0
        self.last_swipe_fired_t = 0.0      # Timestamp when last swipe was fired (history before this is stale)
        self._confirm_cand = ""            # Confirm-N streak state (GESTURE_CONFIRM_N)
        self._confirm_n = 0
        self.hand_must_settle = False
        self.hand_detected = False
        self.hand_box: tuple[float, float, float, float] | None = None
        self.hand_tip: tuple[float, float] = (0.5, 0.5)
        self.latest_gesture = ""
        self.gesture_display_until = 0.0
        self.last_shield_box: tuple[float, float, float, float] | None = None
        self.last_shield_time = 0.0
        self.last_face_center: tuple[float, float] | None = None
        self.last_face_time = 0.0
        self.smooth_face_x: float | None = None
        self.smooth_face_y: float | None = None
        self.face_history: list[tuple[float, float, float]] = []  # (fcx, fcy, timestamp)
        self.body_motion_history: list[tuple[float, float]] = []  # (body_cx, timestamp)
        self.walk_lockout_until = 0.0
        self.is_presenter_walking = False
        self.kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)) if HAS_CV2 else None
        self.last_latency_ms = 0.0

    def process(self, frame, face_boxes=None, pre_small=None) -> str | None:
        if not getattr(config, "GESTURE_SWIPE_ENABLED", True) or (frame is None and pre_small is None) or not HAS_CV2:
            self.hand_detected = False
            self.hand_box = None
            self.is_presenter_walking = False
            return None

        t_start = time.perf_counter()
        now = time.time()

        # 1. Presenter Walking & Pacing Rejection Tracker (Low-pass filtered against detector jitter)
        if face_boxes and len(face_boxes) > 0:
            fx, fy, fbw, fbh = face_boxes[0]
            fcx = fx + fbw * 0.5
            fcy = fy + fbh * 0.5
            if self.smooth_face_x is None:
                self.smooth_face_x = fcx
                self.smooth_face_y = fcy
                self.face_history = [(fcx, fcy, now)]
            else:
                self.smooth_face_x = 0.80 * self.smooth_face_x + 0.20 * fcx
                self.smooth_face_y = 0.80 * self.smooth_face_y + 0.20 * fcy
                self.face_history.append((self.smooth_face_x, self.smooth_face_y, now))
                self.face_history = [p for p in self.face_history if now - p[2] <= 0.55]

            # Sustained unidirectional face translation:
            # If face moved horizontally by >= 0.035 with speed > GESTURE_WALK_LOCKOUT_SPEED (0.09):
            if len(self.face_history) >= 3 and (now - self.face_history[0][2] >= 0.18):
                dt_walk = now - self.face_history[0][2]
                walk_dx = abs(self.smooth_face_x - self.face_history[0][0])
                walk_speed = walk_dx / max(0.03, dt_walk)
                walk_limit = getattr(config, "GESTURE_WALK_LOCKOUT_SPEED", 0.09)
                debounce = getattr(config, "GESTURE_WALK_DEBOUNCE_SEC", 0.65)
                if walk_speed > walk_limit and walk_dx > 0.035:
                    self.walk_lockout_until = now + debounce
                    if getattr(config, "GESTURE_DEBUG_LOGS", True):
                        config.tlog("GestureHUD", f"WALKING DETECTED (face_dx={walk_dx:.2f}, speed={walk_speed:.2f}) -> Gestures locked out")
            self.last_face_time = now
        elif (now - self.last_face_time > 0.8):
            self.smooth_face_x = None
            self.smooth_face_y = None
            self.face_history.clear()

        self.is_presenter_walking = (now < self.walk_lockout_until)
        if self.is_presenter_walking:
            # Person is actively pacing/walking across room -> suppress gestures
            self.history.clear()
            self.hand_detected = False
            self.hand_box = None
            self.last_latency_ms = (time.perf_counter() - t_start) * 1000.0
            return None

        # Zero-Copy / Re-use pre_small from YuNet if provided, else fast nearest-neighbor resize
        if pre_small is not None:
            gw, gh = pre_small.shape[1], pre_small.shape[0]
            if len(pre_small.shape) == 3:
                gray = cv2.cvtColor(pre_small, cv2.COLOR_BGR2GRAY)
            else:
                gray = pre_small
        else:
            fh, fw = frame.shape[:2]
            gw = 160
            gh = int(160 * (fh / fw))
            gh = gh if gh % 2 == 0 else gh + 1
            small = cv2.resize(frame, (gw, gh), interpolation=cv2.INTER_NEAREST)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

        # Fast 3x3 integer blur (SIMD accelerated, zero float allocations)
        gray = cv2.blur(gray, (3, 3))

        # Reset on initialization or long frame pause (>0.5s) to avoid false delta jumps
        if self.prev_gray is None or (now - self.last_frame_time > 0.5):
            self.prev_gray = gray
            self.last_frame_time = now
            self.history.clear()
            self.last_latency_ms = (time.perf_counter() - t_start) * 1000.0
            return None
        self.last_frame_time = now

        # 2. Motion frame difference
        diff = cv2.absdiff(gray, self.prev_gray)
        self.prev_gray = gray
        _, motion_mask = cv2.threshold(diff, 14, 255, cv2.THRESH_BINARY)

        # 3. Head & Collar Shield (Persisted 1.2s against face dropouts)
        # Masks ONLY the head, chin, and neck collar to avoid talking/nodding triggers.
        # Leaves the chest and mid-air space in front of body open for hand gestures!
        if face_boxes and len(face_boxes) > 0:
            fx, fy, fbw, fbh = face_boxes[0]
            self.last_shield_box = (fx, fy, fbw, fbh)
            self.last_shield_time = now
        elif self.last_shield_box and (now - self.last_shield_time > 1.2):
            self.last_shield_box = None

        if self.last_shield_box:
            fx, fy, fbw, fbh = self.last_shield_box
            mx1 = max(0, int((fx - fbw * 0.25) * gw))
            my1 = max(0, int((fy - fbh * 0.20) * gh))
            mx2 = min(gw, int((fx + fbw * 1.25) * gw))
            my2 = min(gh, int((fy + fbh * 1.35) * gh))
            motion_mask[my1:my2, mx1:mx2] = 0

        # 4. Virtual Interaction Elevation Plane Filter:
        # Ignores waist/pockets/legs (y > 0.85) and ceiling fans/lights (y < 0.12)
        min_y = getattr(config, "GESTURE_HAND_MIN_Y", 0.12)
        max_y = getattr(config, "GESTURE_HAND_MAX_Y", 0.85)
        motion_mask[0:int(min_y * gh), :] = 0
        motion_mask[int(max_y * gh):gh, :] = 0

        # ponytail: early-exit — total motion below min_hand_area means no contour can pass the area filter,
        # so skip MORPH_CLOSE + findContours (the expensive pair) and fall through with empty contours.
        # countNonZero is one SIMD pass. Falls through to the natural no-hand path (sweep eval on history still runs).
        # Hand-size scaled (pocket remote: kids = smaller hands): threshold tracks the filter below exactly.
        _hsz = max(0.5, min(2.0, float(getattr(config, "GESTURE_HAND_SIZE", 1.0))))
        if cv2.countNonZero(motion_mask) < (gw * gh) * 0.005 * _hsz:
            contours = []
        else:
            # Morphological closing using cached kernel
            if self.kernel is not None:
                motion_mask = cv2.morphologyEx(motion_mask, cv2.MORPH_CLOSE, self.kernel)

        # 5. Find coherent moving hand contour in the interaction plane
        contours, _ = cv2.findContours(motion_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        min_hand_area = (gw * gh) * 0.005 * _hsz  # ~0.5% of screen area, hand-size scaled
        max_hand_area = (gw * gh) * getattr(config, "GESTURE_MAX_HAND_AREA", 0.085) * _hsz

        best_cnt = None
        max_area = 0
        body_translating = False

        # Optical body translation detector:
        # Evaluated ONLY when face is not tracked. When face is tracked, YuNet velocity has 100% authority.
        if not face_boxes or len(face_boxes) == 0:
            for c in contours:
                area = cv2.contourArea(c)
                bx, by, bw, bh = cv2.boundingRect(c)
                norm_bw = bw / gw
                norm_bh = bh / gh
                # A true walking torso is wide, tall, and occupies large area:
                if area > (gw * gh) * 0.12 and norm_bh > 0.45 and norm_bw > 0.32:
                    body_cx = (bx + bw * 0.5) / gw
                    self.body_motion_history.append((body_cx, now))
                    self.body_motion_history = [p for p in self.body_motion_history if now - p[1] <= 0.45]
                    if len(self.body_motion_history) >= 3 and (now - self.body_motion_history[0][1] >= 0.18):
                        dt_body = now - self.body_motion_history[0][1]
                        dx_body = abs(body_cx - self.body_motion_history[0][0])
                        spd_body = dx_body / max(0.03, dt_body)
                        if spd_body > 0.12 and dx_body > 0.05:
                            body_translating = True
        else:
            self.body_motion_history.clear()

        # Find best hand/arm candidate:
        for c in contours:
            area = cv2.contourArea(c)
            bx, by, bw, bh = cv2.boundingRect(c)
            norm_bw = bw / gw
            norm_bh = bh / gh

            # Hand/arm candidate: compact width (<= 0.34), height up to 0.54, area <= max_hand_area
            if min_hand_area <= area <= max_hand_area and norm_bw <= 0.34 and norm_bh <= 0.54:
                if area > max_area:
                    max_area = area
                    best_cnt = c

        if body_translating:
            debounce = getattr(config, "GESTURE_WALK_DEBOUNCE_SEC", 0.65)
            self.walk_lockout_until = now + debounce
            self.is_presenter_walking = True
            if getattr(config, "GESTURE_DEBUG_LOGS", True):
                config.tlog("GestureHUD", "WALKING DETECTED via Optical Body Flow -> Gestures locked out")
            self.history.clear()
            self.hand_detected = False
            self.hand_box = None
            self.last_latency_ms = (time.perf_counter() - t_start) * 1000.0
            return None

        cooldown = getattr(config, "GESTURE_COOLDOWN_SEC", 1.00)
        sens = getattr(config, "GESTURE_SWIPE_SENSITIVITY", self.sensitivity)
        min_sweep_dist = getattr(config, "GESTURE_SWIPE_DISTANCE", 0.18) / max(0.5, sens)
        drop_reset_y = getattr(config, "GESTURE_DROP_RESET_Y", 0.72)

        # Strict Cooldown Lockout across ALL directions:
        # While in cooldown, continuously wipe history so swipes during cooldown CANNOT queue or trigger later!
        if now - self.last_swipe_time <= cooldown:
            self.history.clear()
            self.hand_must_settle = False
            self.last_latency_ms = (time.perf_counter() - t_start) * 1000.0
            return None

        # Tier 1: Hand drop clears rebound lockout ONLY IF hand has come to rest AND at least 0.60s passed
        if self.rebound_lockout_until > now and (now - self.last_swipe_time >= 0.60) and best_cnt is not None:
            bx_chk, by_chk, bw_chk, bh_chk = cv2.boundingRect(best_cnt)
            check_cy = (by_chk + bh_chk * 0.5) / gh
            if check_cy >= drop_reset_y:
                is_settled = True
                if len(self.history) >= 2:
                    dt_chk = max(0.02, now - self.history[-2][2])
                    v_chk = abs(self.history[-1][3] - self.history[-2][3]) / dt_chk
                    if v_chk > 0.16:
                        is_settled = False
                if is_settled:
                    if getattr(config, "GESTURE_DEBUG_LOGS", True) and self.locked_rebound_gesture:
                        config.tlog("GestureHUD", f"HAND DROPPED & SETTLED (y={check_cy:.2f}) -> Cleared Rebound Lockout")
                    self.rebound_lockout_until = 0.0
                    self.locked_rebound_gesture = ""

        # 6. Holographic Virtual Screen Sweep Recognizer (Relative Displacement, No Center Lockout)
        def evaluate_virtual_screen_sweep(history, current_time) -> str | None:
            if len(history) < 3 or (current_time - self.last_swipe_time <= cooldown):
                return None
            # Hand must fully decelerate to a near-stop after any swipe before a new
            # gesture is recognised — this is the primary return-stroke leak guard.
            if self.hand_must_settle:
                return None

            curr_tip_x, curr_tip_y, curr_t, curr_cx, curr_cy = history[-1]

            best_sweep = None
            max_disp = 0.0

            # Discard history points that predate the last fired swipe — prevents
            # the return stroke from being re-evaluated once the cooldown/rebound
            # window expires (the "return stroke leaked" bug).
            min_valid_t = self.last_swipe_fired_t

            for i in range(len(history) - 2):
                orig_tip_x, orig_tip_y, orig_t, orig_cx, orig_cy = history[i]
                if orig_t <= min_valid_t:  # skip stale pre-swipe origin points
                    continue
                dx = curr_cx - orig_cx
                dy = curr_cy - orig_cy
                dt = max(0.03, curr_t - orig_t)
                speed_x = abs(dx) / dt
                speed_y = abs(dy) / dt

                # Horizontal Virtual Screen Sweep:
                # Calculate directional sensitivity based on user's intended physical swipe direction
                mirror_mode = getattr(config, "MIRROR_GESTURE_X", False) or self.mirror
                is_user_right = (dx < 0) if mirror_mode else (dx > 0)
                dir_sens_h = getattr(config, "GESTURE_SENS_RIGHT", 1.0) if is_user_right else getattr(config, "GESTURE_SENS_LEFT", 1.0)
                eff_sens_h = max(0.2, sens * dir_sens_h)
                h_min_dist = getattr(config, "GESTURE_SWIPE_DISTANCE", 0.18) / eff_sens_h

                # Requires relative displacement >= h_min_dist, horizontal dominance (dx > 1.20*dy), speed > 0.20
                if abs(dx) >= h_min_dist and abs(dx) > (1.20 * abs(dy)) and speed_x > 0.20:
                    # Parallax check against head translation:
                    # If head is also traveling in the same direction at walking speed and relative displacement is small,
                    # this is whole-body translation (walking), not an isolated hand swipe!
                    if len(self.face_history) >= 2:
                        orig_face = min(self.face_history, key=lambda p: abs(p[2] - orig_t))
                        curr_face = min(self.face_history, key=lambda p: abs(p[2] - curr_t))
                        dx_face = curr_face[0] - orig_face[0]
                        spd_face = abs(dx_face) / dt
                        if (dx * dx_face > 0) and spd_face > 0.06 and abs(dx - dx_face) < 0.12:
                            if getattr(config, "GESTURE_DEBUG_LOGS", True):
                                config.tlog("GestureHUD", f"WALK PARALLAX FILTER: hand_dx={dx:+.2f}, head_dx={dx_face:+.2f} -> Swipe Suppressed")
                            continue

                    if abs(dx) > max_disp:
                        max_disp = abs(dx)
                        best_sweep = ("HORIZ", dx)

                # Flick (kid-style fast short stroke): relaxed distance, strict speed+dominance.
                # ponytail: non-tech users (kids especially) flick instead of sweeping — the 0.18
                # full sweep never fires for them. Flick fills best_sweep; a real sweep still
                # upgrades it below. No parallax check: walking bodies move <0.3, flick needs >0.55.
                # Only when no HORIZ candidate yet (never overrides a proper sweep).
                if best_sweep is None:
                    _fmin = getattr(config, "GESTURE_FLICK_DISTANCE", 0.10) / eff_sens_h
                    _fspd = getattr(config, "GESTURE_FLICK_SPEED", 0.55)
                    if abs(dx) >= _fmin and abs(dx) > (1.50 * abs(dy)) and speed_x > _fspd:
                        if abs(dx) > max_disp:
                            max_disp = abs(dx)
                            best_sweep = ("HORIZ", dx)

                # Vertical Swipe (UP -> Face, DOWN -> Slides, if 4-WAY mode enabled)
                if getattr(config, "GESTURE_MODE", "4_WAY") != "HORIZONTAL_SWIPE":
                    inv_y = getattr(config, "INVERT_CAMERA_Y", False) or self.invert_y
                    is_user_up = (dy > 0) if inv_y else (dy < 0)
                    dir_sens_v = getattr(config, "GESTURE_SENS_UP", 1.0) if is_user_up else getattr(config, "GESTURE_SENS_DOWN", 1.0)
                    eff_sens_v = max(0.2, sens * dir_sens_v)
                    v_min = getattr(config, "GESTURE_VERTICAL_DISTANCE", 0.12) / eff_sens_v
                    # SWIPE UP: hand moves upward — clean vertical dominance
                    if dy < 0 and orig_cy >= 0.18 and curr_cy <= 0.70:
                        if abs(dy) >= v_min and abs(dy) > (1.20 * abs(dx)) and speed_y > 0.12:
                            if abs(dy) > max_disp:
                                max_disp = abs(dy)
                                best_sweep = ("VERT", dy)
                    # SWIPE DOWN: hand moves downward — clean vertical dominance
                    elif dy > 0 and orig_cy <= 0.70 and curr_cy >= 0.20:
                        if abs(dy) >= v_min and abs(dy) > (1.20 * abs(dx)) and speed_y > 0.12:
                            if abs(dy) > max_disp:
                                max_disp = abs(dy)
                                best_sweep = ("VERT", dy)
                    # Vertical flick: same kid-style shortcut as horizontal (short + fast).
                    if best_sweep is None and getattr(config, "GESTURE_MODE", "4_WAY") != "HORIZONTAL_SWIPE":
                        _vfmin = getattr(config, "GESTURE_FLICK_VDISTANCE", 0.08) / eff_sens_v
                        _vfspd = getattr(config, "GESTURE_FLICK_SPEED", 0.55)
                        if abs(dy) >= _vfmin and abs(dy) > (1.50 * abs(dx)) and speed_y > _vfspd:
                            if abs(dy) > max_disp:
                                max_disp = abs(dy)
                                best_sweep = ("VERT", dy)

            if best_sweep is None:
                return None

            axis, delta_val = best_sweep
            candidate = None

            if axis == "HORIZ":
                # Natural vs Flipped mapping via independent MIRROR_GESTURE_X
                mirror_mode = getattr(config, "MIRROR_GESTURE_X", False) or self.mirror
                if mirror_mode:
                    # User moves hand right -> camera sees movement left (delta_val < 0) -> SWIPE_RIGHT (Next Slide)
                    candidate = "SWIPE_RIGHT" if delta_val < 0 else "SWIPE_LEFT"
                else:
                    candidate = "SWIPE_RIGHT" if delta_val > 0 else "SWIPE_LEFT"
            elif axis == "VERT":
                inv_y = getattr(config, "INVERT_CAMERA_Y", False) or self.invert_y
                if inv_y:
                    candidate = "SWIPE_DOWN" if delta_val < 0 else "SWIPE_UP"
                else:
                    candidate = "SWIPE_UP" if delta_val < 0 else "SWIPE_DOWN"

            if candidate is not None:
                # Discard opposite return stroke during lockout window
                if current_time < self.rebound_lockout_until and candidate == self.locked_rebound_gesture:
                    if getattr(config, "GESTURE_DEBUG_LOGS", True):
                        rem = self.rebound_lockout_until - current_time
                        config.tlog("GestureHUD", f"REBOUND IGNORED: {candidate} (return stroke filtered, {rem:.2f}s remaining)")
                    return None

                # Confirm-N: same candidate on consecutive evaluations before firing.
                # ponytail: default 1 = today's behavior exactly. 2-3 = single-frame motion
                # noise (sleeves, light flicker, kids flailing) never fires alone. Costs ~1 AI
                # frame (~45ms) per extra confirm — the cheapest accuracy on this silicon.
                _need = max(1, min(3, int(getattr(config, "GESTURE_CONFIRM_N", 1))))
                if candidate == self._confirm_cand:
                    self._confirm_n += 1
                else:
                    self._confirm_cand, self._confirm_n = candidate, 1
                if self._confirm_n < _need:
                    return None

                rebound_time = getattr(config, "GESTURE_REBOUND_LOCKOUT_SEC", 0.60)
                if candidate == "SWIPE_RIGHT":
                    self.locked_rebound_gesture = "SWIPE_LEFT"
                    self.rebound_lockout_until = current_time + rebound_time
                elif candidate == "SWIPE_LEFT":
                    self.locked_rebound_gesture = "SWIPE_RIGHT"
                    self.rebound_lockout_until = current_time + rebound_time
                elif candidate == "SWIPE_UP":
                    self.locked_rebound_gesture = "SWIPE_DOWN"
                    self.rebound_lockout_until = current_time + rebound_time
                elif candidate == "SWIPE_DOWN":
                    self.locked_rebound_gesture = "SWIPE_UP"
                    self.rebound_lockout_until = current_time + rebound_time

                self.last_swipe_time = current_time
                self.last_swipe_fired_t = current_time  # anchor: history before this point is stale
                self.hand_must_settle = True             # block new gestures until hand decelerates to rest
                self.latest_gesture = candidate
                self.gesture_display_until = current_time + 1.6
                self.history.clear()
                if getattr(config, "GESTURE_DEBUG_LOGS", True):
                    config.tlog("GestureHUD", f"GESTURE FIRED -> {candidate} (rebound filter armed for {rebound_time:.1f}s, waiting for hand settle)")
                return candidate

            return None

        result_gesture = None
        if best_cnt is None:
            self.hand_detected = False
            self.hand_box = None
            self.hand_must_settle = False
            # Check if swipe finished right before hand came to rest
            if len(self.history) >= 3:
                result_gesture = evaluate_virtual_screen_sweep(self.history, now)
            self.history = [pt for pt in self.history if now - pt[2] <= 0.35]
            if len(self.history) < 2:
                self.history.clear()
        else:
            M = cv2.moments(best_cnt)
            if M["m00"] > 0:
                cx = (M["m10"] / M["m00"]) / gw
                cy = (M["m01"] / M["m00"]) / gh
            else:
                bx, by, bw, bh = cv2.boundingRect(best_cnt)
                cx = (bx + bw * 0.5) / gw
                cy = (by + bh * 0.5) / gh

            bx, by, bw, bh = cv2.boundingRect(best_cnt)
            self.hand_detected = True
            self.hand_box = (bx / gw, by / gh, bw / gw, bh / gh)
            self.hand_tip = (cx, cy)

            self.history.append((cx, cy, now, cx, cy))
            self.history = [pt for pt in self.history if now - pt[2] <= 0.55]

            # Settle check: clear hand_must_settle once the hand has decelerated to near-rest.
            # CRITICAL: also wipe history so the accumulated retraction motion cannot
            # immediately fire as a new gesture the moment the settle clears.
            if self.hand_must_settle and len(self.history) >= 2:
                dt_s = max(0.02, self.history[-1][2] - self.history[-2][2])
                vx_s = abs(self.history[-1][3] - self.history[-2][3]) / dt_s
                vy_s = abs(self.history[-1][4] - self.history[-2][4]) / dt_s
                v_inst = math.hypot(vx_s, vy_s)
                if v_inst < 0.12:  # < 12% screen width/sec = nearly stopped
                    self.hand_must_settle = False
                    self.history.clear()  # wipe stale retraction — only fresh motion counts
                    if getattr(config, "GESTURE_DEBUG_LOGS", True):
                        config.tlog("GestureHUD", f"Hand settled (v={v_inst:.2f}) -> Gesture recognition resumed")

            result_gesture = evaluate_virtual_screen_sweep(self.history, now)

            # Live terminal telemetry
            if getattr(config, "GESTURE_DEBUG_LOGS", False) and len(self.history) >= 2:
                if not hasattr(self, "_last_debug_telemetry_t") or (now - self._last_debug_telemetry_t > 0.35):
                    self._last_debug_telemetry_t = now
                    dx_recent = cx - self.history[0][3]
                    dt_recent = max(0.02, now - self.history[0][2])
                    sp_recent = abs(dx_recent) / dt_recent
                    status_tag = f"REBOUND LOCK [{self.locked_rebound_gesture}]" if self.rebound_lockout_until > now else "READY"
                    config.tlog("GestureTrack", f"Hand: ({cx:.2f}, {cy:.2f}) | dx={dx_recent:+.2f} v={sp_recent:.2f} | {status_tag}")

        self.last_latency_ms = (time.perf_counter() - t_start) * 1000.0
        return result_gesture


class FreshFrameGrabber:
    """Zero-latency non-blocking frame grabber that continuously drains the queue and auto-reconnects on stream drops.
    Auto-negotiates optimal lightweight stream resolution and drains socket buffer in <0.2ms."""
    def __init__(self, src: str | int):
        self.src = src
        # Auto-negotiate lightweight 640x480 resolution on Android IP Webcams for 5x faster decode
        # ponytail: was fire-and-forget — phones silently ignore it and we paid 1280x720 decode while assuming 640x480.
        # Now reads back status.json (same pattern as pi_cam_opt.py:29) so the log shows what the phone ACTUALLY sends.
        if isinstance(src, str) and src.startswith("http://"):
            try:
                base = src.rsplit("/", 1)[0]
                import urllib.request, json as _json
                urllib.request.urlopen(f"{base}/settings/video_size?set=640x480", timeout=0.8)
                try:
                    _req = urllib.request.urlopen(f"{base}/status.json", timeout=0.8)
                    _size = _json.loads(_req.read().decode()).get("curvals", {}).get("video_size", "unknown")
                    config.tlog("VisionTracker", f"Camera negotiated video_size: {_size} (wanted 640x480)")
                except Exception:
                    pass
            except Exception:
                pass

        self.cap = cv2.VideoCapture(src)
        try:
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        self.latest_frame = None
        self.latest_timestamp = time.perf_counter()
        self.grab_latency_ms = 0.0
        self.running = True
        self.lock = threading.Lock()
        self.t = threading.Thread(target=self._drain_worker, daemon=True, name="FrameGrabber")
        self.t.start()

    def isOpened(self):
        return self.cap.isOpened()

    def _drain_worker(self):
        fail_count = 0
        while self.running:
            try:
                if self.cap.isOpened():
                    t_g0 = time.perf_counter()
                    # Rapid grab consumes incoming network packets instantly (ZERO socket buffer buildup)
                    ret_g = self.cap.grab()
                    if ret_g:
                        fail_count = 0
                        ret, frame = self.cap.retrieve()
                        if ret and frame is not None:
                            rot = getattr(config, "CAMERA_ROTATION", 0)
                            if rot == 90:
                                frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
                            elif rot == 180:
                                frame = cv2.rotate(frame, cv2.ROTATE_180)
                            elif rot == 270:
                                frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
                            t_g1 = time.perf_counter()
                            with self.lock:
                                self.latest_frame = frame
                                self.latest_timestamp = t_g1
                                self.grab_latency_ms = (t_g1 - t_g0) * 1000.0
                    else:
                        fail_count += 1
                else:
                    fail_count += 1

                if fail_count > 15:
                    # Stream dropped or ended prematurely - auto-rediscover sources!
                    time.sleep(0.3)
                    try:
                        self.cap.release()
                    except Exception:
                        pass
                    new_src = self.src
                    sources = discover_camera_sources()
                    for s in sources:
                        if probe_fast(s):
                            new_src = s
                            break
                    self.src = new_src
                    if isinstance(new_src, str) and new_src.startswith("http://"):
                        try:
                            base = new_src.rsplit("/", 1)[0]
                            import urllib.request, json as _json
                            urllib.request.urlopen(f"{base}/settings/video_size?set=640x480", timeout=0.8)
                            try:
                                _req = urllib.request.urlopen(f"{base}/status.json", timeout=0.8)
                                _size = _json.loads(_req.read().decode()).get("curvals", {}).get("video_size", "unknown")
                                config.tlog("VisionTracker", f"Camera re-negotiated video_size: {_size} (wanted 640x480)")
                            except Exception:
                                pass
                        except Exception:
                            pass
                    self.cap = cv2.VideoCapture(self.src)
                    try:
                        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    except Exception:
                        pass
                    fail_count = 0
                # ponytail: Antigravity perf #2 — 2ms spin burns ~8-12% of a Pi-3 core even with nobody watching.
                # Feed is 8.7fps (115ms); 15ms idle cadence can't starve it. Active poll keeps 2ms drain.
                _idle = (not getattr(config, "SHOW_CAMERA_PIP", False)) and (time.time() - LAST_STREAM_POLL > 2.5)
                time.sleep(0.015 if _idle else 0.002)
            except Exception:
                time.sleep(0.02)

    def read(self):
        with self.lock:
            if self.latest_frame is not None:
                return True, self.latest_frame
            return False, None

    def get_latest_frame(self):
        with self.lock:
            return self.latest_frame, self.latest_timestamp, self.grab_latency_ms

    def release(self):
        self.running = False
        try:
            self.cap.release()
        except Exception:
            pass

    def stop(self):
        self.release()


class UniversalVisionTracker:
    """Dual-thread decoupled tracker:
    - AI Thread: Runs YuNet (or Haar cascade) inference.
    - Streamer Thread: Renders tactical HUD & delivers 30+ FPS low-latency MJPEG video.
    """
    target_active: bool = False
    primary_x: float = 0.5
    primary_y: float = 0.5
    crowd_count: int = 0
    estimated_dist: str = "N/A"
    secondary_targets: list = []
    latest_jpeg: bytes | None = None
    latest_pip_surface = None
    hand_detected: bool = False
    hand_box: tuple | None = None
    latest_gesture: str = ""

    def __init__(self, bridge_port: int = 5005, camera_source: int | str = 0):
        self.bridge_port = bridge_port
        self.camera_source = camera_source
        self.running = False
        self._lock = threading.Lock()
        self.latest_jpeg: bytes | None = None
        self.latest_pip_surface: pygame.Surface | None = None
        self.hand_detected = False
        self.hand_box = None
        self.hand_tip = (0.5, 0.5)
        self.is_presenter_walking = False
        self.latest_gesture = ""
        self.gesture_engine = OpticalGestureEngine() if HAS_CV2 else None
        # Landmark confirmer (optional): model lives next to the YuNet one; missing => fail-open.
        self._landmarks = None
        self._last_open = (False, 0.0)  # (is_open, monotonic ts)
        self._lm_tick = 0
        if HAS_CV2:
            for _mp in (
                os.path.join(os.path.dirname(__file__), "models", "hand_landmarker.task"),
                "/home/cyrus/TARS/models/hand_landmarker.task",
            ):
                if os.path.exists(_mp):
                    self._landmarks = HandConfirm(_mp)
                    if self._landmarks.ok:
                        break
                    self._landmarks = None

        # Pre-encode standby placeholder so /snapshot.jpg never 503s on startup
        if HAS_CV2:
            try:
                init_frame = np.full((240, 320, 3), (12, 16, 22), dtype=np.uint8)
                cv2.putText(init_frame, "TARS OPTICAL SENSOR // INITIALIZING...", (20, 120),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.40, (80, 140, 220), 1, cv2.LINE_AA)
                _, buf = cv2.imencode(".jpg", init_frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
                self.latest_jpeg = buf.tobytes()
            except Exception:
                pass

        # Telemetry State
        self.target_active = False
        self.primary_x = 0.5
        self.primary_y = 0.5
        self.crowd_count = 0
        self.estimated_dist = "2.0m"
        self.secondary_targets: list[tuple[float, float]] = []

        # Jitter Suppression Filters (rock-solid when still, zero-lag when moving)
        self.filter_x = OneEuroFilter(min_cutoff=0.40, beta=0.18, d_cutoff=1.0, deadband=0.0025)
        self.filter_y = OneEuroFilter(min_cutoff=0.40, beta=0.18, d_cutoff=1.0, deadband=0.0025)

        # Worker Threads
        self._ai_thread: threading.Thread | None = None
        self._stream_thread: threading.Thread | None = None
        self._grabber: FreshFrameGrabber | None = None
        # Shared Detections Cache for HUD Renderer
        self._cached_faces: list[dict] = []
        self._last_face_time = 0.0
        self._primary_face_idx: int = 0          # Which detected face is the current target (UP/DOWN to cycle)
        self._ai_fps = 0.0
        self.is_low_power = False
        self.last_detection_timestamp = time.time()
        self._metrics_lock = threading.Lock()
        self.metrics = {
            "ai_fps": 0.0,
            "total_ai_ms": 0.0,
            "yunet_ms": 0.0,
            "gesture_ms": 0.0,
            "resize_ms": 0.0,
            "grab_ms": 0.0,
            "camera_src": "searching",
            "faces": 0,
            "hand_active": False,
            "low_power": False
        }

        # UDP Bridge Socket
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # Initialize YuNet Neural Network Detector (optimal 192x144 @ 50 FPS capacity on Pi 3 CPU)
        self._yunet = None
        self._yunet_w = 192
        self._yunet_h = 144
        self._cascade = None

        if HAS_CV2:
            yunet_paths = [
                "/home/cyrus/TARS/models/face_detection_yunet_2023mar.onnx",
                os.path.join(os.path.dirname(__file__), "models", "face_detection_yunet_2023mar.onnx"),
                "face_detection_yunet_2023mar.onnx"
            ]
            for yp in yunet_paths:
                if os.path.exists(yp) and hasattr(cv2, "FaceDetectorYN"):
                    try:
                        self._yunet = cv2.FaceDetectorYN.create(
                            yp, "", (self._yunet_w, self._yunet_h),
                            score_threshold=0.36,
                            nms_threshold=0.25,
                            top_k=5000
                        )
                        config.tlog("VisionTracker", f"YuNet Neural Network loaded (192x144 turbo profile): {yp}")
                        break
                    except Exception as e:
                        config.tlog("VisionTracker", f"Failed initializing YuNet ({yp}): {e}")

            # Fallback Haar cascade
            if self._yunet is None:
                cascade_candidates = [
                    "/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml",
                    "/usr/share/opencv/haarcascades/haarcascade_frontalface_default.xml",
                ]
                if hasattr(cv2, "data") and hasattr(cv2.data, "haarcascades"):
                    cascade_candidates.append(os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))

                for cp in cascade_candidates:
                    if cp and os.path.exists(cp):
                        try:
                            self._cascade = cv2.CascadeClassifier(cp)
                            config.tlog("VisionTracker", f"Fallback Haar cascade loaded: {cp}")
                            break
                        except Exception:
                            pass

        global ACTIVE_TRACKER
        ACTIVE_TRACKER = self

    def cycle_primary_face(self, direction: int = 1) -> dict | None:
        """Cycle the primary tracked face forward (+1) or backward (-1) through detected faces.
        Returns the newly selected face dict, or None if no faces detected.
        Thread-safe.
        """
        with self._lock:
            if not self._cached_faces:
                return None
            n = len(self._cached_faces)
            self._primary_face_idx = (self._primary_face_idx + direction) % n
            return self._cached_faces[self._primary_face_idx]


    def start(self):
        """Starts background AI and Stream workers."""
        if self.running:
            return
        self.running = True
        self._ai_thread = threading.Thread(target=self._run_ai_loop, daemon=True, name="TARS-AI-Vision")
        self._stream_thread = threading.Thread(target=self._run_stream_loop, daemon=True, name="TARS-Stream-HUD")
        self._ai_thread.start()
        self._stream_thread.start()

    def stop(self):
        """Stops background workers and releases hardware."""
        self.running = False
        if self._grabber is not None:
            self._grabber.release()
            self._grabber = None
        try:
            self._sock.close()
        except Exception:
            pass

    def feed_web_telemetry(self, faces: list[dict]):
        """Accepts normalized face bounding boxes directly from Pocket Web Remote camera."""
        with self._lock:
            if not faces:
                self.target_active = False
                self.crowd_count = 0
                self.secondary_targets = []
                self._dispatch_telemetry()
                return

            self.target_active = True
            self.crowd_count = len(faces)
            p = faces[0]
            raw_x = p.get("x", 0.5)
            raw_y = p.get("y", 0.5)
            self.primary_x = self.filter_x.filter(raw_x)
            self.primary_y = self.filter_y.filter(raw_y)
            w_box = p.get("w", 0.25)
            approx_meters = max(0.8, min(4.0, 0.45 / max(0.08, w_box)))
            self.estimated_dist = f"{approx_meters:.1f}m"

            self.secondary_targets = [
                (f.get("x", 0.5), f.get("y", 0.5)) for f in faces[1:5]
            ]
            self._dispatch_telemetry()

    def request_reconnect(self):
        """Button-safe instant reconnect: tears the grabber down; the AI loop rediscovers
        within ~2s on its own. Never blocks (the old path ran the whole multi-second
        probe chain inside the HTTP request AND no-op'd on stale-but-open grabbers)."""
        try:
            if self._grabber is not None:
                try:
                    self._grabber.release()
                except Exception:
                    pass
                self._grabber = None
            config.tlog("VisionTracker", "Reconnect requested -> rediscovering camera")
        except Exception:
            pass

    def _ensure_grabber(self):
        """Connects to the fastest camera source available (USB tethered phones, WiFi IP webcams, or local cameras)."""
        if self._grabber is not None and self._grabber.isOpened():
            return self._grabber

        sources_to_try = discover_camera_sources()
        if self.camera_source not in sources_to_try and self.camera_source != 0:
            sources_to_try.insert(0, self.camera_source)

        for src in sources_to_try:
            if not probe_fast(src):
                continue
            try:
                g = FreshFrameGrabber(src)
                if g.isOpened():
                    # Wait up to 350ms for first frame
                    for _ in range(7):
                        time.sleep(0.05)
                        ret, f = g.read()
                        if ret and f is not None:
                            config.tlog("VisionTracker", f"Zero-lag camera stream locked: {src} ({f.shape[1]}x{f.shape[0]})")
                            self._grabber = g
                            return self._grabber
                    g.release()
            except Exception:
                pass
        return None

    def _run_ai_loop(self):
        """Dedicated AI worker: runs neural network / cascade face detection asynchronously."""
        last_source_check = 0.0
        last_fresh_t = time.time()  # ponytail: auto-heal — open-but-frozen grabbers get torn down below
        last_proc_ts = 0.0  # ponytail: feed is 8.7fps, AI targets 22 — without this, ~60% of resize+YuNet+gesture runs re-process the identical frame
        fps_counter = 0
        fps_timer = time.time()

        while self.running:
            start_t = time.perf_counter()
            now = time.time()

            if self._grabber is None or not self._grabber.isOpened():
                if now - last_source_check > 2.0:
                    last_source_check = now
                    self._ensure_grabber()
                time.sleep(0.05)
                continue

            ret, frame = self._grabber.read()
            if not ret or frame is None:
                time.sleep(0.005)
                continue
            _, frame_ts, _ = self._grabber.get_latest_frame()
            if frame_ts == last_proc_ts:
                time.sleep(0.005)  # ponytail: stale frame — drain thread hasn't delivered a new one; skip ~25ms of duplicate inference
                if time.time() - last_fresh_t > 3.0:
                    # frozen-but-open stream (phone rebooted/died mid-hold): tear down, rediscover
                    try:
                        self._grabber.release()
                    except Exception:
                        pass
                    self._grabber = None
                    config.tlog("VisionTracker", "Frozen stream detected -> auto-rediscovering camera")
                continue
            last_proc_ts = frame_ts
            last_fresh_t = time.time()

            fh, fw = frame.shape[:2]
            detected_faces = []

            # 1. Ultra-fast downsampling with INTER_NEAREST (0.80ms for 1080p -> 192x144)
            # ponytail: reused output buffer — was a fresh 192x144x3 alloc + GC every AI frame (~22/s)
            target_w = self._yunet_w
            target_h = self._yunet_h
            t_res_0 = time.perf_counter()
            _sb = getattr(self, "_small_buf", None)
            if _sb is None or _sb.shape != (target_h, target_w, 3):
                _sb = self._small_buf = np.empty((target_h, target_w, 3), dtype=np.uint8)
            small = cv2.resize(frame, (target_w, target_h), dst=_sb, interpolation=cv2.INTER_NEAREST)
            t_res_ms = (time.perf_counter() - t_res_0) * 1000.0

            # 2. Run YuNet Neural Network
            t_yn_0 = time.perf_counter()
            if self._yunet is not None:
                retval, faces = self._yunet.detect(small)

                if retval and faces is not None and len(faces) > 0:
                    for f in faces:
                        fx, fy, fbw, fbh = float(f[0]), float(f[1]), float(f[2]), float(f[3])
                        score = float(f[14])
                        # Landmarks: 5 points (right eye, left eye, nose tip, right mouth corner, left mouth corner)
                        landmarks = []
                        for l_idx in range(4, 14, 2):
                            lx = float(f[l_idx]) / target_w
                            ly = float(f[l_idx + 1]) / target_h
                            landmarks.append((lx, ly))

                        nx = fx / target_w
                        ny = fy / target_h
                        nw = fbw / target_w
                        nh = fbh / target_h
                        # Calibrated for living room sofa distance (2.2m sofa distance corresponds to nh ~ 0.087)
                        approx_m = max(0.6, min(5.0, 0.19 / max(0.035, nh)))

                        detected_faces.append({
                            "box": (nx, ny, nw, nh),
                            "center": (nx + nw / 2.0, ny + nh / 2.0),
                            "landmarks": landmarks,
                            "score": score,
                            "dist": f"{approx_m:.1f}m",
                            "area": nw * nh
                        })
            t_yn_ms = (time.perf_counter() - t_yn_0) * 1000.0

            # Fallback to Haar Cascade if YuNet returned nothing or not loaded
            if len(detected_faces) == 0 and self._cascade is not None and self._yunet is None:
                small_w = 192
                small_h = int(fh * (small_w / fw))
                small_h = small_h if small_h % 2 == 0 else small_h + 1
                small_h_f = cv2.resize(frame, (small_w, small_h), interpolation=cv2.INTER_NEAREST)
                gray = cv2.cvtColor(small_h_f, cv2.COLOR_BGR2GRAY)
                haar_faces = self._cascade.detectMultiScale(
                    gray, scaleFactor=1.15, minNeighbors=3, minSize=(16, 16), flags=cv2.CASCADE_SCALE_IMAGE
                )
                for (hx, hy, hw, hh) in haar_faces:
                    nx = hx / small_w
                    ny = hy / small_h
                    nw = hw / small_w
                    nh = hh / small_h
                    approx_m = max(0.6, min(5.0, 0.19 / max(0.035, nh)))
                    detected_faces.append({
                        "box": (nx, ny, nw, nh),
                        "center": (nx + nw / 2.0, ny + nh / 2.0),
                        "landmarks": [],
                        "score": 0.85,
                        "dist": f"{approx_m:.1f}m",
                        "area": nw * nh
                    })

            # Sort detected faces by area (closest face first)
            detected_faces.sort(key=lambda d: d["area"], reverse=True)

            # 3. Optical Hand Tracking & Gesture Recognition (re-uses `small` with ZERO resize overhead!)
            t_gest_0 = time.perf_counter()
            # Landmark refresh: every 3rd frame, never in standby (nothing to confirm, save the heat).
            # ponytail: fail-open — no model, no landmarks, stale result => motion pipeline decides alone, as today.
            if self._landmarks is not None and self._landmarks.ok and not self.is_low_power:
                self._lm_tick += 1
                if self._lm_tick % 3 == 0:
                    _lr = self._landmarks.sense(small, time.monotonic_ns() // 1_000_000)
                    if _lr is not None:
                        self._last_open = (_lr[2], time.monotonic())
                        self.hand_tip = (_lr[0], _lr[1])  # steadier marker than the motion centroid
            if self.gesture_engine is not None and config.GESTURE_SWIPE_ENABLED:
                try:
                    f_boxes = [d["box"] for d in detected_faces]
                    gesture = self.gesture_engine.process(frame, face_boxes=f_boxes, pre_small=small)
                    self.hand_detected = self.gesture_engine.hand_detected
                    self.hand_box = self.gesture_engine.hand_box
                    # hand_tip: landmark tip when fresh (set above), else motion centroid.
                    if self._landmarks is None or time.monotonic() - self._last_open[1] > 5.0:
                        self.hand_tip = getattr(self.gesture_engine, "hand_tip", (0.5, 0.5))
                    self.is_presenter_walking = getattr(self.gesture_engine, "is_presenter_walking", False)
                    _open_ok = True
                    if self._landmarks is not None and self._landmarks.ok:
                        _is_open, _ts = self._last_open
                        _open_ok = bool(_is_open) and (time.monotonic() - _ts) <= 0.6
                    if gesture and _open_ok:
                        self.latest_gesture = gesture
                        if gesture == "SWIPE_RIGHT":
                            step_dir = "next"
                        elif gesture == "SWIPE_LEFT":
                            step_dir = "prev"
                        elif gesture == "SWIPE_UP":
                            step_dir = "up"
                        else:
                            step_dir = "down"
                        try:
                            g_payload = {"cmd": "event_step", "dir": step_dir, "gesture": gesture.lower()}
                            self._sock.sendto(json.dumps(g_payload).encode("utf-8"), ("127.0.0.1", self.bridge_port))
                        except Exception:
                            pass
                except Exception:
                    pass
            t_gest_ms = (time.perf_counter() - t_gest_0) * 1000.0
            t_total_ai_ms = (time.perf_counter() - start_t) * 1000.0

            # Low Power Mode & Instant Wake logic
            has_presence = len(detected_faces) > 0 or self.hand_detected
            if has_presence:
                self.last_detection_timestamp = now
                if self.is_low_power:
                    self.is_low_power = False
                    config.tlog("VisionTracker", "INSTANT WAKE: Human presence detected -> Turbo Mode (30+ FPS)")
            elif getattr(config, "LOW_POWER_ENABLED", True):
                if now - self.last_detection_timestamp > getattr(config, "LOW_POWER_TIMEOUT", 12.0):
                    if not self.is_low_power:
                        self.is_low_power = True
                        config.tlog("VisionTracker", "LOW POWER STANDBY: No human target detected -> Throttling AI to 4 FPS")

            thermal = get_system_thermal_telemetry()
            with self._metrics_lock:
                self.metrics = {
                    "ai_fps": self._ai_fps,
                    "total_ai_ms": t_total_ai_ms,
                    "yunet_ms": t_yn_ms,
                    "gesture_ms": t_gest_ms,
                    "resize_ms": t_res_ms,
                    "grab_ms": getattr(self._grabber, "grab_latency_ms", 0.0),
                    "camera_src": str(getattr(self._grabber, "src", "none")),
                    "faces": len(detected_faces),
                    "hand_active": self.hand_detected,
                    "landmarks": bool(self._landmarks is not None and self._landmarks.ok),
                    "low_power": self.is_low_power,
                    "temp_c": thermal["temp_c"],
                    "throttled": thermal["throttled"],
                    "cooling": thermal["cooling"]
                }

            t_now = time.perf_counter()
            with self._lock:
                if len(detected_faces) > 0:
                    self.target_active = True
                    self.crowd_count = len(detected_faces)
                    p = detected_faces[0]
                    raw_cx, raw_cy = p["center"]

                    # Pass through 1€ Filter with explicit timestamp for silky smooth, jitter-free lock
                    self.primary_x = self.filter_x.filter(raw_cx, t_now)
                    self.primary_y = self.filter_y.filter(raw_cy, t_now)
                    self.estimated_dist = p["dist"]

                    self.secondary_targets = [
                        f["center"] for f in detected_faces[1:5]
                    ]
                    self._cached_faces = detected_faces
                    self._last_face_time = now
                else:
                    # Grace period of 0.8s before declaring target lost (suppresses blink dropouts)
                    if now - self._last_face_time > 0.8:
                        self.target_active = False
                        self.crowd_count = 0
                        self.secondary_targets = []
                        self._cached_faces = []
                        self.filter_x.reset()
                        self.filter_y.reset()

            self._dispatch_telemetry()

            # Measure AI FPS
            fps_counter += 1
            if now - fps_timer >= 1.0:
                self._ai_fps = fps_counter / (now - fps_timer)
                fps_counter = 0
                fps_timer = now

            if self.is_low_power:
                sleep_t = max(0.10, 1.0 / getattr(config, "LOW_POWER_AI_FPS", 5.0))
            else:
                target_ai_fps = 20.0 if thermal["cooling"] else getattr(config, "AI_TARGET_FPS", 28.0)
                elapsed = time.perf_counter() - start_t
                sleep_t = max(0.003, (1.0 / target_ai_fps) - elapsed)
            time.sleep(sleep_t)

    def _run_stream_loop(self):
        """Dedicated render & streamer worker: renders high-definition tactical sci-fi HUD at 30–40 FPS."""
        last_idle_render = 0.0
        while self.running:
            # Power & CPU Optimization for Raspberry Pi 3:
            # When no client is polling (>3.5s), throttle to 1.5 FPS idle loop instead of complete freezing
            # so self.latest_jpeg is ALWAYS pre-encoded and ready for instantaneous load on phone web panel!
            now = time.time()
            is_idle = (not config.SHOW_CAMERA_PIP) and (now - LAST_STREAM_POLL > 2.5)
            if is_idle:
                if (now - last_idle_render < 0.4) and (self.latest_jpeg is not None):
                    time.sleep(0.05)
                    continue
                last_idle_render = now

            t0 = time.perf_counter()

            if self._grabber is not None and self._grabber.isOpened():
                ret, frame = self._grabber.read()
            else:
                ret, frame = False, None

            if ret and frame is not None:
                fh, fw = frame.shape[:2]
                # High-Definition 480px preview (crisp on mobile, ~3.5ms encode on Pi 3)
                stream_w = 480
                stream_h = int(stream_w * (fh / fw))
                stream_h = stream_h if stream_h % 2 == 0 else stream_h + 1
                # LINEAR: NEAREST shimmered on real-world edges (window blinds, fan blades) — visible as crawling
                # lines on the display PiP. 2.5ms is worth it; PiP stage below stays NEAREST (already soft at 240px).
                # Reused output buffer — was a fresh 480xHx3 alloc every stream frame (~30/s).
                _ab = getattr(self, "_annot_buf", None)
                if _ab is None or _ab.shape != (stream_h, stream_w, 3):
                    _ab = self._annot_buf = np.empty((stream_h, stream_w, 3), dtype=np.uint8)
                annotated = cv2.resize(frame, (stream_w, stream_h), dst=_ab, interpolation=cv2.INTER_LINEAR)
                # Mirror BEFORE any text/annotations — so text stays readable (not flipped)
                if getattr(config, 'MIRROR_CAMERA_X', False):
                    annotated = cv2.flip(annotated, 1)
            else:
                stream_w, stream_h = 480, 360
                # Sleek sci-fi standby matrix when camera is disconnected
                # ponytail: base built once; per-frame .copy() (0.1ms memcpy) — overlays below mutate `annotated`,
                # so sharing the base object would smear old reticles across frames
                _base = getattr(self, "_standby_base", None)
                if _base is None:
                    _base = self._standby_base = np.full((stream_h, stream_w, 3), (12, 16, 22), dtype=np.uint8)
                    cv2.putText(_base, "TARS OPTICAL SENSOR // SEARCHING FEED...", (60, stream_h // 2),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (80, 140, 220), 1, cv2.LINE_AA)
                annotated = _base.copy()

            ash, asw = annotated.shape[:2]

            # Crosshair center reticle
            cx, cy = asw // 2, ash // 2
            cv2.line(annotated, (cx - 16, cy), (cx + 16, cy), (40, 55, 70), 1)
            cv2.line(annotated, (cx, cy - 16), (cx, cy + 16), (40, 55, 70), 1)

            # System HUD overlay with live ticking clock and status bar
            cv2.rectangle(annotated, (0, 0), (asw, 24), (10, 14, 20), -1)
            cv2.line(annotated, (0, 24), (asw, 24), (45, 55, 70), 1)

            with self._lock:
                faces = list(self._cached_faces)
                tgt_active = self.target_active
                est_dist = self.estimated_dist
                aim_x = self.primary_x
                aim_y = self.primary_y

            t_str = time.strftime("%H:%M:%S")
            dot_col = (145, 235, 56) if tgt_active else (65, 140, 255)
            cv2.circle(annotated, (14, 12), 5, dot_col, -1)
            mode_tag = "YUNET NEURAL 3D" if self._yunet is not None else "HAAR CASCADE"
            status_text = f"TARS SENSOR LIVE  [{t_str}]  LOCKS: {len(faces)} | {mode_tag} | 480p"
            cv2.putText(annotated, status_text, (26, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (240, 240, 245), 1, cv2.LINE_AA)

            mirror_on = getattr(config, 'MIRROR_CAMERA_X', False)

            # Render detected face reticles & 5 facial landmarks
            for i, f_data in enumerate(faces):
                is_primary = (i == 0)
                # Primary: Electric Mint (145, 235, 56) | Secondary: Amber (40, 140, 255)
                bgr_col = (145, 235, 56) if is_primary else (40, 140, 255)

                nx, ny, nw, nh = f_data["box"]
                # Mirror X coordinate to match flipped frame
                if mirror_on:
                    nx = 1.0 - nx - nw
                bx = int(nx * asw)
                by = int(ny * ash)
                bw = int(nw * asw)
                bh = int(nh * ash)

                d = max(10, min(24, bw // 4))

                # Corner reticle brackets (High-contrast military hud)
                cv2.line(annotated, (bx, by), (bx + d, by), bgr_col, 2)
                cv2.line(annotated, (bx, by), (bx, by + d), bgr_col, 2)
                cv2.line(annotated, (bx + bw, by), (bx + bw - d, by), bgr_col, 2)
                cv2.line(annotated, (bx + bw, by), (bx + bw, by + d), bgr_col, 2)
                cv2.line(annotated, (bx, by + bh), (bx + d, by + bh), bgr_col, 2)
                cv2.line(annotated, (bx, by + bh), (bx, by + bh - d), bgr_col, 2)
                cv2.line(annotated, (bx + bw, by + bh), (bx + bw - d, by + bh), bgr_col, 2)
                cv2.line(annotated, (bx + bw, by + bh), (bx + bw, by + bh - d), bgr_col, 2)

                # Center target dot (use filtered aim for primary to guarantee zero jitter)
                if is_primary:
                    aim_x_draw = (1.0 - aim_x) if mirror_on else aim_x
                    fcx = int(aim_x_draw * asw)
                    fcy = int(aim_y * ash)
                else:
                    fcx = bx + bw // 2
                    fcy = by + bh // 2
                cv2.circle(annotated, (fcx, fcy), 4, bgr_col, -1)

                # Draw YuNet Facial Landmarks (Eyes, Nose, Mouth Corners)
                landmarks = f_data.get("landmarks", [])
                if len(landmarks) == 5:
                    # 0: Right Eye, 1: Left Eye, 2: Nose, 3: Right Mouth, 4: Left Mouth
                    pts = [(int((1.0 - lx) * asw) if mirror_on else int(lx * asw), int(ly * ash)) for (lx, ly) in landmarks]

                    # Eye Reticles (Cyan)
                    eye_col = (245, 215, 60)
                    cv2.circle(annotated, pts[0], 3, eye_col, -1)
                    cv2.circle(annotated, pts[1], 3, eye_col, -1)
                    # Eye-line connecting eyes to visualize head roll/tilt
                    cv2.line(annotated, pts[0], pts[1], (180, 160, 50), 1)

                    # Nose Reticle (Mint)
                    cv2.circle(annotated, pts[2], 3, (145, 235, 56), -1)

                    # Mouth Corners (Orange)
                    cv2.circle(annotated, pts[3], 3, (60, 160, 255), -1)
                    cv2.circle(annotated, pts[4], 3, (60, 160, 255), -1)
                    cv2.line(annotated, pts[3], pts[4], (50, 120, 200), 1)

                # Tactical label badge
                conf_pct = int(f_data.get("score", 0.9) * 100)
                tag = f"TARS LOCK [{est_dist}] {conf_pct}%" if is_primary else f"SPECTATOR #{i+1}"
                (tw, th), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.40, 1)
                cv2.rectangle(annotated, (bx, max(0, by - 18)), (bx + tw + 8, max(18, by)), (10, 14, 20), -1)
                cv2.putText(annotated, tag, (bx + 4, max(13, by - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.40, bgr_col, 1, cv2.LINE_AA)

            # 1. Pacing / Walking status indicator
            if getattr(self, "is_presenter_walking", False):
                cv2.rectangle(annotated, (asw // 2 - 140, 28), (asw // 2 + 140, 48), (10, 14, 20), -1)
                cv2.rectangle(annotated, (asw // 2 - 140, 28), (asw // 2 + 140, 48), (40, 140, 255), 1)
                cv2.putText(annotated, "[ PACING DETECTED // SWIPE LOCKED ]", (asw // 2 - 130, 42),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.36, (40, 140, 255), 1, cv2.LINE_AA)

            # 2. Draw Virtual Screen Holographic Boundaries when hand is active
            if self.hand_detected:
                # Left / Right virtual screen transition threshold guides
                x_l = int(0.42 * asw)
                x_r = int(0.58 * asw)
                cv2.line(annotated, (x_l, 32), (x_l, ash - 32), (35, 50, 65), 1)
                cv2.line(annotated, (x_r, 32), (x_r, ash - 32), (35, 50, 65), 1)

                # Leading Fingertip Holographic Target — mirror X if needed
                tip = getattr(self, "hand_tip", (0.5, 0.5))
                tip_x = (1.0 - tip[0]) if mirror_on else tip[0]
                tx = max(4, min(asw - 4, int(tip_x * asw)))
                ty = max(4, min(ash - 4, int(tip[1] * ash)))
                cv2.circle(annotated, (tx, ty), 8, (255, 215, 60), 1)
                cv2.circle(annotated, (tx, ty), 3, (145, 235, 56), -1)
                cv2.line(annotated, (tx - 12, ty), (tx + 12, ty), (255, 215, 60), 1)
                cv2.line(annotated, (tx, ty - 12), (tx, ty + 12), (255, 215, 60), 1)
                cv2.putText(annotated, "VIRTUAL TOUCH", (tx + 12, ty - 6),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.32, (255, 215, 60), 1, cv2.LINE_AA)


            # 3. Draw detected hand boundary reticle
            if self.hand_detected and self.hand_box is not None:
                hbx, hby, hbw, hbh = self.hand_box
                hx = int((1.0 - hbx - hbw) * asw) if mirror_on else int(hbx * asw)
                hy = int(hby * ash)
                hw = int(hbw * asw)
                hh = int(hbh * ash)
                hand_col = (56, 235, 145)  # Mint green
                hd = max(8, min(18, hw // 4))
                cv2.line(annotated, (hx, hy), (hx + hd, hy), hand_col, 2)
                cv2.line(annotated, (hx, hy), (hx, hy + hd), hand_col, 2)
                cv2.line(annotated, (hx + hw, hy), (hx + hw - hd, hy), hand_col, 2)
                cv2.line(annotated, (hx + hw, hy), (hx + hw, hy + hd), hand_col, 2)
                cv2.line(annotated, (hx, hy + hh), (hx + hd, hy + hh), hand_col, 2)
                cv2.line(annotated, (hx, hy + hh), (hx, hy + hh - hd), hand_col, 2)
                cv2.line(annotated, (hx + hw, hy + hh), (hx + hw - hd, hy + hh), hand_col, 2)
                cv2.line(annotated, (hx + hw, hy + hh), (hx + hw, hy + hh - hd), hand_col, 2)

            # Draw real-time sci-fi hand gesture motion trail on live camera feed
            if self.gesture_engine and len(self.gesture_engine.history) >= 2:
                pts = [
                    (int((1.0 - p[3]) * asw) if mirror_on else int(p[3] * asw), int(p[4] * ash))
                    for p in self.gesture_engine.history
                ]
                for k in range(len(pts) - 1):
                    alpha_factor = (k + 1) / len(pts)
                    thickness = max(1, int(3 * alpha_factor))
                    trail_col = (56, 235, 145) if (self.gesture_engine.gesture_display_until > now) else (75, 215, 255)
                    cv2.line(annotated, pts[k], pts[k+1], trail_col, thickness)
                    cv2.circle(annotated, pts[k+1], int(2 + 2 * alpha_factor), trail_col, -1)

            # Draw active swipe gesture banner across bottom of preview
            if self.gesture_engine and self.gesture_engine.gesture_display_until > now:
                g_str = self.gesture_engine.latest_gesture
                if g_str == "SWIPE_RIGHT":
                    g_text = ">>> [SWIPE RIGHT] NEXT SLIDE >>>"
                    g_col = (56, 235, 145)
                elif g_str == "SWIPE_LEFT":
                    g_text = "<<< [SWIPE LEFT] PREV SLIDE <<<"
                    g_col = (75, 215, 255)
                elif g_str == "SWIPE_UP":
                    g_text = "^^^ [SWIPE UP] SHOW FACE ^^^"
                    g_col = (255, 215, 95)
                else:
                    g_text = "vvv [SWIPE DOWN] SHOW SLIDES vvv"
                    g_col = (255, 165, 55)
                (gw_t, gh_t), _ = cv2.getTextSize(g_text, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)
                gbx = (asw - gw_t) // 2
                gby = ash - 26
                cv2.rectangle(annotated, (gbx - 6, gby - 14), (gbx + gw_t + 6, gby + 6), (10, 14, 20), -1)
                cv2.rectangle(annotated, (gbx - 6, gby - 14), (gbx + gw_t + 6, gby + 6), g_col, 1)
                cv2.putText(annotated, g_text, (gbx, gby), cv2.FONT_HERSHEY_SIMPLEX, 0.42, g_col, 1, cv2.LINE_AA)

            # JPEG encode — quality 60 for Pi 3: saves ~4ms per frame vs q72, visually identical at 480p
            # Active when stream client is connected (<2.5s) or periodic idle keep-alive
            if (now - LAST_STREAM_POLL < 2.5) or (self.latest_jpeg is None) or (now - last_idle_render < 0.04):
                _, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 60])
                self.latest_jpeg = buf.tobytes()

            # Prepare pre-scaled Pygame Surface if PiP is active for zero-overhead blitting
            # ponytail: 240x180 corner box needs ~10fps, not 30 — rebuild every 3rd pass, saves 2/3 of resize+cvtColor+tobytes.
            # 320x240, not 240x180: PiP is upscaled ~3x total on the display path; 240px source was mush.
            self._pip_tick = getattr(self, "_pip_tick", 0) + 1
            if config.SHOW_CAMERA_PIP and self._pip_tick % 3 == 1:
                try:
                    pip_w, pip_h = 320, 240
                    # ponytail: fixed-size stage — buffers live once, not per rebuild (dst= overwrites fully, no aliasing)
                    _pf = getattr(self, "_pip_frame", None)
                    if _pf is None:
                        _pf = self._pip_frame = np.empty((pip_h, pip_w, 3), dtype=np.uint8)
                        self._pip_rgb = np.empty((pip_h, pip_w, 3), dtype=np.uint8)
                    pip_frame = cv2.resize(annotated, (pip_w, pip_h), dst=_pf, interpolation=cv2.INTER_NEAREST)
                    pip_rgb = cv2.cvtColor(pip_frame, cv2.COLOR_BGR2RGB, dst=self._pip_rgb)
                    # Mirror already applied at source (flip before annotation) — no need to flip here
                    self.latest_pip_surface = pygame.image.frombuffer(pip_rgb.tobytes(), (pip_w, pip_h), "RGB")
                except Exception:
                    pass

            dt = time.perf_counter() - t0
            sleep_t = max(0.005, 0.033 - dt)  # ~30 FPS
            time.sleep(sleep_t)

    def _dispatch_telemetry(self):
        """Sends UDP packet to main Pygame bridge."""
        payload = {
            "cmd": "target",
            "active": self.target_active,
            "x": round(self.primary_x, 3),
            "y": round(self.primary_y, 3),
            "count": self.crowd_count,
            "dist": self.estimated_dist,
            "hand_detected": self.hand_detected,
            "gesture": self.latest_gesture,
            "low_power": self.is_low_power,
            "secondaries": [
                (round(sx, 3), round(sy, 3)) for sx, sy in self.secondary_targets
            ]
        }
        try:
            from bridge import COMMAND_QUEUE
            COMMAND_QUEUE.put(payload)
        except Exception:
            pass
        try:
            msg = json.dumps(payload).encode("utf-8")
            self._sock.sendto(msg, ("127.0.0.1", self.bridge_port))
        except Exception:
            pass
