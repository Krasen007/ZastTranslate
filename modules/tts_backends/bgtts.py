"""
BgTTS-38M-V2 backend — local Bulgarian (and English) neural dubbing with
zero-shot voice cloning.

Engine
------
`beleata74/BgTTS-38M-V2 <https://huggingface.co/beleata74/BgTTS-38M-V2>`_ — a
38M-parameter encoder-decoder TTS trained on 1,537 h of Bulgarian + English
speech, released under Apache-2.0. Voice identity comes from a 128-d MioCodec
``global_embedding`` extracted from a 3-10 s reference clip (no transcript
needed), which is exactly the "Clone from original" / "Clone from file"
workflow of ZastTranslate.

Trade-offs (surfaced in the UI)
-------------------------------
* **Local & offline** once the checkpoints are cached (BgTTS ~146 MB,
  MioCodec ~499 MB, WavLM base+ ~872 MB). First use needs internet to download.
* **Tiny VRAM footprint** (~1.5 GB) and fast (RTF ~0.3), CPU-friendly.
* **No speed/prosody control** — prosody is learned, so ``speed_control`` and
  ``duration_control`` are False and time_sync fits the *text* instead.
* **Bulgarian + English** only.

The model's own inference code is vendored under
``modules/tts_backends/bgtts_v6`` (see its ``__init__`` docstring).
"""

import os
from typing import Any
import numpy as np
import soundfile as sf

# Must run before any `import miocodec` (first happens in load()): drops that
# library's import-time "FlashAttention is not installed" WARNING on Windows.
from modules import quiet_flash_attn as _quiet_flash_attn  # noqa: F401

from .base import TTSBackend
from config import DEVICE, MODELS_DIR

# Pinned revisions keep downloads reproducible.
BG_TTS_REPO = "beleata74/BgTTS-38M-V2"
BG_TTS_REVISION = "3f6ca06b3ca78352eb00d8e96349c0bedaa3d2f1"
BG_TTS_CHECKPOINT = "checkpoint_inference.pt"

# Bundled reference voices used for the "Default voice" (non-cloning) mode so the
# timbre stays stable across segments instead of being drawn at random per line.
DEFAULT_REF_SAMPLES = {
    "man": "samples/sample_male_bg1.wav",
    "woman": "samples/sample_female_bg1.wav",
}

# BgTTS/MioCodec native sample rate (MioCodec-25Hz-24kHz).
SAMPLE_RATE = 24000

# Measured Bulgarian speaking rate, matching the Edge-TTS BG / fitted_cps table.
FITTED_CPS = 8.0

# Recommended (stable) sampling defaults from the model card.
TEMPERATURE = 0.3
TOP_K = 250
TOP_P = 0.95
REP_PENALTY = 1.1
MAX_NEW_TOKENS = 512  # ~20.5 s of audio at 25 frames/s (upper bound; per-chunk budget is derived from slot)

# 25 codec frames per second of audio. Per-chunk token budget = slot seconds ×
# this rate + margin, clamped to [64, MAX_NEW_TOKENS].
FRAMES_PER_SECOND = 25


