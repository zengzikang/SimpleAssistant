import io
from typing import Optional


class ASRClient:
    """Sends audio bytes to the configured ASR endpoint and returns transcribed text."""

    def __init__(self, config, db=None):
        self.config = config
        self.db = db

    def transcribe(self, audio_bytes: bytes) -> Optional[str]:
        provider = self.config.get("asr", "provider") or "custom"
        url = self.config.get("asr", "url") or ""
        api_key = self.config.get("asr", "api_key") or ""

        if not url:
            return None

        if provider == "openai_whisper":
            return self._transcribe_openai(audio_bytes, api_key, url)
        else:
            return self._transcribe_custom(audio_bytes, url, api_key)

    def _transcribe_openai(self, audio_bytes: bytes, api_key: str, base_url: str) -> Optional[str]:
        try:
            import openai

            client = openai.OpenAI(api_key=api_key, base_url=base_url)
            audio_file = io.BytesIO(audio_bytes)
            audio_file.name = "audio.wav"
            language = self.config.get("asr", "language") or "zh"
            model = self.config.get("asr", "model") or "whisper-1"
            hot_words = "|".join(self.db.get_hot_words()) if self.db else ""
            print(f"hotwords: {hot_words}")
            extra: dict = {}
            if hot_words:
                extra["hotwords"] = hot_words
            response = client.audio.transcriptions.create(
                model=model,
                file=audio_file,
                language=language,
                extra_body=extra,
            )
            return response.text
        except Exception as e:
            return None

    def _transcribe_custom(self, audio_bytes: bytes, url: str, api_key: str) -> Optional[str]:
        try:
            import requests

            headers = {}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            language = self.config.get("asr", "language") or "zh"
            model = self.config.get("asr", "model") or ""
            data: dict = {"language": language}
            if model:
                data["model"] = model

            files = {"file": ("audio.wav", audio_bytes, "audio/wav")}
            response = requests.post(url, files=files, data=data, headers=headers, timeout=30)
            response.raise_for_status()
            result = response.json()
            return (
                result.get("text")
                or result.get("transcript")
                or result.get("result")
                or None
            )
        except Exception:
            return None
