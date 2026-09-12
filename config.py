import os
import json
import time

def tlog(tag: str, msg: str):
    """Timestamped millisecond logging for precision latency & trajectory analysis."""
    now = time.time()
    ms = int((now % 1) * 1000)
    t_str = time.strftime("%H:%M:%S", time.localtime(now)) + f".{ms:03d}"
    try:
        print(f"[{t_str}] [{tag}] {msg}", flush=True)
    except UnicodeEncodeError:
        # ponytail: Windows consoles (cp1252) choke on →/° glyphs and would CRASH the bot mid-frame.
        print(f"[{t_str}] [{tag}] {msg}".encode("ascii", "replace").decode(), flush=True)


# ponytail: 720p, not 1080p. Benched on-Pi: SDL kmsdrm flip costs ~47ms/frame at 1080p
# (8.3MB shadow copy per present) vs ~19ms at 720p — the flip alone made 30fps impossible.
# The TV hardware-upscales to 1080p in silicon for free. 0 = follow desktop mode (legacy behavior).
WIDTH  = 1280
HEIGHT = 720
TARGET_FPS   = 30   # ponytail: 30fps cap — render bench is 11-13ms; 60fps doubles heat for frames the 8.7fps camera feed can't fill
WINDOW_TITLE = "TARS — Live Display"
FULLSCREEN       = True
USE_FRAMEBUFFER  = False
FRAMEBUFFER_DEVICE = "/dev/fb0"

# ── Palette (Exact match to HTML specification) ──────────────────────────────
VOID      = (3,   4,   5)       # deep warm void base
PANEL     = (10,  12,  15)      # panel fill
LINE      = (27,  30,  35)      # hairlines, dividers, subtle grid
TEXT      = (238, 236, 230)     # warm off-white
MUTED     = (125, 125, 132)     # secondary text
MUTED_DIM = (74,  74,  80)      # dim tags, inactive elements

# Emotion colors — Tuned for positive, warm, friendly aesthetics
E_NEUTRAL   = (255, 165,  55)     # radiant amber #ffa537 (warm, polite default)
E_HAPPY     = (255, 215,  95)     # sunny warm gold #ffd75f (pure joy & smiles)
E_PLAYFUL   = (255, 140,  65)     # coral tangerine #ff8c41 (cheeky, witty 75% humor)
E_EXCITED   = ( 56, 235, 145)     # vibrant celebratory mint-emerald #38eb91 (fest energy)
E_CURIOUS   = ( 75, 215, 255)     # brilliant sky cyan #4bd7ff (wonder & discovery)
E_CONFIDENT = ( 95, 175, 255)     # bright electric azure #5fafff (reassuring, smart)
E_WARM      = (255, 150, 130)     # sunrise peach #ff9682 (gentle, friendly welcome)

# Aliases for backwards compatibility
E_SARCASM   = E_PLAYFUL
E_ALERT     = E_EXCITED
E_THINKING  = E_CONFIDENT
E_SKEPTICAL = E_WARM

# ── Screen Orientation (Auto-detects or force "PORTRAIT" / "LANDSCAPE") ───────
SCREEN_ORIENTATION = "AUTO"  # ponytail: rig changes (horizontal Dell now, vertical later) — layouts branch per
# orientation everywhere; lock only if one form becomes permanent. Resize rebuilds are rare (R key).

# ── Face geometry — Landscape (Horizontal / TV / Laptop) ─────────────────────
EYE_W_RATIO   = 0.120    # eye width / screen width
EYE_H_RATIO   = 0.280    # eye outer container height / screen height
EYE_GAP_RATIO = 0.105    # gap between eyes / screen width
FACE_Y_RATIO  = 0.46     # vertical centre of face
MOUTH_W_RATIO = 0.180    # mouth width / screen width
MOUTH_H_RATIO = 0.055    # mouth height / screen height

# ── Face geometry — Portrait (Vertical / Kiosk / Phone / Rotated TV) ─────────
EYE_W_RATIO_PORTRAIT   = 0.220   # 22% of screen width (strong, broad eye presence)
EYE_H_RATIO_PORTRAIT   = 0.160   # 16% of screen height (crisp vertical height)
EYE_GAP_RATIO_PORTRAIT = 0.140   # 14% gap between eyes
FACE_Y_RATIO_PORTRAIT  = 0.440   # Centered placement — balanced between top header and bottom hint
MOUTH_W_RATIO_PORTRAIT = 0.380   # Wide, bold, friendly smile
MOUTH_H_RATIO_PORTRAIT = 0.035   # Proportional curve depth

