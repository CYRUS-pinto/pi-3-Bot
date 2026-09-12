"""Slide text overrides render through the real card builder. Run: python test_slidetext.py"""
import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import sys
import pygame

pygame.init()
pygame.display.set_mode((64, 64))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from main import EventCardManager, make_fonts

mgr = EventCardManager(640, 360, make_fonts(640, 360))


class FakePlay:
    event_idx = 0
    event_reveal_progress = 1.0


def _pixels():
    s = pygame.Surface((640, 360)).convert()
    mgr.draw(s, FakePlay())
    return pygame.surfarray.array3d(s)


base = _pixels()
config.SLIDE_TEXT = {"0": {"name": "ZZZ TEST TITLE", "desc": "qqq test desc"}}
config.SLIDE_TEXT_REV = 1
mgr._build_cards()
edited = _pixels()
assert not (base == edited).all(), "override changed nothing?"
config.SLIDE_TEXT = {}
config.SLIDE_TEXT_REV = 2
mgr._build_cards()
restored = _pixels()
assert (base == restored).all(), "restore differs from original?"

# punch-in supersample: 2x re-render cropped to viewport must differ from naive upscale
# (otherwise the zoom path is just magnifier mush) and must not crash
hi = mgr.render_single(0, 1280, 720, make_fonts(1280, 720))
assert hi.get_size() == (1280, 720)
up = pygame.transform.smoothscale(mgr.surfaces[0], (1280, 720))
import numpy as np
a = pygame.surfarray.array3d(hi).astype(int)
b = pygame.surfarray.array3d(up).astype(int)
assert abs(a - b).mean() > 1.0, "supersample identical to upscale?"
print("PUNCHIN_OK")
print("SLIDETEXT_OK")
