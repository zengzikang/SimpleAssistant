from datetime import datetime, timedelta
from typing import List, Dict


class ContextManager:
    """Keeps recent conversation turns within a time/count window."""

    def __init__(self, max_rounds: int = 10, max_hours: int = 1):
        self.max_rounds = max_rounds
        self.max_hours = max_hours
        self._history: List[Dict] = []

    def add(self, user_input: str, assistant_output: str):
        self._history.append(
            {
                "timestamp": datetime.now(),
                "user": user_input,
                "assistant": assistant_output,
            }
        )
        self._cleanup()

    def _cleanup(self):
        cutoff = datetime.now() - timedelta(hours=self.max_hours)
        self._history = [h for h in self._history if h["timestamp"] > cutoff]
        if len(self._history) > self.max_rounds:
            self._history = self._history[-self.max_rounds :]

    def get_messages(self) -> List[Dict[str, str]]:
        msgs = []
        for h in self._history:
            msgs.append({"role": "user", "content": h["user"]})
            msgs.append({"role": "assistant", "content": h["assistant"]})
        return msgs

    def clear(self):
        self._history.clear()

    def update_settings(self, max_rounds: int, max_hours: int):
        self.max_rounds = max_rounds
        self.max_hours = max_hours
        self._cleanup()

    @property
    def turn_count(self) -> int:
        return len(self._history)
