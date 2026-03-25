from abc import ABC, abstractmethod

import numpy as np


class AudioSource(ABC):
    @abstractmethod
    def load(self) -> tuple[np.ndarray, int]:
        """Decode audio and return (audio, sample_rate).

        audio shape: (channels, samples) float32
        """
        ...
