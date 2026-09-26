"""Structured, correlated logging (master prompt Section 49): every log line in the Signal ->
Risk -> Order pipeline should carry `tenant_id`/`strategy_id`/`signal_ref`/`order_id` so a real
incident can be traced end-to-end by grepping (or querying, once shipped to a real log backend)
for one id, instead of reading application code to guess which log lines belong to which request.

There is no dedicated `signal_id` in this codebase - `Signal` (app/core/models.py) is a transient,
in-memory Pydantic value with no DB identity of its own on the paper/live execution path (only
enriched signals persisted via `persist_signal_history` get a row). `signal_ref` (`"{symbol}@
{timestamp}"`) is used instead as the best available stand-in that still uniquely identifies
*which* signal a given log line is about, until/unless every signal gets a real persisted id.

Usage: `with bind_log_context(tenant_id=..., strategy_id=..., signal_ref=...):` around a pipeline
run, then `update_log_context(order_id=...)` once an order exists partway through it - every
`logging` call made by any module for the rest of that `with` block automatically carries all of
it, with no need to pass these ids into every individual log call by hand.
"""
import contextvars
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

_context: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar("log_context", default=None)


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in (_context.get() or {}).items():
            setattr(record, key, value)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in (_context.get() or {}).items():
            payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """Idempotent - safe to call more than once (e.g. once at app startup, again in a worker
    process someday) without stacking duplicate handlers.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(CorrelationFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


class bind_log_context:
    """Context manager: merges `fields` (dropping any that are None) into the correlation
    context for the duration of the `with` block, restoring whatever was there before on exit.
    Nests correctly - an inner `with bind_log_context(...)` only adds to, never replaces, an
    outer one's fields.
    """

    def __init__(self, **fields: Any) -> None:
        self._fields = {k: v for k, v in fields.items() if v is not None}
        self._token: Optional[contextvars.Token] = None

    def __enter__(self) -> "bind_log_context":
        merged = dict(_context.get() or {})
        merged.update(self._fields)
        self._token = _context.set(merged)
        return self

    def __exit__(self, *exc_info: Any) -> None:
        if self._token is not None:
            _context.reset(self._token)


def update_log_context(**fields: Any) -> None:
    """Adds `fields` to the *currently active* context in place (e.g. an `order_id` that only
    exists partway through a `with bind_log_context(...)` block already opened around the whole
    pipeline run) - unlike `bind_log_context`, this has no matching `with` block of its own and
    is not undone until whichever `bind_log_context` is currently active exits.
    """
    merged = dict(_context.get() or {})
    merged.update({k: v for k, v in fields.items() if v is not None})
    _context.set(merged)
