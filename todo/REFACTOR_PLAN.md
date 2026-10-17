# ZastTranslate Refactor Plan

> **Audit (2026-10-16, app.py = 6,632 lines, not 7,091):** measured with
> `Get-Content app.py | Measure-Object -Line`. Your ticket says 7,091 —
> re-measure after each edit, the number drifts. Missed items marked **[MISSED]**.

## §0. What Is Actually In app.py Today (measured)

| Region (approx. lines) | Contents |
|---|---|
| 1–140 | Gradio-6 Dataframe None-guard, UTF-8 fix, warning silencers, **Windows Proactor patch** (WinError 10054), imports |
| 141–365 | ISO/NLLB maps, `_get_segments_json*`, `find_segment_audio_path`, `_build_dubbing_df_data`, `load_segment_to_editor` |
| 365–435 | **`class AppState`** + global `state`, singletons (downloader/separator/transcriber/reformulator/tts/time_sync/mixer/assembler/srt/youtube_publisher), user-config + backend resolution |
| 437–570 | `check_vram_warning`, voice-clone helpers, CPS builders, **`zast_tooltip()`**, `reset_project()` (25-tuple!) |
| 622–3250 | Handlers: `step0_check_url`, `step1_download`, `step2_transcribe`, `step2b_import_srt`, `step2_clean`, **`step2_generate/change/apply_seo_metadata` (Tab 3 SEO since Oct-2026 move)**, `step3_save_transcription`, `step4_translate`, `step3b_import_translation_srt`, `step5_save_translation`, `step6_synthesize`, `step6_regenerate_segment`, `export_*`, `step5_bulk_run`, `step6_publish_youtube`, `step7_*`, `step8_*`, `import_metadata_from_state`, `get_valid_languages` |
| 3250–3970 | **`BLOCKS_CSS` (~718 lines)** |
| 3969–4430 | **`BLOCKS_JS` (~460 lines)** + `__ZAST_DEBUG_*__` placeholders |
| 4431–4645 | `_get_tab_guide_html(1..9)` |
| 4645–5825 | **UI build: 10 top-level tabs** (1 Import, 2 Transcription, 3 Translation & SEO, 4 Dubbing, 5 Bulk, 6 Shorts, 7 Blog, 8 Thumbnails, Help, CPS) |
| 5825–5900 | `JS_DOWNLOAD_WAIT` / `JS_SRT_WAIT` / `JS_TRANSLATION_SRT_WAIT` |
| 5900–7025 | **~1,100 lines event wiring** (inline closures: `on_llm_backend_change`, `on_llama_server_use`, `on_custom_llm_use`, `on_skip_seo_change`, shorts/blog/flux handlers, `zastSwitchTab` buttons) |
| 7026–7091 | `__main__`: asyncio guard, `ensure_dirs`, `configure_hf_cache`, `app.queue`, dual `launch` |

**[MISSED] in your draft:** Gradio/Proactor patches → `ui/patches.py`;
ISO maps → `pipeline/langcodes.py`; segments helpers → `pipeline/segments.py`;
`zast_tooltip`+guides → `ui/guides.py`; `reset_project` 25-tuple →
`pipeline/reset.py`; `zastSwitchTab` map → `ui/assets/*.js`;
`INITIAL_VALID_LANGS` → `pipeline/languages.py`.

## New Scope (4 Tabs Only)

| Tab | Purpose | Keep? |
|-----|---------|-------|
| Tab 1: Import | YouTube URL / local file | ✅ |
| Tab 2: Transcription | WhisperX + Demucs | ✅ |
| Tab 3: Translation & SEO | LLM picker + translation (fitted + normal) + **YouTube SEO Studio (moved here Oct-2026)** | ✅ |
| Tab 4: Dubbing + Export | TTS voice synthesis + video render | ✅ |
| Tab 5: Bulk Mode | Multi-language batch | ❌ REMOVE |
| Tab 6: Viral Shorts | 9:16 auto-clipper + karaoke | ❌ REMOVE |
| Tab 7: SEO Blog Studio | Video → WordPress blog | ❌ REMOVE |
| Tab 8: Qwen Image Studio | AI thumbnail generation | ❌ REMOVE |
| Config CPS Tab | Voice speed calibration | ⚠️ MERGE into Tab 4 |
| Help tab | Usage docs | ⚠️ **[MISSED]** KEEP but shrink to Tabs 1–4 |

