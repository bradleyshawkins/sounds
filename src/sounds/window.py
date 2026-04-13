from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sounds.engine.player import PlaybackEngine
from sounds.engine.sources.file import FileSource
from sounds.engine.sources.url import URLSource
from sounds.library.db import Database
from sounds.library.scanner import FolderScanner


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
        self._db = Database()
        self._scanner: FolderScanner | None = None

        # True while the user is dragging the seek bar so the position timer
        # doesn't fight the drag.
        self._seeking: bool = False

        # Loop A/B points in input-sample space; None means not set.
        self._loop_start_samples: int | None = None
        self._loop_end_samples: int | None = None

        # Debounce for pitch spinboxes — fires 200 ms after the last valueChanged.
        self._pitch_timer = QTimer(self)
        self._pitch_timer.setSingleShot(True)
        self._pitch_timer.setInterval(200)
        self._pitch_timer.timeout.connect(self._apply_pitch)

        # Position display + play-button sync; runs whenever audio is loaded.
        self._position_timer = QTimer(self)
        self._position_timer.setInterval(100)
        self._position_timer.timeout.connect(self._sync_state)

        # Start in ~/Music if it exists, otherwise home.
        music = Path.home() / "Music"
        self._last_dir = str(music if music.is_dir() else Path.home())

        self._build_ui()
        self._set_transport_enabled(False)
        self._reload_library()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setWindowTitle("Sounds")
        self.setMinimumWidth(600)
        self.resize(800, 640)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # Top pane — controls
        controls = QWidget()
        layout = QVBoxLayout(controls)
        layout.setSpacing(10)
        layout.addLayout(self._build_file_row())
        layout.addLayout(self._build_transport_row())
        layout.addLayout(self._build_seek_row())
        layout.addLayout(self._build_loop_row())
        layout.addLayout(self._build_params_grid())
        layout.addStretch()
        splitter.addWidget(controls)

        # Bottom pane — library
        splitter.addWidget(self._build_library_panel())
        splitter.setStretchFactor(0, 0)  # controls: fixed
        splitter.setStretchFactor(1, 1)  # library: grows

        self.setCentralWidget(splitter)

        self._status = QStatusBar()
        self.setStatusBar(self._status)

    def _build_file_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._open_btn = QPushButton("Open File…")
        self._url_btn = QPushButton("Open URL…")
        self._scan_btn = QPushButton("Scan Folder…")
        self._open_btn.clicked.connect(self._open_file)
        self._url_btn.clicked.connect(self._open_url)
        self._scan_btn.clicked.connect(self._scan_folder)
        row.addWidget(self._open_btn)
        row.addWidget(self._url_btn)
        row.addWidget(self._scan_btn)
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

    def _build_params_grid(self) -> QGridLayout:
        """Speed, Semitones, Cents, and Volume rows in a shared grid so all
        sliders are left-aligned and the same width regardless of label length."""
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)  # slider column grows to fill available width

        # --- Speed ---
        self._speed_slider = QSlider(Qt.Orientation.Horizontal)
        self._speed_slider.setRange(10, 190)
        self._speed_slider.setValue(100)
        self._speed_slider.setTickInterval(10)
        self._speed_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._speed_slider.valueChanged.connect(self._on_speed_slider_moved)
        self._speed_slider.sliderReleased.connect(self._apply_speed)

        self._speed_spin = QDoubleSpinBox()
        self._speed_spin.setRange(0.10, 1.90)
        self._speed_spin.setSingleStep(0.01)
        self._speed_spin.setDecimals(2)
        self._speed_spin.setValue(1.00)
        self._speed_spin.setSuffix("×")
        self._speed_spin.setFixedWidth(80)
        self._speed_spin.editingFinished.connect(self._on_speed_spin_edited)

        grid.addWidget(QLabel("Speed:"), 0, 0)
        grid.addWidget(self._speed_slider, 0, 1)
        grid.addWidget(self._speed_spin, 0, 2)

        # --- Semitones ---
        self._semitones_slider = QSlider(Qt.Orientation.Horizontal)
        self._semitones_slider.setRange(-24, 24)
        self._semitones_slider.setValue(0)
        self._semitones_slider.setTickInterval(6)
        self._semitones_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._semitones_slider.valueChanged.connect(self._on_semitones_slider_moved)
        self._semitones_slider.sliderReleased.connect(self._apply_pitch)

        self._semitones_spin = QSpinBox()
        self._semitones_spin.setRange(-24, 24)
        self._semitones_spin.setValue(0)
        self._semitones_spin.setSuffix(" st")
        self._semitones_spin.setToolTip("Semitones (±24)")
        self._semitones_spin.valueChanged.connect(self._on_semitones_spin_changed)

        grid.addWidget(QLabel("Semitones:"), 1, 0)
        grid.addWidget(self._semitones_slider, 1, 1)
        grid.addWidget(self._semitones_spin, 1, 2)

        # --- Cents ---
        self._cents_slider = QSlider(Qt.Orientation.Horizontal)
        self._cents_slider.setRange(-100, 100)
        self._cents_slider.setValue(0)
        self._cents_slider.setTickInterval(25)
        self._cents_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._cents_slider.valueChanged.connect(self._on_cents_slider_moved)
        self._cents_slider.sliderReleased.connect(self._apply_pitch)

        self._cents_spin = QSpinBox()
        self._cents_spin.setRange(-100, 100)
        self._cents_spin.setValue(0)
        self._cents_spin.setSuffix(" ct")
        self._cents_spin.setToolTip("Fine tuning in cents (±100)")
        self._cents_spin.valueChanged.connect(self._on_cents_spin_changed)

        grid.addWidget(QLabel("Cents:"), 2, 0)
        grid.addWidget(self._cents_slider, 2, 1)
        grid.addWidget(self._cents_spin, 2, 2)

        # --- Volume ---
        self._volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(100)
        self._volume_slider.setTickInterval(25)
        self._volume_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._volume_slider.valueChanged.connect(self._on_volume_changed)

        self._volume_label = QLabel("100%")
        self._volume_label.setFixedWidth(40)

        grid.addWidget(QLabel("Volume:"), 3, 0)
        grid.addWidget(self._volume_slider, 3, 1)
        grid.addWidget(self._volume_label, 3, 2)

        return grid

    def _build_library_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        # Search bar
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search title, artist, or album…")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.textChanged.connect(self._filter_library)
        layout.addWidget(self._search_edit)

        self._library_table = QTableWidget(0, 5)
        self._library_table.setHorizontalHeaderLabels(
            ["Title", "Artist", "Album", "Duration", "Path"]
        )
        self._library_table.horizontalHeader().setStretchLastSection(True)
        self._library_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self._library_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self._library_table.setSortingEnabled(True)
        self._library_table.doubleClicked.connect(self._on_library_double_click)
        layout.addWidget(self._library_table)

        return panel

    def _build_loop_row(self) -> QHBoxLayout:
        self._loop_btn = QPushButton("⟲ Loop")
        self._loop_btn.setCheckable(True)
        self._loop_btn.setToolTip("Enable A/B loop")
        self._loop_btn.toggled.connect(self._on_loop_toggled)

        self._loop_a_edit = QLineEdit("—")
        self._loop_a_edit.setFixedWidth(52)
        self._loop_a_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._loop_a_edit.setToolTip("Loop start (A) — type M:SS or press Set")
        self._loop_a_edit.editingFinished.connect(self._on_loop_a_edited)

        self._loop_b_edit = QLineEdit("—")
        self._loop_b_edit.setFixedWidth(52)
        self._loop_b_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._loop_b_edit.setToolTip("Loop end (B) — type M:SS or press Set")
        self._loop_b_edit.editingFinished.connect(self._on_loop_b_edited)

        self._set_a_btn = QPushButton("Set A")
        self._set_a_btn.setToolTip("Mark current position as loop start")
        self._set_a_btn.clicked.connect(self._on_set_loop_a)

        self._set_b_btn = QPushButton("Set B")
        self._set_b_btn.setToolTip("Mark current position as loop end")
        self._set_b_btn.clicked.connect(self._on_set_loop_b)

        row = QHBoxLayout()
        row.addWidget(self._loop_btn)
        row.addSpacing(8)
        row.addWidget(QLabel("A:"))
        row.addWidget(self._loop_a_edit)
        row.addWidget(self._set_a_btn)
        row.addSpacing(8)
        row.addWidget(QLabel("B:"))
        row.addWidget(self._loop_b_edit)
        row.addWidget(self._set_b_btn)
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
        self._reset_loop()
        dur = _fmt_time(self.engine.duration_seconds())
        self._dur_label.setText(dur)
        self._status.showMessage(f"Ready — {dur}")
        self._seek_slider.setValue(0)
        self._update_position()
        self._position_timer.start()

    # ------------------------------------------------------------------
    # Library / scanning
    # ------------------------------------------------------------------

    def _scan_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Select Folder to Scan", self._last_dir
        )
        if not folder:
            return
        self._last_dir = folder
        self._scan_btn.setEnabled(False)
        self._status.showMessage("Scanning…")

        self._scanner = FolderScanner(folder, self._db)
        self._scanner.progress.connect(self._on_scan_progress)
        self._scanner.finished.connect(self._on_scan_finished)
        self._scanner.error.connect(self._on_scan_error)
        self._scanner.start()

    def _on_scan_progress(self, current: int, total: int, filename: str) -> None:
        self._status.showMessage(f"Scanning {current}/{total} — {filename}")

    def _on_scan_finished(self, added: int, skipped: int) -> None:
        self._scan_btn.setEnabled(True)
        self._status.showMessage(
            f"Scan complete — {added} added/updated, {skipped} unchanged"
        )
        self._reload_library()

    def _on_scan_error(self, msg: str) -> None:
        self._scan_btn.setEnabled(True)
        QMessageBox.critical(self, "Scan Error", msg)

    def _reload_library(self) -> None:
        tracks = self._db.all_tracks()
        self._library_table.setSortingEnabled(False)
        self._library_table.setRowCount(len(tracks))
        for row, track in enumerate(tracks):
            duration = (
                _fmt_time(track["duration"]) if track["duration"] else ""
            )
            for col, value in enumerate([
                track["title"] or "",
                track["artist"] or "",
                track["album"] or "",
                duration,
                track["uri"],
            ]):
                item = QTableWidgetItem(value)
                item.setToolTip(track["uri"])
                self._library_table.setItem(row, col, item)
        self._library_table.setSortingEnabled(True)
        self._library_table.resizeColumnToContents(0)
        self._library_table.resizeColumnToContents(1)
        self._library_table.resizeColumnToContents(2)
        self._library_table.resizeColumnToContents(3)

    def _filter_library(self, text: str) -> None:
        """Show only rows whose title, artist, or album contain the search text."""
        query = text.strip().lower()
        for row in range(self._library_table.rowCount()):
            if query:
                match = any(
                    query in (self._library_table.item(row, col) or QTableWidgetItem()).text().lower()
                    for col in (0, 1, 2)  # Title, Artist, Album
                )
            else:
                match = True
            self._library_table.setRowHidden(row, not match)

    def _on_library_double_click(self) -> None:
        row = self._library_table.currentRow()
        if row < 0:
            return
        path_item = self._library_table.item(row, 4)
        if path_item:
            self._load_source(FileSource(path_item.text()))

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
        secs = self._parse_time(self._pos_edit.text())
        if secs is None:
            self._pos_edit.setText(_fmt_time(self.engine.position_seconds()))
            return
        self.engine.seek(int(secs * self.engine._sample_rate))
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
        self._apply_speed()

    def _on_semitones_slider_moved(self, value: int) -> None:
        self._semitones_spin.blockSignals(True)
        self._semitones_spin.setValue(value)
        self._semitones_spin.blockSignals(False)
        self._pitch_timer.start()

    def _on_semitones_spin_changed(self, value: int) -> None:
        self._semitones_slider.blockSignals(True)
        self._semitones_slider.setValue(value)
        self._semitones_slider.blockSignals(False)
        self._pitch_timer.start()

    def _on_cents_slider_moved(self, value: int) -> None:
        self._cents_spin.blockSignals(True)
        self._cents_spin.setValue(value)
        self._cents_spin.blockSignals(False)
        self._pitch_timer.start()

    def _on_cents_spin_changed(self, value: int) -> None:
        self._cents_slider.blockSignals(True)
        self._cents_slider.setValue(value)
        self._cents_slider.blockSignals(False)
        self._pitch_timer.start()

    def _on_volume_changed(self, value: int) -> None:
        self._volume_label.setText(f"{value}%")
        self.engine.volume = value / 100.0

    def _apply_speed(self) -> None:
        self.engine.speed = self._speed_slider.value() / 100.0

    def _apply_pitch(self) -> None:
        self.engine.semitones = float(self._semitones_spin.value())
        self.engine.cents = float(self._cents_spin.value())

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Loop controls
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_time(text: str) -> float | None:
        """Parse M:SS or raw seconds string into seconds. Returns None if invalid."""
        text = text.strip()
        try:
            if ":" in text:
                parts = text.split(":", 1)
                return int(parts[0]) * 60 + float(parts[1])
            return float(text)
        except (ValueError, IndexError):
            return None

    def _on_loop_toggled(self, _: bool) -> None:
        self._update_engine_loop()

    def _on_set_loop_a(self) -> None:
        self._loop_start_samples = self.engine._input_pos
        self._loop_a_edit.setText(_fmt_time(self.engine.position_seconds()))
        self._update_engine_loop()

    def _on_set_loop_b(self) -> None:
        self._loop_end_samples = self.engine._input_pos
        self._loop_b_edit.setText(_fmt_time(self.engine.position_seconds()))
        self._update_engine_loop()

    def _on_loop_a_edited(self) -> None:
        secs = self._parse_time(self._loop_a_edit.text())
        if secs is None:
            # Revert to last known good value
            self._loop_a_edit.setText(
                _fmt_time(self._loop_start_samples / self.engine._sample_rate)
                if self._loop_start_samples is not None else "—"
            )
            return
        self._loop_start_samples = int(secs * self.engine._sample_rate)
        self._loop_a_edit.setText(_fmt_time(secs))
        self._update_engine_loop()

    def _on_loop_b_edited(self) -> None:
        secs = self._parse_time(self._loop_b_edit.text())
        if secs is None:
            self._loop_b_edit.setText(
                _fmt_time(self._loop_end_samples / self.engine._sample_rate)
                if self._loop_end_samples is not None else "—"
            )
            return
        self._loop_end_samples = int(secs * self.engine._sample_rate)
        self._loop_b_edit.setText(_fmt_time(secs))
        self._update_engine_loop()

    def _update_engine_loop(self) -> None:
        """Push the current loop state to the engine."""
        if not self._loop_btn.isChecked():
            self.engine.set_loop(None, None)
            return
        if self._loop_start_samples is None or self._loop_end_samples is None:
            self.engine.set_loop(None, None)
            return
        start = self._loop_start_samples
        end = self._loop_end_samples
        if start > end:
            start, end = end, start
        self.engine.set_loop(start, end)

    def _reset_loop(self) -> None:
        """Clear loop state when a new file is loaded."""
        self._loop_start_samples = None
        self._loop_end_samples = None
        self._loop_btn.setChecked(False)
        self._loop_a_edit.setText("—")
        self._loop_b_edit.setText("—")
        self.engine.set_loop(None, None)

    def _set_transport_enabled(self, enabled: bool) -> None:
        for w in (
            self._play_btn,
            self._stop_btn,
            self._seek_slider,
            self._loop_btn,
            self._set_a_btn,
            self._set_b_btn,
            self._loop_a_edit,
            self._loop_b_edit,
        ):
            w.setEnabled(enabled)

    def _set_loading(self, loading: bool) -> None:
        for w in (
            self._open_btn,
            self._url_btn,
            self._speed_slider,
            self._speed_spin,
            self._semitones_slider,
            self._semitones_spin,
            self._cents_slider,
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
        self.engine.close()
        self._db.close()
        super().closeEvent(event)
