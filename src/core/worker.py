from typing import Callable
from PyQt5.QtCore import QThread, pyqtSignal


class ProcessWorker(QThread):
    """Runs a processor function in a background thread with streaming support."""

    chunk_received = pyqtSignal(str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, func: Callable, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            result = self.func(*self.args, stream_callback=self._on_chunk, **self.kwargs)
            self.finished.emit(result or "")
        except Exception as e:
            self.error.emit(str(e))

    def _on_chunk(self, text: str):
        self.chunk_received.emit(text)
