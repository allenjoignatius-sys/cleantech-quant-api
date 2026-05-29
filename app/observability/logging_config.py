"""
Structured JSON logging + per-request correlation IDs.

Emits one JSON object per log line (timestamp, level, logger, message, request_id,
plus any ``extra={...}`` fields), which ships cleanly into CloudWatch / Datadog /
Loki without a regex grok stage. A contextvar carries the request id so every log
line within a request is correlated automatically.
"""
from __future__ import annotations

import contextvars
import datetime as _dt
import json
import logging
import sys
import uuid
from typing import Any, Dict, Optional

request_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "request_id", default=None
)

# Standard LogRecord attributes we don't want to duplicate into the JSON body.
_RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {
    "args", "msg", "levelname", "levelno", "pathname", "filename", "module",
    "exc_info", "exc_text", "stack_info", "lineno", "funcName", "created",
    "msecs", "relativeCreated", "thread", "threadName", "processName",
    "process", "name", "taskName",
}


def new_request_id() -> str:
    return uuid.uuid4().hex


def set_request_id(value: Optional[str]) -> str:
    rid = value or new_request_id()
    request_id_var.set(rid)
    return rid


class JsonFormatter(logging.Formatter):
    """Render a LogRecord as a single JSON line."""

    def __init__(self, service: str = "cleantech-quant-api") -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": _dt.datetime.fromtimestamp(record.created, _dt.timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": self.service,
            "message": record.getMessage(),
        }
        rid = request_id_var.get()
        if rid:
            payload["request_id"] = rid
        # Merge structured extras passed via logger.info(..., extra={...})
        for k, v in record.__dict__.items():
            if k not in _RESERVED and not k.startswith("_"):
                payload[k] = v
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", service: str = "cleantech-quant-api",
                      stream=None) -> None:
    """Install the JSON formatter on the root logger (idempotent)."""
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter(service=service))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # Tame noisy libraries
    for noisy in ("uvicorn.access", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
