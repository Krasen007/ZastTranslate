# TODO — Outstanding backlog

Outstanding items are listed below with their current status; completed entries
remain briefly documented for traceability.

## High priority

### 1. Split `app.py` (7,091 lines) into modules
- **Why deferred:** mechanical but huge refactor; must be a standalone PR with
  zero behavior changes so it can be reviewed/diffed in isolation.
- **Approach:** move each tab's handlers into `ui/tabs/*.py`, pipeline logic
  into `pipeline/*.py` (transcribe → translate → tts → assemble), keep
  `app.py` as wiring only. Inline CSS/JS (`BLOCKS_JS`/`BLOCKS_CSS`, ~1.5k
  lines) into `ui/assets/*.js|css`.
- **Payoff:** reviewable diffs, testable pipeline functions without importing
  Gradio, no more merge conflicts on a 7k-line file.

### 2. `AudioMixer` block-wise streaming (long-video OOM)
- **Why deferred:** needs real audio-engineering testing (crossfade/loop
  correctness on 1h+ tracks); the balance bug (renormalization) is already
  fixed in `993b781`.
- **Approach:** replace full-file `librosa.load` + `np.tile` with block-wise
  `soundfile` reads; loop the background on disk (ffmpeg `aloop`) instead of
  tiling in RAM; optional loudness-based ducking (voice −3 dB / BG −9 dB)
  instead of fixed gains.

### 3. `config.py` lazy initialization
- **Why deferred:** `import torch` + `os.makedirs` at module top are consumed
  at import time by all 35 modules; safe laziness needs a coordinated pass.
- **Approach:** `ensure_dirs()` called from `app.py` main; `DEVICE`/`GPU_*`
  behind a `get_device()` helper with cached property; keep module-level
  aliases for backward compatibility during transition.

## Medium priority

### 4. Wire subtitles into the final `assemble()` call
- `modules/video_assembler.py` supports `srt_path` / `hardsub` /
  `sub_language` (added in `993b781`), but the only caller (`app.py:1800`)
  passes video+audio only — the burn-in/softsub path is currently dead code.
- **Approach:** add a UI toggle (Off / Soft / Burn) on the Dubbing tab; pass
  the fitted SRT + target ISO language (drop the `fre` default once wired).


### 6. Dependency pinning for reproducible installs
- `requirements.txt` pins versions now, but `git+https://github.com/m-bain/whisperX.git`
  and `faster-whisper` remain floating relative to their upstream commits.
- **Approach:** pin whisperx to a commit SHA; produce a `uv.lock` /
  `requirements.lock` with hashes for release tags.

### 7. Stop mutating `HF_HOME` at import time
- `modules/tts_backends/factory.py` and `llm_backends/factory.py` set
  `HF_HOME` / `HUGGINGFACE_HUB_CACHE` when imported.
- **Approach:** move into the respective `load()` paths (or a single
  `config.configure_hf_cache()` called from `app.py` main).

### 8. Silent `except Exception: pass` audit
- Remaining in `downloader.py`, `separator.py`, app dataframe parsing, and
  `hybrid.py` path helpers.
- **Approach:** log at `WARNING` with file/segment context (one-line helper
  `_warn_ctx(msg, e)`), keep `pass` only for genuinely optional cleanup.

## Low priority / hygiene

### 9. CI workflow
- Add `.github/workflows/ci.yml` running `py_compile` over all sources +
  the fast test scripts (`test_separator_cache`, `test_srt_pipeline_fixes`,
  `test_srt_cleaner`, `test_translation_import`, `test_bg_audit`) with
  `PYTHONIOENCODING=utf-8`.
- GPU/network tests (`test_gpu_transcribe`, `test_qwen`, `test_generation`,
  live-server part of `test_llama_server_backend`) stay manual/nightly.


## Recently completed

- **Tab 2 "Whisper only"** — the checkbox used the legacy NLLB `Translator`
  for translation. It now skips translation entirely (original text mirrored
  into the translation columns, no LLM/NLLB loaded); see item 5.
- **Tab 4 "Clone from file" used the bundled default voice** — `time_sync.
  sync_all` / `sync_all_never_cut` never received the reference path
  (`voice_mapping=None`), so BgTTS resolved `sample_female_bg1.wav` for every
  segment. `voice_path` is now forwarded from Tab 4 and Bulk Mode, `voice_file`
  is normalized (str vs file object) with an existence check, and the BgTTS
  backend remembers the `load()`-time reference (like VoxCPM2's
  `default_ref_audio`) as a fallback.
