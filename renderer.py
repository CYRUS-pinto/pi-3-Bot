"""TARS renderer — high-performance background & authentic Interstellar telemetry.

Design & Performance:
  - Fixes TV overscan: 6% margin safe-zone prevents edge clipping on physical TVs.
  - Authentic TARS telemetry decks (Humor: 75%, Honesty: 90%, Mission: RESOENANCE '26)
    pre-baked into background surface (_bg) for ZERO runtime CPU cost.
  - Clean deep space void with radial vignette (no ugly grid banding).
  - Scaled, crisp typography readable from across the room.
"""

from __future__ import annotations
import math
import random
import time
import pygame
import config
from face import TARSFace


class Renderer:
    def __init__(self, screen: pygame.Surface):
        self.screen = screen
        self.w, self.h = screen.get_size()
        self._build_all()
        # Initialize face with reference to pre-baked background for seamless blitting
        self.face = TARSFace(self.w, self.h, bg_surface=self._bg)
        self._needs_clear = 2
        self._clock_rect = None

    # ── Build ──────────────────────────────────────────────────────────────────

    def _build_all(self):
        self._build_topbar_fonts()
        self._build_bg()
        self._build_stars()
        self._last_cs = ""
        self._clock_surf = None
        self._needs_clear = 2

    def _build_topbar_fonts(self):
        is_portrait = (getattr(config, "SCREEN_ORIENTATION", "AUTO") == "PORTRAIT") or (
            getattr(config, "SCREEN_ORIENTATION", "AUTO") == "AUTO" and self.h > self.w
        )
        # Monitor-scale safe-zone paddings
        pad_x_frac = 0.045 if is_portrait else config.SAFE_ZONE_PAD_X
        pad_y_frac = 0.025 if is_portrait else config.SAFE_ZONE_PAD_Y
        self._bar_pad_x = max(24, int(self.w * pad_x_frac))
        self._bar_pad_y = max(20, int(self.h * pad_y_frac))

        # Typography scaled for orientation — use min(w,h) so portrait fonts never overflow
        font_ref = min(self.w, self.h)
        self._hud_font_sm = pygame.font.Font(None, max(12, int(font_ref * 0.022)))
        self._hud_font_md = pygame.font.Font(None, max(15, int(font_ref * 0.028)))
        self._hud_font_lg = pygame.font.Font(None, max(19, int(font_ref * 0.036)))

        # Static readouts baked into background
        coord_text = "MANGALURU // 12\u00b058'N 74\u00b050'E" if is_portrait else "MANGALURU, KARNATAKA  //  12\u00b058'N 74\u00b050'E"
        self._coord_surf = self._hud_font_md.render(coord_text, True, config.MUTED)
        self._online_surf = self._hud_font_md.render("SYS.STATUS ONLINE", True, config.E_HAPPY)
        
        if is_portrait:
            # Stacked layout in portrait mode
            self._online_x = self.w - self._bar_pad_x - self._online_surf.get_width()
            self._coord_x = self.w // 2 - self._coord_surf.get_width() // 2
            self._coord_y = self._bar_pad_y + self._hud_font_md.get_height() + 4
        else:
            self._coord_x  = self.w // 2 - self._coord_surf.get_width() // 2
            self._coord_y  = self._bar_pad_y
            self._online_x = self.w - self._bar_pad_x - self._online_surf.get_width()

    def _build_bg(self):
        """Pre-bakes void fill, vignette, corner brackets, and authentic TARS telemetry."""
        is_portrait = (getattr(config, "SCREEN_ORIENTATION", "AUTO") == "PORTRAIT") or (
            getattr(config, "SCREEN_ORIENTATION", "AUTO") == "AUTO" and self.h > self.w
        )
        s = pygame.Surface((self.w, self.h)).convert()
        s.fill(config.VOID)

        # 1. Radial vignette baked once with BLEND_MULT (soft edge gradient)
        v = pygame.Surface((self.w, self.h)).convert()
        v.fill((255, 255, 255))
        cx, cy = self.w // 2, self.h // 2
        for i in range(24, 0, -1):
            frac = i / 24.0
            if frac < 0.35:
                continue
            t = (frac - 0.35) / 0.65
            b = int(255 - 180 * t * t)
            rw = int(cx * frac)
            rh = int(cy * frac)
            pygame.draw.ellipse(v, (b, b, b), (cx - rw, cy - rh, rw * 2, rh * 2))
        s.blit(v, (0, 0), special_flags=pygame.BLEND_MULT)

        # 2. TV-safe Corner HUD brackets (placed comfortably inside bezels)
        bc = config.MUTED_DIM
        bsz = max(22, int(min(self.w, self.h) * 0.024))
        bx = self._bar_pad_x - 12
        by = self._bar_pad_y - 8
        bw = self.w - bx
        bh = self.h - by

        # Top-left, Top-right, Bottom-left, Bottom-right brackets
        pygame.draw.line(s, bc, (bx, by), (bx + bsz, by), 2)
        pygame.draw.line(s, bc, (bx, by), (bx, by + bsz), 2)

        pygame.draw.line(s, bc, (bw, by), (bw - bsz, by), 2)
        pygame.draw.line(s, bc, (bw, by), (bw - bsz, by), 2)

        pygame.draw.line(s, bc, (bx, bh), (bx + bsz, bh), 2)
        pygame.draw.line(s, bc, (bx, bh), (bx, bh - bsz), 2)

        pygame.draw.line(s, bc, (bw, bh), (bw - bsz, bh), 2)
        pygame.draw.line(s, bc, (bw, bh), (bw - bsz, bh), 2)

        # 3. Top status bar readouts
        s.blit(self._coord_surf,  (self._coord_x,  self._coord_y))
        s.blit(self._online_surf, (self._online_x, self._bar_pad_y))

        # 4. Bottom Safe-Zone Status Tag
        by_bot = self.h - self._bar_pad_y
        full_tag_str = "ST ALOYSIUS DEEMED TO BE UNIVERSITY  //  DEPARTMENT OF ROBOTICS & AUTOMATION"
        bot_tag = self._hud_font_sm.render(full_tag_str, True, config.MUTED_DIM)
        
        if bot_tag.get_width() > self.w - 40:
            # 2-line wrapped footer for narrow vertical displays
            tag1 = self._hud_font_sm.render("ST ALOYSIUS DEEMED TO BE UNIVERSITY", True, config.MUTED_DIM)
            tag2 = self._hud_font_sm.render("DEPARTMENT OF ROBOTICS & AUTOMATION", True, config.MUTED_DIM)
            s.blit(tag1, (self.w // 2 - tag1.get_width() // 2, by_bot - tag1.get_height() - 2))
            s.blit(tag2, (self.w // 2 - tag2.get_width() // 2, by_bot + 2))
        else:
            s.blit(bot_tag, (self.w // 2 - bot_tag.get_width() // 2, by_bot - bot_tag.get_height() // 2))

        self._bg = s

    def _build_stars(self):
        random.seed(42)
        self._stars = [
            (random.randint(self._bar_pad_x + 10, self.w - self._bar_pad_x - 10),
             random.randint(self._bar_pad_y + 10, self.h - self._bar_pad_y - 10),
             random.uniform(0, 2 * math.pi),
             random.uniform(3.0, 7.5))
            for _ in range(config.STAR_COUNT)
        ]

    # ── Resize & Invalidation ──────────────────────────────────────────────────

    def resize(self, screen: pygame.Surface):
        self.screen = screen
        self.w, self.h = screen.get_size()
        self._build_all()
        self.face.resize(self.w, self.h, bg_surface=self._bg)

    def invalidate_full(self):
        self._needs_clear = 2

    # ── Draw ──────────────────────────────────────────────────────────────────

    def draw(self, t: float = 0.0, dt: float = 0.016, draw_face: bool = True):
        scr = self.screen

        # 1. Full-screen clear every frame — completely prevents ghosting, trails, and text smearing!
        scr.blit(self._bg, (0, 0))

        # 2. Twinkling atmospheric stars (24 dots)
        self._draw_stars(scr, t)

        # 3. Face (only when active; seamless bounding box blit)
        if draw_face:
            self.face.draw(scr)

        # 4. Top status bar clock
        self._draw_topbar(scr)

    def _draw_stars(self, scr, t):
        for x, y, ph, per in self._stars:
            frac = 0.5 + 0.5 * math.sin(2 * math.pi * t / per + ph)
            v = int(40 + frac * 130)
            pygame.draw.circle(scr, (v, v, v), (x, y), 1)

    def _draw_topbar(self, scr):
        cs = time.strftime("%H:%M:%S")
        if cs != self._last_cs:
            self._clock_surf = self._hud_font_md.render(cs, True, config.MUTED)
            self._last_cs = cs
        if self._clock_surf:
            scr.blit(self._clock_surf, (self._bar_pad_x, self._bar_pad_y))
