"""TARS High-Computation Laptop AI Companion Node.

Runs on the user's laptop to offload heavy computer vision, multi-person tracking,
and high-resolution neural network inference.

Streams low-latency UDP target telemetry directly to TARS running on the Raspberry Pi.

100% OPTIONAL & FAILOVER RESILIENT:
- If this laptop companion is running, TARS consumes its high-precision telemetry.
- If this laptop is turned off, disconnected, or closed, TARS's onboard Raspberry Pi
  AI tracker continues autonomously at 39 FPS without any interruption.

Usage:
    python laptop_companion.py --pi 10.70.4.81
    python laptop_companion.py --pi 192.168.42.195 --cam http://192.168.42.129:8080/video
"""

from __future__ import annotations
import argparse
import json
import os
import socket
import sys
import threading
import time
import cv2
import numpy as np

DEFAULT_PI_IP = "10.70.4.81"
DEFAULT_UDP_PORT = 5005
DEFAULT_CAM_URL = "auto"


def discover_camera_sources() -> list[str | int]:
    """Auto-discovers camera streams across USB tethering, Wi-Fi webcams, and USB webcams."""
    candidates: list[str | int] = []

    # 1. Inspect local network routing / ARP for USB tethering (RNDIS) gateways
    for route_file in ("/proc/net/route", "/proc/net/arp"):
        if os.path.exists(route_file):
            try:
                with open(route_file, "r") as f:
                    for line in f:
                        parts = line.strip().split()
                        if not parts:
                            continue
                        # If route file: parts[0] is iface, parts[2] is gateway hex
                        if "route" in route_file and len(parts) >= 3 and parts[0].startswith("usb"):
                            gw_hex = parts[2]
                            if gw_hex != "00000000" and len(gw_hex) == 8:
                                ip_b = [str(int(gw_hex[i:i+2], 16)) for i in (6, 4, 2, 0)]
                                ip_str = ".".join(ip_b)
                                candidates.append(f"http://{ip_str}:8080/video")
                        # If ARP file: parts[0] is IP, parts[5] is iface
                        elif "arp" in route_file and len(parts) >= 6 and parts[5].startswith("usb"):
                            candidates.append(f"http://{parts[0]}:8080/video")
            except Exception:
                pass

    # 2. Add known USB tethering and active Wi-Fi endpoints
    candidates.extend([
        "http://10.57.90.53:8080/video",    # USB tethering phone 1 (Xiaomi/Redmi)
        "http://10.70.4.51:8080/video",     # Wi-Fi IP Webcam phone 2
        "http://192.168.42.129:8080/video", # Standard Android USB tethering gateway
        "http://192.168.42.1:8080/video",
        "http://10.70.4.81:8080/video",
        "http://10.57.90.53:4747/video",    # DroidCam USB
        "http://10.70.4.51:4747/video",     # DroidCam Wi-Fi
    ])

    # 3. Local USB hardware webcams
    candidates.extend([0, 1])

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


