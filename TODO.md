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

### 2. `AudioMixer` block-wise streaming (long-video OOM) — DONE
- Replaced full-file `librosa.load` + `np.tile` with: ffmpeg normalize to
  44.1 kHz stereo on disk, ffmpeg `-stream_loop` background looping on disk,
  then block-wise `soundfile` streaming (~30 s blocks, ~10 MB RAM constant).
- Added optional `ducking=True` (extra BG attenuation where voice is active,
  attack/release smoothed) alongside the preserved default gains.
- Balance-bug fix (clip, don't renormalize) retained from `993b781`.

### 3. `config.py` lazy initialization — DONE
- `import torch` + `os.makedirs` no longer run at import time: torch loads
  lazily inside `get_device()`/`get_gpu_info()` (cached), dirs via
  `ensure_dirs()` called once from `app.py` main.
- `DEVICE`/`GPU_NAME`/`GPU_VRAM_GB`/`GPU_VRAM` kept as lazy `__getattr__`
  aliases (+ `__all__` so `from config import *` works); all in-repo
  `from config import DEVICE` call sites switched to `config.get_device()`
  (separator, transcriber, reformulator, qwen, all TTS/LLM backends,
  time_sync VRAM shield, app.py warning + help text, video_assembler).

## Medium priority

### 4. Wire subtitles into the final `assemble()` call — DONE
- Added Off / Soft / Burn `subtitle_mode` radio on the Dubbing tab; the
  single-dub `step6_synthesize` path now passes the fitted SRT
  (`output/fitted_{ISO}.srt`) + ISO 639-2 language (`_get_iso639_2`, new
  ISO-1→ISO-2 map) to `video_assembler.assemble()`.
- Default `sub_language` changed `fre` → `und` so unwired callers (bulk,
  regen) no longer mislabel tracks as French.
- Bulk + regen paths intentionally unchanged (no subtitle toggle there yet).

### 6. Dependency pinning for reproducible installs — PARTIAL
- `whisperx` now pinned to v3.2.0 tag SHA `7307306a...` in requirements.txt
  (was floating `git+...whisperX.git` master, which moved to
  torchcodec>=0.6,<0.8 and broke resolution against our torchcodec==0.10.0).
  `faster-whisper==1.2.1` was already pinned.
- No `requirements.lock` with hashes: `uv pip compile` cannot build the
  whisperx v3.2.0 sdist (setuptools build needs git metadata), so a hash
  lock is not generatable in this env. Revisit with a wheel mirror or
  `--no-build` lock at release time.



## Low priority / hygiene

### 9. CI workflow
- Add `.github/workflows/ci.yml` running `py_compile` over all sources +
  the fast test scripts (`test_separator_cache`, `test_srt_pipeline_fixes`,
  `test_srt_cleaner`, `test_translation_import`, `test_bg_audit`) with
  `PYTHONIOENCODING=utf-8`.
- GPU/network tests (`test_gpu_transcribe`, `test_qwen`, `test_generation`,
  live-server part of `test_llama_server_backend`) stay manual/nightly.


## Recently completed
### 7. Stop mutating `HF_HOME` at import time — DONE
- `modules/tts_backends/factory.py` and `llm_backends/factory.py` previously set
  `HF_HOME` / `HUGGINGFACE_HUB_CACHE` at import time / in `get_backend()`.
- **Fix:** added `config.configure_hf_cache()` in `config.py` that sets both
  vars and creates `~/.zasttranslate/models/{hub,...}` once; called from
  `app.py` main before `app.launch()`. Removed all mutations from both factory
  files so imports no longer have side effects on the process environment.