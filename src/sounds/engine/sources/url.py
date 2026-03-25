import os
import tempfile

import numpy as np
import soundfile as sf
import yt_dlp

from .base import AudioSource


class URLSource(AudioSource):
    def __init__(self, url: str) -> None:
        self.url = url

    def load(self) -> tuple[np.ndarray, int]:
        with tempfile.TemporaryDirectory() as tmpdir:
            opts = {
                "format": "bestaudio/best",
                "outtmpl": os.path.join(tmpdir, "%(id)s.%(ext)s"),
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "flac",
                    }
                ],
                "quiet": True,
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([self.url])

            flac_files = [f for f in os.listdir(tmpdir) if f.endswith(".flac")]
            if not flac_files:
                raise RuntimeError(f"yt-dlp produced no audio output for: {self.url}")

            audio_path = os.path.join(tmpdir, flac_files[0])
            audio, sr = sf.read(audio_path, dtype="float32", always_2d=True)
            return audio, sr
