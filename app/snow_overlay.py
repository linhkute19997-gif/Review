"""
Snowflake Overlay — Decorative snowflake animation.
"""
import random
import math
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QPainter, QColor


class SnowflakeOverlay(QWidget):
    """Animated snowflake overlay for the main window."""

    def __init__(self, parent=None, count=80):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")
        self._flakes = []
        self._count = count
        self._init_snowflakes()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_positions)
        self._timer.start(33)  # ~30 fps

    def _init_snowflakes(self):
        self._flakes = [self._make_flake() for _ in range(self._count)]

    def _make_flake(self):
        return {
            'x': random.randint(0, max(self.width(), 100)),
            'y': random.randint(-50, max(self.height(), 100)),
            'size': random.uniform(2, 6),
            'speed': random.uniform(0.5, 2.5),
            'drift': random.uniform(-0.5, 0.5),
            'opacity': random.randint(100, 220),
        }

    def _update_positions(self):
        w, h = self.width(), self.height()
        for f in self._flakes:
            f['y'] += f['speed']
            f['x'] += f['drift'] + math.sin(f['y'] / 30) * 0.3
            if f['y'] > h:
                f['y'] = -10
                f['x'] = random.randint(0, max(w, 1))
            if f['x'] < 0:
                f['x'] = w
            elif f['x'] > w:
                f['x'] = 0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for f in self._flakes:
            color = QColor(255, 255, 255, f['opacity'])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(int(f['x']), int(f['y']),
                               int(f['size']), int(f['size']))
        painter.end()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        for f in self._flakes:
            if f['x'] > self.width():
                f['x'] = random.randint(0, max(self.width(), 1))
