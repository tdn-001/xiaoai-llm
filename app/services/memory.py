from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Session:
    messages: list[dict[str, str]] = field(default_factory=list)
    updated_at: float = 0


class ConversationMemory:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def build_messages(
        self,
        did: str,
        system_prompt: str,
        query: str,
        enabled: bool,
        timeout_minutes: int,
        max_tokens: int,
    ) -> list[dict[str, str]]:
        now = time.time()
        session = self._sessions.setdefault(did, Session())
        if session.updated_at and now - session.updated_at > timeout_minutes * 60:
            session.messages.clear()
        history = session.messages if enabled else []
        messages = [{"role": "system", "content": system_prompt}, *history, {"role": "user", "content": query}]
        return self._trim(messages, max_tokens)

    def remember(self, did: str, query: str, answer: str, enabled: bool) -> None:
        if not enabled:
            return
        session = self._sessions.setdefault(did, Session())
        session.messages.extend(
            [
                {"role": "user", "content": query},
                {"role": "assistant", "content": answer},
            ]
        )
        session.updated_at = time.time()

    def clear(self, did: str | None = None) -> None:
        if did is None:
            self._sessions.clear()
        else:
            self._sessions.pop(did, None)

    @staticmethod
    def _estimate_tokens(message: dict[str, str]) -> int:
        # Conservative approximation that works for Chinese and Latin text without
        # coupling the gateway to a model-specific tokenizer.
        return max(1, len(message["content"]) // 2) + 8

    def _trim(self, messages: list[dict[str, str]], max_tokens: int) -> list[dict[str, str]]:
        if len(messages) <= 2:
            return messages
        system, current = messages[0], messages[-1]
        history = messages[1:-1]
        budget = max_tokens - self._estimate_tokens(system) - self._estimate_tokens(current)
        kept: list[dict[str, str]] = []
        for message in reversed(history):
            cost = self._estimate_tokens(message)
            if cost > budget:
                break
            kept.append(message)
            budget -= cost
        kept.reverse()
        if kept and kept[0]["role"] == "assistant":
            kept.pop(0)
        return [system, *kept, current]
