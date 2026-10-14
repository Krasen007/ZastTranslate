"""Smoke test: VocalSeparator stem caching + _find_existing_stems lookup.

Confirms that re-running separation on an already-processed track reuses the
existing stems (skips demucs entirely) instead of re-separating each time.
"""
import sys
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import os, time

sys.path.insert(0, r"F:\AI\repos\ZastTranslate")

from modules.separator import VocalSeparator
from config import TEMP_DIR

sep = VocalSeparator()
AUDIO = os.path.join(TEMP_DIR, "Another_One_Numberblocks_COURSE_-_Level_1_Red_S1_E2_44k.wav")

# 1) Lookup helper finds the existing stems
found = sep._find_existing_stems("Another_One_Numberblocks_COURSE_-_Level_1_Red_S1_E2_44k")
print("find_existing_stems ->", found)

# 2) separate() must return immediately (no demucs subprocess) on cache hit
t0 = time.time()
res = sep.separate(AUDIO)
elapsed = time.time() - t0
print("separate() returned in %.3fs" % elapsed)
print("vocals exists:", os.path.exists(res["vocals"]), "->", res["vocals"])
print("background exists:", os.path.exists(res["background"]), "->", res["background"])

assert found is not None, "lookup failed"
assert elapsed < 1.0, "separation was NOT cached (took %.2fs)" % elapsed
assert os.path.exists(res["vocals"]) and os.path.exists(res["background"])
print("SEPARATOR CACHE OK")
