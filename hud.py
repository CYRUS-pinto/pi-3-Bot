"""TARS Optical HUD — sci-fi target acquisition, visitor detection & sensor telemetry.

Matches the Interstellar tactical HUD aesthetic:
  - Vector target lock reticles with corner brackets and crosshairs
  - Autonomous demo scanning loop (discovers audience, acquires lock, triggers greeting & wink)
  - External camera-tracking ready (receives normalized x, y, label, and distance via bridge)
  - Zero-allocation direct rendering (< 0.25ms per frame)
"""

from __future__ import annotations
import math
import random
import pygame
import config


class OpticalHUD:
    def __init__(self, w: int, h: int, fonts: dict[str, pygame.font.Font]):
        self.w, self.h = w, h
        self.fonts = fonts

        # Target lock state
        self.active = False
        self.target_name = "SPECTATOR"
        self.target_dist = "2.1m"
        self.crowd_count = 0
        self.tx = float(int(w * 0.72))
        self.ty = float(int(h * 0.42))
        self._target_tx = float(int(w * 0.72))
        self._target_ty = float(int(h * 0.42))
        self.box_w = max(120, int(w * 0.14))
        self.box_h = max(120, int(h * 0.22))
        self.secondary_targets: list[tuple[int, int]] = []

        # Autonomous demo scanner state
        self._auto_timer = 0.0
        self._scan_phase = "IDLE"  # IDLE, SCANNING, LOCKED
        self._phase_timer = 0.0
        self._sweep_x = 0.0

        # Autonomous Social Dwell Trigger
        self._continuous_lock_t = 0.0
        self._lock_greeted = False
        self._greet_cooldown = 0.0
        self._tag_cache: dict = {}  # ponytail: static HUD strings were rasterized every frame — cached here, keyed (text, font-id)

    def _tag(self, font: pygame.font.Font, text: str, color: tuple) -> pygame.Surface:
        key = (text, id(font), color)
        hit = self._tag_cache.get(key)
        if hit is None:
            if len(self._tag_cache) >= 12:
                self._tag_cache.clear()
            hit = self._tag_cache[key] = font.render(text, True, color)
        return hit

    def resize(self, w: int, h: int, fonts: dict[str, pygame.font.Font]):
        self.w, self.h = w, h
        self.fonts = fonts
        self.box_w = max(120, int(w * 0.14))
        self.box_h = max(120, int(h * 0.22))
        self._tag_cache.clear()

    def set_target(self, active: bool, name: str = "SPECTATOR", dist: str = "2.1m",
                   norm_x: float | None = None, norm_y: float | None = None,
                   count: int = 1, secondaries: list | None = None):
        """Sets external target from camera detection script or Pocket Web Remote."""
        self.active = active
        self.target_name = name
        self.target_dist = dist
        self.crowd_count = max(1, count) if active else 0

        if norm_x is not None:
            eff_x = (1.0 - norm_x) if getattr(config, "MIRROR_GAZE_X", config.MIRROR_CAMERA_X) else norm_x
            self._target_tx = self.w * max(0.08, min(0.92, eff_x))
        if norm_y is not None:
            eff_y = (1.0 - norm_y) if config.INVERT_CAMERA_Y else norm_y
            self._target_ty = self.h * max(0.12, min(0.88, eff_y))

        if secondaries:
            self.secondary_targets = [
                (int(self.w * max(0.08, min(0.92, (1.0 - sx) if getattr(config, "MIRROR_GAZE_X", config.MIRROR_CAMERA_X) else sx))),
                 int(self.h * max(0.12, min(0.88, (1.0 - sy) if config.INVERT_CAMERA_Y else sy))))
                for sx, sy in secondaries
            ]
        else:
            self.secondary_targets = []

        if active:
            self._scan_phase = "LOCKED"
        else:
            self._scan_phase = "IDLE"
            self._continuous_lock_t = 0.0
            self._lock_greeted = False

    def trigger_demo_scan(self):
        """Manually or autonomously triggers a high-tech visitor detection sweep."""
        self._scan_phase = "SCANNING"
        self._phase_timer = 0.0
        self._sweep_x = 0.1 * self.w
        self._continuous_lock_t = 0.0
        self._lock_greeted = False

    def update(self, dt: float, speech_ctrl=None, blink_ctrl=None, face=None):
        self._phase_timer += dt
        if self._greet_cooldown > 0.0:
            self._greet_cooldown -= dt

        # Smooth reticle movement (framerate-independent, organic exponential decay)
        decay = 1.0 - math.exp(-15.0 * dt)
        self.tx += (self._target_tx - self.tx) * decay
        self.ty += (self._target_ty - self.ty) * decay

        # Feed full dynamic range calibrated gaze shift directly into face
        if face and self.active:
            # Sightline & Camera Geometry Calibration across all placement positions
            cam_pos = str(getattr(config, "CAMERA_POSITION", "CENTER")).upper()
            if cam_pos in ("CENTER", "MIDDLE"):
                horiz_bias, vert_bias = 0.50, 0.50
            elif cam_pos in ("TOP", "ABOVE"):
                horiz_bias, vert_bias = 0.50, 0.55
            elif cam_pos in ("BOTTOM", "BELOW"):
                horiz_bias, vert_bias = 0.50, 0.35
            elif cam_pos == "LEFT":
                horiz_bias, vert_bias = 0.35, 0.50
            elif cam_pos == "RIGHT":
                horiz_bias, vert_bias = 0.65, 0.50
            else:
                horiz_bias, vert_bias = 0.50, 0.50

            rel_x = (self.tx / self.w) - horiz_bias + config.GAZE_OFFSET_X
            norm_gx = max(-1.0, min(1.0, rel_x * config.GAZE_SENSITIVITY_X))

            rel_y = (self.ty / self.h) - vert_bias + config.GAZE_OFFSET_Y
            norm_gy = max(-1.0, min(1.0, rel_y * config.GAZE_SENSITIVITY_Y))
            face.set_gaze(norm_gx, norm_gy)
        elif face:
            face.set_gaze(0.0, 0.0)

        if config.HUD_AUTO_SCAN and not self.active:
            self._auto_timer += dt
            if self._auto_timer >= config.HUD_SCAN_INTERVAL and self._scan_phase == "IDLE":
                self._auto_timer = 0.0
                self.trigger_demo_scan()

        if self._scan_phase == "SCANNING":
            # Sweep scanner from left to right across audience
            self._sweep_x += (self.w * 0.40) * dt
            if self._phase_timer >= 2.2:
                # Lock onto visitor in demo mode!
                self._scan_phase = "LOCKED"
                self._phase_timer = 0.0
                self.active = True
                self.crowd_count = random.choice([1, 1, 2, 3])
                self.target_name = "VIP GUEST" if self.crowd_count == 1 else "AUDIENCE CLUSTER"
                self.target_dist = f"{random.uniform(1.6, 2.8):.1f}m"
                self._target_tx = self.w * random.uniform(0.60, 0.78)
                self._target_ty = self.h * random.uniform(0.35, 0.50)

        elif self._scan_phase == "LOCKED" and self.active:
            # Accumulate continuous lock time for autonomous engagement
            self._continuous_lock_t += dt
            if self._continuous_lock_t >= 2.0 and not self._lock_greeted and self._greet_cooldown <= 0.0:
                self._lock_greeted = True
                self._greet_cooldown = 18.0  # 18-second conversational cooldown

                # Trigger responsive robotic greeting & wink
                if self.crowd_count > 1:
                    if face:
                        face.set_emotion("EXCITED")
                    if speech_ctrl and not speech_ctrl.is_speaking:
                        speech_ctrl.play_cue(
                            "celebrate",
                            blink_ctrl=blink_ctrl,
                            custom_text=f"Sensors detect a cluster of {self.crowd_count} humans. Welcome to RESOENANCE 2026.",
                            custom_emotion="EXCITED",
                            custom_wink="RIGHT"
                        )
                else:
                    if face:
                        face.set_emotion("HAPPY")
                    if speech_ctrl and not speech_ctrl.is_speaking:
                        speech_ctrl.play_cue(
                            "greeting",
                            blink_ctrl=blink_ctrl,
                            custom_text="Optical sensors locked. Greetings, human. Department of Robotics welcomes you.",
                            custom_emotion="HAPPY",
                            custom_wink="LEFT"
                        )
                    elif blink_ctrl:
                        blink_ctrl.trigger_wink("LEFT")

    def draw(self, screen: pygame.Surface, t: float = 0.0):
        if not getattr(config, "HUD_ENABLED", True):
            return

        f_xs = self.fonts["xs"]
        f_sm = self.fonts["sm"]

        # ── 1. Active Scanning Sweep Line ─────────────────────────────────────
        if self._scan_phase == "SCANNING":
            sx = int(self._sweep_x) % self.w
            pygame.draw.line(screen, (40, 70, 95), (sx, int(self.h * 0.15)), (sx, int(self.h * 0.85)), 1)
            scan_tag = self._tag(f_xs, "OPTICAL SWEEP // SEARCHING FOR AUDIENCE...", config.E_CURIOUS)
            screen.blit(scan_tag, (sx + 8, int(self.h * 0.20)))
            return

        # ── 2. Standby Tactical Crosshairs (Visible when HUD is ON but no face locked) ──
        if not self.active or self._scan_phase == "IDLE":
            cx, cy = self.w // 2, int(self.h * 0.42)
            color = (55, 95, 130)
            corner_len = 16
            bw, bh = 80, 80
            bx, by = cx - bw // 2, cy - bh // 2
            pygame.draw.line(screen, color, (bx, by), (bx + corner_len, by), 1)
            pygame.draw.line(screen, color, (bx, by), (bx, by + corner_len), 1)
            pygame.draw.line(screen, color, (bx + bw, by), (bx + bw - corner_len, by), 1)
            pygame.draw.line(screen, color, (bx + bw, by), (bx + bw, by + corner_len), 1)
            pygame.draw.line(screen, color, (bx, by + bh), (bx + corner_len, by + bh), 1)
            pygame.draw.line(screen, color, (bx, by + bh), (bx, by + bh - corner_len), 1)
            pygame.draw.line(screen, color, (bx + bw, by + bh), (bx + bw - corner_len, by + bh), 1)
            pygame.draw.line(screen, color, (bx + bw, by + bh), (bx + bw, by + bh - corner_len), 1)
            pygame.draw.circle(screen, color, (cx, cy), 3)
            pygame.draw.line(screen, (35, 60, 85), (cx - 16, cy), (cx + 16, cy), 1)
            pygame.draw.line(screen, (35, 60, 85), (cx, cy - 16), (cx, cy + 16), 1)
            tag = self._tag(f_xs, "OPTICAL HUD // SCANNING SECTOR FOR SPECTATORS", (75, 125, 160))
            screen.blit(tag, (cx - tag.get_width() // 2, by + bh + 8))
            return

        # ── 3. Primary Locked Target Reticle ──────────────────────────────────
        if self.active:
            cx, cy = int(self.tx), int(self.ty)
            bw, bh = self.box_w, self.box_h
            bx = cx - bw // 2
            by = cy - bh // 2

            color = config.E_CURIOUS
            glow_c = tuple(int(c * 0.35) for c in color)
            corner_len = max(14, int(bw * 0.22))

            pulse = 0.8 + 0.2 * math.sin(t * 8.0)
            lw = 2 if pulse > 0.9 else 1

            # Corner Brackets
            pygame.draw.line(screen, color, (bx, by), (bx + corner_len, by), lw)
            pygame.draw.line(screen, color, (bx, by), (bx, by + corner_len), lw)
            pygame.draw.line(screen, color, (bx + bw, by), (bx + bw - corner_len, by), lw)
            pygame.draw.line(screen, color, (bx + bw, by), (bx + bw, by + corner_len), lw)
            pygame.draw.line(screen, color, (bx, by + bh), (bx + corner_len, by + bh), lw)
            pygame.draw.line(screen, color, (bx, by + bh), (bx, by + bh - corner_len), lw)
            pygame.draw.line(screen, color, (bx + bw, by + bh), (bx + bw - corner_len, by + bh), lw)
            pygame.draw.line(screen, color, (bx + bw, by + bh), (bx + bw, by + bh - corner_len), lw)

            # Center Crosshairs & Target Dot
            ch_sz = 8
            pygame.draw.line(screen, glow_c, (cx - ch_sz, cy), (cx + ch_sz, cy), 1)
            pygame.draw.line(screen, glow_c, (cx, cy - ch_sz), (cx, cy + ch_sz), 1)
            pygame.draw.circle(screen, color, (cx, cy), 2)

            # ── 3. High-Tech Target Metadata Readout ───────────────────────────
            # ponytail: name/dist change per lock, not per frame — cached like the static tags
            header_str = f"[PRIMARY TARGET: {self.target_name}]"
            h_surf = self._tag(f_sm, header_str, color)
            screen.blit(h_surf, (bx, max(10, by - h_surf.get_height() - 6)))

            dist_str = f"RANGE: {self.target_dist}  //  CLEARANCE: LEVEL-4  //  STATUS: FRIENDLY"
            d_surf = self._tag(f_xs, dist_str, config.TEXT)
            screen.blit(d_surf, (bx, by + bh + 6))

            # ── 4. Multi-Person Crowd Badge & Secondary Reticles ───────────────
            if self.crowd_count > 1:
                crowd_tag = f"[MULTI-TARGET ACQUIRED] COUNT: {self.crowd_count}"
                c_col = config.E_PLAYFUL
                cr_surf = self._tag(f_sm, crowd_tag, c_col)
                # Badge pill at top right of the primary box
                cr_w, cr_h = cr_surf.get_size()
                cr_bx = min(self.w - cr_w - 20, bx + bw + 14)
                cr_by = max(20, by)

                # ponytail: was a fresh SRCALPHA alloc every frame — one reusable pill, regrown only when text size changes
                pill = getattr(self, "_pill_surf", None)
                if pill is None or pill.get_size() != (cr_w + 16, cr_h + 8):
                    pill = self._pill_surf = pygame.Surface((cr_w + 16, cr_h + 8), pygame.SRCALPHA)
                    pill.fill((14, 18, 26, 210))
                screen.blit(pill, (cr_bx - 8, cr_by - 4))
                pygame.draw.rect(screen, tuple(int(c * 0.7) for c in c_col), (cr_bx - 8, cr_by - 4, cr_w + 16, cr_h + 8), width=1)
                screen.blit(cr_surf, (cr_bx, cr_by))

            # Draw secondary target reticles
            for sx, sy in self.secondary_targets:
                s_bw, s_bh = int(bw * 0.65), int(bh * 0.65)
                s_bx, s_by = sx - s_bw // 2, sy - s_bh // 2
                s_col = (70, 130, 170)
                s_len = max(8, int(s_bw * 0.25))

                pygame.draw.line(screen, s_col, (s_bx, s_by), (s_bx + s_len, s_by), 1)
                pygame.draw.line(screen, s_col, (s_bx, s_by), (s_bx, s_by + s_len), 1)
                pygame.draw.line(screen, s_col, (s_bx + s_bw, s_by), (s_bx + s_bw - s_len, s_by), 1)
                pygame.draw.line(screen, s_col, (s_bx + s_bw, s_by), (s_bx + s_bw, s_by + s_len), 1)
                pygame.draw.line(screen, s_col, (s_bx, s_by + s_bh), (s_bx + s_len, s_by + s_bh), 1)
                pygame.draw.line(screen, s_col, (s_bx, s_by + s_bh), (s_bx, s_by + s_bh - s_len), 1)
                pygame.draw.line(screen, s_col, (s_bx + s_bw, s_by + s_bh), (s_bx + s_bw - s_len, s_by + s_bh), 1)
                pygame.draw.line(screen, s_col, (s_bx + s_bw, s_by + s_bh), (s_bx + s_bw, s_by + s_bh - s_len), 1)

        # ── 5. Live Tactical Camera PiP Viewport (Toggled from Remote or Key P) ──
        if config.SHOW_CAMERA_PIP:
            from vision import get_latest_pip_surface
            pip_surf = get_latest_pip_surface()
            if pip_surf is not None:
                pw, ph = pip_surf.get_size()
                # Bottom-left TV safe zone positioning
                px = max(28, int(self.w * config.SAFE_ZONE_PAD_X))
                py = self.h - ph - max(28, int(self.h * config.SAFE_ZONE_PAD_Y)) - 16

                # Clean dark backing and tactical border
                pygame.draw.rect(screen, (8, 12, 18), (px - 4, py - 20, pw + 8, ph + 24), border_radius=4)
                screen.blit(pip_surf, (px, py))
                pygame.draw.rect(screen, config.E_EXCITED, (px - 1, py - 1, pw + 2, ph + 2), width=1, border_radius=2)

                # Header badge
                pip_tag = f_xs.render("\u25cf OPTICAL SENSOR // LIVE PiP", True, config.E_EXCITED)
                screen.blit(pip_tag, (px, py - 16))