> **[MISSED] — Tab 3 is NOT translation-only.** Since Oct-2026 Tab 3 =
> `3. 🌍 Translation & SEO` (cards: 1 LLM Model → 2 Target Language →
> 3 Translation → 4 SEO Studio). The split MUST move `seo_assistant` calls
> with it. `whisper_only_cb` stays Tab 2; `skip_seo_cb` lives Tab 3.

---

## Architecture Goals

1. **Single-file app.py** - but smaller (~800-1000 lines max)
2. **Tab modules** - each tab in its own file, imported by app.py
3. **Clear state management** - single `AppState` class, no global sprawl
4. **Optional features via plugins** - extensibility without bloat
5. **[MISSED] Zero-behavior standalone PR** — mechanical move only, no renames/logic edits.
6. **[MISSED] Testable pipeline without Gradio** — `pipeline/*` return plain data; `ui/` adapters wrap `gr.update()`.

---

## Phase 1: Skeleton (this ticket)

### Target File Structure

```
ZastTranslate/
├── app.py                      # Main entry, 4 tabs, state management
├── config.py                   # Constants, lazy torch loading
├── modules/
│   ├── __init__.py
│   ├── downloader.py           # YouTube + local file import
│   ├── separator.py            # Demucs vocal separation
│   ├── transcriber.py          # WhisperX transcription
│   ├── reformulator.py         # LLM translation
│   ├── tts_backends/
│   │   ├── __init__.py
│   │   ├── factory.py
│   │   ├── voxcpm2.py
│   │   ├── edge_bg.py
│   │   ├── bgtts.py
│   │   ├── omnivoice.py, qwen3.py **[MISSED — exist today]**
│   │   └── bgtts_v6/ **[MISSED — exists today]**
│   ├── llm_backends/
│   │   ├── __init__.py
│   │   ├── factory.py
│   │   └── base/hybrid/llama_cpp/llama_server/local_causal **[MISSED — exist]**
│   ├── time_sync.py            # TTS timing + CPS
│   ├── audio_mixer.py          # Dubbed audio + original music mix
│   ├── video_assembler.py      # Render final MP4
│   ├── srt_parser.py           # Subtitle handling
│   ├── seo_assistant.py        # Tab 3 SEO **[MISSED — keep]**
│   └── utils.py                # Shared utilities
│   # DELETE with Tabs 5–8: shorts_generator, blog_generator,
│   # qwen_image_generator, youtube_publisher **[MISSED]**
├── ui/                         # [MISSED] NEW — all Gradio code
│   ├── patches.py              # Gradio-6 + Proactor patches (ex-lines 1–99)
│   ├── guides.py               # zast_tooltip + _get_tab_guide_html (tabs 1–4)
│   ├── assets/blocks.css       # ex-BLOCKS_CSS verbatim (~718 lines)
│   ├── assets/blocks.js        # ex-BLOCKS_JS verbatim (~460 lines)
│   ├── assets/upload_waits.js  # JS_DOWNLOAD/SRT/TRANSLATION_SRT_WAIT
│   └── tabs/tab_import|transcribe|translate|dub.py  # UI only
├── pipeline/                   # [MISSED] NEW — no `import gradio` at top
│   ├── state|langcodes|segments|import_|transcribe|seo|translate.py
│   └── voice_modes|dub|llm_select|cps|reset.py
├── fitted_cps_config.py        # KEEP at root (Tab 4 CPS merge reads it)
├── requirements.txt
└── temp/, output/, models/     # Existing dirs
```

### What to Extract from Current app.py

