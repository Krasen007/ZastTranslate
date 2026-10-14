# NOTE: This NLLB module is legacy. The active translation path is the LLM
# reformulator (modules/reformulator.py via llm_backends). NLLB is only used as
# an opt-in fallback and loads lazily — importing this module loads nothing.
import torch
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
from modules.utils import cleanup_model
from config import DEVICE, LANGUAGES


def _resolve_nllb_code(name, role="language"):
    code = LANGUAGES.get(name)
    if code is None:
        raise ValueError(
            f"Unknown {role} {name!r} for NLLB translation. "
            f"Available: {sorted(LANGUAGES.keys())}"
        )
    return code

class Translator:
    def __init__(self, model_name="facebook/nllb-200-distilled-600M"):
        self.model_name = model_name
        self.model = None
        self.tokenizer = None
        self.device = DEVICE

    def load_model(self):
        if self.model is None:
            print(f"Loading NLLB-200 ({self.model_name})...")
            self.tokenizer = AutoTokenizer.from_pretrained(self.model_name)
            if self.device == "cuda":
                # FP16 halves the 600M footprint so NLLB can coexist with LLM/TTS.
                self.model = AutoModelForSeq2SeqLM.from_pretrained(
                    self.model_name, torch_dtype=torch.float16
                ).to(self.device)
            else:
                self.model = AutoModelForSeq2SeqLM.from_pretrained(self.model_name).to(self.device)

    def translate_text(self, text, source_lang_name, target_lang_name):
        """
        Translate a single string using NLLB.
        """
        if not text or not str(text).strip():
            return ""
        dummy_seg = [{"text": str(text).strip(), "start": 0.0, "end": 1.0}]
        res = self.translate_segments(dummy_seg, source_lang_name, target_lang_name)
        if res and len(res) > 0:
            return res[0].get("translated_text", "")
        return ""

    def translate_segments(self, segments, source_lang_name, target_lang_name):
        """
        Traduit une liste de segments.
        Ajoute 'translated_text' a chaque segment.
        """
        if not segments:
            return segments

        self.load_model()

        src_code = _resolve_nllb_code(source_lang_name, role="source language")
        tgt_code = _resolve_nllb_code(target_lang_name, role="target language")

        print(f"Traduction de {source_lang_name} ({src_code}) vers {target_lang_name} ({tgt_code})...")

        tgt_token_id = self.tokenizer.convert_tokens_to_ids(tgt_code)
        if tgt_token_id == self.tokenizer.unk_token_id:
            print(f"WARNING: Language code '{tgt_code}' not found in tokenizer. Trying alternatives...")
            tgt_token_id = self.tokenizer.convert_tokens_to_ids(f"__{tgt_code}__")

        self.tokenizer.src_lang = src_code

        # Batch tokenization: one encoder pass for all non-empty segments.
        texts = [seg.get("text", "").strip() for seg in segments]
        non_empty_idx = [i for i, t in enumerate(texts) if t]
        if non_empty_idx:
            batch = [texts[i] for i in non_empty_idx]
            inputs = self.tokenizer(batch, return_tensors="pt", padding=True, truncation=True).to(self.device)
            with torch.no_grad():
                translated_tokens = self.model.generate(
                    **inputs,
                    forced_bos_token_id=tgt_token_id,
                    max_new_tokens=256,
                )
            decoded = self.tokenizer.batch_decode(translated_tokens, skip_special_tokens=True)
            for i, text in zip(non_empty_idx, decoded):
                segments[i]["translated_text"] = text
        for i, text in enumerate(texts):
            if not text:
                segments[i]["translated_text"] = ""

        return segments

    def retranslate_constrained(self, segments, source_lang_name, target_lang_name, 
                                 chars_per_second=15, speed_factor=1.25):
        """
        Re-translate segments that overflow their time slots with a tighter max_length.
        Only re-translates segments where translated_text is too long to fit.
        Returns (segments, retranslated_count).
        """
        if not segments:
            return segments, 0

        self.load_model()

        src_code = _resolve_nllb_code(source_lang_name, role="source language")
        tgt_code = _resolve_nllb_code(target_lang_name, role="target language")

        tgt_token_id = self.tokenizer.convert_tokens_to_ids(tgt_code)
        if tgt_token_id == self.tokenizer.unk_token_id:
            tgt_token_id = self.tokenizer.convert_tokens_to_ids(f"__{tgt_code}__")
        
        self.tokenizer.src_lang = src_code
        
        retranslated = 0
        for seg in segments:
            text = seg.get("text", "").strip()
            translated = seg.get("translated_text", "")
            if not text or not translated:
                continue
            
            duration = seg["end"] - seg["start"]
            # Max chars that fit = duration × cps × speed_factor  
            max_chars = duration * chars_per_second * speed_factor
            
            if len(translated) <= max_chars * 1.1:  # 10% tolerance
                continue
            
            # Re-translate with tighter max_length
            # NLLB subword ratio: ~1.3-1.8 tokens per char, use 1.5
            max_tokens = max(10, int(max_chars * 1.5))
            
            inputs = self.tokenizer(text, return_tensors="pt", truncation=True).to(self.device)

            with torch.no_grad():
                constrained_tokens = self.model.generate(
                    **inputs,
                    forced_bos_token_id=tgt_token_id,
                    max_new_tokens=max_tokens,
                )
            
            constrained_text = self.tokenizer.batch_decode(constrained_tokens, skip_special_tokens=True)[0]
            
            # Only use constrained version if it's actually shorter and non-empty
            if constrained_text.strip() and len(constrained_text) < len(translated):
                old_len = len(translated)
                seg["translated_text"] = constrained_text
                seg["retranslated"] = True
                retranslated += 1
                print(f"  Constrained [{seg['start']:.1f}-{seg['end']:.1f}]: {old_len}->{len(constrained_text)} chars")
        
        return segments, retranslated

    def cleanup(self):
        cleanup_model(self.model)
        cleanup_model(self.tokenizer)
        self.model = None
        self.tokenizer = None

if __name__ == "__main__":
    t = Translator()
    segs = [{"text": "Bonjour tout le monde", "start": 0, "end": 2}]
    res = t.translate_segments(segs, "Francais", "English")
    print(res[0]["translated_text"])
