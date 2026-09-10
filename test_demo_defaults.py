"""One-check demo gate: boxed vertical bot must boot portrait, cool, and forgiving. Run: python test_demo_defaults.py"""
import json

import config

config.load_calibration()  # calibration.json overrides defaults, like on the Pi

assert config.SCREEN_ORIENTATION == "PORTRAIT", config.SCREEN_ORIENTATION
assert config.TARGET_FPS == 30, config.TARGET_FPS
assert config.GESTURE_DEBUG_LOGS is False, "debug spam must stay off for demo"
assert abs(config.GESTURE_WALK_LOCKOUT_SPEED - 0.12) < 1e-9, config.GESTURE_WALK_LOCKOUT_SPEED  # Antigravity-validated; do not retune blind
assert config.GESTURE_WALK_DEBOUNCE_SEC <= 1.5, config.GESTURE_WALK_DEBOUNCE_SEC
assert abs(config.GESTURE_REBOUND_LOCKOUT_SEC - 0.55) < 1e-9, config.GESTURE_REBOUND_LOCKOUT_SEC  # perf analysis #2
assert config.SHOW_GESTURE_BANNER is True, "non-tech users need swipe feedback"
assert config.LOW_POWER_ENABLED is True, "boxed bot with no ventilation must sleep"
assert config.SCREEN_ROTATION == 270, config.SCREEN_ROTATION
assert config.AUTO_CYCLE_ENABLED is True, "attract mode must run unattended"

cal = json.load(open("calibration.json"))
assert cal["screen_rotation"] == 270 and cal["auto_cycle_enabled"] is True
print("DEMO_DEFAULTS_OK")
