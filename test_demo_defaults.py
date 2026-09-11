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
# Full-freedom bounds: positions roam -1..2 (off-canvas clips), sizes capped at crash-safe maxima.
assert 0.2 <= config.FACE_SIZE <= 4.0, config.FACE_SIZE
assert -1.0 <= config.FACE_CX_RATIO <= 2.0, config.FACE_CX_RATIO
assert config.FACE_CY_RATIO is None or -1.0 <= config.FACE_CY_RATIO <= 2.0
assert config.PIP_POS in ("TR", "TL", "BR", "BL", "FREE"), config.PIP_POS
assert 0.1 <= config.PIP_SCALE <= 3.0, config.PIP_SCALE
assert -1.0 <= config.PIP_X <= 2.0 and -1.0 <= config.PIP_Y <= 2.0
assert len(list(config.PIP_CROP)) == 4, config.PIP_CROP
assert 0.3 <= config.SLIDE_ZOOM <= 2.0, config.SLIDE_ZOOM
assert -1.0 <= config.SLIDE_X <= 2.0 and -1.0 <= config.SLIDE_Y <= 2.0
assert config.VSLIDE_MODE is False, "vslide ships OFF (opt-in per venue)"
assert 0.3 <= config.VSLIDE_SCALE <= 1.0 and 0.0 <= config.VSLIDE_X <= 1.0 and 0.0 <= config.VSLIDE_Y <= 1.0
assert config.VSLIDE_FIT == "FIT" and config.VSLIDE_ROT == 0

# Shipped calibration file must carry the demo keys (values may be user-toggled at runtime).
cal = json.load(open("calibration.json"))
for key in ("screen_rotation", "auto_cycle_enabled", "gesture_mode", "show_gesture_banner",
            "gesture_walk_lockout_speed", "gesture_walk_debounce_sec",
            "face_cx", "face_cy", "face_size", "pip_pos", "pip_scale",
            "pip_x", "pip_y", "pip_crop", "slide_zoom", "slide_x", "slide_y",
            "vslide_mode", "vslide_scale", "vslide_x", "vslide_y",
            "vslide_fit", "vslide_rot", "slide_text", "slide_text_rev",
            "gesture_hand_size", "gesture_confirm_n",
            "foam_l", "foam_t", "foam_r", "foam_b"):
    assert key in cal, f"calibration.json missing {key}"
assert isinstance(config.SLIDE_TEXT, dict) and isinstance(config.SLIDE_TEXT_REV, int)
assert 0.5 <= config.GESTURE_HAND_SIZE <= 2.0, config.GESTURE_HAND_SIZE
assert config.GESTURE_CONFIRM_N in (1, 2, 3), config.GESTURE_CONFIRM_N
for _fv in ("FOAM_L", "FOAM_T", "FOAM_R", "FOAM_B"):
    assert 0.0 <= getattr(config, _fv) <= 0.4, (_fv, getattr(config, _fv))
print("DEMO_DEFAULTS_OK")
