# TARS Robot Display Engine — Phase 2: Speech, Vision HUD & Pocket Web Remote

High-performance, aesthetics-focused native Pygame implementation designed for Raspberry Pi (KMSDRM / DirectFB) driving laptop/desktop monitors (13"–21") and large displays.

---

## Key Capabilities & Architecture

- **High-Performance Direct Rendering**: Zero per-frame font rasterization, zero software alpha-blending overhead, zero per-frame surface copies. Frame draw times maintained under **~3.5ms** at rock-solid 60 FPS.
- **Laptop & Monitor Proportions**: Safe-zone padding calibrated to 3.8% with high-density, crisp sci-fi typography comfortable for 0.8m–2m viewing distances.
- **Pocket Web Remote ($0 Teleoperation)**:
  - Built-in zero-dependency web server on `http://<pi_ip>:8080`.
  - Control TARS secretly from **any smartphone browser** in your pocket: trigger voice lines, winks, emotions, target scans, and dossier slides with haptic-vibrating touch buttons!
- **Audio Speech Engine & Reactive Talking Visor (`speech.py`)**:
  - Plays WAV/OGG files or procedurally synthesizes authentic multi-formant sci-fi robotic voice tones with zero external sound files.
  - While talking, the acoustic visor mouth dynamically modulates at 28 rad/s matching speech amplitude, then curls into a warm smile and winks when finished.
- **7 Positive & Expressive Emotion States**:
  1. `NEUTRAL` — Radiant amber (`#ffa537`), polite resting smile.
  2. `HAPPY` — Warm gold (`#ffd75f`), bright crescent gaze & radiant smile.
  3. `PLAYFUL` — Coral tangerine (`#ff8c41`), cheeky wink-ready grin (Humor 75%).
  4. `EXCITED` — Mint emerald (`#38eb91`), wide bright eyes & celebratory joy beam.
  5. `CURIOUS` — Brilliant cyan (`#4bd7ff`), inquisitive upward glance & smile.
  6. `CONFIDENT` — Electric azure (`#5fafff`), sleek reassuring robotic smile.
  7. `WARM` — Sunrise peach (`#ff9682`), gentle gaze & heartwarming welcome smile.
- **Independent Dual-Channel Winking**:
  - Independent eyelid control for Left (`W`) and Right (`E`) winks.
  - Dynamic smile perk: winking automatically deepens the smile by up to 12% and curls toward the winking eye for a charismatic wink-and-smile.
- **Optical Target Lock HUD (`hud.py`)**:
  - Authentic *Interstellar* sci-fi vector reticle overlay.
  - Autonomous demo mode sweeps across the audience, acquires visitor target, speaks a greeting, and winks.
  - Camera-ready: connects to phone webcam (e.g. IP Webcam app) or USB camera.
- **Physical GPIO Hardware Buttons ($0 Buttons)**:
  - Directly connect momentary switches between Raspberry Pi GPIO pins (17=Talk, 27=Wink, 22=Next) and Ground with internal software pull-ups.

---

## Controls

| Key | Action |
|:---|:---|
| **`S`** | **Speak next voice line** (acoustic mouth talks + finishes with a wink) |
| **`T`** | **Trigger optical visitor detection sweep & lock** |
| **`1` – `7`** | Instant switch between 7 positive emotion states |
| **`W` / `E`** | **Left / Right Eye Wink** (with dynamic smile perk) |
| **`B`** | Synchronized Dual Blink |
| **`SPACE` / `→`** | Next Event Dossier card |
| **`←`** | Previous Event card |
| **`D`** | Toggle live performance telemetry HUD graph |
| **`F`** | Toggle Fullscreen |
| **`ESC` / `Q`** | Exit application |

---

## Deployment & Running

### 1. Run TARS on Raspberry Pi (KMSDRM Recommended)

```bash
SDL_VIDEODRIVER=kmsdrm python3 ~/TARS/main.py
```

### 2. Connect Your Phone Remote

1. Ensure your phone is on the same Wi-Fi or mobile hotspot as the Pi.
2. Open Safari / Chrome on your phone:
   ```
   http://<raspberry_pi_ip>:8080
   ```
3. Tap any button to command TARS wirelessly with zero latency!
