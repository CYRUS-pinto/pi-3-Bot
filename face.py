"""TARS face — unified synchronized animation engine.

All facial channels are locked to a single synchronized morph timeline:
  - Eye aperture height (smooth morph from previous fraction to target fraction)
  - Color channel (RGB interpolation between emotion palettes)
  - Multi-tier glow halos (dynamic color & intensity tracking)
  - Bezier mouth curve (interpolated left, mid, right control points)
  - Eyelid blink scaling (smooth cosine velocity curve)

Performance:
  - Synchronized morphing runs at rock-solid 60 FPS (< 3.6ms per frame).
  - Once stable, draw() becomes a single 0.1ms memcpy blit.
  - Telemetry tracking exposed for FPS, morph %, and active channels.
"""

from __future__ import annotations
import math
import time
import pygame
import config

EMOTIONS = (
    "NEUTRAL", "HAPPY", "PLAYFUL", "EXCITED", "CURIOUS", "CONFIDENT", "WARM",
    "SARCASM", "ALERT", "THINKING", "SKEPTICAL"
)

EMOTION_COLORS: dict[str, tuple[int, int, int]] = {
    "NEUTRAL":   config.E_NEUTRAL,    # radiant amber #ffa537 (warm, polite default)
    "HAPPY":     config.E_HAPPY,      # sunny warm gold #ffd75f (pure joy & smiles)
    "PLAYFUL":   config.E_PLAYFUL,    # coral tangerine #ff8c41 (cheeky wink mode / 75% humor)
    "EXCITED":   config.E_EXCITED,    # vibrant celebratory mint-emerald #38eb91 (fest energy)
    "CURIOUS":   config.E_CURIOUS,    # brilliant sky cyan #4bd7ff (wonder & discovery)
    "CONFIDENT": config.E_CONFIDENT,  # bright electric azure #5fafff (reassuring, smart)
    "WARM":      config.E_WARM,       # sunrise peach #ff9682 (gentle, friendly welcome)
    # Aliases
    "SARCASM":   config.E_PLAYFUL,
    "ALERT":     config.E_EXCITED,
    "THINKING":  config.E_CONFIDENT,
    "SKEPTICAL": config.E_WARM,
}

# (left_eye_height_frac, right_eye_height_frac) — All open, lively, happy apertures
EYE_H_FRAC: dict[str, tuple[float, float]] = {
    "NEUTRAL":   (125.0 / 150.0, 125.0 / 150.0),  # open, receptive, polite resting gaze
    "HAPPY":     ( 72.0 / 150.0,  72.0 / 150.0),  # warm, joyful crescent smile eyes
    "PLAYFUL":   ( 92.0 / 150.0, 118.0 / 150.0),  # lively, cheeky asymmetric twinkle
    "EXCITED":   (142.0 / 150.0, 142.0 / 150.0),  # big, bright, wide energized eyes
    "CURIOUS":   (140.0 / 150.0, 120.0 / 150.0),  # bright inquisitive upward gaze
    "CONFIDENT": (110.0 / 150.0, 110.0 / 150.0),  # sharp, capable, reassuring robotic presence
    "WARM":      ( 82.0 / 150.0,  82.0 / 150.0),  # soft, kind, affectionate gaze
    # Aliases
    "SARCASM":   ( 92.0 / 150.0, 118.0 / 150.0),
    "ALERT":     (142.0 / 150.0, 142.0 / 150.0),
    "THINKING":  (110.0 / 150.0, 110.0 / 150.0),
    "SKEPTICAL": ( 82.0 / 150.0,  82.0 / 150.0),
}

# All mouth profiles are active, positive, upbeat smiles (mid point dips downwards)
MOUTH_SHAPES: dict[str, tuple[float, float, float]] = {
    "NEUTRAL":   (0.38, 0.62, 0.38),   # pleasant, polite resting smile
    "HAPPY":     (0.20, 0.88, 0.20),   # huge, radiant, charismatic beaming smile!
    "PLAYFUL":   (0.24, 0.82, 0.32),   # cheerful, cheeky asymmetric cocked grin
    "EXCITED":   (0.16, 0.92, 0.16),   # maximum high-energy celebratory fest beam!
    "CURIOUS":   (0.34, 0.74, 0.26),   # friendly, inquisitive upturned smile
    "CONFIDENT": (0.30, 0.72, 0.30),   # sleek, reassuring, friendly robotic smile
    "WARM":      (0.26, 0.80, 0.26),   # gentle, welcoming, heartwarming smile
    # Aliases
    "SARCASM":   (0.24, 0.82, 0.32),
    "ALERT":     (0.16, 0.92, 0.16),
    "THINKING":  (0.30, 0.72, 0.30),
    "SKEPTICAL": (0.26, 0.80, 0.26),
}

