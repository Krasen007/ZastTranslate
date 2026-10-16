import os
from functools import lru_cache

# Nom du projet
APP_NAME = "ZastTranslate"
APP_VERSION = "1.21"

# Chemins
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(BASE_DIR, "temp")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
MODELS_DIR = os.path.join(BASE_DIR, "models")
VOICES_DIR = os.path.join(BASE_DIR, "voices")
# Persistent YouTube download cache. Unlike TEMP_DIR (wiped on "New Project"),
# files here survive restarts so re-importing the same URL skips the download.
DOWNLOADS_DIR = os.path.join(BASE_DIR, "downloads")

# --- Lazy runtime state (no torch import / makedirs at import time) ---
# All 35 modules import this file, so importing torch (~1 s + CUDA probe)
# and creating directories on every import is wasteful. Heavy work happens
# via ensure_dirs()/get_device()/get_gpu_info(), called once from app.py
# main. DEVICE / GPU_* stay available as module attributes via __getattr__.


def ensure_dirs() -> None:
    """Create working directories. Called once from app.py main."""
    for d in [TEMP_DIR, OUTPUT_DIR, MODELS_DIR, VOICES_DIR, DOWNLOADS_DIR]:
        os.makedirs(d, exist_ok=True)


def _load_torch():
    import torch as _torch
    # Reduce CUDA fragmentation on tight GPUs (e.g. 8 GB):
    # max_split_size_mb stops the caching allocator splintering large
    # blocks (expandable_segments is NOT compiled into Windows torch).
    # Must precede the first CUDA allocation; legacy name kept for
    # older torch builds (torch.js pins 2.8).
    os.environ.setdefault("PYTORCH_ALLOC_CONF", "max_split_size_mb:128")
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "max_split_size_mb:128")
    return _torch


def get_device() -> str:
    """Return 'cuda' if available else 'cpu' (cached, lazy torch import)."""
    global _DEVICE_CACHE
    try:
        return _DEVICE_CACHE
    except NameError:
        pass
    try:
        torch = _load_torch()
        _DEVICE_CACHE = "cuda" if torch.cuda.is_available() else "cpu"
        if _DEVICE_CACHE == "cuda":
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
            print("NVIDIA TF32 Acceleration Enabled (RTX 3000/4000+ speedup)")
    except Exception:
        _DEVICE_CACHE = "cpu"
    return _DEVICE_CACHE


@lru_cache(maxsize=1)
def get_gpu_info():
    """Return (name, vram_gb, vram_str); safe on CPU-only machines."""
    try:
        torch = _load_torch()
        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            gb = float(torch.cuda.get_device_properties(0).total_memory / 1024**3)
            return name, gb, f"{gb:.1f} GB"
    except Exception:
        pass
    return "None", 0.0, "N/A"


def __getattr__(name):
    # Backward-compat lazy aliases: `from config import DEVICE` keeps
    # working (module __getattr__ is consulted when normal lookup fails).
    if name == "DEVICE":
        return get_device()
    if name == "GPU_NAME":
        return get_gpu_info()[0]
    if name == "GPU_VRAM_GB":
        return get_gpu_info()[1]
    if name == "GPU_VRAM":
        return get_gpu_info()[2]
    raise AttributeError(f"module 'config' has no attribute {name!r}")


def configure_hf_cache() -> None:
    """Redirect HuggingFace cache to the project's local models directory.

    Must be called explicitly from the application entry point (app.py main)
    rather than at module import time, so that HF cache location is configured
    once at startup instead of being silently mutated on every import or
    backend lookup.

    Sets both HF_HOME and HUGGINGFACE_HUB_CACHE and creates the directory
    tree so downloads land under ~/.zasttranslate/models/{hub, ...} instead of
    the user's global ~/.cache/huggingface.
    """
    models_dir = os.path.expanduser("~/.zasttranslate/models")
    hf_home = os.path.join(models_dir)
    hub_cache = os.path.join(models_dir, "hub")
    os.makedirs(hf_home, exist_ok=True)
    os.makedirs(hub_cache, exist_ok=True)
    os.environ["HF_HOME"] = hf_home
    os.environ["HUGGINGFACE_HUB_CACHE"] = hub_cache

