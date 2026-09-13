"""Simulation regression tests for the optical gesture engine (no Pi/camera needed).

Drives OpticalGestureEngine.process() with synthetic MJPEG-like frames (white blob
hand + sensor noise + JPEG block shimmer) on real time. Proves flick/hold/teleport/
two-hand/bystander/rebound behavior deterministically. Run: pytest tests/test_gesture_sim.py
"""
import sys
import time

import numpy as np
import cv2
import pytest

sys.path.insert(0, "..")
import config
from vision import OpticalGestureEngine

config.GESTURE_DEBUG_LOGS = False
config.MIRROR_GESTURE_X = False  # intuitive mapping in harness: frame-right == SWIPE_RIGHT

RNG = np.random.default_rng(99)
DT = 0.05


def frame(x, y, r=45, noise=7.0, shimmer=8.0, jx=0.0, jy=0.0, spots=None):
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    pts = spots if spots is not None else [(x, y)]
    for (sx, sy) in pts:
        cv2.circle(img, (int(sx * 640 + jx), int(sy * 480 + jy)), r, (255, 255, 255), -1)
    if shimmer:
        b = RNG.integers(-shimmer, shimmer + 1, (60, 80, 1)).astype(np.int16)
        b = np.repeat(np.repeat(b, 8, axis=0), 8, axis=1)
        img = np.clip(img.astype(np.int16) + b, 0, 255).astype(np.uint8)
    img = np.clip(img.astype(np.int16) + RNG.normal(0, noise, img.shape), 0, 255).astype(np.uint8)
    return img


def drive(engine, xs, y=0.6, faces=None, dt=DT):
    fires = []
    for x in xs:
        g = engine.process(frame(x, y), face_boxes=faces)
        if g:
            fires.append(g)
        time.sleep(dt)
    return fires


def test_flick_right_fires_once():
    e = OpticalGestureEngine()
    fires = drive(e, [0.30 + 0.04 * i for i in range(9)])
    assert fires == ["SWIPE_RIGHT"], f"fast flick must fire exactly once right, got {fires}"


def test_slow_drift_never_sweeps():
    e = OpticalGestureEngine()
    fires = drive(e, [0.30 + 0.0145 * i for i in range(20)])  # 0.29 widths/s drift
    assert [f for f in fires if f in ("SWIPE_RIGHT", "SWIPE_LEFT")] == [] or True
    # drift ends left-of-center: must not SWEEP (hold may fire only in zones; x stays < 0.6)
    assert fires == [], f"slow drift must not fire, got {fires}"


def test_hold_right_fires_once_no_repeat():
    e = OpticalGestureEngine()
    # slow walk-in (0.3/s: below every flick gate), then tremor-hold at 0.70
    drive(e, [0.55 + 0.015 * i for i in range(8)])
    fires = []
    for _ in range(52):
        jx, jy = float(RNG.normal(0, 1.5)), float(RNG.normal(0, 1.5))
        g = e.process(frame(0.70, 0.6, jx=jx, jy=jy))
        if g:
            fires.append(g)
        time.sleep(DT)
    assert fires == ["SWIPE_RIGHT"], f"hold must fire exactly once, got {fires}"
    assert e._hold_fired_zone == "RIGHT"


def test_teleport_cut_no_fire():
    e = OpticalGestureEngine()
    drive(e, [0.30 + 0.02 * i for i in range(5)])
    fires = drive(e, [0.80] * 8)  # instant jump across the frame
    assert fires == [], f"teleport must never fire, got {fires}"


def test_two_hands_fire_overview_once():
    e = OpticalGestureEngine()
    fires = []
    for i in range(34):  # both hands drift slowly (trackable) toward a steady 0.42-apart hold
        jx = float(RNG.normal(0, 1.5))
        lx = 0.28 + 0.003 * i
        rx = 0.74 - 0.003 * i
        g = e.process(frame(0, 0, spots=[(lx, 0.60), (rx, 0.60)], jx=jx))
        if g:
            fires.append(g)
        time.sleep(DT)
    assert fires == ["SWIPE_DOWN"], f"two-hand pose must fire overview once, got {fires}"


def test_bystander_hand_ignored():
    e = OpticalGestureEngine()
    face = [(0.10, 0.20, 0.15, 0.20)]  # presenter far left
    fires = drive(e, [0.78 + 0.005 * i for i in range(24)], y=0.55, faces=face)
    assert fires == [], f"bystander hand must be ignored, got {fires}"


def test_return_stroke_suppressed():
    e = OpticalGestureEngine()
    fires = drive(e, [0.30 + 0.05 * i for i in range(7)])  # fast flick right
    assert fires == ["SWIPE_RIGHT"]
    fires2 = drive(e, [0.65 - 0.06 * i for i in range(6)])  # immediate yank back left
    assert fires2 == [], f"return stroke must be suppressed, got {fires2}"


def test_double_flick_two_fires():
    e = OpticalGestureEngine()
    fires = drive(e, [0.30 + 0.05 * i for i in range(7)])
    time.sleep(0.6)
    # hand must travel back (unseen reset) then flick again
    drive(e, [0.65 - 0.05 * i for i in range(6)])
    time.sleep(0.6)
    fires += drive(e, [0.32 + 0.05 * i for i in range(7)])
    assert fires == ["SWIPE_RIGHT", "SWIPE_RIGHT"], f"two separated flicks = two fires, got {fires}"


def test_pop_in_still_fires_delayed():
    e = OpticalGestureEngine()
    fires = drive(e, [0.55] * 3 + [0.55 + 0.05 * i for i in range(7)])
    assert fires == ["SWIPE_RIGHT"], f"pop-in flick must still fire (delayed ok), got {fires}"
