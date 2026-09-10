"""TARS animation and playlist controller — mirrors HTML state machine.

Phases:
  BOOT  → boot sequence (4 system diagnostics + progress fill + seamless fade)
  FACE  → expressive emotion cycling (NEUTRAL / HAPPY / CURIOUS)
  EVENT → full-screen sci-fi event dossier display with scan sweep reveal

Playlist:
  Opening: 3 face beats (one per emotion × EMOTION_HOLD_TIME)
  Events: each event (EVENT_DISPLAY_TIME) interleaved with a face beat (INTERSTITIAL_TIME)
"""

from __future__ import annotations
import math
import random
import time
import pygame
import config


# ── Official Fest Events ──────────────────────────────────────────────────────
EVENTS = [
    {
        "id": "01",
        "category": "ENTREPRENEURSHIP",
        "name": "Business Battle",
        "time": "15:00 - 18:00",
        "team": "2–5 members",
        "desc": "Pitch. Persuade. Prevail. A startup pitch and strategy face-off under live scrutiny.",
    },
    {
        "id": "02",
        "category": "GAMING",
        "name": "Gaming Tournament",
        "time": "11:00 - 15:00",
        "team": "4–5 members",
        "desc": "Squad up and battle it out across the day's featured esports tournament titles.",
    },
    {
        "id": "03",
        "category": "ENGINEERING",
        "name": "Load Till It Folds",
        "time": "13:00 - 17:00",
        "team": "1–5 members",
        "desc": "Design and fabricate a structural bridge/cantilever, then stress-test until catastrophic failure.",
    },
    {
        "id": "04",
        "category": "SCIENCE",
        "name": "Event Horizon",
        "time": "10:00 - 17:00",
        "team": "2–5 members",
        "desc": "A science exhibition and interactive engineering challenge exploring ideas beyond the ordinary.",
    },
    {
        "id": "05",
        "category": "FABRICATION",
        "name": "KartKraft",
        "time": "11:00 - 16:00",
        "team": "1–5 members",
        "desc": "Design, fabricate, and race custom go-kart chassis builds against the stopwatch.",
    },
    {
        "id": "06",
        "category": "QUIZ",
        "name": "MindForge",
        "time": "10:00 - 13:00",
        "team": "Exactly 2 members",
        "desc": "A high-intensity buzzer quiz spanning astrophysics, tech architecture, and general trivia.",
    },
]

EMOTION_SEQUENCE = ["HAPPY", "PLAYFUL", "EXCITED", "CURIOUS", "CONFIDENT", "WARM", "NEUTRAL"]


def _build_playlist():
    beats = []
    # Opening pass: all 3 emotions
    for e in EMOTION_SEQUENCE:
        beats.append({"type": "face", "emotion": e, "duration": config.EMOTION_HOLD_TIME})

    # Interleaved events and face appearances
    for i, _ in enumerate(EVENTS):
        beats.append({"type": "event", "index": i, "duration": config.EVENT_DISPLAY_TIME})
        if i < len(EVENTS) - 1:
            emo = EMOTION_SEQUENCE[i % len(EMOTION_SEQUENCE)]
            beats.append({"type": "face", "emotion": emo, "duration": config.INTERSTITIAL_TIME})
    return beats


PLAYLIST = _build_playlist()


# ── Boot Sequence ─────────────────────────────────────────────────────────────

BOOT_LINES = ["VISION SYSTEM", "MOTOR ARRAY", "SPEECH ENGINE", "POWER SYSTEM"]


