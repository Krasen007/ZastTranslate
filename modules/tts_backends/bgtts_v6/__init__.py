"""
Vendored BgTTS-38M-V2 inference package (beleata74/BgTTS-38M-V2, Apache-2.0).

This package is a self-contained copy of the model's inference code
(https://huggingface.co/beleata74/BgTTS-38M-V2, commit
3f6ca06b3ca78352eb00d8e96349c0bedaa3d2f1) with flat `import config` style
statements rewritten as relative imports so it can live inside
`modules.tts_backends` without colliding with ZastTranslate's own top-level
`config` module.

Nothing is imported here on purpose: the heavy Torch / MioCodec imports live in
the submodules and are loaded lazily by `modules.tts_backends.bgtts` only when
the BgTTS backend is actually selected.
"""
