"""TARS main — boot → face → event sequence with maximum performance.

Key Enhancements:
  - Event Card surfaces PRE-RENDERED at startup/resize:
    * Zero font rasterization or text wrapping per frame during event display
    * Single blit rendering during static frames
    * High-tech scan sweep reveal transition (450ms) matching Interstellar HUD aesthetic
    * Balanced vertical rhythm eliminating dead void space
  - Face rendering decoupled and seamless against pre-baked background
  - All labels, hints, and top-bar elements pre-cached
  - Fullscreen toggle and resize handling bug-free
"""

from __future__ import annotations
import math
import os
import sys
import time
import queue
import pygame
import config
from animation import BootController, BlinkController, PlaylistController, EVENTS
from speech import SpeechController, VOICE_CUES
from bridge import BridgeReceiver, CMD_EVENT_TYPE, COMMAND_QUEUE
from hud import OpticalHUD


def configure_video():
    if config.USE_FRAMEBUFFER:
        os.environ["SDL_VIDEODRIVER"] = "fbcon"
        os.environ["SDL_FBDEV"]       = config.FRAMEBUFFER_DEVICE


def make_screen():
    flags = pygame.FULLSCREEN | pygame.DOUBLEBUF if config.FULLSCREEN else pygame.DOUBLEBUF
    size  = (config.WIDTH, config.HEIGHT) if (config.WIDTH and config.HEIGHT) else (0, 0)
    return pygame.display.set_mode(size, flags)


# ── Font System ───────────────────────────────────────────────────────────────

def make_fonts(w: int, h: int) -> dict[str, pygame.font.Font]:
    # Use min(w,h) as reference so portrait mode never makes fonts too huge
    ref = min(w, h)
    return {
        "xs":  pygame.font.Font(None, max(12, int(ref * 0.022))),
        "sm":  pygame.font.Font(None, max(14, int(ref * 0.030))),
        "md":  pygame.font.Font(None, max(18, int(ref * 0.040))),
        "lg":  pygame.font.Font(None, max(24, int(ref * 0.058))),
        "xl":  pygame.font.Font(None, max(36, int(ref * 0.082))),
        "xxl": pygame.font.Font(None, max(52, int(ref * 0.120))),
    }


def _render_wrapped_text(surface: pygame.Surface, font: pygame.font.Font,
                         text: str, color: tuple, x: int, y: int, max_w: int) -> int:
    """Render word-wrapped text directly to target surface."""
    words = text.split()
    line = ""
    for word in words:
        test = (line + " " + word).strip()
        if font.size(test)[0] <= max_w:
            line = test
        else:
            if line:
                surface.blit(font.render(line, True, color), (x, y))
                y += font.get_height() + 4
            line = word
    if line:
        surface.blit(font.render(line, True, color), (x, y))
        y += font.get_height() + 4
    return y


# ── Strip-path mapping (two-layer present) ──────────────────────────────────────
# Integer-exact coordinate maps for the 90°/270° + integer-scale present path.
# Proven pixel-identical vs pygame.transform.rotate+scale (rotcheck harness, both angles).
# Rule: strip path runs ONLY at integer scale S; anything else falls back to full pipeline.

def _strip_scale_for(rot: int, cw: int, ch: int, sw: int, sh: int) -> int | None:
    """Integer upscale factor for the rotated canvas, or None (=> full pipeline)."""
    if rot in (90, 270):
        rw, rh = ch, cw
    elif rot in (0, 180):
        rw, rh = cw, ch
    else:
        return None
    if rw <= 0 or rh <= 0 or sw % rw != 0 or sh % rh != 0:
        return None
    sx, sy = sw // rw, sh // rh
    return sx if sx == sy else None


def pip_geom(pip_surf, cw: int, ch: int):
    """PiP video origin + display size honoring PIP_POS (corners or FREE fractions),
    PIP_SCALE, and PIP_CROP source cut. Returns (surf, px, py, dw, dh).
    Crop cuts ceiling/floor out of the camera BEFORE scaling (WYSIWYG with the box)."""
    s = min(3.0, max(0.1, float(getattr(config, "PIP_SCALE", 1.0))))
    pw, ph = pip_surf.get_width(), pip_surf.get_height()
    try:
        _cx, _cy, _cw, _ch = [min(1.0, max(0.0, float(v))) for v in getattr(config, "PIP_CROP", [0, 0, 1, 1])]
    except Exception:
        _cx, _cy, _cw, _ch = (0.0, 0.0, 1.0, 1.0)
    if (_cx, _cy, _cw, _ch) != (0.0, 0.0, 1.0, 1.0) and _cw > 0.05 and _ch > 0.05:
        _rx = min(pw - 1, max(0, int(_cx * pw)))
        _ry = min(ph - 1, max(0, int(_cy * ph)))
        _rw = max(1, min(pw - _rx, int(_cw * pw)))
        _rh = max(1, min(ph - _ry, int(_ch * ph)))
        pip_surf = pip_surf.subsurface(pygame.Rect(_rx, _ry, _rw, _rh)).copy()
        pw, ph = _rw, _rh
    dw = max(48, int(min(pw, max(96, int(cw * 0.34))) * s))
    dh = max(1, int(dw * ph / pw))
    if (dw, dh) != (pw, ph):
        pip_surf = pygame.transform.smoothscale(pip_surf, (dw, dh))
    mx, my = max(16, int(cw * 0.02)), max(16, int(ch * 0.03))
    pos = str(getattr(config, "PIP_POS", "BR")).upper()
    if pos == "FREE":
        _fx = min(1.0, max(0.0, float(getattr(config, "PIP_X", 1.0))))
        _fy = min(1.0, max(0.0, float(getattr(config, "PIP_Y", 1.0))))
        px, py = int(_fx * max(0, cw - dw)), int(_fy * max(0, ch - dh))
        if py < 20 and pos == "FREE":
            py = 20  # keep the tag strip on-canvas
    elif pos == "TL":
        px, py = mx, my + 20
    elif pos == "TR":
        px, py = cw - dw - mx, my + 20
    elif pos == "BL":
        px, py = mx, ch - dh - my
    else:
        px, py = cw - dw - mx, ch - dh - my
    return pip_surf, px, py, dw, dh


def _strip_map_point(x: int, y: int, cw: int, ch: int, angle: int, s: int) -> tuple[int, int]:
    """Canvas pixel -> screen pixel through exact-90 rotation + integer scale."""
    if angle == 90:
        return (s * y, s * (cw - 1 - x))
    return (s * (ch - 1 - y), s * x)  # angle == 270


def _strip_map_rect(x0: int, y0: int, w0: int, h0: int,
                    cw: int, ch: int, angle: int, s: int) -> tuple[int, int, int, int]:
    """Canvas rect -> screen rect through exact-90 rotation + integer scale. All ints.
    Derived from the proven transpose identities (angle90: R[i,j]=C[Wc-1-j,i];
    angle270: R[i,j]=C[j,Hc-1-i]). Pixel-equality harness covers both."""
    if angle == 90:
        rx, ry, rw, rh = y0, cw - x0 - w0, h0, w0
    else:  # angle == 270
        rx, ry, rw, rh = ch - y0 - h0, x0, h0, w0
    return (rx * s, ry * s, rw * s, rh * s)


# ── Pre-rendered Event Cards ──────────────────────────────────────────────────

