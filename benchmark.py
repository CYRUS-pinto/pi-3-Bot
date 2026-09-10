"""Comprehensive performance benchmark with active animations, CPU & Memory telemetry.

Run on the Pi:
    SDL_VIDEODRIVER=kmsdrm python3 ~/TARS/benchmark.py
"""

import os
import time
import math
import pygame
from renderer import Renderer

# ── Zero-dependency System Telemetry ──────────────────────────────────────────

def get_mem_rss_mb() -> float:
    """Read Linux process RSS memory in MB."""
    try:
        with open("/proc/self/status", "r") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except Exception:
        pass
    return 0.0


class CPUTracker:
    def __init__(self):
        self.last_time = time.perf_counter()
        t = os.times()
        self.last_cpu = t.user + t.system

    def sample(self) -> float:
        now = time.perf_counter()
        t = os.times()
        cur_cpu = t.user + t.system
        dt = max(0.001, now - self.last_time)
        dc = cur_cpu - self.last_cpu
        self.last_time = now
        self.last_cpu = cur_cpu
        return max(0.0, min(100.0, (dc / dt) * 100.0))


# ── Benchmark Initialization ─────────────────────────────────────────────────

pygame.init()
info = pygame.display.Info()
print("\n" + "=" * 62)
print("   TARS DISPLAY BENCHMARK: ACTIVE ANIMATIONS & TELEMETRY")
print("=" * 62)
print(f"Driver:       {pygame.display.get_driver()}")
print(f"Native Mode:  {info.current_w}x{info.current_h}")

screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN | pygame.DOUBLEBUF)
w, h = screen.get_size()
print(f"Active Mode:  {w}x{h} ({screen.get_bitsize()} bpp)")
print(f"Initial Mem:  {get_mem_rss_mb():.1f} MB RSS")
print("=" * 62)

renderer = Renderer(screen)
font_hud = pygame.font.Font(None, max(14, int(h * 0.022)))
font_lg  = pygame.font.Font(None, max(22, int(h * 0.040)))
font_sm  = pygame.font.Font(None, max(12, int(h * 0.018)))

cpu_tracker = CPUTracker()

# Pre-fill double buffers
renderer.draw(0.0, 0.016, draw_face=True)
pygame.display.flip()
renderer.draw(0.0, 0.016, draw_face=True)
pygame.display.flip()

frames = 0
duration = 8.0
start = time.perf_counter()

# Timers for component tracking
t_stars_sum = 0.0
t_face_sum  = 0.0
t_bar_sum   = 0.0
t_flip_sum  = 0.0

frame_times: list[float] = []
cpu_samples: list[float] = []
mem_samples: list[float] = []
max_history = min(120, max(40, int(w * 0.35)))

# Graph HUD dimensions
gw = max(260, int(w * 0.30))
gh = max(80, int(h * 0.13))
gx = w - gw - max(20, int(w * 0.025))
gy = h - gh - max(20, int(h * 0.035))

# Animation controllers
from animation import BlinkController
blink = BlinkController(renderer.face)
emotions = ["HAPPY", "PLAYFUL", "EXCITED", "CURIOUS", "CONFIDENT", "WARM", "NEUTRAL"]
last_emotion_t = 0.0
cur_emotion_idx = 0

