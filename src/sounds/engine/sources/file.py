import numpy as np
import soundfile as sf

from .base import AudioSource


class FileSource(AudioSource):
    def __init__(self, path: str) -> None:
        self.path = path

    def load(self) -> tuple[np.ndarray, int]:
        # soundfile returns (samples, channels) — same convention used throughout
        audio, sr = sf.read(self.path, dtype="float32", always_2d=True)
        return audio, sr
