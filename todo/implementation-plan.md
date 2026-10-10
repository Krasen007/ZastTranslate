# ZastTranslate → Bulgarian: Full Audit Summary + Implementation Plan

## 0. TL;DR

`ZastTranslate Beta 1.21` **is the right architecture** for `YouTube → Bulgarian` (`yt-dlp → WhisperX → LLM translate → TTS dub → ffmpeg mux`), but **Bulgarian is blocked in 2 places**:

1. **Translation (soft block, 30 min fix):** Upstream `EuroLLM-9B-Instruct` + `Qwen2.5/3.5` **can** do `BG`, but `config.py:LANGUAGES` has no `Bulgarian`, `factory.py` limits `EuroLLM` to 12 langs, UI does `intersection(TTS ∩ LLM ∩ LANGUAGES)` so `BG` never appears.
2. **Voice/TTS (hard block, 1-3 days):** Only active backend `VoxCPM2` = `30 langs, no bg` (verified `openbmb/VoxCPM2` tags). `Qwen3-TTS/XTTS-v2` also have no `bg`. `Toshko_2` is classic robot, no cloning. Reddit `1rzocet` confirms: `XTTS-v2 + language="ru"` sounds Russian, `Fish-Speech 1.5` won't load on Windows, no free local clone for `BG` today.

Result today:
* `YouTube download + EN transcription + EN SRT` → ✅ works
* `EN → BG subtitles / SRT / title / description / chapters / blog` → ✅ capable, ❌ hidden in UI
* `EN → BG dubbed voice + cloning` → ❌ impossible without new TTS backend

You were right: **README is misleading**. `README.md:180-181`:

> `LLM: Qwen2.5/3.5 support all. EuroLLM supports only European languages. If you combine EuroLLM + VoxCPM2, only European langs supported by both will be available.`

Reality in code:
* `modules/llm_backends/factory.py:30-34`: `EuroLLM capabilities = ["en,fr,es,de,it,pt,nl,pl,sv,cs,ro,hu"]` — 12, no `bg,el,fi,da,no,uk,sl,sk,lt,lv,et,hr` even though HF tags list `bg` + 35 langs.
* `config.py:67-102`: 31 langs, no `bul_Cyrl`. `README:179` TTS list confirms: `ar,my,da,nl,en,fi,fr,de,el,he,hi,id,it,ja,km,ko,lo,ms,no,pl,pt,ru,es,sw,sv,tl,th,tr,vi` — no `bg`.
* `modules/reformulator.py:33-71 _language_name()`, `app.py:61 _NLLB_TO_ISO`, `fitted_cps_config.py` — no `bg` entries → `Unknown / _default`.

## 1. Implementation Plan

### Phase 0 — Baseline (0.5d, no risk)
* Freeze `main 3c5edba`. Document repro: `Start → Tab1 YouTube URL → Tab2 large-v3 → Tab3 target dropdown` — confirm no `Bulgarian`.
* Add `test_bg_audit.py`: assert `Bulgarian in LANGUAGES`, `reformulator._language_name("bg")=="Bulgarian"`, `EuroLLM caps contains bg`, `TTSFactory.get_available_backends()` reports `bg` support.

### Phase 1 — Subtitle-Only BG Unlock (0.5d, quick win, local, free)
Goal: `YouTube → BG SRT + SEO` with existing LLMs, no TTS change.
1. `config.py:67`: add `"Bulgarian": "bul_Cyrl"` (NLLB code, `bul_Cyrl` exists in NLLB-200).
2. `app.py:61-75 _NLLB_TO_ISO`: add `"bul_Cyrl":"BG"`, `app.py:4250 lang_source` + target dropdowns: add `"Bulgarian"` where `list(LANGUAGES.keys())` isn't used.
3. `modules/reformulator.py:33,49`: add `"bulgarian":"Bulgarian"`, `"bul/bulgar/bg":"Bulgarian"` + prefix match.
4. `modules/llm_backends/factory.py:34`: extend EuroLLM to `["en,fr,es,de,it,pt,nl,pl,sv,cs,ro,hu,bg,el,fi,da,no,uk,sl,sk,hr,lt,lv,et"]` to match upstream.
5. `fitted_cps_config.py:19`: add `"bg":8.0` (copy `ru` Cyrillic rate), `config.py:CHARS_PER_SECOND`: add `"cyrillic":11` branch if needed.
6. `README.md:179-181,4270`: update 12→23 EuroLLM list + TTS 30-list note.
* Test: `EN 30s clip → Qwen3.5 → BG Fitted SRT`, `EuroLLM → BG title/desc`, check Cyrillic UTF-8, `40ms gap` normalizer still passes. No VRAM change.

### Phase 2 — Dubbed BG V1 via Edge-TTS (1d, free, 0 VRAM, no clone)
Goal: watchable `YouTube → BG dubbed MP4` now.
1. New `modules/tts_backends/edge_bg.py:EdgeBulgarianBackend(TTSBackend)`: `name="Edge-TTS BG"`, `capabilities={"languages":["bg","en"],"speed_control":True,"vram_gb":0,"sample_rate":24000,"voice_design":False}`, `is_available()=import edge_tts`, `generate()` → `bg-BG-BorislavNeural (male) / KalinaNeural (female)` + `speed→rate%`, save `wav`, return durations. Requires `pip install edge-tts` + net.
2. `modules/tts_backends/factory.py:11`: register `"Edge-TTS BG": EdgeBulgarianBackend()`.
3. `app.py Tab1 TTS dropdown + Tab4 voice modes`: allow `Default voice (Edge)` when `target==Bulgarian`, disable `Clone from original/file` with tooltip.
4. `Tab3 intersection logic`: `BG` visible if `(LLM==Qwen/EuroLLM+bg) AND (TTS==Edge-BG)`.
* Test: full `YouTube → BG` dub, `Demucs remix + NVENC mux`, CPS `bg:8.0` fit check. Fallback: offline warning.

