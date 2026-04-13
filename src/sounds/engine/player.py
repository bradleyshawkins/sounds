"""Playback engine — streaming / real-time mode via pylibrb.

Architecture
------------
RubberBandStretcher runs in real-time mode, consuming raw audio chunks
on the fly and producing stretched/pitch-shifted output without any
pre-processing step. Playback begins instantly when play() is called.
Speed and pitch changes take effect within one chunk (~93 ms at 44 kHz)
with no pause or reprocess step.

Array shape convention
----------------------
All internal audio arrays use (samples, channels) — matching soundfile
and sounddevice. pylibrb expects (channels, samples), so we transpose
on the way in (chunk.T) and on the way out (out.T, then make C-contiguous).

Position tracking
-----------------
Position is tracked in *input* sample space (the raw, unmodified audio)
so that loop points and seek operations remain stable across speed changes.
"""

import threading

import numpy as np
import sounddevice as sd
from pylibrb import Option, RubberBandStretcher

from .sources.base import AudioSource

CHUNK_SIZE = 4096


class PlaybackEngine:
    def __init__(self) -> None:
        self._raw: np.ndarray | None = None  # (samples, channels) float32
        self._sample_rate: int = 44100

        # Current position and loop points in *input* sample space.
        self._input_pos: int = 0
        self._loop_start: int | None = None
        self._loop_end: int | None = None

        self._speed: float = 1.0
        self._semitones: float = 0.0
        self._cents: float = 0.0

        self._playing: bool = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

        # Stretcher created when audio is loaded and reused across play/pause
        # cycles. Setting time_ratio / pitch_scale takes effect immediately.
        self._stretcher: RubberBandStretcher | None = None

        # Set by seek() to signal the playback thread to reset the stretcher
        # buffer so stale pre-seek audio is not heard.
        self._seek_event = threading.Event()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self, source: AudioSource) -> None:
        """Load audio from a source. Blocks while decoding."""
        self.stop()
        audio, sr = source.load()
        with self._lock:
            self._raw = audio
            self._sample_rate = sr
            self._input_pos = 0
            self._loop_start = None
            self._loop_end = None
        self._stretcher = self._make_stretcher(audio.shape[1])

    # ------------------------------------------------------------------
    # Parameters — changes take effect within one chunk, no reprocessing
    # ------------------------------------------------------------------

    @property
    def speed(self) -> float:
        return self._speed

    @speed.setter
    def speed(self, value: float) -> None:
        self._speed = max(0.1, min(2.0, value))
        if self._stretcher is not None:
            self._stretcher.time_ratio = 1.0 / self._speed

    @property
    def semitones(self) -> float:
        return self._semitones

    @semitones.setter
    def semitones(self, value: float) -> None:
        self._semitones = max(-24.0, min(24.0, value))
        if self._stretcher is not None:
            self._stretcher.pitch_scale = 2.0 ** (self.total_semitones / 12.0)

    @property
    def cents(self) -> float:
        return self._cents

    @cents.setter
    def cents(self, value: float) -> None:
        self._cents = max(-100.0, min(100.0, value))
        if self._stretcher is not None:
            self._stretcher.pitch_scale = 2.0 ** (self.total_semitones / 12.0)

    @property
    def total_semitones(self) -> float:
        """Combined pitch shift in semitones (semitones + cents/100)."""
        return self._semitones + self._cents / 100.0

    def set_params(self, speed: float, semitones: float, cents: float) -> None:
        """Set all playback parameters at once."""
        self.speed = speed
        self.semitones = semitones
        self.cents = cents

    @property
    def is_playing(self) -> bool:
        return self._playing

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def play(self) -> None:
        if self._raw is None or self._playing:
            return
        self._playing = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def pause(self) -> None:
        """Pause playback; position is preserved."""
        self._playing = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def stop(self) -> None:
        """Stop playback and reset position to the beginning."""
        self._playing = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        with self._lock:
            self._input_pos = 0

    def seek(self, input_sample: int) -> None:
        """Seek to a position given in input (raw) sample space."""
        with self._lock:
            self._input_pos = max(0, min(input_sample, self._raw_samples))
        # Signal the playback thread to flush stale stretcher buffer.
        self._seek_event.set()

    def set_loop(self, start: int | None, end: int | None) -> None:
        """Set loop boundaries in input sample space. Pass None to clear."""
        with self._lock:
            self._loop_start = start
            self._loop_end = end

    # ------------------------------------------------------------------
    # Read-only properties
    # ------------------------------------------------------------------

    @property
    def _raw_samples(self) -> int:
        return self._raw.shape[0] if self._raw is not None else 0

    def duration_seconds(self) -> float:
        """Duration of the original (unmodified) audio in seconds."""
        return self._raw_samples / self._sample_rate if self._sample_rate else 0.0

    def position_seconds(self) -> float:
        """Current playback position expressed in original audio time."""
        return self._input_pos / self._sample_rate if self._sample_rate else 0.0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _make_stretcher(self, channels: int) -> RubberBandStretcher:
        """Create a fresh real-time stretcher with the current parameters."""
        s = RubberBandStretcher(
            sample_rate=self._sample_rate,
            channels=channels,
            options=Option.PROCESS_REALTIME,
            initial_time_ratio=1.0 / self._speed,
            initial_pitch_scale=2.0 ** (self.total_semitones / 12.0),
        )
        s.set_max_process_size(CHUNK_SIZE)
        return s

    def _run(self) -> None:
        """Playback thread: streams raw audio through RubberBandStretcher."""
        assert self._raw is not None
        assert self._stretcher is not None

        stretcher = self._stretcher
        stretcher.reset()
        self._seek_event.clear()

        with sd.OutputStream(
            samplerate=self._sample_rate,
            channels=self._raw.shape[1],
            dtype="float32",
        ) as stream:
            while self._playing:
                # If a seek happened, reset the stretcher to flush its buffer
                # so we don't hear pre-seek audio at the new position.
                if self._seek_event.is_set():
                    self._seek_event.clear()
                    stretcher.reset()

                with self._lock:
                    pos = self._input_pos
                    loop_start = self._loop_start
                    loop_end = (
                        self._loop_end
                        if self._loop_end is not None
                        else self._raw_samples
                    )

                if pos >= loop_end:
                    if loop_start is not None:
                        with self._lock:
                            self._input_pos = loop_start
                        stretcher.reset()
                        continue
                    else:
                        with self._lock:
                            self._input_pos = 0
                        self._playing = False
                        break

                chunk_end = min(pos + CHUNK_SIZE, loop_end)
                chunk = self._raw[pos:chunk_end, :]  # (samples, channels)

                # pylibrb expects (channels, samples); chunk.T is accepted even
                # when Fortran-contiguous.
                stretcher.process(chunk.T)

                out = stretcher.retrieve_available()  # (channels, samples)
                if out.shape[1] > 0:
                    # sounddevice expects (samples, channels), C-contiguous.
                    stream.write(np.ascontiguousarray(out.T))

                with self._lock:
                    self._input_pos = chunk_end

        self._stretcher = None
