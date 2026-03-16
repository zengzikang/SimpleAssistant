from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QObject, pyqtSignal, QTimer


class ClipboardMonitor(QObject):
    """Polls the system clipboard and emits text_changed when content changes."""

    text_changed = pyqtSignal(str)

    def __init__(self, parent=None, interval_ms: int = 600):
        super().__init__(parent)
        self._clipboard = QApplication.clipboard()
        self._last_text = self._clipboard.text()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._check)
        self._timer.start(interval_ms)

    def _check(self):
        current = self._clipboard.text()
        if current != self._last_text:
            self._last_text = current
            if current.strip():
                self.text_changed.emit(current)

    def current_text(self) -> str:
        return self._clipboard.text()

    def set_text(self, text: str):
        self._clipboard.setText(text)
        self._last_text = text
