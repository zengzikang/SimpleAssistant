from typing import List, Dict, Optional, Callable


class LLMClient:
    def __init__(self, config):
        self.config = config

    def _provider(self) -> str:
        return self.config.get("llm", "provider") or "openai"

    def chat(
        self,
        messages: List[Dict],
        stream_callback: Optional[Callable[[str], None]] = None,
    ) -> str:
        provider = self._provider()
        if provider == "anthropic":
            return self._chat_anthropic(messages, stream_callback)
        else:
            return self._chat_openai(messages, stream_callback)

    # ── OpenAI / Ollama ───────────────────────────────────────────────────────

    def _chat_openai(
        self,
        messages: List[Dict],
        stream_callback: Optional[Callable] = None,
    ) -> str:
        import openai

        api_key     = self.config.get("llm", "api_key")     or ""
        base_url    = self.config.get("llm", "base_url")    or None
        model       = self.config.get("llm", "model")       or "gpt-4o"
        temperature = float(self.config.get("llm", "temperature") or 0.7)
        max_tokens  = int(self.config.get("llm", "max_tokens")    or 2000)

        # Advanced parameters
        top_p              = float(self.config.get("llm", "top_p")              or 0.8)
        presence_penalty   = float(self.config.get("llm", "presence_penalty")   or 1.5)
        top_k              = int(self.config.get("llm", "top_k")                or 20)
        repetition_penalty = float(self.config.get("llm", "repetition_penalty") or 1.0)
        enable_thinking    = bool(self.config.get("llm", "enable_thinking")      or False)

        # Build extra_body (for vLLM / Qwen / compatible back-ends)
        extra_body: Dict = {
            "chat_template_kwargs": {"enable_thinking": enable_thinking},
        }
        if top_k > 0:
            extra_body["top_k"] = top_k
        if repetition_penalty != 1.0:
            extra_body["repetition_penalty"] = repetition_penalty

        client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url if base_url else None,
        )

        common_kwargs = dict(
            model=model,
            messages=messages,
            temperature=temperature,
            top_p=top_p,
            presence_penalty=presence_penalty,
            max_tokens=max_tokens,
            extra_body=extra_body,
        )

        if stream_callback:
            result = ""
            with client.chat.completions.create(**common_kwargs, stream=True) as stream:
                for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        delta = chunk.choices[0].delta.content
                        result += delta
                        stream_callback(delta)
            return result
        else:
            response = client.chat.completions.create(**common_kwargs)
            return response.choices[0].message.content or ""

    # ── Anthropic ─────────────────────────────────────────────────────────────

    def _chat_anthropic(
        self,
        messages: List[Dict],
        stream_callback: Optional[Callable] = None,
    ) -> str:
        import anthropic

        api_key    = self.config.get("llm", "api_key")    or ""
        model      = self.config.get("llm", "model")      or "claude-sonnet-4-6"
        max_tokens = int(self.config.get("llm", "max_tokens") or 2000)

        system_msgs = [m for m in messages if m["role"] == "system"]
        user_msgs   = [m for m in messages if m["role"] != "system"]
        system      = system_msgs[0]["content"] if system_msgs else ""

        client = anthropic.Anthropic(api_key=api_key)
        kwargs: Dict = dict(model=model, max_tokens=max_tokens, messages=user_msgs)
        if system:
            kwargs["system"] = system

        if stream_callback:
            result = ""
            with client.messages.stream(**kwargs) as stream:
                for text in stream.text_stream:
                    result += text
                    stream_callback(text)
            return result
        else:
            response = client.messages.create(**kwargs)
            return response.content[0].text
