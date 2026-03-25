from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from sounds.engine.player import PlaybackEngine
from sounds.engine.sources.file import FileSource
from sounds.engine.sources.url import URLSource


def _fmt_time(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"


class _Worker(QThread):
    """Generic background thread that runs a single callable."""

    error = pyqtSignal(str)

    def __init__(self, fn) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            self._fn()
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.engine = PlaybackEngine()
        self._worker: _Worker | None = None

        # If the user adjusts speed/pitch while a reprocess is already running,
        # the latest values are saved here and applied as soon as it finishes.
        self._pending_params: tuple[float, float, float] | None = None

        # True while the user is dragging the seek bar so the position timer
        # doesn't fight the drag.
        self._seeking: bool = False

        # Debounce for pitch spinboxes — fires 400 ms after the last valueChanged.
        self._pitch_timer = QTimer(self)
        self._pitch_timer.setSingleShot(True)
        self._pitch_timer.setInterval(400)
        self._pitch_timer.timeout.connect(self._trigger_reprocess)

        # Position display + play-button sync; runs whenever audio is loaded.
        self._position_timer = QTimer(self)
        self._position_timer.setInterval(100)
        self._position_timer.timeout.connect(self._sync_state)

        # Start in ~/Music if it exists, otherwise home.
        music = Path.home() / "Music"
        self._last_dir = str(music if music.is_dir() else Path.home())

        self._build_ui()
        self._set_transport_enabled(False)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setWindowTitle("Sounds")
        self.setMinimumWidth(540)

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setSpacing(10)

        layout.addLayout(self._build_file_row())
        layout.addLayout(self._build_transport_row())
        layout.addLayout(self._build_seek_row())
        layout.addLayout(self._build_speed_row())
        layout.addLayout(self._build_pitch_row())
        layout.addStretch()

        self._status = QStatusBar()
        self.setStatusBar(self._status)

    def _build_file_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._open_btn = QPushButton("Open File…")
        self._url_btn = QPushButton("Open URL…")
        self._open_btn.clicked.connect(self._open_file)
        self._url_btn.clicked.connect(self._open_url)
        row.addWidget(self._open_btn)
        row.addWidget(self._url_btn)
        row.addStretch()
        return row

    def _build_transport_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._play_btn = QPushButton("▶")
        self._play_btn.setFixedWidth(48)
        self._stop_btn = QPushButton("■")
        self._stop_btn.setFixedWidth(48)
        self._play_btn.clicked.connect(self._toggle_play)
        self._stop_btn.clicked.connect(self._on_stop)
        self._position_label = QLabel("0:00 / 0:00")
        row.addWidget(self._play_btn)
        row.addWidget(self._stop_btn)
        row.addSpacing(8)
        row.addWidget(self._position_label)
        row.addStretch()
        return row

    def _build_seek_row(self) -> QHBoxLayout:
        self._seek_slider = QSlider(Qt.Orientation.Horizontal)
        self._seek_slider.setRange(0, 10000)
        self._seek_slider.setValue(0)
        self._seek_slider.sliderPressed.connect(self._on_seek_pressed)
        self._seek_slider.sliderReleased.connect(self._on_seek_released)

        # Current position — editable so the user can type a time and seek.
        self._pos_edit = QLineEdit("0:00")
        self._pos_edit.setFixedWidth(52)
        self._pos_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._pos_edit.setToolTip("Current position (M:SS) — press Enter or click away to seek")
        self._pos_edit.editingFinished.connect(self._on_pos_edit_committed)

        # Total duration — read-only.
        self._dur_label = QLineEdit("0:00")
        self._dur_label.setFixedWidth(52)
        self._dur_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._dur_label.setReadOnly(True)

        row = QHBoxLayout()
        row.addWidget(self._pos_edit)
        row.addWidget(self._seek_slider)
        row.addWidget(self._dur_label)
        return row

    def _build_speed_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel("Speed:"))

        self._speed_slider = QSlider(Qt.Orientation.Horizontal)
        self._speed_slider.setRange(10, 200)   # 0.10× – 2.00×
        self._speed_slider.setValue(100)
        self._speed_slider.setTickInterval(10)
        self._speed_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        # valueChanged keeps the spinbox in sync while dragging; the actual
        # reprocess is triggered on sliderReleased (once, when mouse lifts).
        self._speed_slider.valueChanged.connect(self._on_speed_slider_moved)
        self._speed_slider.sliderReleased.connect(self._trigger_reprocess)

        self._speed_spin = QDoubleSpinBox()
        self._speed_spin.setRange(0.10, 2.00)
        self._speed_spin.setSingleStep(0.01)
        self._speed_spin.setDecimals(2)
        self._speed_spin.setValue(1.00)
        self._speed_spin.setSuffix("×")
        self._speed_spin.setFixedWidth(80)
        # editingFinished fires when the user presses Enter or leaves the box.
        self._speed_spin.editingFinished.connect(self._on_speed_spin_edited)

        row.addWidget(self._speed_slider)
        row.addWidget(self._speed_spin)
        return row

    def _build_pitch_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel("Pitch:"))

        self._semitones_spin = QSpinBox()
        self._semitones_spin.setRange(-24, 24)
        self._semitones_spin.setValue(0)
        self._semitones_spin.setSuffix(" st")
        self._semitones_spin.setToolTip("Semitones (±24)")
        self._semitones_spin.valueChanged.connect(self._on_pitch_changed)

        self._cents_spin = QSpinBox()
        self._cents_spin.setRange(-100, 100)
        self._cents_spin.setValue(0)
        self._cents_spin.setSuffix(" ct")
        self._cents_spin.setToolTip("Fine tuning in cents (±100)")
        self._cents_spin.valueChanged.connect(self._on_pitch_changed)

        row.addWidget(self._semitones_spin)
        row.addWidget(self._cents_spin)
        row.addStretch()
        return row

    # ------------------------------------------------------------------
    # File / URL loading
    # ------------------------------------------------------------------

    def _open_file(self) -> None:
        # Static method correctly hands off to the native macOS NSOpenPanel
        # (full Finder sidebar, standard single/double-click navigation).
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Audio File",
            self._last_dir,
            "Audio Files (*.wav *.flac *.aif *.aiff *.ogg *.mp3);;All Files (*)",
        )
        if path:
            self._last_dir = str(Path(path).parent)
            self._load_source(FileSource(path))

    def _open_url(self) -> None:
        url, ok = QInputDialog.getText(self, "Open URL", "Paste a YouTube or audio URL:")
        if ok and url.strip():
            self._load_source(URLSource(url.strip()))

    def _load_source(self, source) -> None:
        self._set_loading(True)
        self.engine.stop()
        self._position_timer.stop()

        worker = _Worker(lambda: self.engine.load(source))
        worker.finished.connect(self._on_load_done)
        worker.error.connect(self._on_worker_error)
        self._worker = worker
        worker.start()

    def _on_load_done(self) -> None:
        self._set_loading(False)
        self._set_transport_enabled(True)
        dur = _fmt_time(self.engine.duration_seconds())
        self._dur_label.setText(dur)
        self._status.showMessage(f"Ready — {dur}")
        self._seek_slider.setValue(0)
        self._update_position()
        self._position_timer.start()

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _toggle_play(self) -> None:
        if self.engine.is_playing:
            self.engine.pause()
        else:
            self.engine.play()
        self._sync_play_btn()

    def _on_stop(self) -> None:
        self.engine.pause()
        self._sync_play_btn()

    def _sync_play_btn(self) -> None:
        self._play_btn.setText("⏸" if self.engine.is_playing else "▶")

    def _sync_state(self) -> None:
        """Called every 100 ms. Syncs position label, play button, and seek bar."""
        self._update_position()
        self._sync_play_btn()
        if not self._seeking:
            self._sync_seek_slider()

    def _update_position(self) -> None:
        pos = _fmt_time(self.engine.position_seconds())
        dur = _fmt_time(self.engine.duration_seconds())
        self._position_label.setText(f"{pos} / {dur}")
        # Keep the seek-bar time boxes in sync unless the user is editing them.
        if not self._pos_edit.hasFocus():
            self._pos_edit.setText(pos)
        self._dur_label.setText(dur)

    def _sync_seek_slider(self) -> None:
        raw = self.engine._raw_samples
        if raw > 0:
            val = int(self.engine._input_pos / raw * 10000)
            self._seek_slider.blockSignals(True)
            self._seek_slider.setValue(val)
            self._seek_slider.blockSignals(False)

    # ------------------------------------------------------------------
    # Seek bar
    # ------------------------------------------------------------------

    def _on_seek_pressed(self) -> None:
        self._seeking = True

    def _on_seek_released(self) -> None:
        self._seeking = False
        raw = self.engine._raw_samples
        if raw > 0:
            sample = int(self._seek_slider.value() / 10000 * raw)
            self.engine.seek(sample)

    def _on_pos_edit_committed(self) -> None:
        """Parse a typed M:SS time and seek to it."""
        text = self._pos_edit.text().strip()
        try:
            if ":" in text:
                parts = text.split(":", 1)
                seconds = int(parts[0]) * 60 + float(parts[1])
            else:
                seconds = float(text)
        except ValueError:
            self._pos_edit.setText(_fmt_time(self.engine.position_seconds()))
            return
        sample = int(seconds * self.engine._sample_rate)
        self.engine.seek(sample)
        self._pos_edit.clearFocus()

    # ------------------------------------------------------------------
    # Speed / pitch changes
    # ------------------------------------------------------------------

    def _on_speed_slider_moved(self, value: int) -> None:
        """Keep spinbox in sync while dragging — no reprocess yet."""
        self._speed_spin.blockSignals(True)
        self._speed_spin.setValue(value / 100.0)
        self._speed_spin.blockSignals(False)

    def _on_speed_spin_edited(self) -> None:
        """User typed a value and pressed Enter or tabbed away."""
        value = round(self._speed_spin.value() * 100)
        self._speed_slider.blockSignals(True)
        self._speed_slider.setValue(value)
        self._speed_slider.blockSignals(False)
        self._trigger_reprocess()

    def _on_pitch_changed(self) -> None:
        self._pitch_timer.start()  # resets the 400 ms countdown

    def _trigger_reprocess(self) -> None:
        """Start a Rubberband reprocess with the current UI parameter values."""
        if self.engine._raw is None:
            return

        speed = self._speed_slider.value() / 100.0
        semitones = float(self._semitones_spin.value())
        cents = float(self._cents_spin.value())

        if self._worker and self._worker.isRunning():
            self._pending_params = (speed, semitones, cents)
            return

        self._start_reprocess_worker(speed, semitones, cents)

    def _start_reprocess_worker(self, speed: float, semitones: float, cents: float) -> None:
        self._pending_params = None
        self._status.showMessage("Processing…")
        self._open_btn.setEnabled(False)
        self._url_btn.setEnabled(False)

        worker = _Worker(lambda: self.engine.set_params(speed, semitones, cents))
        worker.finished.connect(self._on_reprocess_done)
        worker.error.connect(self._on_worker_error)
        self._worker = worker
        worker.start()

    def _on_reprocess_done(self) -> None:
        self._open_btn.setEnabled(True)
        self._url_btn.setEnabled(True)
        self._status.showMessage("Ready")

        if self._pending_params is not None:
            self._start_reprocess_worker(*self._pending_params)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_transport_enabled(self, enabled: bool) -> None:
        self._play_btn.setEnabled(enabled)
        self._stop_btn.setEnabled(enabled)
        self._seek_slider.setEnabled(enabled)

    def _set_loading(self, loading: bool) -> None:
        for w in (
            self._open_btn,
            self._url_btn,
            self._speed_slider,
            self._speed_spin,
            self._semitones_spin,
            self._cents_spin,
        ):
            w.setEnabled(not loading)
        if loading:
            self._status.showMessage("Loading…")

    def _on_worker_error(self, msg: str) -> None:
        self._open_btn.setEnabled(True)
        self._url_btn.setEnabled(True)
        QMessageBox.critical(self, "Error", msg)

    def closeEvent(self, event) -> None:
        self.engine.stop()
        super().closeEvent(event)