# ── Timing ───────────────────────────────────────────────────────────────────
BLINK_MIN_GAP   = 2.0
BLINK_MAX_GAP   = 4.8
BLINK_DURATION  = 0.130
WINK_DURATION   = 0.200
BLINK_MIN_SCALE = 0.06

EMOTION_HOLD_TIME  = 2.6
EVENT_DISPLAY_TIME = 5.0
INTERSTITIAL_TIME  = 1.7
MOUTH_MORPH_TIME   = 0.60
EVENT_REVEAL_TIME  = 0.25

# Boot sequence timing
BOOT_LINE_INTERVAL = 0.32
BOOT_LINE_CHECK_DT = 0.20
BOOT_FILL_INTERVAL = 0.06
BOOT_FILL_STEP     = 6
BOOT_HIDE_DELAY    = 0.40
BOOT_FADE_DURATION = 0.65

STAR_COUNT = 24

# Monitor / Screen proportion settings (tuned for 13"–21" laptop/desktop monitors)
SAFE_ZONE_PAD_X     = 0.038    # 3.8% horizontal margin (comfortably inside standard bezels)
SAFE_ZONE_PAD_Y     = 0.038    # 3.8% vertical margin

# ── Phase 2: Decoupled Bridge, Web Remote & Audio Settings ────────────────────
BRIDGE_UDP_PORT     = 5005     # UDP command listener port
BRIDGE_SERIAL_PORT  = ""       # Optional serial device (e.g. "/dev/ttyUSB0" or "/dev/ttyACM0")
BRIDGE_SERIAL_BAUD  = 115200

WEB_REMOTE_ENABLED  = True     # Built-in smartphone web controller
WEB_REMOTE_PORT     = 8080     # Open http://<pi_ip>:8080 on your phone

GPIO_ENABLED        = True     # Built-in direct Pi GPIO button listener
GPIO_PIN_TALK       = 17       # Push button wired to pin 17 & GND
GPIO_PIN_WINK       = 27       # Push button wired to pin 27 & GND
GPIO_PIN_EVENT      = 22       # Push button wired to pin 22 & GND

AUDIO_ENABLED       = True
AUDIO_SAMPLE_RATE   = 48000    # 48000 Hz matches native HDMI standard on Sony Bravia TV
AUDIO_CHANNELS      = 2
AUDIO_BUFFER        = 1024

HUD_ENABLED         = True
HUD_AUTO_SCAN       = False    # Set False so real camera tracker has 100% authority (no fake demo targets)
HUD_SCAN_INTERVAL   = 14.0     # Time between autonomous scans
HUD_LOCK_DURATION   = 4.0      # How long a target lock reticle stays locked