| Current Code | New Location |
|--------------|--------------|
| Tab 1 UI + `step0_check_url`, `step1_download` | `ui/tabs/tab_import.py` + `pipeline/import_.py` |
| Tab 2 UI (Whisper-only) + transcription logic | `ui/tabs/tab_transcribe.py` + `pipeline/transcribe.py` |
| Tab 3 UI (LLM + translation + SEO studio) + logic | `ui/tabs/tab_translate.py` + `pipeline/translate.py` + `pipeline/seo.py` + `pipeline/llm_select.py` |
| Tab 4 UI + dubbing + segment editor (+CPS) | `ui/tabs/tab_dub.py` + `pipeline/dub.py` + `pipeline/cps.py` |
| `AppState` class + user-config | `pipeline/state.py` |
| CPS config UI | Merge into Tab 4 settings |
| Voice mode helpers (`is_clone_requested`, etc.) | `pipeline/voice_modes.py` |
| 8-step subtitle stabilization | Keep in `modules/srt_cleaner.py` (already exists) |
| **[MISSED]** `BLOCKS_CSS` / `BLOCKS_JS` / `JS_*_WAIT` | `ui/assets/blocks.css` / `blocks.js` / `upload_waits.js` verbatim |
| **[MISSED]** patches / tooltip+guides / langcodes / segments / reset | `ui/patches.py` / `ui/guides.py` / `pipeline/langcodes.py` / `pipeline/segments.py` / `pipeline/reset.py` |
| **[MISSED]** Help tab | Shrink to Tabs 1–4, drop 5–8/CPS sections |

---

## Phase 2: Implementation

### Step 2.1: Create Tab Modules

Each tab module exports:
- `build_ui()` - returns Gradio components dict
- `handle_event()` - returns state updates

```python
# ui/tabs/tab_import.py example
import gradio as gr

def build_ui():
    with gr.Column():
        url_input = gr.Textbox(label="YouTube URL")
        file_input = gr.File(label="Or local video")
        # ...
    return {"url": url_input, "file": file_input, ...}

def handle_download(url, file, resolution, progress=gr.Progress()):
    # Thin adapter: logic lives in pipeline/import_.py (returns plain data)
    from pipeline.import_ import download
    return download(url, file, resolution, progress)
```

> **[MISSED] — handler rule:** `pipeline/*` MUST NOT return `gr.update()`.
> Plain dicts/tuples only; `ui/` converts. `progress=gr.Progress()` defaults
> stay in `ui` adapters, never in `pipeline/`.

### Step 2.2: Refactor State

```python
# pipeline/state.py
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class AppState:
    video_path: Optional[str] = None
    video_title: str = ""
    source_lang: str = "auto"
    segments: list = field(default_factory=list)  # transcription
    translated_segments: list = field(default_factory=list)
    target_lang: str = "fr"
    voice_mode: str = "default"
    # ...
```

> **[MISSED] — current AppState also has:** `video_info`, `synced_segments`,
> `temp_dir`, `keep_models`, `seo_package` (KEEP — Tab 3), `last_voice_tag`,
> plus DELETE `bulk_results`, `bulk_translated_segments`, `blog_package`,
> `detected_shorts`. Move verbatim first (minus deleted), dataclass-ify later.

### Step 2.3: Connect Tabs in app.py

```python
# app.py
from ui.patches import apply_patches  # must run before gradio side effects
apply_patches()
from pipeline.state import AppState
from ui.tabs.tab_import import build_ui as build_tab1
from ui.tabs.tab_transcribe import build_ui as build_tab2
# ...

state = AppState()

with gr.Blocks() as demo:
    gr.Tab("1. Import", build_tab1())
    gr.Tab("2. Transcription", build_tab2())
    gr.Tab("3. Translation & SEO", build_tab3())
    gr.Tab("4. Dubbing + Export", build_tab4())
```

> **[MISSED] — app.py wiring keeps exact order:** preview column → main_tabs
> → 4 tabs + slim Help → JS waits → all `.click/.change/.select` in today's
> order → `app.load(on_app_load)` → `__main__`. Target ~300–400 lines.

---

## Phase 3: Migration Checklist