class BootController:
    """
    Zero-allocation boot sequence controller.
    """
    def __init__(self, w: int, h: int):
        self.w, self.h   = w, h
        self.done        = False
        self.alpha       = 255
        self._t          = 0.0

        self._line_idx   = 0
        self._line_t     = [0.0] * len(BOOT_LINES)
        self._line_check = [False] * len(BOOT_LINES)
        self._show_timer = 0.0

        self._fill_pct   = 0.0
        self._fill_timer = 0.0
        self._fading     = False
        self._fade_t     = 0.0

        # Pre-allocated fade surface to eliminate per-frame allocations during fade-out
        self._fade_surf  = pygame.Surface((w, h))
        self._fade_surf.fill(config.VOID)

    def resize(self, w: int, h: int):
        self.w, self.h = w, h
        self._fade_surf = pygame.Surface((w, h))
        self._fade_surf.fill(config.VOID)

    def update(self, dt: float):
        if self.done:
            return
        self._t += dt

        if not self._fading:
            # Reveal lines
            self._show_timer += dt
            if self._show_timer >= config.BOOT_LINE_INTERVAL:
                self._show_timer = 0.0
                if self._line_idx < len(BOOT_LINES):
                    self._line_idx += 1

            for i in range(self._line_idx):
                self._line_t[i] = min(1.0, self._line_t[i] + dt / 0.30)
                if (self._line_t[i] > config.BOOT_LINE_CHECK_DT / 0.30 and
                        not self._line_check[i]):
                    self._line_check[i] = True

            # Fill bar
            self._fill_timer += dt
            if self._fill_timer >= config.BOOT_FILL_INTERVAL:
                self._fill_timer = 0.0
                self._fill_pct = min(100.0, self._fill_pct + config.BOOT_FILL_STEP)
                if self._fill_pct >= 100.0:
                    self._fading = True
        else:
            # Fade out
            self._fade_t += dt
            if self._fade_t >= config.BOOT_HIDE_DELAY:
                fade_elapsed = self._fade_t - config.BOOT_HIDE_DELAY
                frac = fade_elapsed / config.BOOT_FADE_DURATION
                self.alpha = max(0, int(255 * (1.0 - frac)))
                if frac >= 1.0:
                    self.done = True

    def draw(self, screen: pygame.Surface, font_large: pygame.font.Font, font_small: pygame.font.Font):
        if self.done:
            return

        w, h = screen.get_size()
        cx, cy_base = w // 2, h // 2

        # 1. Header: INITIALIZING T.A.R.S.
        title1 = font_small.render("INITIALIZING ", True, config.MUTED)
        title2 = font_small.render("T.A.R.S.", True, config.TEXT)
        tw = title1.get_width() + title2.get_width()
        tx = cx - tw // 2
        ty = cy_base - int(h * 0.20)
        screen.blit(title1, (tx, ty))
        screen.blit(title2, (tx + title1.get_width(), ty))

        # 2. Diagnostic Lines
        line_w = max(260, int(w * 0.28))
        lx = cx - line_w // 2
        row_h = max(26, int(h * 0.045))

        for i in range(self._line_idx):
            frac = self._line_t[i]
            ly = ty + int(h * 0.08) + i * row_h

            # Zero-allocation color modulation instead of per-frame SRCALPHA surface
            base_col = config.MUTED if self._line_check[i] else config.MUTED_DIM
            col = tuple(int(c * frac) for c in base_col)

            lbl_surf = font_small.render(BOOT_LINES[i], True, col)
            screen.blit(lbl_surf, (lx, ly))

            if self._line_check[i]:
                ok_col = tuple(int(c * frac) for c in config.E_HAPPY)
                ok_surf = font_small.render("OK", True, ok_col)
                screen.blit(ok_surf, (lx + line_w - ok_surf.get_width(), ly))

        # 3. Progress Bar Track & Fill
        bar_y = ty + int(h * 0.08) + len(BOOT_LINES) * row_h + 16
        bar_h = max(3, int(h * 0.005))
        pygame.draw.rect(screen, config.LINE, (lx, bar_y, line_w, bar_h))

        fill_w = int(line_w * (self._fill_pct / 100.0))
        if fill_w > 0:
            pygame.draw.rect(screen, config.E_NEUTRAL, (lx, bar_y, fill_w, bar_h))

        # 4. Ready Percentage
        pct_str = f"READY / {int(self._fill_pct)}%"
        pct_surf = font_small.render(pct_str, True, config.MUTED_DIM)
        screen.blit(pct_surf, (cx - pct_surf.get_width() // 2, bar_y + bar_h + 10))

        # 5. Full boot overlay fade-out
        if self.alpha < 255:
            self._fade_surf.set_alpha(255 - self.alpha)
            screen.blit(self._fade_surf, (0, 0))


# ── Blink & Wink Controller ───────────────────────────────────────────────────

class BlinkController:
    """
    Coordinates synchronized organic eyelid movement:
      - Symmetrical dual-eye blinks with cosine velocity easing
      - Independent left and right eye winking with subtle companion micro-squint
      - Organic natural idle animation with periodic random winks
    """
    def __init__(self, face):
        self.face       = face
        self._gap       = self._new_gap()
        self._blinking  = False
        self._blink_t   = 0.0
        self._mode      = "BOTH"  # "BOTH", "LEFT", "RIGHT"
        self._duration  = config.BLINK_DURATION

    @staticmethod
    def _new_gap():
        return random.uniform(config.BLINK_MIN_GAP, config.BLINK_MAX_GAP)

    @property
    def is_blinking(self) -> bool:
        return self._blinking

    @property
    def blink_progress(self) -> float:
        if self._blinking:
            return min(1.0, self._blink_t / self._duration)
        return 0.0

    def trigger_blink(self):
        """Forces a synchronized dual-eye blink."""
        self._blinking = True
        self._blink_t  = 0.0
        self._mode     = "BOTH"
        self._duration = config.BLINK_DURATION

    def force_blink(self):
        """Alias for trigger_blink for backwards compatibility."""
        self.trigger_blink()

    def trigger_wink(self, side: str = "RANDOM"):
        """Triggers a wink on the specified side ('LEFT', 'RIGHT', or 'RANDOM')."""
        self._blinking = True
        self._blink_t  = 0.0
        s = side.upper()
        if s == "RANDOM":
            self._mode = "LEFT" if random.random() < 0.5 else "RIGHT"
        elif s in ("LEFT", "RIGHT"):
            self._mode = s
        else:
            self._mode = "LEFT"
        self._duration = config.WINK_DURATION

    def update(self, dt: float):
        if not self._blinking:
            self._gap -= dt
            if self._gap <= 0:
                self._blinking = True
                self._blink_t  = 0.0
                # 20% chance of a natural cheek-wink during idle
                if random.random() < 0.20:
                    self._mode = "LEFT" if random.random() < 0.5 else "RIGHT"
                    self._duration = config.WINK_DURATION
                else:
                    self._mode = "BOTH"
                    self._duration = config.BLINK_DURATION
        else:
            self._blink_t += dt
            half = self._duration / 2.0
            if self._blink_t <= half:
                # Organic cosine deceleration closing: 1.0 → BLINK_MIN_SCALE
                frac = self._blink_t / half
                e = 0.5 - 0.5 * math.cos(math.pi * frac)
                main_scale = 1.0 - (1.0 - config.BLINK_MIN_SCALE) * e
                # Gentle companion eye joyful crinkle (stays open & happy at 0.92)
                sub_scale  = 1.0 - 0.08 * e
            elif self._blink_t <= self._duration:
                # Organic cosine acceleration opening: BLINK_MIN_SCALE → 1.0
                frac = (self._blink_t - half) / half
                e = 0.5 - 0.5 * math.cos(math.pi * frac)
                main_scale = config.BLINK_MIN_SCALE + (1.0 - config.BLINK_MIN_SCALE) * e
                sub_scale  = 0.92 + 0.08 * e
            else:
                main_scale = 1.0
                sub_scale  = 1.0
                self._blinking = False
                self._gap = self._new_gap()

            if self._mode == "BOTH":
                self.face.set_blink_scale(main_scale, main_scale)
            elif self._mode == "LEFT":
                self.face.set_blink_scale(main_scale, sub_scale)
            elif self._mode == "RIGHT":
                self.face.set_blink_scale(sub_scale, main_scale)


# ── Playlist Controller ───────────────────────────────────────────────────────

class PlaylistController:
    """
    Coordinates face emotions, event presentations, and scan reveals.
    """
    def __init__(self, face):
        self.face            = face
        self._idx            = 0
        self._timer          = 0.0
        self._beat           = None
        self.phase           = "FACE"
        self.event_idx       = 0
        self.event_reveal_t  = 0.0
        self.manual_hold     = False
        self.manual_timer    = 0.0
        self._advance()

    def hold_manual(self, duration: float = 60.0):
        """Pauses automatic playlist progression so user manual selection stays active."""
        self.manual_hold  = True
        self.manual_timer = duration

    def resume_auto(self):
        """Resumes automatic playlist cycling."""
        self.manual_hold  = False
        self.manual_timer = 0.0

    def _advance(self):
        self._beat           = PLAYLIST[self._idx]
        self._timer          = 0.0
        self._idx            = (self._idx + 1) % len(PLAYLIST)
        self.event_reveal_t  = 0.0

        if self._beat["type"] == "face":
            self.phase = "FACE"
            self.face.set_emotion(self._beat["emotion"])
        else:
            self.phase     = "EVENT"
            self.event_idx = self._beat["index"]

    def update(self, dt: float):
        if self.phase == "EVENT":
            self.event_reveal_t += dt

        if not getattr(config, "AUTO_CYCLE_ENABLED", True):
            return  # Lock in place while auto-cycle is toggled OFF

        if self.manual_hold:
            self.manual_timer -= dt
            if self.manual_timer <= 0:
                self.manual_hold = False
            return  # Lock in place while user manual hold is active

        self._timer += dt
        target_dur = getattr(config, "EVENT_DISPLAY_TIME", 8.0) if self.phase == "EVENT" else self._beat["duration"]
        if self._timer >= target_dur:
            self._advance()

    def step_forward(self):
        self._advance()

    def step_back(self):
        self._idx = (self._idx - 2 + len(PLAYLIST)) % len(PLAYLIST)
        self._advance()

    def step_event(self, delta: int = 1):
        """Advances (+1) or rewinds (-1) event slide with high-tech scan sweep reveal."""
        now = time.time()
        if hasattr(self, "_last_step_t") and (now - self._last_step_t < 0.65):
            return  # Suppress rapid duplicate/rebound triggers
        self._last_step_t = now
        self.phase = "EVENT"
        self.event_idx = (self.event_idx + delta) % len(EVENTS)
        self.event_reveal_t = 0.0
        if not getattr(config, "AUTO_CYCLE_ENABLED", True):
            self.hold_manual(999999.0)
        else:
            self.hold_manual(getattr(config, "EVENT_DISPLAY_TIME", 8.0))

    def set_event(self, idx: int):
        """Jumps directly to event slide by index with scan sweep reveal."""
        self.phase = "EVENT"
        self.event_idx = max(0, min(len(EVENTS) - 1, int(idx)))
        self.event_reveal_t = 0.0
        if not getattr(config, "AUTO_CYCLE_ENABLED", True):
            self.hold_manual(999999.0)
        else:
            self.hold_manual(getattr(config, "EVENT_DISPLAY_TIME", 8.0))

    @property
    def current_event(self):
        if self.phase == "EVENT":
            return EVENTS[self.event_idx]
        return None

    @property
    def event_reveal_progress(self) -> float:
        """Progress of the high-tech scan sweep reveal (0.0 → 1.0)."""
        return min(1.0, self.event_reveal_t / config.EVENT_REVEAL_TIME)

    @property
    def event_progress(self) -> float:
        if self._beat["duration"] > 0:
            return min(1.0, self._timer / self._beat["duration"])
        return 1.0
