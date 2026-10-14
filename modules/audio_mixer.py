import soundfile as sf
import numpy as np
import librosa
import os
import subprocess
from config import OUTPUT_SAMPLE_RATE, OUTPUT_CHANNELS

class AudioMixer:
    def mix(self, voice_path, background_path, output_path, voice_volume_db=0, bg_volume_db=-6):
        """
        Mix voice and background audio.
        """
        # Load voice
        v_audio, sr_v = librosa.load(voice_path, sr=OUTPUT_SAMPLE_RATE, mono=False)
        # Load background
        bg_audio, sr_bg = librosa.load(background_path, sr=OUTPUT_SAMPLE_RATE, mono=False)

        # Convertir en stereo si mono
        if v_audio.ndim == 1:
            v_audio = np.stack([v_audio, v_audio])
        if bg_audio.ndim == 1:
            bg_audio = np.stack([bg_audio, bg_audio])

        # Adjust lengths: reference is the voice (already synced with video)
        target_len = v_audio.shape[1]
        bg_len = bg_audio.shape[1]

        if bg_len < target_len:
            # Loop background if too short
            repeats = int(np.ceil(target_len / bg_len))
            bg_audio = np.tile(bg_audio, (1, repeats))[:, :target_len]
        else:
            # Truncate background if too long
            bg_audio = bg_audio[:, :target_len]

        # Apply gains
        v_gain = 10 ** (voice_volume_db / 20)
        bg_gain = 10 ** (bg_volume_db / 20)
        
        mixed = (v_audio * v_gain) + (bg_audio * bg_gain)
        
        # Clip to int16 range instead of renormalizing the whole mix:
        # renormalization would destroy the voice/BG balance the user set.
        mixed = np.clip(mixed, -1.0, 1.0)

        # Write (transpose for soundfile: shape (samples, channels))
        sf.write(output_path, mixed.T, OUTPUT_SAMPLE_RATE, subtype='PCM_16')
        
        return output_path

    def export_for_youtube(self, mixed_path, output_dir, basename):
        """
        Export as WAV and MP3.
        """
        wav_path = os.path.join(output_dir, f"{basename}_audio.wav")
        mp3_path = os.path.join(output_dir, f"{basename}_audio.mp3")
        
        import shutil
        shutil.copy2(mixed_path, wav_path)

        tmp_mp3 = mp3_path + ".tmp"
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
