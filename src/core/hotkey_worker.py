"""
Worker thread for the full Right-Alt pipeline:
  audio bytes  →  ASR  →  LLM process_voice_command  →  paste result
"""

from PyQt5.QtCore import QThread, pyqtSignal


class HotkeyWorker(QThread):
    status_update  = pyqtSignal(str)   # short message for overlay
    asr_done       = pyqtSignal(str)   # transcribed voice text
    chunk_received = pyqtSignal(str)   # LLM streaming chunk
    finished       = pyqtSignal(str)   # final result (full text)
    error          = pyqtSignal(str)

    def __init__(self, audio_bytes: bytes, selected_text: str, processor, config):
        super().__init__()
        self.audio_bytes   = audio_bytes
        self.selected_text = selected_text
        self.processor     = processor
        self.config        = config

    def run(self):
        try:
            # ── 1. ASR ────────────────────────────────────────────────────────
            self.status_update.emit("正在转写语音…")
            from src.core.asr_client import ASRClient

            text = ASRClient(self.config).transcribe(self.audio_bytes)
            if not text or not text.strip():
                raise ValueError("语音转写失败，请检查 ASR 配置或网络")

            text = text.strip()
            self.asr_done.emit(text)

            # ── 2. LLM smart dispatch ─────────────────────────────────────────
            self.status_update.emit("正在处理…")
            result = self.processor.process_voice_command(
                voice_text=text,
                selected_text=self.selected_text,
                stream_callback=self._on_chunk,
            )
            if not result:
                raise ValueError("LLM 返回空结果")

            # ── 3. Copy to clipboard + simulate Ctrl+V ────────────────────────
            from src.core.clipboard_util import set_clipboard, simulate_paste
            set_clipboard(result)
            simulate_paste(delay_ms=150)

            self.finished.emit(result)

        except Exception as e:
            self.error.emit(str(e))

    def _on_chunk(self, chunk: str):
        self.chunk_received.emit(chunk)
