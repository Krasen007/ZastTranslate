import os
import json
import gc
import urllib.request
import urllib.error
import torch
from .base import LLMBackend
from config import DEVICE

# Configure CUDA DLL directory for Windows (mirrors the other backends so any
# lazy CUDA init in this process can resolve torch's bundled DLLs).
if hasattr(os, "add_dll_directory"):
    try:
        torch_lib = os.path.join(os.path.dirname(torch.__file__), "lib")
        if os.path.exists(torch_lib):
            os.add_dll_directory(torch_lib)
    except Exception:
        pass

# Where the chosen llama-server base URL is persisted (survives restarts).
_SERVER_URL_FILE = os.path.expanduser("~/.zasttranslate/llama_server_url.txt")

# llama-server's OpenAI-compatible default.
DEFAULT_SERVER_URL = "http://127.0.0.1:8080/v1"


def get_server_url() -> str:
    """Return the persisted llama-server base URL, or the default."""
    try:
        if os.path.isfile(_SERVER_URL_FILE):
            with open(_SERVER_URL_FILE, "r", encoding="utf-8") as f:
                val = (f.read() or "").strip().strip('"').strip("'")
                if val:
                    return val
    except Exception:
        pass
    return DEFAULT_SERVER_URL


def set_server_url(url: str) -> str:
    """Validate + persist a llama-server base URL. Returns the cleaned URL."""
    cleaned = (url or "").strip().strip('"').strip("'")
    if not cleaned:
        raise ValueError("Empty URL - paste the llama-server base URL (e.g. http://127.0.0.1:8080/v1).")
    cleaned = cleaned.rstrip("/")
    if not (cleaned.startswith("http://") or cleaned.startswith("https://")):
        raise ValueError("URL must start with http:// or https:// (e.g. http://127.0.0.1:8080/v1).")
    os.makedirs(os.path.dirname(_SERVER_URL_FILE), exist_ok=True)
    with open(_SERVER_URL_FILE, "w", encoding="utf-8") as f:
        f.write(cleaned)
    return cleaned


class LlamaServerBackend(LLMBackend):
    """Talk to an EXTERNAL, already-running llama.cpp server (llama-server / any
    OpenAI-compatible /v1/chat/completions endpoint).

    This is the "run llama.cpp separately" path: you start llama-server yourself
    (with whatever GPU offload you want), and ZastTranslate just sends chat
    requests over HTTP. No in-process model, no wheel, no VRAM used by the app.

    Example server command:
        llama-server -m model.gguf --port 8080 -ngl 99 --ctx-size 4096
    """

    def __init__(self, name: str, base_url: str = None, capabilities: dict = None):
        super().__init__()
        self._name = name
        self.base_url = (base_url or get_server_url()).rstrip("/")
        self._capabilities = capabilities or {"languages": "all", "vram_gb": 0.0}
        self._loaded = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def capabilities(self) -> dict:
        return self._capabilities

    def is_available(self) -> bool:
        # The server backend is always *listable*; whether a server is actually
        # reachable is checked in load() with an actionable message. This keeps
        # the option visible in the dropdown even before the user starts a server.
        return True

    # -- HTTP helpers ---------------------------------------------------------

    def _request(self, path: str, payload: dict = None, timeout: float = 30.0):
        """POST (or GET when payload is None) to {base_url}{path}. Returns parsed JSON."""
        url = f"{self.base_url}{path}"
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "ignore")
            except Exception:
                pass
            raise RuntimeError(f"llama-server HTTP {e.code} at {url}: {detail or e.reason}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(self._unreachable_msg(e.reason)) from e
        except (ConnectionError, TimeoutError, OSError) as e:
            # Covers http.client.RemoteDisconnected and friends, which are NOT
            # wrapped in URLError — a stopped/wrong-port server must still produce
            # the actionable "start llama-server" message rather than a raw traceback.
            raise RuntimeError(self._unreachable_msg(e)) from e

    @staticmethod
    def _unreachable_msg(reason) -> str:
        return (
            f"Cannot reach llama-server ({reason}).\n"
            f"Start it first, e.g.:  llama-server -m your-model.gguf --port 8080 -ngl 99 --ctx-size 4096\n"
            f"Then make sure the URL you set matches the --port."
        )

    def load(self):
        # Probe the server so a wrong URL / stopped server fails fast with a
        # clear, actionable message instead of erroring mid-translation.
        self._request("/models", payload=None, timeout=8.0)
        self._loaded = True
        print(f"[{self.name}] Connected to external llama.cpp server at {self.base_url}")

    def unload(self):
        # The server process is owned by the user; nothing to free here.
        self._loaded = False
        gc.collect()

    def generate(self, messages: list, max_new_tokens: int = 4096, multiline: bool = False, **kwargs) -> str:
        if not self._loaded:
            self.load()

        do_sample = kwargs.get("do_sample", True)
        temperature = kwargs.get("temperature", 0.3)
        payload = {
            "model": "local-model",  # llama-server ignores this and uses its loaded model
            "messages": messages,
            "max_tokens": max_new_tokens,
            "temperature": temperature if do_sample else 0.0,
            "stream": False,
        }
        # llama-server accepts repeat_penalty; harmless if the endpoint ignores it.
        rp = kwargs.get("repetition_penalty")
        if rp:
            payload["repeat_penalty"] = rp

        # Long translations can take a while - allow a generous timeout.
        resp = self._request("/chat/completions", payload=payload, timeout=600.0)
        try:
            choice = resp["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"Unexpected llama-server response shape: {resp}") from e
        return choice if choice is not None else ""

    def generate_batch(self, messages_list: list, max_new_tokens_list: list, **kwargs) -> list:
        # llama-server handles one completion at a time (unless run with --parallel);
        # keep it simple and sequential, matching the base-class contract.
        if not self._loaded:
            self.load()
        return [
            self.generate(msgs, max_new_tokens=mnt, **kwargs)
            for msgs, mnt in zip(messages_list, max_new_tokens_list)
        ]

