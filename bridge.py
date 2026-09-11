"""TARS Command Bridge — asynchronous decoupled receiver for external bot controllers.

Accepts commands over:
  1. Built-in Pocket Web Remote (HTTP on port 8080 & HTTPS Secure Context on port 8443)
  2. Lightweight non-blocking UDP socket (default port 5005)
  3. Optional Raspberry Pi GPIO hardware push-buttons (pins 17, 27, 22)
  4. Optional Serial port (Arduino / USB microcontroller)

All interfaces post thread-safe pygame.USEREVENT events to the main rendering loop.
"""

from __future__ import annotations
import time
import os
import json
import socket
import threading
import queue
import ssl
import io
import wave
import math
import struct
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import pygame
import config

def _build_test_wav() -> bytes:
    """Generates an ultra-clear, high-penetration 16-bit 48kHz stereo sci-fi chime WAV in memory.
    Tuned specifically for smartphone micro-transducers (1kHz-2.5kHz resonant peak)."""
    buf = io.BytesIO()
    sample_rate = 48000
    duration_sec = 1.2
    with wave.open(buf, 'wb') as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        total_frames = int(duration_sec * sample_rate)
        for i in range(total_frames):
            t = i / sample_rate
            # 2-stage cosmic acoustic pulse: initial ping + secondary harmonic shimmer
            env = math.exp(-3.0 * t)
            sweep = 1.0 + 0.3 * math.sin(2 * math.pi * 12.0 * t)
            s = (0.50 * math.sin(2 * math.pi * 1046.5 * sweep * t) +   # C6 fundamental
                 0.35 * math.sin(2 * math.pi * 1318.5 * t) +          # E6 harmonic
                 0.15 * math.sin(2 * math.pi * 1760.0 * t)) * env      # A6 shimmer
            val = max(-32767, min(32767, int(s * 31000)))
            wav.writeframes(struct.pack('<hh', val, val))
    return buf.getvalue()

TEST_WAV_BYTES = _build_test_wav()
LATEST_SPEECH_WAV: bytes = TEST_WAV_BYTES

CMD_EVENT_TYPE = pygame.USEREVENT + 1
COMMAND_QUEUE = queue.Queue()

# ── Smart Audio Routing & Real-Time Phone Speaker Sync ──────────────────────
LATEST_SPEECH_LOCK = threading.Lock()
LATEST_SPEECH_DATA = {
    "id": 0,
    "text": "",
    "cue": "",
    "timestamp": 0.0
}
AUDIO_ROUTE_MODE = "phone"  # "phone" (default), "aux" (Pi jack/speaker), "both", "auto"

def set_latest_speech(text: str, cue: str = ""):
    global LATEST_SPEECH_DATA
    with LATEST_SPEECH_LOCK:
        LATEST_SPEECH_DATA["id"] += 1
        LATEST_SPEECH_DATA["text"] = text
        LATEST_SPEECH_DATA["cue"] = cue
        LATEST_SPEECH_DATA["timestamp"] = time.time()

def set_latest_speech_wav(wav_bytes: bytes):
    global LATEST_SPEECH_WAV
    with LATEST_SPEECH_LOCK:
        LATEST_SPEECH_WAV = wav_bytes

def get_latest_speech_wav() -> bytes:
    with LATEST_SPEECH_LOCK:
        return LATEST_SPEECH_WAV

def check_external_audio_device() -> bool:
    """Checks if an external USB sound card or DAC is attached to the Pi."""
    try:
        if os.path.exists("/proc/asound/cards"):
            with open("/proc/asound/cards", "r") as f:
                c = f.read()
                if "USB" in c or "DAC" in c:
                    return True
    except Exception:
        pass
    return False

def get_audio_route() -> str:
    global AUDIO_ROUTE_MODE
    if AUDIO_ROUTE_MODE == "auto":
        if check_external_audio_device():
            return "aux"
        return "phone"
    return AUDIO_ROUTE_MODE

def set_audio_route(mode: str):
    global AUDIO_ROUTE_MODE
    if mode in ("phone", "aux", "auto", "both"):
        AUDIO_ROUTE_MODE = mode


def parse_command(raw_str: str) -> dict | None:
    """Parses JSON or compact plain-text command strings."""
    s = raw_str.strip()
    if not s:
        return None

    # 1. Try JSON
    if s.startswith("{") and s.endswith("}"):
        try:
            return json.loads(s)
        except Exception:
            return None

    # 2. Compact text protocol
    parts = s.split()
    cmd = parts[0].lower()

    if cmd == "say" and len(parts) > 1:
        return {"cmd": "say", "clip": parts[1]}
    elif cmd == "emotion" and len(parts) > 1:
        return {"cmd": "emotion", "name": parts[1].upper()}
    elif cmd == "wink":
        side = parts[1].upper() if len(parts) > 1 else "LEFT"
        return {"cmd": "wink", "side": side}
    elif cmd == "blink":
        return {"cmd": "blink"}
    elif cmd == "event" and len(parts) > 1:
        try:
            return {"cmd": "event", "index": int(parts[1])}
        except ValueError:
            return None
    elif cmd == "target":
        active = (parts[1].lower() in ("on", "1", "true")) if len(parts) > 1 else False
        name = parts[2] if len(parts) > 2 else "SPECTATOR"
        dist = parts[3] if len(parts) > 3 else "2.1m"
        return {"cmd": "target", "active": active, "name": name, "dist": dist}

    return {"cmd": cmd}


# ── Pocket Web Remote HTML App (Embedded & Self-Contained) ────────────────────

