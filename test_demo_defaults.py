"""Demo gate: boxed vertical bot must boot portrait, cool, and forgiving. Run: python test_demo_defaults.py
 ponytail: asserts module CONSTANTS (user-toggled live settings like auto-cycle/rotation live in
 calibration.json and are allowed to drift — the gate guards what code ships, not what users press).
"""
import json

import config

config.load_calibration()  # mirrors Pi boot: live file overrides where keys exist

# Shipped constants — calibration.json cannot change these; if one flips, the build regressed.
assert config.SCREEN_ORIENTATION == "AUTO", config.SCREEN_ORIENTATION  # horizontal rig now, vertical later; layouts branch both ways
assert config.TARGET_FPS == 30, config.TARGET_FPS
assert config.GESTURE_DEBUG_LOGS is False, "debug spam must stay off for demo"
assert abs(config.GESTURE_REBOUND_LOCKOUT_SEC - 0.55) < 1e-9, config.GESTURE_REBOUND_LOCKOUT_SEC
assert config.LOW_POWER_ENABLED is True, "boxed bot with no ventilation must sleep"
assert config.IDLE_RENDER_FPS <= 10, config.IDLE_RENDER_FPS  # empty-room standby must sip, not clockwise heat

# Walk lockout is UI-tunable: guardrail range, not a pin (Antigravity validated 0.12; tars.log condemned 0.07).
assert 0.10 <= config.GESTURE_WALK_LOCKOUT_SPEED <= 0.16, config.GESTURE_WALK_LOCKOUT_SPEED
assert config.GESTURE_WALK_DEBOUNCE_SEC <= 1.5, config.GESTURE_WALK_DEBOUNCE_SEC

# Layout Studio defaults reproduce the exact shipped look.
assert abs(config.FACE_SIZE - 1.0) < 1e-9, config.FACE_SIZE
assert abs(config.FACE_CX_RATIO - 0.5) < 1e-9, config.FACE_CX_RATIO
assert config.FACE_CY_RATIO is None, config.FACE_CY_RATIO
assert config.PIP_POS == "BR", config.PIP_POS
assert abs(config.PIP_SCALE - 1.0) < 1e-9, config.PIP_SCALE
assert abs(config.PIP_X - 1.0) < 1e-9 and abs(config.PIP_Y - 1.0) < 1e-9
assert list(config.PIP_CROP) == [0.0, 0.0, 1.0, 1.0], config.PIP_CROP
assert abs(config.SLIDE_ZOOM - 1.0) < 1e-9, config.SLIDE_ZOOM

# Shipped calibration file must carry the demo keys (values may be user-toggled at runtime).
cal = json.load(open("calibration.json"))
for key in ("screen_rotation", "auto_cycle_enabled", "gesture_mode", "show_gesture_banner",
            "gesture_walk_lockout_speed", "gesture_walk_debounce_sec",
            "face_cx", "face_cy", "face_size", "pip_pos", "pip_scale",
            "pip_x", "pip_y", "pip_crop", "slide_zoom"):
    assert key in cal, f"calibration.json missing {key}"
print("DEMO_DEFAULTS_OK")