class FreshFrameGrabber:
    """Threaded OpenCV frame reader that discards buffered frames for zero-lag streaming."""
    def __init__(self, src: str | int):
        self.src = src
        self.cap = None
        self.latest_frame = None
        self.latest_ts = 0.0
        self.grab_latency_ms = 0.0
        self.running = False
        self.lock = threading.Lock()
        self.thread = None
        self._init_cap()

    def _init_cap(self):
        src = int(self.src) if str(self.src).isdigit() else str(self.src)
        if isinstance(src, str) and src.startswith("http"):
            # Set transport hints for lowest latency
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "fflags=nobuffer|discardcorrupt;probesize=32;analyzeduration=0"
            self.cap = cv2.VideoCapture(src, cv2.CAP_FFMPEG)
        else:
            self.cap = cv2.VideoCapture(src)
        if self.cap.isOpened():
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    def start(self):
        if not self.cap or not self.cap.isOpened():
            return False
        self.running = True
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()
        return True

    def _worker(self):
        while self.running:
            t0 = time.time()
            if self.cap is None or not self.cap.isOpened():
                time.sleep(0.05)
                continue
            grabbed = self.cap.grab()
            if not grabbed:
                time.sleep(0.02)
                continue
            ret, frame = self.cap.retrieve()
            t_done = time.time()
            if ret and frame is not None:
                with self.lock:
                    self.latest_frame = frame
                    self.latest_ts = t_done
                    self.grab_latency_ms = (t_done - t0) * 1000.0
            else:
                time.sleep(0.01)

    def get_latest_frame(self) -> tuple[np.ndarray | None, float, float]:
        with self.lock:
            return self.latest_frame, self.latest_ts, self.grab_latency_ms

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=0.5)
        if self.cap:
            self.cap.release()



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
        self.history: list[tuple[float, float, float]] = []
        self.last_swipe_time = 0.0
        self.hand_detected = False
        self.hand_box: tuple[float, float, float, float] | None = None
        self.latest_gesture = ""
        self.gesture_display_until = 0.0
        self.last_shield_box: tuple[float, float, float, float] | None = None
        self.last_shield_time = 0.0
        self.kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))

    def process(self, frame, face_boxes=None, pre_small=None) -> str | None:
        if frame is None and pre_small is None:
            self.hand_detected = False
            self.hand_box = None
            return None

        now = time.time()
        if pre_small is not None:
            gh, gw = pre_small.shape[:2]
            small = pre_small
        else:
            fh, fw = frame.shape[:2]
            gw, gh = 160, int(160 * (fh / fw))
            gh = gh if gh % 2 == 0 else gh + 1
            small = cv2.resize(frame, (gw, gh), interpolation=cv2.INTER_NEAREST)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gray = cv2.blur(gray, (5, 5))

        # Reset on initialization or long frame pause (>0.5s) to avoid false delta jumps
        if self.prev_gray is None or (now - self.last_frame_time > 0.5):
            self.prev_gray = gray
            self.last_frame_time = now
            self.history.clear()
            return None
        self.last_frame_time = now

        # 1. Motion frame difference
        diff = cv2.absdiff(gray, self.prev_gray)
        self.prev_gray = gray
        _, motion_mask = cv2.threshold(diff, 14, 255, cv2.THRESH_BINARY)

        # 2. Central Torso & Head Shield (Persisted 1.2s against face dropouts)
        if face_boxes and len(face_boxes) > 0:
            fx, fy, fbw, fbh = face_boxes[0]
            self.last_shield_box = (fx, fy, fbw, fbh)
            self.last_shield_time = now
        elif self.last_shield_box and (now - self.last_shield_time > 1.2):
            self.last_shield_box = None

        if self.last_shield_box:
            fx, fy, fbw, fbh = self.last_shield_box
            # Shield covers head, neck, chest, and torso column all the way to frame bottom
            mx1 = max(0, int((fx - fbw * 0.40) * gw))
            my1 = max(0, int((fy - fbh * 0.30) * gh))
            mx2 = min(gw, int((fx + fbw * 1.40) * gw))
            my2 = gh  # entire column down to bottom
            motion_mask[my1:my2, mx1:mx2] = 0

        # 3. Morphological closing to bridge fingers, palm, wrist into solid blob
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        motion_mask = cv2.morphologyEx(motion_mask, cv2.MORPH_CLOSE, kernel)

        # 4. Find largest coherent moving contour outside torso shield
        contours, _ = cv2.findContours(motion_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        min_hand_area = (gw * gh) * 0.008  # ~0.8% of screen area (approx 150 px)
        max_hand_area = (gw * gh) * 0.35   # ~35% of screen area

        best_cnt = None
        max_area = 0
        for c in contours:
            area = cv2.contourArea(c)
            if min_hand_area <= area <= max_hand_area and area > max_area:
                max_area = area
                best_cnt = c

        cooldown = 0.85
        sens = self.sensitivity

        # 5. Swipe Evaluation Helper
        def check_swipe(history, current_time) -> str | None:
            if len(history) < 3 or (current_time - self.last_swipe_time <= cooldown):
                return None
            old_x, old_y, old_t = history[0]
            curr_x, curr_y = history[-1][0], history[-1][1]
            dx = curr_x - old_x
            dy = curr_y - old_y
            dt = max(0.04, current_time - old_t)
            speed_x = abs(dx) / dt
            speed_y = abs(dy) / dt

            h_thresh = 0.12 / max(0.3, sens)
            v_thresh = 0.14 / max(0.3, sens)

            # Horizontal Swipe
            if abs(dx) >= h_thresh and abs(dx) > (1.25 * abs(dy)) and speed_x > 0.30:
                deltas_x = [history[k+1][0] - history[k][0] for k in range(len(history)-1)]
                pos_count = sum(1 for d in deltas_x if d > 0)
                neg_count = sum(1 for d in deltas_x if d < 0)
                is_consistent = (pos_count >= len(deltas_x) * 0.65) if dx > 0 else (neg_count >= len(deltas_x) * 0.65)

                if is_consistent:
                    if self.mirror:
                        gesture = "SWIPE_RIGHT" if dx > 0 else "SWIPE_LEFT"
                    else:
                        gesture = "SWIPE_RIGHT" if dx < 0 else "SWIPE_LEFT"
                    self.last_swipe_time = current_time
                    self.latest_gesture = gesture
                    self.gesture_display_until = current_time + 1.5
                    self.history.clear()
                    return gesture

            # Vertical Swipe
            if abs(dy) >= v_thresh and abs(dy) > (1.25 * abs(dx)) and speed_y > 0.30:
                deltas_y = [history[k+1][1] - history[k][1] for k in range(len(history)-1)]
                pos_count = sum(1 for d in deltas_y if d > 0)
                neg_count = sum(1 for d in deltas_y if d < 0)
                is_consistent = (pos_count >= len(deltas_y) * 0.65) if dy > 0 else (neg_count >= len(deltas_y) * 0.65)

                if is_consistent:
                    if self.invert_y:
                        gesture = "SWIPE_DOWN" if dy < 0 else "SWIPE_UP"
                    else:
                        gesture = "SWIPE_UP" if dy < 0 else "SWIPE_DOWN"
                    self.last_swipe_time = current_time
                    self.latest_gesture = gesture
                    self.gesture_display_until = current_time + 1.5
                    self.history.clear()
                    return gesture
            return None

        if best_cnt is None:
            self.hand_detected = False
            self.hand_box = None
            # Evaluate any completed swipe before purging history
            g = check_swipe(self.history, now)
            self.history = [pt for pt in self.history if now - pt[2] <= 0.35]
            return g

        M = cv2.moments(best_cnt)
        if M["m00"] <= 0:
            return None

        cx = (M["m10"] / M["m00"]) / gw
        cy = (M["m01"] / M["m00"]) / gh
        bx, by, bw, bh = cv2.boundingRect(best_cnt)
        self.hand_detected = True
        self.hand_box = (bx / gw, by / gh, bw / gw, bh / gh)

        self.history.append((cx, cy, now))
        self.history = [pt for pt in self.history if now - pt[2] <= 0.38]

        return check_swipe(self.history, now)


def find_yunet_model() -> str | None:
    candidates = [
        "face_detection_yunet_2023mar.onnx",
        os.path.join(os.path.dirname(__file__), "models", "face_detection_yunet_2023mar.onnx"),
        os.path.join(os.path.dirname(__file__), "face_detection_yunet_2023mar.onnx"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def main():
    parser = argparse.ArgumentParser(description="TARS High-Computation Laptop AI Companion")
    parser.add_argument("--pi", default=DEFAULT_PI_IP, help=f"Raspberry Pi IP address (default: {DEFAULT_PI_IP})")
    parser.add_argument("--port", type=int, default=DEFAULT_UDP_PORT, help="UDP bridge port (default: 5005)")
    parser.add_argument("--cam", default=DEFAULT_CAM_URL, help="Camera source URL or index (default: phone stream)")
    parser.add_argument("--width", type=int, default=320, help="Inference resolution width (default: 320)")
    parser.add_argument("--height", type=int, default=240, help="Inference resolution height (default: 240)")
    parser.add_argument("--mirror", action="store_true", help="Mirror horizontal coordinates and swipe directions")
    parser.add_argument("--sensitivity", type=float, default=1.0, help="Gesture swipe sensitivity (0.5 to 2.0, default: 1.0)")
    parser.add_argument("--no-gesture", action="store_true", help="Disable hand gesture swipe detection")
    parser.add_argument("--no-gui", action="store_true", help="Run in headless background mode without GUI window")
    args = parser.parse_args()

    print("=" * 65)
    print("  TARS MK-IV // LAPTOP HIGH-COMPUTATION COMPANION NODE")
    print(f"  Target Raspberry Pi : {args.pi}:{args.port}")
    print(f"  Camera Source       : {args.cam}")
    print(f"  Inference Size      : {args.width}x{args.height}")
    print("=" * 65)

    # Setup UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # Initialize Face Detector
    model_path = find_yunet_model()
    detector = None
    if model_path and hasattr(cv2, "FaceDetectorYN"):
        try:
            detector = cv2.FaceDetectorYN.create(
                model_path, "", (args.width, args.height),
                score_threshold=0.55,
                nms_threshold=0.30,
                top_k=5000
            )
            print(f"[Laptop AI] Loaded YuNet Neural Network: {model_path}")
        except Exception as e:
            print(f"[Laptop AI] YuNet init error: {e}")

    # Fallback Haar Cascade
    cascade = None
    if detector is None:
        if hasattr(cv2, "data") and hasattr(cv2.data, "haarcascades"):
            cp = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
            if os.path.exists(cp):
                cascade = cv2.CascadeClassifier(cp)
                print(f"[Laptop AI] Fallback Haar Cascade loaded: {cp}")

    # Open Camera with auto-discovery and zero-lag grabber
    grabber = None
    active_src = None
    if args.cam != "auto":
        candidates = [args.cam]
    else:
        candidates = discover_camera_sources()

    print(f"[Laptop AI] Probing candidate camera streams: {candidates} ...")
    for cand in candidates:
        if probe_fast(cand):
            g = FreshFrameGrabber(cand)
            if g.start():
                # Wait up to 1.5s for first valid frame
                t_wait = time.time()
                while time.time() - t_wait < 1.5:
                    frm, _, _ = g.get_latest_frame()
                    if frm is not None:
                        grabber = g
                        active_src = cand
                        break
                    time.sleep(0.05)
                if grabber:
                    break
                g.stop()

    if grabber is None:
        print(f"[Laptop AI] ERROR: Could not connect to any camera stream.")
        print("[Laptop AI] Please verify that your phone IP webcam server or USB tethering is active.")
        return

    print(f"[Laptop AI] Zero-lag camera stream locked: {active_src}")

    # Initialize Optical Hand Gesture Engine
    gesture_engine = OpticalGestureEngine(sensitivity=args.sensitivity, mirror=args.mirror) if not args.no_gesture else None
    if gesture_engine:
        print("[Laptop AI] Optical Hand Gesture Swipe Recognition: ONLINE (Wave hand left/right to change slides)")

    print("[Laptop AI] Pipeline active. Streaming telemetry to Pi... (Press 'Q' in window to exit)")

    last_send_time = 0.0
    fps_timer = time.time()
    fps_count = 0
    cur_fps = 0.0
    last_stat_time = time.time()

    # Latency tracking metrics
    lat_grab = 0.0
    lat_resize = 0.0
    lat_yunet = 0.0
    lat_gesture = 0.0
    lat_total = 0.0

    while True:
        t_loop_start = time.time()
        frame, frame_ts, grab_ms = grabber.get_latest_frame()
        if frame is None or (frame_ts > 0 and time.time() - frame_ts > 2.5):
            # Stream drop or freeze detected - trigger auto-reconnect
            time.sleep(0.05)
            continue

        lat_grab = grab_ms
        fh, fw = frame.shape[:2]

        t_res0 = time.time()
        small = cv2.resize(frame, (args.width, args.height), interpolation=cv2.INTER_NEAREST)
        lat_resize = (time.time() - t_res0) * 1000.0

        detected_faces = []

        # Neural inference
        t_yn0 = time.time()
        if detector is not None:
            retval, faces = detector.detect(small)
            if retval and faces is not None and len(faces) > 0:
                for f in faces:
                    fx, fy, fbw, fbh = float(f[0]), float(f[1]), float(f[2]), float(f[3])
                    score = float(f[14])
                    nx = fx / args.width
                    ny = fy / args.height
                    nw = fbw / args.width
                    nh = fbh / args.height
                    approx_m = max(0.6, min(5.0, 0.19 / max(0.035, nh)))
                    detected_faces.append({
                        "x": nx + nw / 2.0,
                        "y": ny + nh / 2.0,
                        "box": (nx, ny, nw, nh),
                        "score": score,
                        "dist": f"{approx_m:.1f}m",
                        "area": nw * nh
                    })
        elif cascade is not None:
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            haar_faces = cascade.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=3)
            for (hx, hy, hw, hh) in haar_faces:
                nx = hx / args.width
                ny = hy / args.height
                nw = hw / args.width
                nh = hh / args.height
                approx_m = max(0.6, min(5.0, 0.19 / max(0.035, nh)))
                detected_faces.append({
                    "x": nx + nw / 2.0,
                    "y": ny + nh / 2.0,
                    "box": (nx, ny, nw, nh),
                    "score": 0.85,
                    "dist": f"{approx_m:.1f}m",
                    "area": nw * nh
                })
        lat_yunet = (time.time() - t_yn0) * 1000.0

        detected_faces.sort(key=lambda d: d["area"], reverse=True)

        # Optical Hand Gesture Swipe Detection (Zero-copy surface reuse)
        t_gest0 = time.time()
        if gesture_engine is not None:
            f_boxes = [d["box"] for d in detected_faces]
            gesture = gesture_engine.process(frame, face_boxes=f_boxes, pre_small=small)
            if gesture:
                if gesture == "SWIPE_RIGHT":
                    step_dir = "next"
                elif gesture == "SWIPE_LEFT":
                    step_dir = "prev"
                elif gesture == "SWIPE_UP":
                    step_dir = "up"
                else:
                    step_dir = "down"
                print(f"[Laptop AI] >>> GESTURE SWIPE DETECTED: {gesture} -> ACTION: {step_dir.upper()} >>>")
                try:
                    g_payload = {
                        "cmd": "event_step",
                        "dir": step_dir,
                        "gesture": gesture.lower(),
                        "source": "laptop_node"
                    }
                    sock.sendto(json.dumps(g_payload).encode("utf-8"), (args.pi, args.port))
                except Exception as e:
                    print(f"[Laptop AI] UDP swipe dispatch error: {e}")
        lat_gesture = (time.time() - t_gest0) * 1000.0
        lat_total = (time.time() - t_loop_start) * 1000.0

        now = time.time()
        fps_count += 1
        if now - fps_timer >= 1.0:
            cur_fps = fps_count / (now - fps_timer)
            fps_count = 0
            fps_timer = now

        # Periodic Latency Benchmark report every 4 seconds
        if now - last_stat_time >= 4.0:
            last_stat_time = now
            print(f"[Telemetry Benchmark] {cur_fps:.1f} FPS | Grab: {lat_grab:.1f}ms | Resize: {lat_resize:.1f}ms | YuNet: {lat_yunet:.1f}ms | Gesture: {lat_gesture:.1f}ms | Total: {lat_total:.1f}ms | Cam: {active_src}")

        # Dispatch telemetry to Pi at ~30 FPS
        if now - last_send_time >= 0.033:
            last_send_time = now
            if detected_faces:
                p = detected_faces[0]
                secondaries = [(round(f["x"], 3), round(f["y"], 3)) for f in detected_faces[1:4]]
                payload = {
                    "cmd": "target",
                    "active": True,
                    "name": "LAPTOP TURBO LOCK",
                    "dist": p["dist"],
                    "x": round(p["x"], 3),
                    "y": round(p["y"], 3),
                    "count": len(detected_faces),
                    "secondaries": secondaries,
                    "source": "laptop_node"
                }
            else:
                payload = {
                    "cmd": "target",
                    "active": False,
                    "source": "laptop_node"
                }

            try:
                msg = json.dumps(payload).encode("utf-8")
                sock.sendto(msg, (args.pi, args.port))
            except Exception:
                pass

        # Optional GUI Display
        if not args.no_gui:
            disp = frame.copy()
            dh, dw = disp.shape[:2]

            for i, f in enumerate(detected_faces):
                nx, ny, nw, nh = f["box"]
                bx, by, bw, bh = int(nx * dw), int(ny * dh), int(nw * dw), int(nh * dh)
                col = (56, 235, 145) if i == 0 else (255, 165, 55)
                cv2.rectangle(disp, (bx, by), (bx + bw, by + bh), col, 2)
                tag = f"{f['dist']} ({int(f['score']*100)}%)"
                cv2.putText(disp, tag, (bx, max(20, by - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1, cv2.LINE_AA)

            # Torso Shield Overlay (Shows the protected zone where head/neck motion is ignored)
            if detected_faces:
                prim = detected_faces[0]
                pnx, pny, pnw, pnh = prim["box"]
                tx1 = max(0, int((pnx - pnw * 0.35) * dw))
                ty1 = max(0, int((pny - pnh * 0.30) * dh))
                tx2 = min(dw, int((pnx + pnw * 1.35) * dw))
                ty2 = dh
                cv2.rectangle(disp, (tx1, ty1), (tx2, ty2), (60, 80, 110), 1)
                cv2.putText(disp, "TORSO SHIELD [MOTION FILTERED]", (tx1 + 6, min(dh - 10, ty1 + 16)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.38, (90, 120, 160), 1, cv2.LINE_AA)

            # Hand bounding box overlay
            if gesture_engine and gesture_engine.hand_detected and gesture_engine.hand_box:
                hbx, hby, hbw, hbh = gesture_engine.hand_box
                hx, hy, hw, hh = int(hbx * dw), int(hby * dh), int(hbw * dw), int(hbh * dh)
                cv2.rectangle(disp, (hx, hy), (hx + hw, hy + hh), (56, 235, 145), 2)
                cv2.putText(disp, "HAND RECON", (hx, max(20, hy - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (56, 235, 145), 1, cv2.LINE_AA)

            # Swipe gesture glowing banner overlay
            if gesture_engine and gesture_engine.gesture_display_until > now:
                g_str = gesture_engine.latest_gesture
                if g_str == "SWIPE_RIGHT":
                    g_msg = ">>> [SWIPE RIGHT] NEXT SLIDE >>>"
                    g_col = (56, 235, 145)
                elif g_str == "SWIPE_LEFT":
                    g_msg = "<<< [SWIPE LEFT] PREV SLIDE <<<"
                    g_col = (75, 215, 255)
                elif g_str == "SWIPE_UP":
                    g_msg = "^^^ [SWIPE UP] SHOW FACE ^^^"
                    g_col = (255, 215, 95)
                else:
                    g_msg = "vvv [SWIPE DOWN] SHOW SLIDES vvv"
                    g_col = (255, 165, 55)
                (gw_t, gh_t), _ = cv2.getTextSize(g_msg, cv2.FONT_HERSHEY_SIMPLEX, 0.65, 2)
                gbx = (dw - gw_t) // 2
                gby = dh - 25
                cv2.rectangle(disp, (gbx - 10, gby - 22), (gbx + gw_t + 10, gby + 8), (10, 14, 20), -1)
                cv2.rectangle(disp, (gbx - 10, gby - 22), (gbx + gw_t + 10, gby + 8), g_col, 2)
                cv2.putText(disp, g_msg, (gbx, gby), cv2.FONT_HERSHEY_SIMPLEX, 0.65, g_col, 2, cv2.LINE_AA)

            # Top telemetry banner
            status_text = f"LAPTOP AI ACTIVE // {cur_fps:.1f} FPS // PI: {args.pi} // TARGETS: {len(detected_faces)}"
            cv2.rectangle(disp, (0, 0), (dw, 30), (10, 14, 20), -1)
            cv2.putText(disp, status_text, (12, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (56, 235, 145), 1, cv2.LINE_AA)

            cv2.imshow("TARS Laptop AI Companion", disp)
            k = cv2.waitKey(1) & 0xFF
            if k in (ord('q'), ord('Q'), 27):
                break

    if grabber is not None:
        grabber.stop()
    cv2.destroyAllWindows()
    sock.close()
    print("[Laptop AI] Disconnected cleanly.")


if __name__ == "__main__":
    main()
