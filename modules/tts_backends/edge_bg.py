"""
Edge-TTS BG backend — Bulgarian (and English) neural dubbing via Microsoft Edge's
online text-to-speech service.

Why this backend exists
-----------------------
VoxCPM 2 (30 langs), Qwen3-TTS (10 langs) and XTTS-v2 (17 langs) all ship **no
Bulgarian voice**. Microsoft Edge exposes two native `bg-BG` voices:

* ``bg-BG-BorislavNeural`` (male)
* ``bg-BG-KalinaNeural``   (female)

Trade-offs (by design, surfaced in the UI):
* **0 VRAM** — nothing is loaded on the GPU, so it runs next to a 12 GB LLM.
* **Requires internet** — every segment is a websocket round-trip to Microsoft.
* **No voice cloning** — ``voice_design`` is False, so "Clone from original" /
  "Clone from file" are disabled in the UI when this engine is active.

Install with ``pip install edge-tts``.
"""

import os
import time
import numpy as np
import soundfile as sf

from .base import TTSBackend
from config import DEVICE, TEMP_DIR

# Native Bulgarian Edge voices, keyed by the app's gender radio labels.
BULGARIAN_VOICES = {
    "Man": "bg-BG-BorislavNeural",
    "Woman": "bg-BG-KalinaNeural",
    "male": "bg-BG-BorislavNeural",
    "female": "bg-BG-KalinaNeural",
    "m": "bg-BG-BorislavNeural",
    "f": "bg-BG-KalinaNeural",
}
DEFAULT_BULGARIAN_VOICE = "bg-BG-KalinaNeural"

# English fallback voice, so the engine still honours its "en" capability.
ENGLISH_VOICE = "en-US-EmmaMultilingualNeural"

# Edge clamps prosody to roughly -50% .. +100%. We stay in a conservative band so
# fast-fitted Bulgarian still sounds natural instead of chipmunked.
RATE_MIN = -50
RATE_MAX = 60

# Native speaking rate used to pre-compute the rate needed to fit a time slot.
FITTED_CPS = 8.0

# Network resilience: edge-tts occasionally drops the websocket mid-stream.
MAX_RETRIES = 3
RETRY_BACKOFF_S = 1.5


