import torch
import gc
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
from .base import LLMBackend
from config import DEVICE

class LocalCausalLMBackend(LLMBackend):
    def __init__(self, name: str, model_id: str, capabilities: dict):
        super().__init__()
        self._name = name
        self.model_id = model_id
        self._capabilities = capabilities
        self.device = DEVICE

    @property
    def name(self) -> str:
        return self._name

    @property
    def capabilities(self) -> dict:
        return self._capabilities

    def is_available(self) -> bool:
        try:
            import transformers
            import accelerate
            import bitsandbytes
            return True
        except ImportError:
            return False

    def load(self):
        if self.model is not None:
            return

        # Reclaim any fragmented/leftover VRAM (e.g. from the transcription step)
        # BEFORE loading. The OOM here is triggered by transformers' allocator
        # warmup needing one large contiguous block; emptying the cache returns
        # "reserved but unallocated" fragments to the driver so the block is
        # available. Critical on 8 GB GPUs.
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            if hasattr(torch.cuda, "ipc_collect"):
                torch.cuda.ipc_collect()

        print(f"Loading Translation LLM ({self.name} - {self.model_id})...")
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_id)
        
        if self.device == "cuda":
            # 4-bit quantization: ~4.5GB GPU instead of ~18GB, keeps everything on GPU (faster than CPU offload)
            bnb_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_use_double_quant=True,
                bnb_4bit_quant_type="nf4",
            )
            self.model = AutoModelForCausalLM.from_pretrained(
                self.model_id,
                quantization_config=bnb_config,
                device_map="cuda",
                attn_implementation="sdpa",
            )
            # Re-enable TF32 (pyannote disables it during transcription)
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
        else:
            self.model = AutoModelForCausalLM.from_pretrained(
                self.model_id,
                torch_dtype=torch.float32,
                device_map="cpu",
                attn_implementation="sdpa",
            )

    def unload(self):
        if self.model is not None:
            del self.model
            del self.tokenizer
            self.model = None
            self.tokenizer = None
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    def _apply_chat_template(self, messages):
        """Apply the chat template, tolerating older transformers without `enable_thinking`."""
        try:
            return self.tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=False,  # CRITICAL: disable Qwen3/Reasoning models internal reasoning
            )
        except TypeError:
            # Older transformers / non-Qwen tokenizers don't accept enable_thinking.
            return self.tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )

    def generate(self, messages: list, max_new_tokens: int = 4096, multiline: bool = False, **kwargs) -> str:
        if self.model is None:
            self.load()

        text_input = self._apply_chat_template(messages)
        model_inputs = self.tokenizer([text_input], return_tensors="pt").to(self.device)

        # Default generation args
        # Set pad_token_id explicitly to suppress transformers warning
        if self.model.generation_config.pad_token_id is None:
            self.model.generation_config.pad_token_id = self.tokenizer.eos_token_id
        do_sample = kwargs.get("do_sample", False)
        gen_kwargs = {
            "max_new_tokens": max_new_tokens,
            "do_sample": do_sample,
            "repetition_penalty": kwargs.get("repetition_penalty", 1.0)
        }
        if do_sample:
            gen_kwargs["temperature"] = kwargs.get("temperature", 0.7)

        with torch.no_grad():
            generated_ids = self.model.generate(
                **model_inputs,
                **gen_kwargs
            )

        generated_ids = [
            output_ids[len(input_ids):] for input_ids, output_ids in zip(model_inputs.input_ids, generated_ids)
        ]
        response = self.tokenizer.batch_decode(generated_ids, skip_special_tokens=True)[0]
        return response

    def generate_batch(self, messages_list: list, max_new_tokens_list: list, **kwargs) -> list:
        """Batched inference using left-padding. Processes all prompts in one GPU call."""
        if self.model is None:
            self.load()

        # Left-padding is mandatory for decoder-only LLMs in batch mode
        prev_padding_side = self.tokenizer.padding_side
        self.tokenizer.padding_side = "left"
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token_id = self.tokenizer.eos_token_id
        # Silence the "Setting pad_token_id to eos_token_id" warning from model.generate()
        if self.model.generation_config.pad_token_id is None:
            self.model.generation_config.pad_token_id = self.tokenizer.eos_token_id

        texts = [
            self._apply_chat_template(msgs)
            for msgs in messages_list
        ]

        model_inputs = self.tokenizer(texts, return_tensors="pt", padding=True).to(self.device)
        padded_len = model_inputs.input_ids.shape[1]

        do_sample = kwargs.get("do_sample", False)
        token_limits = list(max_new_tokens_list)
        gen_kwargs = {
            # A hard cap, with each row's actual limit enforced by its logits processor.
            "max_new_tokens": max(token_limits),
            "do_sample": do_sample,
            "repetition_penalty": kwargs.get("repetition_penalty", 1.0),
        }
        if do_sample:
            gen_kwargs["temperature"] = kwargs.get("temperature", 0.7)

        # Constrain a row's next token at its limit; stopping criteria run too late
        # to modify the scores used for sampling.
        from transformers import LogitsProcessor, LogitsProcessorList

        class _RowTokenBudget(LogitsProcessor):
            def __init__(self, prompt_len, limits, pad_id, eos_id):
                self.prompt_len = prompt_len
                self.limits = limits
                self.stop_id = eos_id if eos_id is not None else pad_id

            def __call__(self, input_ids, scores):
                produced = input_ids.shape[1] - self.prompt_len
                for i, limit in enumerate(self.limits):
                    if produced >= limit and self.stop_id is not None and 0 <= self.stop_id < scores.shape[-1]:
                        scores[i].fill_(float("-inf"))
                        scores[i, self.stop_id] = 0.0
                return scores

        eos_id = self.tokenizer.eos_token_id
        gen_kwargs["logits_processor"] = LogitsProcessorList([
            _RowTokenBudget(padded_len, token_limits, self.tokenizer.pad_token_id, eos_id)
        ])

        with torch.no_grad():
            generated_ids = self.model.generate(**model_inputs, **gen_kwargs)

        # Extract only new tokens (after the left-padded prompt length)
        new_ids = [
            generated_ids[i][padded_len:padded_len + token_limits[i]]
            for i in range(len(texts))
        ]
        responses = self.tokenizer.batch_decode(new_ids, skip_special_tokens=True)

        self.tokenizer.padding_side = prev_padding_side
        return responses
