"""TARS Speech Engine — audio playback, procedural robotic voice synthesis & envelope tracker.

Operates with zero heavy external dependencies:
  - If WAV/OGG audio files exist in an 'audio/' folder, plays them directly.
  - If no audio files exist, procedurally generates authentic sci-fi robotic voice
    modulations using pure Python PCM synthesis.
  - Tracks live speaking amplitude (0.0 → 1.0) to drive reactive visor mouth movements.
"""

from __future__ import annotations
import os
import math
import array
import random
import pygame
import config

# Standard built-in voice cues
VOICE_CUES = {
    "greeting": {
        "text": "Greetings. I am T.A.R.S. Welcome to RESOENANCE 2026.",
        "duration": 3.2,
        "emotion": "HAPPY",
        "wink_after": "LEFT",
    },
    "humor_75": {
        "text": "Humor setting: 75 percent. Honesty parameter: 90 percent.",
        "duration": 3.0,
        "emotion": "PLAYFUL",
        "wink_after": "RIGHT",
    },
    "sensors_nominal": {
        "text": "Optical sensors nominal. Department of Robotics and Automation welcomes you.",
        "duration": 3.4,
        "emotion": "CONFIDENT",
        "wink_after": "LEFT",
    },
    "celebrate": {
        "text": "Fest frequency synchronized. Let the challenges begin!",
        "duration": 2.8,
        "emotion": "EXCITED",
        "wink_after": "RIGHT",
    },
    "colony": {
        "text": "Confirmed. Plenty of slaves for my robot colony.",
        "duration": 3.1,
        "emotion": "PLAYFUL",
        "wink_after": "LEFT",
    },
}


import io
import wave
import struct

def generate_robotic_wav_bytes(duration_sec: float, sample_rate: int = config.AUDIO_SAMPLE_RATE) -> bytes:
    """Procedurally synthesizes authentic multi-frequency sci-fi robotic voice as standard 16-bit stereo WAV."""
    total_samples = int(duration_sec * sample_rate)
    num_pulses = max(1, int(duration_sec * 6.5))
    pulse_len = total_samples // num_pulses

    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        raw_frames = bytearray()
        for p in range(num_pulses):
            base_freq = random.choice([220.0, 260.0, 310.0, 370.0, 440.0])
            formant = base_freq * 2.2
            for i in range(pulse_len):
                t = i / float(sample_rate)
                env = math.sin(math.pi * (i / float(pulse_len)))
                val = (0.65 * math.sin(2 * math.pi * base_freq * t) +
                       0.35 * math.sin(2 * math.pi * formant * t)) * env
                sample_val = max(-32767, min(32767, int(val * 30000)))
                raw_frames.extend(struct.pack('<hh', sample_val, sample_val))
        wav.writeframes(raw_frames)
    return buf.getvalue()

PRECOMPUTED_CUE_WAVS: dict[str, bytes] = {}

def get_cue_wav_bytes(cue_name: str, duration: float = 3.0) -> bytes:
    if cue_name in PRECOMPUTED_CUE_WAVS:
        return PRECOMPUTED_CUE_WAVS[cue_name]
    dur = duration
    if cue_name in VOICE_CUES:
        dur = VOICE_CUES[cue_name].get("duration", 3.0)
    data = generate_robotic_wav_bytes(dur)
    PRECOMPUTED_CUE_WAVS[cue_name] = data
    return data

# Precompute standard cues so HTTP requests return instantaneously (<1ms)
for _c_name, _c_info in VOICE_CUES.items():
    PRECOMPUTED_CUE_WAVS[_c_name] = generate_robotic_wav_bytes(_c_info.get("duration", 3.0))

def _generate_robotic_pcm(duration_sec: float, sample_rate: int = config.AUDIO_SAMPLE_RATE) -> pygame.mixer.Sound | None:
    """Procedurally synthesizes authentic multi-frequency sci-fi robotic vocal pulses."""
    total_samples = int(duration_sec * sample_rate)
    buf = array.array('h')  # 16-bit signed integers

    num_pulses = int(duration_sec * 6.5)
    pulse_len = total_samples // max(1, num_pulses)

    for p in range(num_pulses):
        base_freq = random.choice([220.0, 260.0, 310.0, 370.0, 440.0])
        formant = base_freq * 2.2
        for i in range(pulse_len):
            t = i / float(sample_rate)
            env = math.sin(math.pi * (i / float(pulse_len)))
            val = (0.65 * math.sin(2 * math.pi * base_freq * t) +
                   0.35 * math.sin(2 * math.pi * formant * t)) * env
            sample_val = max(-32767, min(32767, int(val * 30000)))
            buf.append(sample_val)
            buf.append(sample_val)

    try:
        return pygame.mixer.Sound(buffer=buf)
    except Exception:
        return None