- [ ] Copy `config.py` unchanged
- [ ] Copy `modules/downloader.py` unchanged
- [ ] Copy `modules/separator.py` unchanged
- [ ] Copy `modules/transcriber.py` unchanged
- [ ] Copy `modules/reformulator.py` unchanged
- [ ] Copy `modules/tts_backends/*` unchanged (incl. `omnivoice.py`, `qwen3.py`, `bgtts_v6/`)
- [ ] Copy `modules/llm_backends/*` unchanged (incl. `base/hybrid/llama_cpp/llama_server/local_causal`)
- [ ] Copy `modules/time_sync.py` unchanged
- [ ] Copy `modules/audio_mixer.py` unchanged
- [ ] Copy `modules/video_assembler.py` unchanged
- [ ] Copy `modules/srt_parser.py` unchanged
- [ ] Copy `modules/srt_cleaner.py` (8-step pipeline) unchanged
- [ ] Copy `modules/utils.py` unchanged
- [ ] Copy `modules/seo_assistant.py` unchanged (Tab 3 needs it) **[MISSED]**
- [ ] Keep `fitted_cps_config.py` at root (Tab 4 CPS merge reads it) **[MISSED]**
- [ ] DELETE imports of `shorts_generator`, `blog_generator`, `qwen_image_generator`, `youtube_publisher` **[MISSED]**
- [ ] Extract `BLOCKS_CSS` → `ui/assets/blocks.css`, `BLOCKS_JS` → `ui/assets/blocks.js`, `JS_*_WAIT` → `ui/assets/upload_waits.js` **[MISSED]**
- [ ] Extract patches/guides/langcodes/segments/reset → `ui/` + `pipeline/` **[MISSED]**
- [ ] Extract Tab 1 → `ui/tabs/tab_import.py` + `pipeline/import_.py`
- [ ] Extract Tab 2 → `ui/tabs/tab_transcribe.py` + `pipeline/transcribe.py`
- [ ] Extract Tab 3 (LLM + translation + SEO) → `ui/tabs/tab_translate.py` + `pipeline/translate.py` + `pipeline/seo.py` **[MISSED: SEO included]**
- [ ] Extract Tab 4 (+CPS) → `ui/tabs/tab_dub.py` + `pipeline/dub.py` + `pipeline/cps.py`
- [ ] Shrink Help tab to Tabs 1–4 **[MISSED]**
- [ ] Delete Tabs 5–8 + CPS tab + `step5_bulk_run`, `step6_publish_youtube`, `step7_*`, `step8_*`, flux/blog/shorts handlers **[MISSED]**
- [ ] Rewrite `app.py` to import and wire tabs (≤400 lines wiring + `__main__`)
- [ ] Verify: `py_compile` all files; no `import gradio` in `pipeline/*`; grep for deleted names is clean **[MISSED]**
- [ ] Test: Import YouTube video
- [ ] Test: Transcribe with WhisperX
- [ ] Test: Translate to French
- [ ] Test: SEO kit in Tab 3 **[MISSED]**
- [ ] Test: LLM switch (Custom + llama-server) in Tab 3 **[MISSED]**
- [ ] Test: Dub with default voice
- [ ] Test: Export MP4

---

## Removed Features (for reference)

If users need these later, they can be extracted to separate Pinokio apps:

| Removed | Future Home |
|---------|-------------|
| Bulk Mode (Tab 5) | `zast-bulk` standalone |
| Viral Shorts (Tab 6) | `zast-shorts` standalone |
| SEO Blog Studio (Tab 7) | `zast-blog` standalone |
| Qwen Image Studio (Tab 8) | `zast-images` standalone |
| CPS Config Tab | Merge into Tab 4 settings |

---

## Success Criteria

1. **app.py ≤ 1000 lines** (currently 6,632 — corrected 2026-10-16, was "~2000+")
2. **4 tabs only** - no more, no less (+ slim Help tab)
3. **Each tab module ≤ 200 lines** (UI-only; logic in `pipeline/*`, also ≤200 each)
4. **Clean imports** - no circular dependencies; `pipeline/*` imports without Gradio
5. **Works end-to-end**: YouTube URL → transcription → translation → dubbed MP4
6. **[MISSED] Zero behavior change**: same component IDs, event order, return-tuple shapes; side-by-side click-through.

---

## Why Rewrite vs. Edit

