import os
import gc
import torch
from .base import LLMBackend
from .local_causal import LocalCausalLMBackend
from .llama_cpp_backend import LlamaCppBackend

# Label used in the UI dropdown for the manual local-model entry.
CUSTOM_BACKEND_NAME = "Custom (Manual local model — folder or .gguf file)"

# Where the chosen manual path is persisted (survives restarts).
_CUSTOM_PATH_FILE = os.path.expanduser("~/.zasttranslate/custom_llm_path.txt")


def get_custom_model_path() -> str:
    """Return the persisted manual local-model path (folder or .gguf file), or ''."""
    try:
        if os.path.isfile(_CUSTOM_PATH_FILE):
            with open(_CUSTOM_PATH_FILE, "r", encoding="utf-8") as f:
                return (f.read() or "").strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def set_custom_model_path(path: str) -> str:
    """Validate + persist a manual local-model path. Returns the cleaned path."""
    cleaned = (path or "").strip().strip('"').strip("'")
    if not cleaned:
        raise ValueError("Empty path — paste a local folder or .gguf file path.")
    cleaned = os.path.abspath(os.path.expanduser(cleaned))
    if os.path.isdir(cleaned):
        # A local HF folder normally holds config.json / tokenizer files.
        # ALSO accept a bare folder that contains .gguf weights (e.g. an HF
        # cache snapshot folder) — hybrid.load() routes it to the C++ engine.
        markers = ("config.json", "tokenizer.json", "tokenizer_config.json")
        has_marker = any(os.path.isfile(os.path.join(cleaned, m)) for m in markers)
        has_gguf = False
        if not has_marker:
            for _root, _dirs, _files in os.walk(cleaned):
                if any(f.lower().endswith(".gguf") for f in _files):
                    has_gguf = True
                    break
        if not has_marker and not has_gguf:
            # Still accept it — transformers will raise a clear error on load if invalid —
            # but warn so the user knows what is expected.
            print(f"[Custom LLM] Warning: folder has no HF config marker (*.json) and no .gguf weights: {cleaned}")
    elif os.path.isfile(cleaned):
        if not cleaned.lower().endswith(".gguf"):
            raise ValueError(
                f"Not a .gguf file: {cleaned}\n"
                "Manual file mode only supports GGUF files for local loading."
            )
    else:
        raise ValueError(
            f"Path does not exist: {cleaned}\n"
            "Paste an existing local folder (HF model) or an existing .gguf file."
        )
    os.makedirs(os.path.dirname(_CUSTOM_PATH_FILE), exist_ok=True)
    with open(_CUSTOM_PATH_FILE, "w", encoding="utf-8") as f:
        f.write(cleaned)
    return cleaned


def is_custom_path_valid(path: str = None) -> bool:
    """True if the manual path exists (folder or .gguf file)."""
    p = path if path is not None else get_custom_model_path()
    if not p:
        return False
    if os.path.isdir(p):
        return True
    return os.path.isfile(p) and p.lower().endswith(".gguf")


def clear_custom_model_path():
    try:
        if os.path.isfile(_CUSTOM_PATH_FILE):
            os.remove(_CUSTOM_PATH_FILE)
    except Exception:
        pass


def build_custom_backend(path: str = None) -> "AcceleratedLLMBackend":
    """Build an AcceleratedLLMBackend pointing at a local folder or .gguf file.

    - Folder  -> PyTorch engine (transformers, from_pretrained(local folder)).
    - .gguf   -> C++ engine (llama-cpp, model_path=local file).
    """
    cleaned = (path if path is not None else get_custom_model_path() or "").strip()
    if not cleaned:
        raise ValueError("No manual model path set. Paste a local folder or .gguf file path first.")
    cleaned = os.path.abspath(os.path.expanduser(cleaned))
    capabilities = {"languages": "all", "vram_gb": 6.0}
    if os.path.isdir(cleaned):
        # Bare folder holding .gguf weights (e.g. HF cache snapshot folder):
        # point straight at the largest .gguf so load() takes the C++ path.
        gguf_hits = []
        for _root, _dirs, _files in os.walk(cleaned):
            for _f in _files:
                if _f.lower().endswith(".gguf"):
                    gguf_hits.append(os.path.join(_root, _f))
        if gguf_hits and not os.path.isfile(os.path.join(cleaned, "config.json")):
            gguf_hits.sort(key=lambda p: os.path.getsize(p), reverse=True)
            return AcceleratedLLMBackend(
                name=CUSTOM_BACKEND_NAME,
                model_id=cleaned,
                gguf_repo="local-file",
                gguf_file=gguf_hits[0],
                capabilities=capabilities,
            )
        return AcceleratedLLMBackend(
            name=CUSTOM_BACKEND_NAME,
            model_id=cleaned,
            gguf_repo=None,
            gguf_file=None,
            capabilities=capabilities,
        )
    if os.path.isfile(cleaned) and cleaned.lower().endswith(".gguf"):
        return AcceleratedLLMBackend(
            name=CUSTOM_BACKEND_NAME,
            model_id=cleaned,  # fallback id (unused when GGUF exists)
            gguf_repo="local-file",  # non-empty sentinel -> hybrid tries C++ engine first
            gguf_file=cleaned,
            capabilities=capabilities,
        )
    raise ValueError(f"Manual model path does not exist: {cleaned}")


