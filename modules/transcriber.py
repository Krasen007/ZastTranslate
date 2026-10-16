import os
import gc

# --- Compat: expose CUDA 12 runtime DLLs to CTranslate2 (faster-whisper) ------
# The ctranslate2 wheel is built against CUDA 12 and lazily LoadLibrary()s
# cublas64_12.dll (+ cuDNN 9 companion DLLs) at the first GPU operation. torch
# 2.10 ships CUDA 13 only (cublas64_13.dll in torch\lib) and no CUDA 12 toolkit
# is installed, so the first model.encode() fails with
# "RuntimeError: Library cublas64_12.dll is not found or cannot be loaded".
# The pip packages nvidia-cublas-cu12 / nvidia-cudnn-cu12 provide those DLLs
# under site-packages/nvidia/*/bin; LoadLibrary searches PATH, so expose the
# dirs via PATH (and os.add_dll_directory) before whisperx touches the GPU.
def _expose_cuda12_dll_dirs():
    if os.name != "nt":
        return
    dll_dirs = []
    try:
        import site
        for site_dir in list(site.getsitepackages()) + [site.getusersitepackages()]:
            for sub in (
                os.path.join("nvidia", "cublas", "bin"),
                os.path.join("nvidia", "cudnn", "bin"),
                os.path.join("nvidia", "cuda_runtime", "bin"),
            ):
                candidate = os.path.join(site_dir, sub)
                if os.path.isdir(candidate) and candidate not in dll_dirs:
                    dll_dirs.append(candidate)
    except Exception:
        pass
    if not dll_dirs:
        print(
            "[transcriber] CUDA 12 DLL dirs not found. If GPU transcription fails with a "
            "missing cublas64_12.dll error, run: pip install nvidia-cublas-cu12 nvidia-cudnn-cu12"
        )
        return
    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    missing = [d for d in dll_dirs if d not in path_entries]
    if missing:
        os.environ["PATH"] = os.pathsep.join(missing + path_entries)
    if hasattr(os, "add_dll_directory"):
        for d in dll_dirs:
            try:
                os.add_dll_directory(d)
            except Exception:
                pass

_expose_cuda12_dll_dirs()
# --- end compat ---------------------------------------------------------------

import whisperx
import torch
from modules.utils import cleanup_model
import config

# --- Compat: whisperx 3.2.x VAD vs pyannote.audio 4.x ------------------------
# whisperx's VoiceActivitySegmentation.__init__ declares `use_auth_token` as an
# explicit parameter (default None) and ALWAYS forwards it to pyannote's
# VoiceActivityDetection.__init__. In pyannote.audio>=4 that kwarg no longer
# exists: it falls through **inference_kwargs into Inference(...) ->
# TypeError: Inference.__init__() got an unexpected keyword argument
# 'use_auth_token'. Popping it from the caller's kwargs is NOT enough (whisperx
# re-injects it via its own default), so the patched subclass bypasses
# whisperx's __init__ and calls pyannote's directly, mapping the legacy kwarg
# to pyannote 4.x's `token`.
# No site-packages edits; applied once at import.
try:
    from whisperx import vad as _whisperx_vad
    from pyannote.audio.pipelines.voice_activity_detection import (
        VoiceActivityDetection as _PyannoteVAD,
    )

    _OrigVAS = _whisperx_vad.VoiceActivitySegmentation

    class _PatchedVAS(_OrigVAS):
        def __init__(self, *args, **kwargs):
            # `use_auth_token` is whisperx's 3rd positional arg; strip it there too
            if len(args) >= 3:
                args = args[:2] + args[3:]
            use_auth_token = kwargs.pop("use_auth_token", None)
            # pyannote.audio>=4 renamed the HF auth kwarg to `token`
            kwargs.setdefault("token", use_auth_token)
            _PyannoteVAD.__init__(self, *args, **kwargs)

    _whisperx_vad.VoiceActivitySegmentation = _PatchedVAS
except Exception as _e:
    print(f"[transcriber] VAD compat patch skipped: {_e}")
# --- end compat ---------------------------------------------------------------


MIN_CUE_WORDS = 3
MIN_CUE_DURATION_MS = 400

