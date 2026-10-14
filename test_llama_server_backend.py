"""
External llama.cpp server backend tests — validates the OpenAI-compatible HTTP
client against a real local server (Python stdlib http.server), plus the
URL persistence helpers and factory registration.

Run with:  python test_llama_server_backend.py
"""

import sys
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import os
import json
import threading
from http.server import BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from http.server import HTTPServer


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class _State:
    last_chat_payload = None


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence test noise
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/models"):
            self._send(200, {"object": "list", "data": [{"id": "local-model"}]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        payload = json.loads(raw.decode("utf-8") or "{}")
        if self.path.endswith("/chat/completions"):
            _State.last_chat_payload = payload
            # Echo back a deterministic completion so we can assert it round-trips.
            user = next((m["content"] for m in payload.get("messages", []) if m.get("role") == "user"), "")
            self._send(200, {
                "choices": [{"message": {"role": "assistant", "content": f"ACK:{user}"}}]
            })
        else:
            self._send(404, {"error": "not found"})


def _start_server():
    srv = _ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, port


def test_url_persistence():
    from modules.llm_backends import llama_server_backend as lsb
    cleaned = lsb.set_server_url("http://127.0.0.1:9999/v1/")
    assert cleaned == "http://127.0.0.1:9999/v1", f"trailing slash not stripped: {cleaned}"
    assert lsb.get_server_url() == "http://127.0.0.1:9999/v1"
    # Invalid inputs raise.
    for bad in ["", "ftp://x", "not-a-url"]:
        try:
            lsb.set_server_url(bad)
            raise AssertionError(f"expected ValueError for {bad!r}")
        except ValueError:
            pass
    print("[OK] set/get_server_url: persistence, trailing-slash strip, validation")


def test_backend_against_live_server():
    from modules.llm_backends.llama_server_backend import LlamaServerBackend

    srv, port = _start_server()
    try:
        base = f"http://127.0.0.1:{port}/v1"
        b = LlamaServerBackend(name="test-server", base_url=base)

        assert b.is_available() is True
        b.load()  # probes /models — must not raise against the live server
        assert b._loaded is True

        out = b.generate(
            [{"role": "user", "content": "hello world"}],
            max_new_tokens=64,
            do_sample=True,
            temperature=0.3,
            repetition_penalty=1.05,
        )
        assert out == "ACK:hello world", f"unexpected completion: {out!r}"
        # Verify the payload we actually sent matches the OpenAI chat schema.
        p = _State.last_chat_payload
        assert p["messages"][0]["content"] == "hello world"
        assert p["max_tokens"] == 64
        assert p["temperature"] == 0.3
        assert p["stream"] is False
        assert p.get("repeat_penalty") == 1.05

        # Batch path returns one result per prompt, in order.
        # NOTE: generate_batch takes a list of message-LISTS (one per prompt).
        outs = b.generate_batch(
            [[{"role": "user", "content": "a"}], [{"role": "user", "content": "b"}]],
            [16, 16],
        )
        assert outs == ["ACK:a", "ACK:b"], f"unexpected batch: {outs}"
        print("[OK] LlamaServerBackend generate + generate_batch round-trip against live server")
    finally:
        srv.shutdown()


def test_backend_unreachable():
    from modules.llm_backends.llama_server_backend import LlamaServerBackend
    # Port 1 (tcpmux) is not an HTTP server -> load() must fail with a clear message.
    b = LlamaServerBackend(name="dead", base_url="http://127.0.0.1:1/v1")
    try:
        b.load()
        raise AssertionError("expected RuntimeError when server unreachable")
    except RuntimeError as e:
        assert "llama-server" in str(e).lower() or "reach" in str(e).lower(), str(e)
    print("[OK] LlamaServerBackend.load() fails fast with actionable message when unreachable")


def test_factory_registration():
    from modules.llm_backends.factory import get_available_backends, get_backend, LLAMA_SERVER_BACKEND_NAME
    av = get_available_backends()
    assert LLAMA_SERVER_BACKEND_NAME in av, "server backend missing from available list"
    be = get_backend(LLAMA_SERVER_BACKEND_NAME)
    assert be.name == LLAMA_SERVER_BACKEND_NAME
    assert be.capabilities.get("languages") == "all"
    print("[OK] factory lists + resolves the llama.cpp server backend")


def main():
    print("=" * 60)
    print("llama.cpp server backend tests")
    print("=" * 60)
    test_url_persistence()
    test_backend_against_live_server()
    test_backend_unreachable()
    test_factory_registration()
    print("=" * 60)
    print("[OK] All llama.cpp server backend tests passed!")
    print("=" * 60)


if __name__ == "__main__":
    main()
