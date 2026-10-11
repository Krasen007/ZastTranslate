"""Smoke test: GPU transcription through the app's real path.

Mirrors app.py step2_transcribe: Transcriber.transcribe() -> whisperx.load_model
-> model.transcribe (which runs detect_language -> model.encode on GPU). Uses
model_size="tiny" for speed. Validates that the CUDA 12 DLL exposure shim in
modules/transcriber.py fixes
"RuntimeError: Library cublas64_12.dll is not found or cannot be loaded".
"""
import sys
import time

sys.path.insert(0, r"F:\AI\repos\ZastTranslate")

import modules.transcriber as tm  # applies CUDA DLL + VAD compat patches

AUDIO = r"F:\AI\repos\ZastTranslate\temp\Another_One_Numberblocks_COURSE_-_Level_1_Red_S1_E2_16k.wav"

tr = tm.Transcriber(model_size="tiny")
t0 = time.time()
res = tr.transcribe(AUDIO, language="en", enable_diarization=False)
print(
    f"transcribe took {time.time() - t0:.1f}s, "
    f"lang={res['language']}, segments={len(res['segments'])}"
)
print("first:", res["segments"][0]["text"] if res["segments"] else None)
print("GPU TRANSCRIBE OK")
