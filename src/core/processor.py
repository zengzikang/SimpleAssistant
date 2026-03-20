from typing import Optional, List, Dict, Callable

from .llm_client import LLMClient
from .context_manager import ContextManager
from src.config.manager import DEFAULT_PROMPT_TEMPLATES

# ── Prompt templates ──────────────────────────────────────────────────────────

# Used for the main hotkey flow: voice command + optional selected text
SMART_DISPATCH_PROMPT = """你是一个语音指令处理专家，帮助用户处理语音输入并执行相应操作。

【语音输入（已转写）】
{voice_text}

{selected_section}

【热词表】
{hot_words}

【处理规则】
如果存在「选中文字」，请根据语音指令对选中文字执行操作：
- 语音含"翻译"→ 翻译（如"翻译成英语"则译为英语，否则默认译为英语）
- 语音含"润色"、"优化"、"改好一点"→ 润色优化
- 语音含"续写"、"继续写"、"接着写"→ 续写（保持风格）
- 语音含"纠错"、"改错"、"检查"→ 纠正错误
- 语音含"总结"、"摘要"→ 总结为要点
- 其他自然语言指令 → 尽力理解并执行

如果没有「选中文字」，将语音内容整理为规范书面语：
- 识别真实意图（以最后说的为准，忽略口误）
- 删除语气词（呃、啊、哦、嗯、那个、就是等）
- 转化为流畅书面语，保持原意

只输出处理后的文字结果，不要添加任何解释、标签或说明。"""

# ── Legacy single-operation prompts (for manual buttons) ─────────────────────

CLEAN_PROMPT = """请处理以下语音转写文字：
1. 识别真实意图（以最后说的为准，忽略口误）
2. 删除语气词（呃、啊、哦、嗯、那个、就是等）
3. 转化为流畅书面语，保持原意
只输出处理后的文字：

{text}"""

CUSTOM_PROMPT = "请按以下指令处理文字。\n指令：{instruction}\n\n文字：\n{text}"


class Processor:
    def __init__(self, config, db, context_manager: ContextManager):
        self.config = config
        self.db = db
        self.context = context_manager
        self.llm = LLMClient(config)

    def _system_message(self) -> str:
        return self.config.get("system_prompt") or ""

    def _messages(self, user_prompt: str, include_context: bool = True) -> List[Dict]:
        msgs: List[Dict] = []
        sys_msg = self._system_message()
        if sys_msg:
            msgs.append({"role": "system", "content": sys_msg})
        if include_context:
            msgs.extend(self.context.get_messages())
        msgs.append({"role": "user", "content": user_prompt})
        return msgs

    def _prompt_template(self, key: str) -> str:
        return self.config.get("prompt_templates", key) or DEFAULT_PROMPT_TEMPLATES[key]

    def _process_selected_text(
        self,
        voice_text: str,
        selected_text: str,
        stream_callback: Optional[Callable] = None,
    ) -> str:
        prompt = self._prompt_template("selected_text_operation").format(
            voice_text=voice_text,
            selected_text=selected_text,
        )
        return self.llm.chat(self._messages(prompt, True), stream_callback)

    # ── Main hotkey pipeline entry point ─────────────────────────────────────

    def process_voice_command(
        self,
        voice_text: str,
        selected_text: str = "",
        stream_callback: Optional[Callable] = None,
    ) -> str:
        """
        Smart dispatch: analyse the voice command and apply it to selected_text
        (if any) or to the voice text itself.
        """
        if selected_text.strip():
            result = self._process_selected_text(
                voice_text=voice_text,
                selected_text=selected_text,
                stream_callback=stream_callback,
            )
        else:
            selected_section = "【选中文字】\n（无，请直接整理语音内容）"
            hot_words = self.db.get_hot_words()
            hot_words_str = "、".join(hot_words) if hot_words else "（暂无）"
            prompt = SMART_DISPATCH_PROMPT.format(
                voice_text=voice_text,
                selected_section=selected_section,
                hot_words=hot_words_str,
            )
            msgs = self._messages(prompt, include_context=True)
            result = self.llm.chat(msgs, stream_callback)

        # Store in context and history
        context_label = f"[语音指令] {voice_text}"
        if selected_text.strip():
            context_label += f"\n[选中] {selected_text[:80]}"
        self.context.add(context_label, result)
        self.db.add_history(
            action="voice_command",
            original=voice_text,
            processed=result,
            context=selected_text,
        )
        return result

    # ── Legacy manual-button methods ──────────────────────────────────────────

    def clean_voice_text(self, text: str, stream_callback: Optional[Callable] = None) -> str:
        result = self.llm.chat(self._messages(CLEAN_PROMPT.format(text=text)), stream_callback)
        self.context.add(f"[整理] {text}", result)
        self.db.add_history("clean", text, result)
        return result

    def translate(self, text: str, target_lang: str = "英语",
                  stream_callback: Optional[Callable] = None) -> str:
        result = self.llm.chat(
            self._messages(
                f"请将以下文字翻译为{target_lang}，保持原文语气。只输出翻译结果：\n\n{text}",
                False,
            ),
            stream_callback,
        )
        self.db.add_history(f"translate_{target_lang}", text, result)
        return result

    def polish(self, text: str, stream_callback: Optional[Callable] = None) -> str:
        result = self.llm.chat(
            self._messages(f"请润色以下文字，使其更流畅专业。只输出润色后的文字：\n\n{text}", False),
            stream_callback,
        )
        self.db.add_history("polish", text, result)
        return result

    def continue_text(self, text: str, stream_callback: Optional[Callable] = None) -> str:
        result = self.llm.chat(
            self._messages(
                f"请续写以下文字，保持相同风格和主题。只输出续写内容（不重复原文）：\n\n{text}",
                False,
            ),
            stream_callback,
        )
        self.db.add_history("continue", text, result)
        return result

    def correct(self, text: str, stream_callback: Optional[Callable] = None) -> str:
        result = self.llm.chat(
            self._messages(
                f"请纠正以下文字中的所有错误（错别字、语法、标点）。只输出纠正后的文字：\n\n{text}",
                False,
            ),
            stream_callback,
        )
        self.db.add_history("correct", text, result)
        return result

    def custom(self, text: str, instruction: str = "",
               stream_callback: Optional[Callable] = None) -> str:
        prompt = CUSTOM_PROMPT.format(text=text, instruction=instruction)
        result = self.llm.chat(self._messages(prompt, True), stream_callback)
        self.context.add(f"[自定义] {instruction}\n{text}", result)
        self.db.add_history("custom", f"{instruction}\n{text}", result)
        return result