# ── Layout Studio (pocket-remote placement: move/resize anything) ────────────
# Defaults reproduce the exact shipped look. Drag sliders in the web remote;
# values persist to calibration.json and apply live (face recomputes geometry).
FACE_CX_RATIO = 0.5    # face center X as screen fraction (0.1 - 0.9, clamped)
FACE_CY_RATIO = None   # face center Y fraction, or None = stock per-orientation ratio
FACE_SIZE     = 1.0    # face scale multiplier (0.2 - 4.0; past ~2.5 is mush, kept legal anyway)
PIP_POS       = "BR"   # PiP corner: TR, TL, BR, BL, or FREE (PIP_X/PIP_Y fractions)
PIP_SCALE     = 1.0    # PiP size multiplier (0.1 - 3.0; crash-guard only, not taste)
PIP_X         = 1.0    # FREE-mode anchor X fraction (0 - 1)
PIP_Y         = 1.0    # FREE-mode anchor Y fraction (0 - 1)
PIP_CROP      = [0.0, 0.0, 1.0, 1.0]  # camera crop fractions [x, y, w, h] — cut ceiling/floor out
SLIDE_ZOOM    = 1.0    # event-slide scale (0.3 shrink-letterbox … 2.0 punch-in crop; 1.0 = full-bleed)
SLIDE_X       = 0.5    # slide anchor X fraction in the letterbox (0 - 1)
SLIDE_Y       = 0.5    # slide anchor Y fraction in the letterbox (0 - 1)
# ── Vertical-slides column (portrait cards floating mid-screen on a landscape
# panel whose edges hide behind thermocol). Toggle from the pocket remote.
VSLIDE_MODE   = False  # portrait card column instead of fullscreen landscape card
VSLIDE_SCALE  = 0.9    # column height as screen-height fraction (0.3 - 1.0)
VSLIDE_X      = 0.5    # column anchor X fraction (0 - 1)
VSLIDE_Y      = 0.5    # column anchor Y fraction (0 - 1)
VSLIDE_FIT    = "FIT"  # FIT (contain, letterbox), STRETCH (exact box), FILL (cover, center-crop)
VSLIDE_ROT    = 0      # slide content rotation inside the box: 0, 90, 180, 270
SLIDE_TEXT    = {}     # pocket-remote text overrides: {index-str: {name, desc}}; length-capped at apply
SLIDE_TEXT_REV = 0     # bumped on every text edit so cached statics rebuild
GESTURE_HAND_SIZE = 1.0  # hand-size scale for every-user accuracy (kids ~0.6, adults ~1.0-1.3); remote slider
GESTURE_CONFIRM_N = 2    # ponytail accuracy 2026-09-12: was 1 — single-frame motion noise (sleeves, light flicker, flailing) fired slides alone. 2 costs ~1 AI frame (~65ms, invisible) and kills almost all ghosts.
FOAM_L = 0.0             # visible-screen margins as fractions (thermocol eats edges; FIT VISIBLE uses these)
FOAM_T = 0.0
FOAM_R = 0.0
FOAM_B = 0.0
# ── Rig viewport: the WHOLE UI renders inside this window (landscape path).
# ponytail: one window beats per-element foam-dodging. Measure foam once, FIT once,
# everything (face, slides, PiP) lives visible forever. Persists across boots.
# (0,0,1,1) = fullscreen = zero behavior change.
VIEW_X = 0.0
VIEW_Y = 0.0
VIEW_W = 1.0
VIEW_H = 1.0

# ── Calibration & Live Sightline Controls ────────────────────────────────────
MIRROR_GAZE_X         = False    # Invert horizontal eye gaze tracking (toggle if robot eye looks opposite to you)
MIRROR_GESTURE_X      = True     # Match user perspective (Hand to TV Right -> Next Slide)
MIRROR_CAMERA_X       = False    # Legacy alias for MIRROR_GAZE_X
INVERT_CAMERA_Y       = False    # Invert vertical eye gaze tracking
# Camera Placement Options:
#   "CENTER" or "MIDDLE" : Camera positioned in the middle of display/bezel (Direct 1:1 sightline) [DEFAULT]
#   "TOP" or "ABOVE"     : Camera placed on top bezel of TV/monitor (looking down into screen)
#   "BOTTOM" or "BELOW"  : Camera placed below TV/monitor (looking up into screen)
#   "LEFT"               : Camera mounted on left side of display
#   "RIGHT"              : Camera mounted on right side of display
CAMERA_POSITION       = "CENTER" 
MONITOR_DIAG_INCHES   = 43       # Monitor diagonal size in inches (24, 32, 43, 55, 65)
COUCH_DIST_METERS     = 2.2      # Distance to viewer in meters
GAZE_SENSITIVITY_X    = 2.4      # Horizontal gaze sensitivity multiplier
GAZE_SENSITIVITY_Y    = 2.2      # Vertical gaze sensitivity multiplier
GAZE_OFFSET_X         = 0.0      # Horizontal trim offset (-0.5 to +0.5)
GAZE_OFFSET_Y         = 0.0      # Vertical trim offset (-0.5 to +0.5)
GESTURE_SWIPE_ENABLED = True     # Optical hand gesture recognition (Left/Right to swipe slides, Up/Down to toggle Face)
GESTURE_SWIPE_SENSITIVITY = 1.0   # Master swipe distance threshold scale (0.5x=wide sweep, 2.5x=micro sweep)
GESTURE_SENS_LEFT         = 1.0   # Directional multiplier for Swipe Left (0.3x to 3.0x)
GESTURE_SENS_RIGHT        = 1.0   # Directional multiplier for Swipe Right (0.3x to 3.0x)
GESTURE_SENS_UP           = 1.0   # Directional multiplier for Swipe Up (0.3x to 3.0x)
GESTURE_SENS_DOWN         = 1.0   # Directional multiplier for Swipe Down (0.3x to 3.0x)
GESTURE_COOLDOWN_SEC  = 0.45     # ponytail 2026-09-12: was 1.00 — a full second of wiped history ate every rapid 2nd flick (felt "delayed/queued"). 0.45 = same-stroke echo guard + real-time paging.
GESTURE_REFRACTORY_SEC = 0.35    # Absolute min gap after a fire: history wiped, nothing evaluates. Covers multi-frame re-fire of the same stroke.
GESTURE_SAME_DIR_SEC = 0.45      # Same-direction repeat needs this gap (rapid next-next-next paging). Opposite stays locked longer (return stroke), axis switch only waits refractory.
GESTURE_REBOUND_LOCKOUT_SEC = 0.70 # Return-stroke guard: opposite direction suppressed this long after a fire (fast yank-backs take ~0.3-0.5s). Hand-drop-to-rest still clears early (vision.py drop-reset).
GESTURE_DROP_RESET_Y  = 0.72     # Hand dropped below upper abdomen & settled clears rebound lockout (min 0.6s post-swipe)
GESTURE_DEBUG_LOGS    = False    # ponytail: off by default — terminal spam costs CPU over SSH; enable via calibration only when tuning
SWIPE_ANIMATION_ENABLED = True   # Visual scan-line sweep animation on TV when swipe occurs

