"""
Floating overlay — three states:
  RECORDING   waveform animation + "录音中…"
  PROCESSING  animated dots + status text
  DONE        tick mark + brief message → auto-hide after 1.5 s
"""

import math

from PyQt5.QtCore import Qt, QTimer, pyqtSlot
from PyQt5.QtGui import QPainter, QColor, QBrush, QFont
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QApplication


# ── Waveform widget ────────────────────────────────────────────────────────────

class WaveformWidget(QWidget):
    BARS = 20
    FPS  = 30

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(220, 52)
        self._level  = 0.0
        self._smooth = [0.08] * self.BARS
        self._phase  = 0.0
        self._timer  = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000 // self.FPS)

    @pyqtSlot(float)
    def set_level(self, level: float):
        self._level = max(0.0, min(1.0, level))

    def reset(self):
        self._level  = 0.0
        self._smooth = [0.08] * self.BARS
        self._phase  = 0.0

    def _tick(self):
        self._phase += 0.25
        for i in range(self.BARS):
            idle   = 0.07 + 0.05 * math.sin(self._phase + i * 0.55)
            audio  = self._level * (0.75 + 0.25 * math.sin(self._phase * 1.8 + i * 0.9))
            target = max(idle, audio)
            self._smooth[i] += (target - self._smooth[i]) * 0.35
        self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        spacing = w / self.BARS
        bar_w   = max(3.0, spacing * 0.55)

        for i, lv in enumerate(self._smooth):
            bar_h = max(4, int(h * 0.92 * lv))
            x = int(i * spacing + (spacing - bar_w) / 2)
            y = (h - bar_h) // 2
            t = min(1.0, lv / 0.6)
            painter.setBrush(QBrush(QColor(int(59 + t * 30), int(130 + t * 50), 246, 230)))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(x, y, int(bar_w), bar_h, int(bar_w / 2), int(bar_w / 2))
        painter.end()


# ── Overlay ────────────────────────────────────────────────────────────────────

_FONT = QFont("Microsoft YaHei", 13)


class RecordingOverlay(QWidget):
    STATE_RECORDING   = "rec"
    STATE_PROCESSING  = "proc"
    STATE_DONE        = "done"

    def __init__(self, parent=None):
        flags = Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool
        super().__init__(parent, flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)   # ← never steal focus

        self._state      = self.STATE_RECORDING
        self._dot_count  = 0
        self._base_msg   = ""

        self._dot_timer = QTimer(self)
        self._dot_timer.timeout.connect(self._tick_dots)

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 14, 22, 14)
        layout.setSpacing(8)

        self.waveform = WaveformWidget(self)
        layout.addWidget(self.waveform, alignment=Qt.AlignCenter)

        self.label = QLabel("🎙  录音中…  松开 Alt 停止")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setFont(_FONT)
        self.label.setStyleSheet("color: #e2e8f0;")
        layout.addWidget(self.label)

        self.setFixedSize(280, 108)

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(15, 23, 42, 218))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(self.rect(), 14, 14)
        painter.end()

    # ── State transitions ─────────────────────────────────────────────────────

    def show_recording(self):
        self._dot_timer.stop()
        self._state = self.STATE_RECORDING
        self.waveform.reset()
        self.waveform.setVisible(True)
        self.label.setText("🎙  录音中…  松开 Alt 停止")
        self._place_and_show()

    def show_processing(self, message: str = "正在处理"):
        self._state    = self.STATE_PROCESSING
        self._base_msg = message
        self._dot_count = 0
        self.waveform.setVisible(False)
        self.label.setText(f"⟳  {message}…")
        self._dot_timer.start(450)
        self._place_and_show()

    def show_done(self, message: str = "已完成，已粘贴"):
        self._dot_timer.stop()
        self._state = self.STATE_DONE
        self.waveform.setVisible(False)
        self.label.setText(f"✓  {message}")
        self._place_and_show()
        QTimer.singleShot(1600, self.hide)

    def hide_overlay(self):
        self._dot_timer.stop()
        self.hide()
        self.waveform.set_level(0.0)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @pyqtSlot(float)
    def set_level(self, level: float):
        self.waveform.set_level(level)

    def set_status(self, text: str):
        if self._state == self.STATE_PROCESSING:
            self._base_msg = text
            self._dot_count = 0
            self.label.setText(f"⟳  {text}…")

    def _tick_dots(self):
        self._dot_count = (self._dot_count % 3) + 1
        self.label.setText(f"⟳  {self._base_msg}" + "." * self._dot_count)

    def _place_and_show(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            screen.x() + (screen.width() - self.width()) // 2,
            screen.y() + screen.height() - self.height() - 64,
        )
        self.show()
        self.raise_()
