#!/usr/bin/env python3
"""
TARS Gesture Visual Calibrator & Diagnostic Suite
Renders on the Pi TV/Display (via Pygame / KMSDRM) and prints to terminal.
Shows live camera feed, YuNet head tracking reticle, hand tracking reticle,
real-time walking suppression badge, motion trajectory trail, and step-by-step instructions.
"""

import os
import sys
import time
import json
import numpy as np

# Ensure TARS root is on python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import cv2
import pygame
from vision import OpticalGestureEngine, FreshFrameGrabber

# Color palette matching TARS sci-fi aesthetics
VOID       = (10, 14, 20)
PANEL      = (16, 22, 32)
BORDER     = (35, 45, 62)
TEXT_WHITE = (240, 245, 255)
AMBER      = (255, 165, 55)
CYAN       = (75, 215, 255)
MINT       = (56, 235, 145)
RED        = (255, 95, 95)
MUTED      = (120, 130, 150)

def main():
    print("=" * 68)
    print("      TARS OPTICAL GESTURE INTERACTIVE TV CALIBRATOR")
    print("=" * 68)
    print("Initializing Pygame graphics engine on TV...")

    # Set default video driver if not specified
    if "SDL_VIDEODRIVER" not in os.environ and sys.platform.startswith("linux"):
        if "DISPLAY" not in os.environ:
            os.environ["SDL_VIDEODRIVER"] = "kmsdrm"

    pygame.init()
    pygame.font.init()
    pygame.mouse.set_visible(False)

    info = pygame.display.Info()
    sw = info.current_w if info.current_w > 0 else 1920
    sh = info.current_h if info.current_h > 0 else 1080

    try:
        screen = pygame.display.set_mode((sw, sh), pygame.FULLSCREEN | pygame.DOUBLEBUF)
    except Exception:
        screen = pygame.display.set_mode((sw, sh), pygame.RESIZABLE)

    clock = pygame.time.Clock()

    font_huge = pygame.font.Font(None, max(34, int(sh * 0.065)))
    font_large = pygame.font.Font(None, max(24, int(sh * 0.042)))
    font_med = pygame.font.Font(None, max(18, int(sh * 0.030)))
    font_small = pygame.font.Font(None, max(15, int(sh * 0.022)))

    cam_src = getattr(config, "CAMERA_STREAM_URL", None)
    if not cam_src or not str(cam_src).startswith("http"):
        cam_src = "http://192.168.42.129:8080/video"

    print(f"Connecting to live camera: {cam_src} ...")
    grabber = FreshFrameGrabber(cam_src)

    # Splash loading screen
    screen.fill(VOID)
    msg = font_large.render(f"CONNECTING TO CAMERA: {cam_src} ...", True, AMBER)
    screen.blit(msg, (sw // 2 - msg.get_width() // 2, sh // 2 - msg.get_height() // 2))
    pygame.display.flip()

    # Wait for frame
    test_frame = None
    t0 = time.time()
    while time.time() - t0 < 8.0:
        ret, f = grabber.read()
        if ret and f is not None:
            test_frame = f
            break
        pygame.event.pump()
        time.sleep(0.05)

    if test_frame is None:
        screen.fill(VOID)
        err = font_large.render("CAMERA CONNECTION FAILED!", True, RED)
        sub = font_med.render("Ensure IP Webcam is running on your phone, then press ESC.", True, TEXT_WHITE)
        screen.blit(err, (sw // 2 - err.get_width() // 2, sh // 2 - 40))
        screen.blit(sub, (sw // 2 - sub.get_width() // 2, sh // 2 + 20))
        pygame.display.flip()
        time.sleep(4.0)
        grabber.stop()
        pygame.quit()
        return

    print(f"[OK] Camera feed acquired ({test_frame.shape[1]}x{test_frame.shape[0]})")

    # Load YuNet neural network face detector
    yunet = None
    yunet_w, yunet_h = 192, 144
    yunet_paths = [
        "/home/cyrus/TARS/models/face_detection_yunet_2023mar.onnx",
        os.path.join(os.path.dirname(__file__), "models", "face_detection_yunet_2023mar.onnx"),
        "face_detection_yunet_2023mar.onnx"
    ]
    for yp in yunet_paths:
        if os.path.exists(yp) and hasattr(cv2, "FaceDetectorYN"):
            try:
                yunet = cv2.FaceDetectorYN.create(
                    yp, "", (yunet_w, yunet_h),
                    score_threshold=0.36,
                    nms_threshold=0.25,
                    top_k=5000
                )
                print(f"[OK] YuNet Face Detector loaded: {yp}")
                break
            except Exception as e:
                print(f"[WARN] Failed loading YuNet: {e}")

    engine = OpticalGestureEngine(sensitivity=1.0, mirror=config.MIRROR_GESTURE_X, invert_y=config.INVERT_CAMERA_Y)

    # Step state machine:
    # 0: Ambient baseline check (4.0s)
    # 1: Swipe Right test (9.5s)
    # 2: Return stroke rejection (6.0s)
    # 3: Swipe Left test (9.5s)
    # 4: Swipe Up (Robot Face thrust) test (9.5s)
    # 5: Walk across room test (10.0s)
    # 6: Summary & Results
    step = 0
    step_start_t = time.time()

    # Generous step durations to give user plenty of time (30s per active test)
    step_durations = [4.0, 30.0, 8.0, 30.0, 30.0, 12.0]
    step_advance_at = 0.0  # timestamp for smooth auto-advance after a successful gesture

    measured = {
        "swipe_right_dx": 0.0,
        "swipe_right_speed": 0.0,
        "swipe_right_ok": False,
        "return_stroke_blocked": True,
        "swipe_left_dx": 0.0,
        "swipe_left_speed": 0.0,
        "swipe_left_ok": False,
        "swipe_up_dy": 0.0,
        "swipe_up_speed": 0.0,
        "swipe_up_ok": False,
        "walk_detected_count": 0,
        "walk_false_swipes": 0,
    }

    triggered_banner = ""
    triggered_banner_until = 0.0
    running = True

    # ── AUTO-MIRROR DETECTION PRE-PHASE ──
    # Runs BEFORE step 0. User swipes hand to their physical RIGHT for 5s.
    # Net camera-space dx tells us if camera is mirrored (selfie) or not.
    mirror_cal_phase = True
    mirror_cal_t = time.time()
    mirror_cal_peak_neg = 0.0   # most negative single-window dx seen (selfie cam: hand-right = cam-left)
    mirror_cal_peak_pos = 0.0   # most positive single-window dx seen
    mirror_cal_detected = False  # did we see a significant sweep?

    while running:
        now = time.time()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT or (ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE):
                running = False
            elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_SPACE:
                # Advance step manually (also clears mirror_cal phase)
                mirror_cal_phase = False
                step += 1
                step_start_t = now
                engine.history.clear()
                engine.last_swipe_time = 0.0
                engine.last_swipe_fired_t = 0.0
                engine.rebound_lockout_until = 0.0
                engine.hand_must_settle = False
            elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_m:
                # Toggle X mirror mode live!
                config.MIRROR_GESTURE_X = not config.MIRROR_GESTURE_X
                config.MIRROR_GAZE_X = config.MIRROR_GESTURE_X
                config.MIRROR_CAMERA_X = config.MIRROR_GESTURE_X
                engine.mirror = config.MIRROR_GESTURE_X
                config.save_calibration()
                triggered_banner = f"CAMERA & GESTURE MIRROR: {'ON (NATURAL)' if config.MIRROR_GESTURE_X else 'OFF (NATIVE)'}"
                triggered_banner_until = now + 2.5
                print(f"[{time.strftime('%H:%M:%S')}] [TV-Calibrate] Mirror toggled: {config.MIRROR_GESTURE_X}")
            elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_y:
                # Toggle Y invert (for upside-down mounted cameras)
                config.INVERT_CAMERA_Y = not config.INVERT_CAMERA_Y
                engine.invert_y = config.INVERT_CAMERA_Y
                config.save_calibration()
                triggered_banner = f"VERTICAL FLIP: {'INVERTED' if config.INVERT_CAMERA_Y else 'NORMAL'}"
                triggered_banner_until = now + 2.5
                print(f"[{time.strftime('%H:%M:%S')}] [TV-Calibrate] Invert Y toggled: {config.INVERT_CAMERA_Y}")

        ret, frame = grabber.read()
        gesture = None
        detected_face_boxes = []

        if ret and frame is not None:
            fh, fw = frame.shape[:2]
            gw = 160
            gh = int(160 * (fh / fw))
            gh = gh if gh % 2 == 0 else gh + 1
            small = cv2.resize(frame, (gw, gh), interpolation=cv2.INTER_NEAREST)

            if yunet is not None:
                yn_small = cv2.resize(frame, (yunet_w, yunet_h), interpolation=cv2.INTER_NEAREST)
                yunet.setInputSize((yunet_w, yunet_h))
                _, faces = yunet.detect(yn_small)
                if faces is not None:
                    for f in faces:
                        x, y, w, h = f[0:4]
                        nx = float(x) / yunet_w
                        ny = float(y) / yunet_h
                        nw = float(w) / yunet_w
                        nh = float(h) / yunet_h
                        detected_face_boxes.append((nx, ny, nw, nh))

            gesture = engine.process(frame, face_boxes=detected_face_boxes, pre_small=small)
            if gesture:
                triggered_banner = f"GESTURE FIRED: {gesture}!"
                triggered_banner_until = now + 1.8
                print(f"[{time.strftime('%H:%M:%S')}] [TV-Calibrate] Triggered: {gesture}")

        # ── AUTO-MIRROR DETECTION STATE MACHINE ──
        # Runs BEFORE step 0. Tracks the largest single hand sweep during 5s.
        # The net camera-space direction of that sweep auto-determines mirror setting.
        if mirror_cal_phase:
            mirror_cal_elapsed = now - mirror_cal_t
            if len(engine.history) >= 2:
                raw_dx = engine.history[-1][3] - engine.history[0][3]
                if raw_dx < mirror_cal_peak_neg:  # track peak negative (leftward in camera)
                    mirror_cal_peak_neg = raw_dx
                    if abs(raw_dx) > 0.20:
                        mirror_cal_detected = True
                if raw_dx > mirror_cal_peak_pos:  # track peak positive (rightward in camera)
                    mirror_cal_peak_pos = raw_dx
                    if abs(raw_dx) > 0.20:
                        mirror_cal_detected = True
            if mirror_cal_elapsed >= 5.0:
                # Auto-configure mirror from dominant direction
                if mirror_cal_detected:
                    # User swiped their PHYSICAL RIGHT. Bigger neg = selfie cam. Bigger pos = standard cam.
                    if abs(mirror_cal_peak_neg) > abs(mirror_cal_peak_pos) + 0.05:
                        new_mirror = True  # selfie camera: right hand -> camera left
                    elif abs(mirror_cal_peak_pos) > abs(mirror_cal_peak_neg) + 0.05:
                        new_mirror = False  # standard camera: right hand -> camera right
                    else:
                        new_mirror = config.MIRROR_GESTURE_X  # ambiguous, keep current
                    if new_mirror != config.MIRROR_GESTURE_X:
                        config.MIRROR_GESTURE_X = new_mirror
                        config.MIRROR_GAZE_X = new_mirror
                        engine.mirror = new_mirror
                        config.save_calibration()
                        triggered_banner = f"AUTO-DETECT: Mirror {'ON (Selfie Cam)' if new_mirror else 'OFF (Standard Cam)'} — Saved!"
                        triggered_banner_until = now + 3.0
                        print(f"[TV-Calibrate] Auto-mirror set: MIRROR_GESTURE_X={new_mirror}")
                    else:
                        triggered_banner = f"Mirror setting confirmed: {'ON' if config.MIRROR_GESTURE_X else 'OFF'} (no change)"
                        triggered_banner_until = now + 2.0
                mirror_cal_phase = False  # advance to main steps
                engine.history.clear()
                engine.last_swipe_time = 0.0
                engine.last_swipe_fired_t = 0.0
                engine.rebound_lockout_until = 0.0
                engine.hand_must_settle = False

        # ── MAIN STEP STATE MACHINE (only runs when mirror_cal_phase is done) ──
        if not mirror_cal_phase:
            time_in_step = now - step_start_t
            cur_limit = step_durations[step] if step < len(step_durations) else 15.0

            if step == 0:
                # Baseline check (4.0s)
                if time_in_step >= cur_limit:
                    step = 1
                    step_start_t = now
                    step_advance_at = 0.0
                    engine.history.clear()
                    engine.last_swipe_time = 0.0
                    engine.last_swipe_fired_t = 0.0
                    engine.rebound_lockout_until = 0.0
                    engine.hand_must_settle = False

            elif step == 1:
                # Swipe Right Test (30.0s)
                if len(engine.history) >= 2:
                    dx = engine.history[-1][3] - engine.history[0][3]
                    dt = max(0.02, engine.history[-1][2] - engine.history[0][2])
                    sp = abs(dx) / dt
                    if abs(dx) > measured["swipe_right_dx"]: measured["swipe_right_dx"] = abs(dx)
                    if sp > measured["swipe_right_speed"]: measured["swipe_right_speed"] = sp
                if gesture == "SWIPE_RIGHT":
                    measured["swipe_right_ok"] = True
                    if step_advance_at == 0.0:
                        step_advance_at = now + 1.8  # auto-advance in 1.8s after success
                if (step_advance_at > 0.0 and now >= step_advance_at) or (time_in_step >= cur_limit):
                    step = 2
                    step_start_t = now
                    step_advance_at = 0.0
                    engine.history.clear()

            elif step == 2:
                # Return Stroke Test (8.0s)
                if not measured["swipe_right_ok"]:
                    # Skip return stroke if step 1 didn't pass
                    step = 3
                    step_start_t = now
                    step_advance_at = 0.0
                    engine.history.clear()
                else:
                    if gesture in ("SWIPE_LEFT", "SWIPE_RIGHT"):
                        measured["return_stroke_blocked"] = False
                    if time_in_step >= cur_limit:
                        step = 3
                        step_start_t = now
                        step_advance_at = 0.0
                        engine.history.clear()
                        engine.last_swipe_time = 0.0
                        engine.last_swipe_fired_t = 0.0
                        engine.rebound_lockout_until = 0.0
                        engine.hand_must_settle = False

            elif step == 3:
                # Swipe Left Test (30.0s)
                if len(engine.history) >= 2:
                    dx = engine.history[-1][3] - engine.history[0][3]
                    dt = max(0.02, engine.history[-1][2] - engine.history[0][2])
                    sp = abs(dx) / dt
                    if abs(dx) > measured["swipe_left_dx"]: measured["swipe_left_dx"] = abs(dx)
                    if sp > measured["swipe_left_speed"]: measured["swipe_left_speed"] = sp
                if gesture == "SWIPE_LEFT":
                    measured["swipe_left_ok"] = True
                    if step_advance_at == 0.0:
                        step_advance_at = now + 1.8  # auto-advance in 1.8s after success
                if (step_advance_at > 0.0 and now >= step_advance_at) or (time_in_step >= cur_limit):
                    step = 4
                    step_start_t = now
                    step_advance_at = 0.0
                    engine.history.clear()
                    engine.last_swipe_time = 0.0
                    engine.last_swipe_fired_t = 0.0
                    engine.rebound_lockout_until = 0.0
                    engine.hand_must_settle = False

            elif step == 4:
                # Swipe Up (Robot Face) Test (30.0s)
                if len(engine.history) >= 2:
                    dy = engine.history[-1][4] - engine.history[0][4]
                    dt = max(0.02, engine.history[-1][2] - engine.history[0][2])
                    sp = abs(dy) / dt
                    if abs(dy) > measured["swipe_up_dy"]: measured["swipe_up_dy"] = abs(dy)
                    if sp > measured["swipe_up_speed"]: measured["swipe_up_speed"] = sp
                if gesture == "SWIPE_UP":
                    measured["swipe_up_ok"] = True
                    if step_advance_at == 0.0:
                        step_advance_at = now + 1.8  # auto-advance in 1.8s after success
                if (step_advance_at > 0.0 and now >= step_advance_at) or (time_in_step >= cur_limit):
                    step = 5
                    step_start_t = now
                    step_advance_at = 0.0
                    engine.history.clear()
                    engine.last_swipe_time = 0.0
                    engine.last_swipe_fired_t = 0.0
                    engine.rebound_lockout_until = 0.0
                    engine.hand_must_settle = False

            elif step == 5:
                # Walk Across Room Test (12.0s)
                if engine.is_presenter_walking:
                    measured["walk_detected_count"] += 1
                if gesture in ("SWIPE_LEFT", "SWIPE_RIGHT", "SWIPE_UP", "SWIPE_DOWN"):
                    measured["walk_false_swipes"] += 1
                    print(f"[{time.strftime('%H:%M:%S')}] [TV-Calibrate] FALSE SWIPE ON WALK: {gesture}")
                if time_in_step >= cur_limit:
                    step = 6
                    step_start_t = now
                    step_advance_at = 0.0
                    print("\n" + "=" * 68)
                    print("                  CALIBRATION RESULTS SUMMARY")
                    print("=" * 68)
                    print(f"Swipe Right (Next Slide) : {'PASSED' if measured['swipe_right_ok'] else 'FAILED'} (Max dx: {measured['swipe_right_dx']:.2f}, speed: {measured['swipe_right_speed']:.2f})")
                    print(f"Return Stroke Rejection  : {'PASSED (Blocked)' if measured['return_stroke_blocked'] else 'FAILED (Leaked)'}")
                    print(f"Swipe Left (Prev Slide)  : {'PASSED' if measured['swipe_left_ok'] else 'FAILED'} (Max dx: {measured['swipe_left_dx']:.2f}, speed: {measured['swipe_left_speed']:.2f})")
                    print(f"Swipe Up (Robot Face)    : {'PASSED' if measured['swipe_up_ok'] else 'FAILED'} (Max dy: {measured['swipe_up_dy']:.2f}, speed: {measured['swipe_up_speed']:.2f})")
                    walk_res = "PASSED (0 False Swipes)" if measured["walk_false_swipes"] == 0 else f"FAILED ({measured['walk_false_swipes']} False Swipes)"
                    print(f"Walk Rejection Test      : {walk_res}")
                    print(f"Mirror Mode Status       : {'MIRRORED (Natural)' if config.MIRROR_GESTURE_X else 'NATIVE (Unmirrored)'}")
                    print("=" * 68)
                    sys.stdout.flush()

            # expose for draw section
            time_in_step = time_in_step  # already set above
            cur_limit = cur_limit        # already set above
        else:
            # During mirror cal phase: expose dummy values for draw section
            time_in_step = now - mirror_cal_t
            cur_limit = 5.0

        # ── DRAW TV DISPLAY ──────────────────────────────────────────────────
        screen.fill(VOID)

        # Header Title Bar
        header_rect = pygame.Rect(0, 0, sw, int(sh * 0.09))
        pygame.draw.rect(screen, PANEL, header_rect)
        pygame.draw.line(screen, BORDER, (0, header_rect.height), (sw, header_rect.height), 1)

        title = font_large.render("TARS OPTICAL GESTURE CALIBRATION & SENSOR TELEMETRY", True, AMBER)
        screen.blit(title, (max(20, int(sw * 0.03)), header_rect.height // 2 - title.get_height() // 2))

        # Mirror toggle status badge in header
        mirror_str = f"MIRROR: [{'ON' if config.MIRROR_GESTURE_X else 'OFF'}]"
        m_surf = font_med.render(mirror_str, True, CYAN if config.MIRROR_GESTURE_X else MUTED)
        step_tag = f"PHASE {min(6, step + 1)} / 6" if step < 6 else "DIAGNOSTIC COMPLETE"
        tag_surf = font_med.render(step_tag, True, MINT)

        screen.blit(m_surf, (sw - tag_surf.get_width() - m_surf.get_width() - max(40, int(sw * 0.05)), header_rect.height // 2 - m_surf.get_height() // 2))
        screen.blit(tag_surf, (sw - tag_surf.get_width() - max(20, int(sw * 0.03)), header_rect.height // 2 - tag_surf.get_height() // 2))

        # Camera View Box in Center
        cam_box_w = int(sw * 0.52)
        cam_box_h = int(sh * 0.50)
        cam_box_x = (sw - cam_box_w) // 2
        cam_box_y = int(sh * 0.11)

        pygame.draw.rect(screen, PANEL, (cam_box_x, cam_box_y, cam_box_w, cam_box_h), border_radius=10)
        pygame.draw.rect(screen, BORDER, (cam_box_x, cam_box_y, cam_box_w, cam_box_h), width=2, border_radius=10)

        if frame is not None:
            try:
                small_f = cv2.resize(frame, (cam_box_w, cam_box_h), interpolation=cv2.INTER_LINEAR)
                rgb = cv2.cvtColor(small_f, cv2.COLOR_BGR2RGB)
                cam_surf = pygame.image.frombuffer(rgb.tobytes(), (cam_box_w, cam_box_h), "RGB")
                screen.blit(cam_surf, (cam_box_x, cam_box_y))
            except Exception:
                pass

        # Draw detected face boxes (CYAN)
        for fbx, fby, fbw, fbh in detected_face_boxes:
            fx = cam_box_x + int(fbx * cam_box_w)
            fy = cam_box_y + int(fby * cam_box_h)
            fw_box = int(fbw * cam_box_w)
            fh_box = int(fbh * cam_box_h)
            pygame.draw.rect(screen, CYAN, (fx, fy, fw_box, fh_box), 2, border_radius=4)
            lbl = font_small.render(f"HEAD ({fbx + fbw*0.5:.2f}, {fby + fbh*0.5:.2f})", True, CYAN)
            screen.blit(lbl, (fx, max(cam_box_y + 4, fy - 18)))

        # Draw hand position (MINT)
        if engine.hand_box:
            hbx, hby, hbw, hbh = engine.hand_box
            hx = cam_box_x + int(hbx * cam_box_w)
            hy = cam_box_y + int(hby * cam_box_h)
            hw = int(hbw * cam_box_w)
            hh = int(hbh * cam_box_h)
            pygame.draw.rect(screen, MINT, (hx, hy, hw, hh), 2, border_radius=4)
            tip_str = f"HAND ({engine.hand_tip[0]:.2f}, {engine.hand_tip[1]:.2f})"
            lbl = font_small.render(tip_str, True, MINT)
            screen.blit(lbl, (hx, max(cam_box_y + 4, hy - 18)))

        # Draw motion trail
        if len(engine.history) >= 2:
            pts = [(cam_box_x + int(p[3] * cam_box_w), cam_box_y + int(p[4] * cam_box_h)) for p in engine.history]
            for k in range(len(pts) - 1):
                col = MINT if (triggered_banner_until > now) else CYAN
                pygame.draw.line(screen, col, pts[k], pts[k+1], 3)
                pygame.draw.circle(screen, col, pts[k+1], 4)

        # Draw Walking Lockout Banner over camera box when walking is detected
        if engine.is_presenter_walking:
            walk_badge_rect = pygame.Rect(cam_box_x + 10, cam_box_y + 10, cam_box_w - 20, 32)
            pygame.draw.rect(screen, (40, 24, 8), walk_badge_rect, border_radius=5)
            pygame.draw.rect(screen, AMBER, walk_badge_rect, width=2, border_radius=5)
            walk_txt = font_med.render("WALKING DETECTED // GESTURES SAFELY MUTED", True, AMBER)
            screen.blit(walk_txt, (walk_badge_rect.centerx - walk_txt.get_width() // 2, walk_badge_rect.centery - walk_txt.get_height() // 2))

        # Progress / Countdown Bar under camera box
        if step < 6:
            bar_w = cam_box_w
            bar_h = 6
            bar_x = cam_box_x
            bar_y = cam_box_y + cam_box_h + 8
            pygame.draw.rect(screen, (25, 32, 45), (bar_x, bar_y, bar_w, bar_h), border_radius=3)
            pct = max(0.0, min(1.0, 1.0 - (time_in_step / cur_limit)))
            filled_w = int(bar_w * pct)
            if filled_w > 0:
                col = MINT if pct > 0.3 else AMBER
                pygame.draw.rect(screen, col, (bar_x, bar_y, filled_w, bar_h), border_radius=3)

        # Draw Big Sci-Fi Instructions at Bottom
        instr_y = cam_box_y + cam_box_h + int(sh * 0.035)

        # ── Live Gesture Prediction (which swipe WILL fire based on current hand velocity) ──
        # Compute live dx over the last 0.3s of history
        live_pred = ""
        live_pred_color = MUTED
        live_dx_pct = 0.0
        live_dy_pct = 0.0
        swipe_dist_thresh = getattr(config, "GESTURE_SWIPE_DISTANCE", 0.18)
        swipe_vert_thresh = getattr(config, "GESTURE_VERTICAL_DISTANCE", 0.18)
        if len(engine.history) >= 2:
            h0 = engine.history[0]
            h1 = engine.history[-1]
            raw_dx = h1[3] - h0[3]  # camera-space delta
            raw_dy = h1[4] - h0[4]
            live_dx_pct = abs(raw_dx) / swipe_dist_thresh  # fraction of threshold met
            live_dy_pct = abs(raw_dy) / swipe_vert_thresh
            mirror = getattr(config, "MIRROR_GESTURE_X", True)
            if abs(raw_dx) > abs(raw_dy) * 1.2 and abs(raw_dx) > 0.04:
                if mirror:
                    live_pred = "<< SWIPE_LEFT" if raw_dx > 0 else "SWIPE_RIGHT >>"
                    live_pred_color = CYAN if raw_dx > 0 else MINT
                else:
                    live_pred = "SWIPE_RIGHT >>" if raw_dx > 0 else "<< SWIPE_LEFT"
                    live_pred_color = MINT if raw_dx > 0 else CYAN
            elif abs(raw_dy) > abs(raw_dx) * 1.2 and abs(raw_dy) > 0.04:
                live_pred = "^^^ SWIPE_UP" if raw_dy < 0 else "vvv SWIPE_DOWN"
                live_pred_color = AMBER
            settle_str = " [SETTLING...]" if engine.hand_must_settle else ""
            pred_str = f"WILL FIRE: {live_pred}{settle_str}" if live_pred else "WILL FIRE: (hold still or move hand)"
        else:
            pred_str = "WILL FIRE: (no hand detected)"
        pred_surf = font_med.render(pred_str, True, live_pred_color if live_pred else MUTED)
        screen.blit(pred_surf, (sw // 2 - pred_surf.get_width() // 2, instr_y - pred_surf.get_height() - 8))

        # ── Distance + Speed live bars (steps 1-4) ──
        if 1 <= step <= 4:
            bar_area_y = instr_y - pred_surf.get_height() - 36
            bar_total_w = int(sw * 0.44)
            bar_h2 = 10
            bar_left = sw // 2 - bar_total_w // 2

            # Travel bar
            travel_pct = min(1.0, live_dx_pct if step in (1, 2, 3) else live_dy_pct)
            pygame.draw.rect(screen, (20, 28, 40), (bar_left, bar_area_y, bar_total_w, bar_h2), border_radius=4)
            fill_w = int(bar_total_w * travel_pct)
            if fill_w > 0:
                col_t = MINT if travel_pct >= 1.0 else (AMBER if travel_pct > 0.5 else CYAN)
                pygame.draw.rect(screen, col_t, (bar_left, bar_area_y, fill_w, bar_h2), border_radius=4)
            # threshold tick
            pygame.draw.line(screen, TEXT_WHITE, (bar_left + bar_total_w, bar_area_y - 3), (bar_left + bar_total_w, bar_area_y + bar_h2 + 3), 2)
            thresh_lbl = font_small.render(f"TRAVEL: {int(travel_pct*100)}% / 100% needed", True, MUTED)
            screen.blit(thresh_lbl, (bar_left, bar_area_y - thresh_lbl.get_height() - 2))

        if mirror_cal_phase:
            # ── AUTO-MIRROR DETECTION SCREEN ──
            rem_mc = max(0.0, 5.0 - (now - mirror_cal_t))
            # Progress bar for mirror cal countdown
            bar_w_mc = int(sw * 0.60)
            bar_x_mc = sw // 2 - bar_w_mc // 2
            bar_y_mc = int(sh * 0.78)
            pygame.draw.rect(screen, (20, 28, 40), (bar_x_mc, bar_y_mc, bar_w_mc, 10), border_radius=4)
            pct_mc = max(0.0, 1.0 - rem_mc / 5.0)
            fill_mc = int(bar_w_mc * pct_mc)
            det_col = MINT if mirror_cal_detected else AMBER
            if fill_mc > 0:
                pygame.draw.rect(screen, det_col, (bar_x_mc, bar_y_mc, fill_mc, 10), border_radius=4)

            main_txt = font_huge.render(
                f"SETUP: SWIPE YOUR RIGHT HAND TO YOUR RIGHT  ({rem_mc:.1f}s)", True, AMBER)
            status_mc = "MOTION DETECTED" if mirror_cal_detected else "(no motion yet — please swipe right now)"
            sub_txt = font_med.render(
                f"Do a wide sweep from center to YOUR RIGHT side and hold.  "
                f"Status: {status_mc}.  Auto-detects your camera orientation.",
                True, CYAN)
            # Show what was detected live
            if mirror_cal_detected:
                if abs(mirror_cal_peak_neg) > abs(mirror_cal_peak_pos) + 0.05:
                    detect_hint = "DETECTED: Standard Facing Camera (Hand Right = Camera Left) -> Setting Mirror=ON"
                elif abs(mirror_cal_peak_pos) > abs(mirror_cal_peak_neg) + 0.05:
                    detect_hint = "DETECTED: Mirrored Camera Stream (Hand Right = Camera Right) -> Setting Mirror=OFF"
                else:
                    detect_hint = "DETECTED: Ambiguous — keeping current setting"
                det_surf = font_med.render(detect_hint, True, MINT)
                screen.blit(det_surf, (sw // 2 - det_surf.get_width() // 2, bar_y_mc + 20))
        elif step == 0:
            rem = max(0.0, cur_limit - time_in_step)
            main_txt = font_huge.render(f"STAND STILL ({rem:.1f}s)", True, TEXT_WHITE)
            sub_txt = font_med.render("Calibrating ambient baseline — arms at sides, face the camera.", True, MUTED)
        elif step == 1:
            rem = max(0.0, cur_limit - time_in_step)
            status_txt = "  ✓ PASSED! (Advancing...)" if measured["swipe_right_ok"] else ""
            main_txt = font_huge.render(f"NEXT SLIDE: Sweep arm YOUR RIGHT >>> ({rem:.1f}s){status_txt}", True, MINT if measured["swipe_right_ok"] else AMBER)
            thresh_pct = int(swipe_dist_thresh * 100)
            sub_txt = font_med.render(
                f"Move right hand physically TO YOUR RIGHT. Watch 'WILL FIRE:' indicator above. "
                f"(If it says SWIPE_LEFT, press [M] to invert). Need {thresh_pct}% travel.",
                True, CYAN)
        elif step == 2:
            rem = max(0.0, cur_limit - time_in_step)
            main_txt = font_huge.render(f"KEEP HAND RAISED & BRING IT BACK ({rem:.1f}s)", True, CYAN)
            sub_txt = font_med.render(
                "Retract your right hand back to center with hand STILL RAISED at chest level. "
                "The system must NOT fire a new gesture. (Press SPACE to skip).",
                True, MUTED)
        elif step == 3:
            rem = max(0.0, cur_limit - time_in_step)
            status_txt = "  ✓ PASSED! (Advancing...)" if measured["swipe_left_ok"] else ""
            main_txt = font_huge.render(f"PREV SLIDE: Sweep arm <<< YOUR LEFT ({rem:.1f}s){status_txt}", True, MINT if measured["swipe_left_ok"] else AMBER)
            thresh_pct = int(swipe_dist_thresh * 100)
            sub_txt = font_med.render(
                f"Drop hand to waist first, then sweep left hand physically TO YOUR LEFT. "
                f"Watch 'WILL FIRE:' indicator above. Need {thresh_pct}% travel.",
                True, CYAN)
        elif step == 4:
            rem = max(0.0, cur_limit - time_in_step)
            status_txt = "  ✓ PASSED! (Advancing...)" if measured["swipe_up_ok"] else ""
            invert_str = "INVERTED" if getattr(config, "INVERT_CAMERA_Y", False) else "NORMAL"
            main_txt = font_huge.render(f"SHOW FACE: Thrust hand UPWARD ^^^ ({rem:.1f}s){status_txt}", True, MINT if measured["swipe_up_ok"] else AMBER)
            thresh_pct = int(swipe_vert_thresh * 100)
            sub_txt = font_med.render(
                f"Start hand at chest level, thrust it UPWARD toward your head (watch 'WILL FIRE: ^^^ SWIPE_UP'). "
                f"If 'vvv SWIPE_DOWN' shows instead, press [Y] to flip vertical axis. [{invert_str}]",
                True, CYAN)
        elif step == 5:
            rem = max(0.0, cur_limit - time_in_step)
            main_txt = font_huge.render(f"WALK NATURALLY ACROSS ROOM ({rem:.1f}s)", True, AMBER)
            sub_txt = font_med.render(
                f"Pace back and forth normally with arms hanging at sides — DO NOT gesture. "
                f"False triggers so far: {measured['walk_false_swipes']}  (goal: 0)",
                True, CYAN)
        else:
            main_txt = font_huge.render("CALIBRATION COMPLETE!", True, MINT)
            r_status = "PASS" if measured['swipe_right_ok'] else "FAIL"
            l_status = "PASS" if measured['swipe_left_ok'] else "FAIL"
            u_status = "PASS" if measured['swipe_up_ok'] else "FAIL"
            ret_status = "PASS" if measured['return_stroke_blocked'] else "LEAKED"
            w_status = "PASS (0 leaks)" if (measured['walk_false_swipes'] == 0) else f"FAIL ({measured['walk_false_swipes']} leaks)"
            sub_txt = font_large.render(f"Right: [{r_status}]  |  Left: [{l_status}]  |  Return Filter: [{ret_status}]  |  Face: [{u_status}]  |  Walk Rejection: [{w_status}]", True, TEXT_WHITE)

        screen.blit(main_txt, (sw // 2 - main_txt.get_width() // 2, instr_y))
        screen.blit(sub_txt, (sw // 2 - sub_txt.get_width() // 2, instr_y + main_txt.get_height() + 8))

        # Flash Action Success Banner
        if triggered_banner_until > now:
            banner_rect = pygame.Rect(sw // 2 - 340, int(sh * 0.02), 680, 48)
            pygame.draw.rect(screen, (10, 30, 20), banner_rect, border_radius=6)
            pygame.draw.rect(screen, MINT, banner_rect, width=2, border_radius=6)
            b_txt = font_large.render(triggered_banner, True, MINT)
            screen.blit(b_txt, (sw // 2 - b_txt.get_width() // 2, banner_rect.y + 10))

        # Bottom exit & controls hint
        hint_txt = "[M] FLIP HORIZONTAL MIRROR  //  [Y] FLIP VERTICAL AXIS  //  [SPACE] NEXT PHASE  //  [ESC] EXIT"
        hint = font_small.render(hint_txt, True, (80, 95, 120))
        screen.blit(hint, (sw // 2 - hint.get_width() // 2, sh - max(22, int(sh * 0.028))))

        pygame.display.flip()
        clock.tick(30)

        # After step 6 summary displayed for 15 seconds, exit
        if step == 6 and time_in_step > 15.0:
            running = False

    print("\n" + "=" * 68)
    print("                  CALIBRATION RESULTS SUMMARY")
    print("=" * 68)
    print(f"Swipe Right (Next Slide) : {'PASSED' if measured['swipe_right_ok'] else 'FAILED'} (Max dx: {measured['swipe_right_dx']:.2f}, speed: {measured['swipe_right_speed']:.2f})")
    print(f"Return Stroke Rejection  : {'PASSED (Blocked)' if measured['return_stroke_blocked'] else 'FAILED (Leaked)'}")
    print(f"Swipe Left (Prev Slide)  : {'PASSED' if measured['swipe_left_ok'] else 'FAILED'} (Max dx: {measured['swipe_left_dx']:.2f}, speed: {measured['swipe_left_speed']:.2f})")
    print(f"Swipe Up (Robot Face)    : {'PASSED' if measured['swipe_up_ok'] else 'FAILED'} (Max dy: {measured['swipe_up_dy']:.2f}, speed: {measured['swipe_up_speed']:.2f})")
    walk_res = "PASSED (0 False Swipes)" if measured["walk_false_swipes"] == 0 else f"FAILED ({measured['walk_false_swipes']} False Swipes)"
    print(f"Walk Rejection Test      : {walk_res}")
    print(f"Mirror Mode Status       : {'MIRRORED (Natural)' if config.MIRROR_GESTURE_X else 'NATIVE (Unmirrored)'}")
    print("=" * 68)
    sys.stdout.flush()

    try:
        grabber.stop()
        if hasattr(grabber, "t") and grabber.t.is_alive():
            grabber.t.join(timeout=0.5)
    except Exception:
        pass

    try:
        pygame.display.quit()
        pygame.quit()
    except Exception:
        pass
    os._exit(0)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[Exited]")
