"""
Regression tests — validates:
  1. The legacy NLLB `Translator` module is fully removed (TODO item 5) and
     `modules.translator` is no longer importable.
  2. Tab 2 "Whisper only" skips translation entirely: original text is mirrored
     into the translation columns and neither the LLM nor NLLB is touched.
  3. Tab 4 / Bulk "Clone from file" plumbing: `TimeSync.sync_all` and
     `_generate_all_natural` forward `voice_path` to the TTS backend, and the
     BgTTS backend falls back to the `load()`-time reference instead of the
     bundled `sample_female_bg1.wav`.

Run with:  python test_whisper_clone_fixes.py
"""

import sys
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import importlib.util
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class _NoLLMTouches:
    """Fails the test if ANY attribute (i.e. any LLM call) is accessed."""
    def __getattr__(self, name):
        raise AssertionError(f"LLM reformulator touched during Whisper-only translation: .{name}")


def _quiet_progress(*_args, **_kwargs):
    pass


def test_nllb_translator_deleted():
    if importlib.util.find_spec("modules.translator") is not None:
        raise AssertionError("modules/translator.py still exists — NLLB Translator must be deleted")
    print("[OK] modules.translator is gone (NLLB Translator deleted)")


def test_whisper_only_skips_translation():
    import app

    saved_segments = app.state.segments
    saved_info = app.state.video_info
    saved_reformulator = app.reformulator
    try:
        app.state.video_info = {"detected_language": "fr"}
        app.state.segments = [
            {"start": 0.0, "end": 2.5, "text": "Bonjour tout le monde"},
            {"start": 3.0, "end": 6.0, "text": "Bienvenue dans cette video"},
        ]
        app.state.translated_segments = []
        # Any call into the reformulator (LLM) must blow up: Whisper-only mode
        # must never load/touch a translation model.
        app.reformulator = _NoLLMTouches()

        status, data, _json_html, title, desc, _srt, _sub_sel, _interactive = app.step4_translate(
            "English", "", "", whisper_only=True, progress=_quiet_progress
        )

        segs = app.state.translated_segments
        assert len(segs) == 2, f"Expected 2 translated segments, got {len(segs)}"
        for src, out in zip(app.state.segments, segs):
            assert out["translated_text"] == src["text"], (
                f"Whisper-only must mirror the original text, got {out['translated_text']!r}"
            )
            assert out["normal_text"] == src["text"], (
                f"normal_text must mirror the original text, got {out['normal_text']!r}"
            )
        # Grid rows: Start, End, Original, Translation, Fitted
        assert data[0][2] == "Bonjour tout le monde", f"Original column wrong: {data[0]}"
        assert data[0][3] == "Bonjour tout le monde", f"Translation column must keep the original: {data[0]}"
        assert title == "" and desc == "", "Whisper-only must not translate metadata"
        assert "whisper-only" in status.lower(), f"Unexpected status: {status}"
        print("[OK] Whisper-only mirrors original text and never touches the LLM/NLLB")
    finally:
        app.state.segments = saved_segments
        app.state.video_info = saved_info
        app.reformulator = saved_reformulator



def test_sync_all_forwards_voice_path():
    from modules.time_sync import TimeSync

    recorded = []

    class _FakeTTS:
        capabilities = {}

        def generate(self, **kwargs):
            recorded.append(kwargs.get("ref_audio_path"))
            raise RuntimeError("stop before any real synthesis")

    ts = TimeSync(_FakeTTS())

    def _fake_sync_segment(seg, language, voice_path=None, gender="Woman"):
        recorded.append(voice_path)
        return {
            "synced_path": None, "start": seg["start"], "end": seg["end"],
            "final_text": seg.get("text", ""), "sped_up": False,
            "truncated": False, "overflow": 0.0, "reformulated": False,
        }

    ts.sync_segment = _fake_sync_segment
    segs = [{"start": 0.0, "end": 2.0, "text": "bg text"}]
    ts.sync_all(segs, "bg", voice_mapping=None, total_duration=10.0, voice_path="X:/voice/ref.wav")
    assert recorded == ["X:/voice/ref.wav"], (
        f"sync_all must forward voice_path to every segment, got {recorded}"
    )
    print("[OK] sync_all forwards voice_path (clone-from-file reaches tts.generate)")


def test_generate_all_natural_forwards_voice_path():
    import glob
    import numpy as np
    import soundfile as sf
    from config import TEMP_DIR
    from modules.time_sync import TimeSync

    recorded = []

    class _FakeTTS:
        capabilities = {}

        def generate(self, text, language, output_path, ref_audio_path=None, speed=1.0,
                     duration=None, gender="Woman", **kwargs):
            recorded.append(ref_audio_path)
            sf.write(output_path, np.zeros(2205, dtype=np.float32), 44100)
            return {"duration": 0.05}

    # Use an unusual start time and clear any stale cache so the call runs.
    seg = {"start": 432.17, "end": 433.17, "text": "bg text"}
    for f in glob.glob(os.path.join(TEMP_DIR, "*432.17*.wav")):
        try:
            os.remove(f)
        except OSError:
            pass

    ts = TimeSync(_FakeTTS())
    ts._generate_all_natural([seg], "bg", voice_mapping=None, voice_path="X:/voice/ref.wav")
    assert recorded == ["X:/voice/ref.wav"], (
        f"_generate_all_natural must forward voice_path, got {recorded}"
    )
    for f in glob.glob(os.path.join(TEMP_DIR, "*432.17*.wav")):
        try:
            os.remove(f)
        except OSError:
            pass
    print("[OK] _generate_all_natural forwards voice_path (Never-Cut clone path)")


def test_bgtts_falls_back_to_load_ref():
    from modules.tts_backends.bgtts import BgTTSBackend

    backend = BgTTSBackend()  # __init__ only — no model download/load
    here = os.path.abspath(__file__)
    backend._load_ref = here  # simulates load(ref_audio_path=<uploaded file>)

    resolved = backend._resolve_reference(None, "Woman")
    assert resolved == here, (
        "BgTTS must fall back to the load()-time reference, "
        f"got {resolved!r} (would be sample_female_bg1.wav — the reported bug)"
    )
    # An explicit per-segment ref always wins over the load()-time fallback.
    with tempfile.NamedTemporaryFile(suffix=".bak", delete=False) as other_file:
        other = other_file.name
    try:
        resolved = backend._resolve_reference(other, "Woman")
        assert resolved == other, f"Explicit ref must win, got {resolved!r}"
    finally:
        os.remove(other)
    print("[OK] BgTTS _resolve_reference falls back to the load()-time reference")


if __name__ == "__main__":
    test_nllb_translator_deleted()
    test_whisper_only_skips_translation()
    test_sync_all_forwards_voice_path()
    test_generate_all_natural_forwards_voice_path()
    test_bgtts_falls_back_to_load_ref()
    print("\nAll whisper-only + clone-from-file regression tests passed.")