WEB_REMOTE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no, viewport-fit=cover">
<title>TARS // POCKET REMOTE</title>
<style>
  :root {
    --void: #06080b;
    --panel: #11141a;
    --border: #222834;
    --amber: #ffa537;
    --gold: #ffd75f;
    --cyan: #4bd7ff;
    --mint: #38eb91;
    --coral: #ff8c41;
    --text: #f0ede6;
    --muted: #848896;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }
  body {
    background: var(--void);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace;
    padding: 16px 14px 44px 14px;
    max-width: 480px;
    margin: 0 auto;
    user-select: none;
  }
  header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid var(--border);
    padding-bottom: 12px;
    margin-bottom: 14px;
  }
  h1 { font-size: 16px; letter-spacing: 2px; color: var(--amber); }
  .badge { font-size: 11px; background: rgba(56, 235, 145, 0.15); color: var(--mint); padding: 4px 8px; border-radius: 4px; border: 1px solid rgba(56, 235, 145, 0.3); }
  
  .section-title {
    font-size: 11px;
    letter-spacing: 1.5px;
    color: var(--muted);
    text-transform: uppercase;
    margin: 16px 0 8px 4px;
  }
  .grid { display: grid; grid-gap: 8px; }
  .grid-2 { grid-template-columns: 1fr 1fr; }
  .grid-3 { grid-template-columns: 1fr 1fr 1fr; }
  .grid-4 { grid-template-columns: 1fr 1fr 1fr 1fr; }

  button {
    background: var(--panel);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 14px 10px;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 4px;
    transition: transform 0.08s, background 0.12s, border-color 0.12s;
  }
  button:active {
    transform: scale(0.96);
    background: #1a202c;
    border-color: var(--amber);
  }
  button span.icon { font-size: 18px; }
  button.primary {
    background: rgba(255, 165, 55, 0.12);
    border-color: rgba(255, 165, 55, 0.4);
    color: var(--amber);
  }
  button.cyan {
    background: rgba(75, 215, 255, 0.10);
    border-color: rgba(75, 215, 255, 0.35);
    color: var(--cyan);
  }
  button.mint {
    background: rgba(56, 235, 145, 0.10);
    border-color: rgba(56, 235, 145, 0.35);
    color: var(--mint);
  }

  .chat-box {
    background: #0d1117;
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 14px;
    margin-bottom: 16px;
  }
  .mic-container {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    margin: 8px 0 12px 0;
  }
  .mic-btn {
    width: 84px;
    height: 84px;
    border-radius: 50%;
    background: radial-gradient(circle, rgba(255, 165, 55, 0.25) 0%, rgba(255, 165, 55, 0.05) 70%);
    border: 2px solid var(--amber);
    color: var(--amber);
    font-size: 32px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    box-shadow: 0 0 18px rgba(255, 165, 55, 0.35);
    transition: transform 0.15s, box-shadow 0.15s;
  }
  .mic-btn.listening {
    border-color: #ff3366;
    color: #ff3366;
    background: radial-gradient(circle, rgba(255, 51, 102, 0.3) 0%, rgba(255, 51, 102, 0.08) 70%);
    box-shadow: 0 0 28px rgba(255, 51, 102, 0.6);
    animation: pulse 1.2s infinite;
  }
  @keyframes pulse {
    0% { transform: scale(1); }
    50% { transform: scale(1.08); }
    100% { transform: scale(1); }
  }
  .mic-hint {
    font-size: 11px;
    letter-spacing: 1.2px;
    color: var(--gold);
    margin-top: 8px;
    text-align: center;
  }
  .text-query-row {
    display: flex;
    gap: 8px;
    margin-top: 10px;
  }
  .text-query-row input {
    flex: 1;
    background: #161b22;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 10px 12px;
    color: #f0ede6;
    font-size: 13px;
    outline: none;
  }
  .text-query-row input:focus {
    border-color: var(--amber);
  }
  .text-query-row button {
    padding: 10px 16px;
    background: var(--amber);
    color: #06080b;
    font-weight: bold;
    border: none;
    border-radius: 6px;
  }
  .reply-card {
    margin-top: 12px;
    background: rgba(255, 255, 255, 0.04);
    border-left: 3px solid var(--amber);
    padding: 10px 12px;
    border-radius: 0 6px 6px 0;
    font-size: 12px;
    line-height: 1.4;
    display: none;
  }
  .reply-card.visible { display: block; }
  .reply-speaker { font-weight: bold; color: var(--amber); font-size: 10px; letter-spacing: 1.5px; margin-bottom: 4px; }
  .reply-text { color: #f0ede6; }

  .status-bar {
    position: fixed;
    bottom: 0; left: 0; right: 0;
    background: #090c10;
    border-top: 1px solid var(--border);
    padding: 8px 16px;
    text-align: center;
    font-size: 11px;
    color: var(--muted);
    z-index: 100;
  }
</style>
</head>
<body>

<!-- ⚡ FLOATING SCI-FI NOTIFICATION TOAST -->
<div id="toastAlert" style="position:fixed; top:18px; left:50%; transform:translateX(-50%); z-index:9999; padding:10px 18px; border-radius:8px; font-size:12px; font-weight:bold; letter-spacing:1px; display:none; box-shadow:0 6px 20px rgba(0,0,0,0.6); transition:opacity 0.3s ease; text-align:center; min-width:240px; pointer-events:none;"></div>

<header>
  <div>
    <h1>T.A.R.S.</h1>
    <div style="font-size: 11px; color: var(--muted); margin-top: 2px;">MK-IV // 75% HUMOR // 90% HONESTY</div>
  </div>
  <div style="display:flex; gap:6px; align-items:center;">
    <div class="badge" id="tempBadge" style="font-size:10px; background:rgba(56,235,145,0.12); color:#38eb91; border:1px solid rgba(56,235,145,0.3);">CPU: --°C</div>
    <div class="badge" id="statusBadge">ONLINE</div>
  </div>
</header>

<!-- 🔒 BROWSER SECURITY & MICROPHONE CONTEXT BANNER -->
<div id="securityBanner" style="display:none; margin-bottom:14px; padding:10px 12px; border-radius:8px; font-size:11px; line-height:1.45;"></div>

<!-- 🔴 LIVE CAMERA SENSOR HUD DISPLAY -->
<div style="background:#0d1117; border:1px solid var(--border); border-radius:12px; padding:12px; margin-bottom:14px;">
  <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
    <div style="font-size:11px; font-weight:bold; letter-spacing:1.5px; color:var(--mint); display:flex; align-items:center; gap:6px;">
      <span style="width:8px; height:8px; border-radius:50%; background:var(--mint); display:inline-block; box-shadow:0 0 8px var(--mint);"></span>
      OPTICAL RECON // LIVE CAMERA STREAM
    </div>
    <div id="lockBadge" style="font-size:10px; background:rgba(255,165,55,0.15); color:var(--amber); padding:2px 6px; border-radius:4px; border:1px solid rgba(255,165,55,0.3); font-weight:bold;">
      SCANNING
    </div>
  </div>

  <div style="position:relative; width:100%; border-radius:8px; overflow:hidden; border:1px solid #222834; background:#000; min-height:220px; display:flex; align-items:center; justify-content:center;">
    <img id="liveStream" src="/stream.mjpg" style="width:100%; height:auto; display:block;" />
    <!-- Sci-fi Corner Badges -->
    <div style="position:absolute; top:8px; left:8px; font-size:9px; color:#38eb91; font-family:monospace; background:rgba(0,0,0,0.7); padding:3px 6px; border-radius:3px; border:1px solid rgba(56,235,145,0.3);">
      FEED: S16 MINI [USB-0]
    </div>
    <div id="streamStats" style="position:absolute; bottom:8px; left:8px; font-size:9px; color:#ff8c41; font-family:monospace; background:rgba(0,0,0,0.7); padding:3px 6px; border-radius:3px; border:1px solid rgba(255,140,65,0.3);">
      TARGET: STANDBY // LATENCY: 12ms
    </div>
  </div>

  <div style="display:flex; gap:6px; margin-top:8px;">
    <button onclick="reconnectCamera()" style="flex:1; padding:8px 4px; font-size:11px; background:#161b22; border-color:#2a3242; color:#848896;">
      🔄 RECONNECT
    </button>
    <button id="btnMirrorCamera" onclick="toggleCameraMirror()" style="flex:1; padding:8px 4px; font-size:11px; background:#161b22; border-color:#2a3242; color:#848896;">
      🪞 GAZE MIRROR
    </button>
    <button id="btnMirrorGestureQuick" onclick="toggleGestureMirror()" style="flex:1; padding:8px 4px; font-size:11px; background:#161b22; border-color:#2a3242; color:#848896;">
      ✋ SWIPE MIRROR
    </button>
    <button onclick="window.open('/snapshot.jpg', '_blank')" style="padding:8px 10px; font-size:11px; background:#161b22; border-color:#2a3242; color:#848896;">
      🔍
    </button>
  </div>
</div>

<!-- 📺 TV DISPLAY & SLIDES CONTROL DECK -->
<div style="background:#0d1117; border:1px solid var(--border); border-radius:12px; padding:12px; margin-bottom:14px;">
  <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
    <div style="font-size:11px; font-weight:bold; letter-spacing:1.5px; color:var(--cyan); display:flex; align-items:center; gap:6px;">
      <span style="font-size:13px;">📺</span> TV DISPLAY & SLIDES CONTROL
    </div>
    <div id="displayModeBadge" style="font-size:10px; background:rgba(75,215,255,0.15); color:var(--cyan); padding:2px 8px; border-radius:4px; border:1px solid rgba(75,215,255,0.3); font-weight:bold;">
      ROBOT FACE
    </div>
  </div>

  <div class="grid grid-3" style="margin-bottom:8px;">
    <button id="btnShowFace" class="cyan" onclick="setDisplayMode('face')">
      [SHOW FACE]
    </button>
    <button id="btnShowSlides" onclick="setDisplayMode('slides')">
      [SHOW SLIDES]
    </button>
    <button id="btnModeAuto" onclick="setDisplayMode('auto')">
      [AUTO CYCLE]
    </button>
  </div>

  <!-- Auto Cycle Toggle & Slide Timing Stepper -->
  <div style="background:#161b22; border:1px solid #222834; border-radius:8px; padding:10px 12px; margin-bottom:10px;">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
      <div style="font-size:11px; font-weight:bold; color:var(--text); letter-spacing:1px; display:flex; align-items:center; gap:5px;">
        <span>⏱️</span> AUTO ADVANCE &amp; TIMING
      </div>
      <button id="btnToggleAutoCycle" onclick="toggleAutoCycle()" class="mint" style="padding:5px 12px; font-size:11px; font-weight:bold; border-radius:6px;">
        AUTO: ON
      </button>
    </div>
    <div style="display:flex; align-items:center; justify-content:space-between;">
      <span style="font-size:11px; color:var(--muted); font-weight:bold;">SLIDE DURATION:</span>
      <div style="display:flex; align-items:center; gap:6px;">
        <button onclick="adjustSlideDuration(-1)" style="padding:6px 12px; font-size:12px; font-weight:bold; background:#0d1117; border-color:#2a3242;">-1s</button>
        <input type="number" id="inpSlideDuration" min="1" max="120" step="0.5" value="8.0" onchange="onSlideDurationInput(this.value)" style="width:68px; text-align:center; background:#0d1117; border:1px solid var(--border); border-radius:6px; color:var(--gold); font-weight:bold; font-size:13px; padding:5px 2px; outline:none;" />
        <span style="font-size:12px; color:var(--gold); font-weight:bold;">sec</span>
        <button onclick="adjustSlideDuration(1)" style="padding:6px 12px; font-size:12px; font-weight:bold; background:#0d1117; border-color:#2a3242;">+1s</button>
      </div>
    </div>
  </div>

  <div style="font-size:10px; color:var(--muted); letter-spacing:1px; margin:8px 0 4px 2px;">SELECT FEST SLIDE TO PRESENT:</div>
  <div class="grid grid-3" style="margin-bottom:8px;">
    <button onclick="selectSlide(0)" style="padding:8px; font-size:11px;">#1 BIZ BATTLE</button>
    <button onclick="selectSlide(1)" style="padding:8px; font-size:11px;">#2 GAMING</button>
    <button onclick="selectSlide(2)" style="padding:8px; font-size:11px;">#3 LOAD BRIDGE</button>
    <button onclick="selectSlide(3)" style="padding:8px; font-size:11px;">#4 HORIZON</button>
    <button onclick="selectSlide(4)" style="padding:8px; font-size:11px;">#5 KARTKRAFT</button>
    <button onclick="selectSlide(5)" style="padding:8px; font-size:11px;">#6 MINDFORGE</button>
  </div>

  <!-- Optical Swipe & Touch Navigation -->
  <div style="display:flex; justify-content:space-between; align-items:center; margin:10px 0 6px 2px;">
    <div style="font-size:10px; color:var(--mint); letter-spacing:1px; font-weight:bold; display:flex; align-items:center; gap:5px;">
      <span>[GESTURE]</span> HAND SWIPE &amp; TOUCH NAV
    </div>
    <div id="handBadge" style="font-size:9px; background:rgba(56,235,145,0.12); color:#38eb91; padding:2px 6px; border-radius:4px; border:1px solid rgba(56,235,145,0.3); font-weight:bold;">
      HAND: SCANNING
    </div>
  </div>

  <div class="grid grid-2" style="margin-bottom:6px;">
    <button onclick="stepSlide('prev')" class="cyan" style="padding:11px 8px; font-size:11px; font-weight:bold;">
      [&lt; PREV SLIDE] (SWIPE L)
    </button>
    <button onclick="stepSlide('next')" class="cyan" style="padding:11px 8px; font-size:11px; font-weight:bold;">
      [NEXT SLIDE &gt;] (SWIPE R)
    </button>
  </div>
  <div class="grid grid-2" style="margin-bottom:8px;">
    <button onclick="stepSlide('up')" class="primary" style="padding:9px 8px; font-size:11px; font-weight:bold;">
      [^ SHOW FACE] (SWIPE UP)
    </button>
    <button onclick="stepSlide('down')" class="primary" style="padding:9px 8px; font-size:11px; font-weight:bold;">
      [v SHOW FACE] (SWIPE DN)
    </button>
  </div>

  <!-- Touch Swipe Trackpad -->
  <div id="swipeTouchPad" style="padding:12px 10px; background:#161b22; border:1px dashed #2a3242; border-radius:8px; text-align:center; font-size:11px; color:#848896; margin-bottom:8px; touch-action:none; user-select:none;">
    <strong>[SWIPE THUMB HERE OR WAVE AT CAMERA]</strong><br>
    <span style="font-size:9px; color:#555d6e;">L/R: SLIDES &bull; UP/DOWN: ROBOT FACE</span>
  </div>

  <div class="grid grid-3" style="margin-top:6px;">
    <button id="btnToggleGesture" onclick="toggleGestureSwipe()" class="mint" style="padding:10px 4px; font-size:11px;">
      [GESTURE: ON]
    </button>
    <button id="btnGestureMode" onclick="toggleGestureMode()" class="primary" style="padding:10px 4px; font-size:11px;">
      [MODE: 4-WAY]
    </button>
    <button id="btnToggleSwipeAnim" onclick="toggleSwipeAnim()" class="mint" style="padding:10px 4px; font-size:11px;">
      [ANIM: ON]
    </button>
  </div>
  <div class="grid grid-2" style="margin-top:6px;">
    <button id="btnTogglePip" onclick="toggleCameraPip()" style="padding:10px; font-size:11px; background:#161b22; border-color:#2a3242; color:#848896;">
      [CAMERA PiP: OFF]
    </button>
    <button id="btnToggleDiag" onclick="toggleDiagnostics()" class="primary" style="padding:10px; font-size:11px;">
      [RETICLE HUD: ON]
    </button>
  </div>

  <!-- Gesture Cooldown Quick Stepper -->
  <div style="background:#161b22; border:1px solid #222834; border-radius:8px; padding:10px 12px; margin-top:8px;">
    <div style="display:flex; justify-content:space-between; align-items:center;">
      <div style="font-size:11px; font-weight:bold; color:var(--text); letter-spacing:1px; display:flex; align-items:center; gap:5px;">
        <span>⏳</span> GESTURE COOLDOWN:
      </div>
      <div style="display:flex; align-items:center; gap:6px;">
        <button onclick="adjustCooldown(-0.1)" style="padding:6px 12px; font-size:12px; font-weight:bold; background:#0d1117; border-color:#2a3242;">-0.1s</button>
        <input type="number" id="inpCooldown" min="0.3" max="3.0" step="0.1" value="1.0" onchange="onCooldownInput(this.value)" style="width:64px; text-align:center; background:#0d1117; border:1px solid var(--border); border-radius:6px; color:var(--gold); font-weight:bold; font-size:13px; padding:5px 2px; outline:none;" />
        <span style="font-size:12px; color:var(--gold); font-weight:bold;">sec</span>
        <button onclick="adjustCooldown(0.1)" style="padding:6px 12px; font-size:12px; font-weight:bold; background:#0d1117; border-color:#2a3242;">+0.1s</button>
      </div>
    </div>
  </div>
</div>

<!-- 🎯 SIGHTLINE & CAMERA GEOMETRY CALIBRATION -->
<div style="background:#0d1117; border:1px solid var(--border); border-radius:12px; padding:12px; margin-bottom:14px;">
  <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
    <div style="font-size:11px; font-weight:bold; letter-spacing:1.5px; color:var(--amber); display:flex; align-items:center; gap:6px;">
      <span style="font-size:13px;">🎯</span> SIGHTLINE & CAMERA CALIBRATION
    </div>
    <div id="calibBadge" style="font-size:10px; background:rgba(255,165,55,0.15); color:var(--amber); padding:2px 8px; border-radius:4px; border:1px solid rgba(255,165,55,0.3); font-weight:bold;">
      CALIBRATED
    </div>
  </div>

  <!-- Quick Toggle Buttons -->
  <div class="grid grid-2" style="margin-bottom:8px;">
    <button id="btnMirrorGaze" onclick="toggleMirrorGaze()" style="padding:10px; font-size:11px;">
      👁️ GAZE MIRROR: OFF
    </button>
    <button id="btnMirrorGesture" onclick="toggleMirrorGesture()" style="padding:10px; font-size:11px;">
      👋 SWIPE MIRROR: NATURAL
    </button>
  </div>
  <div class="grid grid-2" style="margin-bottom:8px;">
    <button id="btnInvertY" onclick="toggleInvertY()" style="padding:10px; font-size:11px;">
      ↕️ INVERT Y: OFF
    </button>
    <button id="btnCamPos" onclick="toggleCamPos()" class="primary" style="padding:10px; font-size:11px;">
      📍 CAM: CENTER
    </button>
  </div>
  <div style="font-size:10px; color:var(--muted); letter-spacing:1px; margin-bottom:4px;">SCREEN ORIENTATION (DIRECT):</div>
  <div class="grid grid-4" style="margin-bottom:8px;">
    <button id="rot0" class="mint" onclick="setScreenRot(0)" style="padding:10px; font-size:11px;">0°</button>
    <button id="rot90" onclick="setScreenRot(90)" style="padding:10px; font-size:11px;">90°</button>
    <button id="rot180" onclick="setScreenRot(180)" style="padding:10px; font-size:11px;">180°</button>
    <button id="rot270" onclick="setScreenRot(270)" style="padding:10px; font-size:11px;">270°</button>
  </div>
  <div class="grid grid-2" style="margin-bottom:10px;">
    <button id="btnRotateScreen" onclick="rotateScreen()" style="padding:10px; font-size:11px;">
      🔄 CYCLE SCREEN
    </button>
    <button id="btnRotateCam" onclick="rotateCamera()" class="cyan" style="padding:10px; font-size:11px;">
      📷 CAMERA ORIENTATION: 0°
    </button>
  </div>

  <!-- Monitor Diagonal Size Selection -->
  <div style="font-size:10px; color:var(--muted); letter-spacing:1px; margin-bottom:4px;">MONITOR / TV SIZE:</div>
  <div class="grid grid-5" style="display:grid; grid-template-columns:repeat(5, 1fr); gap:6px; margin-bottom:12px;">
    <button id="btnDiag24" onclick="setMonitorSize(24)" style="padding:6px; font-size:10px;">24"</button>
    <button id="btnDiag32" onclick="setMonitorSize(32)" style="padding:6px; font-size:10px;">32"</button>
    <button id="btnDiag43" class="cyan" onclick="setMonitorSize(43)" style="padding:6px; font-size:10px;">43"</button>
    <button id="btnDiag55" onclick="setMonitorSize(55)" style="padding:6px; font-size:10px;">55"</button>
    <button id="btnDiag65" onclick="setMonitorSize(65)" style="padding:6px; font-size:10px;">65"</button>
  </div>

  <!-- Real-Time Sliders -->
  <div style="margin-bottom:10px;">
    <div style="display:flex; justify-content:space-between; font-size:10px; color:var(--muted); margin-bottom:2px;">
      <span>DISTANCE TO VIEWER / COUCH</span>
      <span id="lblCouchDist" style="color:var(--text); font-weight:bold;">2.2m</span>
    </div>
    <input type="range" id="rngDist" min="1.0" max="5.0" step="0.2" value="2.2" oninput="onCalibSliderChange()" style="width:100%; accent-color:var(--amber);">
  </div>

  <div style="margin-bottom:10px;">
    <div style="display:flex; justify-content:space-between; font-size:10px; color:var(--muted); margin-bottom:2px;">
      <span>GAZE HORIZONTAL SENSITIVITY</span>
      <span id="lblSensX" style="color:var(--text); font-weight:bold;">2.4x</span>
    </div>
    <input type="range" id="rngSensX" min="0.5" max="4.0" step="0.1" value="2.4" oninput="onCalibSliderChange()" style="width:100%; accent-color:var(--cyan);">
  </div>

  <div style="margin-bottom:10px;">
    <div style="display:flex; justify-content:space-between; font-size:10px; color:var(--muted); margin-bottom:2px;">
      <span>GAZE VERTICAL SENSITIVITY</span>
      <span id="lblSensY" style="color:var(--text); font-weight:bold;">2.2x</span>
    </div>
    <input type="range" id="rngSensY" min="0.5" max="4.0" step="0.1" value="2.2" oninput="onCalibSliderChange()" style="width:100%; accent-color:var(--cyan);">
  </div>

  <div style="margin-bottom:10px;">
    <div style="display:flex; justify-content:space-between; font-size:10px; color:var(--muted); margin-bottom:2px;">
      <span>HORIZONTAL TRIM OFFSET (EYE ALIGNMENT)</span>
      <span id="lblOffX" style="color:var(--text); font-weight:bold;">0.00</span>
    </div>
    <input type="range" id="rngOffX" min="-0.30" max="0.30" step="0.02" value="0.00" oninput="onCalibSliderChange()" style="width:100%; accent-color:var(--mint);">
  </div>

  <div style="margin-bottom:10px;">
    <div style="display:flex; justify-content:space-between; font-size:10px; color:var(--muted); margin-bottom:2px;">
      <span>VERTICAL TRIM OFFSET (EYE ALIGNMENT)</span>
      <span id="lblOffY" style="color:var(--text); font-weight:bold;">0.00</span>
    </div>
    <input type="range" id="rngOffY" min="-0.30" max="0.30" step="0.02" value="0.00" oninput="onCalibSliderChange()" style="width:100%; accent-color:var(--mint);">
  </div>

  <div style="margin-bottom:12px;">
    <div style="display:flex; justify-content:space-between; font-size:10px; color:var(--muted); margin-bottom:2px;">
      <span>⏳ GESTURE COOLDOWN &amp; LOCKOUT</span>
      <span id="lblGestureCooldown" style="color:var(--gold); font-weight:bold;">1.0s</span>
    </div>
    <input type="range" id="rngGestureCooldown" min="0.3" max="3.0" step="0.1" value="1.0" oninput="onCalibSliderChange()" style="width:100%; accent-color:var(--gold);">
    <div style="font-size:9px; color:#555d6e; margin-top:2px;">MINIMUM TIME BETWEEN GESTURES &bull; HIGHER (1.2s–1.5s) = ZERO FALSE TRIGGERS</div>
  </div>

  <div style="margin-bottom:12px;">
    <div style="display:flex; justify-content:space-between; font-size:10px; color:var(--muted); margin-bottom:2px;">
      <span>MASTER GESTURE SWIPE SENSITIVITY</span>
      <span id="lblGestureSens" style="color:var(--gold); font-weight:bold;">1.0x</span>
    </div>
    <input type="range" id="rngGestureSens" min="0.5" max="3.0" step="0.1" value="1.0" oninput="onCalibSliderChange()" style="width:100%; accent-color:var(--gold);">
    <div style="font-size:9px; color:#555d6e; margin-top:2px;">MASTER MULTIPLIER &bull; HIGHER (2.0x+) = SHORT SWIPE &bull; LOWER (0.8x) = WIDE SWIPE</div>
  </div>

  <!-- 🎯 DIRECTIONAL SWIPE SENSITIVITY GRID -->
  <div style="background:#131822; border:1px solid #1f2735; border-radius:8px; padding:10px; margin-bottom:12px;">
    <div style="font-size:10px; font-weight:bold; letter-spacing:1px; color:var(--gold); margin-bottom:8px; display:flex; align-items:center; justify-content:space-between;">
      <span>DIRECTIONAL SWIPE SENSITIVITY</span>
      <span style="font-size:9px; color:var(--muted); font-weight:normal;">PER-AXIS TUNING</span>
    </div>
    <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
      <div>
        <div style="display:flex; justify-content:space-between; font-size:9px; color:var(--muted); margin-bottom:2px;">
          <span>⬅️ LEFT (PREV)</span>
          <span id="lblSensLeft" style="color:var(--text); font-weight:bold;">1.0x</span>
        </div>
        <input type="range" id="rngSensLeft" min="0.3" max="3.0" step="0.1" value="1.0" oninput="onCalibSliderChange()" style="width:100%; accent-color:var(--gold);">
      </div>
      <div>
        <div style="display:flex; justify-content:space-between; font-size:9px; color:var(--muted); margin-bottom:2px;">
          <span>➡️ RIGHT (NEXT)</span>
          <span id="lblSensRight" style="color:var(--text); font-weight:bold;">1.0x</span>
        </div>
        <input type="range" id="rngSensRight" min="0.3" max="3.0" step="0.1" value="1.0" oninput="onCalibSliderChange()" style="width:100%; accent-color:var(--gold);">
      </div>
      <div>
        <div style="display:flex; justify-content:space-between; font-size:9px; color:var(--muted); margin-bottom:2px;">
          <span>⬆️ UP (SHOW FACE)</span>
          <span id="lblSensUp" style="color:var(--text); font-weight:bold;">1.0x</span>
        </div>
        <input type="range" id="rngSensUp" min="0.3" max="3.0" step="0.1" value="1.0" oninput="onCalibSliderChange()" style="width:100%; accent-color:var(--cyan);">
      </div>
      <div>
        <div style="display:flex; justify-content:space-between; font-size:9px; color:var(--muted); margin-bottom:2px;">
          <span>⬇️ DOWN (SHOW FACE)</span>
          <span id="lblSensDown" style="color:var(--text); font-weight:bold;">1.0x</span>
        </div>
        <input type="range" id="rngSensDown" min="0.3" max="3.0" step="0.1" value="1.0" oninput="onCalibSliderChange()" style="width:100%; accent-color:var(--cyan);">
      </div>
    </div>
  </div>

  <div style="display:flex; gap:8px; margin-top:12px;">
    <button id="btnSaveCalib" onclick="saveAllSettings()" class="mint" style="flex:1; padding:11px 8px; font-size:11px; font-weight:bold; letter-spacing:1px; display:flex; align-items:center; justify-content:center; gap:6px;">
      💾 SAVE SETTINGS
    </button>
    <button id="btnResetCalib" onclick="confirmResetCalibration()" style="flex:1; padding:11px 8px; font-size:11px; font-weight:bold; background:#161b22; border-color:#2a3242; color:#ff8c41; letter-spacing:1px; display:flex; align-items:center; justify-content:center; gap:6px;">
      🔄 RESET DEFAULTS
    </button>
  </div>
</div>

<!-- 🎛️ LAYOUT STUDIO — move/resize face + camera box freely, changes apply live -->
<div style="background:#0d1117; border:1px solid var(--border); border-radius:12px; padding:12px; margin-bottom:14px;">
  <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
    <div style="font-size:11px; font-weight:bold; letter-spacing:1.5px; color:var(--gold); display:flex; align-items:center; gap:6px;">
      <span style="font-size:13px;">🎛️</span> LAYOUT STUDIO
    </div>
    <div style="font-size:10px; color:var(--muted);">LIVE — drag &amp; watch TV</div>
  </div>
  <div style="margin-bottom:8px;">
    <div style="display:flex; justify-content:space-between; font-size:11px; color:var(--muted); margin-bottom:2px;">
      <span>🤖 FACE X</span><span id="lblFaceX" style="color:var(--text); font-weight:bold;">50%</span>
    </div>
    <input type="range" id="rngFaceX" min="10" max="90" step="1" value="50" oninput="onLayoutChange()" style="width:100%; accent-color:var(--gold);">
  </div>
  <div style="margin-bottom:8px;">
    <div style="display:flex; justify-content:space-between; font-size:11px; color:var(--muted); margin-bottom:2px;">
      <span>🤖 FACE Y</span><span id="lblFaceY" style="color:var(--text); font-weight:bold;">AUTO</span>
    </div>
    <input type="range" id="rngFaceY" min="10" max="90" step="1" value="44" oninput="onLayoutChange()" style="width:100%; accent-color:var(--gold);">
  </div>
  <div style="margin-bottom:10px;">
    <div style="display:flex; justify-content:space-between; font-size:11px; color:var(--muted); margin-bottom:2px;">
      <span>🔍 FACE SIZE</span><span id="lblFaceSize" style="color:var(--text); font-weight:bold;">100%</span>
    </div>
    <input type="range" id="rngFaceSize" min="40" max="200" step="5" value="100" oninput="onLayoutChange()" style="width:100%; accent-color:var(--gold);">
  </div>
  <div style="font-size:11px; color:var(--muted); margin-bottom:6px;">📹 CAMERA BOX CORNER</div>
  <div class="grid grid-4" style="margin-bottom:8px;">
    <button id="pipTL" onclick="setPipPos('TL')">↖ TL</button>
    <button id="pipTR" onclick="setPipPos('TR')">↗ TR</button>
    <button id="pipBL" onclick="setPipPos('BL')">↙ BL</button>
    <button id="pipBR" class="mint" onclick="setPipPos('BR')">↘ BR</button>
  </div>
  <div style="margin-bottom:8px;">
    <button id="pipFREE" onclick="setPipPos('FREE')" style="width:100%; padding:9px 8px; font-size:11px; font-weight:bold;">✋ FREE PLACE — drag X/Y below</button>
  </div>
  <div style="margin-bottom:8px;">
    <div style="display:flex; justify-content:space-between; font-size:11px; color:var(--muted); margin-bottom:2px;">
      <span>📹 BOX X</span><span id="lblPipX" style="color:var(--text); font-weight:bold;">100%</span>
    </div>
    <input type="range" id="rngPipX" min="0" max="100" step="1" value="100" oninput="onLayoutChange()" style="width:100%; accent-color:var(--cyan);">
  </div>
  <div style="margin-bottom:8px;">
    <div style="display:flex; justify-content:space-between; font-size:11px; color:var(--muted); margin-bottom:2px;">
      <span>📹 BOX Y</span><span id="lblPipY" style="color:var(--text); font-weight:bold;">100%</span>
    </div>
    <input type="range" id="rngPipY" min="0" max="100" step="1" value="100" oninput="onLayoutChange()" style="width:100%; accent-color:var(--cyan);">
  </div>
  <div style="margin-bottom:10px;">
    <div style="display:flex; justify-content:space-between; font-size:11px; color:var(--muted); margin-bottom:2px;">
      <span>📹 CAMERA BOX SIZE</span><span id="lblPipSize" style="color:var(--text); font-weight:bold;">100%</span>
    </div>
    <input type="range" id="rngPipSize" min="30" max="150" step="5" value="100" oninput="onLayoutChange()" style="width:100%; accent-color:var(--cyan);">
  </div>
  <div style="font-size:11px; color:var(--muted); margin-bottom:6px;">✂️ CAMERA CROP (cut ceiling/floor: X / Y / W / H %)</div>
  <div class="grid grid-4" style="margin-bottom:10px;">
    <div><div style="font-size:10px; color:var(--muted);">X <span id="lblCropX">0</span></div><input type="range" id="rngCropX" min="0" max="80" step="1" value="0" oninput="onLayoutChange()" style="width:100%; accent-color:var(--cyan);"></div>
    <div><div style="font-size:10px; color:var(--muted);">Y <span id="lblCropY">0</span></div><input type="range" id="rngCropY" min="0" max="80" step="1" value="0" oninput="onLayoutChange()" style="width:100%; accent-color:var(--cyan);"></div>
    <div><div style="font-size:10px; color:var(--muted);">W <span id="lblCropW">100</span></div><input type="range" id="rngCropW" min="20" max="100" step="1" value="100" oninput="onLayoutChange()" style="width:100%; accent-color:var(--cyan);"></div>
    <div><div style="font-size:10px; color:var(--muted);">H <span id="lblCropH">100</span></div><input type="range" id="rngCropH" min="20" max="100" step="1" value="100" oninput="onLayoutChange()" style="width:100%; accent-color:var(--cyan);"></div>
  </div>
  <div style="margin-bottom:8px;">
    <div style="display:flex; justify-content:space-between; font-size:11px; color:var(--muted); margin-bottom:2px;">
      <span>🖥️ SLIDE ZOOM</span><span id="lblZoom" style="color:var(--text); font-weight:bold;">100%</span>
    </div>
    <input type="range" id="rngZoom" min="50" max="100" step="1" value="100" oninput="onLayoutChange()" style="width:100%; accent-color:var(--gold);">
  </div>
  <div class="grid grid-2" style="margin-bottom:8px;">
    <div><div style="display:flex; justify-content:space-between; font-size:11px; color:var(--muted); margin-bottom:2px;"><span>🖥️ SLIDE X</span><span id="lblSlideX" style="color:var(--text); font-weight:bold;">50%</span></div><input type="range" id="rngSlideX" min="0" max="100" step="1" value="50" oninput="onLayoutChange()" style="width:100%; accent-color:var(--gold);"></div>
    <div><div style="display:flex; justify-content:space-between; font-size:11px; color:var(--muted); margin-bottom:2px;"><span>🖥️ SLIDE Y</span><span id="lblSlideY" style="color:var(--text); font-weight:bold;">50%</span></div><input type="range" id="rngSlideY" min="0" max="100" step="1" value="50" oninput="onLayoutChange()" style="width:100%; accent-color:var(--gold);"></div>
  </div>
  <div style="margin-bottom:8px;">
    <button id="btnVslide" onclick="toggleVslide()" style="width:100%; padding:10px 8px; font-size:11px; font-weight:bold;">📱 PORTRAIT SLIDES: OFF</button>
  </div>
  <div style="font-size:11px; color:var(--muted); margin-bottom:6px;">CONTENT FIT:</div>
  <div class="grid grid-3" style="margin-bottom:8px;">
    <button id="vfitFIT" class="mint" onclick="setVslideFit('FIT')" style="padding:9px 8px; font-size:11px; font-weight:bold;">FIT</button>
    <button id="vfitSTRETCH" onclick="setVslideFit('STRETCH')" style="padding:9px 8px; font-size:11px; font-weight:bold;">STRETCH</button>
    <button id="vfitFILL" onclick="setVslideFit('FILL')" style="padding:9px 8px; font-size:11px; font-weight:bold;">FILL</button>
  </div>
  <div style="font-size:11px; color:var(--muted); margin-bottom:6px;">CONTENT ROTATION:</div>
  <div class="grid grid-4" style="margin-bottom:8px;">
    <button id="vrot0" class="mint" onclick="setVslideRot(0)" style="padding:9px 8px; font-size:11px; font-weight:bold;">0°</button>
    <button id="vrot90" onclick="setVslideRot(90)" style="padding:9px 8px; font-size:11px; font-weight:bold;">90°</button>
    <button id="vrot180" onclick="setVslideRot(180)" style="padding:9px 8px; font-size:11px; font-weight:bold;">180°</button>
    <button id="vrot270" onclick="setVslideRot(270)" style="padding:9px 8px; font-size:11px; font-weight:bold;">270°</button>
  </div>
  <div id="vslideCtl" style="display:none;">
    <div style="margin-bottom:8px;">
      <div style="display:flex; justify-content:space-between; font-size:11px; color:var(--muted); margin-bottom:2px;">
        <span>📱 COLUMN SIZE</span><span id="lblVslideSize" style="color:var(--text); font-weight:bold;">90%</span>
      </div>
      <input type="range" id="rngVslideSize" min="30" max="100" step="1" value="90" oninput="onLayoutChange()" style="width:100%; accent-color:var(--gold);">
    </div>
    <div class="grid grid-2" style="margin-bottom:10px;">
      <div><div style="display:flex; justify-content:space-between; font-size:11px; color:var(--muted); margin-bottom:2px;"><span>📱 COLUMN X</span><span id="lblVslideX" style="color:var(--text); font-weight:bold;">50%</span></div><input type="range" id="rngVslideX" min="0" max="100" step="1" value="50" oninput="onLayoutChange()" style="width:100%; accent-color:var(--gold);"></div>
      <div><div style="display:flex; justify-content:space-between; font-size:11px; color:var(--muted); margin-bottom:2px;"><span>📱 COLUMN Y</span><span id="lblVslideY" style="color:var(--text); font-weight:bold;">50%</span></div><input type="range" id="rngVslideY" min="0" max="100" step="1" value="50" oninput="onLayoutChange()" style="width:100%; accent-color:var(--gold);"></div>
    </div>
  </div>
  <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:2px;">
    <span style="font-size:11px; color:var(--muted);">✋ HAND LANDMARKS</span>
    <span id="lmBadge" style="font-size:10px; padding:2px 8px; border-radius:4px; font-weight:bold; color:var(--muted); border:1px solid var(--border);">OFF — motion only</span>
  </div>
  <div style="display:flex; gap:8px;">
    <button onclick="resetLayout()" style="flex:1; padding:10px 8px; font-size:11px; font-weight:bold; background:#161b22; border-color:#2a3242; color:#ff8c41;">↩ FACE CENTER + AUTO HEIGHT</button>
  </div>
</div>

<!-- 🔊 SMART AUDIO ROUTER & PHONE SPEAKER -->
<!-- Hidden audio pipelines pre-unlocked for mobile Android Chrome -->
<audio id="tarsAudio" preload="auto" playsinline style="display:none;"></audio>
<audio id="tarsSpeech" preload="auto" playsinline style="display:none;"></audio>

<div style="background:#0d1117; border:1px solid var(--border); border-radius:12px; padding:12px; margin-bottom:14px;">
  <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
    <div style="font-size:11px; font-weight:bold; letter-spacing:1.5px; color:var(--gold); display:flex; align-items:center; gap:6px;">
      <span style="font-size:13px;">🔊</span> AUDIO OUTPUT ROUTING
    </div>
    <div id="audioRouteBadge" style="font-size:10px; background:rgba(255,215,95,0.15); color:var(--gold); padding:2px 8px; border-radius:4px; border:1px solid rgba(255,215,95,0.3); font-weight:bold;">
      PHONE + TV (BOTH)
    </div>
  </div>

  <!-- Interactive Tap to Unmute / Phone Speaker Activation Banner -->
  <button id="btnUnlockAudio" onclick="manualUnlockAudio()" style="width:100%; padding:12px 8px; margin-bottom:10px; background:rgba(255,165,55,0.18); border:1.5px solid var(--amber); color:var(--amber); font-weight:bold; border-radius:8px; font-size:12px; display:flex; align-items:center; justify-content:center; gap:6px;">
    🔊 TAP TO ACTIVATE PHONE SPEAKER
  </button>

  <div class="grid grid-3" style="margin-bottom:8px;">
    <button id="btnRoutePhone" onclick="setAudioRoute('phone')">
      📱 PHONE
    </button>
    <button id="btnRouteAux" onclick="setAudioRoute('aux')">
      📺 TV / PI
    </button>
    <button id="btnRouteBoth" class="mint" onclick="setAudioRoute('both')">
      🔊 BOTH
    </button>
  </div>
  <div class="grid grid-2" style="margin-top:6px;">
    <button onclick="testPhoneSpeaker()" class="mint" style="padding:10px; font-size:11px;">
      🔔 TEST PHONE CHIME
    </button>
    <button onclick="testPhoneVoice()" class="primary" style="padding:10px; font-size:11px;">
      🗣️ TEST PHONE VOICE
    </button>
  </div>
  <div class="grid grid-2" style="margin-top:6px;">
    <button onclick="testTvSpeaker()" class="cyan" style="padding:10px; font-size:11px;">
      📺 TEST TV / PI AUDIO
    </button>
    <button onclick="startContinuousPing()" id="btnContPing" style="padding:10px; font-size:11px; background:#161b22; border-color:#2a3242; color:#848896;">
      🔁 15s AUDIO LOOP (VOL UP)
    </button>
  </div>
  <div style="margin-top:8px;">
    <a href="/test_sound.wav" target="_blank" style="display:block; text-align:center; padding:8px; font-size:10px; background:rgba(75,215,255,0.08); border:1px solid rgba(75,215,255,0.25); border-radius:6px; color:#4bd7ff; text-decoration:none; font-weight:bold;">
      🔗 OPEN DIRECT SOUND TEST IN PHONE TAB
    </a>
  </div>
  <div style="margin-top:8px; font-size:10px; color:#ffd75f; background:rgba(255,215,95,0.08); border:1px solid rgba(255,215,95,0.25); border-radius:6px; padding:8px 10px; line-height:1.45;">
    📢 <strong>ANDROID VOLUME CHECK:</strong> Press your phone's physical <strong>Volume UP</strong> button, then tap the <strong>three dots (...)</strong> on the volume bar to make sure the <strong>Media slider 🎵</strong> is at 100% (not just ring volume)!
  </div>
</div>

<div class="chat-box">
  <div class="section-title" style="margin-top:0;">🎙️ Interactive Voice & Intelligence</div>
  <div class="mic-container">
    <button class="mic-btn" id="micBtn" onclick="toggleVoice()">🎙️</button>
    <div class="mic-hint" id="micHint">TAP TO SPEAK TO TARS</div>
  </div>
  <div class="text-query-row">
    <input type="text" id="queryInput" placeholder="Ask TARS anything (or use keyboard mic)..." onkeydown="if(event.key==='Enter') sendChat()">
    <button onclick="sendChat()">SEND</button>
  </div>
  <div class="reply-card" id="replyCard">
    <div class="reply-speaker" id="replySpeaker">TARS RESPONSE</div>
    <div class="reply-text" id="replyText"></div>
  </div>
</div>

<div class="section-title">🤖 Signature Interstellar Quips</div>
<div class="grid grid-2">
  <button class="primary" onclick="quickChat('tell me a joke')">
    <span class="icon">🤖</span> 75% HUMOR JOKE
  </button>
  <button class="primary" onclick="quickChat('what is your honesty setting')">
    <span class="icon">🛡️</span> 90% HONESTY
  </button>
  <button class="primary" onclick="quickChat('greet the judges and teachers')">
    <span class="icon">👨‍🏫</span> GREET JUDGES
  </button>
  <button class="primary" onclick="quickChat('tell us about resoenance fest')">
    <span class="icon">🏆</span> RESOENANCE 2026
  </button>
  <button class="primary" onclick="quickChat('confirmed robot colony')">
    <span class="icon">⚡</span> ROBOT COLONY
  </button>
  <button class="primary" onclick="quickChat('who built you')">
    <span class="icon">🔍</span> WHO BUILT YOU?
  </button>
</div>

<div class="section-title">😉 Eye Dynamics & Winks</div>
<div class="grid grid-3">
  <button onclick="send({cmd:'wink', side:'LEFT'})">
    <span class="icon">😉</span> WINK LEFT
  </button>
  <button onclick="send({cmd:'wink', side:'RIGHT'})">
    <span class="icon">😉</span> WINK RIGHT
  </button>
  <button onclick="send({cmd:'blink'})">
    <span class="icon">👁️</span> BLINK
  </button>
</div>

<div class="section-title">😊 Expressions</div>
<div class="grid grid-3">
  <button class="mint" onclick="send({cmd:'emotion', name:'HAPPY'})">
    <span class="icon">☀️</span> HAPPY
  </button>
  <button class="primary" onclick="send({cmd:'emotion', name:'PLAYFUL'})">
    <span class="icon">😏</span> PLAYFUL
  </button>
  <button class="mint" onclick="send({cmd:'emotion', name:'EXCITED'})">
    <span class="icon">🤩</span> EXCITED
  </button>
  <button class="cyan" onclick="send({cmd:'emotion', name:'CURIOUS'})">
    <span class="icon">🔍</span> CURIOUS
  </button>
  <button class="cyan" onclick="send({cmd:'emotion', name:'CONFIDENT'})">
    <span class="icon">🛡️</span> CONFIDENT
  </button>
  <button class="primary" onclick="send({cmd:'emotion', name:'WARM'})">
    <span class="icon">🌸</span> WARM
  </button>
</div>

<div class="section-title">🎯 Tactical Vision & Crowd Tracking</div>
<div class="grid grid-2">
  <button class="cyan" onclick="send({cmd:'target', active:true, name:'VIP GUEST', dist:'1.8m', x:0.72, y:0.42, count:1})">
    <span class="icon">👤</span> LOCK 1 SPECTATOR
  </button>
  <button class="primary" onclick="send({cmd:'target', active:true, name:'AUDIENCE CLUSTER', dist:'2.2m', x:0.68, y:0.40, count:4, secondaries:[[0.45, 0.42], [0.82, 0.38], [0.55, 0.48]]})">
    <span class="icon">👥</span> LOCK CROWD (4)
  </button>
  <button onclick="send({cmd:'target', active:false})">
    <span class="icon">❌</span> CLEAR TRACKING
  </button>
  <button class="mint" onclick="send({cmd:'event_step', dir:'next'})">
    <span class="icon">▶</span> NEXT DOSSIER
  </button>
</div>

<div style="margin-top:12px; background:#0d1117; border:1px solid var(--border); border-radius:10px; padding:10px;">
  <div style="font-size:10px; color:var(--cyan); letter-spacing:1.5px; margin-bottom:6px; font-weight:bold;">RADAR TOUCHPAD // DRAG TO STEER RETICLE & EYES</div>
  <div id="radarPad" style="height:90px; background:#161b22; border:1px dashed #222834; border-radius:6px; position:relative; touch-action:none; display:flex; align-items:center; justify-content:center; color:#555d6e; font-size:11px;">
    TOUCH & DRAG HERE
    <div id="radarDot" style="width:12px; height:12px; border-radius:50%; background:var(--cyan); position:absolute; transform:translate(-50%,-50%); display:none; box-shadow:0 0 10px var(--cyan);"></div>
  </div>
</div>

<div class="status-bar" id="footerStatus">READY // TOUCH TO DISPATCH COMMAND</div>

<script>
  // ── Browser Security & Context Check ──────────────────────────────────────
  const secBanner = document.getElementById('securityBanner');
  if (secBanner) {
    if (window.isSecureContext) {
      secBanner.style.display = 'block';
      secBanner.style.background = 'rgba(56,235,145,0.12)';
      secBanner.style.border = '1px solid rgba(56,235,145,0.35)';
      secBanner.style.color = '#38eb91';
      secBanner.innerHTML = '🔒 <strong>SECURE HTTPS CONTEXT</strong> // Browser microphone access granted.';
    } else {
      secBanner.style.display = 'block';
      secBanner.style.background = 'rgba(255,165,55,0.12)';
      secBanner.style.border = '1px solid rgba(255,165,55,0.35)';
      secBanner.style.color = '#ffa537';
      const httpsUrl = 'https://' + window.location.hostname + ':8443';
      secBanner.innerHTML = `⚠️ <strong>HTTP (INSECURE)</strong>: Browser blocks in-page mic button on unencrypted HTTP.<br>` +
        `👉 <a href="${httpsUrl}" style="color:#ffd75f; font-weight:bold; text-decoration:underline;">CLICK HERE TO OPEN SECURE HTTPS (PORT 8443)</a><br>` +
        `💡 <em>Or simply tap the query box below and press your phone keyboard's microphone icon!</em>`;
    }
  }

  // ── Web Speech API Recognition ────────────────────────────────────────────
  let recognition = null;
  let isListening = false;

  if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
    const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
    recognition = new SpeechRec();
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.lang = 'en-US';

    recognition.onstart = function() {
      isListening = true;
      document.getElementById('micBtn').classList.add('listening');
      document.getElementById('micHint').textContent = 'LISTENING... SPEAK NOW';
    };
    recognition.onresult = function(event) {
      const transcript = event.results[0][0].transcript;
      document.getElementById('queryInput').value = transcript;
      dispatchChat(transcript);
    };
    recognition.onerror = function(e) {
      stopVoice();
      const err = e.error || 'UNAVAILABLE';
      if (!window.isSecureContext) {
        document.getElementById('micHint').textContent = 'MIC BLOCKED (HTTP). USE KEYBOARD MIC OR OPEN HTTPS:8443';
      } else {
        document.getElementById('micHint').textContent = 'MIC ERROR: ' + err.toUpperCase();
      }
      setTimeout(() => { document.getElementById('micHint').textContent = 'TAP TO SPEAK TO TARS'; }, 3000);
    };
    recognition.onend = function() {
      stopVoice();
    };
  }

  function toggleVoice() {
    if (!window.isSecureContext) {
      const httpsUrl = 'https://' + window.location.hostname + ':8443';
      alert('Mobile browsers block the microphone on HTTP.\\n\\n1. Tap the query box and use your phone keyboard mic button.\\n\\nOR\\n\\n2. Open secure HTTPS at: ' + httpsUrl);
      return;
    }
    if (!recognition) {
      alert('Speech Recognition not supported in this browser. Please type or use keyboard dictation.');
      return;
    }
    if (isListening) {
      recognition.stop();
      stopVoice();
    } else {
      try {
        recognition.start();
      } catch(e) {
        recognition.stop();
        stopVoice();
      }
    }
  }

  // ── Smart Audio Pipeline & Zero-Latency Mobile Playback ───────────────────
  let sharedAudioCtx = null;
  let audioUnlocked = false;
  let lastSpokenId = 0;
  let currentAudioRoute = 'both';

  function getAudioContext() {
    if (!sharedAudioCtx) {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (AudioCtx) sharedAudioCtx = new AudioCtx();
    }
    if (sharedAudioCtx && sharedAudioCtx.state === 'suspended') {
      sharedAudioCtx.resume().catch(()=>{});
    }
    return sharedAudioCtx;
  }

  function unlockAudio() {
    if (audioUnlocked) return;
    audioUnlocked = true;
    const a = document.getElementById('tarsAudio');
    const s = document.getElementById('tarsSpeech');
    // Pre-authorize HTML5 media pipelines with silent micro-buffer
    const silentUri = 'data:audio/wav;base64,UklGRjIAAABXQVZFZm10IBIAAAABAAEAQB8AAEAfAAABAAgAAABkYXRhAgAAAAEA';
    if (a) {
      a.src = silentUri;
      a.play().then(() => { a.pause(); }).catch(()=>{});
    }
    if (s) {
      s.src = silentUri;
      s.play().then(() => { s.pause(); }).catch(()=>{});
    }
    getAudioContext();
    updateUnlockBtn(true);
  }

  function manualUnlockAudio() {
    unlockAudio();
    testPhoneSpeaker();
  }

  function updateUnlockBtn(active) {
    const btn = document.getElementById('btnUnlockAudio');
    if (btn) {
      if (active) {
        btn.style.background = 'rgba(56,235,145,0.18)';
        btn.style.borderColor = '#38eb91';
        btn.style.color = '#38eb91';
        btn.innerHTML = '✅ PHONE SPEAKER ONLINE & READY (TURN VOLUME UP)';
      } else {
        btn.style.background = 'rgba(255,165,55,0.18)';
        btn.style.borderColor = 'var(--amber)';
        btn.style.color = 'var(--amber)';
        btn.innerHTML = '🔊 TAP TO ACTIVATE PHONE SPEAKER';
      }
    }
  }

  document.addEventListener('pointerdown', unlockAudio, {once: true});
  document.addEventListener('click', unlockAudio, {once: true});
  document.addEventListener('touchstart', unlockAudio, {once: true});

  function playTone(freq, dur, delay=0) {
    setTimeout(() => {
      try {
        const ctx = getAudioContext();
        if (!ctx) return;
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = 'triangle';
        osc.frequency.setValueAtTime(freq, ctx.currentTime);
        gain.gain.setValueAtTime(0.90, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + dur);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start();
        osc.stop(ctx.currentTime + dur);
      } catch(e) {}
    }, delay);
  }

  let pingInterval = null;
  function startContinuousPing() {
    unlockAudio();
    const btn = document.getElementById('btnContPing');
    if (pingInterval) {
      clearInterval(pingInterval);
      pingInterval = null;
      if (btn) btn.textContent = '🔁 15s AUDIO LOOP (VOL UP)';
      return;
    }
    if (btn) btn.textContent = '⏹️ STOP AUDIO LOOP';
    let count = 0;
    function ping() {
      const a = document.getElementById('tarsAudio');
      if (a) {
        a.src = '/test_sound.wav?t=' + Date.now();
        a.play().catch(()=>{});
      }
      playTone(1174, 0.22, 0);
      playTone(1568, 0.22, 120);
      count++;
      if (count >= 12) {
        clearInterval(pingInterval);
        pingInterval = null;
        if (btn) btn.textContent = '🔁 15s AUDIO LOOP (VOL UP)';
      }
    }
    ping();
    pingInterval = setInterval(ping, 1200);
  }

  function testPhoneSpeaker() {
    unlockAudio();
    const a = document.getElementById('tarsAudio');
    if (a) {
      a.src = '/test_sound.wav?t=' + Date.now();
      const p = a.play();
      if (p) {
        p.then(() => {
          updateUnlockBtn(true);
        }).catch(err => {
          alert('Phone audio was blocked or muted.\\n\\nPlease make sure your phone MEDIA volume is turned UP!');
        });
      }
    }
    // High-penetration backup acoustic chime
    playTone(1046, 0.25, 0);
    playTone(1318, 0.25, 120);
    playTone(1568, 0.35, 240);

    const fs = document.getElementById('footerStatus');
    fs.textContent = '🔊 PHONE TEST CHIME DISPATCHED (CHECK MEDIA VOLUME)';
  }

  function testPhoneVoice() {
    unlockAudio();
    const s = document.getElementById('tarsSpeech');
    if (s) {
      s.src = '/api/voice.wav?cue=greeting&t=' + Date.now();
      s.play().then(() => {
        updateUnlockBtn(true);
      }).catch(err => {
        alert('Voice audio playback blocked. Tap the screen once and ensure Media volume is up.');
      });
    }
    const card = document.getElementById('replyCard');
    const rep = document.getElementById('replyText');
    if (card && rep) {
      rep.textContent = 'Greetings. I am T.A.R.S. Welcome to RESOENANCE 2026.';
      card.classList.add('visible');
    }
    const fs = document.getElementById('footerStatus');
    fs.textContent = '🗣️ TARS ROBOTIC VOICE PLAYING ON PHONE';
  }

  function testTvSpeaker() {
    if (navigator.vibrate) navigator.vibrate(40);
    send({cmd: 'say', clip: 'greeting'});
    const fs = document.getElementById('footerStatus');
    fs.textContent = 'DISPATCHED GREETING TO TV SPEAKER';
  }

  function playPhoneVoice(text, cue) {
    if (!text && !cue) return;
    unlockAudio();
    const s = document.getElementById('tarsSpeech');
    if (s) {
      const url = cue ? ('/api/voice.wav?cue=' + encodeURIComponent(cue) + '&t=' + Date.now())
                      : ('/api/voice.wav?t=' + Date.now());
      s.src = url;
      s.play().catch(e => {
        console.warn('Voice play error:', e);
      });
    }
  }

  function quickChat(text) {
    unlockAudio();
    const qMap = {
      'tell me a joke': 'humor_75',
      'what is your honesty setting': 'humor_75',
      'greet the judges and teachers': 'greeting',
      'tell us about resoenance fest': 'celebrate',
      'confirmed robot colony': 'colony',
      'who built you': 'sensors_nominal'
    };
    const cue = qMap[text] || '';
    if (currentAudioRoute === 'phone' || currentAudioRoute === 'both') {
      if (cue) {
        const s = document.getElementById('tarsSpeech');
        if (s) {
          s.src = '/api/voice.wav?cue=' + encodeURIComponent(cue) + '&t=' + Date.now();
          s.play().catch(()=>{});
        }
      }
    }
    document.getElementById('queryInput').value = text;
    dispatchChat(text);
  }

  function sendChat() {
    const q = document.getElementById('queryInput').value.trim();
    if (q) dispatchChat(q);
  }

  function dispatchChat(query) {
    unlockAudio();
    if (navigator.vibrate) navigator.vibrate(40);
    const fs = document.getElementById('footerStatus');
    fs.textContent = 'COMPUTING // ' + query.toUpperCase();

    fetch('/api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({query: query})
    }).then(r => r.json()).then(data => {
      fs.textContent = 'TARS RESPONDED // READY';
      const card = document.getElementById('replyCard');
      const rep = document.getElementById('replyText');
      if (card && rep) {
        rep.textContent = data.reply;
        card.classList.add('visible');
      }

      lastSpokenId = data.speech_id || (lastSpokenId + 1);
      if (currentAudioRoute === 'phone' || currentAudioRoute === 'both') {
        const s = document.getElementById('tarsSpeech');
        if (s && data.audio_url) {
          s.src = data.audio_url;
          s.play().catch(e => {
            console.warn('Chat voice play blocked:', e);
          });
        }
      }
    }).catch(e => {
      fs.textContent = 'TRANSMISSION ERROR';
    });
  }

  function setPlaylistMode(mode) {
    if (navigator.vibrate) navigator.vibrate(30);
    fetch('/api/playlist', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mode: mode})
    }).then(() => {
      const bm = document.getElementById('btnModeManual');
      const ba = document.getElementById('btnModeAuto');
      const bg = document.getElementById('modeBadge');
      if (bm && ba && bg) {
        bm.className = (mode === 'manual') ? 'cyan' : '';
        ba.className = (mode === 'auto') ? 'mint' : '';
        bg.textContent = (mode === 'manual') ? 'MANUAL PILOT' : 'AUTO CYCLE';
        bg.style.color = (mode === 'manual') ? '#4bd7ff' : '#38eb91';
        bg.style.background = (mode === 'manual') ? 'rgba(75,215,255,0.15)' : 'rgba(56,235,145,0.15)';
      }
    }).catch(()=>{});
  }

  // ── Sightline & Camera Calibration Engine ──────────────────────────────
  // ── Floating Sci-Fi Toast Notification ──────────────────────────────────
  function showToast(msg, isSuccess=true) {
    const t = document.getElementById('toastAlert');
    if (!t) return;
    t.textContent = msg;
    t.style.display = 'block';
    t.style.background = isSuccess ? 'rgba(56,235,145,0.92)' : 'rgba(255,95,95,0.92)';
    t.style.color = isSuccess ? '#0d1117' : '#ffffff';
    t.style.border = isSuccess ? '1px solid #38eb91' : '1px solid #ff5f5f';
    t.style.opacity = '1';
    clearTimeout(t._timer);
    t._timer = setTimeout(() => {
      t.style.opacity = '0';
      setTimeout(() => { t.style.display = 'none'; }, 300);
    }, 2500);
  }

  // ── Sightline & Camera Calibration Engine ──────────────────────────────
  let calib = {
    mirror_x: false,
    mirror_gaze_x: false,
    mirror_gesture_x: true,
    invert_y: false,
    camera_position: "CENTER",
    monitor_diag: 43,
    couch_dist: 2.2,
    sens_x: 2.4,
    sens_y: 2.2,
    gesture_sens: 1.0,
    sens_left: 1.0,
    sens_right: 1.0,
    sens_up: 1.0,
    sens_down: 1.0,
    offset_x: 0.0,
    offset_y: 0.0
  };

  function sendCalib() {
    fetch('/api/calibrate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(calib)
    }).catch(()=>{});
  }

  function updateMirrorButtons() {
    const bg = document.getElementById('btnMirrorGaze');
    if (bg) {
      bg.textContent = '👁️ GAZE MIRROR: ' + (calib.mirror_gaze_x ? 'ON' : 'OFF');
      bg.className = calib.mirror_gaze_x ? 'primary' : '';
    }
    const bmc = document.getElementById('btnMirrorCamera');
    if (bmc) {
      bmc.textContent = '🪞 GAZE: ' + (calib.mirror_gaze_x ? 'MIRRORED' : 'DIRECT');
      bmc.className = calib.mirror_gaze_x ? 'primary' : '';
    }
    const bs = document.getElementById('btnMirrorGesture');
    if (bs) {
      bs.textContent = '👋 SWIPE MIRROR: ' + (calib.mirror_gesture_x ? 'FLIPPED' : 'NATURAL');
      bs.className = calib.mirror_gesture_x ? 'cyan' : '';
    }
    const bmgq = document.getElementById('btnMirrorGestureQuick');
    if (bmgq) {
      bmgq.textContent = '✋ SWIPE: ' + (calib.mirror_gesture_x ? 'FLIPPED' : 'NATURAL');
      bmgq.className = calib.mirror_gesture_x ? 'cyan' : '';
    }
    const img = document.getElementById('liveStream');
    if (img) {
      img.style.transform = 'none'; // Python renders mirrored feed directly with non-reversed readable text
    }
  }

  function updateMirrorUI(camMirror, gestMirror) {
    if (camMirror !== undefined) {
      calib.mirror_gaze_x = !!camMirror;
      calib.mirror_x = !!camMirror;
    }
    if (gestMirror !== undefined) {
      calib.mirror_gesture_x = !!gestMirror;
    }
    updateMirrorButtons();
  }

  function toggleMirrorGaze() {
    if (navigator.vibrate) navigator.vibrate(30);
    const next = !calib.mirror_gaze_x;
    calib.mirror_gaze_x = next;
    calib.mirror_x = next;
    updateMirrorButtons();
    if (typeof updateMirrorUI === 'function') updateMirrorUI(next, undefined);
    const fs = document.getElementById('footerStatus');
    fs.textContent = 'GAZE MIRROR: ' + (next ? 'FLIPPED (ROBOT TRACKS INVERTED)' : 'NORMAL (DIRECT TRACKING)');
    showToast('👁️ GAZE TRACKING: ' + (next ? 'MIRRORED' : 'DIRECT'));
    fetch('/api/calibrate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mirror_x: next, mirror_gaze_x: next})
    }).catch(()=>{});
  }

  function toggleMirrorGesture() {
    if (navigator.vibrate) navigator.vibrate(30);
    const next = !calib.mirror_gesture_x;
    calib.mirror_gesture_x = next;
    updateMirrorButtons();
    if (typeof updateMirrorUI === 'function') updateMirrorUI(undefined, next);
    const fs = document.getElementById('footerStatus');
    fs.textContent = 'SWIPE MIRROR: ' + (next ? 'FLIPPED (REVERSE SWIPE)' : 'NATURAL (FORWARD SWIPE)');
    showToast('👋 SWIPE GESTURE: ' + (next ? 'FLIPPED' : 'NATURAL'));
    fetch('/api/calibrate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mirror_gesture_x: next})
    }).catch(()=>{});
  }

  function toggleCameraMirror() {
    toggleMirrorGaze();
  }

  // 🎛️ Layout Studio — live move/resize, persists via /api/calibrate
  function layoutPost(partial, toast) {
    if (navigator.vibrate) navigator.vibrate(20);
    fetch('/api/calibrate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(partial)
    }).catch(()=>{});
    if (toast) showToast(toast);
  }
  function onLayoutChange() {
    const fx = +document.getElementById('rngFaceX').value;
    const fy = +document.getElementById('rngFaceY').value;
    const fs = +document.getElementById('rngFaceSize').value;
    const ps = +document.getElementById('rngPipSize').value;
    const px = +document.getElementById('rngPipX').value;
    const py = +document.getElementById('rngPipY').value;
    const cx = +document.getElementById('rngCropX').value;
    const cy = +document.getElementById('rngCropY').value;
    const cw = +document.getElementById('rngCropW').value;
    const ch = +document.getElementById('rngCropH').value;
    const zm = +document.getElementById('rngZoom').value;
    const sx = +document.getElementById('rngSlideX').value;
    const sy = +document.getElementById('rngSlideY').value;
    const vs = +document.getElementById('rngVslideSize').value;
    const vx = +document.getElementById('rngVslideX').value;
    const vy = +document.getElementById('rngVslideY').value;
    document.getElementById('lblVslideSize').textContent = vs + '%';
    document.getElementById('lblVslideX').textContent = vx + '%';
    document.getElementById('lblVslideY').textContent = vy + '%';
    document.getElementById('lblFaceX').textContent = fx + '%';
    document.getElementById('lblFaceY').textContent = fy + '%';
    document.getElementById('lblFaceSize').textContent = fs + '%';
    document.getElementById('lblPipSize').textContent = ps + '%';
    document.getElementById('lblPipX').textContent = px + '%';
    document.getElementById('lblPipY').textContent = py + '%';
    document.getElementById('lblCropX').textContent = cx;
    document.getElementById('lblCropY').textContent = cy;
    document.getElementById('lblCropW').textContent = cw;
    document.getElementById('lblCropH').textContent = ch;
    document.getElementById('lblZoom').textContent = zm + '%';
    document.getElementById('lblSlideX').textContent = sx + '%';
    document.getElementById('lblSlideY').textContent = sy + '%';
    layoutPost({face_cx: fx / 100, face_cy: fy / 100, face_size: fs / 100,
      pip_scale: ps / 100, pip_x: px / 100, pip_y: py / 100,
      pip_crop: [cx / 100, cy / 100, cw / 100, ch / 100],
      slide_zoom: zm / 100, slide_x: sx / 100, slide_y: sy / 100,
      vslide_scale: vs / 100, vslide_x: vx / 100, vslide_y: vy / 100});
  }
  function setPipPos(corner) {
    ['TL','TR','BL','BR'].forEach(c => {
      const b = document.getElementById('pip' + c);
      if (b) b.className = (c === corner) ? 'mint' : '';
    });
    const bf = document.getElementById('pipFREE');
    if (bf) bf.className = (corner === 'FREE') ? 'mint' : '';
    layoutPost({pip_pos: corner}, '📹 CAMERA BOX → ' + corner);
  }
  function resetLayout() {
    document.getElementById('rngFaceX').value = 50;
    document.getElementById('rngFaceY').value = 44;
    document.getElementById('rngFaceSize').value = 100;
    document.getElementById('rngPipX').value = 100;
    document.getElementById('rngPipY').value = 100;
    document.getElementById('rngPipSize').value = 100;
    document.getElementById('rngCropX').value = 0;
    document.getElementById('rngCropY').value = 0;
    document.getElementById('rngCropW').value = 100;
    document.getElementById('rngCropH').value = 100;
    document.getElementById('rngZoom').value = 100;
    document.getElementById('lblFaceX').textContent = '50%';
    document.getElementById('lblFaceY').textContent = 'AUTO';
    document.getElementById('lblFaceSize').textContent = '100%';
    document.getElementById('rngSlideX').value = 50;
    document.getElementById('rngSlideY').value = 50;
    document.getElementById('rngZoom').value = 100;
    document.getElementById('lblSlideX').textContent = '50%';
    document.getElementById('lblSlideY').textContent = '50%';
    document.getElementById('lblZoom').textContent = '100%';
    document.getElementById('rngVslideSize').value = 90;
    document.getElementById('rngVslideX').value = 50;
    document.getElementById('rngVslideY').value = 50;
    vslideOn = false; updateVslideUI();
    layoutPost({face_cx: 0.5, face_cy: null, face_size: 1.0, pip_pos: 'BR',
      pip_x: 1.0, pip_y: 1.0, pip_scale: 1.0, pip_crop: [0, 0, 1, 1],
      slide_zoom: 1.0, slide_x: 0.5, slide_y: 0.5,
      vslide_mode: false, vslide_scale: 0.9, vslide_x: 0.5, vslide_y: 0.5},
      '↩ LAYOUT RESET');
  }
  let vslideOn = false;
  function toggleVslide() {
    vslideOn = !vslideOn;
    updateVslideUI();
    layoutPost({vslide_mode: vslideOn}, vslideOn ? '📱 PORTRAIT COLUMN ON' : '🖥️ FULL SLIDES');
  }
  function setVslideFit(mode) {
    ['FIT','STRETCH','FILL'].forEach(m => {
      const b = document.getElementById('vfit' + m);
      if (b) b.className = (m === mode) ? 'mint' : '';
    });
    layoutPost({vslide_fit: mode}, '📱 FIT → ' + mode);
  }
  function setVslideRot(deg) {
    [0, 90, 180, 270].forEach(d => {
      const b = document.getElementById('vrot' + d);
      if (b) b.className = (d === deg) ? 'mint' : '';
    });
    layoutPost({vslide_rot: deg}, '📱 ROTATE → ' + deg + '°');
  }
  function updateVslideUI() {
    const b = document.getElementById('btnVslide');
    if (b) {
      b.textContent = vslideOn ? '📱 PORTRAIT SLIDES: ON' : '📱 PORTRAIT SLIDES: OFF';
      b.className = vslideOn ? 'mint' : '';
    }
    const ctl = document.getElementById('vslideCtl');
    if (ctl) ctl.style.display = vslideOn ? 'block' : 'none';
  }
  function syncLayoutUI(c) {
    if (!c) return;
    const set = (id, v) => {
      const r = document.getElementById(id);
      if (r && document.activeElement !== r) r.value = v;
    };
    if (c.face_cx !== undefined) { set('rngFaceX', c.face_cx * 100); calib_face_lbl('lblFaceX', c.face_cx * 100, '%'); }
    if (c.face_cy !== undefined && c.face_cy !== null) { set('rngFaceY', c.face_cy * 100); calib_face_lbl('lblFaceY', c.face_cy * 100, '%'); }
    if (c.face_size !== undefined) { set('rngFaceSize', c.face_size * 100); calib_face_lbl('lblFaceSize', c.face_size * 100, '%'); }
    if (c.pip_scale !== undefined) { set('rngPipSize', c.pip_scale * 100); calib_face_lbl('lblPipSize', c.pip_scale * 100, '%'); }
    if (c.pip_x !== undefined) { set('rngPipX', c.pip_x * 100); calib_face_lbl('lblPipX', c.pip_x * 100, '%'); }
    if (c.pip_y !== undefined) { set('rngPipY', c.pip_y * 100); calib_face_lbl('lblPipY', c.pip_y * 100, '%'); }
    if (c.slide_zoom !== undefined) { set('rngZoom', c.slide_zoom * 100); calib_face_lbl('lblZoom', c.slide_zoom * 100, '%'); }
    if (c.slide_x !== undefined) { set('rngSlideX', c.slide_x * 100); calib_face_lbl('lblSlideX', c.slide_x * 100, '%'); }
    if (c.slide_y !== undefined) { set('rngSlideY', c.slide_y * 100); calib_face_lbl('lblSlideY', c.slide_y * 100, '%'); }
    if (c.vslide_mode !== undefined) { vslideOn = !!c.vslide_mode; updateVslideUI(); }
    if (c.vslide_fit !== undefined) {
      ['FIT','STRETCH','FILL'].forEach(m => {
        const b = document.getElementById('vfit' + m);
        if (b) b.className = (m === c.vslide_fit) ? 'mint' : '';
      });
    }
    if (c.vslide_rot !== undefined) {
      [0, 90, 180, 270].forEach(d => {
        const b = document.getElementById('vrot' + d);
        if (b) b.className = (d === c.vslide_rot) ? 'mint' : '';
      });
    }
    if (c.vslide_scale !== undefined) { set('rngVslideSize', c.vslide_scale * 100); calib_face_lbl('lblVslideSize', c.vslide_scale * 100, '%'); }
    if (c.vslide_x !== undefined) { set('rngVslideX', c.vslide_x * 100); calib_face_lbl('lblVslideX', c.vslide_x * 100, '%'); }
    if (c.vslide_y !== undefined) { set('rngVslideY', c.vslide_y * 100); calib_face_lbl('lblVslideY', c.vslide_y * 100, '%'); }
    if (c.pip_pos !== undefined) {
      ['TL','TR','BL','BR'].forEach(x => {
        const b = document.getElementById('pip' + x);
        if (b) b.className = (x === c.pip_pos) ? 'mint' : '';
      });
      const bf = document.getElementById('pipFREE');
      if (bf) bf.className = (c.pip_pos === 'FREE') ? 'mint' : '';
    }
  }
  function calib_face_lbl(id, v, suffix) {
    const l = document.getElementById(id);
    if (l) l.textContent = Math.round(v) + suffix;
  }

  function toggleGestureMirror() {
    toggleMirrorGesture();
  }

  function toggleMirrorX() {
    toggleMirrorGaze();
  }

  function toggleInvertY() {
    if (navigator.vibrate) navigator.vibrate(30);
    calib.invert_y = !calib.invert_y;
    const b = document.getElementById('btnInvertY');
    if (b) {
      b.textContent = '↕️ INVERT Y: ' + (calib.invert_y ? 'ON' : 'OFF');
      b.className = calib.invert_y ? 'primary' : '';
    }
    const fs = document.getElementById('footerStatus');
    fs.textContent = 'INVERT Y: ' + (calib.invert_y ? 'ENABLED' : 'DISABLED');
    showToast('↕️ INVERT Y: ' + (calib.invert_y ? 'ON' : 'OFF'));
    sendCalib();
  }

  function toggleCamPos() {
    if (navigator.vibrate) navigator.vibrate(30);
    const positions = ['CENTER', 'TOP', 'BOTTOM', 'LEFT', 'RIGHT'];
    let cur = (calib.camera_position || 'CENTER').toUpperCase();
    if (cur === 'BELOW') cur = 'BOTTOM';
    if (cur === 'ABOVE') cur = 'TOP';
    if (cur === 'MIDDLE') cur = 'CENTER';
    let idx = positions.indexOf(cur);
    if (idx === -1) idx = 0;
    calib.camera_position = positions[(idx + 1) % positions.length];
    const b = document.getElementById('btnCamPos');
    if (b) {
      b.textContent = '📍 CAM: ' + calib.camera_position;
      b.className = (calib.camera_position === 'CENTER') ? 'primary' : 'cyan';
    }
    const fs = document.getElementById('footerStatus');
    fs.textContent = 'CAMERA MOUNT: ' + calib.camera_position + ' (TAP TO CYCLE)';
    showToast('📍 CAMERA POSITION: ' + calib.camera_position);
    sendCalib();
  }

  function setMonitorSize(diag) {
    if (navigator.vibrate) navigator.vibrate(25);
    calib.monitor_diag = diag;
    [24, 32, 43, 55, 65].forEach(d => {
      const b = document.getElementById('btnDiag' + d);
      if (b) b.className = (d === diag) ? 'cyan' : '';
    });
    const fs = document.getElementById('footerStatus');
    fs.textContent = 'MONITOR SIZE CALIBRATED: ' + diag + ' INCHES';
    showToast('📺 MONITOR SIZE: ' + diag + ' INCHES');
    sendCalib();
  }

  function onCalibSliderChange() {
    calib.couch_dist = parseFloat(document.getElementById('rngDist').value);
    calib.sens_x = parseFloat(document.getElementById('rngSensX').value);
    calib.sens_y = parseFloat(document.getElementById('rngSensY').value);
    const rgs = document.getElementById('rngGestureSens');
    if (rgs) calib.gesture_sens = parseFloat(rgs.value);

    const rsl = document.getElementById('rngSensLeft');
    if (rsl) calib.sens_left = parseFloat(rsl.value);
    const rsr = document.getElementById('rngSensRight');
    if (rsr) calib.sens_right = parseFloat(rsr.value);
    const rsu = document.getElementById('rngSensUp');
    if (rsu) calib.sens_up = parseFloat(rsu.value);
    const rsd = document.getElementById('rngSensDown');
    if (rsd) calib.sens_down = parseFloat(rsd.value);

    const rgcd = document.getElementById('rngGestureCooldown');
    if (rgcd) {
      calib.gesture_cooldown = parseFloat(rgcd.value);
      currentCooldown = calib.gesture_cooldown;
      const icd = document.getElementById('inpCooldown');
      if (icd && document.activeElement !== icd) icd.value = currentCooldown.toFixed(1);
      const lcd = document.getElementById('lblGestureCooldown');
      if (lcd) lcd.textContent = currentCooldown.toFixed(1) + 's';
    }

    calib.offset_x = parseFloat(document.getElementById('rngOffX').value);
    calib.offset_y = parseFloat(document.getElementById('rngOffY').value);

    document.getElementById('lblCouchDist').textContent = calib.couch_dist.toFixed(1) + 'm';
    document.getElementById('lblSensX').textContent = calib.sens_x.toFixed(1) + 'x';
    document.getElementById('lblSensY').textContent = calib.sens_y.toFixed(1) + 'x';
    const lgs = document.getElementById('lblGestureSens');
    if (lgs && calib.gesture_sens) lgs.textContent = calib.gesture_sens.toFixed(1) + 'x';

    const lsl = document.getElementById('lblSensLeft');
    if (lsl && calib.sens_left) lsl.textContent = calib.sens_left.toFixed(1) + 'x';
    const lsr = document.getElementById('lblSensRight');
    if (lsr && calib.sens_right) lsr.textContent = calib.sens_right.toFixed(1) + 'x';
    const lsu = document.getElementById('lblSensUp');
    if (lsu && calib.sens_up) lsu.textContent = calib.sens_up.toFixed(1) + 'x';
    const lsd = document.getElementById('lblSensDown');
    if (lsd && calib.sens_down) lsd.textContent = calib.sens_down.toFixed(1) + 'x';

    document.getElementById('lblOffX').textContent = (calib.offset_x >= 0 ? '+' : '') + calib.offset_x.toFixed(2);
    document.getElementById('lblOffY').textContent = (calib.offset_y >= 0 ? '+' : '') + calib.offset_y.toFixed(2);

    sendCalib();
  }

  function saveAllSettings() {
    if (navigator.vibrate) navigator.vibrate([40, 30, 40]);
    const fs = document.getElementById('footerStatus');
    fs.textContent = 'PERSISTING SIGHTLINE & GESTURE SETTINGS TO DISK...';
    fetch('/api/calibrate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(Object.assign({}, calib, {save: true}))
    }).then(r => r.json()).then(() => {
      fs.textContent = 'SETTINGS PERSISTED TO DISK // READY';
      showToast('✅ ALL SETTINGS SAVED PERMANENTLY', true);
    }).catch(() => {
      fs.textContent = 'ERROR SAVING SETTINGS';
      showToast('❌ FAILED TO SAVE SETTINGS', false);
    });
  }

  function confirmResetCalibration() {
    if (confirm('Reset all sightline calibration, gesture sensitivity, and camera orientation to factory defaults?')) {
      resetCalibration();
    }
  }

  function resetCalibration() {
    if (navigator.vibrate) navigator.vibrate(60);
    calib = {
      mirror_x: false,
      mirror_gaze_x: false,
      mirror_gesture_x: true,
      invert_y: false,
      camera_position: "CENTER",
      monitor_diag: 43,
      couch_dist: 2.2,
      sens_x: 2.4,
      sens_y: 2.2,
      gesture_sens: 1.0,
      gesture_cooldown: 1.0,
      sens_left: 1.0,
      sens_right: 1.0,
      sens_up: 1.0,
      sens_down: 1.0,
      offset_x: 0.0,
      offset_y: 0.0
    };
    document.getElementById('rngDist').value = 2.2;
    document.getElementById('rngSensX').value = 2.4;
    document.getElementById('rngSensY').value = 2.2;
    const rgs = document.getElementById('rngGestureSens');
    if (rgs) rgs.value = 1.0;
    const rsl = document.getElementById('rngSensLeft');
    if (rsl) rsl.value = 1.0;
    const rsr = document.getElementById('rngSensRight');
    if (rsr) rsr.value = 1.0;
    const rsu = document.getElementById('rngSensUp');
    if (rsu) rsu.value = 1.0;
    const rsd = document.getElementById('rngSensDown');
    if (rsd) rsd.value = 1.0;

    const rgcd = document.getElementById('rngGestureCooldown');
    if (rgcd) rgcd.value = 1.0;
    const lcd = document.getElementById('lblGestureCooldown');
    if (lcd) lcd.textContent = '1.0s';
    const icd = document.getElementById('inpCooldown');
    if (icd) icd.value = '1.0';
    currentCooldown = 1.0;

    document.getElementById('rngOffX').value = 0.0;
    document.getElementById('rngOffY').value = 0.0;
    document.getElementById('lblCouchDist').textContent = '2.2m';
    document.getElementById('lblSensX').textContent = '2.4x';
    document.getElementById('lblSensY').textContent = '2.2x';
    const lgs = document.getElementById('lblGestureSens');
    if (lgs) lgs.textContent = '1.0x';
    const lsl = document.getElementById('lblSensLeft');
    if (lsl) lsl.textContent = '1.0x';
    const lsr = document.getElementById('lblSensRight');
    if (lsr) lsr.textContent = '1.0x';
    const lsu = document.getElementById('lblSensUp');
    if (lsu) lsu.textContent = '1.0x';
    const lsd = document.getElementById('lblSensDown');
    if (lsd) lsd.textContent = '1.0x';

    document.getElementById('lblOffX').textContent = '0.00';
    document.getElementById('lblOffY').textContent = '0.00';
    updateMirrorButtons();
    document.getElementById('btnInvertY').textContent = '↕️ INVERT Y: OFF';
    document.getElementById('btnInvertY').className = '';
    document.getElementById('btnCamPos').textContent = '📍 CAM: CENTER';
    document.getElementById('btnCamPos').className = 'primary';
    setMonitorSize(43);
    sendCalib();
    showToast('🔄 SETTINGS RESET TO FACTORY DEFAULTS', true);
  }

  // ── TV Display & Slides Controls ───────────────────────────────────────
  function setDisplayMode(mode) {
    if (navigator.vibrate) navigator.vibrate(30);
    fetch('/api/display_control', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({cmd: 'display_mode', mode: mode})
    }).then(() => {
      const bf = document.getElementById('btnShowFace');
      const bs = document.getElementById('btnShowSlides');
      const ba = document.getElementById('btnModeAuto');
      const bg = document.getElementById('displayModeBadge');
      if (bf && bs && ba && bg) {
        bf.className = (mode === 'face') ? 'cyan' : '';
        bs.className = (mode === 'slides') ? 'primary' : '';
        ba.className = (mode === 'auto') ? 'mint' : '';
        if (mode === 'face') {
          bg.textContent = 'ROBOT FACE';
          bg.style.color = '#4bd7ff';
        } else if (mode === 'slides') {
          bg.textContent = 'EVENT SLIDES';
          bg.style.color = '#ffa537';
        } else {
          bg.textContent = 'AUTO CYCLE';
          bg.style.color = '#38eb91';
        }
      }
    });
  }

  function selectSlide(idx) {
    if (navigator.vibrate) navigator.vibrate(30);
    fetch('/api/display_control', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({cmd: 'set_slide', index: idx})
    }).then(() => {
      const bg = document.getElementById('displayModeBadge');
      if (bg) {
        bg.textContent = 'SLIDE #' + (idx + 1);
        bg.style.color = '#ffa537';
      }
      const bs = document.getElementById('btnShowSlides');
      const bf = document.getElementById('btnShowFace');
      if (bs) bs.className = 'primary';
      if (bf) bf.className = '';
    });
  }

  function stepSlide(dir) {
    if (navigator.vibrate) navigator.vibrate(25);
    fetch('/api/display_control', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({cmd: 'event_step', dir: dir})
    }).then(() => {
      const fs = document.getElementById('footerStatus');
      fs.textContent = 'SLIDE STEPPED: ' + dir.toUpperCase();
      const bs = document.getElementById('btnShowSlides');
      const bf = document.getElementById('btnShowFace');
      if (bs) bs.className = 'primary';
      if (bf) bf.className = '';
    }).catch(()=>{});
  }

  let currentAutoCycle = true;
  let currentSlideDuration = 8.0;
  let currentGestureMode = "4_WAY";

  function updateAutoCycleUI(enabled, dur) {
    if (enabled !== undefined) currentAutoCycle = !!enabled;
    const b = document.getElementById('btnToggleAutoCycle');
    if (b) {
      b.textContent = currentAutoCycle ? 'AUTO: ON' : 'AUTO: OFF';
      b.className = currentAutoCycle ? 'mint' : '';
      b.style.borderColor = currentAutoCycle ? 'var(--mint)' : '#2a3242';
      b.style.color = currentAutoCycle ? 'var(--mint)' : '#848896';
    }
    if (dur !== undefined) {
      currentSlideDuration = parseFloat(dur);
      const inp = document.getElementById('inpSlideDuration');
      if (inp && document.activeElement !== inp) {
        inp.value = currentSlideDuration.toFixed(1);
      }
    }
  }

  function toggleAutoCycle() {
    if (navigator.vibrate) navigator.vibrate(30);
    const next = !currentAutoCycle;
    updateAutoCycleUI(next);
    fetch('/api/display_control', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({cmd: 'auto_cycle', enabled: next})
    }).then(r => r.json()).then(data => {
      if (data && data.auto_cycle_enabled !== undefined) {
        updateAutoCycleUI(data.auto_cycle_enabled, data.event_display_time);
      }
      const fs = document.getElementById('footerStatus');
      fs.textContent = 'AUTO SLIDE CYCLE: ' + (currentAutoCycle ? 'ACTIVATED' : 'PAUSED');
    }).catch(()=>{});
  }

  function adjustSlideDuration(delta) {
    if (navigator.vibrate) navigator.vibrate(20);
    let dur = Math.max(1.0, Math.min(120.0, currentSlideDuration + delta));
    onSlideDurationInput(dur);
  }

  function onSlideDurationInput(val) {
    let dur = parseFloat(val);
    if (isNaN(dur) || dur < 1.0) dur = 8.0;
    currentSlideDuration = dur;
    const inp = document.getElementById('inpSlideDuration');
    if (inp) inp.value = currentSlideDuration.toFixed(1);
    fetch('/api/display_control', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({cmd: 'slide_duration', duration: currentSlideDuration})
    }).then(r => r.json()).then(data => {
      const fs = document.getElementById('footerStatus');
      fs.textContent = 'SLIDE DURATION SET: ' + currentSlideDuration.toFixed(1) + 's';
    }).catch(()=>{});
  }


  let currentGestureState = true;
  function updateGestureButton(enabled) {
    currentGestureState = !!enabled;
    const b = document.getElementById('btnToggleGesture');
    if (b) {
      b.textContent = currentGestureState ? '[GESTURE: ON]' : '[GESTURE: OFF]';
      b.className = currentGestureState ? 'mint' : '';
      b.style.borderColor = currentGestureState ? 'var(--mint)' : '#2a3242';
      b.style.color = currentGestureState ? 'var(--mint)' : '#848896';
    }
  }

  function toggleGestureSwipe() {
    if (navigator.vibrate) navigator.vibrate(30);
    updateGestureButton(!currentGestureState);
    fetch('/api/display_control', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({cmd: 'toggle_gesture'})
    }).then(r => r.json()).then(data => {
      if (data && data.gesture_enabled !== undefined) updateGestureButton(data.gesture_enabled);
      const fs = document.getElementById('footerStatus');
      fs.textContent = 'OPTICAL HAND GESTURE SWIPE: ' + (currentGestureState ? 'ACTIVATED' : 'MUTED');
    }).catch(()=>{});
  }

  // currentGestureMode declared above at initialization block
  function updateGestureModeUI(mode) {
    currentGestureMode = mode || 'HORIZONTAL_SWIPE';
    const b = document.getElementById('btnGestureMode');
    if (b) {
      const isHoriz = (currentGestureMode === 'HORIZONTAL_SWIPE');
      b.textContent = isHoriz ? '[MODE: HORIZONTAL SLIDES]' : '[MODE: 4-WAY GESTURE]';
      b.className = isHoriz ? 'primary' : 'mint';
    }
  }

  function toggleGestureMode() {
    if (navigator.vibrate) navigator.vibrate(30);
    const nextMode = (currentGestureMode === 'HORIZONTAL_SWIPE') ? '4_WAY' : 'HORIZONTAL_SWIPE';
    updateGestureModeUI(nextMode);
    fetch('/api/display_control', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({cmd: 'gesture_mode', mode: nextMode})
    }).then(r => r.json()).then(data => {
      if (data && data.gesture_mode) updateGestureModeUI(data.gesture_mode);
      const fs = document.getElementById('footerStatus');
      fs.textContent = 'GESTURE RECOGNITION MODE: ' + (currentGestureMode === 'HORIZONTAL_SWIPE' ? 'HORIZONTAL SLIDE SWIPING' : '4-WAY (UP/DOWN/LEFT/RIGHT)');
    }).catch(()=>{});
  }

  let currentSwipeAnim = true;
  function updateSwipeAnimButton(enabled) {
    if (enabled !== undefined) currentSwipeAnim = !!enabled;
    const b = document.getElementById('btnToggleSwipeAnim');
    if (b) {
      b.textContent = currentSwipeAnim ? '[ANIM: ON]' : '[ANIM: OFF]';
      b.className = currentSwipeAnim ? 'mint' : '';
      b.style.borderColor = currentSwipeAnim ? 'var(--mint)' : '#2a3242';
      b.style.color = currentSwipeAnim ? 'var(--mint)' : '#848896';
    }
  }

  function toggleSwipeAnim() {
    if (navigator.vibrate) navigator.vibrate(30);
    const next = !currentSwipeAnim;
    updateSwipeAnimButton(next);
    fetch('/api/display_control', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({cmd: 'toggle_swipe_anim'})
    }).then(r => r.json()).then(data => {
      if (data && data.swipe_anim_enabled !== undefined) updateSwipeAnimButton(data.swipe_anim_enabled);
      const fs = document.getElementById('footerStatus');
      fs.textContent = 'SWIPE MOTION ANIMATION: ' + (currentSwipeAnim ? 'ENABLED' : 'MUTED');
      showToast('📺 SWIPE ANIMATION: ' + (currentSwipeAnim ? 'ON' : 'OFF'), currentSwipeAnim);
    }).catch(()=>{});
  }

  let currentCooldown = 1.0;
  function updateCooldownUI(val) {
    if (val === undefined) return;
    currentCooldown = Math.max(0.3, Math.min(3.0, parseFloat(val) || 1.0));
    const inp = document.getElementById('inpCooldown');
    if (inp && document.activeElement !== inp) inp.value = currentCooldown.toFixed(1);
    const rng = document.getElementById('rngGestureCooldown');
    if (rng && document.activeElement !== rng) rng.value = currentCooldown.toFixed(1);
    const lbl = document.getElementById('lblGestureCooldown');
    if (lbl) lbl.textContent = currentCooldown.toFixed(1) + 's';
    calib.gesture_cooldown = currentCooldown;
  }

  function adjustCooldown(delta) {
    if (navigator.vibrate) navigator.vibrate(20);
    const next = Math.round((currentCooldown + delta) * 10) / 10;
    onCooldownInput(next);
  }

  function onCooldownInput(val) {
    let cd = parseFloat(val);
    if (isNaN(cd) || cd < 0.3) cd = 0.3;
    if (cd > 3.0) cd = 3.0;
    currentCooldown = Math.round(cd * 10) / 10;
    updateCooldownUI(currentCooldown);
    fetch('/api/display_control', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({cmd: 'gesture_cooldown', cooldown: currentCooldown})
    }).then(r => r.json()).then(data => {
      if (data && data.gesture_cooldown !== undefined) updateCooldownUI(data.gesture_cooldown);
      const fs = document.getElementById('footerStatus');
      fs.textContent = 'GESTURE COOLDOWN SET: ' + currentCooldown.toFixed(1) + 's';
    }).catch(()=>{});
  }


  // Touch swipe listener for mobile remote (4-way: Left/Right: Slides, Up/Down: Face/Slides)
  let touchStartX = 0;
  let touchStartY = 0;
  const swipePad = document.getElementById('swipeTouchPad');
  if (swipePad) {
    swipePad.addEventListener('touchstart', (e) => {
      touchStartX = e.touches[0].clientX;
      touchStartY = e.touches[0].clientY;
    }, {passive: true});

    swipePad.addEventListener('touchend', (e) => {
      const deltaX = e.changedTouches[0].clientX - touchStartX;
      const deltaY = e.changedTouches[0].clientY - touchStartY;
      const absX = Math.abs(deltaX);
      const absY = Math.abs(deltaY);

      if (absX > 30 && absX > absY * 1.20) {
        if (deltaX > 0) {
          stepSlide('next');
          swipePad.textContent = '>>> [SWIPED RIGHT] NEXT CHALLENGE >>>';
        } else {
          stepSlide('prev');
          swipePad.textContent = '<<< [SWIPED LEFT] PREV CHALLENGE <<<';
        }
      } else if (absY > 30 && absY > absX * 1.20) {
        if (deltaY < 0) {
          stepSlide('up');
          swipePad.textContent = '^^^ [SWIPED UP] SHOW ROBOT FACE ^^^';
        } else {
          stepSlide('down');
          swipePad.textContent = 'vvv [SWIPED DOWN] SHOW ROBOT FACE vvv';
        }
      }
      setTimeout(() => {
        swipePad.innerHTML = '<strong>[SWIPE THUMB HERE OR WAVE AT CAMERA]</strong><br><span style="font-size:9px; color:#555d6e;">L/R: SLIDES &bull; UP/DOWN: ROBOT FACE</span>';
      }, 1200);
    }, {passive: true});
  }

  let currentPipState = false;
  let currentDiagState = true;

  function updatePipButton(active) {
    currentPipState = !!active;
    const b = document.getElementById('btnTogglePip');
    if (b) {
      b.textContent = currentPipState ? '[CAMERA PiP: ON]' : '[CAMERA PiP: OFF]';
      b.className = currentPipState ? 'mint' : '';
      b.style.borderColor = currentPipState ? 'var(--mint)' : '#2a3242';
      b.style.color = currentPipState ? 'var(--mint)' : '#848896';
    }
  }

  function updateDiagButton(active) {
    currentDiagState = !!active;
    const b = document.getElementById('btnToggleDiag');
    if (b) {
      b.textContent = currentDiagState ? '[RETICLE HUD: ON]' : '[RETICLE HUD: OFF]';
      b.className = currentDiagState ? 'primary' : '';
      b.style.borderColor = currentDiagState ? 'var(--amber)' : '#2a3242';
      b.style.color = currentDiagState ? 'var(--amber)' : '#848896';
    }
  }

  function toggleCameraPip() {
    if (navigator.vibrate) navigator.vibrate(30);
    updatePipButton(!currentPipState);
    fetch('/api/display_control', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({cmd: 'toggle_pip'})
    }).then(r => r.json()).then(data => {
      if (data && data.show_pip !== undefined) updatePipButton(data.show_pip);
      const fs = document.getElementById('footerStatus');
      fs.textContent = 'CAMERA PiP ON TV: ' + (currentPipState ? 'ACTIVATED' : 'DISABLED');
    }).catch(()=>{});
  }

  function toggleDiagnostics() {
    if (navigator.vibrate) navigator.vibrate(30);
    const nextState = !currentDiagState;
    updateDiagButton(nextState);
    fetch('/api/display_control', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({cmd: 'set_hud', enabled: nextState})
    }).then(r => r.json()).then(data => {
      if (data && data.show_hud !== undefined) updateDiagButton(data.show_hud);
      const fs = document.getElementById('footerStatus');
      fs.textContent = 'TACTICAL RETICLE HUD ON TV: ' + (currentDiagState ? 'VISIBLE' : 'HIDDEN');
    }).catch(()=>{});
  }

  let currentScreenRot = 0;
  let currentCamRot = 0;

  function rotateScreen() {
    if (navigator.vibrate) navigator.vibrate(30);
    const order = [0, 90, 180, 270];
    let idx = order.indexOf(currentScreenRot);
    currentScreenRot = order[(idx + 1) % order.length];
    setScreenRot(currentScreenRot);
  }

  function setScreenRot(deg) {
    if (navigator.vibrate) navigator.vibrate(30);
    currentScreenRot = deg;
    syncRotButtons(deg);
    const fs = document.getElementById('footerStatus');
    if (fs) fs.textContent = 'SCREEN ORIENTATION SET: ' + deg + '°';
    showToast('🖥️ SCREEN → ' + deg + '°');
    fetch('/api/display_control', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({cmd: 'rotate_screen', rotation: deg})
    }).catch(()=>{});
  }

  function syncRotButtons(deg) {
    [0, 90, 180, 270].forEach(d => {
      const b = document.getElementById('rot' + d);
      if (b) b.className = (d === deg) ? 'mint' : '';
    });
  }

  function rotateCamera() {
    if (navigator.vibrate) navigator.vibrate(30);
    const order = [0, 90, 180, 270];
    let idx = order.indexOf(currentCamRot);
    currentCamRot = order[(idx + 1) % order.length];
    const b = document.getElementById('btnRotateCam');
    if (b) b.textContent = '📷 CAMERA ORIENTATION: ' + currentCamRot + '°';
    const fs = document.getElementById('footerStatus');
    fs.textContent = 'CAMERA ORIENTATION SET: ' + currentCamRot + '°';
    fetch('/api/display_control', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({cmd: 'rotate_camera', rotation: currentCamRot})
    }).catch(()=>{});
  }

  function setAudioRoute(mode) {
    if (navigator.vibrate) navigator.vibrate(30);
    currentAudioRoute = mode;
    fetch('/api/audio_route', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mode: mode})
    }).then(r => r.json()).then(res => {
      updateRouteButtons(res.mode, res.active);
    }).catch(()=>{});
  }

  function updateRouteButtons(mode, active) {
    currentAudioRoute = active || mode;
    const bp = document.getElementById('btnRoutePhone');
    const ba = document.getElementById('btnRouteAux');
    const bb = document.getElementById('btnRouteBoth');
    if (bp && ba && bb) {
      bp.className = (mode === 'phone') ? 'mint' : '';
      ba.className = (mode === 'aux') ? 'mint' : '';
      bb.className = (mode === 'both') ? 'mint' : '';
    }
    const badge = document.getElementById('audioRouteBadge');
    if (badge && active) {
      let label = 'PHONE SPEAKER';
      let col = '#38eb91';
      let bg = 'rgba(56,235,145,0.15)';
      if (active === 'aux') {
        label = 'TV / PI ONLY';
        col = '#4bd7ff';
        bg = 'rgba(75,215,255,0.15)';
      } else if (active === 'both') {
        label = 'PHONE + TV (BOTH)';
        col = '#ffd75f';
        bg = 'rgba(255,215,95,0.15)';
      }
      badge.textContent = label;
      badge.style.color = col;
      badge.style.background = bg;
      badge.style.border = '1px solid ' + col;
    }
  }

  function reconnectCamera() {
    const fs = document.getElementById('footerStatus');
    fs.textContent = 'SIGNALING RECONNECT TO CAMERA SENSOR...';
    fetch('/api/reconnect_cam', {method: 'POST'}).then(() => {
      fs.textContent = 'SENSOR RECONNECTED // STREAM RESUMING';
    }).catch(() => {});
  }

  // Poll optical sensor status & speech events every 250ms for low latency
  setInterval(() => {
    fetch('/api/status').then(r => r.json()).then(st => {
      // 1. Sync speech lines from TV / Pi to phone speaker
      if (st.speech && st.speech.id > lastSpokenId && st.speech.text) {
        lastSpokenId = st.speech.id;
        if (st.audio_active_route === 'phone' || st.audio_active_route === 'both') {
          playPhoneVoice(st.speech.text, st.speech.cue);
          const card = document.getElementById('replyCard');
          const rep = document.getElementById('replyText');
          if (card && rep) {
            rep.textContent = st.speech.text;
            card.classList.add('visible');
          }
        }
      }

      // 2. Update audio route badge and buttons
      if (st.audio_mode && st.audio_active_route) {
        updateRouteButtons(st.audio_mode, st.audio_active_route);
      }

      // 3. Optical sensor telemetry & CPU thermal state
      const tb = document.getElementById('tempBadge');
      if (tb && st.thermal) {
        tb.textContent = 'CPU: ' + st.thermal.temp_c.toFixed(1) + '°C';
        if (st.thermal.cooling) {
          tb.style.color = '#ff5f5f';
          tb.style.background = 'rgba(255,95,95,0.2)';
          tb.style.borderColor = 'rgba(255,95,95,0.4)';
        } else {
          tb.style.color = '#38eb91';
          tb.style.background = 'rgba(56,235,145,0.12)';
          tb.style.borderColor = 'rgba(56,235,145,0.3)';
        }
      }

      const lb = document.getElementById('lockBadge');
      const ss = document.getElementById('streamStats');
      if (st.active) {
        lb.textContent = 'LOCKED (' + st.count + ' DETECTED)';
        lb.style.color = '#38eb91';
        lb.style.background = 'rgba(56,235,145,0.15)';
        ss.textContent = 'DIST: ' + st.dist + ' // AIM: (' + st.aim_x + ', ' + st.aim_y + ')';
      } else {
        lb.textContent = 'SCANNING // NO TARGET';
        lb.style.color = '#ff8c41';
        lb.style.background = 'rgba(255,165,55,0.15)';
        ss.textContent = 'TARGET: STANDBY // LATENCY: 12ms';
      }

      // 4. Optical PiP, Blue HUD and Hand Gesture telemetry sync
      if (st.calibration) {
        if (st.calibration.show_pip !== undefined) updatePipButton(st.calibration.show_pip);
        if (st.calibration.hud_enabled !== undefined) updateDiagButton(st.calibration.hud_enabled);
        if (st.calibration.gesture_swipe_enabled !== undefined) updateGestureButton(st.calibration.gesture_swipe_enabled);
        const hb = document.getElementById('handBadge');
        if (hb) {
          if (st.calibration.hand_detected) {
            hb.textContent = '[HAND TRACKED] (' + (st.calibration.latest_gesture || 'ACTIVE') + ')';
            hb.style.color = '#38eb91';
            hb.style.background = 'rgba(56,235,145,0.25)';
          } else {
            hb.textContent = 'HAND: SCANNING';
            hb.style.color = '#848896';
            hb.style.background = 'rgba(132,136,150,0.12)';
          }
        }
        if (st.calibration.gesture_sens !== undefined) {
          const rgs = document.getElementById('rngGestureSens');
          const lgs = document.getElementById('lblGestureSens');
          if (rgs && document.activeElement !== rgs) {
            rgs.value = st.calibration.gesture_sens;
            calib.gesture_sens = st.calibration.gesture_sens;
          }
          if (lgs) lgs.textContent = Number(st.calibration.gesture_sens).toFixed(1) + 'x';
        }
        if (st.calibration.sens_left !== undefined) {
          const r = document.getElementById('rngSensLeft');
          const l = document.getElementById('lblSensLeft');
          if (r && document.activeElement !== r) { r.value = st.calibration.sens_left; calib.sens_left = st.calibration.sens_left; }
          if (l) l.textContent = Number(st.calibration.sens_left).toFixed(1) + 'x';
        }
        if (st.calibration.sens_right !== undefined) {
          const r = document.getElementById('rngSensRight');
          const l = document.getElementById('lblSensRight');
          if (r && document.activeElement !== r) { r.value = st.calibration.sens_right; calib.sens_right = st.calibration.sens_right; }
          if (l) l.textContent = Number(st.calibration.sens_right).toFixed(1) + 'x';
        }
        if (st.calibration.sens_up !== undefined) {
          const r = document.getElementById('rngSensUp');
          const l = document.getElementById('lblSensUp');
          if (r && document.activeElement !== r) { r.value = st.calibration.sens_up; calib.sens_up = st.calibration.sens_up; }
          if (l) l.textContent = Number(st.calibration.sens_up).toFixed(1) + 'x';
        }
        if (st.calibration.sens_down !== undefined) {
          const r = document.getElementById('rngSensDown');
          const l = document.getElementById('lblSensDown');
          if (r && document.activeElement !== r) { r.value = st.calibration.sens_down; calib.sens_down = st.calibration.sens_down; }
          if (l) l.textContent = Number(st.calibration.sens_down).toFixed(1) + 'x';
        }
        if (st.calibration.gesture_cooldown !== undefined) {
          updateCooldownUI(st.calibration.gesture_cooldown);
        }
        if (st.calibration.swipe_anim_enabled !== undefined) {
          updateSwipeAnimButton(st.calibration.swipe_anim_enabled);
        }
        if (st.calibration.mirror_gaze_x !== undefined || st.calibration.mirror_gesture_x !== undefined) {
          updateMirrorUI(st.calibration.mirror_gaze_x, st.calibration.mirror_gesture_x);
        }
        if (st.calibration.screen_rotation !== undefined) {
          currentScreenRot = st.calibration.screen_rotation;
          syncRotButtons(currentScreenRot);
        }
        syncLayoutUI(st.calibration);
      }
      const lmb = document.getElementById('lmBadge');
      if (lmb) {
        if (st.landmarks) {
          lmb.textContent = 'ON — confirmed swipes';
          lmb.style.color = '#38eb91';
          lmb.style.borderColor = 'rgba(56,235,145,0.3)';
        } else {
          lmb.textContent = 'OFF — motion only';
          lmb.style.color = '';
          lmb.style.borderColor = '';
        }
      }

      // 5. Auto Cycle & Gesture Mode sync
      if (st.auto_cycle_enabled !== undefined || st.event_display_time !== undefined) {
        updateAutoCycleUI(st.auto_cycle_enabled, st.event_display_time);
      }
      if (st.gesture_mode !== undefined) {
        updateGestureModeUI(st.gesture_mode);
      }
      const sb = document.getElementById('statusBadge');
      if (sb) {
        sb.textContent = 'ONLINE';
        sb.style.color = '#38eb91';
        sb.style.background = 'rgba(56,235,145,0.12)';
        sb.style.borderColor = 'rgba(56,235,145,0.3)';
      }
    }).catch(()=>{
      const sb = document.getElementById('statusBadge');
      if (sb) {
        sb.textContent = 'OFFLINE';
        sb.style.color = '#ff5f5f';
        sb.style.background = 'rgba(255,95,95,0.2)';
        sb.style.borderColor = 'rgba(255,95,95,0.4)';
      }
    });
  }, 250);



  // ── High-Performance Real-Time Camera Stream Poller ───────────────────────
  let pumpRunning = false;
  let useMjpeg = true;

  function startLiveStream() {
    const streamImg = document.getElementById('liveStream');
    if (!streamImg) return;
    if (useMjpeg) {
      streamImg.onerror = function() {
        console.warn('MJPEG stream connection dropped, switching to snapshot pump');
        useMjpeg = false;
        startSnapshotPump();
      };
      streamImg.src = '/stream.mjpg?t=' + Date.now();
    } else {
      startSnapshotPump();
    }
  }

  function startSnapshotPump() {
    if (pumpRunning) return;
    pumpRunning = true;
    const streamImg = document.getElementById('liveStream');
    function nextFrame() {
      const nextImg = new Image();
      nextImg.onload = function() {
        if (streamImg) streamImg.src = this.src;
        setTimeout(nextFrame, 40); // 25 FPS silky-smooth stream
      };
      nextImg.onerror = function() {
        setTimeout(nextFrame, 300); // Graceful retry on glitch
      };
      nextImg.src = '/snapshot.jpg?t=' + Date.now();
    }
    nextFrame();
  }
  window.addEventListener('DOMContentLoaded', startLiveStream);
  setTimeout(startLiveStream, 50);

  function send(data){
    if(navigator.vibrate) navigator.vibrate(35);
    const fs = document.getElementById('footerStatus');
    fs.textContent = 'TRANSMITTING // ' + (data.cmd || '').toUpperCase();
    fetch('/api/cmd', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(data)
    }).then(r => {
      setTimeout(() => { fs.textContent = 'COMMAND DELIVERED // READY'; }, 300);
    }).catch(e => {
      fs.textContent = 'ERROR SENDING COMMAND';
    });
  }

  const pad = document.getElementById('radarPad');
  const dot = document.getElementById('radarDot');
  let padDragging = false;
  let lastRadarSend = 0;

  function handleRadar(e){
    if(!pad) return;
    const rect = pad.getBoundingClientRect();
    const touch = e.touches ? e.touches[0] : e;
    const clX = Math.max(0, Math.min(rect.width, touch.clientX - rect.left));
    const clY = Math.max(0, Math.min(rect.height, touch.clientY - rect.top));

    if(dot){
      dot.style.display = 'block';
      dot.style.left = clX + 'px';
      dot.style.top = clY + 'px';
    }

    const normX = clX / rect.width;
    const normY = clY / rect.height;

    const now = Date.now();
    if(now - lastRadarSend > 60){
      lastRadarSend = now;
      send({cmd:'target', active:true, name:'MANUAL AIM', dist:'1.9m', x:normX, y:normY, count:1});
    }
  }

  if(pad){
    pad.addEventListener('pointerdown', (e) => { padDragging = true; handleRadar(e); });
    pad.addEventListener('pointermove', (e) => { if(padDragging) handleRadar(e); });
    window.addEventListener('pointerup', () => { padDragging = false; });
  }
