"""Playback engine — streaming / real-time mode via pylibrb.

Architecture
------------
RubberBandStretcher runs in real-time mode, consuming raw audio chunks
on the fly and producing stretched/pitch-shifted output without any
pre-processing step. Playback begins instantly when play() is called.

The OutputStream uses a callback driven by the audio hardware. Every
~93 ms the driver calls _audio_callback on a dedicated high-priority
thread asking for the next buffer of samples. The callback pulls from
_output_queue; when the queue is empty it returns silence. The stream
runs for the lifetime of a loaded file and is never stopped — avoiding
the CoreAudio hardware click that occurs on macOS when a stream starts
or stops.

   Producer thread (_run) → _output_queue → _audio_callback → hardware

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

import queue
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
        self._channels: int = 2

        # Current position and loop points in *input* sample space.
        self._input_pos: int = 0
        self._loop_start: int | None = None
        self._loop_end: int | None = None

        self._speed: float = 1.0
        self._semitones: float = 0.0
        self._cents: float = 0.0
        self._volume: float = 1.0

        self._playing: bool = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

        # Stretcher created on load, reused across play/pause cycles.
        self._stretcher: RubberBandStretcher | None = None

        # Output pipeline: producer → queue → callback → hardware.
        # _cb_leftover holds samples that didn't fit in the previous callback
        # invocation (stretcher output is variable-size; callback blocksize is fixed).
        self._output_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=4)
        self._cb_leftover: np.ndarray | None = None

        self._stream: sd.OutputStream | None = None

        # Set by seek() to signal the playback thread to reset the stretcher.
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
            self._channels = audio.shape[1]
            self._input_pos = 0
            self._loop_start = None
            self._loop_end = None
        self._stretcher = self._make_stretcher(audio.shape[1])
        self._open_stream(audio.shape[1])

    # ------------------------------------------------------------------
    # Parameters — changes take effect within one chunk, no reprocessing
    # ------------------------------------------------------------------

    @property
    def speed(self) -> float:
        return self._speed

    @speed.setter
    def speed(self, value: float) -> None:
        self._speed = max(0.1, min(1.9, value))
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

    @property
    def volume(self) -> float:
        return self._volume

    @volume.setter
    def volume(self, value: float) -> None:
        self._volume = max(0.0, min(1.0, value))

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
        # Discard buffered audio so the callback falls silent immediately.
        self._flush_queue()

    def stop(self) -> None:
        """Stop playback and reset position to the beginning."""
        self.pause()
        with self._lock:
            self._input_pos = 0

    def seek(self, input_sample: int) -> None:
        """Seek to a position given in input (raw) sample space."""
        with self._lock:
            self._input_pos = max(0, min(input_sample, self._raw_samples))
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

    def close(self) -> None:
        """Release resources. Call before the application exits."""
        self.stop()
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self._stretcher = None

    def duration_seconds(self) -> float:
        """Duration of the original (unmodified) audio in seconds."""
        return self._raw_samples / self._sample_rate if self._sample_rate else 0.0

    def position_seconds(self) -> float:
        """Current playback position expressed in original audio time."""
        return self._input_pos / self._sample_rate if self._sample_rate else 0.0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _flush_queue(self) -> None:
        """Discard all buffered audio (queue + callback carry-over)."""
        self._cb_leftover = None
        while not self._output_queue.empty():
            try:
                self._output_queue.get_nowait()
            except queue.Empty:
                break

    def _open_stream(self, channels: int) -> None:
        """(Re)open the persistent callback stream."""
        self._flush_queue()
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
        self._stream = sd.OutputStream(
            samplerate=self._sample_rate,
            channels=channels,
            blocksize=CHUNK_SIZE,
            dtype="float32",
            callback=self._audio_callback,
        )
        self._stream.start()

    def _audio_callback(
        self,
        outdata: np.ndarray,
        frames: int,
        _time,  # noqa: ANN001 — CData type from cffi, not annotatable
        _status: sd.CallbackFlags,
    ) -> None:
        """Audio driver callback — runs on a dedicated high-priority thread.

        Pulls samples from _output_queue to fill outdata. Writes silence
        when the queue is empty (paused / stopped / end of track).
        Handles variable-size chunks via _cb_leftover carry-over.
        """
        filled = 0

        # Drain carry-over from the previous callback invocation first.
        if self._cb_leftover is not None:
            take = min(len(self._cb_leftover), frames)
            outdata[:take] = self._cb_leftover[:take]
            filled = take
            remainder = self._cb_leftover[take:]
            self._cb_leftover = remainder if len(remainder) else None

        # Pull further chunks from the queue as needed.
        while filled < frames:
            try:
                chunk = self._output_queue.get_nowait()
            except queue.Empty:
                outdata[filled:] = 0
                return

            take = min(len(chunk), frames - filled)
            outdata[filled : filled + take] = chunk[:take]
            filled += take
            if take < len(chunk):
                self._cb_leftover = chunk[take:]

    def _make_stretcher(self, channels: int) -> RubberBandStretcher:
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
        """Producer thread: stretches audio and enqueues it for output."""
        assert self._raw is not None
        assert self._stretcher is not None

        stretcher = self._stretcher
        stretcher.reset()
        self._seek_event.clear()

        while self._playing:
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
            chunk = self._raw[pos:chunk_end, :]
            stretcher.process(chunk.T)

            out = stretcher.retrieve_available()
            if out.shape[1] > 0:
                audio = np.ascontiguousarray(out.T)
                if self._volume != 1.0:
                    audio = audio * self._volume
                self._output_queue.put(audio)

            with self._lock:
                self._input_pos = chunk_end