| Approach | Pros | Cons |
|----------|------|------|
| Edit existing app.py | Preserve working logic | Accumulated debt stays, complex conditional logic |
| Rewrite fresh | Clean slate, enforced simplicity | Time cost, risk of regression |

**Decision**: Rewrite. The current app.py has too many conditional branches (tabs 5-8). Starting fresh enforces the 4-tab constraint and gives us a chance to design for modularity from day one.

> **[MISSED] — CONTRADICTION with your ticket:** the ticket says *"mechanical
> but huge refactor; must be a standalone PR with zero behavior changes"*
> (i.e. **move, don't rewrite**), but this section decides **Rewrite**. Pick
> one before starting: **(A) Mechanical move (recommended)** — cut/paste
> verbatim into `ui/`+`pipeline/`, delete Tabs 5–8, identical behavior.
> **(B) Fresh rewrite** — from scratch, higher regression risk, needs E2E
> suite first. Do NOT mix A and B in one PR.

---

## Appendix R: Single Massive LLM Regeneration Prompt

> One self-contained prompt. Paste it into a fresh LLM session (optionally
> with current `app.py` as behavior ground truth) to regenerate the
> simplified 4-tab app. Plan-doc only — no code changed in this session.

```text
════════════════════════════════════════════════════════════════════
ZastTranslate — FULL REGENERATION PROMPT (simplified 4-tab rebuild)
════════════════════════════════════════════════════════════════════

You are regenerating ZastTranslate, an OFFLINE Windows desktop app for
video translation & dubbing (NO lip-sync). Stack (fixed, do not change):
Python 3.14, Gradio 6, yt-dlp, WhisperX (large-v3), Demucs, local LLMs
(Qwen3.5-9B / Qwen2.5-7B-Instruct / EuroLLM-9B / Custom GGUF / llama.cpp
server), TTS engines (VoxCPM 2, BgTTS-38M, Edge-TTS BG), FFmpeg,
PyTorch/CUDA. If you are given the current app.py (~6,632 lines) as a
reference, treat it as the GROUND TRUTH for behavior of kept features.

GOAL: a drastically simplified rebuild with exactly 4 main tabs + a slim
Help tab, split into ui/ + pipeline/ packages, app.py reduced to wiring
only. ZERO behavior change to anything you keep: same labels, choices,
defaults, elem_ids, event order, file formats, and return-tuple shapes.

──────────────────────────────────────────────────────────────────────
SECTION 1 — EXACT TARGET UI (4 tabs + slim Help, in order)
──────────────────────────────────────────────────────────────────────
Tab 1 "1. 📥 Import" (id=tab_import):
  - Quick-guide accordion, YouTube URL + 🔍 Check URL + resolution
    dropdown, local file upload (elem_id=local_file_input) + optional
    Video Title textbox, 🎙️ TTS Voice Synthesis Engine dropdown,
    📥 Import + 🔄 New Project buttons, status, "➡️ Continue to
    Transcription (Tab 2)" button.

Tab 2 "2. 🎤 Transcription" (id=tab_transcription):
  - Quick-guide accordion; card 1 "🎙️ 1. Whisper AI Speech Recognition":
    Source Language + Whisper Model dropdowns, ▶️ Run Transcription
    (elem_id=btn_run_transcription), 🎙️ "Whisper only" checkbox
    (whisper_only_cb); card 2 "📄 2. Subtitles Editor & SRT Import":
    SRT upload + Import SRT, status, interactive grid
    (elem_id=transcription_df), Validate Transcription / Clean Fillers /
    Export-Refresh SRT / Open Folder buttons, downloadable SRT file,
    "➡️ Continue to Translation (Tab 3)" button.
  - NO LLM picker and NO SEO studio here (they moved to Tab 3).

Tab 3 "3. 🌍 Translation & SEO" (id=tab_translation) — FOUR cards:
  1) "🧠 1. LLM Model (Translation / SEO)": llm_backend_dropdown +
     🚫 Skip YouTube SEO studio checkbox (skip_seo_cb); conditional
     custom_llm_row (manual model path + ✅ Use this model) +
     custom_llm_status; conditional llama_server_row (server URL +
     ✅ Use this URL) + llama_server_status; collapsible VRAM guide
     accordion (Qwen3.5-9B / Qwen2.5-7B / EuroLLM recommendations).
  2) "🌐 2. Target Language & Video Metadata": lang_target dropdown,
     ⬇️ Import from URL button, original title/desc + translated
     title/desc textboxes (with copy buttons).
  3) "🎯 3. Translation": ▶️ Run Translation
     (elem_id=btn_run_translation) + translated-SRT import +
     Import Translated SRT, status, editable grid
     (elem_id=translation_df), Validate Translation / Export Full /
     Export Fitted / Open Folder, downloadable file.
  4) "🚀 4. YouTube SEO & Metadata Studio" (group elem_id=
     seo_studio_group): hashtag-pack radio (4 packs), ✨ Generate
     Complete YouTube SEO Kit, title/tags/chapters/description outputs,
     📥 Apply as Original Video Metadata, seo_status.
     "➡️ Continue to Dubbing & Export (Tab 4)" button.

Tab 4 "4. 🎬 Dubbing & Export" (id=tab_dubbing):
  - Voice Mode radio (Default / Clone from original / Clone from file),
    default voice gender, voice sample file, 🔊 Never Cut Vocal +
    warning, dialogue grid (elem_id=dubbing_segments_df) + segment
    editor card (index/text/start-end/regenerate), "🎬 3. Master
    Assembly & Video Rendition": Subtitles Off/Soft/Burn radio, 🎬
    Assemble (elem_id=btn_assemble_video), video/audio players +
    download buttons/files, Open/Clear output folder.
  - PLUS merged CPS card (former ⚙️ CPS tab): CPS dataframe
    (Language/ISO/Default CPS/Your CPS) + 💾 Save + ↩️ Reset + status.

Help tab (id=tab_help): accordion per kept area only — Preview &
Subtitles, Tab 1–Tab 4, Troubleshooting, About/Credits. DELETE all
Tab 5/6/7/8/CPS help sections.

DELETE ENTIRELY: Tab 5 Bulk, Tab 6 Viral Shorts, Tab 7 Blog Studio,
Tab 8 Qwen Image Studio, CPS tab, and every related component,
handler, helper, import and module reference (bulk_*, shorts_*, blog_*,
flux_*, step5_bulk_run, step6_publish_youtube, step7_*, step8_*, the
btn_next_tab4_to_shorts/blog/thumb buttons, the "8."/"9." entries in
the zastSwitchTab keyword map).

──────────────────────────────────────────────────────────────────────
SECTION 2 — EXACT FILE STRUCTURE
──────────────────────────────────────────────────────────────────────
app.py                    # wiring only: patches first, preview column,
                          # main_tabs, JS waits, all event bindings in
                          # current order, app.load, __main__ launch
ui/patches.py             # Gradio-6 Dataframe None-guard + Windows
                          # Proactor _call_connection_lost patch
ui/guides.py              # zast_tooltip() + _get_tab_guide_html() for
                          # tabs 1–4 (+ Help strings)
ui/assets/blocks.css      # BLOCKS_CSS verbatim
ui/assets/blocks.js       # BLOCKS_JS verbatim (keep __ZAST_DEBUG_*__
                          # placeholders + debugLog stripping logic)
ui/assets/upload_waits.js # JS_DOWNLOAD_WAIT / JS_SRT_WAIT /
                          # JS_TRANSLATION_SRT_WAIT
ui/tabs/tab_import.py     # build_ui() → component dict (UI only)
ui/tabs/tab_transcribe.py # same contract
ui/tabs/tab_translate.py  # same contract (LLM + translation + SEO)
ui/tabs/tab_dub.py        # same contract (+ merged CPS card + Help)
pipeline/state.py         # AppState + user-config load/save +
                          # current/available backend resolution
pipeline/langcodes.py     # NLLB/ISO maps + _get_iso_code/_get_iso639_2
pipeline/segments.py      # segments JSON, find_segment_audio_path,
                          # dubbing-df builder, segment-editor core
pipeline/import_.py       # step0_check_url / step1_download core
pipeline/transcribe.py    # step2_transcribe, step2b_import_srt,
                          # step2_clean_transcription, step3_save
pipeline/seo.py           # step2_generate/change/apply_seo_metadata
pipeline/translate.py     # step4_translate, step3b_import, step5_save,
                          # get_valid_languages + INITIAL_VALID_LANGS
pipeline/voice_modes.py   # clone helpers, get_voice_mode_choices,
                          # update_voice_mode_ui, check_vram_warning
pipeline/dub.py           # step6_synthesize, step6_regenerate_segment,
                          # export/clear/open output
pipeline/llm_select.py    # LLM backend switch / custom path / llama
                          # server handlers (return plain dicts)
pipeline/cps.py           # CPS dataframe + load/save overrides
pipeline/reset.py         # reset_project + on_app_load (tuple shapes
                          # byte-identical to current app.load outputs)
config.py, fitted_cps_config.py, requirements.txt, modules/* KEPT.
modules/{shorts_generator,blog_generator,qwen_image_generator,
youtube_publisher}.py DELETED.

──────────────────────────────────────────────────────────────────────
SECTION 3 — HARD CONSTRAINTS
──────────────────────────────────────────────────────────────────────
1. Zero behavior change on kept paths: identical labels/choices/
   defaults/validation order; UTF-8-SIG .srt exports; 1080p MP4 mux
   with soft/hard subtitle modes; output filenames unchanged.
2. pipeline/* MUST NOT `import gradio` at module top level and MUST NOT
   return gr.update()/gr.HTML() — plain data only; ui/ adapters convert.
   progress=gr.Progress() defaults live only in ui/ adapter signatures.
3. Keep component elem_ids: main_app_tabs, right_tabs_col, video_player,
   local_file_input, srt_file_input, btn_run_transcription,
   transcription_df, btn_run_translation, translation_srt_input,
   translation_df, seo_studio_group, dubbing_segments_df,
   segments_json_holder, subtitle_selection, subtitle_overlay.
4. Keep event binding ORDER exactly as current app.py (reset, LLM
   change, transcription, SEO, translation, dubbing, app.load last).
5. AppState: keep video_info, segments, translated_segments,
   synced_segments, temp_dir, keep_models, seo_package, last_voice_tag;
   DROP bulk_results, bulk_translated_segments, blog_package,
   detected_shorts.
6. config.py lazy-torch discipline: no torch import, no makedirs at
   module import time; ensure_dirs()/configure_hf_cache() called from
   __main__ only.
7. Windows-only behavior preserved: Proactor 10054 patch, os.startfile
   folder opener, port-fallback app.launch, UTF-8 console reconfigure.
8. Gradio 6 + Python 3.14 APIs only (Dataframe headers/wrap/max_height,
   Textbox buttons=["copy"], Number precision, Radio/Checkbox info=).
9. No dangling references: after deleting Tabs 5–8, grep for
   bulk_|shorts_|blog_|flux_|youtube_publisher must return zero hits in
   app.py and ui/; every file must pass `python -m py_compile`.
10. No new dependencies, no version bumps, no behavior "improvements".

──────────────────────────────────────────────────────────────────────
SECTION 4 — VERIFICATION GATE (must all pass)
──────────────────────────────────────────────────────────────────────
V1. `python -m py_compile` on app.py, ui/**.py, pipeline/**.py.
V2. grep -rn "bulk_|shorts_|blog_|flux_|youtube_publisher" app.py ui/
    → zero matches (except this doc).
V3. grep -rn "import gradio" pipeline/ → zero matches.
V4. Side-by-side click-through on a test video: Import URL → Transcribe
    (large-v3) → validate → open Tab 3, switch LLM (incl. Custom path)
    → Translate (FR) → validate → Generate SEO Kit → Apply metadata →
    Tab 4, default voice → Assemble → MP4 plays with translated audio;
    SRT + fitted SRT downloads byte-compare identical to old build.
V5. `git diff --stat`: app.py shrinks (≤1,000 lines), only ui/ +
    pipeline/ added, modules/ unchanged except 4 deleted files.
════════════════════════════════════════════════════════════════════
```