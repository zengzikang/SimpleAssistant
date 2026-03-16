import json
from pathlib import Path
from typing import Any, Dict

DEFAULT_SYSTEM_PROMPT = """你是一个智能语音助手，专门帮助用户处理语音转写的文字。

你的核心任务：
1. 识别用户的真实意图，忽略口误和前后矛盾的表达（以最后说的为准）
2. 删除所有语气词（呃、啊、哦、嗯、那个、就是、然后等填充词）
3. 将口语转化为流畅的书面语
4. 保持用户的核心意思不变

当前热词表（请注意识别这些词汇）：
{hot_words}

用户语言习惯摘要：
{user_habits}"""

DEFAULT_CONFIG: Dict[str, Any] = {
    "asr": {
        "provider": "custom",
        "url": "",
        "api_key": "",
        "model": "",
        "language": "zh",
    },
    "llm": {
        "provider": "openai",
        "api_key": "",
        "base_url": "",
        "model": "gpt-4o",
        "temperature": 0.7,
        "max_tokens": 2000,
        # Advanced / per-model parameters
        "top_p": 0.8,
        "presence_penalty": 1.5,
        "top_k": 20,
        "repetition_penalty": 1.0,
        "enable_thinking": False,
    },
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
    "context": {
        "max_rounds": 10,
        "max_hours": 1,
    },
    "ui": {
        "always_on_top": False,
        "font_size": 13,
    },
}


class ConfigManager:
    def __init__(self):
        self.config_dir = Path.home() / ".simple_assistant"
        self.config_file = self.config_dir / "config.json"
        self.config_dir.mkdir(exist_ok=True)
        self._config = self._load()

    def _load(self) -> Dict[str, Any]:
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                return self._deep_merge(DEFAULT_CONFIG.copy(), saved)
            except Exception:
                pass
        return self._deep_copy(DEFAULT_CONFIG)

    def _deep_copy(self, d: dict) -> dict:
        import copy
        return copy.deepcopy(d)

    def _deep_merge(self, base: dict, override: dict) -> dict:
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                base[key] = self._deep_merge(base[key], value)
            else:
                base[key] = value
        return base

    def save(self):
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(self._config, f, ensure_ascii=False, indent=2)

    def get(self, *keys, default=None):
        val = self._config
        for key in keys:
            if isinstance(val, dict):
                val = val.get(key)
                if val is None:
                    return default
            else:
                return default
        return val

    def set(self, *keys_and_value):
        *keys, value = keys_and_value
        d = self._config
        for key in keys[:-1]:
            d = d.setdefault(key, {})
        d[keys[-1]] = value
        self.save()

    def get_all(self) -> Dict[str, Any]:
        return self._deep_copy(self._config)

    def update(self, data: Dict[str, Any]):
        self._config = self._deep_merge(self._config, data)
        self.save()
