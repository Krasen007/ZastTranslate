# ZastTranslate Refactor Plan

## New Scope (4 Tabs Only)

| Tab | Purpose | Keep? |
|-----|---------|-------|
| Tab 1: Import | YouTube URL / local file | ✅ |
| Tab 2: Transcription | WhisperX + Demucs | ✅ |
| Tab 3: Translation | LLM translation (fitted + normal) | ✅ |
| Tab 4: Dubbing + Export | TTS voice synthesis + video render | ✅ |
| Tab 5: Bulk Mode | Multi-language batch | ❌ REMOVE |
| Tab 6: Viral Shorts | 9:16 auto-clipper + karaoke | ❌ REMOVE |
| Tab 7: SEO Blog Studio | Video → WordPress blog | ❌ REMOVE |
| Tab 8: Qwen Image Studio | AI thumbnail generation | ❌ REMOVE |
| Config CPS Tab | Voice speed calibration | ⚠️ MERGE into Tab 4 |

---

## Architecture Goals

1. **Single-file app.py** - but smaller (~800-1000 lines max)
2. **Tab modules** - each tab in its own file, imported by app.py
3. **Clear state management** - single `AppState` class, no global sprawl
4. **Optional features via plugins** - extensibility without bloat

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
│   │   └── bgtts.py
│   ├── llm_backends/
│   │   ├── __init__.py
│   │   ├── factory.py
│   │   └── ...
│   ├── time_sync.py            # TTS timing + CPS
│   ├── audio_mixer.py          # Dubbed audio + original music mix
│   ├── video_assembler.py      # Render final MP4
│   ├── srt_parser.py           # Subtitle handling
│   └── utils.py                # Shared utilities
├── requirements.txt
└── temp/, output/, models/     # Existing dirs
```

### What to Extract from Current app.py

| Current Code | New Location |
|--------------|--------------|
| Tab 1 UI + `step0_check_url`, `step1_download` | `modules/tab_import.py` |
| Tab 2 UI + transcription logic | `modules/tab_transcribe.py` |
| Tab 3 UI + translation logic | `modules/tab_translate.py` |
| Tab 4 UI + dubbing + segment editor | `modules/tab_dub.py` |
| `AppState` class | `modules/state.py` |
| CPS config UI | Merge into Tab 4 settings |
| Voice mode helpers (`is_clone_requested`, etc.) | `modules/voice_helpers.py` |
| 8-step subtitle stabilization | Keep in `modules/srt_cleaner.py` (already exists) |

---

## Phase 2: Implementation

### Step 2.1: Create Tab Modules

Each tab module exports:
- `build_ui()` - returns Gradio components dict
- `handle_event()` - returns state updates

```python
# modules/tab_import.py example
import gradio as gr

def build_ui():
    with gr.Column():
        url_input = gr.Textbox(label="YouTube URL")
        file_input = gr.File(label="Or local video")
        # ...
    return {"url": url_input, "file": file_input, ...}

def handle_download(url, file, resolution, progress=gr.Progress()):
    # Download logic
    return state_update_dict
```

### Step 2.2: Refactor State

```python
# modules/state.py
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

### Step 2.3: Connect Tabs in app.py

```python
# app.py
from modules.state import AppState
from modules.tab_import import build_ui as build_tab1
from modules.tab_transcribe import build_ui as build_tab2
# ...

state = AppState()

with gr.Blocks() as demo:
    gr.Tab("1. Import", build_tab1())
    gr.Tab("2. Transcription", build_tab2())
    gr.Tab("3. Translation", build_tab3())
    gr.Tab("4. Dubbing + Export", build_tab4())
```

---

## Phase 3: Migration Checklist

- [ ] Copy `config.py` unchanged
- [ ] Copy `modules/downloader.py` unchanged
- [ ] Copy `modules/separator.py` unchanged
- [ ] Copy `modules/transcriber.py` unchanged
- [ ] Copy `modules/reformulator.py` unchanged
- [ ] Copy `modules/tts_backends/*` unchanged
- [ ] Copy `modules/llm_backends/*` unchanged
- [ ] Copy `modules/time_sync.py` unchanged
- [ ] Copy `modules/audio_mixer.py` unchanged
- [ ] Copy `modules/video_assembler.py` unchanged
- [ ] Copy `modules/srt_parser.py` unchanged
- [ ] Copy `modules/srt_cleaner.py` (8-step pipeline) unchanged
- [ ] Copy `modules/utils.py` unchanged
- [ ] Extract Tab 1 → `modules/tab_import.py`
- [ ] Extract Tab 2 → `modules/tab_transcribe.py`
- [ ] Extract Tab 3 → `modules/tab_translate.py`
- [ ] Extract Tab 4 → `modules/tab_dub.py`
- [ ] Rewrite `app.py` to import and wire tabs
- [ ] Test: Import YouTube video
- [ ] Test: Transcribe with WhisperX
- [ ] Test: Translate to French
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

1. **app.py ≤ 1000 lines** (currently ~2000+)
2. **4 tabs only** - no more, no less
3. **Each tab module ≤ 200 lines**
4. **Clean imports** - no circular dependencies
5. **Works end-to-end**: YouTube URL → transcription → translation → dubbed MP4

---

## Why Rewrite vs. Edit

| Approach | Pros | Cons |
|----------|------|------|
| Edit existing app.py | Preserve working logic | Accumulated debt stays, complex conditional logic |
| Rewrite fresh | Clean slate, enforced simplicity | Time cost, risk of regression |

**Decision**: Rewrite. The current app.py has too many conditional branches (tabs 5-8). Starting fresh enforces the 4-tab constraint and gives us a chance to design for modularity from day one.