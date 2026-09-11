"""VSLIDE integration: force portrait-column mode headless, drive into EVENT slides,
run frames through the real main loop, assert it survives (no NameError/regressions).
Run: python test_vslide.py"""
import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
config.VSLIDE_MODE = True
config.VSLIDE_SCALE = 0.9

import main as M
from bridge import COMMAND_QUEUE

errors = []


def _run():
    try:
        M.main()
    except Exception as e:  # noqa: BLE001 — test must surface loop crashes
        errors.append(repr(e))


t = threading.Thread(target=_run, daemon=True)
t.start()
time.sleep(9)  # BOOT (~2-3s) -> PLAY
COMMAND_QUEUE.put({"cmd": "display_mode", "mode": "slides"})
time.sleep(12)  # EVENT frames through the vslide branch (reveal + steady + banner-free)
COMMAND_QUEUE.put({"cmd": "display_mode", "mode": "face"})
time.sleep(4)
COMMAND_QUEUE.put({"cmd": "display_mode", "mode": "auto"})
time.sleep(3)
assert not errors, f"main loop crashed in vslide mode: {errors}"
print("VSLIDE_OK", flush=True)
os._exit(0)
