"""Landmark hand-confirm unit tests: pure function + fail-open wrapper. No model needed.
Run: python test_landmarks.py"""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vision import hand_is_open, HandConfirm


def _pts(overrides):
    p = [(0.5, 0.5)] * 21
    for i, v in overrides.items():
        p[i] = v
    return p


# open palm: index/middle/ring tips clearly above PIPs
assert hand_is_open(_pts({8: (0.4, 0.3), 6: (0.4, 0.5), 12: (0.5, 0.3),
                          10: (0.5, 0.5), 16: (0.6, 0.3), 14: (0.6, 0.5)})) is True
# fist: all tips below PIPs
assert hand_is_open(_pts({8: (0.4, 0.6), 6: (0.4, 0.5), 12: (0.5, 0.6),
                          10: (0.5, 0.5), 16: (0.6, 0.6), 14: (0.6, 0.5)})) is False
# pointing (index only) counts — single-finger sweeps must work
assert hand_is_open(_pts({8: (0.4, 0.3), 6: (0.4, 0.5), 12: (0.5, 0.6),
                          10: (0.5, 0.5), 16: (0.6, 0.6), 14: (0.6, 0.5)})) is True
# sleeve blob: everything level
assert hand_is_open([(0.5, 0.5)] * 21) is False
# exact-margin edge: strictly greater required
assert hand_is_open(_pts({8: (0.4, 0.48), 6: (0.4, 0.5)})) is False
assert hand_is_open(_pts({8: (0.4, 0.47), 6: (0.4, 0.5)})) is True
# malformed input never crashes the loop
assert hand_is_open([(0.5, 0.5)] * 5) is False

# wrapper fail-open: bogus path => disabled, never raises
hc = HandConfirm("/nonexistent/hand.task")
assert hc.ok is False
print("LANDMARKS_OK")