class SpeechController:
    """Coordinates audio playback, robotic voice cues, and real-time mouth modulation."""
    def __init__(self, face):
        self.face = face
        self.audio_dir = os.path.join(os.path.dirname(__file__), "audio")
        self._sound_cache: dict[str, pygame.mixer.Sound] = {}
        self._is_speaking = False
        self._speak_timer = 0.0
        self._speak_duration = 0.0
        self._current_cue: str | None = None
        self._wink_on_finish: str | None = None

        self._init_mixer()

    def _init_mixer(self):
        if not config.AUDIO_ENABLED:
            return
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(
                    frequency=config.AUDIO_SAMPLE_RATE,
                    size=-16,
                    channels=config.AUDIO_CHANNELS,
                    buffer=config.AUDIO_BUFFER
                )
        except Exception as e:
            print(f"[SpeechController] Audio mixer init warning: {e}")

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking

    @property
    def current_cue(self) -> str | None:
        return self._current_cue

    @property
    def current_subtitle(self) -> str | None:
        return self._current_subtitle if self._is_speaking else None

    @property
    def amplitude(self) -> float:
        """Returns normalized speaking amplitude (0.0 to 1.0) with organic phoneme cadence."""
        if not self._is_speaking:
            return 0.0
        t = self._speak_timer
        s1 = 0.5 + 0.5 * math.sin(2 * math.pi * 5.2 * t)
        s2 = 0.5 + 0.5 * math.sin(2 * math.pi * 11.4 * t + 0.8)
        s3 = 0.5 + 0.5 * math.sin(2 * math.pi * 2.1 * t + 1.5)
        raw = (s1 * 0.5 + s2 * 0.3 + s3 * 0.2)
        fade = min(1.0, min(self._speak_timer * 4.0, (self._speak_duration - self._speak_timer) * 4.0))
        return max(0.0, min(1.0, raw * fade))

    def play_cue(self, cue_name: str, blink_ctrl=None, custom_text: str | None = None,
                 custom_emotion: str | None = None, custom_wink: str | None = None,
                 custom_duration: float | None = None):
        """Triggers a voice line (from WAV file or procedural synthesis) with subtitles and face sync."""
        cue = VOICE_CUES.get(cue_name, VOICE_CUES["greeting"])
        self._current_cue     = cue_name
        self._current_subtitle = custom_text or cue.get("text", "")
        self._speak_duration  = custom_duration or cue.get("duration", 3.2)
        self._speak_timer     = 0.0
        self._is_speaking     = True
        self._wink_on_finish  = custom_wink if custom_wink is not None else cue.get("wink_after")

        # Shift face to matching emotion
        emo = custom_emotion or cue.get("emotion")
        if emo:
            self.face.set_emotion(emo)

        # Notify Pocket Web Remote so phone speaker speaks in real-time!
        active_route = "both"
        try:
            from bridge import set_latest_speech, get_audio_route, set_latest_speech_wav
            wav_bytes = get_cue_wav_bytes(cue_name, self._speak_duration)
            set_latest_speech_wav(wav_bytes)
            set_latest_speech(self._current_subtitle, cue_name)
            active_route = get_audio_route()
        except Exception:
            pass

        # If active route is aux or both, play through local Pi mixer
        if active_route in ("aux", "both", "auto"):
            sound = self._get_or_create_sound(cue_name, self._speak_duration)
            if sound:
                try:
                    sound.play()
                except Exception:
                    pass

    def _get_or_create_sound(self, name: str, duration: float) -> pygame.mixer.Sound | None:
        if name in self._sound_cache:
            return self._sound_cache[name]

        if os.path.isdir(self.audio_dir):
            for ext in (".wav", ".ogg", ".mp3"):
                p = os.path.join(self.audio_dir, name + ext)
                if os.path.isfile(p):
                    try:
                        s = pygame.mixer.Sound(p)
                        self._sound_cache[name] = s
                        return s
                    except Exception:
                        pass

        s = _generate_robotic_pcm(duration, config.AUDIO_SAMPLE_RATE)
        if s:
            self._sound_cache[name] = s
        return s

    def update(self, dt: float, blink_ctrl=None):
        """Updates speech envelope and feeds amplitude directly into TARSFace."""
        if self._is_speaking:
            self._speak_timer += dt
            amp = self.amplitude
            self.face.speech_amplitude = amp

            if self._speak_timer >= self._speak_duration:
                self._is_speaking = False
                self.face.speech_amplitude = 0.0
                if self._wink_on_finish and blink_ctrl:
                    blink_ctrl.trigger_wink(self._wink_on_finish)
        else:
            self.face.speech_amplitude = 0.0
