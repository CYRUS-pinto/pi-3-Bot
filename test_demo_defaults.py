"""Demo gate: boxed vertical bot must boot portrait, cool, and forgiving. Run: python test_demo_defaults.py
 ponytail: asserts module CONSTANTS (user-toggled live settings like auto-cycle/rotation live in
 calibration.json and are allowed to drift — the gate guards what code ships, not what users press).
"""
import json

import config

config.load_calibration()  # mirrors Pi boot: live file overrides where keys exist

# Shipped constants — calibration.json cannot change these; if one flips, the build regressed.
assert config.SCREEN_ORIENTATION == "PORTRAIT", config.SCREEN_ORIENTATION
assert config.TARGET_FPS == 30, config.TARGET_FPS
assert config.GESTURE_DEBUG_LOGS is False, "debug spam must stay off for demo"
assert abs(config.GESTURE_REBOUND_LOCKOUT_SEC - 0.55) < 1e-9, config.GESTURE_REBOUND_LOCKOUT_SEC
assert config.LOW_POWER_ENABLED is True, "boxed bot with no ventilation must sleep"
assert config.IDLE_RENDER_FPS <= 10, config.IDLE_RENDER_FPS  # empty-room standby must sip, not clockwise heat

# Walk lockout is UI-tunable: guardrail range, not a pin (Antigravity validated 0.12; tars.log condemned 0.07).
assert 0.10 <= config.GESTURE_WALK_LOCKOUT_SPEED <= 0.16, config.GESTURE_WALK_LOCKOUT_SPEED
assert config.GESTURE_WALK_DEBOUNCE_SEC <= 1.5, config.GESTURE_WALK_DEBOUNCE_SEC

# Shipped calibration file must carry the demo keys (values may be user-toggled at runtime).
cal = json.load(open("calibration.json"))
for key in ("screen_rotation", "auto_cycle_enabled", "gesture_mode", "show_gesture_banner",
            "gesture_walk_lockout_speed", "gesture_walk_debounce_sec"):
    assert key in cal, f"calibration.json missing {key}"
print("DEMO_DEFAULTS_OK")