# GPU/CPU: lazy aliases above (get_device/get_gpu_info via __getattr__).
# NOTE: `from config import DEVICE` triggers __getattr__ at import time and
# binds a snapshot; long-lived modules should `import config` +
# `config.get_device()` or `from config import get_device` instead.
# __all__ includes the lazy names so `from config import *` keeps working.
__all__ = [
    "APP_NAME", "APP_VERSION", "BASE_DIR", "TEMP_DIR", "OUTPUT_DIR",
    "MODELS_DIR", "VOICES_DIR", "DOWNLOADS_DIR", "ensure_dirs",
    "get_device", "get_gpu_info", "configure_hf_cache", "DEVICE",
    "GPU_NAME", "GPU_VRAM_GB", "GPU_VRAM",
    "LANGUAGES", "MAX_SPEED_FACTOR",
]

# Synchronization constants
TOLERANCE_TOO_LONG = 0.15    # 150ms: beyond this, adjust speed
MAX_SPEED_FACTOR = 1.25      # Max native speedup (CustomVoice instruct)
MAX_STRETCH_FACTOR = 1.2     # Max librosa time_stretch (voice clone fallback)
MIN_SEGMENT_DURATION = 0.3   # Ignore segments < 300ms

# Never Cut Vocal mode
NEVER_CUT_WARNING = (
    "⚠️ 'Never Cut Vocal' mode enabled: all text will be spoken in full, "
    "but dubbing may drift out of sync with on-screen actions."
)

# Estimated characters per second by language family (for LLM pre-check)
CHARS_PER_SECOND = {
    "latin": 13,      # FR, EN, ES, DE, IT, PT (balance: reformulation quality vs TTS fit)
    "cjk": 7,         # ZH, JA, KO
    "arabic": 12,     # AR
    "cyrillic": 11,   # BG, RU, UK — Cyrillic carries fewer chars per second than Latin
    "default": 13
}

# Audio
WHISPERX_SAMPLE_RATE = 16000
DEMUCS_SAMPLE_RATE = 44100
OUTPUT_SAMPLE_RATE = 44100
OUTPUT_CHANNELS = 2  # stereo
OUTPUT_BIT_DEPTH = 16
MP3_BITRATE = "320k"

# Supported languages (language codes)
# Full list covering VoxCPM2 (30 langs) + Qwen3-TTS (10 langs)
# The UI dynamically filters based on selected TTS + LLM backend capabilities
LANGUAGES = {
    "French": "fra_Latn",
    "English": "eng_Latn",
    "Spanish": "spa_Latn",
    "German": "deu_Latn",
    "Italian": "ita_Latn",
    "Portuguese": "por_Latn",
    "Japanese": "jpn_Jpan",
    "Korean": "kor_Hang",
    "Chinese": "zho_Hans",
    "Russian": "rus_Cyrl",
    "Arabic": "arb_Arab",
    "Hindi": "hin_Deva",
    "Dutch": "nld_Latn",
    "Polish": "pol_Latn",
    "Turkish": "tur_Latn",
    "Swedish": "swe_Latn",
    "Czech": "ces_Latn",
    "Romanian": "ron_Latn",
    "Hungarian": "hun_Latn",
    # VoxCPM2 additional languages
    "Burmese": "mya_Mymr",
    "Danish": "dan_Latn",
    "Finnish": "fin_Latn",
    "Greek": "ell_Grek",
    "Hebrew": "heb_Hebr",
    "Indonesian": "ind_Latn",
    "Khmer": "khm_Khmr",
    "Lao": "lao_Laoo",
    "Malay": "zsm_Latn",
    "Norwegian": "nob_Latn",
    "Swahili": "swh_Latn",
    "Tagalog": "tgl_Latn",
    "Thai": "tha_Thai",
    "Vietnamese": "vie_Latn",
    # Bulgarian — subtitles/SEO via LLM, dubbing via the Edge-TTS BG backend
    "Bulgarian": "bul_Cyrl",
}
