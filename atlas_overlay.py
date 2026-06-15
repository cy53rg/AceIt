"""
atlas_overlay.py — HoloOverlay transparent HUD (PySide6 only).

The HUD is the "GUIDING" surface: it draws non-interactive virtual markers over
the live screen (focus rings, target bounding boxes, trajectory path lines) so
Atlas can teach a task step-by-step WITHOUT ever moving the physical mouse.
Physical automation ("DOING") lives entirely in atlas_core.AtlasHands behind the
permission gate — the two never mix.
"""
from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QRectF,
    Property,
    QPropertyAnimation,
    QSequentialAnimationGroup,
    Qt,
    QTimer,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget


class HoloOverlay(QWidget):
    """Full-screen transparent overlay with animated focus ring."""

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowTransparentForInput
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool,
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        screen = QApplication.primaryScreen()
        if screen:
            self.setGeometry(screen.geometry())

        self._ring_pos = QPointF(0, 0)
        self._ring_scale = 1.0
        self._ring_opacity = 0.0
        self._anims: list[QPropertyAnimation] = []
        self._seq: QSequentialAnimationGroup | None = None
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._start_fade_out)

        # GUIDING marker state (drawn alongside the focus ring).
        self._box: QRectF | None = None     # highlighted target bounding box
        self._label: str = ""               # caption shown above the box/ring
        self._path: list[QPointF] = []       # trajectory vector path points
        self._marker_timer = QTimer(self)
        self._marker_timer.setSingleShot(True)
        self._marker_timer.timeout.connect(self.clear_markers)

    def get_ring_pos(self) -> QPointF:
        return self._ring_pos

    def set_ring_pos(self, value: QPointF) -> None:
        self._ring_pos = value
        self.update()

    ring_pos = Property(QPointF, get_ring_pos, set_ring_pos)

    def get_ring_scale(self) -> float:
        return self._ring_scale

    def set_ring_scale(self, value: float) -> None:
        self._ring_scale = value
        self.update()

    ring_scale = Property(float, get_ring_scale, set_ring_scale)

    def get_ring_opacity(self) -> float:
        return self._ring_opacity

    def set_ring_opacity(self, value: float) -> None:
        self._ring_opacity = value
        self.update()

    ring_opacity = Property(float, get_ring_opacity, set_ring_opacity)

    def _cancel_anims(self) -> None:
        self._hide_timer.stop()
        for anim in self._anims:
            anim.stop()
        self._anims.clear()
        if self._seq is not None:
            self._seq.stop()
            self._seq = None

    def focus_on(self, x: int, y: int, w: int, h: int) -> None:
        """Animate ring to center of target rect, pulse, auto-hide after 3s."""
        self._cancel_anims()
        target = QPointF(x + w / 2.0, y + h / 2.0)
        if not self.isVisible():
            self.show()

        fade_in = QPropertyAnimation(self, b"ring_opacity")
        fade_in.setDuration(250)
        fade_in.setStartValue(self._ring_opacity)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.InOutCubic)

        move = QPropertyAnimation(self, b"ring_pos")
        move.setDuration(800)
        move.setStartValue(self._ring_pos)
        move.setEndValue(target)
        move.setEasingCurve(QEasingCurve.InOutQuad)

        self._anims.extend([fade_in, move])
        fade_in.start()
        move.start()

        def _pulse() -> None:
            up = QPropertyAnimation(self, b"ring_scale")
            up.setDuration(200)
            up.setStartValue(1.0)
            up.setEndValue(1.3)
            up.setEasingCurve(QEasingCurve.OutBack)
            down = QPropertyAnimation(self, b"ring_scale")
            down.setDuration(200)
            down.setStartValue(1.3)
            down.setEndValue(1.0)
            down.setEasingCurve(QEasingCurve.InOutCubic)
            self._seq = QSequentialAnimationGroup(self)
            self._seq.addAnimation(up)
            self._seq.addAnimation(down)
            self._seq.finished.connect(lambda: self._hide_timer.start(3000))
            self._seq.start()

        move.finished.connect(_pulse)

    # ── GUIDING markers (no mouse control — purely visual tutorial overlay) ────

    def mark_target(
        self, x: int, y: int, w: int, h: int,
        label: str = "", hold_ms: int = 4000,
    ) -> None:
        """
        Highlight a UI target with a bounding box + focus ring + optional label.

        Used by GUIDING mode to point at a control while a voice tutorial plays —
        Atlas never clicks it for the user, it shows them where to go.
        """
        self._marker_timer.stop()
        self._box = QRectF(float(x), float(y), float(max(1, w)), float(max(1, h)))
        self._label = str(label or "")
        self.focus_on(x, y, w, h)
        if hold_ms > 0:
            self._marker_timer.start(hold_ms)

    def draw_path(self, points: list[tuple[int, int]], hold_ms: int = 4500) -> None:
        """Draw a trajectory vector path (connected arrowed line) over the screen."""
        self._marker_timer.stop()
        self._path = [QPointF(float(px), float(py)) for px, py in points]
        if self._ring_opacity <= 0.01:
            self.set_ring_opacity(1.0)
        if not self.isVisible():
            self.show()
        self.update()
        if hold_ms > 0:
            self._marker_timer.start(hold_ms)

    def clear_markers(self) -> None:
        """Drop boxes/paths/labels and fade the HUD out."""
        self._box = None
        self._label = ""
        self._path = []
        self._start_fade_out()

    def _start_fade_out(self) -> None:
        fade = QPropertyAnimation(self, b"ring_opacity")
        fade.setDuration(500)
        fade.setStartValue(self._ring_opacity)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.InOutCubic)
        fade.finished.connect(self.hide)
        self._anims.append(fade)
        fade.start()

    def paintEvent(self, _event) -> None:  # noqa: ANN001
        if self._ring_opacity <= 0.01:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

        cx, cy = self._ring_pos.x(), self._ring_pos.y()
        scale = self._ring_scale
        alpha_outer = int(255 * self._ring_opacity * 0.35)
        alpha_inner = int(255 * self._ring_opacity)

        outer = QColor(0, 212, 255, alpha_outer)
        inner = QColor(0, 212, 255, alpha_inner)
        gold = QColor(212, 175, 55, alpha_inner)

        painter.setPen(QPen(outer, 3.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), scale * 40, scale * 40)

        painter.setPen(QPen(inner, 1.5))
        painter.drawEllipse(QPointF(cx, cy), scale * 28, scale * 28)

        arm = 14
        offset = scale * 36
        painter.setPen(QPen(gold, 2.0))
        for dx, dy in [(-1, -1), (1, -1), (-1, 1), (1, 1)]:
            ox, oy = cx + dx * offset, cy + dy * offset
            painter.drawLine(ox, oy, ox + dx * arm, oy)
            painter.drawLine(ox, oy, ox, oy + dy * arm)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 212, 255, 255))
        painter.drawEllipse(QPointF(cx, cy), 3, 3)

        # ── GUIDING bounding box ──────────────────────────────────────────────
        if self._box is not None:
            box_pen = QPen(QColor(0, 212, 255, alpha_inner), 2.0)
            box_pen.setStyle(Qt.DashLine)
            painter.setPen(box_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(self._box, 6, 6)

        # ── GUIDING caption ───────────────────────────────────────────────────
        if self._label:
            painter.setFont(QFont("Segoe UI", 11, QFont.Bold))
            painter.setPen(QPen(QColor(212, 175, 55, alpha_inner)))
            anchor = self._box if self._box is not None else QRectF(cx - 100, cy - 60, 200, 20)
            painter.drawText(
                QRectF(anchor.left(), anchor.top() - 26, max(220.0, anchor.width()), 22),
                Qt.AlignLeft | Qt.AlignVCenter, self._label,
            )

        # ── GUIDING trajectory path ───────────────────────────────────────────
        if len(self._path) >= 2:
            path_pen = QPen(QColor(212, 175, 55, alpha_inner), 2.5)
            path_pen.setCapStyle(Qt.RoundCap)
            painter.setPen(path_pen)
            for i in range(len(self._path) - 1):
                painter.drawLine(self._path[i], self._path[i + 1])
            # Endpoint node so the destination reads clearly.
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(212, 175, 55, alpha_inner))
            painter.drawEllipse(self._path[-1], 5, 5)
