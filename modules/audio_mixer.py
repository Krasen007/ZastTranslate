import os
import subprocess
import tempfile

import numpy as np
import soundfile as sf

from config import OUTPUT_SAMPLE_RATE, OUTPUT_CHANNELS

# Frames per processing block (~30 s at 44.1 kHz stereo float32 ~= 10 MB),
# so even 1 h+ tracks mix in constant memory.
_BLOCK_FRAMES = OUTPUT_SAMPLE_RATE * 30
# Voice activity threshold for ducking (~-40 dBFS RMS).
_DUCK_THRESHOLD_RMS = 0.01


def _run_ffmpeg(args, timeout=600):
    subprocess.run(
        ["ffmpeg", "-y", *args],
        check=True,
        capture_output=True,
        timeout=timeout,
    )


def _to_stereo_44k(src_path, dst_path):
    """Normalize any input to 44.1 kHz stereo PCM WAV (streaming ffmpeg)."""
    _run_ffmpeg([
        "-i", src_path,
        "-ar", str(OUTPUT_SAMPLE_RATE),
        "-ac", str(OUTPUT_CHANNELS),
        "-c:a", "pcm_s16le",
        dst_path,
    ])


class AudioMixer:
    def mix(self, voice_path, background_path, output_path,
            voice_volume_db=0, bg_volume_db=-6, ducking=False,
            duck_extra_db=6.0, block_frames=_BLOCK_FRAMES):
        """
        Mixing is block-wise: both inputs are normalized to 44.1 kHz
        stereo on disk via ffmpeg (no full-file librosa.load), then
        soundfile streams both tracks in ~30 s blocks. A short background
        is looped from its normalized file inside each block.
        ducking=True applies extra bg attenuation where voice is active.
        """
        if not os.path.exists(voice_path):
            raise FileNotFoundError(f"Voice track not found: {voice_path}")
        if not os.path.exists(background_path):
            raise FileNotFoundError(f"BG track not found: {background_path}")
        if block_frames <= 0:
            raise ValueError("block_frames must be positive")

        v_gain = 10 ** (voice_volume_db / 20)
        bg_gain = 10 ** (bg_volume_db / 20)
        duck_lin = 10 ** (-abs(duck_extra_db) / 20)

        tmp_dir = tempfile.mkdtemp(prefix="mix_")
        voice_norm = os.path.join(tmp_dir, "voice_44k.wav")
        bg_norm = os.path.join(tmp_dir, "bg_44k.wav")
        try:
            _to_stereo_44k(voice_path, voice_norm)
            _to_stereo_44k(background_path, bg_norm)
            target_frames = sf.info(voice_norm).frames
            if target_frames <= 0:
                raise ValueError(f"Voice track empty: {voice_path}")
            if sf.info(bg_norm).frames <= 0:
                raise ValueError(f"Background audio has no frames: {background_path}")

            cur_duck = 1.0
            with sf.SoundFile(voice_norm, "r") as vf, \
                    sf.SoundFile(bg_norm, "r") as bf, \
                    sf.SoundFile(output_path, "w",
                                 samplerate=OUTPUT_SAMPLE_RATE,
                                 channels=OUTPUT_CHANNELS,
                                 subtype="PCM_16") as out:
                while True:
                    v_block = vf.read(block_frames, dtype="float32",
                                      always_2d=True)
                    if len(v_block) == 0:
                        break
                    n = len(v_block)
                    b_block = np.empty((n, OUTPUT_CHANNELS), dtype=np.float32)
                    filled = 0
                    while filled < n:
                        chunk = bf.read(n - filled, dtype="float32",
                                        always_2d=True)
                        if len(chunk) == 0:
                            bf.seek(0)
                            continue
                        b_block[filled:filled + len(chunk)] = chunk
                        filled += len(chunk)
                    if ducking:
                        rms = float(np.sqrt(np.mean(v_block ** 2) + 1e-12))
                        tgt = duck_lin if rms > _DUCK_THRESHOLD_RMS else 1.0
                        c = 0.7 if tgt < cur_duck else 0.15
                        cur_duck = (1.0 - c) * cur_duck + c * tgt
                        b_block = b_block * cur_duck
                    mixed = (v_block * v_gain) + (b_block * bg_gain)
                    # Clip, don't renormalize: renormalization would destroy
                    # the voice/BG balance the user set.
                    np.clip(mixed, -1.0, 1.0, out=mixed)
                    out.write(mixed)
        finally:
            for p in (voice_norm, bg_norm):
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except OSError:
                    pass
            try:
                os.rmdir(tmp_dir)
            except OSError:
                pass
        return output_path

    def export_for_youtube(self, mixed_path, output_dir, basename):
        """
        Export as WAV and MP3.
        """
        wav_path = os.path.join(output_dir, f"{basename}_audio.wav")
        mp3_path = os.path.join(output_dir, f"{basename}_audio.mp3")
        
        import shutil
        shutil.copy2(mixed_path, wav_path)

        tmp_mp3 = mp3_path + ".tmp.mp3"
        cmd = [
            'ffmpeg', '-y', '-i', mixed_path,
            '-b:a', '320k',
            tmp_mp3
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=600)
            os.replace(tmp_mp3, mp3_path)
        finally:
            try:
                if os.path.exists(tmp_mp3):
                    os.remove(tmp_mp3)
            except OSError:
                pass

        return {"wav": wav_path, "mp3": mp3_path}
