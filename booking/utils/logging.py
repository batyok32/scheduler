"""Structured logging helpers (no secrets in log messages)."""

from __future__ import annotations

import json
import logging
import traceback
from datetime import date, datetime
from typing import Any


class JsonFormatter(logging.Formatter):
    """Minimal JSON lines formatter for structured logs."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = "".join(
                traceback.format_exception_only(record.exc_info[0], record.exc_info[1])
            ).strip()
        ctx = getattr(record, "ctx", None) or getattr(record, "extra_fields", None)
        if isinstance(ctx, dict):
            payload["context"] = ctx
        return json.dumps(payload, default=_json_default)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError


def log_ctx(**kwargs: Any) -> dict[str, dict[str, Any]]:
    """Structured context for ``logger.info(..., extra=log_ctx(...))``."""
    return {"ctx": kwargs}
