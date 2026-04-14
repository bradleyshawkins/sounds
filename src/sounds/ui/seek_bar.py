"""Custom seek bar widget with section band overlay.

Replaces QSlider in the main window. Draws colored section bands across
the full bar width, then a playhead handle on top. Emits signals for
seek interactions so the window can drive the engine.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QMouseEvent, QPainter
from PyQt6.QtWidgets import QWidget

_HANDLE_RADIUS = 6
_BAR_HEIGHT = 10


class SeekBar(QWidget):
    """Horizontal seek bar with optional colored section bands.

    Signals
    -------
    seek_started()
        Mouse button pressed — the window should stop updating the bar
        position so it doesn't fight the drag.
    seek_requested(fraction)
        Emitted on press and during drag. ``fraction`` is in [0.0, 1.0].
    seek_ended()
        Mouse button released.
    """

    seek_started = pyqtSignal()
    seek_requested = pyqtSignal(float)
    seek_ended = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._position: float = 0.0  # 0.0 – 1.0
        self._sections: list[dict] = []
        self._dragging: bool = False
        self.setMinimumHeight(28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setEnabled(False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_position(self, fraction: float) -> None:
        """Update the playhead position without emitting any signals."""
        self._position = max(0.0, min(1.0, fraction))
        self.update()

    def set_sections(self, sections: list[dict]) -> None:
        """Replace the displayed sections.

        Each dict must have ``start_sample``, ``end_sample``, ``label``,
        ``color`` keys (same schema as the DB and analyzer output).
        """
        self._sections = sections
        self.update()

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paintEvent(self, a0) -> None:  # noqa: ANN001
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        bar_top = (h - _BAR_HEIGHT) // 2
        bar_rect_args = (0, bar_top, w, _BAR_HEIGHT)

        # --- Background track ---
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#3a3a3a") if self.isEnabled() else QColor("#2a2a2a"))
        p.drawRoundedRect(*bar_rect_args, 4, 4)

        # --- Section bands ---
        if self._sections:
            total = self._sections[-1]["end_sample"]
            if total > 0:
                font = QFont()
                font.setPointSize(7)
                p.setFont(font)
                for s in self._sections:
                    x1 = int(s["start_sample"] / total * w)
                    x2 = int(s["end_sample"] / total * w)
                    band_w = x2 - x1
                    if band_w <= 0:
                        continue

                    color = QColor(s["color"])
                    color.setAlphaF(0.75)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(color)
                    # Clip bands to bar bounds with rounded corners on the ends.
                    p.drawRect(x1, bar_top, band_w - 1, _BAR_HEIGHT)

                    # Label — only if the band is wide enough to fit text.
                    if band_w > 28:
                        p.setPen(QColor(255, 255, 255, 180))
                        p.drawText(x1, bar_top, band_w - 1, _BAR_HEIGHT,
                                   Qt.AlignmentFlag.AlignCenter, s["label"])

        # --- Playhead ---
        if self.isEnabled():
            x = int(self._position * w)
            cx = max(_HANDLE_RADIUS, min(w - _HANDLE_RADIUS, x))
            cy = h // 2
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("white"))
            p.drawEllipse(
                cx - _HANDLE_RADIUS, cy - _HANDLE_RADIUS,
                _HANDLE_RADIUS * 2, _HANDLE_RADIUS * 2,
            )

    # ------------------------------------------------------------------
    # Mouse interaction
    # ------------------------------------------------------------------

    def mousePressEvent(self, a0: QMouseEvent | None) -> None:
        if a0 is not None and a0.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self.seek_started.emit()
            self.seek_requested.emit(self._fraction(a0.pos().x()))

    def mouseMoveEvent(self, a0: QMouseEvent | None) -> None:
        if self._dragging and a0 is not None:
            self.seek_requested.emit(self._fraction(a0.pos().x()))

    def mouseReleaseEvent(self, a0: QMouseEvent | None) -> None:
        if a0 is not None and a0.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self.seek_ended.emit()

    def _fraction(self, x: int) -> float:
        return max(0.0, min(1.0, x / self.width()))