# ── TV Screen Display & Slide Controls ───────────────────────────────────────
SHOW_CAMERA_PIP       = False    # Display live camera tracking video box on TV
FORCE_DISPLAY_MODE    = "AUTO"   # "AUTO" (rotates between face and slides), "FACE", "SLIDES"
ACTIVE_SLIDE_INDEX    = 0        # Current slide index (0 to 5)
SHOW_DIAGNOSTICS      = False    # Diagnostic performance graph (blue box at bottom-right)
SHOW_GESTURE_BANNER   = True     # ponytail: non-tech users need the green SWIPE feedback or they assume the bot is dead
AI_TARGET_FPS         = 22.0     # Target AI face tracking FPS (prevents CPU thermal throttling)
THERMAL_THROTTLE_LIMIT_C = 60.0  # ponytail: was 70 — Pi already reports 0x80008 (firmware soft-throttling, CPU capped UNDER us) at 69°C. Govern at 60, before firmware caps the CPU and the lag death-spiral starts
EVENT_DISPLAY_TIME    = 8.0      # Seconds each event slide is displayed during auto-cycle
AUTO_CYCLE_ENABLED    = True     # True: slides advance automatically, False: manual hold
GESTURE_MODE          = "4_WAY"  # "4_WAY" (L/R: slides, UP: face, DN: slides) or "HORIZONTAL_SWIPE"
GESTURE_SWIPE_DISTANCE = 0.18    # Min 18% horizontal frame sweep across virtual screen
GESTURE_VERTICAL_DISTANCE = 0.12  # Min 12% vertical frame sweep for UP/DOWN (relaxed for natural gestures)
GESTURE_WALK_LOCKOUT_SPEED = 0.12 # Antigravity-validated (walkthrough synthetic suite passed): zone-sweep engine structurally rejects flutter, so lockout stays tight; was 0.07 (strangled swipes, see tars.log)
GESTURE_WALK_DEBOUNCE_SEC  = 1.20 # ponytail: was 2.50 — 2.5s dead zone after every glance felt broken to non-tech users
GESTURE_MAX_HAND_AREA      = 0.095# Maximum contour area fraction (0.095 = 9.5% of frame; allows extended arm)
GESTURE_HAND_MIN_Y     = 0.10     # Interaction elevation ceiling (ignores ceiling light/fan motion)
GESTURE_HAND_MAX_Y     = 0.80     # Interaction elevation floor (0.80 allows chest & mid-torso hand swipes)
GESTURE_HOLD_ENABLED   = True     # Hold-zone gestures: dwell hand in LEFT/RIGHT/TOP zone (highest accuracy — position + stillness, no shape reading)
GESTURE_HOLD_SEC       = 0.8      # Dwell time to fire a hold (seconds). No key-repeat: must leave zone before refire
GESTURE_HOLD_MAX_SPEED = 0.25     # Must be this still to count as holding (a swipe passing through is faster — naturally exclusive)
GESTURE_TWOHAND_ENABLED = True    # Two-hand command: both hands up/visible together (the deliberate "everybody look" pose)
GESTURE_TWOHAND_SEC    = 1.0      # How long both hands must be present together to fire (maps to slides overview)
# Screen Physical Rotation:
#   0   : Standard Landscape / Native
#   90  : Vertical / Portrait (Clockwise)
#   180 : Inverted / Upside-down (Common when HDMI/power cord orientation is flipped)
#   270 : Vertical / Portrait (Counter-Clockwise)
SCREEN_ROTATION       = 0
# Camera Physical Rotation:
#   0   : Standard right-side up
#   90  : Phone mounted vertically (Clockwise)
#   180 : Phone mounted upside down (Cable port on top)
#   270 : Phone mounted vertically (Counter-Clockwise)
CAMERA_ROTATION       = 0