while time.perf_counter() - start < duration:
    t_frame_start = time.perf_counter()
    now_rel = time.perf_counter() - start
    dt = 0.016

    # 1. Active Emotion Cycling (every 1.8 seconds)
    if now_rel - last_emotion_t >= 1.8:
        last_emotion_t = now_rel
        cur_emotion_idx = (cur_emotion_idx + 1) % len(emotions)
        renderer.face.set_emotion(emotions[cur_emotion_idx])

    # 2. Synchronized Blink and Face Update
    blink.update(dt)
    renderer.face.update(dt)

    # 3. Timed Rendering Components
    t1 = time.perf_counter()
    renderer._draw_stars(screen, now_rel)
    t2 = time.perf_counter()

    renderer.face.draw(screen)
    t3 = time.perf_counter()

    renderer._draw_topbar(screen)
    t4 = time.perf_counter()

    # 4. Draw Live Telemetry Graph HUD
    pygame.draw.rect(screen, (8, 10, 14), (gx, gy, gw, gh), border_radius=4)
    pygame.draw.rect(screen, (35, 40, 50), (gx, gy, gw, gh), width=1, border_radius=4)

    scale_y = gh / 80.0
    y_30fps = gy + gh - int(33.3 * scale_y)
    y_60fps = gy + gh - int(16.6 * scale_y)
    pygame.draw.line(screen, (40, 45, 55), (gx, y_30fps), (gx + gw, y_30fps), 1)
    pygame.draw.line(screen, (25, 55, 40), (gx, y_60fps), (gx + gw, y_60fps), 1)

    # Plot oscilloscope frame time trace
    if len(frame_times) > 1:
        pts = []
        step_x = gw / max(1, max_history - 1)
        for i, ft in enumerate(frame_times):
            px = int(gx + i * step_x)
            py = max(gy + 2, min(gy + gh - 2, int(gy + gh - ft * scale_y)))
            pts.append((px, py))
        if len(pts) >= 2:
            pygame.draw.lines(screen, (85, 199, 255), False, pts, 2)

    # Live Readouts
    last_ms = frame_times[-1] if frame_times else 16.6
    cur_fps = 1000.0 / max(1.0, last_ms)
    cur_mem = get_mem_rss_mb()
    cur_cpu = cpu_tracker.sample() if frames % 10 == 0 else (cpu_samples[-1] if cpu_samples else 0.0)

    fps_surf  = font_lg.render(f"{cur_fps:.1f} FPS", True, (255, 157, 46))
    ms_surf   = font_hud.render(f"{last_ms:.1f}ms", True, (160, 160, 165))
    morph_pct = int(renderer.face.morph_progress * 100)
    morph_lbl = "STABLE" if not renderer.face.is_morphing else f"SYNC {morph_pct}%"
    sys_str   = f"CPU:{cur_cpu:.0f}% RAM:{cur_mem:.1f}M [{emotions[cur_emotion_idx]}] {morph_lbl}"
    sys_surf  = font_sm.render(sys_str, True, (130, 135, 145))

    screen.blit(fps_surf, (gx + 10, gy + 8))
    screen.blit(ms_surf,  (gx + 10 + fps_surf.get_width() + 10, gy + 14))
    screen.blit(sys_surf, (gx + 10, gy + gh - 18))

    # 5. Display Flip (VSync)
    t_pre_flip = time.perf_counter()
    pygame.display.flip()
    t5 = time.perf_counter()

    t_stars_sum += (t2 - t1)
    t_face_sum  += (t3 - t2)
    t_bar_sum   += (t4 - t3)
    t_flip_sum  += (t5 - t_pre_flip)

    frame_ms = (t5 - t_frame_start) * 1000.0
    frame_times.append(frame_ms)
    if len(frame_times) > max_history:
        frame_times.pop(0)

    if frames % 10 == 0:
        cpu_samples.append(cur_cpu)
        mem_samples.append(cur_mem)

    frames += 1
    pygame.event.pump()

elapsed = time.perf_counter() - start
avg_fps = frames / elapsed
avg_total_ms = (elapsed / frames) * 1000.0

ms_star = (t_stars_sum / frames) * 1000.0
ms_face = (t_face_sum / frames) * 1000.0
ms_bar  = (t_bar_sum / frames) * 1000.0
ms_flip = (t_flip_sum / frames) * 1000.0

avg_cpu = sum(cpu_samples) / max(1, len(cpu_samples))
peak_mem = max(mem_samples) if mem_samples else get_mem_rss_mb()
end_mem = get_mem_rss_mb()

# ── Terminal ASCII Performance Report ─────────────────────────────────────────
print(f"\nRESULTS: {frames} frames in {elapsed:.2f}s  |  Average: {avg_fps:.2f} FPS ({avg_total_ms:.2f} ms/frame)\n")
print("┌──────────────────────────┬───────────┬────────┬────────────────────────────────────────┐")
print("│ Component                │ Time (ms) │ Pct    │ Breakdown Graph                        │")
print("├──────────────────────────┼───────────┼────────┼────────────────────────────────────────┤")

def print_row(name, ms, total_ms):
    pct = (ms / total_ms) * 100.0 if total_ms > 0 else 0
    bar_len = min(38, int((ms / total_ms) * 38)) if total_ms > 0 else 0
    bar = "█" * bar_len + "░" * (38 - bar_len)
    print(f"│ {name:<24} │ {ms:>7.2f}ms │ {pct:>5.1f}% │ [{bar}] │")

print_row("Stars Update/Draw", ms_star, avg_total_ms)
print_row("Face Active Draw", ms_face, avg_total_ms)
print_row("Top Status Bar", ms_bar, avg_total_ms)
print_row("Display Flip (VSync)", ms_flip, avg_total_ms)
print("└──────────────────────────┴───────────┴────────┴────────────────────────────────────────┘")

# Latency & System Distribution
sorted_times = sorted(frame_times)
p50 = sorted_times[len(sorted_times) // 2]
p95 = sorted_times[int(len(sorted_times) * 0.95)]
p99 = sorted_times[int(len(sorted_times) * 0.99)]
min_ms = sorted_times[0]
max_ms = sorted_times[-1]

print("\n=== System Resource Telemetry ===")
print(f"  CPU Utilization:   {avg_cpu:.1f}% average")
print(f"  Memory Footprint:  {end_mem:.1f} MB RSS (Peak: {peak_mem:.1f} MB)")
print(f"  Frame Latency:     Min: {min_ms:.1f}ms | 50th: {p50:.1f}ms | 95th: {p95:.1f}ms | Max: {max_ms:.1f}ms")
print("=================================\n")

pygame.quit()
