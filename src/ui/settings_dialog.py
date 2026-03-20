from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QTabWidget,
    QWidget,
    QFormLayout,
    QLineEdit,
    QComboBox,
    QTextEdit,
    QPushButton,
    QLabel,
    QListWidget,
    QSpinBox,
    QDoubleSpinBox,
    QDialogButtonBox,
    QCheckBox,
    QScrollArea,
)
from PyQt5.QtGui import QFont
from PyQt5.QtCore import Qt


class SettingsDialog(QDialog):
    def __init__(self, config, db, parent=None):
        super().__init__(parent)
        self.config = config
        self.db = db
        self.setWindowTitle("设置")
        self.resize(620, 560)
        self._setup_ui()
        self._load_values()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_llm_tab(), "大语言模型")
        self.tabs.addTab(self._build_asr_tab(), "语音识别(ASR)")
        self.tabs.addTab(self._build_prompt_tab(), "系统提示词")
        self.tabs.addTab(self._build_operation_prompts_tab(), "处理提示词")
        self.tabs.addTab(self._build_hotwords_tab(), "热词管理")
        self.tabs.addTab(self._build_context_tab(), "上下文")
        layout.addWidget(self.tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ── LLM tab ───────────────────────────────────────────────────────────────

    def _build_llm_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(10)
        form.setContentsMargins(16, 16, 16, 16)

        self.llm_provider = QComboBox()
        self.llm_provider.addItems(["openai", "ollama", "anthropic"])
        form.addRow("服务商：", self.llm_provider)

        self.llm_api_key = QLineEdit()
        self.llm_api_key.setEchoMode(QLineEdit.Password)
        self.llm_api_key.setPlaceholderText("sk-...")
        form.addRow("API Key：", self.llm_api_key)

        self.llm_base_url = QLineEdit()
        self.llm_base_url.setPlaceholderText(
            "留空则使用默认值；Ollama 填 http://localhost:11434/v1"
        )
        form.addRow("Base URL：", self.llm_base_url)

        self.llm_model = QLineEdit()
        self.llm_model.setPlaceholderText("gpt-4o / claude-sonnet-4-6 / llama3.1 ...")
        form.addRow("模型名称：", self.llm_model)

        self.llm_temperature = QDoubleSpinBox()
        self.llm_temperature.setRange(0.0, 2.0)
        self.llm_temperature.setSingleStep(0.1)
        self.llm_temperature.setDecimals(1)
        form.addRow("Temperature：", self.llm_temperature)

        self.llm_max_tokens = QSpinBox()
        self.llm_max_tokens.setRange(100, 16000)
        self.llm_max_tokens.setSingleStep(100)
        form.addRow("Max Tokens：", self.llm_max_tokens)

        # ── Advanced parameters ────────────────────────────────────────────
        adv_lbl = QLabel("── 高级参数（vLLM / Qwen 等兼容后端）──")
        adv_lbl.setStyleSheet("color:#6b7280; font-size:11px; padding-top:8px;")
        form.addRow("", adv_lbl)

        self.llm_top_p = QDoubleSpinBox()
        self.llm_top_p.setRange(0.0, 1.0)
        self.llm_top_p.setSingleStep(0.05)
        self.llm_top_p.setDecimals(2)
        self.llm_top_p.setToolTip("Top-p (nucleus sampling)，推荐 0.8")
        form.addRow("Top-p：", self.llm_top_p)

        self.llm_presence_penalty = QDoubleSpinBox()
        self.llm_presence_penalty.setRange(-2.0, 2.0)
        self.llm_presence_penalty.setSingleStep(0.1)
        self.llm_presence_penalty.setDecimals(1)
        self.llm_presence_penalty.setToolTip("重复主题惩罚，推荐 1.5")
        form.addRow("Presence Penalty：", self.llm_presence_penalty)

        self.llm_top_k = QSpinBox()
        self.llm_top_k.setRange(0, 200)
        self.llm_top_k.setSingleStep(5)
        self.llm_top_k.setToolTip("Top-k 采样（0 = 不传递此参数），推荐 20")
        form.addRow("Top-k：", self.llm_top_k)

        self.llm_repetition_penalty = QDoubleSpinBox()
        self.llm_repetition_penalty.setRange(0.5, 2.0)
        self.llm_repetition_penalty.setSingleStep(0.05)
        self.llm_repetition_penalty.setDecimals(2)
        self.llm_repetition_penalty.setToolTip("字符级重复惩罚（extra_body），1.0 = 不惩罚")
        form.addRow("Repetition Penalty：", self.llm_repetition_penalty)

        self.llm_enable_thinking = QCheckBox("启用思考模式（enable_thinking）")
        self.llm_enable_thinking.setToolTip(
            "取消勾选可关闭千问 3.5 等模型的思考链输出，减少延迟"
        )
        form.addRow("", self.llm_enable_thinking)

        note = QLabel(
            "<small><b>Ollama</b> 使用 OpenAI 兼容接口，Base URL 填 <code>http://localhost:11434/v1</code>，"
            "API Key 填任意非空字符串即可。<br>"
            "高级参数通过 <code>extra_body</code> 传递，仅 vLLM/Qwen 等兼容后端生效；"
            "官方 OpenAI 接口会忽略不认识的字段。</small>"
        )
        note.setWordWrap(True)
        form.addRow("", note)

        return w

    # ── ASR tab ───────────────────────────────────────────────────────────────

    def _build_asr_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(10)
        form.setContentsMargins(16, 16, 16, 16)

        self.asr_provider = QComboBox()
        self.asr_provider.addItems(["custom", "openai_whisper"])
        form.addRow("服务商类型：", self.asr_provider)

        self.asr_url = QLineEdit()
        self.asr_url.setPlaceholderText("http://your-asr-server/v1/audio/transcriptions")
        form.addRow("API 地址：", self.asr_url)

        self.asr_key = QLineEdit()
        self.asr_key.setEchoMode(QLineEdit.Password)
        self.asr_key.setPlaceholderText("（可选）")
        form.addRow("API Key：", self.asr_key)

        self.asr_model = QLineEdit()
        self.asr_model.setPlaceholderText("whisper-1（可选）")
        form.addRow("模型名称：", self.asr_model)

        self.asr_language = QLineEdit()
        self.asr_language.setPlaceholderText("zh")
        form.addRow("语言代码：", self.asr_language)

        note = QLabel(
            "<small>配置 ASR 服务后，主界面将出现「录音」按钮，可直接录音转写。"
            "也可以使用外部语音输入法后将结果粘贴到主界面。</small>"
        )
        note.setWordWrap(True)
        form.addRow("", note)

        return w

    # ── System prompt tab ─────────────────────────────────────────────────────

    def _build_prompt_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(6)

        layout.addWidget(
            QLabel("系统提示词：")
        )

        self.system_prompt_edit = QTextEdit()
        self.system_prompt_edit.setFont(QFont("Courier New", 12))
        layout.addWidget(self.system_prompt_edit)

        reset_btn = QPushButton("恢复默认提示词")
        reset_btn.clicked.connect(self._reset_prompt)
        layout.addWidget(reset_btn)

        return w

    def _build_operation_prompts_tab(self) -> QWidget:
        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)

        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        self.selected_operation_prompt_edit = self._add_prompt_editor(
            layout, "选中内容处理提示词（支持 {voice_text}、{selected_text}）："
        )

        reset_btn = QPushButton("恢复默认处理提示词")
        reset_btn.clicked.connect(self._reset_operation_prompts)
        layout.addWidget(reset_btn)
        layout.addStretch(1)

        scroll.setWidget(w)
        return container

    # ── Hot words tab ─────────────────────────────────────────────────────────

    def _build_hotwords_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(6)

        layout.addWidget(
            QLabel("热词表（专有名词、人名、品牌、时事热词等）：")
        )

        self.hotword_list = QListWidget()
        layout.addWidget(self.hotword_list)

        row = QHBoxLayout()
        self.new_hotword_input = QLineEdit()
        self.new_hotword_input.setPlaceholderText("输入新热词后按 Enter 或点击「添加」")
        self.new_hotword_input.returnPressed.connect(self._add_hotword)
        row.addWidget(self.new_hotword_input)

        add_btn = QPushButton("添加")
        add_btn.clicked.connect(self._add_hotword)
        row.addWidget(add_btn)

        del_btn = QPushButton("删除选中")
        del_btn.setStyleSheet("color: #DC2626;")
        del_btn.clicked.connect(self._del_hotword)
        row.addWidget(del_btn)

        layout.addLayout(row)
        return w

    # ── Context tab ───────────────────────────────────────────────────────────

    def _build_context_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(10)
        form.setContentsMargins(16, 16, 16, 16)

        self.ctx_max_rounds = QSpinBox()
        self.ctx_max_rounds.setRange(1, 50)
        form.addRow("最大轮数：", self.ctx_max_rounds)

        self.ctx_max_hours = QSpinBox()
        self.ctx_max_hours.setRange(1, 24)
        form.addRow("有效时长（小时）：", self.ctx_max_hours)

        self.ui_always_on_top = QCheckBox("主界面始终置顶")
        form.addRow("", self.ui_always_on_top)

        note = QLabel(
            "<small>系统会将时间窗口内最近 N 轮对话作为上下文发送给 LLM，"
            "帮助其更好地理解您的语言习惯和连续指令。</small>"
        )
        note.setWordWrap(True)
        form.addRow("", note)

        return w

    # ── Load / Save ───────────────────────────────────────────────────────────

    def _load_values(self):
        c = self.config

        # LLM
        self._set_combo(self.llm_provider, c.get("llm", "provider") or "openai")
        self.llm_api_key.setText(c.get("llm", "api_key") or "")
        self.llm_base_url.setText(c.get("llm", "base_url") or "")
        self.llm_model.setText(c.get("llm", "model") or "")
        self.llm_temperature.setValue(float(c.get("llm", "temperature") or 0.7))
        self.llm_max_tokens.setValue(int(c.get("llm", "max_tokens") or 2000))
        self.llm_top_p.setValue(float(c.get("llm", "top_p") or 0.8))
        self.llm_presence_penalty.setValue(float(c.get("llm", "presence_penalty") or 1.5))
        self.llm_top_k.setValue(int(c.get("llm", "top_k") or 20))
        self.llm_repetition_penalty.setValue(float(c.get("llm", "repetition_penalty") or 1.0))
        self.llm_enable_thinking.setChecked(bool(c.get("llm", "enable_thinking") or False))

        # ASR
        self._set_combo(self.asr_provider, c.get("asr", "provider") or "custom")
        self.asr_url.setText(c.get("asr", "url") or "")
        self.asr_key.setText(c.get("asr", "api_key") or "")
        self.asr_model.setText(c.get("asr", "model") or "")
        self.asr_language.setText(c.get("asr", "language") or "zh")

        # Prompt
        self.system_prompt_edit.setPlainText(c.get("system_prompt") or "")
        self.selected_operation_prompt_edit.setPlainText(
            c.get("prompt_templates", "selected_text_operation") or ""
        )

        # Hot words
        self.hotword_list.clear()
        for word in self.db.get_hot_words():
            self.hotword_list.addItem(word)

        # Context
        self.ctx_max_rounds.setValue(int(c.get("context", "max_rounds") or 10))
        self.ctx_max_hours.setValue(int(c.get("context", "max_hours") or 1))
        self.ui_always_on_top.setChecked(bool(c.get("ui", "always_on_top") or False))

    def _save(self):
        self.config.update(
            {
                "llm": {
                    "provider":           self.llm_provider.currentText(),
                    "api_key":            self.llm_api_key.text(),
                    "base_url":           self.llm_base_url.text(),
                    "model":              self.llm_model.text(),
                    "temperature":        self.llm_temperature.value(),
                    "max_tokens":         self.llm_max_tokens.value(),
                    "top_p":              self.llm_top_p.value(),
                    "presence_penalty":   self.llm_presence_penalty.value(),
                    "top_k":              self.llm_top_k.value(),
                    "repetition_penalty": self.llm_repetition_penalty.value(),
                    "enable_thinking":    self.llm_enable_thinking.isChecked(),
                },
                "asr": {
                    "provider": self.asr_provider.currentText(),
                    "url": self.asr_url.text(),
                    "api_key": self.asr_key.text(),
                    "model": self.asr_model.text(),
                    "language": self.asr_language.text() or "zh",
                },
                "system_prompt": self.system_prompt_edit.toPlainText(),
                "prompt_templates": {
                    "selected_text_operation": self.selected_operation_prompt_edit.toPlainText(),
                },
                "context": {
                    "max_rounds": self.ctx_max_rounds.value(),
                    "max_hours": self.ctx_max_hours.value(),
                },
                "ui": {
                    "always_on_top": self.ui_always_on_top.isChecked(),
                },
            }
        )

        # Sync hot words
        existing = set(self.db.get_hot_words())
        current = {
            self.hotword_list.item(i).text()
            for i in range(self.hotword_list.count())
        }
        for word in current - existing:
            self.db.add_hot_word(word)
        for word in existing - current:
            self.db.remove_hot_word(word)

        self.accept()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_combo(self, combo: QComboBox, value: str):
        idx = combo.findText(value)
        combo.setCurrentIndex(max(0, idx))

    def _add_prompt_editor(self, layout: QVBoxLayout, label_text: str) -> QTextEdit:
        layout.addWidget(QLabel(label_text))
        edit = QTextEdit()
        edit.setFont(QFont("Courier New", 11))
        edit.setMinimumHeight(96)
        layout.addWidget(edit)
        return edit

    def _reset_prompt(self):
        from src.config.manager import DEFAULT_SYSTEM_PROMPT
        self.system_prompt_edit.setPlainText(DEFAULT_SYSTEM_PROMPT)

    def _reset_operation_prompts(self):
        from src.config.manager import DEFAULT_PROMPT_TEMPLATES

        self.selected_operation_prompt_edit.setPlainText(
            DEFAULT_PROMPT_TEMPLATES["selected_text_operation"]
        )

    def _add_hotword(self):
        word = self.new_hotword_input.text().strip()
        if not word:
            return
        existing = [self.hotword_list.item(i).text() for i in range(self.hotword_list.count())]
        if word not in existing:
            self.hotword_list.addItem(word)
        self.new_hotword_input.clear()

    def _del_hotword(self):
        row = self.hotword_list.currentRow()
        if row >= 0:
            self.hotword_list.takeItem(row)
