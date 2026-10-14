"""Silence MioCodec's import-time FlashAttention warning (one line, once).

Background
----------
At import, ``miocodec.module.transformer`` logs at WARNING::

    FlashAttention is not installed. Falling back to PyTorch SDPA
    implementation. There is no warranty that the model will work correctly.

That fires on every Windows launch of this app: flash-attn publishes no
Windows wheels (and none for Python 3.14), so it can never be installed here —
``install.js`` already only attempts it on non-Windows platforms.

Why the fallback is safe
------------------------
PyTorch's SDPA backend computes the exact same scaled-dot-product attention
softmax(QK^T/√d)V as flash-attn, just with a different kernel; upstream's
"no warranty" line reflects that *they* mostly test the flash-attn path, not a
known correctness defect. ZastTranslate's BgTTS audio checks pass on SDPA.

What this module does
---------------------
Attaches a filter to the shared ``miocodec`` logger (all miocodec modules get
this same logger object from ``miocodec.util.get_logger()``) that drops ONLY
that exact record and prints one concise INFO-style line instead. Every other
miocodec warning (e.g. the Pupu-Vocoder missing-checkpoint warning) stays
visible. Idempotent; safe to call before or after ``import miocodec``.
"""

import logging

_ANNOUNCED = False

_FLASH_MSG_MARKER = "FlashAttention is not installed"


class _FlashAttnImportWarningFilter(logging.Filter):
    """Drop MioCodec's import-time FlashAttention warning; announce once."""

    def filter(self, record: logging.LogRecord) -> bool:
        global _ANNOUNCED
        try:
            msg = record.getMessage()
        except Exception:
            return True
        if _FLASH_MSG_MARKER in msg:
            if not _ANNOUNCED:
                _ANNOUNCED = True
                print(
                    "[MioCodec] FlashAttention not installed — using PyTorch SDPA "
                    "(normal on Windows: flash-attn ships no Windows/Py3.14 wheels; "
                    "SDPA is the supported inference path)."
                )
            return False  # suppress only this record
        return True


def install_flash_attn_quiet() -> logging.Logger:
    """Attach the filter to the shared 'miocodec' logger (idempotent)."""
    logger = logging.getLogger("miocodec")
    if not any(isinstance(f, _FlashAttnImportWarningFilter) for f in logger.filters):
        logger.addFilter(_FlashAttnImportWarningFilter())
    return logger


# Install on import so callers only need `import modules.quiet_flash_attn`.
install_flash_attn_quiet()