class AcceleratedLLMBackend(LLMBackend):
    """
    Unified LLM backend that automatically uses the high-speed C++ engine (llama-cpp / GGUF)
    when available, with transparent fallback to PyTorch NF4 (transformers).
    The user only sees the clean model name without technical jargon.
    """
    def __init__(self, name: str, model_id: str, gguf_repo: str = None, gguf_file: str = None, capabilities: dict = None):
        super().__init__()
        self._name = name
        self.model_id = model_id
        self.gguf_repo = gguf_repo
        self.gguf_file = gguf_file
        self._capabilities = capabilities or {"languages": "all", "vram_gb": 5.0}
        self.active_engine = None
        self._backend = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def capabilities(self) -> dict:
        return self._capabilities

    def is_available(self) -> bool:
        # Manual local model: available if the file/folder exists AND an engine is installed.
        maybe_local = (self.gguf_file or "") if isinstance(self.gguf_file, str) else ""
        if maybe_local and os.path.isfile(maybe_local) and maybe_local.lower().endswith(".gguf"):
            return LlamaCppBackend(self.name, self.gguf_repo or "", maybe_local, {}).is_available()
        if self.model_id and os.path.isdir(os.path.abspath(os.path.expanduser(self.model_id))):
            return LocalCausalLMBackend(self.name, self.model_id, {}).is_available()
        # Available if either llama_cpp or transformers is installed
        cpp_ok = LlamaCppBackend(self.name, self.gguf_repo or "", self.gguf_file or "", {}).is_available() if self.gguf_repo else False
        pt_ok = LocalCausalLMBackend(self.name, self.model_id, {}).is_available()
        return cpp_ok or pt_ok

    def load(self):
        if self._backend is not None:
            return

        # Manual custom model with no valid path saved (placeholder backend,
        # e.g. right after install or after the path file was deleted): raise
        # the actionable message HERE (load time) — never at get_backend time,
        # which must stay safe for app startup.
        if self._name == CUSTOM_BACKEND_NAME and (
            not self.model_id
            or self.model_id == "custom-manual-model"
            or (
                not (isinstance(self.gguf_file, str) and os.path.isfile(self.gguf_file))
                and not (
                    isinstance(self.model_id, str)
                    and os.path.isdir(os.path.abspath(os.path.expanduser(self.model_id)))
                )
            )
        ):
            raise ValueError(
                "Custom model selected but no valid local path is set.\n"
                "In Tab 2, paste a local model folder (HF format with config.json) "
                "or a .gguf file in the 'Manual local model' field, "
                "click 'Use this model', then retry."
            )

        # Manual local model fast-path: absolute .gguf file -> C++ engine directly.
        maybe_local_gguf = (self.gguf_file or "") if isinstance(self.gguf_file, str) else ""
        if maybe_local_gguf and os.path.isfile(maybe_local_gguf) and maybe_local_gguf.lower().endswith(".gguf"):
            cpp_backend = LlamaCppBackend(self.name, self.gguf_repo, maybe_local_gguf, self._capabilities)
            if not cpp_backend.is_available():
                raise RuntimeError(
                    "llama-cpp-python is not installed, so local .gguf files cannot be loaded. "
                    "Use a local HF folder instead, or install llama-cpp-python."
                )
            print(f"[{self.name}] Initializing High-Speed C++ Engine (local GGUF file)...")
            cpp_backend.load()
            self._backend = cpp_backend
            self.active_engine = "cpp"
            print(f"[{self.name}] C++ Engine active and ready.")
            return

        # Manual local model: a bare folder containing a *GGUF snapshot* (HF cache
        # layout: <root>/snapshots/<hash>/*.gguf) with no config.json. Route it to
        # the C++ engine automatically so users can paste the snapshot folder.
        maybe_folder = os.path.abspath(os.path.expanduser(self.model_id)) if isinstance(self.model_id, str) else ""
        if maybe_folder and os.path.isdir(maybe_folder) and not self.gguf_repo:
            gguf_hits = []
            for _root, _dirs, _files in os.walk(maybe_folder):
                for _f in _files:
                    if _f.lower().endswith(".gguf"):
                        gguf_hits.append(os.path.join(_root, _f))
                        break
                if gguf_hits:
                    break
            if gguf_hits:
                # Prefer the largest file (main weights over MTP/projection shards).
                gguf_hits.sort(key=lambda p: os.path.getsize(p), reverse=True)
                print(f"[{self.name}] Folder holds GGUF weights, routing to C++ engine: {gguf_hits[0]}")
                cpp_backend = LlamaCppBackend(self.name, "local-file", gguf_hits[0], self._capabilities)
                cpp_backend.load()  # raises a clear install message if llama-cpp is missing
                self._backend = cpp_backend
                self.active_engine = "cpp"
                print(f"[{self.name}] C++ Engine active and ready.")
                return

        # Manual local model fast-path: absolute local HF folder -> PyTorch engine directly
        # (avoids the Hub-cache lookup below, which would mis-handle absolute paths).
        maybe_local_folder = os.path.abspath(os.path.expanduser(self.model_id)) if isinstance(self.model_id, str) else ""
        if maybe_local_folder and os.path.isdir(maybe_local_folder):
            if not self.gguf_repo:  # pure folder backend (custom manual folder)
                print(f"[{self.name}] Initializing PyTorch Engine (local folder)...")
                pt_backend = LocalCausalLMBackend(self.name, self.model_id, self._capabilities)
                pt_backend.load()
                self._backend = pt_backend
                self.active_engine = "pytorch"
                print(f"[{self.name}] PyTorch Engine active and ready.")
                return
            has_local_pt = True  # local folder present: skip C++ download path

        # Check if local GGUF already exists
        gguf_dir = os.path.expanduser("~/.zasttranslate/models/gguf")
        gguf_local_path = os.path.join(gguf_dir, self.gguf_file) if self.gguf_file else None
        has_local_gguf = gguf_local_path and os.path.exists(gguf_local_path)

        # Check if local PyTorch model already exists
        hf_dir = os.path.expanduser("~/.zasttranslate/models/hub")
        try:
            has_local_pt
        except NameError:
            pt_folder = f"models--{self.model_id.replace('/', '--')}"
            has_local_pt = os.path.exists(os.path.join(hf_dir, pt_folder))

        # Decision: Use C++ GGUF if llama_cpp is available and (local GGUF exists OR local PyTorch doesn't exist)
        cpp_backend = LlamaCppBackend(self.name, self.gguf_repo, self.gguf_file, self._capabilities) if self.gguf_repo else None
        use_cpp = cpp_backend and cpp_backend.is_available() and (has_local_gguf or not has_local_pt)

        if use_cpp:
            try:
                print(f"[{self.name}] Initializing High-Speed C++ Engine (GGUF / llama-cpp + KV Cache)...")
                cpp_backend.load()
                self._backend = cpp_backend
                self.active_engine = "cpp"
                print(f"[{self.name}] C++ Engine active and ready.")
                return
            except Exception as e:
                print(f"[{self.name}] C++ engine load notice: {e}. Falling back to PyTorch NF4...")

        # Fallback / Default: PyTorch NF4 engine
        print(f"[{self.name}] Initializing PyTorch NF4 Engine (transformers)...")
        pt_backend = LocalCausalLMBackend(self.name, self.model_id, self._capabilities)
        pt_backend.load()
        self._backend = pt_backend
        self.active_engine = "pytorch"
        print(f"[{self.name}] PyTorch NF4 Engine active and ready.")

    def unload(self):
        if self._backend is not None:
            self._backend.unload()
            self._backend = None
            self.active_engine = None
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    def generate(self, messages: list, max_new_tokens: int = 4096, multiline: bool = False, **kwargs) -> str:
        if self._backend is None:
            self.load()
        return self._backend.generate(messages, max_new_tokens=max_new_tokens, multiline=multiline, **kwargs)

    def generate_batch(self, messages_list: list, max_new_tokens_list: list, **kwargs) -> list:
        if self._backend is None:
            self.load()
        return self._backend.generate_batch(messages_list, max_new_tokens_list, **kwargs)
