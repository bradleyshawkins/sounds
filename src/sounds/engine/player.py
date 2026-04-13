"""Playback engine.

Architecture
------------
pyrubberband wraps the Rubberband CLI, so calling it per-chunk would spawn a
subprocess on every iteration — far too slow for real-time output. Instead the
engine pre-processes the full audio array whenever speed or pitch changes, then
feeds the result to sounddevice in fixed-size chunks from a background thread.

Array shape convention
----------------------
All audio arrays use (samples, channels) — the same layout used by soundfile,
sounddevice, and pyrubberband.  No transposing is needed between libraries.

Position tracking
-----------------
Position is always tracked in *input* sample space (the raw, unmodified audio)
so that loop points and seek operations remain stable across re-processes.
"""

import threading

import numpy as np
import pyrubberband as pyrb
import sounddevice as sd

from .sources.base import AudioSource

CHUNK_SIZE = 4096


class PlaybackEngine:
    def __init__(self) -> None:
        self._raw: np.ndarray | None = None        # (samples, channels) float32
        self._processed: np.ndarray | None = None  # (samples, channels) float32
        self._sample_rate: int = 44100

        # Current position and loop points are all in *input* sample space.
        self._input_pos: int = 0
        self._loop_start: int | None = None  # input samples
        self._loop_end: int | None = None    # input samples

        self._speed: float = 1.0       # 0.1 – 2.0
        self._semitones: float = 0.0   # ±24
        self._cents: float = 0.0       # ±100

        self._playing: bool = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self, source: AudioSource) -> None:
        """Load audio from a source. Blocks while decoding and processing."""
        self.stop()
        audio, sr = source.load()
        with self._lock:
            self._raw = audio
            self._sample_rate = sr
            self._input_pos = 0
            self._loop_start = None
            self._loop_end = None
        self._reprocess()

    # ------------------------------------------------------------------
    # Parameters
    # ------------------------------------------------------------------

    @property
    def speed(self) -> float:
        return self._speed

    @speed.setter
    def speed(self, value: float) -> None:
        self._speed = max(0.1, min(2.0, value))
        self._apply_params()

    @property
    def semitones(self) -> float:
        return self._semitones

    @semitones.setter
    def semitones(self, value: float) -> None:
        self._semitones = max(-24.0, min(24.0, value))
        self._apply_params()

    @property
    def cents(self) -> float:
        return self._cents

    @cents.setter
    def cents(self, value: float) -> None:
        self._cents = max(-100.0, min(100.0, value))
        self._apply_params()

    @property
    def total_semitones(self) -> float:
        """Combined pitch shift in semitones (semitones + cents/100)."""
        return self._semitones + self._cents / 100.0

    def set_params(self, speed: float, semitones: float, cents: float) -> None:
        """Set all playback parameters and reprocess once.

        Prefer this over the individual setters when changing multiple values
        together (e.g. from a debounced UI callback) so Rubberband only runs once.
        """
        self._speed = max(0.1, min(2.0, speed))
        self._semitones = max(-24.0, min(24.0, semitones))
        self._cents = max(-100.0, min(100.0, cents))
        self._apply_params()

    @property
    def is_playing(self) -> bool:
        return self._playing

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def play(self) -> None:
        if self._processed is None or self._playing:
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

    def set_loop(self, start: int | None, end: int | None) -> None:
        """Set loop boundaries in input sample space. Pass None to clear."""
        with self._lock:
            self._loop_start = start
            self._loop_end = end

    # ------------------------------------------------------------------
    # Convenience read-only properties
    # ------------------------------------------------------------------

    @property
    def _raw_samples(self) -> int:
        return self._raw.shape[0] if self._raw is not None else 0

    @property
    def _processed_samples(self) -> int:
        return self._processed.shape[0] if self._processed is not None else 0

    def duration_seconds(self) -> float:
        """Duration of the original (unmodified) audio in seconds."""
        return self._raw_samples / self._sample_rate if self._sample_rate else 0.0

    def position_seconds(self) -> float:
        """Current playback position expressed in original audio time."""
        return self._input_pos / self._sample_rate if self._sample_rate else 0.0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _apply_params(self) -> None:
        """Re-process audio after a speed/pitch change, then restore position."""
        if self._raw is None:
            return
        was_playing = self._playing
        if was_playing:
            self.pause()

        # Explicitly snapshot and restore position so it survives the reprocess
        # regardless of where the playback thread last wrote _input_pos.
        saved_pos = self._input_pos
        self._reprocess()
        with self._lock:
            self._input_pos = saved_pos

        if was_playing:
            self.play()

    def _reprocess(self) -> None:
        """Run pyrubberband on the full raw audio with the current parameters.

        pyrubberband calls the Rubberband CLI once for the whole array.
        Audio shape throughout is (samples, channels).
        """
        if self._raw is None:
            return

        audio = self._raw  # (samples, channels)
        speed = self._speed
        pitch = self.total_semitones

        if speed == 1.0 and pitch == 0.0:
            processed = audio.copy()
        elif speed == 1.0:
            processed = pyrb.pitch_shift(audio, self._sample_rate, n_steps=pitch)
        elif pitch == 0.0:
            processed = pyrb.time_stretch(audio, self._sample_rate, rate=speed)
        else:
            processed = pyrb.time_stretch(
                audio,
                self._sample_rate,
                rate=speed,
                rbargs={"--pitch": pitch},
            )

        with self._lock:
            self._processed = processed.astype("float32")

    def _input_to_output(self, input_sample: int) -> int:
        """Convert an input-space sample index to output-space."""
        if self._raw_samples == 0:
            return 0
        fraction = input_sample / self._raw_samples
        return int(fraction * self._processed_samples)

    def _run(self) -> None:
        """Playback thread: reads from _processed and writes to sounddevice."""
        channels = self._processed.shape[1]

        with sd.OutputStream(
            samplerate=self._sample_rate,
            channels=channels,
            dtype="float32",
        ) as stream:
            while self._playing:
                # Read loop points each iteration so UI changes take effect immediately.
                with self._lock:
                    out_pos = self._input_to_output(self._input_pos)
                    loop_start_in = self._loop_start
                    loop_end_raw = self._loop_end if self._loop_end is not None else self._raw_samples

                out_end = self._input_to_output(loop_end_raw)

                if out_pos >= out_end:
                    if loop_start_in is not None:
                        with self._lock:
                            self._input_pos = loop_start_in
                        continue
                    else:
                        # Reset so pressing play again starts from the beginning.
                        with self._lock:
                            self._input_pos = 0
                        self._playing = False
                        break

                chunk_out_end = min(out_pos + CHUNK_SIZE, out_end)
                # Slice along axis 0 — result is already (samples, channels) and C-contiguous
                chunk = self._processed[out_pos:chunk_out_end, :]

                stream.write(chunk)

                # Advance input_pos proportionally to stay in input-sample space
                samples_written = chunk_out_end - out_pos
                input_advance = int(samples_written * self._speed)
                with self._lock:
                    self._input_pos = min(
                        self._input_pos + input_advance, self._raw_samples
                    )
