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
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

_context: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar("log_context", default=None)


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in (_context.get() or {}).items():
            setattr(record, key, value)
        return True


# UI2-5: a broker's OAuth redirect carries a one-time login code (and the signed state) in its query string. The access
# log (uvicorn) and HTTP client logs print request URLs, so on the callback paths the query values are replaced before a
# line is written - the code never reaches a log.
# Any path ending in /oauth/callback (with or without a trailing slash, under any prefix such as /api/v1/...): a
# prefix or slash a proxy or a later API version adds must not let the code through.
_CALLBACK_QUERY = re.compile(r"(/oauth/callback/?\?)([^\s\"']*)")
_CALLBACK_MARK = "/oauth/callback"
SECRET_QUERY_LOGGERS = ("uvicorn.access", "httpx", "httpcore")


def _redact_callback_query(text: str) -> str:
    return _CALLBACK_QUERY.sub(lambda m: m.group(1) + "[redacted]", text)


class CallbackQueryRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str) and _CALLBACK_MARK in record.msg:
            record.msg = _redact_callback_query(record.msg)
        if record.args:
            args = record.args if isinstance(record.args, tuple) else (record.args,)
            if any(_CALLBACK_MARK in str(a) for a in args):
                record.args = tuple(_redact_callback_query(str(a)) if _CALLBACK_MARK in str(a) else a for a in args)
        return True


def install_callback_query_redaction() -> None:
    """Idempotent: adds the filter once to each logger that prints request URLs."""
    for name in SECRET_QUERY_LOGGERS:
        target = logging.getLogger(name)
        if not any(isinstance(f, CallbackQueryRedactionFilter) for f in target.filters):
            target.addFilter(CallbackQueryRedactionFilter())


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
    install_callback_query_redaction()


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
