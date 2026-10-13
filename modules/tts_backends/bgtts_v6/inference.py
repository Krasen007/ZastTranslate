"""
V6 Inference — encoder-decoder TTS with MioCodec + speaker cloning
===================================================================
1. Encode text with encoder (bidirectional, once)
2. Autoregressively decode audio tokens with decoder + speaker embedding
3. Decode tokens with MioCodec using global_embedding
"""

import torch
import argparse
import time
import math
import warnings
from pathlib import Path
from .config import (
    AUDIO_OFFSET, NUM_AUDIO_TOKENS, END_OF_SPEECH_TOKEN_ID,
    START_OF_SPEECH_TOKEN_ID, CODEC_SAMPLE_RATE, CODEC_FRAME_RATE,
)
from .tokenizer import TTSTokenizer
from .codec import CodecV6
from .model import load_for_inference


def _split_text(text, tokenizer, max_len=250):
    """Split text into chunks that fit within encoder max_text_len."""
    import re
    if max_len < 3:
        raise ValueError('max_len must leave room for text and two boundary tokens.')
    text = tokenizer.normalize_text(text)
    sentences = re.split(r'(?<=[.!?;:,])\s+', text)
    chunks = []
    current = ""
    for sent in sentences:
        candidate = (current + " " + sent).strip() if current else sent
        enc_len = len(tokenizer.build_encoder_input(candidate))
        if enc_len <= max_len:
            current = candidate
        else:
            if current:
                chunks.append(current)
            # If single sentence is too long, split by words
            if len(tokenizer.build_encoder_input(sent)) > max_len:
                words = sent.split()
                current = ""
                for w in words:
                    cand = (current + " " + w).strip() if current else w
                    if len(tokenizer.build_encoder_input(cand)) <= max_len:
                        current = cand
                    else:
                        if current:
                            chunks.append(current)
                        # A URL or unspaced word can exceed the encoder limit too.
                        current = ''
                        for ch in w:
                            if len(tokenizer.build_encoder_input(current + ch)) > max_len:
                                if current:
                                    chunks.append(current)
                                current = ch
                            else:
                                current += ch
            else:
                current = sent
    if current:
        chunks.append(current)
    return chunks


def _apply_repetition_penalty(logits, token_ids, penalty):
    """Reduce repeated-token scores regardless of the logit's sign."""
    if penalty != 1.0 and token_ids:
        indices = sorted(set(token_ids[-100:]))
        scores = logits[:, indices]
        logits[:, indices] = torch.where(scores < 0, scores * penalty, scores / penalty)
    return logits


@torch.no_grad()
def generate(model, tokenizer, text, speaker_emb,
             max_new_tokens=512, temperature=0.7, top_k=250,
             top_p=0.95, rep_penalty=1.1, device="cuda"):
    """
    Generate audio tokens from text.

    Args:
        model: TTSEncoderDecoder
        tokenizer: TTSTokenizer
        text: input text string
        speaker_emb: [128] MioCodec global_embedding
        max_new_tokens: max decoder steps
        temperature: sampling temperature
        top_k: top-k filtering
        top_p: nucleus sampling threshold
        rep_penalty: repetition penalty on recent tokens
        device: cuda/cpu

    Returns:
        torch.Tensor of MioCodec codes [num_frames], or None
    """
    if not math.isfinite(temperature) or temperature <= 0:
        raise ValueError('temperature must be finite and positive.')
    if not 0 < top_p <= 1 or not math.isfinite(rep_penalty) or rep_penalty <= 0:
        raise ValueError('top_p must be in (0, 1] and rep_penalty must be positive.')
    if not isinstance(top_k, int) or top_k < 0 or not isinstance(max_new_tokens, int) or max_new_tokens < 1:
        raise ValueError('top_k must be a non-negative integer and max_new_tokens a positive integer.')
    if not tokenizer.encode_text(text):
        raise ValueError('Text contains no supported characters.')
    # 1. Encode text (one shot, bidirectional)
    enc_ids = tokenizer.build_encoder_input(text).unsqueeze(0).to(device)
    if enc_ids.shape[1] > model.config.max_text_len:
        raise ValueError('Text exceeds encoder capacity; split it with _split_text first.')
    enc_mask = torch.ones_like(enc_ids)

    enc_out = model.encode(enc_ids, enc_mask)  # [1, T_enc, d_model]

    # 2. Prepare speaker embedding
    spk = speaker_emb.unsqueeze(0).to(device)  # [1, 128]

    # 3. Start decoder with <sos>
    dec_ids = torch.tensor([[START_OF_SPEECH_TOKEN_ID]], device=device)
    past = None
    generated_tokens = []

    for step in range(max_new_tokens):
        inp = dec_ids[:, -1:] if past is not None else dec_ids

        # Only pass speaker_emb on first step (already baked into embeddings)
        # Actually, with KV-cache, we only process new tokens, so speaker
        # needs to be added each time. The model handles this correctly.
        dec_out = model.decoder(
            input_ids=inp,
            encoder_output=enc_out,
            encoder_mask=enc_mask,
            speaker_emb=spk,
            past_key_values=past,
            use_cache=True,
        )
        past = dec_out["past_key_values"]
        logits = dec_out["logits"][:, -1, :]

        # Mask: only allow audio tokens + end_of_speech
        mask = torch.full_like(logits, float("-inf"))
        mask[:, AUDIO_OFFSET:AUDIO_OFFSET + NUM_AUDIO_TOKENS] = 0
        mask[:, END_OF_SPEECH_TOKEN_ID] = 0
        logits = logits + mask

        # Repetition penalty on recent tokens
        _apply_repetition_penalty(logits, generated_tokens, rep_penalty)

        logits = logits / temperature

        # Top-k
        if top_k > 0:
            kth = torch.topk(logits, min(top_k, logits.shape[-1])).values[:, -1:]
            logits[logits < kth] = float("-inf")

        # Top-p (nucleus)
        if top_p < 1.0:
            sorted_l, sorted_i = torch.sort(logits, descending=True)
            cum = torch.cumsum(torch.softmax(sorted_l, -1), -1)
            remove = cum > top_p
            remove[:, 1:] = remove[:, :-1].clone()
            remove[:, 0] = False
            logits[remove.scatter(1, sorted_i, remove)] = float("-inf")

        next_tok = torch.multinomial(torch.softmax(logits, -1), 1)
        tok_id = next_tok.item()

        if tok_id == END_OF_SPEECH_TOKEN_ID:
            break

        generated_tokens.append(tok_id)
        dec_ids = torch.cat([dec_ids, next_tok], dim=-1)
    else:
        warnings.warn('Reached max_new_tokens before end of speech; audio may be truncated. Use shorter text chunks.', RuntimeWarning)

    if not generated_tokens:
        return None

    result = torch.tensor(generated_tokens, dtype=torch.long)
    audio_mask = (result >= AUDIO_OFFSET) & (result < AUDIO_OFFSET + NUM_AUDIO_TOKENS)
    return result[audio_mask] - AUDIO_OFFSET


