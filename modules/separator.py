import sys
import torch
import os
import subprocess
from modules.utils import cleanup_model
from config import TEMP_DIR
import config

class VocalSeparator:
    def __init__(self, model_name="htdemucs"):
        self.model_name = model_name

    def _find_existing_stems(self, track_name, audio_path=None):
        """
        Return the directory containing already-separated stems for this track,
        or None if none exist. Mirrors the robust lookup used after demucs runs
        so we can detect a previous run BEFORE paying for separation again.

        Only returns a cache hit when the directory name actually matches
        ``track_name`` (exact or fuzzy). NEVER falls back to the most-recent
        unrelated stems — reusing the wrong song's vocals is worse than
        re-running Demucs. When ``audio_path`` is given, the cached stems must
        also be newer than the source audio (avoids stale reuse after the
        user replaces the file with same name).
        """
        parent_dir = os.path.join(TEMP_DIR, self.model_name)
        direct = os.path.join(parent_dir, track_name)
        if os.path.exists(os.path.join(direct, "vocals.wav")):
            if self._stems_are_fresh(os.path.join(direct, "vocals.wav"), audio_path):
                return direct
            return None
        if not os.path.exists(parent_dir):
            return None

        try:
            subdirs = [d for d in os.listdir(parent_dir) if os.path.isdir(os.path.join(parent_dir, d))]
        except OSError:
            return None
        valid_dirs = []
        for sd in subdirs:
            vp = os.path.join(parent_dir, sd, "vocals.wav")
            if os.path.exists(vp):
                valid_dirs.append(sd)
        if not valid_dirs:
            return None

        # Clean function to compare strings by alphanumeric content only
        def clean_name(s):
            return "".join(c.lower() for c in s if c.isalnum())

        cleaned_track = clean_name(track_name)
        best_sd, best_score = None, -1
        for sd in valid_dirs:
            cleaned_sd = clean_name(sd)
            score = 0
            if cleaned_track == cleaned_sd:
                score = 100
            elif cleaned_track.startswith(cleaned_sd) or cleaned_sd.startswith(cleaned_track):
                score = 50 + min(len(cleaned_track), len(cleaned_sd))
            elif cleaned_sd in cleaned_track or cleaned_track in cleaned_sd:
                score = 25 + min(len(cleaned_track), len(cleaned_sd))
            if score > best_score:
                best_score = score
                best_sd = sd

        if best_sd and best_score > 0:
            candidate = os.path.join(parent_dir, best_sd)
            if self._stems_are_fresh(os.path.join(candidate, "vocals.wav"), audio_path):
                return candidate
            return None
        # No name match -> cache miss. Do NOT return the most-recent
        # unrelated stems (previous behaviour caused silent wrong-song dubbing).
        return None

    @staticmethod
    def _stems_are_fresh(vocals_path, audio_path=None):
        """True if cached stems exist, are non-empty, and newer than source audio."""
        try:
            if not os.path.isfile(vocals_path) or os.path.getsize(vocals_path) == 0:
                return False
        except OSError:
            return False
        if audio_path:
            try:
                if os.path.isfile(audio_path) and os.path.getmtime(vocals_path) < os.path.getmtime(audio_path):
                    return False
            except OSError:
                pass
        return True

    def separate(self, audio_path):
        """
        Separate vocals from background audio using Demucs.
        Uses the demucs command line for simplicity and robustness.
        Reuses already-separated stems when available (skips demucs entirely).
        Returns {"vocals": str, "background": str}
        """
        print(f"Separating audio with {self.model_name}...")

        track_name = os.path.splitext(os.path.basename(audio_path))[0]

        # Cache hit: this track was already separated in a previous run.
        cached_dir = self._find_existing_stems(track_name, audio_path=audio_path)
        if cached_dir:
            vocals_path = os.path.join(cached_dir, "vocals.wav")
            background_path = os.path.join(cached_dir, "no_vocals.wav")
            if os.path.exists(vocals_path):
                print(f"[VocalSeparator] Reusing existing separated stems: {cached_dir}")
                return {"vocals": vocals_path, "background": background_path}

        # Demucs command with --two-stems to get vocals.wav + no_vocals.wav
        cmd = [
            sys.executable, "-m", "demucs",
            "-n", self.model_name,
            "--out", TEMP_DIR,
            "--device", config.get_device(),
            "--two-stems", "vocals",
            audio_path
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=3600)

        # Default Demucs output path: out_dir/model_name/track_name/
        # Robust lookup handles demucs sanitizing/truncating the directory name.
        output_dir = self._find_existing_stems(track_name, audio_path=None) or os.path.join(TEMP_DIR, self.model_name, track_name)

        vocals_path = os.path.join(output_dir, "vocals.wav")
        background_path = os.path.join(output_dir, "no_vocals.wav")

        if not os.path.exists(vocals_path):
            raise FileNotFoundError(f"Vocals not found at {vocals_path}")

        return {"vocals": vocals_path, "background": background_path}

    def cleanup(self):
        # Nothing to clean up since we use subprocess
        pass

if __name__ == "__main__":
    import sys
    sep = VocalSeparator()
    if len(sys.argv) > 1:
        res = sep.separate(sys.argv[1])
        print(f"Vocals: {res['vocals']}")
        print(f"Background: {res['background']}")
    else:
        print("Usage: python separator.py [AUDIO_PATH]")
