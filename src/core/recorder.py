import io
import wave
import threading
from typing import Optional, Callable

import numpy as np


def play_beep(freq: int = 880, duration_ms: int = 80):
    """Play a short beep. Non-blocking."""
    def _play():
        try:
            import sys
            if sys.platform == "win32":
                import winsound
                winsound.Beep(freq, duration_ms)
                return
        except Exception:
            pass
        # Fallback: generate tone via sounddevice
        try:
            import sounddevice as sd
            sr = 22050
            t = np.linspace(0, duration_ms / 1000, int(sr * duration_ms / 1000), False)
            tone = np.sin(2 * np.pi * freq * t) * 0.35
            # Fade in/out to avoid clicks
            fade = max(1, int(sr * 0.005))
            tone[:fade] *= np.linspace(0, 1, fade)
            tone[-fade:] *= np.linspace(1, 0, fade)
            sd.play(tone.astype(np.float32), sr, blocking=True)
        except Exception:
            pass

    threading.Thread(target=_play, daemon=True).start()


class AudioRecorder:
    """Records microphone audio and returns WAV bytes.

    Optionally calls `level_callback(float)` on every audio chunk with an
    RMS level in [0, 1] for real-time waveform visualization.
    """

    SAMPLE_RATE = 16000
    CHANNELS = 1
    CHUNK = 1024

    def __init__(self):
        self._recording = False
        self._frames: list = []
        self._thread: Optional[threading.Thread] = None

    def start(self, level_callback: Optional[Callable[[float], None]] = None):
        try:
            import sounddevice as sd  # noqa: F401
        except ImportError:
            raise ImportError("录音功能需要安装 sounddevice：pip install sounddevice")

        self._recording = True
        self._frames = []

        def _record():
            import sounddevice as sd

            with sd.InputStream(
                samplerate=self.SAMPLE_RATE,
                channels=self.CHANNELS,
                dtype="int16",
                blocksize=self.CHUNK,
            ) as stream:
                while self._recording:
                    data, _ = stream.read(self.CHUNK)
                    self._frames.append(data.copy())
                    if level_callback:
                        rms = float(np.sqrt(np.mean(data.astype(np.float32) ** 2))) / 32768.0
                        level_callback(min(1.0, rms * 10))

        self._thread = threading.Thread(target=_record, daemon=True)
        self._thread.start()

    def stop(self) -> Optional[bytes]:
        self._recording = False
        if self._thread:
            self._thread.join(timeout=3)

        if not self._frames:
            return None

        audio_data = np.concatenate(self._frames, axis=0)

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(self.CHANNELS)
            wf.setsampwidth(2)  # int16 = 2 bytes
            wf.setframerate(self.SAMPLE_RATE)
            wf.writeframes(audio_data.tobytes())

        return buf.getvalue()

    @property
    def is_recording(self) -> bool:
        return self._recording