def synthesize(checkpoint, text, output="output.wav",
               speaker_wav=None, speaker_emb_path=None,
               temperature=0.7, top_k=250, top_p=0.95,
               rep_penalty=1.1, max_tokens=512, device="cuda"):
    """
    Full TTS pipeline: text → audio file.

    Speaker can be provided as:
      1. speaker_wav: path to reference audio (will encode with MioCodec)
      2. speaker_emb_path: path to saved .pt embedding
    """
    print(f"'{text[:80]}' | T={temperature}")
    model = load_for_inference(checkpoint, device=device)
    tokenizer = TTSTokenizer()
    codec = CodecV6(device=device)

    # Get speaker embedding
    if speaker_emb_path:
        import numpy as np
        if str(speaker_emb_path).endswith('.npy'):
            speaker_emb = torch.from_numpy(np.load(speaker_emb_path)).to(device)
        else:
            speaker_emb = torch.load(speaker_emb_path, map_location=device, weights_only=True)
        if isinstance(speaker_emb, dict):
            speaker_emb = speaker_emb.get("global_embedding",
                                           speaker_emb.get("embedding"))
        if speaker_emb.dim() > 1:
            speaker_emb = speaker_emb.squeeze()
        print(f"Speaker from preset: {speaker_emb.shape}")
    elif speaker_wav:
        result = codec.encode(speaker_wav)
        speaker_emb = result['global_embedding'].to(device)
        print(f"Speaker from wav: {speaker_wav}")
    else:
        raise ValueError("Provide speaker_wav or speaker_emb_path")

    # Split long text into chunks that fit encoder max_text_len
    chunks = _split_text(text, tokenizer, max_len=min(250, model.config.max_text_len))
    if not chunks:
        raise ValueError('Provide non-empty text.')
    print(f"Text split into {len(chunks)} chunk(s)")

    t0 = time.time()
    all_codes = []
    for i, chunk in enumerate(chunks):
        enc_len = len(tokenizer.build_encoder_input(chunk))
        print(f"  [{i+1}/{len(chunks)}] {enc_len} enc tokens: '{chunk[:60]}...'")
        codes = generate(model, tokenizer, chunk, speaker_emb, max_tokens,
                         temperature, top_k, top_p, rep_penalty, device)
        if codes is None or len(codes) == 0:
            raise RuntimeError(f'No audio generated for chunk {i + 1}; synthesis stopped to avoid omitting text.')
        all_codes.append(codes)
    gen_time = time.time() - t0

    if not all_codes:
        print("No audio generated!")
        return

    codes = torch.cat(all_codes)
    audio_dur = len(codes) / CODEC_FRAME_RATE
    rtf = gen_time / audio_dur if audio_dur > 0 else float('inf')
    print(f"{len(codes)} tokens ({audio_dur:.1f}s audio, {gen_time:.2f}s gen, RTF={rtf:.3f})")

    # Decode to wav
    wav = codec.tokens_to_wav(codes, speaker_emb, output)
    print(f"Saved: {output} ({len(wav)/CODEC_SAMPLE_RATE:.2f}s)")
    return wav


def main():
    p = argparse.ArgumentParser(description="V6 TTS Inference")
    p.add_argument("--checkpoint", required=True)
    p.add_argument("--text", required=True)
    p.add_argument("--output", default="output.wav")
    p.add_argument("--speaker-wav", help="Reference audio for voice cloning")
    p.add_argument("--speaker-emb", help="Path to saved speaker embedding .pt")
    p.add_argument("--temperature", type=float, default=0.7)
    p.add_argument("--top-k", type=int, default=250)
    p.add_argument("--top-p", type=float, default=0.95)
    p.add_argument("--rep-penalty", type=float, default=1.1)
    p.add_argument("--max-tokens", type=int, default=512)
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    a = p.parse_args()
    synthesize(a.checkpoint, a.text, a.output,
               speaker_wav=a.speaker_wav,
               speaker_emb_path=a.speaker_emb,
               temperature=a.temperature, top_k=a.top_k,
               top_p=a.top_p, rep_penalty=a.rep_penalty,
               max_tokens=a.max_tokens, device=a.device)

if __name__ == "__main__":
    main()
