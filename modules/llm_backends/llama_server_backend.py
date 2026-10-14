import os
import json
import gc
import ipaddress
import socket
import urllib.request
import urllib.error
import urllib.parse
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
    from urllib.parse import urlparse
    cleaned = (url or "").strip().strip('"').strip("'")
    if not cleaned:
        raise ValueError("Empty URL - paste the llama-server base URL (e.g. http://127.0.0.1:8080/v1).")
    cleaned = cleaned.rstrip("/")
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL must start with http:// or https:// (e.g. http://127.0.0.1:8080/v1).")
    if not parsed.hostname:
        raise ValueError(f"URL has no host: {cleaned!r}")
    host = (parsed.hostname or "").lower()
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(
            f"[llama-server] WARNING: non-loopback server URL ({cleaned}). "
            "Prompts (video transcripts) will be sent to that host — "
            "only use servers you trust."
        )
    os.makedirs(os.path.dirname(_SERVER_URL_FILE), exist_ok=True)
    tmp = _SERVER_URL_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(cleaned)
    os.replace(tmp, _SERVER_URL_FILE)
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

    def _request(self, path: str, payload: dict = None, timeout: float = 30.0, retries: int = 3):
        """POST (or GET when payload is None) to {base_url}{path}. Returns parsed JSON."""
        url = f"{self.base_url}{path}"
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        last_error = None
        retries = max(1, retries)
        parsed_url = urllib.parse.urlsplit(url)
        hostname = (parsed_url.hostname or "").lower()
        is_loopback = hostname == "localhost"
        if hostname and not is_loopback:
            try:
                is_loopback = ipaddress.ip_address(hostname).is_loopback
            except ValueError:
                pass
        for attempt in range(1, retries + 1):
            req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
            req.add_header("Content-Type", "application/json")
            api_key = os.environ.get("LLAMA_SERVER_API_KEY", "")
            if api_key and (parsed_url.scheme == "https" or is_loopback):
                req.add_header("Authorization", f"Bearer {api_key}")
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
                # 4xx (bad request / auth) won't heal on retry — fail fast.
                if 400 <= e.code < 500:
                    raise RuntimeError(f"llama-server HTTP {e.code} at {url}: {detail or e.reason}") from e
                last_error = RuntimeError(f"llama-server HTTP {e.code} at {url}: {detail or e.reason}")
            except urllib.error.URLError as e:
                if path.endswith("/chat/completions") and isinstance(e.reason, (TimeoutError, socket.timeout)):
                    raise RuntimeError(
                        f"llama-server chat request timed out at {url}; the request may be slow or timed out."
                    ) from e
                last_error = RuntimeError(self._unreachable_msg(e.reason))
                break  # server down / DNS: retrying won't help within one call
            except (TimeoutError, socket.timeout) as e:
                if path.endswith("/chat/completions"):
                    raise RuntimeError(
                        f"llama-server chat request timed out at {url}; the request may be slow or timed out."
                    ) from e
                last_error = RuntimeError(self._unreachable_msg(e))
            except (ConnectionError, TimeoutError, OSError) as e:
                # Covers http.client.RemoteDisconnected and friends, which are NOT
                # wrapped in URLError — a stopped/wrong-port server must still produce
                # the actionable "start llama-server" message rather than a raw traceback.
                last_error = RuntimeError(self._unreachable_msg(e))
            if attempt < retries:
                import time as _time
                _time.sleep(1.5 * attempt)
        raise last_error

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

    # -- response helpers -----------------------------------------------------

    @staticmethod
    def _message(resp: dict) -> dict:
        """Pull the assistant message out of a chat/completions response."""
        try:
            return resp["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"Unexpected llama-server response shape: {resp}") from e

    @classmethod
    def _extract_content(cls, resp: dict) -> str:
        """Return the assistant ANSWER text from a chat/completions response.

        Reasoning / hybrid models (e.g. Qwen3-based Qwythos/Mythos) split their
        output into `reasoning_content` (the internal "thinking") and `content`
        (the real answer). We only ever want the answer, so this reads `content`
        and NEVER falls back to the reasoning trace (that would be meta-text,
        not the translation).
        """
        content = cls._message(resp).get("content")
        return content if content is not None else ""

    @classmethod
    def _has_reasoning(cls, resp: dict) -> bool:
        """True when the model emitted a non-empty reasoning trace. Used to detect
        that `content` came back empty only because the budget was spent thinking."""
        reasoning = cls._message(resp).get("reasoning_content")
        return bool(reasoning and str(reasoning).strip())

    def generate(self, messages: list, max_new_tokens: int = 4096, multiline: bool = False, **kwargs) -> str:
        if not self._loaded:
            self.load()

        do_sample = kwargs.get("do_sample", True)
        temperature = kwargs.get("temperature", 0.3)

        def _payload(mnt: int) -> dict:
            p = {
                "model": "local-model",  # llama-server ignores this and uses its loaded model
                "messages": messages,
                "max_tokens": mnt,
                "temperature": temperature if do_sample else 0.0,
                "stream": False,
                # Hybrid reasoning models (e.g. Qwen3-based Qwythos/Mythos) spend the
                # ENTIRE token budget on `reasoning_content` ("thinking") and never emit
                # the actual `content` answer unless thinking is disabled. Dubbing needs
                # the plain translation, so disable it. This is harmless for
                # non-reasoning models (llama-server ignores an unused chat-template kwarg).
                "chat_template_kwargs": {"enable_thinking": False},
            }
            # llama-server accepts repeat_penalty; harmless if the endpoint ignores it.
            rp = kwargs.get("repetition_penalty")
            if rp:
                p["repeat_penalty"] = rp
            return p

        # Long translations can take a while - allow a generous timeout.
        resp = self._request("/chat/completions", payload=_payload(max_new_tokens), timeout=600.0)
        content = self._extract_content(resp)

        # Defensive: older llama.cpp builds may ignore `chat_template_kwargs` and still
        # burn the budget on reasoning, leaving `content` empty. If that happened, retry
        # once with a larger (capped) budget so the model can finish thinking AND answer.
        if not content and self._has_reasoning(resp):
            bigger = min(int(max_new_tokens) * 2, 2048)
            resp = self._request("/chat/completions", payload=_payload(bigger), timeout=600.0)
            content = self._extract_content(resp)
        return content

    def generate_batch(self, messages_list: list, max_new_tokens_list: list, **kwargs) -> list:
        # llama-server handles one completion at a time (unless run with --parallel);
        # keep it simple and sequential, matching the base-class contract.
        if not self._loaded:
            self.load()
        return [
            self.generate(msgs, max_new_tokens=mnt, **kwargs)
            for msgs, mnt in zip(messages_list, max_new_tokens_list)
        ]