# ── Autonomous Low-Power Sleep & Instant Wake ───────────────────────────────
LOW_POWER_ENABLED     = True    # Drop AI rate and dim face when no one is around
LOW_POWER_TIMEOUT     = 8.0     # Seconds of no face/spectator detected before sleep
LOW_POWER_AI_FPS      = 4.0     # Standby AI poll rate (slashes Pi CPU by >80%)
LOW_POWER_RENDER_FPS  = 20      # Thermal-cooling render rate (nobody overheats, nobody notices)
IDLE_RENDER_FPS       = 8       # ponytail: empty-room standby — log proved 20fps of an unwatched screen still climbs to 72°C. 8fps breathing, instant wake to 30 on presence.

# Servo hardware retired (no arduinos/servos on this build). Revive: git checkout HEAD~ -- arduino.py arduino/
SERVO_PAN_PIN         = 9       # Physical eye pan / head turn servo
SERVO_TILT_PIN        = 10      # Physical eye tilt / nod servo
SERVO_ARM_L_PIN       = 5       # Left mechanical arm servo
SERVO_ARM_R_PIN       = 6       # Right mechanical arm servo

# ── Persistent Storage of Calibration ────────────────────────────────────────
CALIBRATION_FILE = os.path.join(os.path.dirname(__file__), "calibration.json")