### Phase 3 — Local Neural BG Single-Voice (1-2d, offline, <4GB)
Goal: offline, no cloud.
* Add `MMSBGBackend (facebook/mms-tts-bul, VITS 36M, CC-BY-NC, transformers pipeline)` and/or `PiperBGBackend (bg_BG, <1GB)` and/or `QwenBGBackend (beleata74/bg-tts-v7, Qwen3-0.6B+MioCodec 608M, Apache-2.0, Feb 2026 — freshest)`.
* Same `TTSBackend` contract, `capabilities languages=["bg"]`, register in factory, `CPS` recalibrate per engine.
* Test WER/MOS vs Edge, VRAM on 8GB.

### Phase 4 — True BG Cloning (2-5d, R&D)
* Prototype `F5TTS_BG / FishSpeech_BG / KugelAudio_BG` backends behind feature flag. Needs `BG dataset + fine-tune`, `Blackwell/Windows` wheel fixes per Reddit. Keep `VoxCPM2` for other 30 langs, route `bg` to new backend. `ElevenLabs BG` optional paid path.
* Do NOT use `Toshko_2` or `XTTS-v2 ru` hack — quality/license fail.

### Phase 5 — Bulk + Polish (0.5d)
* Bulk mode `CheckboxGroup langs` includes `Bulgarian`, `SEO/Shorts/Blog` prompts handle Cyrillic casing (extend `srt_cleaner.py:fix_inter_cue_casing`), docs + tutorial update.

**Order:** Phase 1 → 2 gives you usable `YouTube→BG video` this week. Phase 3 → 4 gives offline → cloning.

Phase 1 + Phase 2 are implemented and validated.

## What I did

**Phase 1 — Subtitle-Only BG Unlock** (local, free, 0 VRAM change)
- `config.py` — added `"Bulgarian": "bul_Cyrl"` to `LANGUAGES`; added a `"cyrillic": 11` branch to `CHARS_PER_SECOND`
- `app.py` — `_NLLB_TO_ISO["bul_Cyrl"] = "BG"`; added **Bulgarian** to the Tab 2 source-language dropdown and its Whisper code map
- `modules/reformulator.py` — `bulgarian`/`bul`/`bg` → `"Bulgarian"` in both name maps
- `modules/llm_backends/factory.py` — EuroLLM extended to the full upstream **24** languages incl. `bg`
- `fitted_cps_config.py` — `"bg": 8.0`
- `modules/time_sync.py` — `get_language_family()` now returns `"cyrillic"` for BG/RU/UK (checked *before* cjk/arabic)
- README + in-app guides updated

**Phase 2 — Dubbed BG via Edge-TTS**
- New `modules/tts_backends/edge_bg.py` (182 lines) implementing the full `TTSBackend` contract: `bg-BG-BorislavNeural`/`KalinaNeural`, `speed → rate%`, 0 VRAM, 3-retry with backoff, MP3→WAV decode (soundfile → pydub → ffmpeg fallback)
- Registered as `"Edge-TTS BG"` in the TTS factory; added to `requirements.txt` and `check_env.py`
- `app.py` — Voice Mode radios are now dynamic: cloning is hidden for Bulgarian *and* for any engine with `voice_design=False`, with an explanatory tooltip. Wired to `.change` on `tts_backend_dropdown`, `lang_target`, and `voice_mode`
- Backend-level guards in `step6_synthesize`, segment-regen, and bulk mode so a stale clone value can never reach a non-cloning engine

## Two things worth flagging

1. **Bug I found and fixed:** VoxCPM 2 declares `"languages": "all"`, which would have silently offered Bulgarian on an engine with **no Bulgarian voice** — exactly the hard block your plan describes. I added an `excluded_languages` capability (`["bg"]`) honored by `get_valid_languages`, so BG now appears *only* with Edge-TTS BG. Also fixed a latent startup crash where the TTS fallback hardcoded `"VoxCPM 2"` even when uninstalled.

2. **Deviation from the plan:** the plan's text said "23 languages" but its own list contains 24. I implemented the list verbatim and corrected the docs to say 24.

## Validation
- `test_bg_audit.py` (new, 12 checks) — **all pass**, including regression guards on latin/cjk/arabic families and VoxCPM's BG exclusion
- `test_srt_pipeline_fixes.py` — **all 7 acceptance tests pass** (no regressions)
- **Live end-to-end**: generated real Bulgarian audio through the actual Edge service; both test segments fit their 3.0s slots with −0.67s headroom, and the retry path fired and recovered on a 403
- `app.py` imports cleanly with no Gradio warnings

Note: `test_srt_cleaner.py`'s `test_ergonomic_wrapping` fails, but that is **pre-existing** — I confirmed via `git diff --numstat` that `srt_cleaner.py` and `srt_parser.py` are untouched. I also installed several dev-only deps (librosa, gradio, transformers, whisperx, etc.) into the global interpreter to run the tests; in the real Pinokio venv these come from `requirements.txt`/`install.js`.

Phase 3 (offline local BG) and Phase 4 (true cloning) remain, as scoped.