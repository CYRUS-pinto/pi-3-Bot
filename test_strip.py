"""Strip-path proof: mapping math pixel-identical to full rotate+scale, both angles.
Run: python test_strip.py (headless, needs pygame+numpy only)"""
import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import random

import pygame

pygame.init()
pygame.display.set_mode((64, 64))

import main as M

random.seed(7)


def _pattern(w, h):
    s = pygame.Surface((w, h)).convert()
    s.fill((3, 4, 5))
    pygame.draw.circle(s, (255, 200, 100), (w // 4, h * 3 // 4), 18)
    pygame.draw.rect(s, (80, 200, 255), (w * 3 // 5, h // 8, 30, 14))
    pygame.draw.line(s, (56, 235, 145), (0, 0), (w - 1, h - 1), 2)
    return s


def _full(canvas, angle, S):
    rot = pygame.transform.rotate(canvas, angle)
    sw, sh = rot.get_width() * S, rot.get_height() * S
    out = pygame.Surface((sw, sh)).convert()
    pygame.transform.scale(rot, (sw, sh), out)
    return out


def test_rects_both_angles():
    for Wc, Hc, angle, S in ((100, 160, 90, 3), (100, 160, 270, 3),
                             (135, 241, 90, 2), (135, 241, 270, 2),
                             (540, 960, 90, 2)):
        canvas = _pattern(Wc, Hc)
        full = _full(canvas, angle, S)
        for _ in range(12):
            w0 = random.randint(1, Wc)
            h0 = random.randint(1, Hc)
            x0 = random.randint(0, Wc - w0)
            y0 = random.randint(0, Hc - h0)
            got = M._strip_map_rect(x0, y0, w0, h0, Wc, Hc, angle, S)
            # strip path: crop -> rotate -> scale -> blit at mapped origin
            crop = canvas.subsurface(pygame.Rect(x0, y0, w0, h0)).copy()
            r = pygame.transform.rotate(crop, angle)
            st = pygame.transform.scale(r, (r.get_width() * S, r.get_height() * S))
            expect_w, expect_h = got[2], got[3]
            assert (st.get_width(), st.get_height()) == (expect_w, expect_h), (angle, got, st.get_size())
            ref = full.subsurface(pygame.Rect(got[0], got[1], got[2], got[3])).copy()
            a = pygame.surfarray.array3d(st)
            b = pygame.surfarray.array3d(ref)
            assert (a == b).all(), f"PIXEL MISMATCH angle={angle} rect={(x0, y0, w0, h0)}"
    print("RECTS_OK")


def test_points_both_angles():
    for Wc, Hc, angle, S in ((100, 160, 90, 3), (100, 160, 270, 2)):
        canvas = pygame.Surface((Wc, Hc)).convert()
        canvas.fill((0, 0, 0))
        pts = [(random.randint(0, Wc - 1), random.randint(0, Hc - 1)) for _ in range(20)]
        for (x, y) in pts:
            canvas.set_at((x, y), (255, 255, 255))
        full = _full(canvas, angle, S)
        for (x, y) in pts:
            gx, gy = M._strip_map_point(x, y, Wc, Hc, angle, S)
            # nearest-SxS block must be lit in full output
            block = full.subsurface(pygame.Rect(gx, gy, S, S)).copy()
            assert pygame.surfarray.array3d(block).sum() > 0, f"DOT LOST {(x, y)} -> {(gx, gy)}"
    print("POINTS_OK")


def test_scale_for():
    assert M._strip_scale_for(270, 540, 960, 1920, 1080) == 2
    assert M._strip_scale_for(90, 540, 960, 1920, 1080) == 2
    assert M._strip_scale_for(270, 432, 768, 1920, 1080) is None  # 2.5x non-integer
    assert M._strip_scale_for(0, 540, 960, 1920, 1080) is None
    assert M._strip_scale_for(45, 540, 960, 1920, 1080) is None
    print("SCALE_OK")


def test_face_strip_equivalence():
    from face import TARSFace
    Wc, Hc, S, angle = 270, 480, 2, 90
    face = TARSFace(Wc, Hc, bg_surface=None)
    face.set_emotion("HAPPY")
    face.update(0.5)
    if face._geom is None:
        face._compute_geom()
    if face._dirty:
        face._rebuild()
    # full path: face onto canvas, rotate whole, scale
    canvas = pygame.Surface((Wc, Hc)).convert()
    canvas.fill((3, 4, 5))
    face.draw(canvas)
    full = _full(canvas, angle, S)
    # strip path: _surf only, mapped blit
    fr = face._srect
    r = pygame.transform.rotate(face._surf, angle)
    st = pygame.transform.scale(r, (r.get_width() * S, r.get_height() * S))
    gx, gy, gw, gh = M._strip_map_rect(fr.x, fr.y, fr.w, fr.h, Wc, Hc, angle, S)
    assert (st.get_width(), st.get_height()) == (gw, gh)
    ref = full.subsurface(pygame.Rect(gx, gy, gw, gh)).copy()
    assert (pygame.surfarray.array3d(st) == pygame.surfarray.array3d(ref)).all(), "FACE STRIP DIFFERS"
    print("FACE_OK")


test_rects_both_angles()
test_points_both_angles()
test_scale_for()
test_face_strip_equivalence()
print("STRIP_OK")
