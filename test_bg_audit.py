"""
Bulgarian support audit — validates Phase 1 (subtitle-only BG unlock) and
Phase 2 (dubbed BG via the Edge-TTS BG backend) of the implementation plan.

Run with:  python test_bg_audit.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import LANGUAGES, CHARS_PER_SECOND
from fitted_cps_config import FITTED_CPS_BY_LANG, get_fitted_cps, get_effective_cps
from modules.reformulator import Reformulator
from modules.llm_backends.factory import get_available_backends as get_llm_backends
from modules.tts_backends.base import TTSBackend
from modules.time_sync import TimeSync


def test_bg_in_languages():
    assert "Bulgarian" in LANGUAGES, "Bulgarian missing from config.LANGUAGES"
    assert LANGUAGES["Bulgarian"] == "bul_Cyrl", f"Expected bul_Cyrl, got {LANGUAGES['Bulgarian']}"
    print(f"[OK] config.LANGUAGES['Bulgarian'] = {LANGUAGES['Bulgarian']}")


def test_reformulator_language_name():
    r = Reformulator()
    assert r._language_name("bg") == "Bulgarian"
    assert r._language_name("bul_Cyrl") == "Bulgarian"
    assert r._language_name("Bulgarian") == "Bulgarian"
    print("[OK] reformulator._language_name resolves bg / bul_Cyrl / Bulgarian -> 'Bulgarian'")


def test_eurollm_supports_bg():
    backends = get_llm_backends()
    euro = [b for name, b in backends.items() if "EuroLLM" in name]
    assert euro, "EuroLLM backend is not available in this environment"
    langs = euro[0].capabilities.get("languages")
    assert langs != "all", "EuroLLM should declare an explicit language list"
    assert "bg" in langs, f"'bg' missing from EuroLLM languages: {langs}"
    assert len(langs) == 24, f"Expected 24 EuroLLM languages, got {len(langs)}: {langs}"
    print(f"[OK] EuroLLM capabilities include 'bg' ({len(langs)} languages total)")


def test_tts_factory_reports_bg():
    from modules.tts_backends.factory import get_available_backends, get_backend

    available = get_available_backends()
    assert "Edge-TTS BG" in available, (
        f"Edge-TTS BG not available (is edge-tts installed?). Got: {list(available.keys())}"
    )

    backend = get_backend("Edge-TTS BG")
    assert isinstance(backend, TTSBackend)
    caps = backend.capabilities
    assert "bg" in caps["languages"], f"'bg' missing from Edge-TTS BG languages: {caps['languages']}"
    assert caps["voice_design"] is False, "Edge-TTS BG must not advertise voice cloning"
    assert caps["vram_gb"] == 0.0, "Edge-TTS BG must be a 0 VRAM backend"
    assert caps["speed_control"] is True, "Edge-TTS BG must expose native speed control"
    print(f"[OK] TTSFactory reports 'Edge-TTS BG' -> languages={caps['languages']}, "
          f"vram={caps['vram_gb']} GB, cloning={caps['voice_design']}")


def test_edge_voice_selection():
    from modules.tts_backends.factory import get_backend

    backend = get_backend("Edge-TTS BG")
    assert backend._pick_voice("bg", "Man") == "bg-BG-BorislavNeural"
    assert backend._pick_voice("bg", "Woman") == "bg-BG-KalinaNeural"
    assert backend._pick_voice("bul_Cyrl", "Woman") == "bg-BG-KalinaNeural"
    # Non-Bulgarian targets fall back to the English voice, per its declared capability.
    assert backend._pick_voice("fr", "Woman") == "en-US-EmmaMultilingualNeural"
    print("[OK] Edge-TTS BG voice mapping: bg/Borislav, bg/Kalina, en fallback")


def test_edge_rate_math():
    from modules.tts_backends.factory import get_backend

    backend = get_backend("Edge-TTS BG")
    assert backend._compute_rate("тест", speed=1.0, duration=None) == 0
    assert backend._compute_rate("тест", speed=1.25, duration=None) == 25
    # 80 chars at 8 CPS = 10s natural, squeezed into a 4s slot -> ~+138% clamped to +60
    rate = backend._compute_rate("я" * 80, speed=1.0, duration=4.0)
    assert rate == 60, f"Expected clamped +60%, got {rate:+d}%"
    # A line that already fits must not be slowed down.
    assert backend._compute_rate("здравей", speed=1.0, duration=30.0) == 0
    print("[OK] Edge-TTS BG rate math: speed->rate%, overflow pre-compensation, clamped")

def test_fitted_cps_bg():
    assert FITTED_CPS_BY_LANG.get("bg") == 8.0, f"Expected bg:8.0, got {FITTED_CPS_BY_LANG.get('bg')}"
    assert get_fitted_cps("bg") == 8.0
    assert get_effective_cps("bg", {"bg": 9.5}) == 9.5, "user overrides must win for bg"
    print("[OK] fitted CPS: bg = 8.0 chars/sec (user override honoured)")


def test_cyrillic_chars_per_second():
    assert "cyrillic" in CHARS_PER_SECOND, "cyrillic family missing from config.CHARS_PER_SECOND"
    assert TimeSync.get_language_family("bul_Cyrl") == "cyrillic"
    assert TimeSync.get_language_family("bg") == "cyrillic"
    assert TimeSync.get_language_family("rus_Cyrl") == "cyrillic"
    # Regression guard: other families must be untouched.
    assert TimeSync.get_language_family("fra_Latn") == "latin"
    assert TimeSync.get_language_family("zho_Hans") == "cjk"
    assert TimeSync.get_language_family("arb_Arab") == "arabic"
    print(f"[OK] CHARS_PER_SECOND['cyrillic'] = {CHARS_PER_SECOND['cyrillic']}; family mapping correct")


def test_nllb_to_iso_bg():
    import app
    assert app._NLLB_TO_ISO.get("bul_Cyrl") == "BG"
    assert app._get_iso_code("bul_Cyrl") == "BG"
    print("[OK] app._NLLB_TO_ISO['bul_Cyrl'] = 'BG'")


def test_voice_mode_gating_for_bg():
    import app

    choices, value, info = app.get_voice_mode_choices("Edge-TTS BG", "Bulgarian")
    assert choices == [app.VOICE_MODE_DEFAULT], f"Cloning must be hidden for BG, got {choices}"
    assert value == app.VOICE_MODE_DEFAULT
    assert "cloning" in info.lower()

    # A clonable engine + non-BG target keeps the full choice set.
    choices_ok, _, _ = app.get_voice_mode_choices("VoxCPM 2", "French")
    assert app.VOICE_MODE_CLONE_ORIGINAL in choices_ok, f"VoxCPM 2 + French should allow cloning: {choices_ok}"
    assert app.VOICE_MODE_CLONE_FILE in choices_ok

    # A non-clonable engine hides cloning even for non-BG targets.
    choices_edge_en, _, _ = app.get_voice_mode_choices("Edge-TTS BG", "French")
    assert choices_edge_en == [app.VOICE_MODE_DEFAULT]

    assert app.is_clone_requested("Clone from original") is True
    assert app.is_clone_requested("Clone from file") is True
    assert app.is_clone_requested(app.VOICE_MODE_DEFAULT) is False
    assert app.is_clone_requested("Default voice") is False
    print("[OK] Voice Mode gating: BG and non-clonable engines -> Default voice only")


def test_valid_languages_intersection():
    import app

    # Qwen ("all") + Edge-TTS BG (["bg","en"]) must expose Bulgarian.
    langs = app.get_valid_languages("Edge-TTS BG", "Qwen2.5-7B-Instruct (Ideal for 8 GB VRAM GPUs)")
    assert "Bulgarian" in langs, f"Bulgarian missing from intersection: {langs}"
    print(f"[OK] Intersection(Qwen + Edge-TTS BG) includes Bulgarian ({len(langs)} langs)")


def test_voxcpm_does_not_offer_bulgarian():
    import app

    # VoxCPM 2 declares languages="all" but ships no Bulgarian voice, so the
    # excluded_languages capability must hide Bulgarian from the dropdown.
    langs = app.get_valid_languages("VoxCPM 2", "Qwen2.5-7B-Instruct (Ideal for 8 GB VRAM GPUs)")
    assert "Bulgarian" not in langs, (
        "Bulgarian must NOT be offered with VoxCPM 2 — it has no Bulgarian voice. "
        "Users must pick Edge-TTS BG for Bulgarian dubbing."
    )
    # Regression guard: other languages must survive the exclusion filter.
    assert "French" in langs and "Russian" in langs, f"Unexpected intersection: {langs}"
    print(f"[OK] VoxCPM 2 hides Bulgarian (excluded_languages honoured); "
          f"{len(langs)} other languages remain")


def test_bgtts_registered():
    from modules.tts_backends.factory import _factory

    assert "BgTTS-38M" in _factory._backends, "BgTTS-38M not registered in the TTS factory"
    backend = _factory._backends["BgTTS-38M"]
    caps = backend.capabilities
    assert caps["languages"] == ["bg", "en"], f"Unexpected BgTTS languages: {caps['languages']}"
    assert caps["voice_design"] is True, "BgTTS-38M must advertise voice cloning"
    assert caps["sample_rate"] == 24000, f"Unexpected BgTTS sample rate: {caps['sample_rate']}"
    assert caps["fitted_cps"] == 8.0, f"Unexpected BgTTS fitted_cps: {caps['fitted_cps']}"
    assert caps["speed_control"] is False
    print("[OK] BgTTS-38M registered: languages=['bg','en'], cloning=True, 24kHz")


def test_caps_can_clone_helper():
    import app

    all_caps = {"voice_design": True, "languages": "all", "excluded_languages": ["bg"]}
    assert app._caps_can_clone(all_caps, "French") is True
    assert app._caps_can_clone(all_caps, "Bulgarian") is False, "excluded_languages must block bg"
    # Fail open on an unknown/blank target (state may not be resolved yet).
    assert app._caps_can_clone(all_caps, None) is True

    assert app._caps_can_clone({"voice_design": False}, "French") is False

    bgtts_caps = {"voice_design": True, "languages": ["bg", "en"]}
    assert app._caps_can_clone(bgtts_caps, "Bulgarian") is True
    assert app._caps_can_clone(bgtts_caps, "French") is False

    # Display name, NLLB code and ISO code all resolve to the same decision.
    assert app._target_lang_iso("Bulgarian") == "bg"
    assert app._target_lang_iso("bul_Cyrl") == "bg"
    assert app._target_lang_iso("bg") == "bg"
    print("[OK] _caps_can_clone: excluded/unsupported blocked, unknown fails open, bg resolves")


def test_bgtts_enables_bulgarian_cloning():
    import app

    choices, value, _ = app.get_voice_mode_choices("BgTTS-38M", "Bulgarian")
    assert app.VOICE_MODE_CLONE_ORIGINAL in choices, f"BgTTS-38M must allow BG cloning: {choices}"
    assert app.VOICE_MODE_CLONE_FILE in choices
    assert value == app.VOICE_MODE_DEFAULT
    print("[OK] BgTTS-38M offers Bulgarian cloning (unlike VoxCPM 2 / Edge-TTS BG)")


TESTS = [
    test_bg_in_languages,
    test_reformulator_language_name,
    test_eurollm_supports_bg,
    test_tts_factory_reports_bg,
    test_edge_voice_selection,
    test_edge_rate_math,
    test_fitted_cps_bg,
    test_cyrillic_chars_per_second,
    test_nllb_to_iso_bg,
    test_voice_mode_gating_for_bg,
    test_valid_languages_intersection,
    test_voxcpm_does_not_offer_bulgarian,
    test_bgtts_registered,
    test_caps_can_clone_helper,
    test_bgtts_enables_bulgarian_cloning,
]

if __name__ == "__main__":
    print("=" * 62)
    print("Bulgarian Support Audit (Phase 1 + Phase 2)")
    print("=" * 62)

    failures = []
    for t in TESTS:
        try:
            t()
        except Exception as e:
            failures.append((t.__name__, e))
            print(f"[FAIL] {t.__name__}: {type(e).__name__}: {e}")

    print("=" * 62)
    if failures:
        print(f"[FAIL] {len(failures)}/{len(TESTS)} test(s) failed:")
        for name, err in failures:
            print(f"   - {name}: {err}")
        sys.exit(1)
    print(f"[OK] All {len(TESTS)} Bulgarian audit checks passed!")
    sys.exit(0)