class BgTTSBackend(TTSBackend):
    """Local BgTTS-38M-V2 backend (Bulgarian + English, zero-shot cloning)."""

    def __init__(self):
        super().__init__()
        self.device = DEVICE
        self._sample_rate = SAMPLE_RATE
        self._tokenizer: Any = None
        self._codec: Any = None
        self._ckpt_path: Any = None
        # ref-path (abspath) -> torch.Tensor [128] speaker embedding (kept on CPU).
        self._speaker_cache: dict[str, Any] = {}

    @property
    def name(self) -> str:
        return "BgTTS-38M"

    @property
    def capabilities(self) -> dict:
        return {
            "languages": ["bg", "en"],
            "speed_control": False,      # no explicit prosody control in the model
            "duration_control": False,   # time_sync fits the text instead
            "multi_speaker": False,
            "voice_design": True,        # zero-shot cloning via MioCodec speaker embedding
            "vram_gb": 1.5,              # BgTTS 38M + MioCodec 132M + WavLM base+
            "sample_rate": self._sample_rate,
            "fitted_cps": FITTED_CPS,
            "fitted_speed_factor": 1.0,  # no speed control available
            "requires_internet": False,  # fully local once the checkpoints are cached
        }

    def is_available(self) -> bool:
        import importlib.util
        try:
            # Lightweight existence checks for the vendored model's heavy deps
            # (bgtts_v6 imports them lazily, only when this backend is selected).
            if importlib.util.find_spec("miocodec") is None:
                return False
            if importlib.util.find_spec("jsonargparse") is None:
                return False
            import numpy as np  # noqa: F401
            import soundfile as sf  # noqa: F401
            from huggingface_hub import hf_hub_download  # noqa: F401
            return True
        except Exception:
            return False

    # -- model / checkpoint loading -------------------------------------------

    def _model_cache_dir(self) -> str:
        cache_dir = os.path.join(MODELS_DIR, "bgtts")
        os.makedirs(cache_dir, exist_ok=True)
        return cache_dir

    def _download(self, filename: str) -> str:
        """Download (or reuse cached) a file from the pinned BgTTS repo."""
        from huggingface_hub import hf_hub_download

        return hf_hub_download(
            repo_id=BG_TTS_REPO,
            filename=filename,
            revision=BG_TTS_REVISION,
            local_dir=self._model_cache_dir(),
        )

    def load(self, ref_audio_path=None):
        # Already loaded: just warm the speaker embedding if a new reference arrived.
        if self.model is not None:
            if ref_audio_path and os.path.exists(ref_audio_path):
                self._get_speaker_embedding(ref_audio_path)
            return

        # Lazy heavy imports — keeps app startup fast and lets the app boot even
        # when miocodec/jsonargparse are not installed (is_available() gates it).
        from .bgtts_v6.model import load_for_inference
        from .bgtts_v6.tokenizer import TTSTokenizer
        from .bgtts_v6.codec import CodecV6

        self._ckpt_path = self._download(BG_TTS_CHECKPOINT)
        print(f"[BgTTS-38M] Loading BgTTS model from {self._ckpt_path} ({self.device})...")
        self.model = load_for_inference(self._ckpt_path, device=self.device)
        self._tokenizer = TTSTokenizer()
        print("[BgTTS-38M] Loading MioCodec (Aratako/MioCodec-25Hz-24kHz)...")
        self._codec = CodecV6(device=self.device)
        self._speaker_cache = {}
        print("[BgTTS-38M] Ready (Bulgarian + English, zero-shot voice cloning).")

    def unload(self):
        if self.model is not None:
            self.model = None
            self._tokenizer = None
            self._codec = None
            self._speaker_cache = {}
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass

    # -- reference / speaker embedding ----------------------------------------

    @staticmethod
    def _gender_key(gender) -> str:
        g = str(gender or "").strip().lower()
        return "man" if g.startswith("m") else "woman"

    def _default_reference(self, gender) -> str:
        """Download and return the bundled default reference clip for a gender."""
        return self._download(DEFAULT_REF_SAMPLES[self._gender_key(gender)])

    def _resolve_reference(self, ref_audio_path, gender) -> str:
        if ref_audio_path and os.path.exists(ref_audio_path):
            return ref_audio_path
        return self._default_reference(gender)

    def _get_speaker_embedding(self, ref_path: str):
        """Return (and cache) the 128-d MioCodec speaker embedding for a clip."""
        if self._codec is None:
            self.load()
        assert self._codec is not None  # narrowed after load()

        key = os.path.abspath(ref_path)
        cached = self._speaker_cache.get(key)
        if cached is not None:
            return cached

        emb = self._codec.encode(ref_path)["global_embedding"]
        if hasattr(emb, "dim") and emb.dim() > 1:
            emb = emb.squeeze()
        emb = emb.cpu().float()
        self._speaker_cache[key] = emb
        print(f"[BgTTS-38M] Cached speaker embedding from {os.path.basename(ref_path)}")
        return emb

    # -- synthesis -------------------------------------------------------------

    def generate(self, text: str, language: str, output_path: str,
                 ref_audio_path: str = None, speed: float = 1.0,
                 duration: float = None, gender: str = "Woman", **kwargs) -> dict:
        import torch
        from .bgtts_v6.inference import _split_text, generate as _gen

        if self.model is None:
            self.load()
        assert self.model is not None  # narrowed after load()

        ref_path = self._resolve_reference(ref_audio_path, gender)
        speaker_emb = self._get_speaker_embedding(ref_path)

        max_len = min(250, getattr(self.model.config, "max_text_len", 250))
        chunks = _split_text(text, self._tokenizer, max_len=max_len)
        if not chunks:
            raise ValueError("[BgTTS-38M] Text contains no supported characters.")

        # Duration-aware token budget: slot seconds → codec frames (+25% margin).
        # Falls back to the full MAX_NEW_TOKENS when no slot is given.
        slot_budget = None
        if duration and duration > 0:
            slot_budget = max(64, min(MAX_NEW_TOKENS, int(duration * FRAMES_PER_SECOND * 1.25)))

        all_codes = []
        for i, chunk in enumerate(chunks):
            per_chunk = MAX_NEW_TOKENS
            if slot_budget is not None and len(chunks) > 0:
                per_chunk = max(64, min(MAX_NEW_TOKENS, slot_budget // len(chunks)))
            codes = _gen(
                self.model, self._tokenizer, chunk, speaker_emb,
                per_chunk, TEMPERATURE, TOP_K, TOP_P, REP_PENALTY, self.device,
            )
            if codes is None or len(codes) == 0:
                raise RuntimeError(
                    f"[BgTTS-38M] No audio generated for chunk {i + 1}/{len(chunks)}."
                )
            all_codes.append(codes)

        codes = torch.cat(all_codes) if len(all_codes) > 1 else all_codes[0]
        wav = self._codec.decode(codes, speaker_emb)  # torch [samples] @ 24kHz
        wav = wav.detach().cpu().numpy().astype(np.float32)
        wav = np.ascontiguousarray(wav)
        sf.write(output_path, wav, self._sample_rate)

        return {
            "duration": len(wav) / self._sample_rate,
            "path": output_path,
            "sample_rate": self._sample_rate,
        }