</script>
</body>
</html>
"""


class WebRemoteHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

    def do_GET(self):
        path_clean = self.path.split("?")[0]
        if path_clean in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(WEB_REMOTE_HTML.encode("utf-8"))
        elif path_clean == "/stream.mjpg":
            from vision import get_latest_stream_frame, notify_stream_active
            notify_stream_active()
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                last_f = None
                while True:
                    f = get_latest_stream_frame()
                    if f is not None and f is not last_f:
                        last_f = f
                        part = f"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {len(f)}\r\n\r\n".encode("utf-8")
                        self.wfile.write(part + f + b"\r\n")
                    time.sleep(0.033)
            except Exception:
                pass
        elif path_clean == "/snapshot.jpg":
            from vision import get_latest_stream_frame, notify_stream_active
            notify_stream_active()
            f = get_latest_stream_frame()
            if not f:
                for _ in range(12):
                    time.sleep(0.08)
                    f = get_latest_stream_frame()
                    if f:
                        break
            if not f:
                try:
                    import cv2, numpy as np
                    fb = np.full((240, 320, 3), (12, 16, 22), dtype=np.uint8)
                    cv2.putText(fb, "TARS OPTICAL SENSOR // CONNECTING...", (20, 120),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.40, (80, 140, 220), 1, cv2.LINE_AA)
                    _, buf = cv2.imencode(".jpg", fb, [cv2.IMWRITE_JPEG_QUALITY, 55])
                    f = buf.tobytes()
                except Exception:
                    pass

            if f:
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(f)))
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.send_header("Pragma", "no-cache")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(f)
            else:
                self.send_response(503)
                self.send_header("Retry-After", "1")
                self.end_headers()
        elif path_clean == "/test_sound.wav":
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(TEST_WAV_BYTES)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(TEST_WAV_BYTES)
        elif path_clean == "/api/voice.wav":
            import urllib.parse
            parsed = urllib.parse.urlparse(self.path)
            qparams = urllib.parse.parse_qs(parsed.query)
            cue = qparams.get("cue", [""])[0]
            data = None
            if cue:
                try:
                    from speech import get_cue_wav_bytes
                    data = get_cue_wav_bytes(cue)
                except Exception:
                    pass
            if not data:
                data = get_latest_speech_wav()
            if not data:
                data = TEST_WAV_BYTES
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        elif path_clean == "/api/status":
            from vision import ACTIVE_TRACKER, get_system_thermal_telemetry
            active_route = get_audio_route()
            ext_detected = check_external_audio_device()
            therm = get_system_thermal_telemetry()
            with LATEST_SPEECH_LOCK:
                speech_info = dict(LATEST_SPEECH_DATA)
            st = {
                "active": bool(getattr(ACTIVE_TRACKER, "target_active", False)),
                "count": int(getattr(ACTIVE_TRACKER, "crowd_count", 0)),
                "dist": str(getattr(ACTIVE_TRACKER, "estimated_dist", "N/A")),
                "aim_x": round(float(getattr(ACTIVE_TRACKER, "primary_x", 0.5)), 2),
                "aim_y": round(float(getattr(ACTIVE_TRACKER, "primary_y", 0.5)), 2),
                "audio_mode": AUDIO_ROUTE_MODE,
                "audio_active_route": active_route,
                "external_audio_detected": ext_detected,
                "speech": speech_info,
                "thermal": therm,
                "landmarks": bool(getattr(getattr(ACTIVE_TRACKER, "_landmarks", None), "ok", False)),
                "auto_cycle_enabled": getattr(config, "AUTO_CYCLE_ENABLED", True),
                "event_display_time": getattr(config, "EVENT_DISPLAY_TIME", 8.0),
                "gesture_mode": getattr(config, "GESTURE_MODE", "HORIZONTAL_SWIPE"),
                "calibration": {
                    "mirror_x": getattr(config, "MIRROR_GAZE_X", config.MIRROR_CAMERA_X),
                    "mirror_gaze_x": getattr(config, "MIRROR_GAZE_X", config.MIRROR_CAMERA_X),
                    "mirror_gesture_x": getattr(config, "MIRROR_GESTURE_X", False),
                    "screen_rotation": getattr(config, "SCREEN_ROTATION", 0),
                    "invert_y": config.INVERT_CAMERA_Y,
                    "camera_position": config.CAMERA_POSITION,
                    "monitor_diag": config.MONITOR_DIAG_INCHES,
                    "couch_dist": config.COUCH_DIST_METERS,
                    "sens_x": config.GAZE_SENSITIVITY_X,
                    "sens_y": config.GAZE_SENSITIVITY_Y,
                    "gesture_sens": getattr(config, "GESTURE_SWIPE_SENSITIVITY", 1.0),
                    "sens_left": getattr(config, "GESTURE_SENS_LEFT", 1.0),
                    "sens_right": getattr(config, "GESTURE_SENS_RIGHT", 1.0),
                    "sens_up": getattr(config, "GESTURE_SENS_UP", 1.0),
                    "sens_down": getattr(config, "GESTURE_SENS_DOWN", 1.0),
                    "offset_x": config.GAZE_OFFSET_X,
                    "offset_y": config.GAZE_OFFSET_Y,
                    "show_pip": config.SHOW_CAMERA_PIP,
                    "show_diagnostics": config.SHOW_DIAGNOSTICS,
                    "hud_enabled": config.HUD_ENABLED,
                    "gesture_swipe_enabled": getattr(config, "GESTURE_SWIPE_ENABLED", True),
                    "gesture_mode": getattr(config, "GESTURE_MODE", "HORIZONTAL_SWIPE"),
                    "gesture_cooldown": getattr(config, "GESTURE_COOLDOWN_SEC", 1.00),
                    "swipe_anim_enabled": getattr(config, "SWIPE_ANIMATION_ENABLED", True),
                    "hand_detected": bool(getattr(ACTIVE_TRACKER, "hand_detected", False)),
                    "latest_gesture": str(getattr(ACTIVE_TRACKER, "latest_gesture", "NONE")),
                    "face_cx": getattr(config, "FACE_CX_RATIO", 0.5),
                    "face_cy": getattr(config, "FACE_CY_RATIO", None),
                    "face_size": getattr(config, "FACE_SIZE", 1.0),
                    "pip_pos": getattr(config, "PIP_POS", "BR"),
                    "pip_scale": getattr(config, "PIP_SCALE", 1.0),
                    "pip_x": getattr(config, "PIP_X", 1.0),
                    "pip_y": getattr(config, "PIP_Y", 1.0),
                    "pip_crop": list(getattr(config, "PIP_CROP", [0, 0, 1, 1])),
                    "slide_zoom": getattr(config, "SLIDE_ZOOM", 1.0),
                    "slide_x": getattr(config, "SLIDE_X", 0.5),
                    "slide_y": getattr(config, "SLIDE_Y", 0.5),
                    "vslide_mode": getattr(config, "VSLIDE_MODE", False),
                    "vslide_scale": getattr(config, "VSLIDE_SCALE", 0.9),
                    "vslide_x": getattr(config, "VSLIDE_X", 0.5),
                    "vslide_y": getattr(config, "VSLIDE_Y", 0.5),
                    "vslide_fit": getattr(config, "VSLIDE_FIT", "FIT"),
                    "vslide_rot": getattr(config, "VSLIDE_ROT", 0),
                }
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(st).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/cmd":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")
                cmd_dict = json.loads(body)
                COMMAND_QUEUE.put(cmd_dict)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
            except Exception as e:
                self.send_response(400)
                self.end_headers()
        elif self.path == "/api/playlist":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                mode = body.get("mode", "manual")
                COMMAND_QUEUE.put({"cmd": "playlist", "mode": mode})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
            except Exception:
                self.send_response(400)
                self.end_headers()
        elif self.path == "/api/chat":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")
                req = json.loads(body)
                query = req.get("query", "")
                from dialogue import match_intent
                matched = match_intent(query)
                try:
                    from speech import get_cue_wav_bytes
                    wav_data = get_cue_wav_bytes(matched["cue"])
                    set_latest_speech_wav(wav_data)
                except Exception:
                    pass
                set_latest_speech(matched["text"], matched["cue"])
                chat_payload = {
                    "cmd": "say",
                    "clip": matched["cue"],
                    "text": matched["text"],
                    "emotion": matched["emotion"],
                    "wink": matched["wink"],
                    "query": query,
                }
                COMMAND_QUEUE.put(chat_payload)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                with LATEST_SPEECH_LOCK:
                    curr_speech_id = LATEST_SPEECH_DATA["id"]
                resp_json = json.dumps({
                    "status": "ok",
                    "reply": matched["text"],
                    "cue": matched["cue"],
                    "emotion": matched["emotion"],
                    "wink": matched["wink"],
                    "speech_id": curr_speech_id,
                    "audio_url": f"/api/voice.wav?cue={matched['cue']}&t={int(time.time()*1000)}"
                })
                self.wfile.write(resp_json.encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self.end_headers()
        elif self.path == "/api/vision":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")
                req = json.loads(body)
                active = req.get("active", True)
                faces = req.get("faces", [])
                primary = faces[0] if faces else {"x": req.get("x", 0.5), "y": req.get("y", 0.5)}
                count = len(faces) if faces else (1 if active else 0)
                secondaries = [(f.get("x", 0.5), f.get("y", 0.5)) for f in faces[1:4]]
                vis_payload = {
                    "cmd": "target",
                    "active": active,
                    "name": "VIP SPECTATOR" if count <= 1 else "AUDIENCE CLUSTER",
                    "dist": req.get("dist", "1.9m"),
                    "x": primary.get("x", 0.5),
                    "y": primary.get("y", 0.5),
                    "count": count,
                    "secondaries": secondaries,
                }
                COMMAND_QUEUE.put(vis_payload)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
            except Exception as e:
                self.send_response(400)
                self.end_headers()
        elif self.path == "/api/audio_route":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                new_mode = body.get("mode", "phone")
                set_audio_route(new_mode)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "ok",
                    "mode": AUDIO_ROUTE_MODE,
                    "active": get_audio_route()
                }).encode("utf-8"))
            except Exception:
                self.send_response(400)
                self.end_headers()
        elif self.path == "/api/reconnect_cam":
            try:
                from vision import ACTIVE_TRACKER
                if ACTIVE_TRACKER is not None:
                    ACTIVE_TRACKER._ensure_grabber()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b'{"status":"reconnected"}')
            except Exception:
                self.send_response(400)
                self.end_headers()
        elif self.path == "/api/calibrate":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                cmd_dict = {"cmd": "calibrate"}
                cmd_dict.update(body)
                COMMAND_QUEUE.put(cmd_dict)
                if "mirror_gaze_x" in body:
                    config.MIRROR_GAZE_X = bool(body["mirror_gaze_x"])
                    config.MIRROR_CAMERA_X = config.MIRROR_GAZE_X
                elif "mirror_x" in body:
                    config.MIRROR_GAZE_X = bool(body["mirror_x"])
                    config.MIRROR_CAMERA_X = config.MIRROR_GAZE_X

                if "mirror_gesture_x" in body:
                    config.MIRROR_GESTURE_X = bool(body["mirror_gesture_x"])

                if "invert_y" in body: config.INVERT_CAMERA_Y = bool(body["invert_y"])
                if "camera_position" in body: config.CAMERA_POSITION = str(body["camera_position"]).upper()
                if "monitor_diag" in body: config.MONITOR_DIAG_INCHES = float(body["monitor_diag"])
                if "couch_dist" in body: config.COUCH_DIST_METERS = float(body["couch_dist"])
                if "sens_x" in body: config.GAZE_SENSITIVITY_X = float(body["sens_x"])
                if "sens_y" in body: config.GAZE_SENSITIVITY_Y = float(body["sens_y"])
                if "gesture_sens" in body: config.GESTURE_SWIPE_SENSITIVITY = float(body["gesture_sens"])
                if "sens_left" in body: config.GESTURE_SENS_LEFT = float(body["sens_left"])
                if "sens_right" in body: config.GESTURE_SENS_RIGHT = float(body["sens_right"])
                if "sens_up" in body: config.GESTURE_SENS_UP = float(body["sens_up"])
                if "sens_down" in body: config.GESTURE_SENS_DOWN = float(body["sens_down"])
                if "gesture_cooldown" in body: config.GESTURE_COOLDOWN_SEC = max(0.1, min(5.0, float(body["gesture_cooldown"])))
                if "swipe_anim_enabled" in body: config.SWIPE_ANIMATION_ENABLED = bool(body["swipe_anim_enabled"])
                if "offset_x" in body: config.GAZE_OFFSET_X = float(body["offset_x"])
                if "offset_y" in body: config.GAZE_OFFSET_Y = float(body["offset_y"])
                config.save_calibration()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
            except Exception as e:
                self.send_response(400)
                self.end_headers()
        elif self.path == "/api/display_control":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                cmd = body.get("cmd")
                if cmd == "toggle_pip":
                    config.SHOW_CAMERA_PIP = not config.SHOW_CAMERA_PIP
                    COMMAND_QUEUE.put({"cmd": "set_pip", "enabled": config.SHOW_CAMERA_PIP})
                elif cmd == "set_pip":
                    config.SHOW_CAMERA_PIP = bool(body.get("enabled", True))
                    COMMAND_QUEUE.put({"cmd": "set_pip", "enabled": config.SHOW_CAMERA_PIP})
                elif cmd in ("toggle_diagnostics", "toggle_hud"):
                    config.HUD_ENABLED = not config.HUD_ENABLED
                    config.SHOW_DIAGNOSTICS = config.HUD_ENABLED
                    COMMAND_QUEUE.put({"cmd": "set_hud", "enabled": config.HUD_ENABLED})
                elif cmd in ("set_diagnostics", "set_hud"):
                    config.HUD_ENABLED = bool(body.get("enabled", True))
                    config.SHOW_DIAGNOSTICS = config.HUD_ENABLED
                    COMMAND_QUEUE.put({"cmd": "set_hud", "enabled": config.HUD_ENABLED})
                elif cmd == "toggle_gesture":
                    config.GESTURE_SWIPE_ENABLED = not getattr(config, "GESTURE_SWIPE_ENABLED", True)
                    COMMAND_QUEUE.put({"cmd": "set_gesture", "enabled": config.GESTURE_SWIPE_ENABLED})
                elif cmd == "set_gesture":
                    config.GESTURE_SWIPE_ENABLED = bool(body.get("enabled", True))
                    COMMAND_QUEUE.put({"cmd": "set_gesture", "enabled": config.GESTURE_SWIPE_ENABLED})
                elif cmd == "toggle_swipe_anim":
                    config.SWIPE_ANIMATION_ENABLED = not getattr(config, "SWIPE_ANIMATION_ENABLED", True)
                    COMMAND_QUEUE.put({"cmd": "set_swipe_anim", "enabled": config.SWIPE_ANIMATION_ENABLED})
                elif cmd == "set_swipe_anim":
                    config.SWIPE_ANIMATION_ENABLED = bool(body.get("enabled", True))
                    COMMAND_QUEUE.put({"cmd": "set_swipe_anim", "enabled": config.SWIPE_ANIMATION_ENABLED})
                elif cmd in ("gesture_cooldown", "set_cooldown"):
                    cd = float(body.get("cooldown", body.get("seconds", 1.0)))
                    config.GESTURE_COOLDOWN_SEC = max(0.1, min(5.0, cd))
                    COMMAND_QUEUE.put({"cmd": "set_cooldown", "cooldown": config.GESTURE_COOLDOWN_SEC})
                elif cmd == "auto_cycle":
                    if "enabled" in body:
                        config.AUTO_CYCLE_ENABLED = bool(body["enabled"])
                    else:
                        config.AUTO_CYCLE_ENABLED = not getattr(config, "AUTO_CYCLE_ENABLED", True)
                    COMMAND_QUEUE.put({"cmd": "auto_cycle", "enabled": config.AUTO_CYCLE_ENABLED})
                elif cmd == "slide_duration":
                    dur = float(body.get("duration", body.get("seconds", 8.0)))
                    config.EVENT_DISPLAY_TIME = max(1.0, min(120.0, dur))
                    COMMAND_QUEUE.put({"cmd": "slide_duration", "duration": config.EVENT_DISPLAY_TIME})
                elif cmd == "gesture_mode":
                    gmode = str(body.get("mode", "HORIZONTAL_SWIPE")).upper()
                    config.GESTURE_MODE = gmode
                    COMMAND_QUEUE.put({"cmd": "gesture_mode", "mode": gmode})
                elif cmd in ("rotate_screen", "set_rotation", "rotate_display"):
                    if "rotation" in body:
                        config.SCREEN_ROTATION = int(body["rotation"])
                    else:
                        order = [0, 90, 180, 270]
                        cur = getattr(config, "SCREEN_ROTATION", 0)
                        idx = order.index(cur) if cur in order else 0
                        config.SCREEN_ROTATION = order[(idx + 1) % len(order)]
                    COMMAND_QUEUE.put({"cmd": "set_rotation", "rotation": config.SCREEN_ROTATION})
                elif cmd in ("rotate_camera", "set_camera_rotation"):
                    if "rotation" in body:
                        config.CAMERA_ROTATION = int(body["rotation"])
                    else:
                        order = [0, 90, 180, 270]
                        cur = getattr(config, "CAMERA_ROTATION", 0)
                        idx = order.index(cur) if cur in order else 0
                        config.CAMERA_ROTATION = order[(idx + 1) % len(order)]
                else:
                    COMMAND_QUEUE.put(body)
                config.save_calibration()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "ok",
                    "show_pip": config.SHOW_CAMERA_PIP,
                    "show_hud": config.HUD_ENABLED,
                    "show_diagnostics": config.SHOW_DIAGNOSTICS,
                    "gesture_enabled": getattr(config, "GESTURE_SWIPE_ENABLED", True),
                    "gesture_mode": getattr(config, "GESTURE_MODE", "HORIZONTAL_SWIPE"),
                    "swipe_anim_enabled": getattr(config, "SWIPE_ANIMATION_ENABLED", True),
                    "gesture_cooldown": getattr(config, "GESTURE_COOLDOWN_SEC", 1.00),
                    "auto_cycle_enabled": getattr(config, "AUTO_CYCLE_ENABLED", True),
                    "event_display_time": getattr(config, "EVENT_DISPLAY_TIME", 8.0),
                    "screen_rotation": getattr(config, "SCREEN_ROTATION", 0),
                    "camera_rotation": getattr(config, "CAMERA_ROTATION", 0)
                }).encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Silence console access logs to keep terminal pristine
        return


class BridgeReceiver:
    """Background listener thread for incoming bot, network & web remote commands."""
    def __init__(self, port: int = config.BRIDGE_UDP_PORT, web_port: int = config.WEB_REMOTE_PORT):
        self.port = port
        self.web_port = web_port
        self.running = False
        self._thread: threading.Thread | None = None
        self._web_thread: threading.Thread | None = None
        self._https_thread: threading.Thread | None = None
        self._sock: socket.socket | None = None
        self._httpd: ThreadingHTTPServer | None = None
        self._httpsd: ThreadingHTTPServer | None = None

    def start(self):
        if self.running:
            return
        self.running = True

        # 1. UDP Listener Thread
        self._thread = threading.Thread(target=self._udp_worker, daemon=True)
        self._thread.start()
        config.tlog("BridgeReceiver", f"UDP listener active on port {self.port}")

        # 2. Pocket Web Remote Server Threads
        if config.WEB_REMOTE_ENABLED:
            # A. Standard HTTP Server (Port 8080)
            try:
                self._httpd = ThreadingHTTPServer(("0.0.0.0", self.web_port), WebRemoteHandler)
                self._web_thread = threading.Thread(target=self._web_worker, daemon=True)
                self._web_thread.start()
                config.tlog("BridgeReceiver", f"Pocket Web Remote (HTTP) online at http://0.0.0.0:{self.web_port}")
            except Exception as e:
                config.tlog("BridgeReceiver", f"Web remote HTTP init error: {e}")

            # B. Secure HTTPS Server (Port 8443) for browser microphone permissions
            cert_candidates = [
                "/home/cyrus/TARS/cert.pem",
                os.path.join(os.path.dirname(__file__), "cert.pem"),
                "cert.pem"
            ]
            key_candidates = [
                "/home/cyrus/TARS/key.pem",
                os.path.join(os.path.dirname(__file__), "key.pem"),
                "key.pem"
            ]
            cert_file = next((p for p in cert_candidates if os.path.exists(p)), None)
            key_file = next((p for p in key_candidates if os.path.exists(p)), None)

            if cert_file and key_file:
                try:
                    ssl_port = self.web_port + 363  # 8443
                    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                    ssl_ctx.load_cert_chain(certfile=cert_file, keyfile=key_file)
                    self._httpsd = ThreadingHTTPServer(("0.0.0.0", ssl_port), WebRemoteHandler)
                    self._httpsd.socket = ssl_ctx.wrap_socket(self._httpsd.socket, server_side=True)
                    self._https_thread = threading.Thread(target=self._https_worker, daemon=True)
                    self._https_thread.start()
                    config.tlog("BridgeReceiver", f"Secure Pocket Remote (HTTPS) online at https://0.0.0.0:{ssl_port}")
                except Exception as e:
                    config.tlog("BridgeReceiver", f"HTTPS init error: {e}")

        # 3. Optional GPIO Hardware Button Listener
        if config.GPIO_ENABLED:
            self._init_gpio()

    def _udp_worker(self):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("0.0.0.0", self.port))
            self._sock.settimeout(0.5)
        except Exception as e:
            config.tlog("BridgeReceiver", f"Could not bind UDP port {self.port}: {e}")
            return

        while self.running:
            try:
                data, addr = self._sock.recvfrom(2048)
                msg = data.decode("utf-8", errors="ignore").strip()
                cmd_dict = parse_command(msg)
                if cmd_dict:
                    COMMAND_QUEUE.put(cmd_dict)
            except socket.timeout:
                continue
            except Exception:
                if not self.running:
                    break

    def _web_worker(self):
        if self._httpd:
            try:
                self._httpd.serve_forever()
            except Exception:
                pass

    def _https_worker(self):
        if self._httpsd:
            try:
                self._httpsd.serve_forever()
            except Exception:
                pass

    def _init_gpio(self):
        """Optional zero-cost physical button support on Raspberry Pi."""
        try:
            import RPi.GPIO as GPIO
            GPIO.setmode(GPIO.BCM)
            for pin in (config.GPIO_PIN_TALK, config.GPIO_PIN_WINK, config.GPIO_PIN_EVENT):
                GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

            def _btn_callback(channel):
                if channel == config.GPIO_PIN_TALK:
                    COMMAND_QUEUE.put({"cmd": "say", "clip": "greeting"})
                elif channel == config.GPIO_PIN_WINK:
                    COMMAND_QUEUE.put({"cmd": "wink", "side": "LEFT"})
                elif channel == config.GPIO_PIN_EVENT:
                    COMMAND_QUEUE.put({"cmd": "event_step", "dir": "next"})

            GPIO.add_event_detect(config.GPIO_PIN_TALK,  GPIO.FALLING, callback=_btn_callback, bouncetime=250)
            GPIO.add_event_detect(config.GPIO_PIN_WINK,  GPIO.FALLING, callback=_btn_callback, bouncetime=250)
            GPIO.add_event_detect(config.GPIO_PIN_EVENT, GPIO.FALLING, callback=_btn_callback, bouncetime=250)
            config.tlog("BridgeReceiver", "Raspberry Pi GPIO hardware button listener initialized")
        except Exception:
            # Running on non-Pi machine or RPi.GPIO not installed — fails silently
            pass

    def stop(self):
        self.running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        if self._httpd:
            try:
                self._httpd.shutdown()
            except Exception:
                pass
        if self._httpsd:
            try:
                self._httpsd.shutdown()
            except Exception:
                pass


def send_command(cmd_dict_or_str, host: str = "127.0.0.1", port: int = config.BRIDGE_UDP_PORT):
    """Utility function to send a command packet to TARS from any script or terminal."""
    if isinstance(cmd_dict_or_str, dict):
        msg = json.dumps(cmd_dict_or_str)
    else:
        msg = str(cmd_dict_or_str)

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(msg.encode("utf-8"), (host, port))
    s.close()
