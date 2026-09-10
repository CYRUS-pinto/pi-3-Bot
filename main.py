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
        w, h = self.w, self.h
        is_portrait = (getattr(config, "SCREEN_ORIENTATION", "AUTO") == "PORTRAIT") or (
            getattr(config, "SCREEN_ORIENTATION", "AUTO") == "AUTO" and h > w
        )
        fxs, fsm, fmd, flg, fxl, fxxl = (
            self.fonts["xs"], self.fonts["sm"], self.fonts["md"],
            self.fonts["lg"], self.fonts["xl"], self.fonts["xxl"],
        )

        pad_x  = max(28, int(w * 0.05)) if is_portrait else max(55, int(w * 0.08))
        card_w = w - pad_x * 2

        for i, ev in enumerate(EVENTS):
            surf = pygame.Surface((w, h)).convert()
            surf.fill(config.VOID)

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

            self.surfaces.append(surf)

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
        # ponytail: 0.5 → 540×960 canvas; rotate ~6ms + smoothscale ~8ms ≈ 14ms total vs ~31ms at 0.667.
        # Verified on-Pi: 0.667 could never hold 30fps (31ms > 33ms budget before ANY drawing) — that WAS the lag.
        # Softness from 2x upscale is invisible at 2m viewing; judder is not.
        _CANVAS_SCALE = 0.5
        canvas_w = max(1, int(h * _CANVAS_SCALE))   # landscape h=1080 → portrait canvas_w=540
        canvas_h = max(1, int(w * _CANVAS_SCALE))   # landscape w=1920 → portrait canvas_h=960
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

    from arduino import get_arduino
    arduino  = get_arduino()

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

    def update_canvas_geometry():
        nonlocal canvas, canvas_w, canvas_h, fonts, face_label, face_label_x, face_label_y, face_hint, face_hint_x, face_hint_y
        current_rot = getattr(config, "SCREEN_ROTATION", 0)
        if current_rot in (90, 270) and w > h:
            _CANVAS_SCALE = 0.5
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
        renderer.invalidate_full()

    from vision import get_vision_metrics as _get_vm  # ponytail: cached import — thermal governor reads cooling without per-frame cost
    clock   = pygame.time.Clock()
    total_t = 0.0
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

    running = True
    while running:
        frame_no += 1
        if frame_no % 45 == 0:  # ponytail: 1.5s cadence — thermal moves slowly, per-frame metrics read would be pure overhead
            try:
                cooling_active = bool(_get_vm().get("cooling", False))
            except Exception:
                pass
        if (is_low_power and getattr(config, "LOW_POWER_ENABLED", True)) or cooling_active:
            fps_target = getattr(config, "LOW_POWER_RENDER_FPS", 20)
        else:
            fps_target = config.TARGET_FPS
        dt = min(clock.tick(fps_target) / 1000.0, 0.05)
        total_t += dt

        # ── Command Dispatcher (Handles queue and Pygame events) ─────────────
        def handle_payload(payload: dict):
            nonlocal last_gesture_banner, last_gesture_banner_until, last_cmd_t, last_cmd_sig, is_low_power, swipe_flash_dir, swipe_flash_t
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
                if arduino:
                    arduino.trigger_gesture("WAVE")
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

                if arduino:
                    if direction in ("next", "right"):
                        arduino.trigger_gesture("SWIPE_RIGHT")
                    elif direction in ("prev", "left"):
                        arduino.trigger_gesture("SWIPE_LEFT")
                    elif direction in ("up", "face"):
                        arduino.trigger_gesture("SWIPE_UP")
                    elif direction in ("down", "slides", "events"):
                        arduino.trigger_gesture("SWIPE_DOWN")

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
                    if arduino:
                        arduino.set_standby(is_low_power)
                    if is_low_power:
                        last_gesture_banner = "[STANDBY // LOW POWER // SENSORS MONITORING]"
                        last_gesture_banner_until = total_t + 2.0
                        renderer.invalidate_full()
                    else:
                        last_gesture_banner = "[ACTIVE // HUMAN TARGET ACQUIRED]"
                        last_gesture_banner_until = total_t + 2.0
                        renderer.invalidate_full()

                if active and nx is not None and ny is not None and not is_low_power:
                    if arduino:
                        arduino.set_gaze(float(nx) - 0.5, float(ny) - 0.5)

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

        # ── Draw ─────────────────────────────────────────────────────────────
        if phase == "BOOT":
            renderer.draw(total_t, dt, draw_face=False)
            boot.draw(canvas, fonts["lg"], fonts["sm"])
        else:
            in_event = (play.phase == "EVENT")
            # Draw background and stars (face drawn only during FACE phase)
            renderer.draw(total_t, dt, draw_face=not in_event)

            if not in_event:
                canvas.blit(face_label, (face_label_x, face_label_y))
                canvas.blit(face_hint,  (face_hint_x,  face_hint_y))
                # Draw optical target acquisition HUD during face mode
                hud.draw(canvas, total_t)
            else:
                card_mgr.draw(canvas, play)

            # Draw sci-fi dialogue subtitle capsule when speaking
            if speech.is_speaking and speech.current_subtitle:
                draw_speech_subtitles(canvas, speech.current_subtitle, fonts["sm"], renderer.face.active_color, canvas_w, canvas_h)

        # ── Performance Telemetry HUD Graph (Toggle with D) ───────────────────
        recent_ms.append(dt * 1000.0)
        if len(recent_ms) > 60:
            recent_ms.pop(0)

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
        if config.SHOW_CAMERA_PIP:
            from vision import get_latest_pip_surface
            pip_surf = get_latest_pip_surface()
            if pip_surf:
                pw, ph = pip_surf.get_width(), pip_surf.get_height()
                px = canvas_w - pw - max(16, int(canvas_w * 0.02))
                py = canvas_h - ph - max(16, int(canvas_h * 0.03))
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
        if rot == 180:
            flipped = pygame.transform.flip(canvas, True, True)
            if flipped.get_size() == screen.get_size():
                screen.blit(flipped, (0, 0))
            else:
                pygame.transform.scale(flipped, screen.get_size(), screen)
        elif rot in (90, 270):
            angle = 270 if rot == 90 else 90
            if canvas is not screen:
                rotated = pygame.transform.rotate(canvas, angle)
            else:
                rotated = pygame.transform.rotate(screen.copy(), angle)
            # Scale up to fill physical screen if canvas was downscaled.
            # smoothscale = bilinear filtering — eliminates the pixelated/blocky look.
            if rotated.get_size() != screen.get_size():
                pygame.transform.smoothscale(rotated, screen.get_size(), screen)
            else:
                screen.blit(rotated, (0, 0))
        elif canvas is not screen:
            screen.blit(canvas, (0, 0))

        pygame.display.flip()

    if arduino:
        arduino.stop()
    vision.stop()
    bridge.stop()
    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
