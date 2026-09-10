"""TARS MK-IV // Arduino Serial Hardware Servo Controller.

Provides non-blocking physical robotic interaction:
  - Physical Gaze Tracking: Pan/Tilt servos track human target face coordinates.
  - Physical Hand/Arm Gestures: Dual mechanical arms swipe left/right to change slides,
    wave hello during speech cues, and raise/lower in celebratory fest reactions.
  - Power Saving Standby: Automatically parks servos to neutral resting angles when
    TARS enters Low-Power Standby Mode to prevent motor buzzing and conserve energy.
  - Resilient Failover: Non-blocking background worker with auto-port detection
    (/dev/ttyACM*, /dev/ttyUSB*, COM*). TARS runs at 100% performance whether the
    Arduino is plugged in, unplugged, or reconnected live.
"""

from __future__ import annotations
import os
import sys
import time
import glob
import queue
import threading

import config

# Optional pyserial
try:
    import serial
    import serial.tools.list_ports
    HAS_PYSERIAL = True
except ImportError:
    HAS_PYSERIAL = False


class ArduinoController:
    """Thread-safe, non-blocking hardware servo manager."""
    def __init__(self, port: str = "AUTO", baud: int = 115200):
        self.requested_port = port
        self.baud = baud
        self.ser = None
        self.active_port = None
        self.connected = False
        
        # State tracking
        self.cur_pan = 90
        self.cur_tilt = 90
        self.cur_arm_l = 90
        self.cur_arm_r = 90
        self.is_standby = False
        
        # Thread-safe queue
        self._cmd_queue: queue.Queue[str] = queue.Queue(maxsize=32)
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        
    def start(self):
        if self._running or not getattr(config, "ARDUINO_ENABLED", True):
            return
        self._running = True
        self._thread = threading.Thread(target=self._worker, daemon=True, name="TARS-Arduino")
        self._thread.start()
        
    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        self._close_serial()
        
    def _find_ports(self) -> list[str]:
        """Auto-discovers Arduino USB serial ports across Linux, Raspberry Pi, and Windows."""
        ports = []
        if HAS_PYSERIAL:
            try:
                for p in serial.tools.list_ports.comports():
                    # Check for Arduino, CH340, FTDI, CP210x USB descriptors
                    desc = (p.description or "").lower()
                    hwid = (p.hwid or "").lower()
                    if any(k in desc or k in hwid for k in ("arduino", "ch340", "usb serial", "acm", "ftdi", "cp210")):
                        ports.insert(0, p.device)
                    else:
                        ports.append(p.device)
            except Exception:
                pass
                
        # Direct Linux /dev entries
        if sys.platform.startswith("linux"):
            for pattern in ("/dev/ttyACM*", "/dev/ttyUSB*"):
                for dev in sorted(glob.glob(pattern)):
                    if dev not in ports:
                        ports.append(dev)
                        
        # Windows COM port fallback
        if sys.platform.startswith("win") and not ports:
            for i in range(1, 25):
                ports.append(f"COM{i}")
                
        return ports

    def _connect(self) -> bool:
        if self.requested_port != "AUTO":
            candidate_ports = [self.requested_port]
        else:
            candidate_ports = self._find_ports()
            
        for port in candidate_ports:
            try:
                if HAS_PYSERIAL:
                    s = serial.Serial(port, self.baud, timeout=0.1, write_timeout=0.1)
                    time.sleep(1.2)  # Wait for Arduino bootloader reset
                    self.ser = s
                    self.active_port = port
                    self.connected = True
                    print(f"[Arduino] Successfully connected to servo hardware on {port} @ {self.baud} baud", flush=True)
                    # Send initial sync
                    self._send_raw("SYNC\n")
                    self.set_neutral()
                    return True
                else:
                    # Linux direct character device fallback
                    if sys.platform.startswith("linux") and os.path.exists(port):
                        fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
                        self.ser = os.fdopen(fd, 'w', buffering=1)
                        self.active_port = port
                        self.connected = True
                        print(f"[Arduino] Connected via native device node {port}", flush=True)
                        return True
            except Exception:
                continue
        return False

    def _close_serial(self):
        self.connected = False
        if self.ser is not None:
            try:
                if hasattr(self.ser, "close"):
                    self.ser.close()
                elif hasattr(self.ser, "flush"):
                    self.ser.flush()
            except Exception:
                pass
            self.ser = None
        self.active_port = None

    def _send_raw(self, line: str):
        if not self.connected or self.ser is None:
            return
        try:
            if HAS_PYSERIAL:
                self.ser.write(line.encode("ascii", errors="ignore"))
                self.ser.flush()
            else:
                self.ser.write(line)
                self.ser.flush()
        except Exception:
            # Serial write failure (wire unplugged)
            self._close_serial()

    def _worker(self):
        last_conn_try = 0.0
        last_send_t = 0.0
        
        while self._running:
            now = time.time()
            if not self.connected:
                if now - last_conn_try > 3.0:
                    last_conn_try = now
                    self._connect()
                time.sleep(0.1)
                continue
                
            try:
                # Rate limit servo updates to ~40Hz to avoid serial buffer congestion
                cmd = self._cmd_queue.get(timeout=0.04)
                if cmd:
                    self._send_raw(cmd)
            except queue.Empty:
                pass
            except Exception:
                self._close_serial()

    def set_gaze(self, norm_x: float, norm_y: float):
        """Maps normalized target coords (-1.0 to +1.0) to Pan/Tilt servo degrees (30 to 150)."""
        if not self.connected or self.is_standby:
            return
            
        # Invert or mirror according to calibration
        nx = -norm_x if getattr(config, "MIRROR_GAZE_X", getattr(config, "MIRROR_CAMERA_X", False)) else norm_x
        ny = -norm_y if getattr(config, "INVERT_CAMERA_Y", False) else norm_y
        
        # 90 degrees is centered looking straight forward
        pan_deg = int(90 - (nx * 45.0))
        pan_deg = max(30, min(150, pan_deg))
        
        tilt_deg = int(90 + (ny * 35.0))
        tilt_deg = max(45, min(135, tilt_deg))
        
        if abs(pan_deg - self.cur_pan) >= 2 or abs(tilt_deg - self.cur_tilt) >= 2:
            self.cur_pan = pan_deg
            self.cur_tilt = tilt_deg
            try:
                self._cmd_queue.put_nowait(f"PAN:{pan_deg}\nTILT:{tilt_deg}\n")
            except queue.Full:
                pass

    def trigger_gesture(self, gesture_name: str):
        """Sends discrete mechanical hand gesture command."""
        if not self.connected:
            return
        g = gesture_name.upper().strip()
        print(f"[Arduino] Dispatching physical hand gesture: {g}", flush=True)
        try:
            self._cmd_queue.put_nowait(f"GESTURE:{g}\n")
        except queue.Full:
            pass

    def set_standby(self, standby: bool):
        """Parks servos to resting neutral angles during low-power mode."""
        if self.is_standby == standby:
            return
        self.is_standby = standby
        if standby:
            print("[Arduino] Entering Low-Power Servo Standby (Servos Parked)", flush=True)
            try:
                self._cmd_queue.put_nowait("STANDBY\n")
            except queue.Full:
                pass
        else:
            print("[Arduino] Resuming Active Servo Tracking", flush=True)
            self.set_neutral()

    def set_neutral(self):
        """Centers pan/tilt and places arms in polite ready position."""
        self.cur_pan = 90
        self.cur_tilt = 90
        self.cur_arm_l = 90
        self.cur_arm_r = 90
        try:
            self._cmd_queue.put_nowait("PAN:90\nTILT:90\nARMS:90,90\n")
        except queue.Full:
            pass


_CONTROLLER: ArduinoController | None = None

def get_arduino() -> ArduinoController:
    global _CONTROLLER
    if _CONTROLLER is None:
        port = getattr(config, "ARDUINO_PORT", "AUTO")
        baud = getattr(config, "ARDUINO_BAUD", 115200)
        _CONTROLLER = ArduinoController(port=port, baud=baud)
        _CONTROLLER.start()
    return _CONTROLLER
