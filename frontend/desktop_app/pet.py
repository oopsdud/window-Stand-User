"""Resolution-independent default mascot and state-aware imported image animation."""

import math
import time

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from desktop_app.brand import DEFAULT_ART
from desktop_app.settings import asset_path

COLORS = {
    "idle": "#387e6b",
    "thinking": "#6c69b5",
    "working": "#328b7f",
    "paused": "#bd8b48",
    "done": "#38825b",
    "error": "#bf6a62",
}


class PetCanvas(QWidget):
    clicked = Signal()
    dragged = Signal(object)
    drag_finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(100, 100)
        self.state = "idle"
        self.config = {"assets": {}, "motion": "float", "reduce_motion": False}
        self.frames = []
        self.durations = []
        self.art = None
        self.started = time.monotonic()
        self.press = None
        self.moved = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(33)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def configure(self, config):
        self.config = config
        # Assets have content-addressed filenames: only decode when the selected file changes.
        self._load_art()
        self.update()

    def showEvent(self, event):
        self.timer.start(33)
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        dragging = self.press is not None and self.moved
        self.press = None
        if dragging:
            self.drag_finished.emit()
        super().hideEvent(event)

    def set_state(self, state):
        self.state = state
        self._load_art()
        self.update()

    def _load_art(self):
        assets = self.config.get("assets", {})
        selected = assets.get(self.state) or assets.get("idle") or DEFAULT_ART
        if selected == self.art:
            return
        self.art = selected
        self.frames, self.durations = [], []
        path = asset_path(selected)
        if not path:
            return
        try:
            with Image.open(path) as image:
                for index in range(min(120, getattr(image, "n_frames", 1))):
                    image.seek(index)
                    frame = image.convert("RGBA")
                    data = frame.tobytes()
                    qimage = QImage(
                        data,
                        frame.width,
                        frame.height,
                        frame.width * 4,
                        QImage.Format.Format_RGBA8888,
                    ).copy()
                    self.frames.append(QPixmap.fromImage(qimage))
                    self.durations.append(max(40, image.info.get("duration", 100)))
        except (OSError, ValueError):
            self.frames, self.durations = [], []

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        side = min(self.width(), self.height())
        painter.translate((self.width() - side) / 2, (self.height() - side) / 2)
        painter.scale(side / 200, side / 200)
        t = time.monotonic() - self.started
        still = self.config.get("reduce_motion", False)
        phase = 0 if still else t
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(24, 59, 51, 22))
        if not self.frames:
            painter.drawEllipse(QRectF(49, 177, 102, 10))
        bob = 0 if still else math.sin(phase * 2) * 4
        if self.state == "done" and not still:
            bob = -abs(math.sin(phase * 4)) * 9
        if self.config.get("motion") == "bounce" and not still:
            bob = -abs(math.sin(phase * 2.5)) * 11
        painter.translate(100, 110 + bob)
        breath = 1 + (0 if still else math.sin(phase * 2) * 0.015)
        painter.scale(1 / breath, breath)
        if self.config.get("motion") == "float" and not still:
            painter.rotate(math.sin(phase * 1.2) * 2)
        painter.translate(-100, -110)
        if self.frames:
            index = 0
            if not still:
                cursor = (t * 1000) % sum(self.durations)
                for i, duration in enumerate(self.durations):
                    cursor -= duration
                    if cursor < 0:
                        index = i
                        break
            pixmap = self.frames[index]
            scaled = pixmap.size().scaled(166, 166, Qt.AspectRatioMode.KeepAspectRatio)
            rect = QRectF(
                (200 - scaled.width()) / 2, 174 - scaled.height(), scaled.width(), scaled.height()
            )
            painter.drawPixmap(rect, pixmap, QRectF(pixmap.rect()))
        else:
            self._draw_default(painter, phase, still)
        if self.state in {"thinking", "working"}:
            painter.setBrush(QColor(COLORS[self.state]))
            for i in range(3):
                radius = 2.7 + (0 if still else (math.sin(phase * 4 - i) + 1) * 1.2)
                painter.drawEllipse(QPointF(156 + i * 10, 52), radius, radius)
        painter.end()

    def _draw_default(self, painter, phase, still):
        # Little sprout, mint body, dark face and tiny feet: all vector drawn.
        painter.setPen(QPen(QColor("#4a8b70"), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(102, 61), QPointF(105, 35))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#a9cd90"))
        leaf = QPainterPath(QPointF(105, 41))
        leaf.cubicTo(81, 43, 85, 18, 105, 41)
        painter.drawPath(leaf)
        painter.setBrush(QColor("#609b78"))
        leaf = QPainterPath(QPointF(105, 38))
        leaf.cubicTo(108, 16, 134, 25, 105, 38)
        painter.drawPath(leaf)
        painter.setBrush(QColor("#6b9e88"))
        painter.drawRoundedRect(QRectF(58, 158, 27, 19), 9, 9)
        painter.drawRoundedRect(QRectF(115, 158, 27, 19), 9, 9)
        body = QPainterPath(QPointF(39, 109))
        body.cubicTo(35, 73, 49, 54, 65, 64)
        body.cubicTo(85, 53, 117, 53, 136, 65)
        body.cubicTo(155, 50, 167, 77, 162, 110)
        body.cubicTo(174, 150, 151, 169, 101, 170)
        body.cubicTo(48, 170, 26, 150, 39, 109)
        gradient = QLinearGradient(70, 55, 120, 170)
        gradient.setColorAt(0, QColor("#d9edc8"))
        gradient.setColorAt(1, QColor("#8abca3"))
        painter.setBrush(gradient)
        painter.drawPath(body)
        painter.setBrush(QColor("#2f4945"))
        painter.drawRoundedRect(QRectF(50, 87, 101, 57), 25, 25)
        painter.setBrush(QColor("#edf4d9"))
        blink = not still and phase % 5.3 < 0.14
        if self.state == "done" or blink:
            painter.setPen(
                QPen(QColor("#edf4d9"), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            )
            painter.drawArc(QRectF(70, 105, 14, 12), 0, 180 * 16)
            painter.drawArc(QRectF(115, 105, 14, 12), 0, 180 * 16)
        else:
            eye_height = 7 if self.state == "paused" else 16
            painter.drawRoundedRect(QRectF(72, 104, 9, eye_height), 4, 4)
            painter.drawRoundedRect(QRectF(118, 104, 9, eye_height), 4, 4)
        painter.setPen(QPen(QColor("#edf4d9"), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if self.state == "error":
            painter.drawArc(QRectF(94, 121, 13, 10), 0, 180 * 16)
        else:
            painter.drawArc(QRectF(94, 116, 13, 10), 180 * 16, 180 * 16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#dfada0"))
        painter.drawEllipse(QRectF(59, 123, 13, 6))
        painter.drawEllipse(QRectF(129, 123, 13, 6))
        painter.setBrush(QColor("#daeaca"))
        painter.drawEllipse(QRectF(89, 151, 22, 7))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.press = event.globalPosition().toPoint()
            self.moved = False

    def mouseMoveEvent(self, event):
        if self.press is not None:
            current = event.globalPosition().toPoint()
            delta = current - self.press
            if self.moved or delta.manhattanLength() > 4:
                self.moved = True
                self.dragged.emit(delta)
                self.press = current

    def mouseReleaseEvent(self, event):
        if self.press is not None:
            if not self.moved:
                self.clicked.emit()
            else:
                self.drag_finished.emit()
        self.press = None
