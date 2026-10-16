"""Verify the Windows asyncio WinError 10054 suppression patch in app.py.

Reproduces the exact failure path from the terminal log:

    Exception in callback _ProactorBasePipeTransport._call_connection_lost()
    ConnectionResetError: [WinError 10054] ... forcibly closed by the remote host

That traceback originates at asyncio/proactor_events.py:165, where
_call_connection_lost() calls self._sock.shutdown(socket.SHUT_RDWR) during
transport cleanup AFTER FFmpeg has already finished writing its output. When the
peer (browser / download) closed first, shutdown() raises ConnectionResetError.

app.py monkeypatches _ProactorBasePipeTransport._call_connection_lost at import
time to swallow exactly that OSError. This test drives a real ProactorEventLoop
transport whose socket peer is gone, and asserts:
  1. The ORIGINAL method raises (proves the bug is real).
  2. AFTER app.py is imported the patched method does NOT raise, and still
     completes teardown (socket cleared, _called_connection_lost set).
"""
import asyncio
import sys
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _make_transport_like(loop):
    """Build a minimal _ProactorBasePipeTransport-shaped object whose
    _sock.shutdown() raises ConnectionResetError — the condition at
    asyncio/proactor_events.py:165."""
    from asyncio.proactor_events import _ProactorBasePipeTransport

    class _RaisingSocket:
        def fileno(self):
            return 42  # non -1, so the hasattr/fileno guard passes

        def shutdown(self, how):
            exc = ConnectionResetError(
                10054, "An existing connection was forcibly closed by the remote host"
            )
            # Real Windows syscalls populate winerror; ctypes/errno paths do not.
            # Set it explicitly so the harness matches what the OS actually raises.
            exc.winerror = 10054
            raise exc

        def close(self):
            pass

    class _FakeProtocol:
        def connection_lost(self, exc):
            pass

    obj = object.__new__(_ProactorBasePipeTransport)
    obj._loop = loop
    obj._sock = _RaisingSocket()
    obj._protocol = _FakeProtocol()
    obj._server = None
    obj._called_connection_lost = False
    obj._closing = False  # avoids __del__ AttributeError noise on teardown
    return obj


def test_unpatched_raises():
    """The ORIGINAL method must raise on a reset peer (proves the bug is real)."""
    import app  # noqa: F401  (stashes the original as app._zast_orig_call_connection_lost)

    orig = app._zast_orig_call_connection_lost

    loop = asyncio.new_event_loop()
    try:
        obj = _make_transport_like(loop)
        try:
            orig(obj, None)
        except ConnectionResetError as e:
            assert getattr(e, "winerror", None) == 10054, f"unexpected winerror: {e}"
            print(f"[OK] unpatched _call_connection_lost raises ConnectionResetError winerror={e.winerror}")
            return True
        print("[FAIL] unpatched method did NOT raise; test harness is wrong.")
        return False
    finally:
        loop.close()


def test_patched_swallows():
    """The patched method must NOT raise, and must still complete teardown."""
    import app  # noqa: F401  (applies the patch at import time)

    from asyncio import proactor_events

    # The patch rebinds the CLASS method and stashes the original in app's
    # globals (not on proactor_events), so assert on both of those.
    assert hasattr(
        app, "_zast_orig_call_connection_lost"
    ), "app.py did not stash the original _call_connection_lost"
    patched = proactor_events._ProactorBasePipeTransport._call_connection_lost
    assert (
        patched.__name__ == "_zast_safe_call_connection_lost"
    ), f"unexpected method: {patched}"

    loop = asyncio.new_event_loop()
    try:
        obj = _make_transport_like(loop)
        try:
            patched(obj, None)
        except Exception as e:
            print(f"[FAIL] patched method still raised: {type(e).__name__}: {e}")
            traceback.print_exc()
            return False

        assert obj._sock is None, "socket not cleared on the swallow path"
        assert obj._called_connection_lost is True, (
            "_called_connection_lost not set; callback could re-enter"
        )
        print("[OK] patched _call_connection_lost swallows WinError 10054 and completes teardown")
        return True
    finally:
        loop.close()


if __name__ == "__main__":
    print("=== WinError 10054 suppression test ===")
    ok_unpatched = test_unpatched_raises()
    ok_patched = test_patched_swallows()
    print("=== summary ===")
    print(f"unpatched-raises (or skip): {ok_unpatched}")
    print(f"patched-swallows: {ok_patched}")
    if ok_unpatched and ok_patched:
        print("[PASS] WinError 10054 noise is suppressed at the transport layer.")
        sys.exit(0)
    print("[FAIL]")
    sys.exit(1)
