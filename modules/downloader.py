import yt_dlp
import os
import shutil
import subprocess
from modules.utils import get_exact_duration, convert_sample_rate
from config import TEMP_DIR, DOWNLOADS_DIR

# Detect available JS runtime once at module load.
# yt-dlp needs a JS runtime to solve YouTube's n-challenge (format URL decryption).
# Node.js is bundled with Pinokio; deno is yt-dlp's default but rarely installed.
_JS_RUNTIME = next((r for r in ('node', 'deno', 'phantomjs') if shutil.which(r)), None)
if _JS_RUNTIME:
    print(f"[yt-dlp] JS runtime detected: {_JS_RUNTIME}")
else:
    print("[yt-dlp] WARNING: No JS runtime (node/deno) found in PATH. Some YouTube formats may be unavailable.")


def _ydl_base_opts(allow_remote_components: bool = False) -> dict:
    """Common yt-dlp options with automatic JS runtime injection.

    YouTube's n-challenge needs a JS runtime (node/deno) plus yt-dlp's remote
    EJS solver ('ejs:github', fetched over HTTPS from the yt-dlp org). That is
    a supply-chain surface, so set allow_remote_components=False to disable it
    (some age-gated / SABR formats may then fail).
    """
    opts = {}
    if _JS_RUNTIME:
        opts['js_runtimes'] = {_JS_RUNTIME: {}}
        if allow_remote_components:
            opts['remote_components'] = ['ejs:github']
    ffmpeg = shutil.which('ffmpeg')
    if ffmpeg:
        opts['ffmpeg_location'] = ffmpeg
    return opts


_ALLOW_REMOTE_EJS = (
    os.environ.get("ZAST_ALLOW_REMOTE_EJS", "") == "1"
    and os.environ.get("ZAST_NO_REMOTE_EJS", "") != "1"
)


