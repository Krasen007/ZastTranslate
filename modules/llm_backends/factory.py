import os
from .hybrid import (
    AcceleratedLLMBackend,
    CUSTOM_BACKEND_NAME,
    build_custom_backend,
    get_custom_model_path,
    is_custom_path_valid,
)
from .llama_server_backend import (
    LlamaServerBackend,
    get_server_url,
    set_server_url,
)

# Label used in the UI dropdown for the external llama.cpp server entry.
LLAMA_SERVER_BACKEND_NAME = "llama.cpp server (external — you run llama-server)"

# Global cache for the active backend
_active_llm_backend = None
_custom_llm_backend = None
_llama_server_backend = None

def get_available_backends() -> dict:
    """Returns a clean list of available LLM backends with transparent C++ acceleration and VRAM guidance."""
    backends = {
        "Qwen3.5-9B (Recommended — 12 to 24 GB VRAM)": AcceleratedLLMBackend(
            name="Qwen3.5-9B (Recommended — 12 to 24 GB VRAM)",
            model_id="Qwen/Qwen3.5-9B",
            gguf_repo="bartowski/Qwen_Qwen3.5-9B-GGUF",
            gguf_file="Qwen_Qwen3.5-9B-Q4_K_M.gguf",
            capabilities={
                "languages": "all",
                "vram_gb": 6.0
            }
        ),
        "Qwen2.5-7B-Instruct (Ideal for 8 GB VRAM GPUs)": AcceleratedLLMBackend(
            name="Qwen2.5-7B-Instruct (Ideal for 8 GB VRAM GPUs)",
            model_id="Qwen/Qwen2.5-7B-Instruct",
            gguf_repo="bartowski/Qwen2.5-7B-Instruct-GGUF",
            gguf_file="Qwen2.5-7B-Instruct-Q4_K_M.gguf",
            capabilities={
                "languages": "all",
                "vram_gb": 4.5
            }
        ),
        "EuroLLM-9B-Instruct (European Languages — 12 GB+)": AcceleratedLLMBackend(
            name="EuroLLM-9B-Instruct (European Languages — 12 GB+)",
            model_id="utter-project/EuroLLM-9B-Instruct",
            capabilities={
                # 24 languages, matching the upstream EuroLLM-9B-Instruct HF language tags.
                "languages": ["en", "fr", "es", "de", "it", "pt", "nl", "pl", "sv", "cs", "ro", "hu",
                              "bg", "el", "fi", "da", "no", "uk", "sl", "sk", "hr", "lt", "lv", "et"],
                "vram_gb": 6.0
            }
        )
    }
    
    # Filter only those whose requirements are installed
    available = {}
    for name, backend in backends.items():
        if backend.is_available():
            available[name] = backend

    # Manual local model: always listed so users can pick a folder/.gguf file.
    # It is selectable even before a valid path is set; get_backend() raises a
    # clear message in that case.
    global _custom_llm_backend
    try:
        custom_path = get_custom_model_path()
        if custom_path and is_custom_path_valid(custom_path):
            # Reuse the cached instance; it is rebuilt only via
            # reset_custom_backend_cache() (called when a new path is saved).
            if _custom_llm_backend is None:
                _custom_llm_backend = build_custom_backend(custom_path)
            if _custom_llm_backend.is_available():
                available[CUSTOM_BACKEND_NAME] = _custom_llm_backend
            else:
                # Engine missing (e.g. .gguf without llama-cpp): still list it so
                # the user sees the option; loading will explain what to install.
                available[CUSTOM_BACKEND_NAME] = _custom_llm_backend
        else:
            # No valid path yet — list a lightweight placeholder entry.
            # It is replaced by the real backend once a path is saved.
            if CUSTOM_BACKEND_NAME not in available:
                placeholder = AcceleratedLLMBackend(
                    name=CUSTOM_BACKEND_NAME,
                    model_id=custom_path or "custom-manual-model",
                    gguf_repo=None,
                    gguf_file=None,
                    capabilities={"languages": "all", "vram_gb": 6.0},
                )
                available[CUSTOM_BACKEND_NAME] = placeholder
    except Exception as e:
        print(f"[Custom LLM] note: {e}")

    # External llama.cpp server: always listed. It has no engine/package
    # requirement of its own — the user runs llama-server separately and this
    # backend just talks to it over HTTP (reachability is checked at load()).
    global _llama_server_backend
    if _llama_server_backend is None:
        _llama_server_backend = LlamaServerBackend(
            name=LLAMA_SERVER_BACKEND_NAME,
            base_url=get_server_url(),
            capabilities={"languages": "all", "vram_gb": 0.0},
        )
    else:
        _llama_server_backend.base_url = get_server_url().rstrip("/")
    available[LLAMA_SERVER_BACKEND_NAME] = _llama_server_backend

    return available

