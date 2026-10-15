"""Structured logging: key=value text for terminals, one JSON object per line for services."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

_STANDARD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    return {k: v for k, v in record.__dict__.items() if k not in _STANDARD}


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        fields = " ".join(f"{k}={_quote(v)}" for k, v in _extras(record).items())
        line = f"{ts} {record.levelname:<7} {record.getMessage()}"
        if fields:
            line = f"{line} {fields}"
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **_extras(record),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def _quote(value: Any) -> str:
    text = str(value)
    return json.dumps(text, ensure_ascii=False) if (" " in text or not text) else text


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    root = logging.getLogger("invoice_to_excel")
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
