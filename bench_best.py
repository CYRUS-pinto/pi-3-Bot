"""TARS definitive benchmark: render stages, AI, camera, thermal, offload verdict.
Run ON THE PI with TARS stopped:
    pkill -f "python3.*main.py"; sleep 1
    SDL_VIDEODRIVER=kmsdrm python3 ~/TARS/bench_best.py [--laptop <laptop-ip>]
Needs the real display (KMS flip timing). Writes bench_best.json + prints verdicts.
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import time

os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

RESULTS: dict = {"budgets": {
    "render_30fps_ms": 33.3, "rotate_ms": 8.0, "yunet_ms": 30.0,
    "grab_ms": 60.0, "temp_c": 65.0, "offload_win_ratio": 0.6,
}}


def pct(vals, p):
    s = sorted(vals)
    return s[min(len(s) - 1, int(len(s) * p / 100))]


def thermal():
    out = {"temp_c": -1.0, "throttled": "?"}
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            out["temp_c"] = int(f.read().strip()) / 1000.0
    except Exception:
        pass
    try:
        out["throttled"] = subprocess.check_output(
            ["vcgencmd", "get_throttled"], timeout=5).decode().strip()
    except Exception:
        pass
    return out


def phase_yunet():
    import glob
    cands = [os.path.join(HERE, "models", "face_detection_yunet_2023mar.onnx"),
             os.path.join(HERE, "face_detection_yunet_2023mar.onnx")]
    mp = next((c for c in cands if os.path.exists(c)), None)
    if mp is None:
        return {"status": "no-model"}
    yn = cv2.FaceDetectorYN.create(mp, "", (192, 144))
    frame = (np.random.rand(144, 192, 3) * 255).astype(np.uint8)
    for _ in range(5):
        yn.detect(frame)
    ts = []
    for _ in range(100):
        t0 = time.perf_counter()
        yn.detect(frame)
        ts.append((time.perf_counter() - t0) * 1000.0)
    return {"status": "ok", "avg_ms": round(sum(ts) / len(ts), 2),
            "p95_ms": round(pct(ts, 95), 2), "model": os.path.basename(mp)}


def phase_grab():
    try:
        from vision import discover_camera_sources, probe_fast
    except Exception as e:
        return {"status": f"no-vision-import: {e}"}
    srcs = [s for s in discover_camera_sources() if probe_fast(s)]
    if not srcs:
        return {"status": "no-camera"}
    cap = cv2.VideoCapture(srcs[0])
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass
    for _ in range(5):
        cap.grab()
    ret, frame = cap.read()
    shape = list(frame.shape) if ret and frame is not None else None
    ts = []
    for _ in range(20):
        t0 = time.perf_counter()
        cap.grab()
        cap.retrieve()
        ts.append((time.perf_counter() - t0) * 1000.0)
    cap.release()
    return {"status": "ok", "src": str(srcs[0]), "shape": shape,
            "avg_ms": round(sum(ts) / len(ts), 1), "p95_ms": round(pct(ts, 95), 1),
            "fps": round(1000.0 / (sum(ts) / len(ts)), 1)}


def phase_gesture():
    from vision import OpticalGestureEngine
    eng = OpticalGestureEngine()
    black = np.zeros((360, 480, 3), dtype=np.uint8)
    eng.process(black, face_boxes=[])
    ts = []
    for _ in range(30):
        t0 = time.perf_counter()
        eng.process(black, face_boxes=[])
        ts.append((time.perf_counter() - t0) * 1000.0)
    still = sum(ts) / len(ts)
    # one motion frame cost (white paddle => full contour path)
    f = black.copy()
    f[150:210, 200:260] = 255
    t0 = time.perf_counter()
    eng.process(f, face_boxes=[])
    motion = (time.perf_counter() - t0) * 1000.0
    return {"status": "ok", "still_avg_ms": round(still, 3),
            "motion_one_ms": round(motion, 2)}


def phase_render():
    import pygame
    import config
    from renderer import Renderer
    pygame.init()
    pygame.display.set_caption("TARS-BENCH")
    flags = pygame.FULLSCREEN | pygame.DOUBLEBUF if config.FULLSCREEN else pygame.DOUBLEBUF
    size = (config.WIDTH, config.HEIGHT) if (config.WIDTH and config.HEIGHT) else (0, 0)
    screen = pygame.display.set_mode(size, flags)
    w, h = screen.get_size()
    canvas = pygame.Surface((max(1, int(h * 0.5)), max(1, int(w * 0.5)))).convert()
    r = Renderer(canvas)
    r.face.set_emotion("HAPPY")
    # warmup (morph + caches settle)
    for i in range(30):
        r.draw(i * 0.033, 0.033, draw_face=True)
    # NOTE: this block must mirror main.py's present path exactly — it IS the shipped pipeline.
    # (An earlier revision measured smoothscale here while main.py had already moved to scale: bench lied, main didn't.)
    import numpy as _np
    from pygame import surfarray as _sa
    t_face, t_rot, t_scale, t_flip, t_all = [], [], [], [], []
    N = 120
    for i in range(N):
        t0 = time.perf_counter()
        r.draw(i * 0.033, 0.033, draw_face=True)
        t1 = time.perf_counter()
        _px = _sa.pixels3d(canvas)
        _hold = _np.ascontiguousarray(_np.transpose(_px, (1, 0, 2))[:, ::-1, :])
        del _px
        rotated = pygame.image.frombuffer(_hold, (_hold.shape[0], _hold.shape[1]), "RGB")
        t2 = time.perf_counter()
        pygame.transform.scale(rotated, screen.get_size(), screen)
        t3 = time.perf_counter()
        pygame.display.flip()
        t4 = time.perf_counter()
        t_face.append((t1 - t0) * 1000.0)
        t_rot.append((t2 - t1) * 1000.0)
        t_scale.append((t3 - t2) * 1000.0)
        t_flip.append((t4 - t3) * 1000.0)
        t_all.append((t4 - t0) * 1000.0)
    pygame.quit()
    return {"status": "ok", "screen": [w, h],
            "face_avg_ms": round(sum(t_face) / N, 2),
            "rotate_avg_ms": round(sum(t_rot) / N, 2),
            "scale_avg_ms": round(sum(t_scale) / N, 2),
            "flip_avg_ms": round(sum(t_flip) / N, 2),
            "frame_avg_ms": round(sum(t_all) / N, 2),
            "frame_p95_ms": round(pct(t_all, 95), 2),
            "achieved_fps": round(1000.0 / (sum(t_all) / N), 1)}


def phase_offload(laptop):
    """Worth borrowing the laptop? Needs only ping: offload_total ≈ rtt + 8ms laptop infer."""
    if not laptop:
        return {"status": "skipped (no --laptop)"}
    try:
        out = subprocess.check_output(
            ["ping", "-c", "20", "-i", "0.2", laptop],
            timeout=30).decode()
        import re
        m = re.search(r"rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/", out)
        if not m:
            return {"status": "ping-unparseable"}
        rtts = {"min": float(m.group(1)), "avg": float(m.group(2)), "max": float(m.group(3))}
    except Exception as e:
        return {"status": f"ping-failed: {e}"}
    y = RESULTS.get("yunet", {})
    local = y.get("avg_ms", 40.0)
    remote_total = rtts["avg"] + 8.0 + 2.0  # rtt + laptop infer est + UDP send
    verdict = "OFFLOAD_WINS" if remote_total < local * 0.6 else "LOCAL_WINS"
    return {"status": "ok", "rtt_ms": rtts, "local_yunet_ms": local,
            "remote_est_ms": round(remote_total, 1), "verdict": verdict}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--laptop", default="")
    args = ap.parse_args()

    RESULTS["thermal_before"] = thermal()
    print("=== YuNet ===", flush=True)
    RESULTS["yunet"] = phase_yunet()
    print(RESULTS["yunet"], flush=True)
    print("=== Grab ===", flush=True)
    RESULTS["grab"] = phase_grab()
    print(RESULTS["grab"], flush=True)
    print("=== Gesture ===", flush=True)
    RESULTS["gesture"] = phase_gesture()
    print(RESULTS["gesture"], flush=True)
    print("=== Render (KMS, 120 frames) ===", flush=True)
    RESULTS["render"] = phase_render()
    print(RESULTS["render"], flush=True)
    RESULTS["thermal_after"] = thermal()
    print("=== Offload probe ===", flush=True)
    RESULTS["offload"] = phase_offload(args.laptop)
    print(RESULTS["offload"], flush=True)

    # verdicts vs budgets
    B = RESULTS["budgets"]
    V = {}
    r = RESULTS.get("render", {})
    if r.get("status") == "ok":
        V["30fps"] = "PASS" if r["frame_p95_ms"] < B["render_30fps_ms"] else "FAIL"
        V["60fps"] = "PASS" if r["frame_p95_ms"] < 16.6 else "FAIL (expected on Pi3)"
    y = RESULTS.get("yunet", {})
    if y.get("status") == "ok":
        V["yunet"] = "PASS" if y["avg_ms"] < B["yunet_ms"] else "SLOW"
    g = RESULTS.get("grab", {})
    if g.get("status") == "ok":
        V["camera"] = "PASS" if g["avg_ms"] < B["grab_ms"] else "BOTTLENECK"
    t = RESULTS.get("thermal_after", {})
    V["thermal"] = "HOT" if t.get("temp_c", 0) > B["temp_c"] else "OK"
    RESULTS["verdicts"] = V
    print("=== VERDICTS ===", flush=True)
    print(json.dumps(V, indent=1), flush=True)

    with open(os.path.join(HERE, "bench_best.json"), "w") as f:
        json.dump(RESULTS, f, indent=1)
    print("wrote bench_best.json", flush=True)


if __name__ == "__main__":
    main()
