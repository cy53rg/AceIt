"""
atlas_overlay.py — HoloOverlay transparent focus ring (PySide6 only).
"""
from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    Property,
    QPropertyAnimation,
    QSequentialAnimationGroup,
    Qt,
    QTimer,
)
from PySide6.QtGui import QColor, QPainter, QPen
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
