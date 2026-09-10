import os
import sys
import time
import cv2
import urllib.request
import json

print("=== TARS CAMERA OPTIMIZATION BENCHMARK ===", flush=True)

# 1. Check Temperature
try:
    with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
        temp_c = int(f.read().strip()) / 1000.0
        print(f"Current Pi CPU Temp: {temp_c:.1f}°C", flush=True)
except Exception as e:
    print(f"Could not read temp: {e}", flush=True)

# 2. Check Throttled status
try:
    import subprocess
    th = subprocess.check_output(["vcgencmd", "get_throttled"]).decode().strip()
    print(f"Throttled state: {th}", flush=True)
except Exception:
    pass

# 3. Check IP Webcam settings API
url_base = "http://10.57.90.53:8080"
try:
    req = urllib.request.urlopen(f"{url_base}/status.json", timeout=2)
    data = json.loads(req.read().decode())
    curvals = data.get("curvals", {})
    cur_size = curvals.get("video_size", "unknown")
    cur_qual = curvals.get("quality", "unknown")
    print(f"IP Webcam current video_size: {cur_size}, quality: {cur_qual}", flush=True)
except Exception as e:
    print(f"IP Webcam status error: {e}", flush=True)

# Try switching resolution to 960x720 or 1280x720 for ultra-fast decode
for target_size in ["960x720", "1280x720"]:
    try:
        req = urllib.request.urlopen(f"{url_base}/settings/video_size?set={target_size}", timeout=2)
        print(f"Set video_size to {target_size} -> Response code: {req.getcode()}", flush=True)
        time.sleep(0.5)
        break
    except Exception as e:
        print(f"Failed setting {target_size}: {e}", flush=True)

# Test OpenCV capture latency
print("\nTesting OpenCV VideoCapture stream latency...", flush=True)
cap = cv2.VideoCapture(f"{url_base}/video")
if cap.isOpened():
    # Set 1-frame buffer
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    
    # Warmup
    for _ in range(5):
        cap.grab()
    
    ret, frame = cap.read()
    if ret and frame is not None:
        print(f"Captured frame shape: {frame.shape} ({frame.shape[1]}x{frame.shape[0]})", flush=True)
        
        # Benchmark grab vs retrieve
        t0 = time.perf_counter()
        for _ in range(30):
            cap.grab()
        t1 = time.perf_counter()
        grab_fps = 30.0 / (t1 - t0)
        print(f"30x cap.grab() only: {t1 - t0:.3f}s -> {grab_fps:.1f} FPS ({(t1 - t0)/30*1000.0:.2f}ms/grab)", flush=True)
        
        t0 = time.perf_counter()
        for _ in range(20):
            cap.grab()
            _, _ = cap.retrieve()
        t1 = time.perf_counter()
        decode_fps = 20.0 / (t1 - t0)
        print(f"20x cap.grab() + retrieve(): {t1 - t0:.3f}s -> {decode_fps:.1f} FPS ({(t1 - t0)/20*1000.0:.2f}ms/frame)", flush=True)
    cap.release()
else:
    print("Could not open VideoCapture", flush=True)