def split_segment(seg, max_duration=9.0, max_chars=110, min_words=MIN_CUE_WORDS, min_duration_ms=MIN_CUE_DURATION_MS):
    """
    Split a single segment into smaller cues if it exceeds max_duration or max_chars.
    Uses word-level timestamps from WhisperX to strictly avoid orphans and broken sentence cuts:
    - Never split mid-phrase without punctuation unless exceeding max_duration.
    - No cue with fewer than min_words (3) words unless the entire segment is that short.
    - Trailing words under threshold (even if ending with a period like 'caisse.') are merged into the previous cue.
    """
    duration = seg.get('end', 0.0) - seg.get('start', 0.0)
    text = seg.get('text', '')
    min_duration = min_duration_ms / 1000.0
    
    if duration <= max_duration and len(text) <= max_chars:
        return [seg]
        
    words = seg.get('words', [])
    if words:
        # 1. Fill missing word-level timestamps via interpolation
        seg_start = float(seg.get('start', 0.0))
        seg_end = float(seg.get('end', seg_start + 1.0))
        
        # Ensure words have start and end
        for idx, w in enumerate(words):
            if 'start' not in w or w['start'] is None:
                w['start'] = seg_start if idx == 0 else words[idx-1].get('end', seg_start)
            if 'end' not in w or w['end'] is None:
                w['end'] = w['start'] + 0.1
            if w['end'] < w['start']:
                w['end'] = w['start'] + 0.1

        sub_segs = []
        curr_words = []
        
        def build_sub_seg(word_list):
            if not word_list:
                return None
            sub_start = word_list[0]['start']
            sub_end = word_list[-1]['end']
            sub_text = " ".join([w.get('word', '').strip() for w in word_list if w.get('word', '').strip()])
            return {
                'start': round(sub_start, 3),
                'end': round(sub_end, 3),
                'text': sub_text,
                'words': list(word_list)
            }
            
        strong_puncts = ('.', '?', '!', ';', '…')
        weak_puncts = (',', ':', '-')
        
        for i, w in enumerate(words):
            curr_words.append(w)
            
            word_text = w.get('word', '').strip()
            has_strong_punct = any(word_text.endswith(p) for p in strong_puncts)
            has_weak_punct = any(word_text.endswith(p) for p in weak_puncts)
            
            curr_dur = curr_words[-1]['end'] - curr_words[0]['start']
            curr_len = sum(len(cw.get('word', '')) for cw in curr_words) + (len(curr_words) - 1)
            
            is_last = (i == len(words) - 1)
            if is_last:
                break
                
            rem_words = words[i+1:]
            rem_count = len(rem_words)
            rem_dur = rem_words[-1]['end'] - rem_words[0]['start']
            rem_last_text = rem_words[-1].get('word', '').strip()
            rem_ends_strong = any(rem_last_text.endswith(p) for p in strong_puncts)
            
            # Check if splitting here would leave an invalid orphan remainder (< 3 words)
            rem_is_too_short = (rem_count < min_words or rem_dur < min_duration)
            
            should_split = False
            if not rem_is_too_short:
                if has_strong_punct and curr_dur >= 2.5:
                    should_split = True
                elif has_weak_punct and curr_dur >= 4.0:
                    should_split = True
                elif curr_dur >= max_duration:
                    should_split = True
                elif curr_len >= max_chars and (has_strong_punct or has_weak_punct):
                    should_split = True
                    
            if should_split:
                sub_seg = build_sub_seg(curr_words)
                if sub_seg:
                    sub_segs.append(sub_seg)
                curr_words = []
                
        # Handle remaining words
        if curr_words:
            curr_dur = curr_words[-1]['end'] - curr_words[0]['start']
            is_orphan = (len(curr_words) < min_words or curr_dur < min_duration)
            
            if is_orphan and sub_segs:
                # Merge into the previous sub-segment instead of expelling a 1-2 word orphan
                prev = sub_segs[-1]
                prev['words'].extend(curr_words)
                prev['end'] = round(curr_words[-1]['end'], 3)
                prev['text'] = " ".join([w.get('word', '').strip() for w in prev['words'] if w.get('word', '').strip()])
            else:
                sub_seg = build_sub_seg(curr_words)
                if sub_seg:
                    sub_segs.append(sub_seg)
                    
        # Adjust contiguous segment boundaries to prevent overlaps
        for idx in range(len(sub_segs) - 1):
            if sub_segs[idx]['end'] > sub_segs[idx+1]['start']:
                # Set previous end strictly at or before next start
                sub_segs[idx]['end'] = round(max(sub_segs[idx]['start'] + 0.1, sub_segs[idx+1]['start']), 3)
                
        return sub_segs if sub_segs else [seg]
        
    else:
        # Fallback: split by text length proportionally without word timestamps
        tokens = text.split()
        if not tokens:
            return [seg]
            
        chunks = []
        curr_chunk = []
        curr_len = 0
        for t in tokens:
            if curr_len + len(t) + 1 > max_chars and curr_chunk:
                chunks.append(" ".join(curr_chunk))
                curr_chunk = [t]
                curr_len = len(t)
            else:
                curr_chunk.append(t)
                curr_len += len(t) + 1
        if curr_chunk:
            # Check if last chunk is too small (< min_words), merge with previous chunk if so
            if len(curr_chunk) < min_words and chunks:
                chunks[-1] += " " + " ".join(curr_chunk)
            else:
                chunks.append(" ".join(curr_chunk))
            
        total_chars = sum(len(c) for c in chunks)
        if total_chars == 0:
            return [seg]
            
        sub_segs = []
        start_time = seg.get('start', 0.0)
        seg_duration = max(0.1, seg.get('end', 0.0) - start_time)
        
        for c in chunks:
            chunk_dur = (len(c) / total_chars) * seg_duration
            end_time = start_time + chunk_dur
            sub_segs.append({
                'start': round(start_time, 3),
                'end': round(end_time, 3),
                'text': c
            })
            start_time = end_time
            
        return sub_segs