def reset_custom_backend_cache():
    """Forget the cached custom backend so a newly saved path takes effect."""
    global _custom_llm_backend
    _custom_llm_backend = None

def get_backend(name: str):
    """
    Returns the requested backend.
    Handles exact names and aliases/prefixes (e.g. 'Qwen3.5-9B' -> 'Qwen3.5-9B (Recommandé — 12 à 24 Go VRAM)').
    If a different backend is currently loaded, it unloads it to free VRAM.
    """
    global _active_llm_backend
    global _custom_llm_backend

    # Manual custom model: reuse the cached backend built from the persisted path.
    # NOTE: must NEVER raise here — app.py calls get_backend() at startup to
    # compute INITIAL_VALID_LANGS. If no valid path is saved yet, return a
    # lightweight placeholder (languages="all") so the app boots; the real
    # error is raised later at .load() time (or by on_custom_llm_use).
    if name == CUSTOM_BACKEND_NAME or (name or "").strip().lower().startswith("custom"):
        custom_path = get_custom_model_path()
        if not custom_path or not is_custom_path_valid(custom_path):
            return AcceleratedLLMBackend(
                name=CUSTOM_BACKEND_NAME,
                model_id="custom-manual-model",
                gguf_repo=None,
                gguf_file=None,
                capabilities={"languages": "all", "vram_gb": 6.0},
            )
        # Reuse the cached custom backend; rebuild only after
        # reset_custom_backend_cache() clears it (new path saved).
        if _custom_llm_backend is None:
            _custom_llm_backend = build_custom_backend(custom_path)
        requested_backend = _custom_llm_backend
        if _active_llm_backend is not None and _active_llm_backend is not requested_backend:
            # Different instance (e.g. a stale loaded custom backend): unload it
            # before replacing so its VRAM is freed.
            print(f"Switching LLM Backend: Unloading {_active_llm_backend.name} to free VRAM...")
            try:
                _active_llm_backend.unload()
            except Exception:
                pass
        _active_llm_backend = requested_backend
        return _active_llm_backend

    available = get_available_backends()
    
    # 1. Exact match
    if name in available:
        requested_backend = available[name]
    else:
        # 2. Match by prefix or model name token
        matched = None
        base_name = name.split(' (')[0].strip() if name else ""
        for k, v in available.items():
            if base_name and (base_name == k.split(' (')[0].strip() or base_name in k):
                matched = v
                break
        if matched is None:
            if not available:
                raise ValueError(
                    f"Unknown LLM backend: {name!r} (no LLM backends are available — "
                    "install transformers+accelerate+bitsandbytes for PyTorch models, "
                    "llama-cpp-python for GGUF models, or start a llama-server)."
                )
            raise ValueError(
                f"Unknown LLM backend: {name!r}. Available: {sorted(available.keys())}"
            )
        requested_backend = matched
    
    if _active_llm_backend is not None and _active_llm_backend.name != requested_backend.name:
        print(f"Switching LLM Backend: Unloading {_active_llm_backend.name} to free VRAM...")
        _active_llm_backend.unload()
        
    _active_llm_backend = requested_backend
    return _active_llm_backend
