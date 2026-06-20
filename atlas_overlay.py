"""
atlas_overlay.py — HoloOverlay transparent HUD (PySide6 only).

The HUD is the "GUIDING" surface: it draws non-interactive virtual markers over
the live screen (focus rings, target bounding boxes, trajectory path lines) so
Atlas can teach a task step-by-step WITHOUT ever moving the physical mouse.
Physical automation ("DOING") lives entirely in atlas_core.AtlasHands behind the
permission gate — the two never mix.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QRectF,
    Property,
    QPropertyAnimation,
    QParallelAnimationGroup,
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


class AgentCursorOverlay(QWidget):
    """
    Persistent animated agent cursor — moves smoothly between targets, never
    teleports or disappears between steps.  Click-through, always on top.
    """

    CURSOR_W, CURSOR_H = 48, 48

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput,
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(self.CURSOR_W, self.CURSOR_H)

        self._cursor_x = 100.0
        self._cursor_y = 100.0
        self._pulse = 1.0
        self._opacity = 1.0
        self._move_grp: QParallelAnimationGroup | None = None
        self._pulse_anim: QSequentialAnimationGroup | None = None
        self._on_move_done: Callable | None = None

        screen = QApplication.primaryScreen()
        if screen:
            g = screen.geometry()
            self._cursor_x = g.width() / 2.0
            self._cursor_y = g.height() / 2.0
        self._sync_geometry()

    def get_cursor_x(self) -> float:
        return self._cursor_x

    def set_cursor_x(self, v: float) -> None:
        self._cursor_x = v
        self._sync_geometry()
        self.update()

    cursor_x = Property(float, get_cursor_x, set_cursor_x)

    def get_cursor_y(self) -> float:
        return self._cursor_y

    def set_cursor_y(self, v: float) -> None:
        self._cursor_y = v
        self._sync_geometry()
        self.update()

    cursor_y = Property(float, get_cursor_y, set_cursor_y)

    def get_pulse(self) -> float:
        return self._pulse

    def set_pulse(self, v: float) -> None:
        self._pulse = v
        self.update()

    pulse = Property(float, get_pulse, set_pulse)

    def _sync_geometry(self) -> None:
        self.move(
            int(self._cursor_x - self.CURSOR_W / 2),
            int(self._cursor_y - self.CURSOR_H / 2),
        )

    def show_cursor(self) -> None:
        if not self.isVisible():
            self.show()
        self._opacity = 1.0
        self.update()

    def animate_to(
        self,
        x: int,
        y: int,
        duration_ms: int = 250,
        on_done=None,
    ) -> None:
        """Smooth move to screen coordinates (center of target)."""
        self.show_cursor()
        if self._move_grp:
            self._move_grp.stop()
        if self._pulse_anim:
            self._pulse_anim.stop()

        self._on_move_done = on_done
        ax = QPropertyAnimation(self, b"cursor_x")
        ax.setDuration(duration_ms)
        ax.setStartValue(self._cursor_x)
        ax.setEndValue(float(x))
        ax.setEasingCurve(QEasingCurve.InOutQuad)
        ay = QPropertyAnimation(self, b"cursor_y")
        ay.setDuration(duration_ms)
        ay.setStartValue(self._cursor_y)
        ay.setEndValue(float(y))
        ay.setEasingCurve(QEasingCurve.InOutQuad)

        grp = QParallelAnimationGroup(self)
        grp.addAnimation(ax)
        grp.addAnimation(ay)

        def _finished() -> None:
            cb = self._on_move_done
            self._on_move_done = None
            if cb:
                cb()

        grp.finished.connect(_finished)
        self._move_grp = grp
        grp.start()

    def pulse_click(self, on_done=None) -> None:
        """Brief scale pulse when a click lands."""
        up = QPropertyAnimation(self, b"pulse")
        up.setDuration(90)
        up.setStartValue(1.0)
        up.setEndValue(1.45)
        up.setEasingCurve(QEasingCurve.OutQuad)
        down = QPropertyAnimation(self, b"pulse")
        down.setDuration(120)
        down.setStartValue(1.45)
        down.setEndValue(1.0)
        down.setEasingCurve(QEasingCurve.InOutQuad)
        self._pulse_anim = QSequentialAnimationGroup(self)
        self._pulse_anim.addAnimation(up)
        self._pulse_anim.addAnimation(down)
        if on_done:
            self._pulse_anim.finished.connect(on_done)
        self._pulse_anim.start()

    def paintEvent(self, _event) -> None:  # noqa: ANN001
        if self._opacity <= 0.01:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self._pulse
        p.setPen(QPen(QColor(0, 212, 255, 230), 2.0))
        p.setBrush(QColor(0, 212, 255, 180))
        tip = QPointF(self.CURSOR_W / 2, 8 * s)
        p.drawPolygon([
            QPointF(tip.x(), tip.y()),
            QPointF(tip.x() - 10 * s, tip.y() + 22 * s),
            QPointF(tip.x(), tip.y() + 16 * s),
            QPointF(tip.x() + 10 * s, tip.y() + 22 * s),
        ])
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(212, 175, 55, 220))
        p.drawEllipse(QPointF(tip.x(), tip.y() + 6 * s), 3 * s, 3 * s)
