# TODO — Outstanding backlog

Items deliberately **not** included in the CodeRabbit full-review fix commit
(`993b781`) because they need their own focused pass. Each entry lists *why* it
was deferred and the suggested approach.

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

### 5. Decide the fate of the NLLB `Translator`
- It is legacy (the LLM reformulator is the active path) but still loads with
  the default import chain.
- **Approach:** either delete it, or gate it behind an explicit
  "Fallback: NLLB" option with lazy import so `transformers` Seq2Seq code
  isn't reachable by accident.

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

### 10. Remote EJS solver policy
- `yt-dlp` still fetches `ejs:github` by default (`ZAST_NO_REMOTE_EJS=1`
  disables it). **Approach:** vendor/pin it once yt-dlp supports version
  pinning, then flip the default to off.

### 11. `start.js` auto-updates `yt-dlp` on every launch
- Unpinned `uv pip install -U yt-dlp` — a bad nightly can break downloads
  mid-release. **Approach:** pin a known-good floor or move the update behind
  an explicit "Update yt-dlp" menu entry.

### 12. Remove / gate the JS debug log
- `app.py` embeds `js_debug_log` + `[ZastDebug]` console logging (hidden in
  production CSS but still executing). **Approach:** gate behind
  `ZAST_DEBUG=1` and strip from `BLOCKS_JS` otherwise.

### 13. `voxcpm` import failure in this environment
- `check_env.py` reports `[FAIL] voxcpm not installed` (pre-existing env
  state, unrelated to the review fixes — VoxCPM 2 correctly disappears from
  the TTS engine list). **Approach:** re-run the install.js step 3
  (`uv pip install "voxcpm>=2.0.2"`) or confirm the package was renamed.

### 14. Windows console encoding in test scripts
- The test scripts crash under `cp1252`/`cp1251` consoles; they only pass
  with `PYTHONIOENCODING=utf-8`. **Approach:** add the UTF-8 `reconfigure`
  preamble (as `app.py` does) to every `test_*.py`.