def split_long_segments(segments, max_duration=8.0, max_chars=75, min_words=MIN_CUE_WORDS, min_duration_ms=MIN_CUE_DURATION_MS):
    """Split all segments in the list that exceed threshold duration or character count."""
    new_segments = []
    for seg in segments:
        new_segments.extend(split_segment(seg, max_duration, max_chars, min_words, min_duration_ms))
    return new_segments

DEFAULT_INITIAL_PROMPT = (
    "ZastTranslate, Pinokio, WhisperX, large-v3, Qwen, Qwen3.5-9B, VoxCPM 2, "
    "FLUX.1-schnell, Fitted, Bulk Mode, Translation et Dubbing, Blog Studio, "
    "Clean Fillers, 16:9, 9:16, longues traînes, tics IA, Demucs, pyannote, Ollama, vLLM, "
    "Claude, Claude.ai, ChatGPT, OpenAI, GPT-4, DeepSeek, PyTorch, LoRA, ElevenLabs, "
    "Kokoro, F5-TTS, CosyVoice, Midjourney, Stable Diffusion, ComfyUI, Hugging Face, "
    "FFmpeg, VRAM, CUDA, GGUF, Python, GitHub, YouTube, Gradio, LLM, IA, API, SRT, VTT, TTS, "
    "WordPress, TikTok."
)

def merge_orphan_punctuation_segments(segments):
    """
    Merge segments that consist only of punctuation (e.g. '?', '.', '!', '...', ',', ';', ':')
    into the previous segment, eliminating isolated single-punctuation subtitle lines.
    """
    if not segments:
        return segments

    import re
    cleaned = []
    for seg in segments:
        text = seg.get("text", "").strip()
        is_only_punct = bool(re.match(r"^[\s\.\?\!\,\;\:\-\–\—\…\¿\¡\'\"\(\)\[\]\«\»]+$", text))
        
        if is_only_punct:
            if cleaned:
                prev = cleaned[-1]
                prev_text = prev.get("text", "").rstrip()
                # In French/English typography, handle spacing before punctuation
                if text in ("?", "!", ";", ":"):
                    if not prev_text.endswith(text):
                        prev["text"] = f"{prev_text} {text}"
                else:
                    if not prev_text.endswith(text):
                        prev["text"] = f"{prev_text}{text}"
                # Extend previous segment end timestamp
                if seg.get("end") and seg.get("end") > prev.get("end", 0.0):
                    prev["end"] = seg["end"]
                # Also merge words if present
                if "words" in prev and "words" in seg:
                    prev["words"] = prev.get("words", []) + seg.get("words", [])
            else:
                continue
        else:
            cleaned.append(seg)
            
    return cleaned

