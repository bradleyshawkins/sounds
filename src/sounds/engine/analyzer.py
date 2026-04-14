"""Song structure analyzer — detects segment boundaries via librosa.

Uses beat-synchronous MFCC features and agglomerative clustering to find
where the structure changes. Boundaries are snapped to the nearest beat so
they land on musically meaningful positions.

Sections are labeled A, B, C… and assigned colors from a fixed palette.
The labels have no semantic meaning (this approach cannot distinguish
chorus from verse); the user can rename them after the fact. A future
allin1 backend can replace _analyze() and return real semantic labels
using the same dict schema.
"""

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

from sounds.models import Section

def _section_colors(n: int) -> list[str]:
    """Generate n visually distinct hex colors using evenly-spaced HSL hues."""
    import colorsys
    colors = []
    for i in range(n):
        hue = i / n
        r, g, b = colorsys.hls_to_rgb(hue, 0.45, 0.55)
        colors.append(f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}")
    return colors


class StructureAnalyzer(QThread):
    """Detects structural segments in a loaded audio array.

    Runs in a background thread so the UI stays responsive.

    Signals
    -------
    finished(sections)
        List of :class:`~sounds.models.Section` objects.
    error(message)
        Emitted if analysis fails.
    """

    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, audio: np.ndarray, sample_rate: int) -> None:
        super().__init__()
        # audio shape: (samples, channels) float32 — same as engine convention.
        self._audio = audio
        self._sr = sample_rate

    def run(self) -> None:
        try:
            sections = self._analyze()
            self.finished.emit(sections)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _analyze(self) -> list[Section]:
        import librosa  # imported here to keep startup fast

        # Convert (samples, channels) → mono (samples,)
        y = self._audio.mean(axis=1) if self._audio.ndim > 1 else self._audio
        sr = self._sr
        n_samples = len(y)
        hop_length = 512  # librosa default

        # Beat tracking — used to snap boundaries and sync features.
        _, beat_frames = librosa.beat.beat_track(y=y, sr=sr, hop_length=hop_length)

        # Compute and normalize MFCCs.
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=12, hop_length=hop_length)
        mfcc = librosa.util.normalize(mfcc, axis=1)

        # Aggregate features at beat level — reduces matrix from ~15k frames
        # to ~300 beats for a 3-minute song, keeping boundaries on the beat.
        mfcc_sync = librosa.util.sync(mfcc, beat_frames.tolist(), aggregate=np.median)

        # Estimate segment count: roughly one section per 20 s, between 3 and 16.
        duration = n_samples / sr
        k = max(3, min(16, int(duration / 20)))
        k = min(k, mfcc_sync.shape[1] - 1)

        # Agglomerative clustering returns k-1 boundary beat indices.
        # Clamp to valid range — agglomerative can return n_beats (== len)
        # as a boundary meaning "end of track", which would be out of bounds.
        boundary_beats = librosa.segment.agglomerative(mfcc_sync, k=k)
        boundary_beats = boundary_beats[boundary_beats < len(beat_frames)]

        # Convert beat indices → frame indices → sample indices.
        boundary_frames = beat_frames[boundary_beats]
        boundary_samples = librosa.frames_to_samples(
            boundary_frames, hop_length=hop_length
        )

        # Build sections from the breakpoint list [0, b1, b2, …, n_samples].
        breakpoints = [0, *map(int, boundary_samples), n_samples]
        n_sections = len(breakpoints) - 1
        colors = _section_colors(n_sections)
        return [
            Section(
                start_sample=breakpoints[i],
                end_sample=breakpoints[i + 1],
                label=chr(ord("A") + i),
                color=colors[i],
            )
            for i in range(n_sections)
        ]
