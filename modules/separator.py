import sys
import torch
import os
import subprocess
from modules.utils import cleanup_model
from config import DEVICE, TEMP_DIR

class VocalSeparator:
    def __init__(self, model_name="htdemucs"):
        self.model_name = model_name

    def _find_existing_stems(self, track_name):
        """
        Return the directory containing already-separated stems for this track,
        or None if none exist. Mirrors the robust lookup used after demucs runs
        so we can detect a previous run BEFORE paying for separation again.
        """
        parent_dir = os.path.join(TEMP_DIR, self.model_name)
        direct = os.path.join(parent_dir, track_name)
        if os.path.exists(os.path.join(direct, "vocals.wav")):
            return direct
        if not os.path.exists(parent_dir):
            return None

        subdirs = [d for d in os.listdir(parent_dir) if os.path.isdir(os.path.join(parent_dir, d))]
        valid_dirs = []
        for sd in subdirs:
            vp = os.path.join(parent_dir, sd, "vocals.wav")
            if os.path.exists(vp):
                try:
                    mtime = os.path.getmtime(vp)
                except OSError:
                    mtime = 0
                valid_dirs.append((sd, mtime))
        if not valid_dirs:
            return None

        # Clean function to compare strings by alphanumeric content only
        def clean_name(s):
            return "".join(c.lower() for c in s if c.isalnum())

        cleaned_track = clean_name(track_name)
        best_sd, best_score = None, -1
        for sd, _mtime in valid_dirs:
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
            return os.path.join(parent_dir, best_sd)
        # Fallback: most recently written stems
        valid_dirs.sort(key=lambda x: x[1], reverse=True)
        return os.path.join(parent_dir, valid_dirs[0][0])

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
        cached_dir = self._find_existing_stems(track_name)
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
            "--device", DEVICE,
            "--two-stems", "vocals",
            audio_path
        ]
        subprocess.run(cmd, check=True)

        # Default Demucs output path: out_dir/model_name/track_name/
        # Robust lookup handles demucs sanitizing/truncating the directory name.
        output_dir = self._find_existing_stems(track_name) or os.path.join(TEMP_DIR, self.model_name, track_name)

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