class EdgeBulgarianBackend(TTSBackend):
    """Cloud TTS backend providing native Bulgarian voices (no VRAM, no cloning)."""

    def __init__(self):
        super().__init__()
        self.device = DEVICE
        self._sample_rate = 24000
        self._warned_no_clone = False

    @property
    def name(self) -> str:
        return "Edge-TTS BG"

    @property
    def capabilities(self) -> dict:
        return {
            "languages": ["bg", "en"],
            "speed_control": True,        # native prosody rate (speed -> rate%)
            "duration_control": False,    # no exact-duration API; time_sync fits it
            "multi_speaker": False,
            "voice_design": False,        # cloning intentionally unsupported
            "vram_gb": 0.0,               # fully cloud: zero GPU footprint
            "sample_rate": self._sample_rate,
            "fitted_cps": FITTED_CPS,     # measured bg-BG neural speaking rate
            "fitted_speed_factor": 1.0,   # rate% handled natively inside generate()
            "requires_internet": True,    # surfaced by the UI as an offline warning
        }

    def is_available(self) -> bool:
        try:
            import edge_tts  # noqa: F401
            return True
        except ImportError:
            return False

    def load(self, ref_audio_path=None):
        """No model to load — the service is remote. Kept for API symmetry."""
        self.model = "edge-tts-remote"
        if ref_audio_path and not self._warned_no_clone:
            print(
                "[Edge-TTS BG] Voice cloning is not supported by this engine. "
                "Ignoring reference audio and using a native bg-BG voice. "
                "Switch to VoxCPM 2 in Tab 1 to clone a voice."
            )
            self._warned_no_clone = True

    def unload(self):
        self.model = None

    def _pick_voice(self, language: str, gender: str = "Woman") -> str:
        """Resolve the Edge voice name from the target language + gender."""
        code = str(language or "").strip().lower()
        # Accept both ISO ("bg") and NLLB ("bul_Cyrl") style codes.
        is_bulgarian = code.startswith("bg") or code.startswith("bul")
        if is_bulgarian:
            return BULGARIAN_VOICES.get(str(gender), DEFAULT_BULGARIAN_VOICE)
        return ENGLISH_VOICE

    @staticmethod
    def _clamp_rate(rate_pct: float) -> int:
        return int(max(RATE_MIN, min(RATE_MAX, round(rate_pct))))

    def _compute_rate(self, text: str, speed: float, duration: float = None) -> int:
        """
        Translate the pipeline's `speed` factor into an Edge `rate` percentage.

        When `duration` (the segment slot) is supplied we additionally anticipate
        overflow: if the text would naturally run past the slot, the rate is raised
        so time_sync rarely has to truncate. The voice is never slowed down.
        """
        rate_pct = (float(speed) - 1.0) * 100.0

        if duration and duration > 0 and text:
            natural = len(text) / FITTED_CPS
            if natural > duration:
                rate_pct = max(rate_pct, (natural / duration - 1.0) * 100.0 * 0.95)

        return self._clamp_rate(rate_pct)

    @staticmethod
    def _decode_to_wav(src_path: str, output_path: str) -> tuple:
        """
        Decode the MP3 produced by edge-tts into the WAV path expected by time_sync.

        Tries soundfile first (libsndfile >= 1.1 reads MP3), then pydub, then a raw
        ffmpeg call. Returns (sample_rate, n_samples).
        """
        try:
            data, sr = sf.read(src_path, dtype="float32", always_2d=False)
        except Exception:
            try:
                from pydub import AudioSegment
                seg = AudioSegment.from_file(src_path)
                samples = np.asarray(seg.get_array_of_samples(), dtype=np.float32)
                samples = samples / float(1 << (8 * seg.sample_width - 1))
                if seg.channels > 1:
                    samples = samples.reshape(-1, seg.channels)
                data, sr = samples, seg.frame_rate
            except Exception:
                import subprocess
                tmp_out = output_path + ".tmp.wav"
                try:
                    subprocess.run(
                        ["ffmpeg", "-y", "-v", "error", "-i", src_path, tmp_out],
                        check=True, capture_output=True, timeout=120,
                    )
                    os.replace(tmp_out, output_path)
                finally:
                    try:
                        if os.path.exists(tmp_out):
                            os.remove(tmp_out)
                    except OSError:
                        pass
                info = sf.info(output_path)
                return info.samplerate, info.frames

        if data.ndim > 1:
            data = data.mean(axis=1)

        tmp_out = output_path + ".tmp.wav"
        sf.write(tmp_out, data.astype(np.float32), sr)
        os.replace(tmp_out, output_path)
        return sr, len(data)

    def generate(self, text: str, language: str, output_path: str, ref_audio_path: str = None,
                 speed: float = 1.0, duration: float = None, gender: str = "Woman", **kwargs) -> dict:
        import edge_tts

        voice = self._pick_voice(language, gender)
        rate_pct = self._compute_rate(text, speed, duration)
        rate_str = f"{rate_pct:+d}%"

        # time_sync hands us .wav temp paths; edge-tts only emits MP3.
        base, _ext = os.path.splitext(output_path)
        mp3_path = f"{base}_edge.mp3"

        safe_preview = (text or "")[:60]
        print(f"[Edge-TTS BG] voice={voice} rate={rate_str} text='{safe_preview}'")

        last_error = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                communicate = edge_tts.Communicate(text or " ", voice=voice, rate=rate_str)
                communicate.save_sync(mp3_path)
                last_error = None
                break
            except Exception as e:
                last_error = e
                print(f"[Edge-TTS BG] Attempt {attempt}/{MAX_RETRIES} failed: {e}")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_S * attempt)

        if last_error is not None:
            raise RuntimeError(
                f"Edge-TTS BG failed after {MAX_RETRIES} attempts. This engine needs an "
                f"active internet connection to reach the Microsoft Edge speech service. "
                f"Last error: {last_error}"
            )

        try:
            sr, n_samples = self._decode_to_wav(mp3_path, output_path)
        finally:
            try:
                if os.path.exists(mp3_path):
                    os.remove(mp3_path)
            except OSError:
                pass

        self._sample_rate = sr or self._sample_rate

        return {
            "duration": (n_samples / sr) if sr else 0.0,
            "path": output_path,
            "sample_rate": sr or self._sample_rate,
        }
