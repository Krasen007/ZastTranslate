# Inference fixes — 2026-09-19

Model weights and training code are unchanged.

- Normalize Bulgarian stressed и (ѝ/Ѝ, including decomposed Unicode) to supported и/И instead of dropping it; normalize curly quotation marks without changing vocabulary IDs.
- Split overlong unspaced text and validate encoder capacity and sampling arguments.
- Correct repetition penalty for negative logits.
- Correct mono/channel-first tensor and soundfile stereo input handling for MioCodec; preserve single-frame code dimensions.
- Return both expected Gradio outputs on invalid input or failed synthesis; avoid silently omitting failed text chunks.
- Apply an offset causal mask when processing multiple new decoder tokens with a KV cache.
- Add CPU device selection to the CLI, pathlib support for speaker presets, and a warning when generation reaches its token limit.
- Remove deprecated Gradio Blocks theme/css arguments; bind to localhost by default (use --host 0.0.0.0 explicitly for network access).

Validation: `python -m unittest test_regressions -v` — seven tests passed. Local GPU synthesis with reference audio and CPU token generation were also checked. These fixes do not claim to resolve every acoustic artifact. Training-loop and amplitude-normalization changes discussed in the later audit are not included.
