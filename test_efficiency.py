"""Efficiency budgets: gesture detection intact + caches bounded. Run: python test_efficiency.py"""
import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import numpy as np

import config
from vision import OpticalGestureEngine

# Fast deterministic tuning for the synthetic sweep (restored after)
_saved = (config.GESTURE_COOLDOWN_SEC, config.GESTURE_SWIPE_DISTANCE,
          config.GESTURE_SWIPE_SENSITIVITY, config.GESTURE_WALK_LOCKOUT_SPEED)
config.GESTURE_COOLDOWN_SEC = 0.0
config.GESTURE_SWIPE_DISTANCE = 0.18
config.GESTURE_SWIPE_SENSITIVITY = 1.0
config.GESTURE_WALK_LOCKOUT_SPEED = 99.0  # walk gate off: synthetic frames carry no face


def _black():
    return np.zeros((360, 480, 3), dtype=np.uint8)


def test_still_frames_cost_nothing_and_fire_nothing():
    eng = OpticalGestureEngine()
    for _ in range(6):
        assert eng.process(_black(), face_boxes=[]) is None
    assert eng.hand_detected is False
    print("STILL_OK")


def test_synthetic_sweep_still_fires():
    # White paddle sweeps L->R across the zone rails at chest height; full path must run.
    eng = OpticalGestureEngine()
    eng.process(_black(), face_boxes=[])
    fired = None
    for i in range(14):
        f = _black()
        x = 40 + i * 30  # 40..430px in 480px frame
        f[150:210, x:x + 60] = 255
        r = eng.process(f, face_boxes=[])
        if isinstance(r, str) and "SWIPE" in r:
            fired = r
            break
    assert fired is not None, "synthetic sweep fired nothing — early-exit broke detection?"
    print("SWEEP_OK", fired)


def test_kid_flick_fires_without_full_sweep():
    # Flick: 3 fast frames, ~0.15 total displacement — BELOW the 0.18 full-sweep
    # minimum. Must still fire via the flick shortcut (kids flick, they don't sweep).
    eng = OpticalGestureEngine()
    eng.process(_black(), face_boxes=[])
    fired = None
    for i in range(5):
        f = _black()
        x = 200 + i * 25  # short fast run, chest height
        f[150:210, x:x + 60] = 255
        r = eng.process(f, face_boxes=[])
        if isinstance(r, str) and "SWIPE" in r:
            fired = r
            break
    assert fired is not None, "kid flick fired nothing — non-tech users can't swipe?"
    print("FLICK_OK", fired)


def test_caches_bounded():
    import pygame
    pygame.init()
    pygame.display.set_mode((64, 64))
    from hud import OpticalHUD
    fonts = {"xs": pygame.font.Font(None, 14), "sm": pygame.font.Font(None, 18),
             "md": pygame.font.Font(None, 22)}
    hud = OpticalHUD(480, 640, fonts)
    for i in range(40):
        hud._tag(fonts["xs"], f"TAG-{i}", (255, 255, 255))
    assert len(hud._tag_cache) <= 12, len(hud._tag_cache)

    import main as M
    for i in range(20):
        # prime caption cache through distinct texts (needs a surface)
        surf = pygame.Surface((480, 640))
        M.draw_speech_subtitles(surf, f"subtitle text number {i}", fonts["sm"], (255, 200, 100), 480, 640)
    assert len(M.draw_speech_subtitles._cap_cache) <= 4, len(M.draw_speech_subtitles._cap_cache)
    print("CACHE_OK")


test_still_frames_cost_nothing_and_fire_nothing()
test_synthetic_sweep_still_fires()
test_kid_flick_fires_without_full_sweep()
test_caches_bounded()

config.GESTURE_COOLDOWN_SEC, config.GESTURE_SWIPE_DISTANCE, \
    config.GESTURE_SWIPE_SENSITIVITY, config.GESTURE_WALK_LOCKOUT_SPEED = _saved
print("EFFICIENCY_OK")
