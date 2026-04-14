"""Section bar — displays and edits detected song structure segments.

Sits above the seek bar. Hidden when no sections are loaded.

The section bar owns its data and all mutations. The window only needs
two signals:
  - sections_changed(list)   — save the new state to the database
  - section_looped(int, int) — seek to start_sample, set loop to end_sample

Interaction
-----------
- Click a section (no drag)          → loop that section
- Drag a boundary (edit mode only)   → adjust the boundary
- Drag elsewhere                     → ignored
- Right-click a section              → context menu (Rename, Merge, Delete)
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QContextMenuEvent, QFont, QMouseEvent, QPainter
from PyQt6.QtWidgets import QInputDialog, QMenu, QPushButton, QWidget

from sounds.models import Section

_BAR_HEIGHT = 24
_BOUNDARY_GRAB_PX = 5
_DRAG_THRESHOLD_PX = 4

_IDLE = 0
_PENDING = 1    # pressed — waiting to confirm click vs drag
_BOUNDARY = 2   # dragging a section boundary
_CANCELLED = 3  # moved without hitting a boundary — ignore until release


def _fmt_time(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"


class SectionBar(QWidget):
    """Colored section band display with full editing support.

    By default (edit_mode=False) any click loops the section under the
    cursor. Boundary dragging is only available when edit_mode=True,
    preventing accidental resizes and making small sections reliably
    clickable.

    Signals
    -------
    section_looped(start_sample, end_sample)
        A section was clicked. Window should seek and set the loop.
    sections_changed(sections)
        Internal sections were mutated. Window should save to DB.
    """

    section_looped = pyqtSignal(int, int)
    sections_changed = pyqtSignal(list)

    _BTN_WIDTH = 48

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sections: list[Section] = []
        self._edit_mode: bool = False
        self._sample_rate: int = 44100
        self._mode: int = _IDLE
        self._press_x: int = 0
        self._drag_boundary: int = -1
        self.setFixedHeight(_BAR_HEIGHT)
        self.setVisible(False)

        self._edit_btn = QPushButton("Edit", self)
        self._edit_btn.setCheckable(True)
        self._edit_btn.setFixedSize(self._BTN_WIDTH, _BAR_HEIGHT - 4)
        self._edit_btn.setToolTip("Toggle section edit mode — enables boundary dragging")
        self._edit_btn.toggled.connect(self._on_edit_toggled)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_sections(self, sections: list[Section]) -> None:
        self._sections = list(sections)
        # Reset edit mode whenever sections are replaced.
        self._edit_btn.setChecked(False)
        self.setVisible(bool(self._sections))
        self.update()

    def sections(self) -> list[Section]:
        return list(self._sections)

    def set_sample_rate(self, sample_rate: int) -> None:
        self._sample_rate = sample_rate

    def resizeEvent(self, a0) -> None:  # noqa: ANN001
        super().resizeEvent(a0)
        bh = self._edit_btn.height()
        self._edit_btn.move(self.width() - self._BTN_WIDTH - 2, (self.height() - bh) // 2)

    def _on_edit_toggled(self, enabled: bool) -> None:
        self._edit_mode = enabled
        self._edit_btn.setText("Done" if enabled else "Edit")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update()

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paintEvent(self, a0) -> None:  # noqa: ANN001
        if not self._sections:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        total = self._sections[-1].end_sample
        if total <= 0:
            return

        font = QFont()
        font.setPointSize(8)
        p.setFont(font)

        for s in self._sections:
            x1 = int(s.start_sample / total * w)
            x2 = int(s.end_sample / total * w)
            band_w = x2 - x1
            if band_w <= 0:
                continue

            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(s.color))
            p.drawRect(x1, 0, band_w - 1, h)

            if band_w > 20:
                p.setPen(QColor(255, 255, 255, 210))
                p.drawText(
                    x1 + 4, 0, band_w - 8, h,
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                    s.label,
                )

        # Boundary dividers — brighter in edit mode to signal they're draggable
        p.setPen(QColor(255, 255, 255, 160) if self._edit_mode else QColor(0, 0, 0, 80))
        for s in self._sections[:-1]:
            bx = int(s.end_sample / total * w)
            p.drawLine(bx, 0, bx, h)

        # Time bubble at the boundary being dragged
        if self._mode == _BOUNDARY and self._drag_boundary >= 0:
            sample = self._sections[self._drag_boundary].end_sample
            bx = int(sample / total * w)
            time_str = _fmt_time(sample / self._sample_rate)

            lbl_font = QFont()
            lbl_font.setPointSize(8)
            lbl_font.setBold(True)
            p.setFont(lbl_font)
            fm = p.fontMetrics()
            pad = 4
            bw = fm.horizontalAdvance(time_str) + pad * 2
            bh = fm.height() + pad
            bx_c = max(0, min(w - bw, bx - bw // 2))
            by = (h - bh) // 2
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(15, 15, 15, 220))
            p.drawRoundedRect(bx_c, by, bw, bh, 3, 3)
            p.setPen(QColor("white"))
            p.drawText(bx_c, by, bw, bh, Qt.AlignmentFlag.AlignCenter, time_str)

    # ------------------------------------------------------------------
    # Mouse interaction
    # ------------------------------------------------------------------

    def mousePressEvent(self, a0: QMouseEvent | None) -> None:
        if a0 is None or a0.button() != Qt.MouseButton.LeftButton:
            return
        x = a0.pos().x()
        self._press_x = x
        if self._edit_mode and self._boundary_near(x) >= 0:
            self._mode = _BOUNDARY
            self._drag_boundary = self._boundary_near(x)
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self._mode = _PENDING

    def mouseMoveEvent(self, a0: QMouseEvent | None) -> None:
        if a0 is None:
            return
        x = a0.pos().x()
        if self._mode == _IDLE:
            near = self._edit_mode and self._boundary_near(x) >= 0
            self.setCursor(
                Qt.CursorShape.SizeHorCursor if near
                else Qt.CursorShape.PointingHandCursor
            )
        elif self._mode == _PENDING:
            if abs(x - self._press_x) >= _DRAG_THRESHOLD_PX:
                self._mode = _CANCELLED
        elif self._mode == _BOUNDARY:
            self._move_boundary(self._drag_boundary, x)

    def mouseReleaseEvent(self, a0: QMouseEvent | None) -> None:
        if a0 is None or a0.button() != Qt.MouseButton.LeftButton:
            return
        x = a0.pos().x()
        mode, self._mode = self._mode, _IDLE
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        if mode == _PENDING:
            idx = self._section_at(x)
            if idx >= 0:
                s = self._sections[idx]
                self.section_looped.emit(s.start_sample, s.end_sample)

        elif mode == _BOUNDARY:
            # Boundary drag complete — finalize and notify.
            new_sample = self._boundary_sample_from_x(self._drag_boundary, x)
            self._sections[self._drag_boundary].end_sample = new_sample
            self._sections[self._drag_boundary + 1].start_sample = new_sample
            self._drag_boundary = -1
            self.sections_changed.emit(self.sections())

    def contextMenuEvent(self, a0: QContextMenuEvent | None) -> None:
        if a0 is None:
            return
        idx = self._section_at(a0.pos().x())
        if idx < 0:
            return
        menu = QMenu(self)
        rename_act = menu.addAction("Rename…")
        menu.addSeparator()
        merge_prev_act = menu.addAction("Merge with Previous") if idx > 0 else None
        merge_next_act = (
            menu.addAction("Merge with Next")
            if idx < len(self._sections) - 1 else None
        )
        menu.addSeparator()
        delete_act = menu.addAction("Delete Section")

        action = menu.exec(a0.globalPos())

        if action == rename_act:
            self._rename(idx)
        elif action == delete_act:
            self._delete(idx)
        elif merge_prev_act and action == merge_prev_act:
            self._merge(idx - 1, idx)
        elif merge_next_act and action == merge_next_act:
            self._merge(idx, idx + 1)

    # ------------------------------------------------------------------
    # Mutations — all changes go through here so sections_changed fires
    # ------------------------------------------------------------------

    def _rename(self, idx: int) -> None:
        name, ok = QInputDialog.getText(
            self, "Rename Section", "Section name:",
            text=self._sections[idx].label,
        )
        if ok and name.strip():
            self._sections[idx].label = name.strip()
            self.update()
            self.sections_changed.emit(self.sections())

    def _delete(self, idx: int) -> None:
        if len(self._sections) == 1:
            self._sections = []
        elif idx == 0:
            self._sections[1].start_sample = self._sections[0].start_sample
            self._sections.pop(0)
        else:
            self._sections[idx - 1].end_sample = self._sections[idx].end_sample
            self._sections.pop(idx)
        self.setVisible(bool(self._sections))
        self.update()
        self.sections_changed.emit(self.sections())

    def _merge(self, left: int, right: int) -> None:
        """Absorb section at right into section at left."""
        self._sections[left].end_sample = self._sections[right].end_sample
        self._sections.pop(right)
        self.update()
        self.sections_changed.emit(self.sections())

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _total_samples(self) -> int:
        return self._sections[-1].end_sample if self._sections else 0

    def _section_at(self, x: int) -> int:
        total = self._total_samples()
        if total <= 0:
            return -1
        w = self.width()
        for i, s in enumerate(self._sections):
            if int(s.start_sample / total * w) <= x < int(s.end_sample / total * w):
                return i
        return -1

    def _boundary_near(self, x: int) -> int:
        total = self._total_samples()
        if total <= 0 or len(self._sections) < 2:
            return -1
        w = self.width()
        for i in range(len(self._sections) - 1):
            if abs(x - int(self._sections[i].end_sample / total * w)) <= _BOUNDARY_GRAB_PX:
                return i
        return -1

    def _move_boundary(self, boundary_idx: int, x: int) -> None:
        new_sample = self._boundary_sample_from_x(boundary_idx, x)
        self._sections[boundary_idx].end_sample = new_sample
        self._sections[boundary_idx + 1].start_sample = new_sample
        self.update()

    def _boundary_sample_from_x(self, boundary_idx: int, x: int) -> int:
        total = self._total_samples()
        if total <= 0:
            return 0
        raw = int(max(0.0, min(1.0, x / self.width())) * total)
        lo = self._sections[boundary_idx].start_sample + 1
        hi = self._sections[boundary_idx + 1].end_sample - 1
        return max(lo, min(hi, raw))
