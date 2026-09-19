from __future__ import annotations

import logging
import re
from collections import deque
from datetime import datetime, timezone
from threading import Lock
from typing import Any


SECRET_PATTERN = re.compile(
    r"(?i)(api[_-]?key|password|pass[_-]?token|service[_-]?token|bootstrap[_-]?token|setup[_-]?token|authorization|cookie)(\s*[:=]\s*)([^\s,;}]+)"
)

_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_DOUBLE_BACKSLASH_U = re.compile(r"\\\\u([0-9a-fA-F]{4})")


def _decode_unicode_escapes(text: str) -> str:
    """Decode Python repr-style \\uXXXX escapes into real characters."""
    text = _DOUBLE_BACKSLASH_U.sub(lambda m: chr(int(m.group(1), 16)), text)
    text = _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text)
    return text


class MemoryLogHandler(logging.Handler):
    def __init__(self, capacity: int = 1000):
        super().__init__()
        self.records: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._next_id = 1
        self._lock = Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            message = _decode_unicode_escapes(message)
            message = SECRET_PATTERN.sub(r"\1\2***", message)
            item = {
                "id": self._next_id,
                "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "device": getattr(record, "device", None),
                "message": message,
            }
            with self._lock:
                self._next_id += 1
                self.records.append(item)
        except Exception:
            self.handleError(record)

    def list(self, level: str | None = None, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self.records)
        if level:
            items = [item for item in items if item["level"] == level.upper()]
        return [item for item in items if item["id"] > after][-limit:]

    def clear(self) -> None:
        with self._lock:
            self.records.clear()


memory_logs = MemoryLogHandler()
memory_logs.setFormatter(logging.Formatter("%(message)s"))


def configure_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if memory_logs not in root.handlers:
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(stream)
        root.addHandler(memory_logs)