class VideoDownloader:
    def __init__(self):
        os.makedirs(TEMP_DIR, exist_ok=True)

    @staticmethod
    def _base_opts():
        return _ydl_base_opts(allow_remote_components=_ALLOW_REMOTE_EJS)

    def check_url(self, url):
        """
        Fetch video info without downloading.
        Returns {"title": str, "duration": float, "resolutions": list[str]}
        Set ZAST_ALLOW_REMOTE_EJS=1 to enable yt-dlp's remote EJS solver.
        Set ZAST_NO_REMOTE_EJS=1 to disable it even when explicitly enabled.
        """
        ydl_opts = {
            **VideoDownloader._base_opts(),
            'quiet': True,
            'noplaylist': True,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                formats = info.get('formats', [])
                # Collect unique video heights
                heights = sorted(set(
                    f['height'] for f in formats
                    if f.get('height') and f.get('vcodec', 'none') != 'none'
                ), reverse=True)
                resolutions = [f"{h}p" for h in heights]
                return {
                    "title": info.get('title', 'Unknown'),
                    "description": info.get('description', ''),
                    "duration": info.get('duration', 0),
                    "youtube_id": info.get('id', None),
                    "resolutions": resolutions,
                }
        except Exception as e:
            print(f"yt-dlp check error: {e}")
            raise

    @staticmethod
    def _find_cached_video(youtube_id):
        """Return a cached video path for this YouTube ID, or None.

        Cache files are named '<id>.mp4' inside DOWNLOADS_DIR. A zero-byte or
        missing file is treated as a cache miss so a half-written download is
        never reused.
        """
        if not youtube_id:
            return None
        cached = os.path.join(DOWNLOADS_DIR, f"{youtube_id}.mp4")
        try:
            if os.path.isfile(cached) and os.path.getsize(cached) > 0:
                return cached
        except OSError:
            return None
        return None

    def _probe_info(self, url):
        """Fetch video metadata WITHOUT downloading (used for the cache check)."""
        ydl_opts = {
            **VideoDownloader._base_opts(),
            'quiet': True,
            'noplaylist': True,
            'skip_download': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)

    def download(self, url, resolution="1080p", progress_callback=None):
        """
        Download video via yt-dlp with real-time progress updates.
        Returns {"video_path": str, "audio_16k": str, "audio_44k": str, "duration": float, "title": str}

        Caching: if this exact YouTube video was downloaded before (matched by its
        video ID), the cached file in DOWNLOADS_DIR is reused and the download is
        skipped entirely. This survives "New Project" (which only wipes TEMP_DIR).
        """
        # 1) Probe metadata first (no download) so we can check the cache before
        #    spending bandwidth. This also gives us the stable video ID.
        if progress_callback:
            progress_callback(0.02, "Checking local cache & connecting to YouTube...")
        try:
            probe = self._probe_info(url)
        except Exception as e:
            print(f"yt-dlp probe error: {e}")
            raise

        youtube_id = probe.get('id')
        title = probe.get('title', 'video')
        description = probe.get('description', '')

        # 2) Cache hit -> reuse the existing file, skip downloading.
        cached = self._find_cached_video(youtube_id)
        if cached:
            if progress_callback:
                progress_callback(0.9, f"Already downloaded — reusing cached copy of '{title}'...")
            print(f"[yt-dlp] Cache HIT for {youtube_id}: reusing {cached}")
            return self._process_video(cached, title, description, youtube_id)

        # 3) Cache miss -> download into the persistent cache as '<id>.mp4'.
        if resolution == "Best":
            fmt = "bestvideo+bestaudio/best"
            format_sort = ['res', 'ext:mp4:m4a', 'codec:h264:vp9']
        else:
            height = resolution.replace("p", "")
            fmt = f"bestvideo[height<={height}]+bestaudio/bestvideo+bestaudio/best[height<={height}]/best"
            format_sort = [f'res:{height}', 'ext:mp4:m4a', 'codec:h264:vp9']

        def ytdl_hook(d):
            if progress_callback and d.get('status') == 'downloading':
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                downloaded = d.get('downloaded_bytes') or 0
                if total > 0:
                    pct = max(0.05, min(0.85, (downloaded / total) * 0.85))
                    speed = d.get('speed')
                    speed_mb = f" ({speed / 1024 / 1024:.1f} MB/s)" if speed else ""
                    progress_callback(pct, f"Downloading video... {int((downloaded / total) * 100)}%{speed_mb}")
            elif progress_callback and d.get('status') == 'finished':
                progress_callback(0.88, "Extracting audio and processing formats...")

        ydl_opts = {
            **VideoDownloader._base_opts(),
            'format': fmt,
            'format_sort': format_sort,
            # Store in the persistent cache, keyed by video ID for reliable reuse.
            'outtmpl': os.path.join(DOWNLOADS_DIR, f"{youtube_id or '%(id)s'}.%(ext)s"),
            'merge_output_format': 'mp4',
            'noplaylist': True,
            'quiet': True,
            'restrictfilenames': True,
            'progress_hooks': [ytdl_hook],
        }

        try:
            if progress_callback:
                progress_callback(0.05, "Downloading video...")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                video_filename = ydl.prepare_filename(info)
                if 'merge_output_format' in ydl_opts and ydl_opts['merge_output_format']:
                    base, _ = os.path.splitext(video_filename)
                    video_filename = base + '.' + ydl_opts['merge_output_format']

                if progress_callback:
                    progress_callback(0.92, "Finalizing audio tracks (16kHz / 44.1kHz)...")
                return self._process_video(video_filename, info.get('title', 'video'), info.get('description', ''), info.get('id', None))
        except Exception as e:
            print(f"yt-dlp download error: {e}")
            raise

    def import_local(self, filepath, progress_callback=None):
        """
        Copy local file to TEMP_DIR with progress feedback.
        """
        filename = os.path.basename(filepath)
        dest_path = os.path.join(TEMP_DIR, filename)
        if progress_callback:
            progress_callback(0.1, f"Importing local file ({filename})...")
        shutil.copy2(filepath, dest_path)
        return self._process_video(dest_path, os.path.splitext(filename)[0], "", None, progress_callback=progress_callback)

    def _process_video(self, video_path, title, description="", youtube_id=None, progress_callback=None):
        """
        Extract audio and return file info.
        """
        audio_paths = self.extract_audio(video_path, progress_callback=progress_callback)
        if progress_callback:
            progress_callback(0.95, "Analyzing media duration & metadata...")
        duration = get_exact_duration(video_path)
        
        return {
            "video_path": video_path,
            "audio_16k": audio_paths["audio_16k"],
            "audio_44k": audio_paths["audio_44k"],
            "duration": duration,
            "title": title,
            "description": description,
            "youtube_id": youtube_id
        }

    def extract_audio(self, video_path, progress_callback=None):
        """
        Extract two audio versions:
        - WAV 16kHz mono (for WhisperX)
        - WAV 44.1kHz stereo (for Demucs)
        """
        base_name = os.path.splitext(os.path.basename(video_path))[0]
        audio_16k = os.path.join(TEMP_DIR, f"{base_name}_16k.wav")
        audio_44k = os.path.join(TEMP_DIR, f"{base_name}_44k.wav")

        # Extract 16k mono
        if progress_callback:
            progress_callback(0.40, "Extracting audio track for WhisperX (16kHz mono)...")
        convert_sample_rate(video_path, audio_16k, 16000, 1)
        
        # Extract 44.1k stereo
        if progress_callback:
            progress_callback(0.75, "Extracting high-fidelity audio track for Demucs (44.1kHz stereo)...")
        convert_sample_rate(video_path, audio_44k, 44100, 2)

        return {"audio_16k": audio_16k, "audio_44k": audio_44k}

if __name__ == "__main__":
    import sys
    dl = VideoDownloader()
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        print(f"Processing: {arg}")
        if arg.startswith("http"):
            res = dl.download(arg)
        else:
            res = dl.import_local(arg)
        print(f"Result: {res}")
    else:
        print("Usage: python downloader.py [URL or LOCAL_PATH]")