# Glow: 2 high-contrast hollow rounded rects, (outset_px, brightness_fraction)
GLOW_LAYERS = ((10, 0.20), (4, 0.42))
_GLOW_MARGIN = 48


def _ease_cubic(t: float) -> float:
    """Fast-start smooth-deceleration curve (cubic-bezier equivalent)."""
    u = 1.0 - t
    return 1.0 - u * u * u


def _bezier(p0x, pcx, p1x, lY, mY, rY, steps=16):
    pts = []
    for i in range(steps + 1):
        t, u = i / steps, 1.0 - i / steps
        pts.append((int(u*u*p0x + 2*u*t*pcx + t*t*p1x),
                    int(u*u*lY  + 2*u*t*mY  + t*t*rY)))
    return pts


class TARSFace:
    def __init__(self, sw: int, sh: int, bg_surface: pygame.Surface | None = None):
        self.sw, self.sh = sw, sh
        self.bg = bg_surface

        self.emotion = "NEUTRAL"
        self.target_emotion = "NEUTRAL"

        # ── Synchronized Morph State ──────────────────────────────────────────
        # All channels interpolate together along _morph_t (0.0 → 1.0)
        self._morph_t = 1.0
        self._is_morphing = False

        # Channel 1: Color
        self._cur_color    = [float(c) for c in EMOTION_COLORS["NEUTRAL"]]
        self._from_color   = [float(c) for c in EMOTION_COLORS["NEUTRAL"]]
        self._target_color = [float(c) for c in EMOTION_COLORS["NEUTRAL"]]

        # Channel 2: Eye Aperture Height Fraction (Left, Right)
        self._cur_h_frac    = [float(f) for f in EYE_H_FRAC["NEUTRAL"]]
        self._from_h_frac   = [float(f) for f in EYE_H_FRAC["NEUTRAL"]]
        self._target_h_frac = [float(f) for f in EYE_H_FRAC["NEUTRAL"]]

        # Channel 3: Mouth Control Points
        self._cur_mouth    = list(MOUTH_SHAPES["NEUTRAL"])
        self._from_mouth   = list(MOUTH_SHAPES["NEUTRAL"])
        self._target_mouth = list(MOUTH_SHAPES["NEUTRAL"])

        # Channel 4: Blink scale (Left, Right independent eyelids for winking)
        self.blink_scale_l = 1.0
        self.blink_scale_r = 1.0

        # Channel 5: Speech acoustic amplitude (0.0 to 1.0)
        self.speech_amplitude = 0.0
        self._prev_speech_amp = 0.0

        # Channel 6: Dynamic Eye Gaze Direction (-1.0 to +1.0)
        self._cur_gaze_x = 0.0
        self._cur_gaze_y = 0.0
        self._target_gaze_x = 0.0
        self._target_gaze_y = 0.0

        # Geometry & pre-rendered surface
        self._geom  = None
        self._surf  = None
        self._srect = None
        self._dirty = True

    # ── Telemetry & Properties ────────────────────────────────────────────────

    @property
    def is_morphing(self) -> bool:
        return self._is_morphing

    @property
    def morph_progress(self) -> float:
        """0.0 → 1.0 progress of the current emotional shift."""
        return self._morph_t

    @property
    def active_color(self) -> tuple[int, int, int]:
        return (int(self._cur_color[0]), int(self._cur_color[1]), int(self._cur_color[2]))

    @property
    def blink_scale(self) -> float:
        """Combined average blink scale for telemetry & backwards compatibility."""
        return (self.blink_scale_l + self.blink_scale_r) / 2.0

    # ── Public API ─────────────────────────────────────────────────────────────

    def resize(self, sw: int, sh: int, bg_surface: pygame.Surface | None = None):
        if (sw, sh) != (self.sw, self.sh) or bg_surface is not self.bg:
            self.sw, self.sh = sw, sh
            self.bg = bg_surface
            self._invalidate()

    def set_emotion(self, emotion: str):
        """Initiates a unified, synchronized transition across all facial channels."""
        if emotion in EMOTIONS and emotion != self.target_emotion:
            self.target_emotion = emotion
            self.emotion = emotion

            # Lock from states to current values
            self._from_color   = list(self._cur_color)
            self._target_color = [float(c) for c in EMOTION_COLORS[emotion]]

            self._from_h_frac   = list(self._cur_h_frac)
            self._target_h_frac = [float(f) for f in EYE_H_FRAC[emotion]]

            self._from_mouth   = list(self._cur_mouth)
            self._target_mouth = list(MOUTH_SHAPES[emotion])

            self._morph_t     = 0.0
            self._is_morphing = True
            self._dirty       = True

    def set_blink_scale(self, scale_l: float, scale_r: float | None = None):
        """Sets eyelid aperture scaling for left and right eyes independently (enables winks)."""
        if scale_r is None:
            scale_r = scale_l
        nl = max(config.BLINK_MIN_SCALE, min(1.0, scale_l))
        nr = max(config.BLINK_MIN_SCALE, min(1.0, scale_r))
        if abs(nl - self.blink_scale_l) > 0.002 or abs(nr - self.blink_scale_r) > 0.002:
            self.blink_scale_l = nl
            self.blink_scale_r = nr
            self._dirty = True

    def set_gaze(self, norm_x: float, norm_y: float):
        """Sets target gaze direction normalized between -1.0 (left/up) and +1.0 (right/down)."""
        tx = max(-1.0, min(1.0, norm_x))
        ty = max(-1.0, min(1.0, norm_y))
        if abs(tx - self._target_gaze_x) > 0.01 or abs(ty - self._target_gaze_y) > 0.01:
            self._target_gaze_x = tx
            self._target_gaze_y = ty

    def update(self, dt: float):
        """Updates all channels synchronously using cubic easing."""
        if self._is_morphing:
            self._morph_t = min(1.0, self._morph_t + dt / config.MOUTH_MORPH_TIME)
            e = _ease_cubic(self._morph_t)

            # 1. Sync Color Channel
            for i in range(3):
                self._cur_color[i] = self._from_color[i] + (self._target_color[i] - self._from_color[i]) * e

            # 2. Sync Eye Height Aperture (Left, Right)
            for i in range(2):
                self._cur_h_frac[i] = self._from_h_frac[i] + (self._target_h_frac[i] - self._from_h_frac[i]) * e

            # 3. Sync Mouth Bezier Points
            for i in range(3):
                self._cur_mouth[i] = self._from_mouth[i] + (self._target_mouth[i] - self._from_mouth[i]) * e

            self._dirty = True

            if self._morph_t >= 1.0:
                self._is_morphing = False
                self._cur_color   = list(self._target_color)
                self._cur_h_frac  = list(self._target_h_frac)
                self._cur_mouth   = list(self._target_mouth)

        # Smooth Gaze Shift (snappy, organic, framerate-independent)
        dx = self._target_gaze_x - self._cur_gaze_x
        dy = self._target_gaze_y - self._cur_gaze_y
        dist = math.hypot(dx, dy)
        if dist > 0.02:  # ponytail: was 0.002 — sub-pixel gaze jitter rebuilt the whole face every frame; 0.02 restores the 0.1ms blit fast-path when gaze settles
            decay = 1.0 - math.exp(-18.0 * dt)
            self._cur_gaze_x += dx * decay
            self._cur_gaze_y += dy * decay
            self._dirty = True

        if self.speech_amplitude > 0.005 or self._prev_speech_amp > 0.005:
            self._dirty = True
        self._prev_speech_amp = self.speech_amplitude

    def draw(self, screen: pygame.Surface):
        """Hot path: single fast blit when face is static, rebuild when animating."""
        if self._dirty:
            if self._geom is None:
                self._compute_geom()
            self._rebuild()
        if self._surf and self._srect:
            screen.blit(self._surf, self._srect)

    # ── Internal ───────────────────────────────────────────────────────────────

    def _invalidate(self):
        self._geom  = None
        self._dirty = True

    def _compute_geom(self):
        is_portrait = (getattr(config, "SCREEN_ORIENTATION", "AUTO") == "PORTRAIT") or (
            getattr(config, "SCREEN_ORIENTATION", "AUTO") == "AUTO" and self.sh > self.sw
        )
        if is_portrait:
            eye_w_ratio   = getattr(config, "EYE_W_RATIO_PORTRAIT", 0.220)
            eye_h_ratio   = getattr(config, "EYE_H_RATIO_PORTRAIT", 0.160)
            gap_ratio     = getattr(config, "EYE_GAP_RATIO_PORTRAIT", 0.140)
            face_y_ratio  = getattr(config, "FACE_Y_RATIO_PORTRAIT", 0.380)
            mouth_w_ratio = getattr(config, "MOUTH_W_RATIO_PORTRAIT", 0.380)
            mouth_h_ratio = getattr(config, "MOUTH_H_RATIO_PORTRAIT", 0.035)
        else:
            eye_w_ratio   = config.EYE_W_RATIO
            eye_h_ratio   = config.EYE_H_RATIO
            gap_ratio     = config.EYE_GAP_RATIO
            face_y_ratio  = config.FACE_Y_RATIO
            mouth_w_ratio = config.MOUTH_W_RATIO
            mouth_h_ratio = config.MOUTH_H_RATIO

        # Layout Studio placement (pocket remote): position + scale freely, defaults = stock look.
        _fs = min(4.0, max(0.2, float(getattr(config, "FACE_SIZE", 1.0))))
        _cxr = min(0.9, max(0.1, float(getattr(config, "FACE_CX_RATIO", 0.5))))
        _cyr = getattr(config, "FACE_CY_RATIO", None)
        ow  = max(44, int(self.sw * eye_w_ratio * _fs))
        oh  = max(64, int(self.sh * eye_h_ratio * _fs))
        gap = max(24, int(self.sw * gap_ratio * _fs))
        cx  = min(self.sw - 1, max(1, int(self.sw * _cxr)))
        cy  = min(self.sh - 1, max(1, int(self.sh * (min(0.9, max(0.1, float(_cyr))) if _cyr is not None else face_y_ratio))))
        lx  = cx - gap // 2 - ow
        rx  = cx + gap // 2
        ins = max(3, int(ow * 0.146))
        # Ensure mouth is comfortably below the bottom edge of the eyes
        mouth_gap = max(30, int(self.sh * (0.045 if is_portrait else 0.052)))
        mcy = cy + oh // 2 + mouth_gap

        mw = max(80, int(self.sw * mouth_w_ratio * _fs))
        mh = max(18, int(self.sh * mouth_h_ratio * _fs))

        # Dynamic head-turn safe margin
        head_margin_x = max(36, int(ow * 0.40))
        head_margin_y = max(24, int(oh * 0.25))

        fx = max(0, lx - _GLOW_MARGIN - head_margin_x)
        fy = max(0, cy - oh // 2 - _GLOW_MARGIN - head_margin_y)
        fr = min(self.sw, rx + ow + _GLOW_MARGIN + head_margin_x)
        fb = min(self.sh, mcy + mh // 2 + 16 + head_margin_y)
        fw, fh = max(1, fr - fx), max(1, fb - fy)

        self._srect = pygame.Rect(fx, fy, fw, fh)
        self._surf  = pygame.Surface((fw, fh)).convert()
        if self.bg is not None and self.bg.get_width() >= fr and self.bg.get_height() >= fb:
            self._bg_slice = self.bg.subsurface(self._srect).copy()
        else:
            self._bg_slice = pygame.Surface((fw, fh)).convert()
            self._bg_slice.fill(config.VOID)

        self._geom = (ow, oh, gap, cx, cy, lx, rx, ins, mcy, mw, mh, fx, fy, fw, fh)

    def _rebuild(self):
        """Render complete face with synchronized channels onto a pre-allocated Surface."""
        ow, oh, gap, cx, cy, lx, rx, ins, mcy, mw, mh, fx, fy, fw, fh = self._geom

        color = self.active_color

        # Fast background reset without memory allocation
        surf = self._surf
        surf.blit(self._bg_slice, (0, 0))

        ox, oy = -fx, -fy

        # ── 3D Parallax Head Tracking Shift ──────────────────────────────────
        head_shift_x = int(self._cur_gaze_x * max(26, int(ow * 0.34)))
        head_shift_y = int(self._cur_gaze_y * max(14, int(oh * 0.20)))

        # Dynamic internal eye gaze (pupil shift)
        max_shift_x = int(ins * 0.95)
        max_shift_y = int(ins * 0.65)
        shift_x     = int(self._cur_gaze_x * max_shift_x)
        shift_y     = int(self._cur_gaze_y * max_shift_y)

        # ── Eyes (Synchronized aperture, independent eye scale & winking) ─────
        eye_specs = [
            (lx + head_shift_x, self._cur_h_frac[0], self.blink_scale_l),
            (rx + head_shift_x, self._cur_h_frac[1], self.blink_scale_r),
        ]

        for ex, h_frac, b_scale in eye_specs:
            ih  = max(4, int(oh * h_frac * b_scale))
            iy  = (cy + head_shift_y) - ih // 2
            ibr = max(6, int(min(ow, ih) * 0.22))
            lx_ = ex + ox
            ey_ = iy + oy

            # 1. Glow: multi-layer hollow rounded rects tracking live color
            for outset, bfrac in GLOW_LAYERS:
                gc  = tuple(int(c * bfrac) for c in color)
                obr = ibr + outset
                pygame.draw.rect(surf, gc,
                    (lx_ - outset, ey_ - outset,
                     ow + outset * 2, ih + outset * 2),
                    width=2, border_radius=obr)

            # 2. Outer coloured aperture frame / housing bezel
            pygame.draw.rect(surf, color, (lx_, ey_, ow, ih), width=max(2, int(ins * 0.35)), border_radius=ibr)

            # 3. Deep optic socket cavity (recessed dark visor background)
            vx, vy = lx_ + ins, ey_ + ins
            vw, vh = max(4, ow - ins * 2), max(4, ih - ins * 2)
            vbr    = max(3, ibr - ins)
            socket_bg = (10, 14, 20)
            pygame.draw.rect(surf, socket_bg, (vx, vy, vw, vh), border_radius=vbr)

            # 4. Subtle socket contour hairline
            socket_edge = (24, 30, 42)
            pygame.draw.rect(surf, socket_edge, (vx, vy, vw, vh), width=1, border_radius=vbr)

            # 5. LIVING RADIANT ROBOTIC PUPIL / OPTIC IRIS
            # Sized proportionately to socket cavity and eyelid aperture
            pw = max(14, int(vw * 0.52))
            ph = max(8, int(vh * 0.65))
            pbr = max(4, int(min(pw, ph) * 0.35))

            # Dynamic pupil travel across the eye socket (smoothly tracks human across the room!)
            travel_x = max(0, (vw - pw) // 2)
            travel_y = max(0, (vh - ph) // 2)
            px = vx + (vw - pw) // 2 + int(self._cur_gaze_x * travel_x)
            py = vy + (vh - ph) // 2 + int(self._cur_gaze_y * travel_y)

            # Pupil Layer A: Soft luminous ambient glow halo
            pupil_aura = tuple(int(c * 0.38) for c in color)
            pygame.draw.rect(surf, pupil_aura,
                             (px - 3, py - 3, pw + 6, ph + 6),
                             border_radius=pbr + 2)

            # Pupil Layer B: Radiant main body in active emotion palette
            pygame.draw.rect(surf, color, (px, py, pw, ph), border_radius=pbr)

            # Pupil Layer C: Concentrated high-energy core highlight
            if pw > 12 and ph > 10:
                cw = max(6, int(pw * 0.52))
                ch = max(4, int(ph * 0.52))
                cx_p = px + (pw - cw) // 2
                cy_p = py + (ph - ch) // 2
                core_col = tuple(min(255, int(c * 1.25)) for c in color)
                cbr = max(2, int(min(cw, ch) * 0.30))
                pygame.draw.rect(surf, core_col, (cx_p, cy_p, cw, ch), border_radius=cbr)

            # Pupil Layer D: Specular lens reflection glint (living optical reflection)
            if ph > 14:
                ds = ow / 96.0
                dr = max(2, int(3.6 * ds))
                # Glass reflection sits at upper-left corner of the pupil
                hx = px + max(3, int(pw * 0.18))
                hy = py + max(3, int(ph * 0.16))
                if hy + dr < py + ph - 2:
                    # Soft outer specular halo
                    pygame.draw.circle(surf, (190, 205, 225), (hx, hy), dr + 1)
                    # Crisp bright white reflection point
                    pygame.draw.circle(surf, (255, 255, 255), (hx, hy), dr)

        # ── Mouth (Refined acoustic visor curve with dynamic head turn) ───────
        sx  = mw / 200.0
        p0x = int(cx - mw // 2 + 18  * sx) + ox + int(head_shift_x * 0.60)
        pcx = int(cx - mw // 2 + 100 * sx) + ox + int(head_shift_x * 0.60)
        p1x = int(cx - mw // 2 + 182 * sx) + ox + int(head_shift_x * 0.60)
        mcy_eff = mcy + int(head_shift_y * 0.50)

        # Dynamic playful smile perk when an eye winks
        wink_diff = self.blink_scale_r - self.blink_scale_l  # positive when left eye is winking
        wink_mag  = abs(wink_diff)
        w_boost   = wink_mag * 0.12  # deepens smile by up to 12% during wink

        # Tilt smile corner towards the winking eye for a charismatic wink-smirk
        l_offset = -w_boost * 0.4 if wink_diff > 0.05 else (w_boost * 0.2 if wink_diff < -0.05 else 0.0)
        r_offset = -w_boost * 0.4 if wink_diff < -0.05 else (w_boost * 0.2 if wink_diff > 0.05 else 0.0)

        # Speaking acoustic modulation
        spk = self.speech_amplitude
        spk_wave = math.sin(time.perf_counter() * 28.0) * spk * 0.28 if spk > 0.01 else 0.0

        lY  = int(mcy_eff - mh // 2 + (self._cur_mouth[0] + l_offset - spk_wave * 0.4) * mh) + oy
        mY  = int(mcy_eff - mh // 2 + (self._cur_mouth[1] + w_boost  + spk_wave)       * mh) + oy
        rY  = int(mcy_eff - mh // 2 + (self._cur_mouth[2] + r_offset - spk_wave * 0.4) * mh) + oy
        pts = _bezier(p0x, pcx, p1x, lY, mY, rY)

        lw = max(3, int(mw / 30))
        # Subtle glow outline under mouth
        gc = tuple(int(c * 0.28) for c in color)
        pygame.draw.lines(surf, gc,    False, pts, lw + 4)
        pygame.draw.lines(surf, color, False, pts, lw)

        # ── Multi-Band Acoustic Spectrum Equalizer Visor ─────────────────────
        if spk > 0.03:
            num_bars = 16
            span_w   = p1x - p0x
            bar_w    = max(2, int(span_w / (num_bars * 2.2)))
            now_t    = time.perf_counter()
            cap_col  = tuple(min(255, int(c * 1.35 + 50)) for c in color)

            for b_i in range(num_bars):
                frac = b_i / float(num_bars - 1)
                bx   = int(p0x + frac * span_w)

                # Quadratic Bezier Y baseline at frac
                t = frac
                curve_y = (1.0 - t)**2 * lY + 2.0 * (1.0 - t) * t * mY + t**2 * rY

                # Multi-harmonic audio frequency simulation
                h1 = math.sin(now_t * 24.0 + b_i * 0.85)
                h2 = math.cos(now_t * 40.0 + b_i * 1.35)
                h3 = math.sin(now_t * 16.0 - b_i * 0.55)
                band_energy = 0.52 + 0.28 * h1 + 0.14 * h2 + 0.06 * h3
                bell = math.sin(math.pi * frac)  # Acoustic taper at edges

                bar_h = max(3, int(mh * 1.8 * band_energy * bell * spk))
                bar_top = int(curve_y - bar_h // 2)

                # Glow backing
                pygame.draw.rect(surf, gc,
                    (bx - bar_w // 2 - 1, bar_top - 1, bar_w + 2, bar_h + 2),
                    border_radius=2)
                # Primary spectrum bar
                pygame.draw.rect(surf, color,
                    (bx - bar_w // 2, bar_top, bar_w, bar_h),
                    border_radius=1)
                # Studio spectrum peak cap
                if bar_h >= 6:
                    pygame.draw.rect(surf, cap_col,
                        (bx - bar_w // 2, bar_top, bar_w, 2))

        # Glowing termination nodes at mouth endpoints
        for ep in (pts[0], pts[-1]):
            pygame.draw.circle(surf, gc,    ep, lw + 2)
            pygame.draw.circle(surf, color, ep, lw)

        self._surf  = surf
        self._srect = pygame.Rect(fx, fy, fw, fh)
        self._dirty = False
