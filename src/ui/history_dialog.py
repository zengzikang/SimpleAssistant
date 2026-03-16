from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QPushButton,
    QLabel,
    QTextEdit,
    QHeaderView,
    QMessageBox,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

ACTION_LABELS = {
    "clean": "整理",
    "polish": "润色",
    "correct": "纠错",
    "continue": "续写",
    "custom": "自定义",
}


def _action_label(action: str) -> str:
    if action.startswith("translate_"):
        lang = action[len("translate_"):]
        return f"翻译→{lang}"
    return ACTION_LABELS.get(action, action)


class HistoryDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._records = []
        self.setWindowTitle("历史记录")
        self.resize(860, 580)
        self._setup_ui()
        self._load()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["时间", "操作", "原文（预览）", "结果（预览）"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(self._on_select)
        layout.addWidget(self.table)

        # Detail
        layout.addWidget(QLabel("详细内容："))
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(130)
        self.detail.setFont(QFont("Microsoft YaHei", 12))
        layout.addWidget(self.detail)

        # Buttons
        btn_row = QHBoxLayout()

        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._load)
        btn_row.addWidget(refresh_btn)

        clear_btn = QPushButton("清空历史")
        clear_btn.setStyleSheet("color: #DC2626;")
        clear_btn.clicked.connect(self._clear)
        btn_row.addWidget(clear_btn)

        btn_row.addStretch()

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

    def _load(self):
        self._records = self.db.get_history(limit=300)
        self.table.setRowCount(len(self._records))
        for row, rec in enumerate(self._records):
            time_str = rec["created_at"][:19].replace("T", " ")
            label = _action_label(rec["action"])
            original = (rec["original_text"] or "")[:60].replace("\n", " ")
            processed = (rec["processed_text"] or "")[:60].replace("\n", " ")
            self.table.setItem(row, 0, QTableWidgetItem(time_str))
            self.table.setItem(row, 1, QTableWidgetItem(label))
            self.table.setItem(row, 2, QTableWidgetItem(original))
            self.table.setItem(row, 3, QTableWidgetItem(processed))

    def _on_select(self):
        row = self.table.currentRow()
        if 0 <= row < len(self._records):
            rec = self._records[row]
            self.detail.setPlainText(
                f"【原文】\n{rec['original_text'] or ''}\n\n【结果】\n{rec['processed_text'] or ''}"
            )

    def _clear(self):
        if (
            QMessageBox.question(
                self,
                "确认",
                "确定要清空所有历史记录吗？",
                QMessageBox.Yes | QMessageBox.No,
            )
            == QMessageBox.Yes
        ):
            self.db.clear_history()
            self._load()
            self.detail.clear()