def load_calibration():
    global MIRROR_GAZE_X, MIRROR_GESTURE_X, MIRROR_CAMERA_X, INVERT_CAMERA_Y, CAMERA_POSITION, MONITOR_DIAG_INCHES
    global COUCH_DIST_METERS, GAZE_SENSITIVITY_X, GAZE_SENSITIVITY_Y, GAZE_OFFSET_X, GAZE_OFFSET_Y
    global SHOW_CAMERA_PIP, SHOW_DIAGNOSTICS, HUD_ENABLED, GESTURE_SWIPE_ENABLED, GESTURE_SWIPE_SENSITIVITY
    global GESTURE_HOLD_ENABLED, GESTURE_TWOHAND_ENABLED
    global GESTURE_SENS_LEFT, GESTURE_SENS_RIGHT, GESTURE_SENS_UP, GESTURE_SENS_DOWN
    global SCREEN_ROTATION, CAMERA_ROTATION, SHOW_GESTURE_BANNER
    global EVENT_DISPLAY_TIME, AUTO_CYCLE_ENABLED, GESTURE_MODE
    global GESTURE_WALK_LOCKOUT_SPEED, GESTURE_WALK_DEBOUNCE_SEC
    global SWIPE_ANIMATION_ENABLED, GESTURE_COOLDOWN_SEC
    global FACE_CX_RATIO, FACE_CY_RATIO, FACE_SIZE, PIP_POS, PIP_SCALE
    global PIP_X, PIP_Y, PIP_CROP, SLIDE_ZOOM, SLIDE_X, SLIDE_Y
    global VSLIDE_MODE, VSLIDE_SCALE, VSLIDE_X, VSLIDE_Y, VSLIDE_FIT, VSLIDE_ROT
    global SLIDE_TEXT, SLIDE_TEXT_REV, GESTURE_HAND_SIZE, GESTURE_CONFIRM_N
    global GESTURE_REFRACTORY_SEC, GESTURE_SAME_DIR_SEC
    global FOAM_L, FOAM_T, FOAM_R, FOAM_B, VIEW_X, VIEW_Y, VIEW_W, VIEW_H
    if os.path.exists(CALIBRATION_FILE):
        try:
            with open(CALIBRATION_FILE, "r") as f:
                data = json.load(f)
            MIRROR_GAZE_X = bool(data.get("mirror_gaze_x", data.get("mirror_x", MIRROR_GAZE_X)))
            MIRROR_GESTURE_X = bool(data.get("mirror_gesture_x", MIRROR_GESTURE_X))
            MIRROR_CAMERA_X = MIRROR_GAZE_X
            INVERT_CAMERA_Y = bool(data.get("invert_y", INVERT_CAMERA_Y))
            CAMERA_POSITION = str(data.get("camera_position", CAMERA_POSITION)).upper()
            MONITOR_DIAG_INCHES = float(data.get("monitor_diag", MONITOR_DIAG_INCHES))
            COUCH_DIST_METERS = float(data.get("couch_dist", COUCH_DIST_METERS))
            GAZE_SENSITIVITY_X = float(data.get("sens_x", GAZE_SENSITIVITY_X))
            GAZE_SENSITIVITY_Y = float(data.get("sens_y", GAZE_SENSITIVITY_Y))
            GAZE_OFFSET_X = float(data.get("offset_x", GAZE_OFFSET_X))
            GAZE_OFFSET_Y = float(data.get("offset_y", GAZE_OFFSET_Y))
            SHOW_CAMERA_PIP = bool(data.get("show_pip", SHOW_CAMERA_PIP))
            SHOW_DIAGNOSTICS = bool(data.get("show_diagnostics", SHOW_DIAGNOSTICS))
            HUD_ENABLED = bool(data.get("hud_enabled", HUD_ENABLED))
            GESTURE_SWIPE_ENABLED = bool(data.get("gesture_swipe_enabled", GESTURE_SWIPE_ENABLED))
            GESTURE_HOLD_ENABLED = bool(data.get("gesture_hold_enabled", GESTURE_HOLD_ENABLED))
            GESTURE_TWOHAND_ENABLED = bool(data.get("gesture_twohand_enabled", GESTURE_TWOHAND_ENABLED))
            GESTURE_SWIPE_SENSITIVITY = float(data.get("gesture_sens", GESTURE_SWIPE_SENSITIVITY))
            GESTURE_SENS_LEFT = float(data.get("sens_left", GESTURE_SENS_LEFT))
            GESTURE_SENS_RIGHT = float(data.get("sens_right", GESTURE_SENS_RIGHT))
            GESTURE_SENS_UP = float(data.get("sens_up", GESTURE_SENS_UP))
            GESTURE_SENS_DOWN = float(data.get("sens_down", GESTURE_SENS_DOWN))
            GESTURE_COOLDOWN_SEC = float(data.get("gesture_cooldown_sec", data.get("gesture_cooldown", GESTURE_COOLDOWN_SEC)))
            GESTURE_REFRACTORY_SEC = float(data.get("gesture_refractory_sec", GESTURE_REFRACTORY_SEC))
            GESTURE_SAME_DIR_SEC = float(data.get("gesture_same_dir_sec", GESTURE_SAME_DIR_SEC))
            SWIPE_ANIMATION_ENABLED = bool(data.get("swipe_animation_enabled", data.get("swipe_anim_enabled", SWIPE_ANIMATION_ENABLED)))
            SCREEN_ROTATION = int(data.get("screen_rotation", SCREEN_ROTATION))
            CAMERA_ROTATION = int(data.get("camera_rotation", CAMERA_ROTATION))
            SHOW_GESTURE_BANNER = bool(data.get("show_gesture_banner", SHOW_GESTURE_BANNER))
            EVENT_DISPLAY_TIME = float(data.get("event_display_time", EVENT_DISPLAY_TIME))
            AUTO_CYCLE_ENABLED = bool(data.get("auto_cycle_enabled", AUTO_CYCLE_ENABLED))
            GESTURE_MODE = str(data.get("gesture_mode", GESTURE_MODE))
            GESTURE_WALK_LOCKOUT_SPEED = float(data.get("gesture_walk_lockout_speed", GESTURE_WALK_LOCKOUT_SPEED))
            GESTURE_WALK_DEBOUNCE_SEC = float(data.get("gesture_walk_debounce_sec", GESTURE_WALK_DEBOUNCE_SEC))
            FACE_CX_RATIO = min(2.0, max(-1.0, float(data.get("face_cx", FACE_CX_RATIO if FACE_CX_RATIO is not None else 0.5))))
            _cy = data.get("face_cy", FACE_CY_RATIO)
            FACE_CY_RATIO = None if _cy is None else min(0.9, max(0.1, float(_cy)))
            FACE_SIZE = min(4.0, max(0.2, float(data.get("face_size", FACE_SIZE))))
            _pp = str(data.get("pip_pos", PIP_POS)).upper()
            PIP_POS = _pp if _pp in ("TR", "TL", "BR", "BL", "FREE") else "BR"
            PIP_SCALE = min(3.0, max(0.1, float(data.get("pip_scale", PIP_SCALE))))
            PIP_X = min(2.0, max(-1.0, float(data.get("pip_x", PIP_X))))
            PIP_Y = min(2.0, max(-1.0, float(data.get("pip_y", PIP_Y))))
            try:
                _cr = [min(1.0, max(0.0, float(v))) for v in data.get("pip_crop", PIP_CROP)]
                if len(_cr) == 4 and _cr[2] > 0.05 and _cr[3] > 0.05:
                    PIP_CROP = _cr
            except Exception:
                pass
            SLIDE_ZOOM = min(2.0, max(0.3, float(data.get("slide_zoom", SLIDE_ZOOM))))
            SLIDE_X = min(2.0, max(-1.0, float(data.get("slide_x", SLIDE_X))))
            SLIDE_Y = min(2.0, max(-1.0, float(data.get("slide_y", SLIDE_Y))))
            VSLIDE_MODE = bool(data.get("vslide_mode", VSLIDE_MODE))
            VSLIDE_SCALE = min(1.5, max(0.2, float(data.get("vslide_scale", VSLIDE_SCALE))))
            VSLIDE_X = min(2.0, max(-1.0, float(data.get("vslide_x", VSLIDE_X))))
            VSLIDE_Y = min(2.0, max(-1.0, float(data.get("vslide_y", VSLIDE_Y))))
            _vf = str(data.get("vslide_fit", VSLIDE_FIT)).upper()
            VSLIDE_FIT = _vf if _vf in ("FIT", "STRETCH", "FILL") else "FIT"
            try:
                _vr = int(data.get("vslide_rot", VSLIDE_ROT))
                VSLIDE_ROT = _vr if _vr in (0, 90, 180, 270) else 0
            except Exception:
                pass
            try:
                _st = data.get("slide_text", {})
                if isinstance(_st, dict):
                    _clean = {}
                    for _k, _v in list(_st.items())[:12]:
                        if isinstance(_v, dict):
                            _clean[str(int(_k))] = {
                                "name": str(_v.get("name", ""))[:60],
                                "desc": str(_v.get("desc", ""))[:300],
                            }
                    SLIDE_TEXT = _clean
            except Exception:
                pass
            try:
                SLIDE_TEXT_REV = int(data.get("slide_text_rev", SLIDE_TEXT_REV))
            except Exception:
                pass
            GESTURE_HAND_SIZE = min(2.0, max(0.5, float(data.get("gesture_hand_size", GESTURE_HAND_SIZE))))
            GESTURE_CONFIRM_N = max(1, min(3, int(data.get("gesture_confirm_n", GESTURE_CONFIRM_N))))
            FOAM_L = min(0.4, max(0.0, float(data.get("foam_l", FOAM_L))))
            FOAM_T = min(0.4, max(0.0, float(data.get("foam_t", FOAM_T))))
            FOAM_R = min(0.4, max(0.0, float(data.get("foam_r", FOAM_R))))
            FOAM_B = min(0.4, max(0.0, float(data.get("foam_b", FOAM_B))))
            VIEW_X = min(1.0, max(0.0, float(data.get("view_x", VIEW_X))))
            VIEW_Y = min(1.0, max(0.0, float(data.get("view_y", VIEW_Y))))
            VIEW_W = min(1.0, max(0.2, float(data.get("view_w", VIEW_W))))
            VIEW_H = min(1.0, max(0.2, float(data.get("view_h", VIEW_H))))
        except Exception:
            pass