class Transcriber:
    def __init__(self, model_size="large-v3", compute_type="float16"):
        self.model_size = model_size
        self.compute_type = compute_type if config.get_device() == "cuda" else "int8"
        self.device = config.get_device()

    @staticmethod
    def _compat_asr_options(model_size, language):
        """Forward-compat shim for newer faster-whisper (>=1.1).

        whisperx 3.2.0 builds TranscriptionOptions(**defaults) from a defaults
        dict that predates faster-whisper's required 'multilingual'/'hotwords'
        args, so load_model() crashes with
        `TypeError: TranscriptionOptions.__init__() missing ...`.
        whisperx merges our asr_options into those defaults first, so passing
        the missing keys here fixes it without touching site-packages.
        Keys are only added when the installed faster-whisper accepts them,
        keeping backward compatibility with older versions.
        """
        extra = {}
        try:
            import inspect
            import faster_whisper.transcribe as _fw_transcribe
            params = inspect.signature(_fw_transcribe.TranscriptionOptions.__init__).parameters
            if "multilingual" in params:
                # Describes the model, not the request: our models are
                # multilingual unless an english-only '.en' variant is used.
                extra["multilingual"] = not str(model_size or "").endswith(".en")
            if "hotwords" in params:
                extra["hotwords"] = None
        except Exception:
            pass
        return extra

    @staticmethod
    def _vram_status():
        """Human-readable VRAM state for diagnostics ('' when CUDA is unavailable)."""
        if not torch.cuda.is_available():
            return ""
        free_b, total_b = torch.cuda.mem_get_info()
        return (
            f" | VRAM free {free_b / 1024 ** 3:.2f}/{total_b / 1024 ** 3:.2f} GB, "
            f"allocated {torch.cuda.memory_allocated() / 1024 ** 3:.2f} GB, "
            f"reserved {torch.cuda.memory_reserved() / 1024 ** 3:.2f} GB"
        )

    @staticmethod
    def _pick_batch_size(free_bytes):
        """Smaller batches = less activation VRAM.

        Called AFTER the ASR model is resident, so `free_bytes` is what is left
        for activations — on an 8 GB GPU with desktop usage that is often only
        a few GB, where batch_size=16 overflows.
        """
        free_gb = free_bytes / 1024 ** 3
        if free_gb >= 4.5:
            return 16
        if free_gb >= 2.5:
            return 8
        if free_gb >= 1.5:
            return 4
        return 2

    def transcribe(self, audio_path, language=None, enable_diarization=True,
                   initial_prompt=None, model_size=None, batch_size=None):
        """
        Transcribe audio with WhisperX.
        model_size: optional per-call override (Whisper Model dropdown);
                    falls back to the instance default.
        batch_size: optional per-call override; defaults to a value derived
                    from the VRAM free after loading the model.
        Returns {"language": str, "segments": list}
        """
        if model_size:
            self.model_size = model_size
        prompt = initial_prompt or DEFAULT_INITIAL_PROMPT
        print(f"Loading WhisperX {self.model_size} on {self.device} with initial_prompt context...{self._vram_status()}")
        asr_options = {
            "initial_prompt": prompt,
            "condition_on_previous_text": False,
        }
        # Shim for faster-whisper>=1.1 required TranscriptionOptions args
        # (whisperx 3.2.0 defaults predate them). See _compat_asr_options.
        asr_options.update(self._compat_asr_options(self.model_size, language))
        try:
            model = whisperx.load_model(
                self.model_size, 
                self.device, 
                compute_type=self.compute_type,
                language=language,
                asr_options=asr_options
            )
        except Exception as e:
            print(f"Model loading error: {e}")
            raise

        print("Transcription in progress..." + self._vram_status())
        model_a = None
        try:
            # All VRAM-heavy work runs inside this try so the models are always
            # released below — otherwise an OOM (or any error) during generation
            # / alignment leaks ~3 GB per attempt and compounds across retries.
            audio = whisperx.load_audio(audio_path)

            # Pick a batch size that fits the VRAM left after the model loaded.
            if batch_size is None:
                if self.device == "cuda":
                    free_b, _total_b = torch.cuda.mem_get_info()
                    batch_size = self._pick_batch_size(free_b)
                else:
                    batch_size = 16
            print(f"ASR batch_size={batch_size}" + self._vram_status())

            # Retry with a halved batch on activation-spike OOM instead of
            # failing the whole transcription (common on 8 GB GPUs).
            #
            # NOTE: CTranslate2 (faster-whisper's backend) allocates GPU memory
            # from its OWN pool, entirely outside torch's caching allocator, so
            # torch.cuda.empty_cache() cannot reclaim a single byte of it. Once
            # that pool is exhausted the CUDA context is left unusable and the
            # *next* call fails with `parallel_for failed: cudaErrorInvalidDevice:
            # invalid device ordinal` instead of another clean OOM. Both symptoms
            # are therefore retryable, and the ASR model is dropped + reloaded on
            # every retry — that is the only way to release CTranslate2's pool
            # and get back a context that can actually run inference.
            bs = batch_size
            while True:
                try:
                    result = model.transcribe(audio, batch_size=bs)
                    break
                except RuntimeError as err:
                    msg = str(err).lower()
                    is_oom = "out of memory" in msg
                    is_bad_device = "invalid device" in msg or "parallel_for failed" in msg
                    if (not is_oom and not is_bad_device) or bs <= 1:
                        raise
                    new_bs = max(1, bs // 2)
                    cause = "OOM" if is_oom else "poisoned CUDA context"
                    print(f"[VRAM] {cause} at batch_size={bs}; "
                          f"reloading ASR model, retrying with batch_size={new_bs}{self._vram_status()}")
                    bs = new_bs
                    model = None
                    gc.collect()
                    if self.device == "cuda":
                        torch.cuda.empty_cache()
                        if hasattr(torch.cuda, "ipc_collect"):
                            torch.cuda.ipc_collect()
                    print("Reloading WhisperX ASR model..." + self._vram_status())
                    model = whisperx.load_model(
                        self.model_size,
                        self.device,
                        compute_type=self.compute_type,
                        language=language,
                        asr_options=asr_options
                    )
            detected_lang = result.get("language", "unknown")

            # Alignment
            print("Word-level alignment...")
            model_a, metadata = whisperx.load_align_model(
                language_code=result["language"], 
                device=self.device
            )
            result = whisperx.align(
                result["segments"], 
                model_a, 
                metadata, 
                audio, 
                self.device, 
                return_char_alignments=False
            )
        finally:
            # Cleanup alignment + ASR models on BOTH success and error paths.
            # cleanup_model() only deletes ITS OWN parameter — it cannot drop the
            # references held by this frame. Rebind them to None here, otherwise
            # the ~3 GB ASR model stays resident while the alignment model loads
            # (double occupancy → CUDA OOM on 8 GB GPUs).
            model_a = None
            model = None
            gc.collect()
            if self.device == "cuda":
                torch.cuda.empty_cache()
                if hasattr(torch.cuda, "ipc_collect"):
                    torch.cuda.ipc_collect()
            print(f"ASR/alignment models released{self._vram_status()}")
        
        # Re-enable TF32 — pyannote disables it for reproducibility,
        # but we need it for fast LLM/TTS inference on Ampere+ GPUs
        if self.device == "cuda":
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
            print("TF32 re-enabled after transcription")
        
        split_segs = split_long_segments(result["segments"], max_duration=9.0, max_chars=110)
        print(f"Split long segments: {len(result['segments'])} -> {len(split_segs)}")
        
        # Step 3: Merge solitary orphan punctuation lines and remove empty cues
        split_segs = merge_orphan_punctuation_segments(split_segs)
        from modules.srt_parser import merge_sentence_fragments
        split_segs = merge_sentence_fragments(split_segs)
        from modules.srt_cleaner import (
            remove_empty_cues_and_redistribute,
            fix_inter_cue_casing,
            apply_asr_corrections_cross_cues,
            normalize_timecodes,
        )
        split_segs = remove_empty_cues_and_redistribute(split_segs, text_key="text")
        
        # Step 4: Inter-cue casing (protects acronyms, proper nouns, single accented letters)
        split_segs = fix_inter_cue_casing(split_segs, text_key="text", lang_code=detected_lang)
        
        # Step 5: Post-ASR dictionary corrections on reconstituted text
        split_segs = apply_asr_corrections_cross_cues(split_segs, text_key="text")
        
        # Step 6: Strict timecode normalization (end[i] <= start[i+1] - 40ms, min 400ms duration)
        split_segs = normalize_timecodes(split_segs, min_gap_ms=40, min_cue_duration_ms=MIN_CUE_DURATION_MS, text_key="text")
            
        return {
            "language": detected_lang,
            "segments": split_segs
        }

    def cleanup(self):
        cleanup_model(None)

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        t = Transcriber(model_size="base") # petit modele pour test
        res = t.transcribe(sys.argv[1], enable_diarization=False)
        print(f"Langue: {res['language']}")
        for s in res['segments']:
            print(f"{s['start']}-{s['end']}: {s['text']}")
