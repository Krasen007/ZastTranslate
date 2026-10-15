"""
Translation-step tests — validates:
  1. Importing an already-translated SRT into the Translation tab (standalone step).
  2. step5_save_translation's fallback when the Dataframe grid comes back empty
     but state.translated_segments is still populated (the intermittent
     "No translation data found" bug).

Run with:  python test_translation_import.py
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _write_srt(path, cues):
    """cues: list of (start_sec, end_sec, text)."""
    from modules.utils import format_timestamp
    lines = []
    for i, (start, end, text) in enumerate(cues, 1):
        lines.append(str(i))
        lines.append(f"{format_timestamp(start)} --> {format_timestamp(end)}")
        lines.append(text)
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


class _FakeFile:
    """Mimics Gradio's uploaded-file object (has .name)."""
    def __init__(self, path):
        self.name = path


def test_import_translation_srt():
    import app

    # Reset state to a clean transcription of the same length as the SRT.
    app.state.video_info = {"detected_language": "fr"}
    app.state.segments = [
        {"start": 0.0, "end": 3.0, "text": "Bonjour a tous"},
        {"start": 4.0, "end": 7.0, "text": "Bienvenue dans cette video"},
    ]
    app.state.translated_segments = []

    tmp_srt = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_test_translation.srt")
    try:
        # Use cues that survive the parser's sentence-fragment merge (terminal
        # punctuation, >=4 words, duration >= 1.8s) — same rules as Tab 2 import.
        _write_srt(tmp_srt, [
            (0.0, 3.0, "Hello everyone here."),
            (4.0, 7.0, "Welcome to this video today."),
        ])

        # Returns 8 values: status, data, json_html, title, desc, srt_path,
        # subtitle-selection default, and the interactive gr.update.
        (status, data, json_html, title, desc, srt_path, sub_sel, _interactive) = app.step3b_import_translation_srt(
            _FakeFile(tmp_srt), "English"
        )

        assert "imported" in status.lower(), f"Unexpected status: {status}"
        assert len(data) == 2, f"Expected 2 rows, got {len(data)}"
        # Grid columns: Start, End, Original, Translation, Fitted
        assert data[0][2] == "Bonjour a tous", f"Original text not preserved: {data[0]}"
        assert data[0][3] == "Hello everyone here.", f"Translation not set: {data[0]}"
        # State populated so Validate -> Dubbing works.
        assert len(app.state.translated_segments) == 2, "translated_segments not populated"
        seg0 = app.state.translated_segments[0]
        assert seg0["translated_text"] == "Hello everyone here."
        assert seg0["normal_text"] == "Hello everyone here."
        assert seg0["text"] == "Bonjour a tous"
        # Target language recorded for CPS / dubbing.
        assert app.state.video_info.get("target_language_name") == "English"
        assert app.state.video_info.get("target_language") == "eng_Latn"
        # Subtitle selection defaults to the translation.
        assert sub_sel == "Translation"
        print("[OK] step3b_import_translation_srt populates grid + state (2 segments)")
    finally:
        if os.path.exists(tmp_srt):
            os.remove(tmp_srt)


def test_import_translation_srt_no_file():
    import app
    out = app.step3b_import_translation_srt(None, "English")
    assert out[0].lower().startswith("error"), f"Expected error, got: {out[0]}"
    print("[OK] step3b_import_translation_srt(None) returns a clean error")


def test_save_translation_empty_grid_fallback():
    import app

    # Simulate the intermittent bug: the grid payload is empty but state is valid.
    app.state.video_info = {"detected_language": "fr"}
    app.state.translated_segments = [
        {"start": 0.0, "end": 2.0, "text": "Bonjour",
         "translated_text": "Hello", "normal_text": "Hello"},
        {"start": 2.5, "end": 4.0, "text": "Le monde",
         "translated_text": "The world", "normal_text": "The world"},
    ]

    # Empty / None grid payload (what Gradio 6 sometimes delivers).
    for empty in ([], None):
        # Returns 5: status, continue-update, json_html, dubbing df, dubbing-btn update
        status, synth_upd, json_html, df_data, _dub_btn = app.step5_save_translation(
            empty, "Fitted Translation", "", ""
        )
        assert "No translation data found" not in status, (
            f"Fallback failed for payload {empty!r}: {status}"
        )
        assert "validated" in status.lower(), f"Expected validated, got: {status}"
        assert len(df_data) == 2, f"Dubbing df not rebuilt from state: {df_data}"
    print("[OK] step5_save_translation falls back to state on empty grid payload")


def test_save_translation_truly_empty_still_errors():
    import app
    app.state.translated_segments = []
    status, _, _, _, _ = app.step5_save_translation([], "Fitted Translation", "", "")
    assert "No translation data found" in status, f"Expected error when nothing to save: {status}"
    print("[OK] step5_save_translation still errors when there is genuinely no data")


def main():
    print("=" * 60)
    print("Translation import / validate tests")
    print("=" * 60)
    test_import_translation_srt()
    test_import_translation_srt_no_file()
    test_save_translation_empty_grid_fallback()
    test_save_translation_truly_empty_still_errors()
    print("=" * 60)
    print("[OK] All translation-step tests passed!")
    print("=" * 60)


if __name__ == "__main__":
    main()
