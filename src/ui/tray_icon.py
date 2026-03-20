"""
TrayIcon — 程序常驻的系统托盘图标，同时也是整个语音处理流程的控制器。

热键监听在 TrayIcon.__init__() 里启动（程序一启动就生效），
而不是等到主界面打开时才初始化。
"""

import time

from PyQt5.QtWidgets import QSystemTrayIcon, QMenu, QAction
from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor, QFont
from PyQt5.QtCore import Qt, QTimer, pyqtSlot

from src.core.context_manager import ContextManager
from src.core.processor import Processor


# ── Tray icon image ───────────────────────────────────────────────────────────

def _make_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#2563EB"))
    p.setPen(Qt.NoPen)
    p.drawEllipse(2, 2, 60, 60)
    p.setPen(QColor("white"))
    f = QFont()
    f.setPixelSize(28)
    f.setBold(True)
    p.setFont(f)
    p.drawText(pixmap.rect(), Qt.AlignCenter, "助")
    p.end()
    return QIcon(pixmap)


# ── TrayIcon ──────────────────────────────────────────────────────────────────

class TrayIcon(QSystemTrayIcon):

    def __init__(self, config, db, app):
        super().__init__()
        self.config = config
        self.db     = db
        self.app    = app

        # Core objects (live here, not in MainWindow)
        self.context_manager = ContextManager(
            max_rounds=int(config.get("context", "max_rounds") or 10),
            max_hours =int(config.get("context", "max_hours")  or 1),
        )
        self.processor = Processor(config, db, self.context_manager)

        # Runtime state
        self._main_window  = None
        self._overlay      = None
        self._overlay_token = 0
        self._recording    = False
        self._recorder     = None
        self._worker       = None

        # Build tray
        self.setIcon(_make_icon())
        self.setToolTip("简单助手  —  单击右 Alt 开始/停止录音")
        self._build_menu()
        self.activated.connect(self._on_activated)

        # ── 热键监听：程序启动后立即生效 ──────────────────────────────────────
        self._setup_hotkey()

    # ── Tray menu ─────────────────────────────────────────────────────────────

    def _build_menu(self):
        menu = QMenu()

        open_act = QAction("打开主界面", self)
        open_act.triggered.connect(self._show_main_window)
        menu.addAction(open_act)

        menu.addSeparator()

        quit_act = QAction("退出", self)
        quit_act.triggered.connect(self.app.quit)
        menu.addAction(quit_act)

        self.setContextMenu(menu)

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self._show_main_window()

    def _show_main_window(self):
        if self._main_window is None:
            from src.ui.main_window import MainWindow
            self._main_window = MainWindow(self.config, self.db,
                                           self.context_manager, self.processor)
        w = self._main_window
        if w.isVisible():
            w.raise_()
            w.activateWindow()
        else:
            w.show()
            w.raise_()
            w.activateWindow()

    # ── Hotkey setup ──────────────────────────────────────────────────────────

    def _setup_hotkey(self):
        from src.core.hotkey_listener import HotkeyListener
        self._hotkey = HotkeyListener()
        self._hotkey.record_started.connect(self._on_hotkey_press)
        self._hotkey.record_stopped.connect(self._on_hotkey_release)
        ok = self._hotkey.start()
        if ok:
            print("[TrayIcon] 全局热键监听已启动 (右 Alt)")
        else:
            print("[TrayIcon] 全局热键监听启动失败")
            self.showMessage("简单助手",
                             "全局热键 (右Alt) 不可用，请检查 pynput / 系统权限",
                             QSystemTrayIcon.Warning, 4000)

    def _get_overlay(self):
        if self._overlay is None:
            from src.ui.recording_overlay import RecordingOverlay
            self._overlay = RecordingOverlay()
        return self._overlay

    # ── Hotkey handlers ───────────────────────────────────────────────────────

    @pyqtSlot()
    def _on_hotkey_press(self):
        """右 Alt 按下 → 响蜂鸣 + 开始录音（无论主界面是否打开）"""
        if self._recording:
            return

        from src.core.recorder import play_beep, AudioRecorder

        play_beep(880, 80)

        overlay = self._get_overlay()
        try:
            self._recorder = AudioRecorder()
            self._recorder.start(level_callback=overlay.set_level)
            self._recording = True
        except Exception as exc:
            overlay.show_processing(f"录音失败: {exc}")
            print(f"[TrayIcon] 录音启动失败: {exc}")
            return

        # Delay the overlay until clipboard capture finishes (or times out).
        # Chromium/Electron editors rely on synthetic Ctrl+C; if our floating
        # window appears too early, they can lose focus and the capture path
        # fails, which is why selection currently only works in Notepad++.
        self._overlay_token += 1
        self._show_recording_overlay_when_ready(
            self._overlay_token,
            deadline=time.monotonic() + 1.2,
        )
        print("[TrayIcon] 录音已开始")

    @pyqtSlot()
    def _on_hotkey_release(self):
        """右 Alt 松开 → 停止录音 → ASR + LLM → 粘贴"""
        if not self._recording:
            return

        from src.core.recorder import play_beep
        play_beep(660, 80)
        self._recording = False

        audio_bytes = self._recorder.stop() if self._recorder else None
        self._recorder = None
        print(f"[TrayIcon] 录音已停止，音频大小: {len(audio_bytes) if audio_bytes else 0} bytes")

        overlay = self._get_overlay()

        if not audio_bytes:
            overlay.hide_overlay()
            print("[TrayIcon] 未录到音频")
            return

        asr_url = self.config.get("asr", "url") or ""
        if not asr_url:
            overlay.show_done("请先在设置中配置 ASR 地址")
            self._show_main_window()
            return

        selected_text = self._hotkey.captured_text
        print(f"[TrayIcon] 选中文字: {repr(selected_text[:40])}")

        overlay.show_processing("正在转写语音")
        self._run_pipeline(audio_bytes, selected_text)

    def _show_recording_overlay_when_ready(self, token: int, deadline: float):
        if token != self._overlay_token or not self._recording:
            return
        if self._hotkey.wait_for_capture(0):
            self._get_overlay().show_recording()
            return
        if time.monotonic() >= deadline:
            print("[TrayIcon] 选中文本捕获超时，继续显示录音浮层")
            self._get_overlay().show_recording()
            return
        QTimer.singleShot(
            40,
            lambda: self._show_recording_overlay_when_ready(token, deadline),
        )

    def _run_pipeline(self, audio_bytes: bytes, selected_text: str):
        from src.core.hotkey_worker import HotkeyWorker
        worker = HotkeyWorker(audio_bytes, selected_text, self.processor, self.config, self.db)
        worker.status_update.connect(self._on_status)
        worker.asr_done.connect(self._on_asr_done)
        worker.finished.connect(self._on_pipeline_done)
        worker.error.connect(self._on_pipeline_error)
        self._worker = worker
        worker.start()

    @pyqtSlot(str)
    def _on_status(self, msg: str):
        self._get_overlay().set_status(msg)
        print(f"[TrayIcon] {msg}")

    @pyqtSlot(str)
    def _on_asr_done(self, text: str):
        self._get_overlay().set_status("正在处理")
        print(f"[TrayIcon] 转写结果: {text[:60]}")

    @pyqtSlot(str)
    def _on_pipeline_done(self, result: str):
        preview = result[:24] + ("…" if len(result) > 24 else "")
        self._get_overlay().show_done(f"已粘贴：{preview}")
        print(f"[TrayIcon] 处理完成: {result[:60]}")

        # Refresh main window history if it's open
        if self._main_window is not None:
            self._main_window.history_updated.emit()

    @pyqtSlot(str)
    def _on_pipeline_error(self, error: str):
        self._get_overlay().show_done(f"失败：{error[:36]}")
        print(f"[TrayIcon] 错误: {error}")