def save_calibration():
    data = {
        "mirror_x": MIRROR_GAZE_X,
        "mirror_gaze_x": MIRROR_GAZE_X,
        "mirror_gesture_x": MIRROR_GESTURE_X,
        "invert_y": INVERT_CAMERA_Y,
        "camera_position": CAMERA_POSITION,
        "monitor_diag": MONITOR_DIAG_INCHES,
        "couch_dist": COUCH_DIST_METERS,
        "sens_x": GAZE_SENSITIVITY_X,
        "sens_y": GAZE_SENSITIVITY_Y,
        "offset_x": GAZE_OFFSET_X,
        "offset_y": GAZE_OFFSET_Y,
        "show_pip": SHOW_CAMERA_PIP,
        "show_diagnostics": SHOW_DIAGNOSTICS,
        "hud_enabled": HUD_ENABLED,
        "gesture_swipe_enabled": GESTURE_SWIPE_ENABLED,
        "gesture_hold_enabled": GESTURE_HOLD_ENABLED,
        "gesture_twohand_enabled": GESTURE_TWOHAND_ENABLED,
        "gesture_sens": GESTURE_SWIPE_SENSITIVITY,
        "sens_left": GESTURE_SENS_LEFT,
        "sens_right": GESTURE_SENS_RIGHT,
        "sens_up": GESTURE_SENS_UP,
        "sens_down": GESTURE_SENS_DOWN,
        "gesture_cooldown": GESTURE_COOLDOWN_SEC,
        "gesture_cooldown_sec": GESTURE_COOLDOWN_SEC,
        "gesture_refractory_sec": GESTURE_REFRACTORY_SEC,
        "gesture_same_dir_sec": GESTURE_SAME_DIR_SEC,
        "swipe_anim_enabled": SWIPE_ANIMATION_ENABLED,
        "swipe_animation_enabled": SWIPE_ANIMATION_ENABLED,
        "screen_rotation": SCREEN_ROTATION,
        "camera_rotation": CAMERA_ROTATION,
        "show_gesture_banner": SHOW_GESTURE_BANNER,
        "event_display_time": EVENT_DISPLAY_TIME,
        "auto_cycle_enabled": AUTO_CYCLE_ENABLED,
        "gesture_mode": GESTURE_MODE,
        "gesture_walk_lockout_speed": GESTURE_WALK_LOCKOUT_SPEED,
        "gesture_walk_debounce_sec": GESTURE_WALK_DEBOUNCE_SEC,
        "face_cx": FACE_CX_RATIO,
        "face_cy": FACE_CY_RATIO,
        "face_size": FACE_SIZE,
        "pip_pos": PIP_POS,
        "pip_scale": PIP_SCALE,
        "pip_x": PIP_X,
        "pip_y": PIP_Y,
        "pip_crop": list(PIP_CROP),
        "slide_zoom": SLIDE_ZOOM,
        "slide_x": SLIDE_X,
        "slide_y": SLIDE_Y,
        "vslide_mode": VSLIDE_MODE,
        "vslide_scale": VSLIDE_SCALE,
        "vslide_x": VSLIDE_X,
        "vslide_y": VSLIDE_Y,
        "vslide_fit": VSLIDE_FIT,
        "vslide_rot": VSLIDE_ROT,
        "slide_text": {k: dict(v) for k, v in SLIDE_TEXT.items()},
        "slide_text_rev": SLIDE_TEXT_REV,
        "gesture_hand_size": GESTURE_HAND_SIZE,
        "gesture_confirm_n": GESTURE_CONFIRM_N,
        "foam_l": FOAM_L,
        "foam_t": FOAM_T,
        "foam_r": FOAM_R,
        "foam_b": FOAM_B,
        "view_x": VIEW_X,
        "view_y": VIEW_Y,
        "view_w": VIEW_W,
        "view_h": VIEW_H,
    }
    try:
        with open(CALIBRATION_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


load_calibration()

