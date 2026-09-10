import cv2
import time
import os

print("--- DIAGNOSTIC SCRIPT ---")
os.system("vcgencmd measure_temp")
os.system("vcgencmd get_throttled")

for url in ["http://10.57.90.53:8080/video", "http://10.70.4.51:8080/video"]:
    print("Testing:", url)
    cap = cv2.VideoCapture(url)
    if not cap.isOpened():
        print("Could not open:", url)
        continue
    ret, frame = cap.read()
    if not ret or frame is None:
        print("Could not read frame from:", url)
        cap.release()
        continue
    print(f"Shape: {frame.shape}, Dtype: {frame.dtype}")
    t0 = time.perf_counter()
    count = 0
    for _ in range(20):
        r, f = cap.read()
        if r:
            count += 1
    t1 = time.perf_counter()
    dt = t1 - t0
    print(f"Read {count} frames in {dt:.3f}s -> {count/dt:.1f} FPS (Average {dt/count*1000.0:.1f}ms per frame decode)")
    cap.release()
    break
