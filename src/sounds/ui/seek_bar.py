"""Seek bar — clean playhead widget with no section awareness."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QMouseEvent, QPainter
from PyQt6.QtWidgets import QWidget

_HANDLE_RADIUS = 6
_BAR_HEIGHT = 6


class SeekBar(QWidget):
    """Simple horizontal seek bar.

    Signals
    -------
    seek_started()
    seek_requested(fraction)   0.0–1.0, emitted on press and during drag
    seek_ended()
    """

    seek_started = pyqtSignal()
    seek_requested = pyqtSignal(float)
    seek_ended = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._position: float = 0.0
        self._dragging: bool = False
        self.setMinimumHeight(24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setEnabled(False)

    def set_position(self, fraction: float) -> None:
        self._position = max(0.0, min(1.0, fraction))
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
        filled_w = int(self._position * w)

        p.setPen(Qt.PenStyle.NoPen)

        # Track background
        p.setBrush(QColor("#3a3a3a") if self.isEnabled() else QColor("#2a2a2a"))
        p.drawRoundedRect(0, bar_top, w, _BAR_HEIGHT, 3, 3)

        # Played portion
        if filled_w > 0 and self.isEnabled():
            p.setBrush(QColor("#6a6a6a"))
            p.drawRoundedRect(0, bar_top, filled_w, _BAR_HEIGHT, 3, 3)

        # Playhead handle
        if self.isEnabled():
            cx = max(_HANDLE_RADIUS, min(w - _HANDLE_RADIUS, filled_w))
            cy = h // 2
            p.setBrush(QColor("white"))
            p.drawEllipse(
                cx - _HANDLE_RADIUS, cy - _HANDLE_RADIUS,
                _HANDLE_RADIUS * 2, _HANDLE_RADIUS * 2,
            )

    # ------------------------------------------------------------------
    # Mouse
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