class EventCardManager:
    """
    Pre-renders all 6 event dossiers into static surfaces at startup.
    During runtime, rendering an event is a single instant blit.
    """
    def __init__(self, w: int, h: int, fonts: dict[str, pygame.font.Font]):
        self.w, self.h = w, h
        self.fonts = fonts
        self.surfaces: list[pygame.Surface] = []
        self.last_drawn_idx: int = 0
        self._build_cards()

    def resize(self, w: int, h: int, fonts: dict[str, pygame.font.Font]):
        self.w, self.h = w, h
        self.fonts = fonts
        self._build_cards()

    def _build_cards(self):
        self.surfaces = []
        for i in range(len(EVENTS)):
            self.surfaces.append(self.render_single(i, self.w, self.h, self.fonts))

    def render_single(self, i: int, w: int, h: int, fonts: dict[str, pygame.font.Font]) -> pygame.Surface | None:
        """Renders ONE event card at arbitrary size (zoom-in supersampling). None on bad index."""
        if not (0 <= i < len(EVENTS)):
            return None
        fxs, fsm, fmd, flg, fxl, fxxl = (
            fonts["xs"], fonts["sm"], fonts["md"],
            fonts["lg"], fonts["xl"], fonts["xxl"],
        )
        is_portrait = (getattr(config, "SCREEN_ORIENTATION", "AUTO") == "PORTRAIT") or (
            getattr(config, "SCREEN_ORIENTATION", "AUTO") == "AUTO" and h > w
        )
        ev = EVENTS[i]
        pad_x  = max(28, int(w * 0.05)) if is_portrait else max(55, int(w * 0.08))
        card_w = w - pad_x * 2
        surf = pygame.Surface((w, h)).convert()
        surf.fill(config.VOID)
        # ponytail: pocket-remote text overrides (SLIDE_TEXT {index: {name, desc}}).
        # Length-capped so a phone typo can't blow the layout.
        _ov = getattr(config, "SLIDE_TEXT", {}) or {}
        _ov = _ov.get(str(i), {}) or {}
        ev = dict(ev, name=str(_ov.get("name", ev["name"]))[:60],
                  desc=str(_ov.get("desc", ev["desc"]))[:300])

        # 1. Fest Header (Clean sci-fi title at upper center)
        ky = max(26, int(h * 0.045)) if is_portrait else max(38, int(h * 0.085))
        kicker = fxs.render("SIX FREQUENCIES. ONE EVENT HORIZON.", True, config.MUTED_DIM)
        title  = flg.render("RESOENANCE", True, config.TEXT)
        surf.blit(kicker, (w // 2 - kicker.get_width() // 2, ky))
        surf.blit(title,  (w // 2 - title.get_width() // 2, ky + kicker.get_height() + 4))

        sep_y = ky + kicker.get_height() + title.get_height() + 10
        pygame.draw.line(surf, config.LINE, (pad_x, sep_y), (pad_x + card_w, sep_y), 1)

        # 2. Event Number & Category Header
        ny = sep_y + int(h * (0.018 if is_portrait else 0.025))
        num_surf = fxxl.render(ev["id"], True, config.MUTED_DIM)
        cat_tag  = f"[ {ev['category']} ]"
        cat_surf = fsm.render(cat_tag, True, config.E_NEUTRAL)

        surf.blit(num_surf, (pad_x, ny))
        surf.blit(cat_surf, (pad_x + card_w - cat_surf.get_width(),
                            ny + num_surf.get_height() // 2 - cat_surf.get_height() // 2))

        # Hairline beneath category
        dy = ny + num_surf.get_height() + 8
        pygame.draw.line(surf, config.LINE, (pad_x, dy), (pad_x + card_w, dy), 1)

        # 3. Event Name
        name_y    = dy + int(h * (0.016 if is_portrait else 0.020))
        name_surf = fxl.render(ev["name"], True, config.TEXT)
        surf.blit(name_surf, (pad_x, name_y))

        # 4. Description (Comfortably spaced, eliminates dead voids)
        desc_y = name_y + name_surf.get_height() + int(h * 0.014)
        desc_max_w = card_w - (12 if is_portrait else int(card_w * 0.18))
        after_desc_y = _render_wrapped_text(surf, fmd, ev["desc"], (195, 192, 186),
                                           pad_x, desc_y, desc_max_w)

        # 5. High-tech Structured Metadata Panel
        if is_portrait:
            # 2x2 Grid for vertical screens
            meta_box_y = max(after_desc_y + int(h * 0.025), int(h * 0.50))
            meta_box_h = max(110, int(h * 0.16))
            meta_box_w = card_w
            pygame.draw.rect(surf, config.PANEL, (pad_x, meta_box_y, meta_box_w, meta_box_h), border_radius=6)
            pygame.draw.rect(surf, config.LINE,  (pad_x, meta_box_y, meta_box_w, meta_box_h), width=1, border_radius=6)

            mid_x = pad_x + meta_box_w // 2
            mid_y = meta_box_y + meta_box_h // 2
            pygame.draw.line(surf, config.LINE, (mid_x, meta_box_y + 10), (mid_x, meta_box_y + meta_box_h - 10), 1)
            pygame.draw.line(surf, config.LINE, (pad_x + 10, mid_y), (pad_x + meta_box_w - 10, mid_y), 1)

            quads = [
                ("SCHEDULE",  ev["time"],            pad_x + 14, meta_box_y + 10, config.TEXT),
                ("TEAM SIZE", ev["team"],            mid_x + 14, meta_box_y + 10, config.TEXT),
                ("VENUE",     "MAIN ARENA / CAMPUS", pad_x + 14, mid_y + 10,      config.MUTED),
                ("STATUS",    "OFFICIAL ENTRY OPEN", mid_x + 14, mid_y + 10,      config.E_HAPPY),
            ]
            for lbl, val, qx, qy, col in quads:
                lbl_surf = fxs.render(lbl, True, config.MUTED_DIM)
                val_surf = fsm.render(val, True, col)
                surf.blit(lbl_surf, (qx, qy))
                surf.blit(val_surf, (qx, qy + lbl_surf.get_height() + 4))
        else:
            meta_box_y = max(after_desc_y + int(h * 0.025), int(h * 0.58))
            meta_box_h = max(70, int(h * 0.16))
            meta_box_w = card_w
            pygame.draw.rect(surf, config.PANEL, (pad_x, meta_box_y, meta_box_w, meta_box_h), border_radius=6)
            pygame.draw.rect(surf, config.LINE,  (pad_x, meta_box_y, meta_box_w, meta_box_h), width=1, border_radius=6)

            cols = [
                ("SCHEDULE",  ev["time"]),
                ("TEAM SIZE", ev["team"]),
                ("VENUE",     "MAIN CAMPUS / ARENA"),
                ("STATUS",    "OFFICIAL ENTRY OPEN"),
            ]
            col_w = meta_box_w // len(cols)
            inner_pad_y = meta_box_y + int(meta_box_h * 0.22)
            for col_i, (lbl, val) in enumerate(cols):
                cx_col = pad_x + col_i * col_w + 20
                lbl_surf = fxs.render(lbl, True, config.MUTED_DIM)
                val_surf = fsm.render(val, True, config.TEXT if col_i < 2 else config.E_HAPPY if col_i == 3 else config.MUTED)
                surf.blit(lbl_surf, (cx_col, inner_pad_y))
                surf.blit(val_surf, (cx_col, inner_pad_y + lbl_surf.get_height() + 6))
                if col_i > 0:
                    pygame.draw.line(surf, config.LINE,
                                     (pad_x + col_i * col_w, meta_box_y + 12),
                                     (pad_x + col_i * col_w, meta_box_y + meta_box_h - 12), 1)

        # 6. Static Right Rail Dots
        rail_x = w - max(16, int(w * 0.025)) if is_portrait else w - max(30, int(w * 0.035))
        step_y = max(18, int(h * 0.028)) if is_portrait else max(22, int(h * 0.036))
        ry0    = h // 2 - (len(EVENTS) - 1) * step_y // 2
        for dot_i in range(len(EVENTS)):
            dot_y = ry0 + dot_i * step_y
            is_curr = (dot_i == i)
            dot_col = config.E_NEUTRAL if is_curr else config.MUTED_DIM
            radius  = 4 if is_curr else 2
            pygame.draw.circle(surf, dot_col, (rail_x, dot_y), radius)

        # 7. Static Bottom Timeline Ticks
        tick_w   = max(28, int(w * 0.035)) if is_portrait else max(45, int(w * 0.045))
        tick_gap = 6 if is_portrait else 8
        total_tw = len(EVENTS) * tick_w + (len(EVENTS) - 1) * tick_gap
        tx0 = w // 2 - total_tw // 2
        ty  = h - max(32, int(h * 0.045))
        for tick_i in range(len(EVENTS)):
            col = (config.E_NEUTRAL if tick_i == i
                   else config.MUTED_DIM if tick_i < i
                   else config.LINE)
            pygame.draw.rect(surf, col, (tx0 + tick_i * (tick_w + tick_gap), ty, tick_w, 4), border_radius=1)

        # 8. Bottom Sci-Fi Keyboard Hint
        if is_portrait:
            hint_str = "RIGHT HAND SWIPE: NEXT SLIDE  //  LEFT HAND: PREV  //  SWIPE UP: FACE"
            hint_surf = fxs.render(hint_str, True, (60, 64, 75))
            surf.blit(hint_surf, (w // 2 - hint_surf.get_width() // 2, h - int(h * 0.025)))
        else:
            hint_str = "RIGHT HAND SWIPE: NEXT SLIDE  //  LEFT HAND: PREV  //  SWIPE UP: FACE  //  SPACE: ADVANCE"
            hint_surf = fxs.render(hint_str, True, (44, 46, 52))
            surf.blit(hint_surf, (w - hint_surf.get_width() - max(20, int(w * 0.03)), h - int(h * 0.030)))

        return surf


    def draw(self, screen: pygame.Surface, play: PlaylistController):
        """Draws event card with smooth wipe transition over the previous slide."""
        idx = play.event_idx
        if not (0 <= idx < len(self.surfaces)):
            return

        card_surf = self.surfaces[idx]
        progress  = play.event_reveal_progress

        if progress < 1.0:
            # Keep previous slide surface visible underneath rather than blanking to void/stars!
            prev_idx = getattr(self, "last_drawn_idx", 0)
            if 0 <= prev_idx < len(self.surfaces) and prev_idx != idx:
                screen.blit(self.surfaces[prev_idx], (0, 0))

            # High-tech scan reveal sweep (top to bottom)
            sweep_y = int(self.h * progress)
            if sweep_y > 0:
                clip_rect = pygame.Rect(0, 0, self.w, sweep_y)
                screen.blit(card_surf, (0, 0), clip_rect)
                # Glowing amber scanline at sweep front
                pygame.draw.line(screen, config.E_NEUTRAL, (0, sweep_y), (self.w, sweep_y), 2)
                glow_col = tuple(int(c * 0.35) for c in config.E_NEUTRAL)
                pygame.draw.line(screen, glow_col, (0, max(0, sweep_y - 2)), (self.w, max(0, sweep_y - 2)), 1)
        else:
            # Full instant blit
            screen.blit(card_surf, (0, 0))
            self.last_drawn_idx = idx


def draw_speech_subtitles(screen: pygame.Surface, text: str, font: pygame.font.Font, active_color: tuple, w: int, h: int):
    """Renders a cinematic sci-fi dialogue subtitle capsule at the bottom-center of the screen."""
    # ponytail: same subtitle rasterized every spoken frame — cache on (text, size, font); speech changes text, not 30×/sec
    _sc = getattr(draw_speech_subtitles, "_cap_cache", None)
    if _sc is None:
        _sc = draw_speech_subtitles._cap_cache = {}
    key = (text, w, h, id(font))
    hit = _sc.get(key)
    if hit is None:
        if len(_sc) >= 4:
            _sc.clear()
        max_text_w = int(w * 0.76)
        words = text.split(" ")
        lines = []
        curr_line = ""
        for word in words:
            test_l = (curr_line + " " + word).strip()
            if font.size(test_l)[0] > max_text_w and curr_line:
                lines.append(curr_line)
                curr_line = word
            else:
                curr_line = test_l
        if curr_line:
            lines.append(curr_line)

        line_surfs = [font.render(l, True, (245, 245, 250)) for l in lines]
        box_w = max(s.get_width() for s in line_surfs) + 38
        total_text_h = sum(s.get_height() for s in line_surfs) + (len(lines) - 1) * 4
        box_h = total_text_h + 16
        bg_s = pygame.Surface((box_w, box_h), pygame.SRCALPHA)
        bg_s.fill((10, 14, 20, 220))
        hit = _sc[key] = (line_surfs, bg_s, box_w, box_h)
    line_surfs, bg_s, box_w, box_h = hit
    bx = (w - box_w) // 2
    by = h - box_h - max(30, int(h * 0.055))
    screen.blit(bg_s, (bx, by))

    b_col = tuple(int(c * 0.7) for c in active_color)
    pygame.draw.rect(screen, b_col, (bx, by, box_w, box_h), width=1, border_radius=4)

    # Active pulse dot
    dot_col = tuple(min(255, int(c * 1.25)) for c in active_color)
    pygame.draw.circle(screen, dot_col, (bx + 14, by + box_h // 2), 3)

    # Text blit
    curr_y = by + 8
    for s in line_surfs:
        screen.blit(s, (bx + 26, curr_y))
        curr_y += s.get_height() + 4


# ── Main Entry ────────────────────────────────────────────────────────────────

def main():
    configure_video()
    if config.AUDIO_ENABLED:
        try:
            pygame.mixer.pre_init(config.AUDIO_SAMPLE_RATE, -16, config.AUDIO_CHANNELS, config.AUDIO_BUFFER)
        except Exception:
            pass
    pygame.init()
    pygame.display.set_caption(config.WINDOW_TITLE)

    screen = make_screen()
    w, h   = screen.get_size()
    rot    = getattr(config, "SCREEN_ROTATION", 0)
    if rot in (90, 270) and w > h:
        # Render at reduced internal resolution to make pygame.transform.rotate fast.
        # ponytail: 0.5 → canvas at half panel res, rotated, EXACT 2x integer upscale to panel.
        # 0.4 was tried and looked bad: 2.5x non-integer nearest gives uneven pixels (doubled AND tripled
        # side by side) — jagged text shimmer. Integer 2x doubles every pixel cleanly.
        # Rule: upscale ratio must stay integer. Panel is 720p (flip is 19ms there vs 47ms at 1080p).
        _CANVAS_SCALE = 0.5
        canvas_w = max(1, int(h * _CANVAS_SCALE))
        canvas_h = max(1, int(w * _CANVAS_SCALE))
        # Landscape canvas drawn then rotated to portrait
        canvas = pygame.Surface((canvas_w, canvas_h)).convert()
    else:
        canvas_w, canvas_h = w, h
        canvas = screen
    fonts  = make_fonts(canvas_w, canvas_h)

    from renderer import Renderer
    renderer = Renderer(canvas)

    boot     = BootController(canvas_w, canvas_h)
    blink    = None
    play     = None
    card_mgr = EventCardManager(canvas_w, canvas_h, fonts)

    # Phase 2 & 3: Speech, Optical HUD, External Command Bridge, and Vision Daemon
    speech   = SpeechController(renderer.face)
    hud      = OpticalHUD(canvas_w, canvas_h, fonts)
    bridge   = BridgeReceiver(config.BRIDGE_UDP_PORT)
    bridge.start()

    from vision import UniversalVisionTracker
    vision   = UniversalVisionTracker(config.BRIDGE_UDP_PORT)
    vision.start()

    # ponytail: servo hardware retired — no arduino import, no serial scan, no thread.
    # Revive: git checkout HEAD~ -- arduino.py arduino/ && re-add get_arduino() here.
    # Pre-render face header and bottom keyboard hint overlays
    def make_face_overlays(w_curr, h_curr, f_dict):
        is_port = (getattr(config, "SCREEN_ORIENTATION", "AUTO") == "PORTRAIT") or (
            getattr(config, "SCREEN_ORIENTATION", "AUTO") == "AUTO" and h_curr > w_curr
        )
        fl_str = "T.A.R.S. // MK-IV TACTICAL RECON" if is_port else "SCHOOL OF ENGINEERING PRESENTS  //  T.A.R.S."
        fl = f_dict["sm"].render(fl_str, True, config.MUTED)
        fl_x = w_curr // 2 - fl.get_width() // 2
        fl_y = max(42, int(h_curr * 0.065)) if is_port else max(52, int(h_curr * 0.082))
        
        fh_str = "SWIPE HAND: SLIDES  //  1-7: EMOTIONS  //  D: HUD  //  R: ROTATE" if is_port else "1-7: EMOTIONS  //  W/E: WINK  //  S: TALK  //  T: SCAN  //  SPACE: DOSSIER  //  D: HUD  //  R: ROTATE"
        fh = f_dict["xs"].render(fh_str, True, (55, 60, 72))
        fh_x = w_curr // 2 - fh.get_width() // 2 if is_port else w_curr - fh.get_width() - max(20, int(w_curr * 0.03))
        fh_y = h_curr - int(h_curr * (0.025 if is_port else 0.030))
        return fl, fl_x, fl_y, fh, fh_x, fh_y

    face_label, face_label_x, face_label_y, face_hint, face_hint_x, face_hint_y = make_face_overlays(canvas_w, canvas_h, fonts)

    # ── Static hi-res layer (crisp text) ──────────────────────────────────────
    # ponytail: text quality lives HERE, not on the dynamic canvas. Static canvas renders at FULL
    # portrait res, so its rotate lands EXACTLY on screen — zero upscale, zero blur. Rebuilt only on
    # slide/phase/geometry change (~45ms once per 8s). Dynamic layer stays small+fast. Split wins both.
    sc_w = sc_h = 0
    sc_fonts = None
    sc_card_mgr = None
    sc_label = sc_hint = None
    sc_label_x = sc_label_y = sc_hint_x = sc_hint_y = 0
    sc_canvas = None

    def _build_static_layer():
        nonlocal sc_w, sc_h, sc_fonts, sc_card_mgr
        nonlocal sc_label, sc_label_x, sc_label_y, sc_hint, sc_hint_x, sc_hint_y, sc_canvas
        _srot = getattr(config, "SCREEN_ROTATION", 0)
        if _srot in (90, 270) and w > h:
            sc_w, sc_h = h, w  # full portrait res: rotated output == screen size, no scale step
            sc_fonts = make_fonts(sc_w, sc_h)
            sc_card_mgr = EventCardManager(sc_w, sc_h, sc_fonts)
            sc_label, sc_label_x, sc_label_y, sc_hint, sc_hint_x, sc_hint_y = make_face_overlays(
                sc_w, sc_h, sc_fonts)
            sc_canvas = pygame.Surface((sc_w, sc_h)).convert()
        else:
            sc_w, sc_h = 0, 0
            sc_fonts = sc_card_mgr = sc_canvas = None

    _build_static_layer()

    def update_canvas_geometry():
        nonlocal canvas, canvas_w, canvas_h, fonts, face_label, face_label_x, face_label_y, face_hint, face_hint_x, face_hint_y
        _strip_reset()
        _build_static_layer()
        current_rot = getattr(config, "SCREEN_ROTATION", 0)
        if current_rot in (90, 270) and w > h:
            _CANVAS_SCALE = 0.5  # keep integer 2x upscale (see above) — do not retune blind
            canvas_w = max(1, int(h * _CANVAS_SCALE))
            canvas_h = max(1, int(w * _CANVAS_SCALE))
            canvas = pygame.Surface((canvas_w, canvas_h)).convert()
        else:
            canvas_w, canvas_h = w, h
            canvas = screen
        fonts = make_fonts(canvas_w, canvas_h)
        renderer.resize(canvas)
        boot.resize(canvas_w, canvas_h)
        card_mgr.resize(canvas_w, canvas_h, fonts)
        hud.resize(canvas_w, canvas_h, fonts)
    face_label, face_label_x, face_label_y, face_hint, face_hint_x, face_hint_y = make_face_overlays(canvas_w, canvas_h, fonts)

    # ── Vertical-slides column (portrait cards floating mid-screen, thermocol-proof) ──
    # ponytail: landscape panel whose edges hide behind foam -> slides become a portrait
    # column (the vertical card design) instead of a cropped fullscreen card. Lazy-built,
    # keyed on (scale, canvas size); landscape-only (portrait panels already show cards).
    vs_fonts = None
    vs_mgr = None
    vs_canvas = None
    vs_key = None

    vs_show = None       # cached transformed column (surf, x, y)
    vs_show_key = None

    def _vslide_layer():
        # Base portrait card at FULL canvas height (scale applies at present, not build).
        nonlocal vs_fonts, vs_mgr, vs_canvas, vs_key
        if not getattr(config, "VSLIDE_MODE", False) or canvas_w <= canvas_h:
            return (None, None)
        _key = (canvas_w, canvas_h)
        if vs_mgr is None or _key != vs_key:
            _vw = max(96, int(canvas_h * 0.5625))
            _vh = max(96, canvas_h)
            vs_fonts = make_fonts(_vw, _vh)
            vs_mgr = EventCardManager(_vw, _vh, vs_fonts)
            vs_canvas = pygame.Surface((_vw, _vh)).convert()
            vs_key = _key
        return (vs_mgr, vs_canvas)
        renderer.invalidate_full()

    from vision import get_vision_metrics as _get_vm  # ponytail: cached import — thermal governor reads cooling without per-frame cost
    import gc as _gc
    _gc.disable()  # ponytail: cyclic GC pauses are frame-time landmines (~5-20ms hitches on Pi3).
    # Refcounting still frees everything promptly; full collects happen below at slide transitions,
    # where a 10ms pause hides inside the reveal sweep. Worst case fallback: every ~60s.
    clock   = pygame.time.Clock()
    total_t = 0.0
    last_draw_ms = 0.0  # ponytail: actual draw+present cost. recent_ms/dt includes tick() idle wait —
    # it reads 50.0ms at a 20fps cap and lies about draw cost. Heartbeat reports this one.
    phase   = "BOOT"
    cooling_active = False  # ponytail: render loop previously ignored thermal state — hottest thread ran full speed while boxed
    frame_no = 0
    show_diagnostics = config.SHOW_DIAGNOSTICS
    is_low_power = False
    recent_ms: list[float] = []
    last_gesture_banner = ""
    last_gesture_banner_until = 0.0
    last_cmd_t = 0.0
    last_cmd_sig = ""
    # Swipe visual feedback flash
    swipe_flash_dir = ""     # "left", "right", "up", "down"
    swipe_flash_t   = -9.0   # timestamp when flash started

    # Pre-allocate swipe flash surfaces once (never allocate inside the render loop!)
    # These thin line surfaces are reused every frame — no GC pressure.
    _flash_v_surfs: dict[int, pygame.Surface] = {}   # width → SRCALPHA surf (vertical strips)
    _flash_h_surfs: dict[int, pygame.Surface] = {}   # height → SRCALPHA surf (horizontal strips)
    def _get_flash_v(px_w: int, px_h: int) -> pygame.Surface:
        if px_w not in _flash_v_surfs or _flash_v_surfs[px_w].get_height() != px_h:
            _flash_v_surfs[px_w] = pygame.Surface((max(1, px_w), px_h), pygame.SRCALPHA)
        return _flash_v_surfs[px_w]
    def _get_flash_h(px_w: int, px_h: int) -> pygame.Surface:
        if px_h not in _flash_h_surfs or _flash_h_surfs[px_h].get_width() != px_w:
            _flash_h_surfs[px_h] = pygame.Surface((px_w, max(1, px_h)), pygame.SRCALPHA)
        return _flash_h_surfs[px_h]

    # ── Two-layer present state (strip path: static cache + dynamic strips) ──
    # Full pipeline re-transforms 2M static pixels every frame. Strip path rotates+scales
    # the static layer ONCE per change and only the face/PiP/clock/stars per frame.
    _strip_static = None
    _strip_static_key = None
    _strip_static_prev = None  # ponytail: previous slide's static — reveal sweeps new over old, both hi-res
    _strip_build_frame = -1000000  # last static build frame (zoom drags throttle to 2Hz)
    _fzoom_surf = None  # cached supersampled punch-in card (full pipeline zoom > 1)
    _fzoom_key = None
    _strip_face_key = None
    _strip_face_surf = None
    _strip_face_xy = (0, 0)
    _strip_clock_key = ""
    _strip_clock_surf = None
    _strip_clock_xy = (0, 0)
    _strip_pip_tag = None
    _strip_pip_tag_key = None

    def _strip_reset():
        nonlocal _strip_static, _strip_static_key, _strip_static_prev, _strip_build_frame
        nonlocal _strip_face_key, _strip_face_surf
        nonlocal _strip_clock_key, _strip_clock_surf, _strip_pip_tag, _strip_pip_tag_key
        _strip_static = None
        _strip_static_key = None
        _strip_static_prev = None
        _strip_build_frame = -1000000
        _strip_face_key = None
        _strip_face_surf = None
        _strip_clock_key = ""
        _strip_clock_surf = None
        _strip_pip_tag = None
        _strip_pip_tag_key = None

    running = True
    while running:
        frame_no += 1
        if frame_no % 45 == 0:  # ponytail: 1.5s cadence — thermal moves slowly, per-frame metrics read would be pure overhead
            try:
                # Hysteresis band: enter cooling at 60 (vision flag), exit only below 55.
                # Without this the governor flaps 30↔20fps at the boundary — that oscillation IS judder.
                _vm = _get_vm()
                if bool(_vm.get("cooling", False)):
                    cooling_active = True
                elif (_vm.get("temp_c", 99.0) or 99.0) < 55.0:
                    cooling_active = False
            except Exception:
                pass
        if is_low_power and getattr(config, "LOW_POWER_ENABLED", True):
            fps_target = getattr(config, "IDLE_RENDER_FPS", 8)  # empty room: sip power, wake instantly
        elif cooling_active:
            fps_target = getattr(config, "LOW_POWER_RENDER_FPS", 20)
        else:
            fps_target = config.TARGET_FPS
        dt = min(clock.tick(fps_target) / 1000.0, 0.05)
        total_t += dt

        # ── Command Dispatcher (Handles queue and Pygame events) ─────────────
        def handle_payload(payload: dict):
            nonlocal last_gesture_banner, last_gesture_banner_until, last_cmd_t, last_cmd_sig, is_low_power, swipe_flash_dir, swipe_flash_t
            nonlocal vs_mgr, vs_canvas, vs_key, vs_show, vs_show_key
            if not payload:
                return

            cmd = payload.get("cmd", "").lower()
            direction = payload.get("dir", payload.get("direction", "")).lower()
            sig = f"{cmd}:{direction}:{payload.get('index')}:{payload.get('mode')}"
            now_t = time.time()
            # De-duplication: filter out duplicate commands received within 60ms
            if sig == last_cmd_sig and (now_t - last_cmd_t) < 0.060:
                return
            last_cmd_t = now_t
            last_cmd_sig = sig

            # Manual user actions hold for 60s so auto-playlist doesn't overwrite them
            if cmd in ("emotion", "wink", "blink", "say") and play:
                play.hold_manual(60.0)

            if cmd == "say":
                speech.play_cue(
                    payload.get("clip", "greeting"),
                    blink_ctrl=blink,
                    custom_text=payload.get("text"),
                    custom_emotion=payload.get("emotion"),
                    custom_wink=payload.get("wink"),
                    custom_duration=payload.get("duration"),
                )
            elif cmd == "emotion":
                renderer.face.set_emotion(payload.get("name", "HAPPY"))
            elif cmd == "wink":
                if blink:
                    blink.trigger_wink(payload.get("side", "LEFT"))
            elif cmd == "blink":
                if blink:
                    blink.trigger_blink()
            elif cmd == "playlist":
                if play:
                    mode = payload.get("mode", "manual")
                    if mode == "auto":
                        config.AUTO_CYCLE_ENABLED = True
                        play.resume_auto()
                        config.tlog("SlideControl", "Playlist Mode -> AUTO")
                    else:
                        config.AUTO_CYCLE_ENABLED = False
                        play.hold_manual(999999.0)
                        config.tlog("SlideControl", "Playlist Mode -> MANUAL HOLD")
            elif cmd in ("auto_cycle", "set_auto_cycle"):
                if "enabled" in payload:
                    config.AUTO_CYCLE_ENABLED = bool(payload["enabled"])
                else:
                    config.AUTO_CYCLE_ENABLED = not getattr(config, "AUTO_CYCLE_ENABLED", True)
                if play:
                    if config.AUTO_CYCLE_ENABLED:
                        play.resume_auto()
                    else:
                        play.hold_manual(999999.0)
                config.save_calibration()
                config.tlog("SlideControl", f"Auto Cycle -> {'ENABLED' if config.AUTO_CYCLE_ENABLED else 'PAUSED'}")
            elif cmd in ("slide_duration", "set_duration", "slide_timing"):
                val = float(payload.get("duration", payload.get("seconds", 8.0)))
                config.EVENT_DISPLAY_TIME = max(1.0, min(120.0, val))
                config.save_calibration()
                config.tlog("SlideControl", f"Slide Duration -> {config.EVENT_DISPLAY_TIME:.1f}s")
            elif cmd == "fit_visible":
                # ponytail: FIT math lives SERVER-side (an earlier phone-side version mixed
                # screen fractions with leftover anchors and silently fit nothing — proven by
                # foam_l arriving while zoom stayed 1.0). Anchors are fractions of LEFTOVER space.
                _fl = min(0.4, max(0.0, float(getattr(config, "FOAM_L", 0.0))))
                _ft = min(0.4, max(0.0, float(getattr(config, "FOAM_T", 0.0))))
                _fr = min(0.4, max(0.0, float(getattr(config, "FOAM_R", 0.0))))
                _fb = min(0.4, max(0.0, float(getattr(config, "FOAM_B", 0.0))))
                _vw, _vh = max(0.1, 1 - _fl - _fr), max(0.1, 1 - _ft - _fb)
                _zm = min(2.0, max(0.3, min(_vw, _vh)))
                _rem = 1 - _zm
                config.SLIDE_ZOOM = _zm
                config.SLIDE_X = min(2.0, max(-1.0, (_fl + (_vw - _zm) / 2) / _rem if _rem > 0.01 else 0.5))
                config.SLIDE_Y = min(2.0, max(-1.0, (_ft + (_vh - _zm) / 2) / _rem if _rem > 0.01 else 0.5))
                _vh2 = min(1.5, max(0.2, _vh))
                _cw2 = _vh2 * 9 / 16 / max(0.01, (canvas_w / max(1, canvas_h)))
                _remx = 1 - _cw2
                _remy = 1 - _vh2
                config.VSLIDE_SCALE = _vh2
                config.VSLIDE_X = min(2.0, max(-1.0, (_fl + (_vw - _cw2) / 2) / _remx if _remx > 0.01 else 0.5))
                config.VSLIDE_Y = min(2.0, max(-1.0, (_ft + (_vh - _vh2) / 2) / _remy if _remy > 0.01 else 0.5))
                config.save_calibration()
                renderer.face._invalidate()
                last_gesture_banner = f"[FIT VISIBLE: zoom {_zm:.2f}]"
                last_gesture_banner_until = total_t + 1.6
                config.tlog("SlideControl", f"Fit visible -> zoom {_zm:.2f} (foam L{_fl:.2f} T{_ft:.2f} R{_fr:.2f} B{_fb:.2f})")
            elif cmd in ("gesture_mode", "set_gesture_mode"):
                mode = str(payload.get("mode", "")).upper()
                if mode in ("HORIZONTAL_SWIPE", "HORIZONTAL", "SLIDES_ONLY"):
                    config.GESTURE_MODE = "HORIZONTAL_SWIPE"
                elif mode in ("4_WAY", "ALL"):
                    config.GESTURE_MODE = "4_WAY"
                else:
                    config.GESTURE_MODE = "4_WAY" if getattr(config, "GESTURE_MODE", "HORIZONTAL_SWIPE") == "HORIZONTAL_SWIPE" else "HORIZONTAL_SWIPE"
                config.save_calibration()
                config.tlog("GestureEngine", f"Gesture Mode -> {config.GESTURE_MODE}")
            elif cmd == "event":
                if play:
                    play.phase = "EVENT"
                    play.event_idx = payload.get("index", 0)
                    play.event_reveal_t = 0.0
                    play.hold_manual(999999.0)
                    renderer.invalidate_full()
                    ev_name = EVENTS[play.event_idx]["name"]
                    config.tlog("SlideControl", f"Jump to Slide #{play.event_idx + 1}: {ev_name}")
            elif cmd == "display_mode":
                m = payload.get("mode", "auto").lower()
                if play:
                    if m == "face":
                        play.phase = "FACE"
                        play.hold_manual(999999.0)
                        renderer.invalidate_full()
                        config.tlog("SlideControl", "Mode -> ROBOT FACE")
                    elif m == "slides":
                        play.phase = "EVENT"
                        play.event_reveal_t = 0.0
                        play.hold_manual(999999.0)
                        renderer.invalidate_full()
                        ev_name = EVENTS[play.event_idx]["name"]
                        config.tlog("SlideControl", f"Mode -> EVENT SLIDES (#{play.event_idx + 1}: {ev_name})")
                    elif m == "auto":
                        play.resume_auto()
                        renderer.invalidate_full()
                        config.tlog("SlideControl", "Mode -> AUTO PLAYLIST")
            elif cmd in ("slide", "set_slide"):
                if play:
                    play.set_event(payload.get("index", 0))
                    renderer.invalidate_full()
                    ev_name = EVENTS[play.event_idx]["name"]
                    config.tlog("SlideControl", f"Set Slide -> #{play.event_idx + 1}: {ev_name}")
            elif cmd in ("event_step", "swipe", "gesture"):
                now_g = time.time()
                last_gesture_t = getattr(play, "_last_gesture_exec_t", 0.0) if play else 0.0
                gesture_cd = float(getattr(config, "GESTURE_COOLDOWN_SEC", 1.00))
                is_camera_gesture = bool(payload.get("gesture"))

                # Strict Cooldown across ALL directions:
                # If a camera gesture arrives while still on cooldown, DROP IT IMMEDIATELY (no queueing!)
                if is_camera_gesture and (now_g - last_gesture_t < gesture_cd):
                    rem_cd = gesture_cd - (now_g - last_gesture_t)
                    if getattr(config, "GESTURE_DEBUG_LOGS", False):
                        config.tlog("GestureSwipe", f"DROPPED {payload.get('gesture')}: on cooldown ({rem_cd:.2f}s remaining)")
                    return

                gesture_name = payload.get("gesture", "").upper()
                if not direction:
                    if "RIGHT" in gesture_name or gesture_name == "SWIPE_RIGHT":
                        direction = "next"
                    elif "LEFT" in gesture_name or gesture_name == "SWIPE_LEFT":
                        direction = "prev"
                    elif "UP" in gesture_name or gesture_name == "SWIPE_UP":
                        direction = "up"
                    elif "DOWN" in gesture_name or gesture_name == "SWIPE_DOWN":
                        direction = "down"

                # Update timestamp for unified cooldown across all directions
                if play:
                    play._last_gesture_exec_t = now_g
                    play._last_phase_change_t = now_g

                # Trigger swipe flash ONLY for real hand gestures (when SWIPE_ANIMATION_ENABLED is True)
                if is_camera_gesture and getattr(config, "SWIPE_ANIMATION_ENABLED", True):
                    if direction in ("next", "right"):
                        swipe_flash_dir = "right"
                    elif direction in ("prev", "left"):
                        swipe_flash_dir = "left"
                    elif direction in ("up", "face"):
                        swipe_flash_dir = "up"
                    elif direction in ("down", "slides", "events"):
                        swipe_flash_dir = "down"
                    swipe_flash_t = total_t

                if play:
                    if direction in ("up", "down", "face"):
                        # SWIPE UP or DOWN → Dedicated focus on ROBOT FACE
                        play.phase = "FACE"
                        play.hold_manual(999999.0)
                        renderer.invalidate_full()
                        last_gesture_banner = "[ROBOT FACE]"
                        last_gesture_banner_until = total_t + 1.2
                        config.tlog("GestureSwipe", f"SWIPE {direction.upper()} → ROBOT FACE")
                    elif direction in ("slides", "events"):
                        # Dedicated focus on EVENT SLIDES
                        play.phase = "EVENT"
                        play.event_reveal_t = 0.0
                        play.hold_manual(999999.0)
                        renderer.invalidate_full()
                        ev_name = EVENTS[play.event_idx]["name"]
                        last_gesture_banner = f"[SLIDE: {ev_name}]"
                        last_gesture_banner_until = total_t + 1.2
                        config.tlog("GestureSwipe", f"SHOW SLIDES → Slide #{play.event_idx + 1}: {ev_name}")
                    elif direction in ("toggle", "swap"):
                        play.phase = "FACE" if play.phase == "EVENT" else "EVENT"
                        play.event_reveal_t = 0.0
                        play.hold_manual(999999.0)
                        renderer.invalidate_full()
                        config.tlog("GestureSwipe", f"SWAP DISPLAY → {play.phase}")
                    else:
                        # LEFT/RIGHT → Slide navigation (always brings up slides if on face)
                        if play.phase != "EVENT":
                            play.phase = "EVENT"
                            play.event_reveal_t = 0.0
                            play.hold_manual(999999.0)
                        step = 1 if direction in ("next", "right") else -1
                        play.step_event(step)
                        renderer.invalidate_full()
                        ev_name = EVENTS[play.event_idx]["name"]
                        last_gesture_banner = f"[{'NEXT' if step > 0 else 'PREV'}: {ev_name}]"
                        last_gesture_banner_until = total_t + 1.2
                        config.tlog("GestureSwipe", f"{'SWIPE RIGHT (+1)' if step > 0 else 'SWIPE LEFT (-1)'} → Slide #{play.event_idx + 1}: {ev_name}")


            elif cmd in ("toggle_pip", "set_pip"):
                if "enabled" in payload:
                    config.SHOW_CAMERA_PIP = bool(payload["enabled"])
                else:
                    config.SHOW_CAMERA_PIP = not config.SHOW_CAMERA_PIP
                config.save_calibration()
                renderer.invalidate_full()
            elif cmd in ("toggle_diagnostics", "set_diagnostics", "toggle_hud", "set_hud"):
                nonlocal show_diagnostics
                if "enabled" in payload:
                    val = bool(payload["enabled"])
                else:
                    val = not config.HUD_ENABLED
                config.HUD_ENABLED = val
                config.SHOW_DIAGNOSTICS = val
                show_diagnostics = val
                config.save_calibration()
                renderer.invalidate_full()
                last_gesture_banner = f"[RETICLE HUD: {'ENABLED' if val else 'MUTED'}]"
                last_gesture_banner_until = total_t + 1.6
            elif cmd in ("rotate_screen", "set_rotation", "rotate_display"):
                if "rotation" in payload:
                    config.SCREEN_ROTATION = int(payload["rotation"])
                else:
                    order = [0, 90, 180, 270]
                    cur = getattr(config, "SCREEN_ROTATION", 0)
                    idx = order.index(cur) if cur in order else 0
                    config.SCREEN_ROTATION = order[(idx + 1) % len(order)]
                config.save_calibration()
                update_canvas_geometry()
                last_gesture_banner = f"[SCREEN ROTATION: {config.SCREEN_ROTATION}°]"
                last_gesture_banner_until = total_t + 1.8
                config.tlog("Display", f"Screen Rotation -> {config.SCREEN_ROTATION}°")
            elif cmd in ("rotate_camera", "set_camera_rotation"):
                if "rotation" in payload:
                    config.CAMERA_ROTATION = int(payload["rotation"])
                else:
                    order = [0, 90, 180, 270]
                    cur = getattr(config, "CAMERA_ROTATION", 0)
                    idx = order.index(cur) if cur in order else 0
                    config.CAMERA_ROTATION = order[(idx + 1) % len(order)]
                config.save_calibration()
                last_gesture_banner = f"[CAMERA ROTATION: {config.CAMERA_ROTATION}°]"
                last_gesture_banner_until = total_t + 1.8
                config.tlog("Vision", f"Camera Rotation -> {config.CAMERA_ROTATION}°")
            elif cmd == "calibrate":
                banner_items = []
                if "mirror_gaze_x" in payload:
                    config.MIRROR_GAZE_X = bool(payload["mirror_gaze_x"])
                    config.MIRROR_CAMERA_X = config.MIRROR_GAZE_X
                    banner_items.append(f"GAZE MIRROR: {'ON' if config.MIRROR_GAZE_X else 'OFF'}")
                elif "mirror_x" in payload:
                    config.MIRROR_GAZE_X = bool(payload["mirror_x"])
                    config.MIRROR_CAMERA_X = config.MIRROR_GAZE_X
                    banner_items.append(f"GAZE MIRROR: {'ON' if config.MIRROR_GAZE_X else 'OFF'}")

                if "mirror_gesture_x" in payload:
                    config.MIRROR_GESTURE_X = bool(payload["mirror_gesture_x"])
                    if vision and getattr(vision, "gesture_engine", None):
                        vision.gesture_engine.mirror = config.MIRROR_GESTURE_X
                    banner_items.append(f"SWIPE MIRROR: {'FLIPPED' if config.MIRROR_GESTURE_X else 'NATURAL'}")

                if "invert_y" in payload:
                    config.INVERT_CAMERA_Y = bool(payload["invert_y"])
                    if vision and getattr(vision, "gesture_engine", None):
                        vision.gesture_engine.invert_y = config.INVERT_CAMERA_Y
                    banner_items.append(f"INVERT Y: {'ON' if config.INVERT_CAMERA_Y else 'OFF'}")

                if "camera_position" in payload:
                    config.CAMERA_POSITION = str(payload["camera_position"]).upper()
                if "monitor_diag" in payload:
                    config.MONITOR_DIAG_INCHES = float(payload["monitor_diag"])
                if "couch_dist" in payload:
                    config.COUCH_DIST_METERS = float(payload["couch_dist"])
                if "sens_x" in payload:
                    config.GAZE_SENSITIVITY_X = float(payload["sens_x"])
                if "sens_y" in payload:
                    config.GAZE_SENSITIVITY_Y = float(payload["sens_y"])
                if "offset_x" in payload:
                    config.GAZE_OFFSET_X = float(payload["offset_x"])
                if "offset_y" in payload:
                    config.GAZE_OFFSET_Y = float(payload["offset_y"])
                if "gesture_sens" in payload:
                    config.GESTURE_SWIPE_SENSITIVITY = float(payload["gesture_sens"])
                    if vision and getattr(vision, "gesture_engine", None):
                        vision.gesture_engine.sensitivity = config.GESTURE_SWIPE_SENSITIVITY
                    banner_items.append(f"GESTURE SENS: {config.GESTURE_SWIPE_SENSITIVITY:.1f}x")
                if "sens_left" in payload:
                    config.GESTURE_SENS_LEFT = float(payload["sens_left"])
                if "sens_right" in payload:
                    config.GESTURE_SENS_RIGHT = float(payload["sens_right"])
                if "sens_up" in payload:
                    config.GESTURE_SENS_UP = float(payload["sens_up"])
                if "sens_down" in payload:
                    config.GESTURE_SENS_DOWN = float(payload["sens_down"])
                if "gesture_hand_size" in payload:
                    config.GESTURE_HAND_SIZE = min(2.0, max(0.5, float(payload["gesture_hand_size"])))
                    banner_items.append(f"HAND SIZE: {config.GESTURE_HAND_SIZE:.1f}x")
                if "gesture_confirm_n" in payload:
                    config.GESTURE_CONFIRM_N = max(1, min(3, int(payload["gesture_confirm_n"])))
                    banner_items.append(f"CONFIRM: {config.GESTURE_CONFIRM_N}x")
                for _fk, _fattr in (("foam_l", "FOAM_L"), ("foam_t", "FOAM_T"),
                                    ("foam_r", "FOAM_R"), ("foam_b", "FOAM_B")):
                    if _fk in payload:
                        setattr(config, _fattr, min(0.4, max(0.0, float(payload[_fk]))))
                # ── Layout Studio: move/resize face + PiP live from the pocket remote ──
                _layout_touched = False
                if "face_cx" in payload:
                    config.FACE_CX_RATIO = min(2.0, max(-1.0, float(payload["face_cx"])))
                    _layout_touched = True
                if "face_cy" in payload:
                    _v = payload["face_cy"]
                    config.FACE_CY_RATIO = None if _v is None else min(0.9, max(0.1, float(_v)))
                    _layout_touched = True
                if "face_size" in payload:
                    config.FACE_SIZE = min(4.0, max(0.2, float(payload["face_size"])))
                    _layout_touched = True
                if "pip_pos" in payload:
                    _pp = str(payload["pip_pos"]).upper()
                    if _pp in ("TR", "TL", "BR", "BL"):
                        config.PIP_POS = _pp
                        _layout_touched = True
                if "pip_scale" in payload:
                    config.PIP_SCALE = min(3.0, max(0.1, float(payload["pip_scale"])))
                    _layout_touched = True
                if "pip_x" in payload:
                    config.PIP_X = min(2.0, max(-1.0, float(payload["pip_x"])))
                    if str(getattr(config, "PIP_POS", "BR")).upper() != "FREE":
                        config.PIP_POS = "FREE"  # dragging X/Y implies free placement
                    _layout_touched = True
                if "pip_y" in payload:
                    config.PIP_Y = min(2.0, max(-1.0, float(payload["pip_y"])))
                    if str(getattr(config, "PIP_POS", "BR")).upper() != "FREE":
                        config.PIP_POS = "FREE"
                    _layout_touched = True
                if "pip_crop" in payload:
                    try:
                        _cr = [min(1.0, max(0.0, float(v))) for v in payload["pip_crop"]]
                        if len(_cr) == 4 and _cr[2] > 0.05 and _cr[3] > 0.05:
                            config.PIP_CROP = _cr
                            _layout_touched = True
                    except Exception:
                        pass
                if "slide_zoom" in payload:
                    config.SLIDE_ZOOM = min(2.0, max(0.3, float(payload["slide_zoom"])))
                    _layout_touched = True
                if "slide_x" in payload:
                    config.SLIDE_X = min(2.0, max(-1.0, float(payload["slide_x"])))
                    _layout_touched = True
                if "slide_y" in payload:
                    config.SLIDE_Y = min(2.0, max(-1.0, float(payload["slide_y"])))
                    _layout_touched = True
                if "vslide_mode" in payload:
                    config.VSLIDE_MODE = bool(payload["vslide_mode"])
                    banner_items.append(f"VSLIDES: {'COLUMN' if config.VSLIDE_MODE else 'FULL'}")
                if "vslide_scale" in payload:
                    config.VSLIDE_SCALE = min(1.5, max(0.2, float(payload["vslide_scale"])))
                    _layout_touched = True
                if "vslide_x" in payload:
                    config.VSLIDE_X = min(2.0, max(-1.0, float(payload["vslide_x"])))
                    _layout_touched = True
                if "vslide_y" in payload:
                    config.VSLIDE_Y = min(2.0, max(-1.0, float(payload["vslide_y"])))
                    _layout_touched = True
                # ── Slide text editing: rewrite a slide's title/desc live, persisted ──
                if "slide_text" in payload and isinstance(payload["slide_text"], dict):
                    try:
                        _st = payload["slide_text"]
                        _idx = int(_st.get("index", -1))
                        from animation import EVENTS as _EVENTS
                        if 0 <= _idx < len(_EVENTS):
                            _cur = dict(getattr(config, "SLIDE_TEXT", {}) or {})
                            _entry = dict(_cur.get(str(_idx), {}))
                            if "name" in _st:
                                _nv = str(_st["name"])[:60]
                                if _nv:
                                    _entry["name"] = _nv
                                else:
                                    _entry.pop("name", None)  # empty restores original
                            if "desc" in _st:
                                _dv = str(_st["desc"])[:300]
                                if _dv:
                                    _entry["desc"] = _dv
                                else:
                                    _entry.pop("desc", None)
                            if _entry:
                                _cur[str(_idx)] = _entry
                            else:
                                _cur.pop(str(_idx), None)
                            config.SLIDE_TEXT = _cur
                            config.SLIDE_TEXT_REV = int(getattr(config, "SLIDE_TEXT_REV", 0)) + 1
                            card_mgr.resize(canvas_w, canvas_h, fonts)
                            if sc_card_mgr is not None:
                                sc_card_mgr.resize(sc_w, sc_h, sc_fonts)
                            vs_mgr, vs_canvas, vs_key = None, None, None
                            vs_show, vs_show_key = None, None
                            _strip_reset()
                            banner_items.append(f"SLIDE #{_idx + 1} TEXT UPDATED")
                    except Exception:
                        pass
                if "vslide_fit" in payload:
                    _vf = str(payload["vslide_fit"]).upper()
                    if _vf in ("FIT", "STRETCH", "FILL"):
                        config.VSLIDE_FIT = _vf
                        _layout_touched = True
                if "vslide_rot" in payload:
                    try:
                        _vr = int(payload["vslide_rot"])
                        if _vr in (0, 90, 180, 270):
                            config.VSLIDE_ROT = _vr
                            _layout_touched = True
                    except Exception:
                        pass
                if _layout_touched:
                    renderer.face._invalidate()  # recompute geometry next draw; strip cache re-keys on rect
                    # ponytail: NO banner for layout drags — banners force the full pipeline for 2s,
                    # hiding zoom/position changes while adjusting (user: "slide does not work").
                    # Phone slider labels already confirm each drag; static key rebuilds live.
                config.save_calibration()
                if banner_items:
                    last_gesture_banner = f"[{' // '.join(banner_items)}]"
                    last_gesture_banner_until = total_t + 2.0
                    renderer.invalidate_full()
                    config.tlog("Vision", f"Calibration updated -> {' // '.join(banner_items)}")
            elif cmd in ("toggle_gesture", "set_gesture"):
                if "enabled" in payload:
                    config.GESTURE_SWIPE_ENABLED = bool(payload["enabled"])
                else:
                    config.GESTURE_SWIPE_ENABLED = not getattr(config, "GESTURE_SWIPE_ENABLED", True)
                config.save_calibration()
            elif cmd in ("toggle_gesture_mode", "set_gesture_mode"):
                if "mode" in payload:
                    config.GESTURE_MODE = str(payload["mode"])
                else:
                    config.GESTURE_MODE = "HORIZONTAL_SWIPE" if getattr(config, "GESTURE_MODE", "HORIZONTAL_SWIPE") == "4_WAY" else "4_WAY"
                config.save_calibration()
                mode_str = "HORIZONTAL SLIDES" if config.GESTURE_MODE == "HORIZONTAL_SWIPE" else "4-WAY GESTURE"
                last_gesture_banner = f"[GESTURE: {mode_str}]"
                last_gesture_banner_until = total_t + 1.8
                config.tlog("Gesture", f"Mode -> {config.GESTURE_MODE}")
            elif cmd in ("toggle_swipe_anim", "set_swipe_anim", "swipe_anim"):
                if "enabled" in payload:
                    config.SWIPE_ANIMATION_ENABLED = bool(payload["enabled"])
                else:
                    config.SWIPE_ANIMATION_ENABLED = not getattr(config, "SWIPE_ANIMATION_ENABLED", True)
                config.save_calibration()
                last_gesture_banner = f"[SWIPE ANIMATION: {'ON' if config.SWIPE_ANIMATION_ENABLED else 'OFF'}]"
                last_gesture_banner_until = total_t + 1.8
                config.tlog("SlideControl", f"Swipe Animation -> {'ENABLED' if config.SWIPE_ANIMATION_ENABLED else 'DISABLED'}")
            elif cmd in ("gesture_cooldown", "set_cooldown"):
                val = float(payload.get("cooldown", payload.get("seconds", 1.0)))
                config.GESTURE_COOLDOWN_SEC = max(0.3, min(3.0, val))
                config.save_calibration()
                last_gesture_banner = f"[GESTURE COOLDOWN: {config.GESTURE_COOLDOWN_SEC:.1f}s]"
                last_gesture_banner_until = total_t + 1.8
                config.tlog("GestureEngine", f"Gesture Cooldown -> {config.GESTURE_COOLDOWN_SEC:.1f}s")
            elif cmd == "target":
                active = payload.get("active", True)
                nx = payload.get("x")
                ny = payload.get("y")
                lp = bool(payload.get("low_power", False))
                if lp != is_low_power:
                    is_low_power = lp
                    if is_low_power:
                        last_gesture_banner = "[STANDBY // LOW POWER // SENSORS MONITORING]"
                        last_gesture_banner_until = total_t + 2.0
                        renderer.invalidate_full()
                    else:
                        last_gesture_banner = "[ACTIVE // HUMAN TARGET ACQUIRED]"
                        last_gesture_banner_until = total_t + 2.0
                        renderer.invalidate_full()

                hud.set_target(
                    active,
                    name=payload.get("name", "SPECTATOR"),
                    dist=payload.get("dist", "2.1m"),
                    norm_x=nx,
                    norm_y=ny,
                    count=payload.get("count", 1),
                    secondaries=payload.get("secondaries"),
                )

        # 1. Drain thread-safe COMMAND_QUEUE (Web Remote, UDP, Camera AI)
        while not COMMAND_QUEUE.empty():
            try:
                handle_payload(COMMAND_QUEUE.get_nowait())
            except queue.Empty:
                break

        # 2. Pygame Event Loop
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                k = ev.key
                if k in (pygame.K_ESCAPE, pygame.K_q):
                    running = False
                elif k == pygame.K_f:
                    config.FULLSCREEN = not config.FULLSCREEN
                    screen = make_screen()
                    w, h   = screen.get_size()
                    update_canvas_geometry()  # ponytail: was duplicating resize logic AND dropping rotation geometry → 75ms full-res rotate path
                if phase != "BOOT":
                    if k == pygame.K_SPACE:
                        play.step_forward()
                    elif k == pygame.K_RIGHT:
                        if play:
                            play.step_event(1)
                            renderer.invalidate_full()
                    elif k == pygame.K_LEFT:
                        if play:
                            play.step_event(-1)
                            renderer.invalidate_full()
                    elif k == pygame.K_UP:
                        if play:
                            play.phase = "FACE"
                            play.hold_manual(999999.0)
                            renderer.invalidate_full()
                    elif k == pygame.K_DOWN:
                        if play:
                            play.phase = "EVENT"
                            play.event_reveal_t = 0.0
                            play.hold_manual(999999.0)
                            renderer.invalidate_full()
                    elif k == pygame.K_g:
                        config.GESTURE_SWIPE_ENABLED = not getattr(config, "GESTURE_SWIPE_ENABLED", True)
                        config.save_calibration()
                        last_gesture_banner = "[GESTURE SENSOR] ONLINE" if config.GESTURE_SWIPE_ENABLED else "[GESTURE SENSOR] MUTED"
                        last_gesture_banner_until = total_t + 1.4
                    elif k == pygame.K_1:
                        renderer.face.set_emotion("NEUTRAL")
                    elif k == pygame.K_2:
                        renderer.face.set_emotion("HAPPY")
                    elif k == pygame.K_3:
                        renderer.face.set_emotion("PLAYFUL")
                    elif k == pygame.K_4:
                        renderer.face.set_emotion("EXCITED")
                    elif k == pygame.K_5:
                        renderer.face.set_emotion("CURIOUS")
                    elif k == pygame.K_6:
                        renderer.face.set_emotion("CONFIDENT")
                    elif k == pygame.K_7:
                        renderer.face.set_emotion("WARM")
                    elif k == pygame.K_w:
                        if blink:
                            blink.trigger_wink("LEFT")
                    elif k == pygame.K_e:
                        if blink:
                            blink.trigger_wink("RIGHT")
                    elif k == pygame.K_b:
                        if blink:
                            blink.trigger_blink()
                    elif k == pygame.K_s:
                        cue_keys = list(VOICE_CUES.keys())
                        curr = speech.current_cue
                        idx = (cue_keys.index(curr) + 1) % len(cue_keys) if curr in cue_keys else 0
                        speech.play_cue(cue_keys[idx], blink_ctrl=blink)
                    elif k == pygame.K_t:
                        hud.trigger_demo_scan()
                    elif k == pygame.K_d:
                        show_diagnostics = not show_diagnostics
                        config.HUD_ENABLED = show_diagnostics
                        config.SHOW_DIAGNOSTICS = show_diagnostics
                        config.save_calibration()
                        last_gesture_banner = f"[RETICLE HUD: {'ENABLED' if show_diagnostics else 'MUTED'}]"
                        last_gesture_banner_until = total_t + 1.6
                    elif k == pygame.K_r:
                        order = [0, 90, 180, 270]
                        cur = getattr(config, "SCREEN_ROTATION", 0)
                        idx = order.index(cur) if cur in order else 0
                        config.SCREEN_ROTATION = order[(idx + 1) % len(order)]
                        config.save_calibration()
                        update_canvas_geometry()
                        last_gesture_banner = f"[SCREEN ROTATION: {config.SCREEN_ROTATION}°]"
                        last_gesture_banner_until = total_t + 1.8
                        config.tlog("Display", f"Keyboard 'R' -> Screen Rotation {config.SCREEN_ROTATION}°")
                    elif k == pygame.K_c:
                        order = [0, 90, 180, 270]
                        cur = getattr(config, "CAMERA_ROTATION", 0)
                        idx = order.index(cur) if cur in order else 0
                        config.CAMERA_ROTATION = order[(idx + 1) % len(order)]
                        config.save_calibration()
                        last_gesture_banner = f"[CAMERA ROTATION: {config.CAMERA_ROTATION}°]"
                        last_gesture_banner_until = total_t + 1.8
                        config.tlog("Vision", f"Keyboard 'C' -> Camera Rotation {config.CAMERA_ROTATION}°")
                    elif k == pygame.K_p:
                        config.SHOW_CAMERA_PIP = not config.SHOW_CAMERA_PIP
                    elif k == pygame.K_m:
                        config.MIRROR_CAMERA_X = not config.MIRROR_CAMERA_X
                        config.MIRROR_GAZE_X = config.MIRROR_CAMERA_X
                        config.MIRROR_GESTURE_X = config.MIRROR_CAMERA_X
                        if vision and getattr(vision, "gesture_engine", None):
                            vision.gesture_engine.mirror = config.MIRROR_GESTURE_X
                        config.save_calibration()
                        last_gesture_banner = f"[CAMERA & GESTURE MIRROR: {'ON' if config.MIRROR_CAMERA_X else 'OFF'}]"
                        last_gesture_banner_until = total_t + 2.0
                        config.tlog("Vision", f"Keyboard 'M' -> Mirror toggled to {config.MIRROR_CAMERA_X}")
                    elif k == pygame.K_0:
                        if play:
                            play.phase = "FACE" if play.phase == "EVENT" else "EVENT"
                            play.hold_manual(999999.0)
                            renderer.invalidate_full()
            elif ev.type == pygame.VIDEORESIZE:
                w, h   = ev.size
                screen = pygame.display.set_mode((w, h), pygame.RESIZABLE | pygame.DOUBLEBUF)
                update_canvas_geometry()

        # ── Update ───────────────────────────────────────────────────────────
        if phase == "BOOT":
            boot.update(dt)
            if boot.done:
                phase = "PLAY"
                play  = PlaylistController(renderer.face)
                blink = BlinkController(renderer.face)
                renderer.invalidate_full()
        else:
            renderer.face.update(dt)
            if blink:
                blink.update(dt)
            speech.update(dt, blink_ctrl=blink)
            hud.update(dt, speech_ctrl=speech, blink_ctrl=blink, face=renderer.face)
            if play:
                old_play_phase = play.phase
                play.update(dt)
                if play.phase != old_play_phase:
                    renderer.invalidate_full()
                    _gc.collect()  # hidden inside the transition; keeps cyclic trash at zero between slides
                elif frame_no % 1800 == 0:
                    _gc.collect()  # ~60s fallback so long FACE stares never accumulate cycles

        # ── Draw ─────────────────────────────────────────────────────────────
        t_db0 = time.perf_counter()
        _strip_ran = False
        if phase == "BOOT":
            renderer.draw(total_t, dt, draw_face=False)
            boot.draw(canvas, fonts["lg"], fonts["sm"])
        else:
            in_event = (play.phase == "EVENT")
            _rot_now = getattr(config, "SCREEN_ROTATION", 0)
            _sw, _sh = screen.get_size()
            S = _strip_scale_for(_rot_now, canvas_w, canvas_h, _sw, _sh) \
                if (canvas is not screen and _rot_now in (90, 270)) else None
            _sang = 270 if _rot_now == 90 else 90
            _flash_on = getattr(config, "SWIPE_ANIMATION_ENABLED", True) and (
                0.0 <= (total_t - swipe_flash_t) < 0.50)
            # Strip eligibility: steady state + reveal sweep. Anything else transient/debug -> full.
            # ponytail: reveal used to fall back to the low-res full pipeline for 0.25s per slide —
            # every slide change visibly SNAPPED pixelated->crisp. Now the sweep itself is screen-space.
            _strip_ran = (
                S is not None
                and not show_diagnostics
                and not getattr(config, "HUD_ENABLED", True)
                and not (speech.is_speaking and speech.current_subtitle)
                and not (last_gesture_banner_until > total_t and last_gesture_banner)
                and not _flash_on
            )
            # (STRIPDBG served its verdict 2026-09-11: rot=0 user-set for landscape tests. Removed.)
            if _strip_ran:
                # ── Strip path: cached static + transformed dynamic strips ──
                # ponytail: zoom is part of the key — otherwise a zoom drag wouldn't rebuild
                # the cached static until the next slide change.
                _skey = ("E" if in_event else "F", play.event_idx if in_event else -1,
                         int(getattr(config, "SLIDE_TEXT_REV", 0)),
                         _rot_now, canvas_w, canvas_h, _sw, _sh,
                         round(min(2.0, max(0.3, float(getattr(config, "SLIDE_ZOOM", 1.0)))), 3),
                         round(min(1.0, max(0.0, float(getattr(config, "SLIDE_X", 0.5)))), 3),
                         round(min(1.0, max(0.0, float(getattr(config, "SLIDE_Y", 0.5)))), 3))
                # ponytail: zoom drags re-post every tick; a 45ms rebuild per tick would stutter
                # the drag itself. Structural changes rebuild now; zoom-only re-renders at most 2Hz.
                _struct_new, _zoom_new = _skey[:8], _skey[8:]
                _struct_old = _strip_static_key[:8] if _strip_static_key else None
                _zoom_old = _strip_static_key[8:] if _strip_static_key else None
                _need_now = (_struct_new != _struct_old) or (
                    _zoom_new != _zoom_old and (frame_no - _strip_build_frame) >= 15)
                if _need_now:
                    _strip_build_frame = frame_no
                    _strip_static_prev = _strip_static
                    # base: bg smooth-upscaled once (flat void/vignette upscale cleanly)
                    pygame.transform.smoothscale(renderer._bg, (sc_w, sc_h), sc_canvas)
                    if in_event:
                        sc_card_mgr.draw(sc_canvas, play)
                        # ponytail: slide zoom letterboxes below 1.0; above 1.0 the card is
                        # RE-RENDERED at zoom size (render_single) instead of upscaling raster —
                        # punch-in stays crispy because every glyph redraws at final size.
                        # Paid once per change into the cached static — zero per-frame cost.
                        _z = min(2.0, max(0.3, float(getattr(config, "SLIDE_ZOOM", 1.0))))
                        _zx = min(1.0, max(0.0, float(getattr(config, "SLIDE_X", 0.5))))
                        _zy = min(1.0, max(0.0, float(getattr(config, "SLIDE_Y", 0.5))))
                        if _z < 0.999:
                            _zw, _zh = max(1, int(sc_w * _z)), max(1, int(sc_h * _z))
                            _zc = pygame.transform.smoothscale(sc_canvas, (_zw, _zh))
                            sc_canvas.fill(config.VOID)
                            sc_canvas.blit(_zc, (int(_zx * (sc_w - _zw)), int(_zy * (sc_h - _zh))))
                            del _zc
                        elif _z > 1.001:
                            _hw, _hh = max(1, int(sc_w * _z)), max(1, int(sc_h * _z))
                            _hf = make_fonts(_hw, _hh)
                            _hi = sc_card_mgr.render_single(play.event_idx, _hw, _hh, _hf)
                            del _hf
                            if _hi is not None:
                                sc_canvas.fill(config.VOID)
                                sc_canvas.blit(_hi,
                                               (int(_zx * (sc_w - _hw)), int(_zy * (sc_h - _hh))))
                                del _hi
                    else:
                        sc_canvas.blit(sc_label, (sc_label_x, sc_label_y))
                        sc_canvas.blit(sc_hint, (sc_hint_x, sc_hint_y))
                    _rr = pygame.transform.rotate(sc_canvas, _sang)
                    if (_rr.get_width(), _rr.get_height()) != (_sw, _sh):
                        # safety: should be exact-fit by construction; scale only if geometry lied
                        pygame.transform.smoothscale(_rr, (_sw, _sh), screen)
                    else:
                        screen.blit(_rr, (0, 0))
                    _strip_static = screen.copy()
                    _strip_static_key = _skey
                # reveal sweep in screen space: new hi-res static wipes over previous (both crisp)
                _rev = play.event_reveal_progress if in_event else 1.0
                if in_event and _rev < 1.0 and _strip_static_prev is not None:
                    screen.blit(_strip_static_prev, (0, 0))
                    _sy = int(_sh * _rev)
                    if _sy > 0:
                        screen.blit(_strip_static, (0, 0),
                                    pygame.Rect(0, 0, _sw, _sy))
                        pygame.draw.line(screen, config.E_NEUTRAL, (0, _sy), (_sw, _sy), 2)
                        _glow = tuple(int(c * 0.35) for c in config.E_NEUTRAL)
                        pygame.draw.line(screen, _glow, (0, max(0, _sy - 2)), (_sw, max(0, _sy - 2)), 1)
                else:
                    screen.blit(_strip_static, (0, 0))
                if not in_event:
                    # face strip (update() already ran above; drive rebuild state only)
                    _face = renderer.face
                    if _face._geom is None:
                        _face._compute_geom()
                    _fresh = bool(_face._dirty)
                    if _fresh:
                        _face._rebuild()
                    _fr = _face._srect
                    _fk = (_fr.x, _fr.y, _fr.w, _fr.h)
                    if _fresh or _fk != _strip_face_key or _strip_face_surf is None:
                        _fr2 = pygame.transform.rotate(_face._surf, _sang)
                        # ponytail: face is ORGANIC (circles, curves, glow) — bilinear suits it;
                        # text/dots elsewhere keep nearest-exact. Only runs when face is dirty.
                        _strip_face_surf = pygame.transform.smoothscale(
                            _fr2, (_fr2.get_width() * S, _fr2.get_height() * S))
                        _strip_face_key = _fk
                        _strip_face_xy = _strip_map_rect(
                            _fr.x, _fr.y, _fr.w, _fr.h, canvas_w, canvas_h, _sang, S)[:2]
                    screen.blit(_strip_face_surf, _strip_face_xy)
                # stars in screen space (2x2 block == what integer scale makes of a canvas dot)
                for _stx, _sty, _ph, _per in renderer._stars:
                    _frac = 0.5 + 0.5 * math.sin(2 * math.pi * total_t / _per + _ph)
                    _vv = int(40 + _frac * 130)
                    _dxx, _dyy = _strip_map_point(_stx, _sty, canvas_w, canvas_h, _sang, S)
                    screen.fill((_vv, _vv, _vv), (_dxx, _dyy, S, S))
                # clock strip (same font object => identical raster; re-transformed 1/sec)
                _cs = time.strftime("%H:%M:%S")
                if _cs != _strip_clock_key:
                    _cs0 = renderer._hud_font_md.render(_cs, True, config.MUTED)
                    _cr = pygame.transform.rotate(_cs0, _sang)
                    _strip_clock_surf = pygame.transform.scale(
                        _cr, (_cr.get_width() * S, _cr.get_height() * S))
                    _strip_clock_xy = _strip_map_rect(
                        renderer._bar_pad_x, renderer._bar_pad_y,
                        _cs0.get_width(), _cs0.get_height(),
                        canvas_w, canvas_h, _sang, S)[:2]
                    _strip_clock_key = _cs
                if _strip_clock_surf is not None:
                    screen.blit(_strip_clock_surf, _strip_clock_xy)
                # PiP strip (FACE only; small corner — photos proved full-size ate half the display)
                if config.SHOW_CAMERA_PIP and not in_event:
                    from vision import get_latest_pip_surface
                    _pip = get_latest_pip_surface()
                    if _pip is not None:
                        _pip, _px0, _py0, _pw, _ph = pip_geom(_pip, canvas_w, canvas_h)
                        if _strip_pip_tag_key != id(fonts["xs"]):
                            _strip_pip_tag = fonts["xs"].render(
                                "LIVE OPTICAL RECON [PiP]", True, (56, 235, 145))
                            _strip_pip_tag_key = id(fonts["xs"])
                        _bw, _bh = _pw + 4, _ph + 22
                        _ps = pygame.Surface((_bw, _bh), pygame.SRCALPHA)
                        _ps.fill((10, 14, 20, 255))
                        pygame.draw.rect(_ps, (56, 235, 145), (0, 0, _bw, _bh), width=1, border_radius=6)
                        _ps.blit(_strip_pip_tag, (6, 3))
                        _ps.blit(_pip, (2, 20))
                        _pr = pygame.transform.rotate(_ps, _sang)
                        # ponytail: camera video is organic — bilinear, like the face. ~1ms on this size.
                        _prs = pygame.transform.smoothscale(
                            _pr, (_pr.get_width() * S, _pr.get_height() * S))
                        _dx, _dy, _, _ = _strip_map_rect(
                            _px0 - 2, _py0 - 20, _bw, _bh, canvas_w, canvas_h, _sang, S)
                        screen.blit(_prs, (_dx, _dy))
            else:
                # ── Full pipeline (transients, debug, reveal, HUD) ──
                # Draw background and stars (face drawn only during FACE phase)
                renderer.draw(total_t, dt, draw_face=not in_event)

                if not in_event:
                    canvas.blit(face_label, (face_label_x, face_label_y))
                    canvas.blit(face_hint, (face_hint_x, face_hint_y))
                    # Draw optical target acquisition HUD during face mode
                    hud.draw(canvas, total_t)
                else:
                    _vmgr, _vc = _vslide_layer()
                    if _vmgr is not None:
                        # ponytail: column = base card -> content rotate -> FIT/STRETCH/FILL into the
                        # anchor box. Transformed output cached per (slide, scale, fit, rot, settled);
                        # reveal frames render direct (0.25s). Per-frame steady cost: one blit.
                        _vrot = getattr(config, "VSLIDE_ROT", 0)
                        _vrot = _vrot if _vrot in (0, 90, 180, 270) else 0
                        _vfit = str(getattr(config, "VSLIDE_FIT", "FIT")).upper()
                        _vfit = _vfit if _vfit in ("FIT", "STRETCH", "FILL") else "FIT"
                        _vsc = min(1.5, max(0.2, float(getattr(config, "VSLIDE_SCALE", 0.9))))
                        _vrev = play.event_reveal_progress >= 1.0
                        _vk = (play.event_idx, round(_vsc, 3), _vfit, _vrot, _vrev,
                               int(getattr(config, "SLIDE_TEXT_REV", 0)))
                        if _vk != vs_show_key:
                            _vmgr.draw(_vc, play)
                            _card = _vc if _vrot == 0 else pygame.transform.rotate(_vc, _vrot)
                            _boxH = max(1, int(canvas_h * _vsc))
                            _boxW = max(1, int(_boxH * 9 / 16))
                            _cw, _chh = _card.get_size()
                            if _vfit == "STRETCH":
                                _show = pygame.transform.scale(_card, (_boxW, _boxH)) if (_cw, _chh) != (_boxW, _boxH) else _card.copy()
                            elif _vfit == "FILL":
                                _k = max(_boxW / max(1, _cw), _boxH / max(1, _chh))
                                _tmp = pygame.transform.scale(_card, (max(1, int(_cw * _k)), max(1, int(_chh * _k))))
                                _show = pygame.Surface((_boxW, _boxH)).convert()
                                _show.blit(_tmp, ((_boxW - _tmp.get_width()) // 2, (_boxH - _tmp.get_height()) // 2))
                                del _tmp
                            else:  # FIT contain
                                _k = min(_boxW / max(1, _cw), _boxH / max(1, _chh))
                                _fw, _fh = max(1, int(_cw * _k)), max(1, int(_chh * _k))
                                if (_fw, _fh) == (_cw, _chh):
                                    _show = _card.copy()
                                else:
                                    _show = pygame.Surface((_boxW, _boxH)).convert()
                                    _show.fill(config.VOID)
                                    _fs = pygame.transform.scale(_card, (_fw, _fh))
                                    _show.blit(_fs, ((_boxW - _fw) // 2, (_boxH - _fh) // 2))
                                    del _fs
                            _vxx = int(min(1.0, max(0.0, float(getattr(config, "VSLIDE_X", 0.5)))) * max(0, canvas_w - _boxW))
                            _vyy = int(min(1.0, max(0.0, float(getattr(config, "VSLIDE_Y", 0.5)))) * max(0, canvas_h - _boxH))
                            vs_show, vs_show_key = (_show, _vxx, _vyy), _vk
                        canvas.blit(vs_show[0], (vs_show[1], vs_show[2]))
                        # zoom letterbox applies to fullscreen cards only; column has its own scale
                        _vskip_zoom = True
                    else:
                        card_mgr.draw(canvas, play)
                        _vskip_zoom = False
                    # ponytail: slide zoom/position must work in the FULL pipeline too — rot-0
                    # landscape never runs the strip path, so strip-only zoom was dead there.
                    # Below 1.0 letterboxes (smoothscale down, cached look); above 1.0 the card
                    # RE-RENDERS at zoom size so punch-in stays crispy (cached per state, ~1 blit/frame).
                    # Skipped for vslide column (it carries its own scale + position above).
                    _fz = min(2.0, max(0.3, float(getattr(config, "SLIDE_ZOOM", 1.0))))
                    _fx = min(1.0, max(0.0, float(getattr(config, "SLIDE_X", 0.5))))
                    _fy = min(1.0, max(0.0, float(getattr(config, "SLIDE_Y", 0.5))))
                    if not _vskip_zoom and (_fz < 0.999 or _fz > 1.001 or abs(_fx - 0.5) > 0.001 or abs(_fy - 0.5) > 0.001):
                        if _fz > 1.001:
                            _fzk = (play.event_idx, round(_fz, 3), round(_fx, 3), round(_fy, 3),
                                    int(getattr(config, "SLIDE_TEXT_REV", 0)))
                            if _fzk != _fzoom_key:
                                _fw, _fh = max(1, int(canvas_w * _fz)), max(1, int(canvas_h * _fz))
                                _ff = make_fonts(_fw, _fh)
                                _fzoom_surf = card_mgr.render_single(play.event_idx, _fw, _fh, _ff)
                                del _ff
                                _fzoom_key = _fzk
                            if _fzoom_surf is not None:
                                canvas.fill(config.VOID)
                                canvas.blit(_fzoom_surf,
                                            (int(_fx * (canvas_w - _fzoom_surf.get_width())),
                                             int(_fy * (canvas_h - _fzoom_surf.get_height()))))
                        else:
                            _zw, _zh = max(1, int(canvas_w * _fz)), max(1, int(canvas_h * _fz))
                            _zs = pygame.transform.smoothscale(canvas, (_zw, _zh))
                            canvas.fill(config.VOID)
                            canvas.blit(_zs, (int(_fx * (canvas_w - _zw)), int(_fy * (canvas_h - _zh))))
                            del _zs

                # Draw sci-fi dialogue subtitle capsule when speaking
                if speech.is_speaking and speech.current_subtitle:
                    draw_speech_subtitles(canvas, speech.current_subtitle, fonts["sm"], renderer.face.active_color, canvas_w, canvas_h)

        # ── Performance Telemetry HUD Graph (Toggle with D) ───────────────────
        recent_ms.append(dt * 1000.0)
        if len(recent_ms) > 60:
            recent_ms.pop(0)
        # ponytail: 10s heartbeat to tars.log — the only honest lag meter on a headless box.
        # Read it with: tail -f ~/TARS/tars.log | grep HEARTBEAT
        if frame_no % 300 == 0 and len(recent_ms) > 10:
            try:
                _hb = _get_vm()
                config.tlog("HEARTBEAT",
                            f"draw={last_draw_ms:.1f}ms path={'strip' if _strip_ran else 'full'} pace={sum(recent_ms)/len(recent_ms):.1f}ms fps_target={fps_target} "
                            f"cpu={_hb.get('temp_c', -1):.1f}C cooling={_hb.get('cooling', '?')} "
                            f"ai_fps={_hb.get('ai_fps', -1):.1f} grab={_hb.get('grab_ms', -1):.0f}ms "
                            f"phase={phase}/{getattr(play, 'phase', '-') if play else '-'}")
            except Exception:
                pass

        if show_diagnostics:
            gw, gh = max(280, int(canvas_w * 0.32)), max(106, int(canvas_h * 0.17))
            gx, gy = max(16, int(canvas_w * 0.02)), canvas_h - gh - max(16, int(canvas_h * 0.03))
            pygame.draw.rect(canvas, (8, 12, 18), (gx, gy, gw, gh), border_radius=6)
            pygame.draw.rect(canvas, (35, 45, 60), (gx, gy, gw, gh), width=1, border_radius=6)

            # Guidelines & frame time graph
            scale_y = gh / 90.0
            y30 = gy + gh - int(33.3 * scale_y)
            pygame.draw.line(canvas, (25, 35, 45), (gx, y30), (gx + gw, y30), 1)
            if len(recent_ms) > 1:
                step = gw / max(1, len(recent_ms) - 1)
                pts = [(int(gx + i * step), max(gy + 4, min(gy + gh - 4, int(gy + gh - v * scale_y))))
                       for i, v in enumerate(recent_ms)]
                pygame.draw.lines(canvas, config.E_CURIOUS, False, pts, 2)

            cur_fps = 1.0 / max(0.001, dt)
            morph_pct = int(renderer.face.morph_progress * 100)
            morph_tag = "STABLE" if not renderer.face.is_morphing else f"SYNC {morph_pct}%"
            fps_str = f"RENDER: {cur_fps:.1f} FPS ({dt*1000.0:.1f}ms) | FACE: [{renderer.face.emotion}] {morph_tag}"
            canvas.blit(fonts["xs"].render(fps_str, True, (240, 245, 255)), (gx + 10, gy + 8))

            from vision import get_vision_metrics
            vm = get_vision_metrics()
            cam_info = str(vm.get("camera_src", "none"))
            if len(cam_info) > 34:
                cam_info = "..." + cam_info[-31:]
            cam_str = f"CAMERA: {cam_info} | AI: {vm.get('ai_fps', 0.0):.1f} FPS"
            canvas.blit(fonts["xs"].render(cam_str, True, config.E_PLAYFUL), (gx + 10, gy + 24))

            lat_str1 = f"GRAB: {vm.get('grab_ms', 0.0):.1f}ms | AI: {vm.get('total_ai_ms', 0.0):.1f}ms | YUNET: {vm.get('yunet_ms', 0.0):.1f}ms"
            canvas.blit(fonts["xs"].render(lat_str1, True, (160, 200, 230)), (gx + 10, gy + 40))

            temp_c = vm.get("temp_c", 50.0)
            cooling = vm.get("cooling", False)
            temp_col = (255, 120, 120) if cooling else (56, 235, 145)
            pwr_mode = "LOW STANDBY" if is_low_power else ("THERMAL COOLING" if cooling else "OPTIMAL")
            pwr_str = f"CPU TEMP: {temp_c:.1f}°C | PWR: {pwr_mode}"
            canvas.blit(fonts["xs"].render(pwr_str, True, temp_col), (gx + 10, gy + 56))

            track_str = f"TARGETS: {vm.get('faces', 0)} Face(s) | HAND: {'ENGAGED' if vm.get('hand_active') else 'IDLE'}"
            canvas.blit(fonts["xs"].render(track_str, True, config.E_EXCITED if vm.get('hand_active') else config.E_NEUTRAL), (gx + 10, gy + 72))

        # ── Picture-in-Picture (Live Optical Recon Feed on TV) ───────────────
        # ponytail: strip path already drew PiP above — this full-pipeline block must not double-draw.
        # ponytail: PiP stays a SMALL corner in FACE phase and hides during EVENT — slides own the screen.
        # (Photos proved a 320px PiP on a 360px canvas ate half the display and covered the face.)
        _event_now = (phase != "BOOT" and play is not None and play.phase == "EVENT")
        if config.SHOW_CAMERA_PIP and not _strip_ran and not _event_now:
            from vision import get_latest_pip_surface
            pip_surf = get_latest_pip_surface()
            if pip_surf:
                pip_surf, px, py, pw, ph = pip_geom(pip_surf, canvas_w, canvas_h)
                # High-tech border and badge
                pygame.draw.rect(canvas, (10, 14, 20), (px - 2, py - 20, pw + 4, ph + 22), border_radius=6)
                pygame.draw.rect(canvas, (56, 235, 145), (px - 2, py - 20, pw + 4, ph + 22), width=1, border_radius=6)
                pip_tag = fonts["xs"].render("LIVE OPTICAL RECON [PiP]", True, (56, 235, 145))
                canvas.blit(pip_tag, (px + 4, py - 17))
                canvas.blit(pip_surf, (px, py))

        # ── On-Screen Hand Gesture Action Banner (Muted during slides) ───────
        can_show_banner = (
            last_gesture_banner_until > total_t and last_gesture_banner
        )
        if can_show_banner:
            # ponytail: banner text changes per-gesture, not per-frame — cache the raster + card, reuse the blit
            _bc = getattr(draw_speech_subtitles, "_banner_cache", None)
            if _bc is None:
                _bc = draw_speech_subtitles._banner_cache = {}
            key = (last_gesture_banner, canvas_w, id(fonts["md"]))
            hit = _bc.get(key)
            if hit is None:
                if len(_bc) >= 6:
                    _bc.clear()  # ponytail: bounded — resize/font swaps auto-miss via id(), stale entries die here
                gb_surf = fonts["md"].render(last_gesture_banner, True, config.E_EXCITED)
                gb_w, gb_h = gb_surf.get_size()
                bg_card = pygame.Surface((gb_w + 32, gb_h + 16), pygame.SRCALPHA)
                bg_card.fill((10, 14, 20, 235))
                hit = _bc[key] = (gb_surf, bg_card, gb_w, gb_h)
            gb_surf, bg_card, gb_w, gb_h = hit
            gb_x = (canvas_w - gb_w) // 2
            gb_y = max(20, int(canvas_h * 0.04))
            canvas.blit(bg_card, (gb_x - 16, gb_y - 8))
            pygame.draw.rect(canvas, config.E_EXCITED, (gb_x - 16, gb_y - 8, gb_w + 32, gb_h + 16), width=2, border_radius=6)
            canvas.blit(gb_surf, (gb_x, gb_y))

        # ── Swipe Flash Animation (sci-fi scan-line sweep) ───────────────────
        FLASH_DUR = 0.50
        flash_age = total_t - swipe_flash_t
        if getattr(config, "SWIPE_ANIMATION_ENABLED", True) and (0.0 <= flash_age < FLASH_DUR):
            prog = flash_age / FLASH_DUR                      # 0→1 over duration
            fade = int(255 * (1.0 - prog ** 0.6))             # ease-out alpha
            cw, ch = canvas_w, canvas_h
            # Color: CYAN/MINT for horizontal (slides), ELECTRIC BLUE for vertical (face)
            if swipe_flash_dir in ("right", "left"):
                r, g, b = 56, 235, 145   # mint green — slides
            else:
                r, g, b = 80, 160, 255   # electric blue — face

            if swipe_flash_dir == "right":
                lx = int(prog * cw)
                # Leading bright line — reuse pre-allocated surfaces
                for w_strip, a_frac in [(6, 1.0), (4, 0.5), (3, 0.25), (2, 0.1)]:
                    sx = max(0, lx - (6 - w_strip) * 5)
                    if sx < cw:
                        sv = _get_flash_v(w_strip, ch)
                        sv.fill((r, g, b, int(fade * a_frac)))
                        canvas.blit(sv, (sx, 0))
            elif swipe_flash_dir == "left":
                lx = int((1.0 - prog) * cw)
                for w_strip, a_frac in [(6, 1.0), (4, 0.5), (3, 0.25), (2, 0.1)]:
                    sx = min(cw - w_strip, lx + (6 - w_strip) * 5)
                    if sx >= 0:
                        sv = _get_flash_v(w_strip, ch)
                        sv.fill((r, g, b, int(fade * a_frac)))
                        canvas.blit(sv, (sx, 0))
            elif swipe_flash_dir == "up":
                ly = int((1.0 - prog) * ch)
                for h_strip, a_frac in [(6, 1.0), (4, 0.5), (3, 0.25), (2, 0.1)]:
                    sy = min(ch - h_strip, ly + (6 - h_strip) * 5)
                    if sy >= 0:
                        sh = _get_flash_h(cw, h_strip)
                        sh.fill((r, g, b, int(fade * a_frac)))
                        canvas.blit(sh, (0, sy))
            elif swipe_flash_dir == "down":
                ly = int(prog * ch)
                for h_strip, a_frac in [(6, 1.0), (4, 0.5), (3, 0.25), (2, 0.1)]:
                    sy = max(0, ly - (6 - h_strip) * 5)
                    if sy < ch:
                        sh = _get_flash_h(cw, h_strip)
                        sh.fill((r, g, b, int(fade * a_frac)))
                        canvas.blit(sh, (0, sy))

        # ── Physical Display Rotation & Output Flip ─────────────────────────
        # When canvas is smaller than screen (reduced-res portrait rendering),
        # rotate the small canvas then scale-blit to fill the physical screen.
        # This is dramatically faster than rotating a full-resolution surface.
        rot = getattr(config, "SCREEN_ROTATION", 0)
        if _strip_ran:
            pygame.display.flip()  # strips already composited directly on screen
        elif rot == 180:
            flipped = pygame.transform.flip(canvas, True, True)
            if flipped.get_size() == screen.get_size():
                screen.blit(flipped, (0, 0))
            else:
                pygame.transform.scale(flipped, screen.get_size(), screen)
        elif rot in (90, 270):
            angle = 270 if rot == 90 else 90
            # ponytail: plain rotate() won. A numpy exact-transpose was proven pixel-identical headless
            # but benched SLOWER on Pi3 (28ms vs 20ms) — the strided 2MB copy is cache-hostile on LPDDR2.
            # Clever lost to simple; simple ships.
            if canvas is not screen:
                rotated = pygame.transform.rotate(canvas, angle)
            else:
                rotated = pygame.transform.rotate(screen.copy(), angle)
            # Scale up to fill physical screen if canvas was downscaled.
            # ponytail: was smoothscale — bench on Pi3 measured 89ms for 960x540→1920x1080 (THE lag; budget is 33ms).
            # scale() (nearest) does the exact 2x step in ~5ms. Slight edge stair-stepping at 2m viewing beats 6fps.
            if rotated.get_size() != screen.get_size():
                pygame.transform.scale(rotated, screen.get_size(), screen)
            else:
                screen.blit(rotated, (0, 0))
        elif canvas is not screen:
            screen.blit(canvas, (0, 0))

        if not _strip_ran:
            pygame.display.flip()
        last_draw_ms = (time.perf_counter() - t_db0) * 1000.0

    vision.stop()
    bridge.stop()
    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
