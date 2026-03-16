"""
Main window — history viewer + settings entry point.

Layout:
  ┌─────────────────┬────────────────────────────────────┐
  │  历史记录        │  detail panel                      │
  │  [entry …]      │  时间 / 操作 / 语音输入 / 选中 / 结果│
  └─────────────────┴────────────────────────────────────┘

Voice processing happens invisibly via global Right-Alt hotkey (managed by TrayIcon).
"""

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QListWidget, QListWidgetItem,
    QPushButton, QLabel, QTextEdit, QFrame,
    QStatusBar, QApplication, QMessageBox,
)
from PyQt5.QtCore import Qt, pyqtSignal, pyqtSlot, QSize
from PyQt5.QtGui import QFont

# ── Helpers ───────────────────────────────────────────────────────────────────

ACTION_LABELS = {
    "voice_command": "语音指令",
    "clean":         "整理文字",
    "polish":        "润色",
    "correct":       "纠错",
    "continue":      "续写",
    "custom":        "自定义",
}


def _action_label(action: str) -> str:
    if action.startswith("translate_"):
        return f"翻译→{action[len('translate_'):]}"
    return ACTION_LABELS.get(action, action)


def _divider() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet("color:#e5e7eb;")
    return f


def _section_lbl(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("font-weight:bold; color:#6b7280; font-size:11px;")
    return lbl


STYLE = """
QMainWindow, QWidget { background:#f9fafb; font-family:'Microsoft YaHei'; }
QListWidget {
    border:none; background:#f3f4f6;
    border-right:1px solid #e5e7eb; outline:0;
}
QListWidget::item {
    padding:10px 14px; border-bottom:1px solid #e5e7eb; color:#374151;
}
QListWidget::item:selected {
    background:#dbeafe; color:#1e40af; border-left:3px solid #2563EB;
}
QListWidget::item:hover:!selected { background:#eff6ff; }
QTextEdit {
    border:1px solid #e5e7eb; border-radius:6px;
    padding:6px; background:#ffffff; font-size:13px;
}
QPushButton {
    border-radius:6px; padding:6px 16px; font-size:13px;
    background:#f3f4f6; border:1px solid #d1d5db;
}
QPushButton:hover { background:#e5e7eb; }
QPushButton:disabled { color:#9ca3af; }
QStatusBar { background:#f3f4f6; border-top:1px solid #e5e7eb; font-size:12px; }
"""


class MainWindow(QMainWindow):
    history_updated = pyqtSignal()   # thread-safe: emitted from TrayIcon worker

    def __init__(self, config, db, context_manager, processor):
        super().__init__()
        self.config          = config
        self.db              = db
        self.context_manager = context_manager
        self.processor       = processor

        self._records: list = []

        self._build_ui()
        self._apply_always_on_top()

        self.history_updated.connect(self._reload_history)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.setWindowTitle("简单助手")
        self.setMinimumSize(780, 540)
        self.resize(960, 640)
        self.setStyleSheet(STYLE)

        # Toolbar
        tb = QWidget()
        tb.setStyleSheet("QWidget{background:#fff;border-bottom:1px solid #e5e7eb;}")
        tbl = QHBoxLayout(tb)
        tbl.setContentsMargins(16, 8, 16, 8)

        title = QLabel("简单助手")
        title.setFont(QFont("Microsoft YaHei", 15, QFont.Bold))
        title.setStyleSheet("color:#1e40af; background:transparent; border:none;")
        tbl.addWidget(title)

        hint = QLabel("单击右 Alt 开始录音  •  再次单击停止并自动处理粘贴")
        hint.setStyleSheet("color:#9ca3af; font-size:12px; background:transparent; border:none;")
        tbl.addWidget(hint)
        tbl.addStretch()

        ctx_btn = QPushButton("🗑  清除上下文")
        ctx_btn.clicked.connect(self._clear_context)
        tbl.addWidget(ctx_btn)

        settings_btn = QPushButton("⚙  设置")
        settings_btn.clicked.connect(self._open_settings)
        tbl.addWidget(settings_btn)

        # Splitter
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet("QSplitter::handle{background:#e5e7eb;}")

        # Left — history list
        left = QWidget()
        left.setMinimumWidth(210)
        left.setMaximumWidth(290)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(0)

        lh = QLabel("  历史记录")
        lh.setStyleSheet(
            "background:#e5e7eb; padding:8px 14px; font-weight:bold;"
            " font-size:12px; color:#6b7280; border-bottom:1px solid #d1d5db;"
        )
        ll.addWidget(lh)

        self.history_list = QListWidget()
        self.history_list.setFont(QFont("Microsoft YaHei", 12))
        self.history_list.currentRowChanged.connect(self._on_row_changed)
        ll.addWidget(self.history_list)

        rb = QPushButton("↺  刷新")
        rb.setStyleSheet(
            "QPushButton{border:none;border-top:1px solid #e5e7eb;"
            "padding:8px;border-radius:0;background:#f9fafb;font-size:12px;}"
            "QPushButton:hover{background:#f3f4f6;}"
        )
        rb.clicked.connect(self._reload_history)
        ll.addWidget(rb)

        splitter.addWidget(left)

        # Right — detail
        right = QWidget()
        right.setMinimumWidth(400)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(20, 16, 20, 12)
        rl.setSpacing(8)

        self.placeholder = QLabel("← 从左侧选择一条历史记录查看详情")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setStyleSheet("color:#9ca3af; font-size:14px;")

        self.detail_widget = QWidget()
        dl = QVBoxLayout(self.detail_widget)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(6)

        self.meta_lbl = QLabel()
        self.meta_lbl.setStyleSheet("color:#6b7280; font-size:12px;")
        dl.addWidget(self.meta_lbl)
        dl.addWidget(_divider())

        dl.addWidget(_section_lbl("语音输入"))
        self.voice_edit = QTextEdit()
        self.voice_edit.setReadOnly(True)
        self.voice_edit.setMaximumHeight(76)
        self.voice_edit.setFont(QFont("Microsoft YaHei", 12))
        dl.addWidget(self.voice_edit)

        self.selected_header = _section_lbl("选中文字（上下文）")
        dl.addWidget(self.selected_header)
        self.selected_edit = QTextEdit()
        self.selected_edit.setReadOnly(True)
        self.selected_edit.setMaximumHeight(76)
        self.selected_edit.setFont(QFont("Microsoft YaHei", 12))
        self.selected_edit.setStyleSheet(
            "QTextEdit{background:#fffbeb;border:1px solid #fde68a;}"
        )
        dl.addWidget(self.selected_edit)

        dl.addWidget(_section_lbl("处理结果"))
        self.result_edit = QTextEdit()
        self.result_edit.setReadOnly(True)
        self.result_edit.setFont(QFont("Microsoft YaHei", 13))
        self.result_edit.setStyleSheet(
            "QTextEdit{background:#f0fdf4;border:1px solid #86efac;}"
        )
        dl.addWidget(self.result_edit)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.copy_btn = QPushButton("📋  复制结果")
        self.copy_btn.clicked.connect(self._copy_result)
        btn_row.addWidget(self.copy_btn)
        self.repaste_btn = QPushButton("↩  重新粘贴")
        self.repaste_btn.setToolTip("复制结果到剪贴板并模拟 Ctrl+V")
        self.repaste_btn.clicked.connect(self._repaste_result)
        btn_row.addWidget(self.repaste_btn)
        btn_row.addStretch()
        clr_btn = QPushButton("🗑  清空历史")
        clr_btn.setStyleSheet("QPushButton{color:#DC2626;}")
        clr_btn.clicked.connect(self._clear_history)
        btn_row.addWidget(clr_btn)
        dl.addLayout(btn_row)

        rl.addWidget(self.placeholder)
        rl.addWidget(self.detail_widget)
        self.detail_widget.setVisible(False)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(tb)
        root.addWidget(splitter, 1)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self._set_status("就绪  |  单击右 Alt 开始录音，再次单击停止")

        self._reload_history()

    def _apply_always_on_top(self):
        if self.config.get("ui", "always_on_top"):
            self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

    # ── History ───────────────────────────────────────────────────────────────

    @pyqtSlot()
    def _reload_history(self):
        self._records = self.db.get_history(limit=300)
        self.history_list.clear()
        for rec in self._records:
            time_str = rec["created_at"][:16].replace("T", " ")
            action   = _action_label(rec["action"])
            preview  = (rec["original_text"] or "")[:26].replace("\n", " ")
            item = QListWidgetItem(f"{time_str}\n{action}  {preview}")
            item.setSizeHint(QSize(0, 52))
            self.history_list.addItem(item)

    @pyqtSlot(int)
    def _on_row_changed(self, row: int):
        if row < 0 or row >= len(self._records):
            self.placeholder.setVisible(True)
            self.detail_widget.setVisible(False)
            return

        rec = self._records[row]
        self.placeholder.setVisible(False)
        self.detail_widget.setVisible(True)

        time_str = rec["created_at"][:19].replace("T", " ")
        self.meta_lbl.setText(f"{time_str}  ·  {_action_label(rec['action'])}")
        self.voice_edit.setPlainText(rec["original_text"] or "")

        ctx = (rec.get("context_text") or "").strip()
        self.selected_header.setVisible(bool(ctx))
        self.selected_edit.setVisible(bool(ctx))
        if ctx:
            self.selected_edit.setPlainText(ctx)

        self.result_edit.setPlainText(rec["processed_text"] or "")

    # ── Detail actions ────────────────────────────────────────────────────────

    def _copy_result(self):
        text = self.result_edit.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            self._set_status("已复制到剪贴板")

    def _repaste_result(self):
        text = self.result_edit.toPlainText()
        if text:
            from src.core.clipboard_util import set_clipboard, simulate_paste
            set_clipboard(text)
            simulate_paste(delay_ms=100)
            self._set_status("已重新粘贴")

    def _clear_history(self):
        if QMessageBox.question(
            self, "确认", "确定要清空所有历史记录吗？",
            QMessageBox.Yes | QMessageBox.No,
        ) == QMessageBox.Yes:
            self.db.clear_history()
            self._reload_history()
            self.placeholder.setVisible(True)
            self.detail_widget.setVisible(False)

    # ── Settings / Context ────────────────────────────────────────────────────

    def _clear_context(self):
        self.context_manager.clear()
        self._set_status("上下文已清除")

    def _open_settings(self):
        from src.ui.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self.config, self.db, self)
        if dlg.exec_():
            self.context_manager.update_settings(
                int(self.config.get("context", "max_rounds") or 10),
                int(self.config.get("context", "max_hours")  or 1),
            )
            self._apply_always_on_top()
            self._set_status("设置已保存")

    def _set_status(self, msg: str):
        self.status_bar.showMessage(msg)

    def closeEvent(self, event):  # noqa: N802
        event.ignore()
        self.hide()
