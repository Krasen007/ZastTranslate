---
license: apache-2.0
language:
  - bg
  - en
pipeline_tag: text-to-speech
tags:
  - tts
  - bulgarian
  - miocodec
  - encoder-decoder
  - voice-cloning
  - speech-synthesis
library_name: pytorch
---

# BgTTS-38M V2 — Bulgarian Text-to-Speech with Voice Cloning

A lightweight **38M parameter** encoder-decoder TTS model for **Bulgarian and English** speech synthesis with **zero-shot voice cloning** via [MioCodec](https://huggingface.co/Aratako/MioCodec-25Hz-24kHz).

**V2 improvements over V1:**
- **Speaker normalization** — stable voice quality across all reference audio files
- **Larger training dataset** — 1,537 hours (vs 1,172h in V1)
- **BF16 training** — more stable gradients, no GradScaler needed
- **Zero dropout** — better utilization of model capacity
- **20 epochs** with careful LR scheduling

## Audio Samples

### Female Voice (Bulgarian)

<audio controls src="https://huggingface.co/beleata74/BgTTS-38M-V2/resolve/main/samples/sample_female_bg1.wav"></audio>

### Female Voice (English)

<audio controls src="https://huggingface.co/beleata74/BgTTS-38M-V2/resolve/main/samples/sample_female_en1.wav"></audio>

### Male Voice 1 (Bulgarian)

<audio controls src="https://huggingface.co/beleata74/BgTTS-38M-V2/resolve/main/samples/sample_male_bg1.wav"></audio>

### Male Voice 1 (English)

<audio controls src="https://huggingface.co/beleata74/BgTTS-38M-V2/resolve/main/samples/sample_male_en1.wav"></audio>

### Male Voice 2 (Bulgarian)

<audio controls src="https://huggingface.co/beleata74/BgTTS-38M-V2/resolve/main/samples/sample_male2_bg1.wav"></audio>

### Male Voice 2 (English)

<audio controls src="https://huggingface.co/beleata74/BgTTS-38M-V2/resolve/main/samples/sample_male2_en1.wav"></audio>

## Key Features

- **Bilingual**: Native Bulgarian + English in a single model
- **Voice cloning**: Zero-shot — just provide 3-10 seconds of reference audio
- **Tiny footprint**: 146 MB inference checkpoint, runs on CPU
- **Fast**: RTF ~0.3 on both GPU and CPU (3.3× faster than real-time)
- **Speaker-stable**: V2's normalized speaker embedding ensures consistent quality regardless of reference audio

## 🎙️ Voice Cloning

This model supports zero-shot voice cloning — it can generate speech in any voice given just a short reference audio clip. No fine-tuning needed.

### How it Works

1. Provide a reference audio (3-10 seconds of clear speech, WAV format, ideally 24kHz)
2. MioCodec extracts a 128-dimensional speaker embedding (`global_embedding`)
3. The embedding is **L2-normalized** and scaled by a learned parameter (`spk_scale`) before being added to the decoder
4. The same embedding is used for MioCodec waveform reconstruction

### V2 Improvement: Speaker Normalization

In V1, the speaker embedding had 7× larger norm than content tokens, causing the model to over-rely on the reference audio for pronunciation quality. V2 normalizes the speaker vector to unit norm, ensuring:
- **Consistent quality** across all reference voices
- The model learns speech patterns from data, not from speaker shortcuts
- Reference audio only affects **timbre**, not articulation

## Model Architecture

| Component | Details |
|---|---|
| Text Encoder | 4-layer bidirectional Transformer (d=384, 6 heads, ff=1536) |
| Audio Decoder | 8-layer causal Transformer (d=384, 6 heads, ff=1536) with cross-attention |
| Speaker Injection | L2-normalized Linear(128 → 384) with learned scale, additive bias |
| Audio Codec | [MioCodec](https://huggingface.co/Aratako/MioCodec-25Hz-24kHz) 25Hz, 1 codebook, 12800 codes, 24kHz output |
| Total Parameters | 38.2M (Encoder: 9.6M, Decoder: 28.6M) |
| Activations | SwiGLU |
| Normalization | RMSNorm (pre-norm) |
| Positional Encoding | Learned (encoder), RoPE (decoder) |
| Embeddings | Tied decoder (lm_head = token_embedding) |
| KV-Cache | Yes (for fast autoregressive inference) |

### Tokenizer

Character-level tokenizer supporting 146 characters:
- Bulgarian Cyrillic (А-Я, а-я)
- English Latin (A-Z, a-z)
- Digits, punctuation, whitespace

Total vocabulary: **12,955 tokens** (9 special + 146 text + 12,800 audio codes)

## Training

| Parameter | Value |
|---|---|
| **Data** | 728K samples, **1,537 hours** total |
| Bulgarian | ~620K samples (~1,368 hours) |
| English | ~108K samples (~169 hours) |
| **Epochs** | 20 |
| **LR Schedule** | Cosine decay, peak 7e-5, warmup 2 epochs, min 5e-6 |
| **Batch Size** | 64 |
| **Optimizer** | AdamW (betas=0.9, 0.999), weight decay 0.01 |
| **Precision** | BF16 (no GradScaler) |
| **Dropout** | 0.0 (unnecessary — model is 38M, data is 1,537h) |
| **Final Loss** | 5.04 |
| **Hardware** | NVIDIA RTX 5090 (32GB VRAM) |

### Why Zero Dropout?

With only 38M parameters and 138M audio tokens (1,537 hours), the model has **0.28 parameters per token**. Overfitting is mathematically impossible — the model is severely underfitting the data. Dropout only slows convergence without providing any regularization benefit.

## Quick Start

### Requirements

```bash
pip install torch torchaudio soundfile miocodec
```

### Python API

```python
import torch
from model import load_for_inference
from tokenizer import TTSTokenizer
from codec import CodecV6
from inference import generate

device = "cuda"  # or "cpu"

# Load model
model = load_for_inference("checkpoint_inference.pt", device=device)
tokenizer = TTSTokenizer()
codec = CodecV6(device=device)

# Get speaker embedding from reference audio
ref = codec.encode("reference_speaker.wav")
speaker_emb = ref["global_embedding"].to(device)

# Generate
codes = generate(
    model, tokenizer,
    text="Здравейте, как сте днес?",
    speaker_emb=speaker_emb,
    temperature=0.3,
    top_k=250,
    max_new_tokens=512,
    device=device,
)

# Decode to audio
if codes is not None:
    wav = codec.tokens_to_wav(codes, speaker_emb, "output.wav")
```

### CLI

```bash
python inference.py \
  --checkpoint checkpoint_inference.pt \
  --text "Здравейте, как сте днес?" \
  --speaker-wav reference.wav \
  --output output.wav \
  --temperature 0.3
```

### Web UI (Gradio)

```bash
python server.py
# Opens at http://localhost:7860
```

### Parameters

| Parameter | Default | Description |
|---|---|---|
| `--temperature` | 0.3 | Sampling temperature (lower = stable, higher = expressive) |
| `--top-k` | 250 | Top-k filtering |
| `--top-p` | 0.95 | Nucleus sampling threshold |
| `--rep-penalty` | 1.1 | Repetition penalty on recent tokens |
| `--max-tokens` | 512 | Maximum decoder steps (~20 seconds) |

**Recommended temperature: 0.3** for clean, stable output. Use 0.5-0.7 for more expressive/varied speech.

## ⚠️ Important: Sentence Length

> The encoder supports up to **256 characters** (~18 seconds of audio). For longer texts, `inference.py` automatically splits by sentence boundaries and concatenates the audio. No manual splitting needed.

## Files

```
checkpoint_inference.pt   # Model weights only (146 MB)
checkpoint.pt             # Full checkpoint with optimizer state (438 MB, for continued training)
config.py                 # Model configuration
model.py                  # Architecture (TTSEncoderDecoder + speaker normalization)
tokenizer.py              # Character-level tokenizer
codec.py                  # MioCodec wrapper
inference.py              # Inference pipeline with KV-cache + sentence splitting
train.py                  # Training script (BF16)
server.py                 # Gradio web UI
samples/                  # Audio samples (3 voices × 2 languages × 3 texts)
```

## Performance

### Benchmarks

| Hardware | RTF | Speed | Notes |
|---|---|---|---|
| **Intel i3-9100F (CPU)** | **0.30** | **3.3× real-time** | **Windows 10, CPU-only, no GPU** |

### CPU-only Deployment (Tested on Windows 10)

| Component | Disk Space |
|---|---|
| Python venv (PyTorch CPU + deps) | 654 MB |
| BgTTS-38M-V2 (checkpoint + code) | 146 MB |
| MioCodec (auto-downloaded, cached) | 499 MB |
| WavLM base+ (auto-downloaded, cached) | 872 MB |
| **Total** | **2.12 GB** |

No NVIDIA GPU, no CUDA, no special drivers needed. Works on any x86-64 machine with Python 3.8+.

## Comparison with Other Models

| Model | Parameters | Size | Languages | Voice Cloning | Open Source |
|---|---|---|---|---|---|
| **BgTTS-38M V2** | **38M** | **146 MB** | BG + EN | ✅ | ✅ |
| Kokoro-82M | 82M | ~200 MB | Multi | ❌ | ✅ |
| XTTS-v2 | ~467M | ~1.8 GB | 16 | ✅ | ✅ |
| CSM-1B | 1B | ~4 GB | EN | ✅ | ✅ |
| Dia-1.6B | 1.6B | ~6.4 GB | EN | ✅ | ✅ |

BgTTS-38M V2 is the **smallest TTS model with voice cloning** we are aware of, and the **only** open-source TTS model with native Bulgarian language support.

## Limitations

- Best with sentences up to ~18 seconds. Longer texts are auto-split by `inference.py`.
- Bulgarian quality is superior to English (82% of training data is Bulgarian).
- Voice cloning quality depends on reference audio clarity — use clean recordings without background noise.
- No explicit prosody control (pitch, speed) — these are implicitly learned from data.
- Character-level tokenizer may struggle with rare Unicode characters outside the supported set.

## License

Apache 2.0

## Citation

```bibtex
@misc{bgtts38mv2,
  title={BgTTS-38M V2: Bulgarian Text-to-Speech with Voice Cloning and Speaker Normalization},
  author={beleata74},
  year={2026},
  url={https://huggingface.co/beleata74/BgTTS-38M-V2}
}
```
