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

# Layout Studio values are user-tunable from the remote: assert VALIDITY (ranges/membership),
# not equality — the pocket remote legitimately rewrites these (Pi reported FREE = user dragged it).
assert 0.4 <= config.FACE_SIZE <= 2.0, config.FACE_SIZE
assert 0.1 <= config.FACE_CX_RATIO <= 0.9, config.FACE_CX_RATIO
assert config.FACE_CY_RATIO is None or 0.1 <= config.FACE_CY_RATIO <= 0.9
assert config.PIP_POS in ("TR", "TL", "BR", "BL", "FREE"), config.PIP_POS
assert 0.3 <= config.PIP_SCALE <= 1.5, config.PIP_SCALE
assert 0.0 <= config.PIP_X <= 1.0 and 0.0 <= config.PIP_Y <= 1.0
assert len(list(config.PIP_CROP)) == 4, config.PIP_CROP
assert 0.5 <= config.SLIDE_ZOOM <= 1.0, config.SLIDE_ZOOM
assert 0.0 <= config.SLIDE_X <= 1.0 and 0.0 <= config.SLIDE_Y <= 1.0
assert config.VSLIDE_MODE is False, "vslide ships OFF (opt-in per venue)"
assert 0.3 <= config.VSLIDE_SCALE <= 1.0 and 0.0 <= config.VSLIDE_X <= 1.0 and 0.0 <= config.VSLIDE_Y <= 1.0

# Shipped calibration file must carry the demo keys (values may be user-toggled at runtime).
cal = json.load(open("calibration.json"))
for key in ("screen_rotation", "auto_cycle_enabled", "gesture_mode", "show_gesture_banner",
            "gesture_walk_lockout_speed", "gesture_walk_debounce_sec",
            "face_cx", "face_cy", "face_size", "pip_pos", "pip_scale",
            "pip_x", "pip_y", "pip_crop", "slide_zoom", "slide_x", "slide_y",
            "vslide_mode", "vslide_scale", "vslide_x", "vslide_y"):
    assert key in cal, f"calibration.json missing {key}"
print("DEMO_DEFAULTS_OK")
