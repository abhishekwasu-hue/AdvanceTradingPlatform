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

try:  # AUD-G2.1: the trace id in each JSON log line when a span is active (app/observability/tracing.py)
    from opentelemetry import trace as _otel_trace
except Exception:  # noqa: BLE001 - the package is optional; without it no trace ids are added
    _otel_trace = None

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


# AUD-G1.4 (owner audit 28 Oct; DPDP): no email address or phone number in a log line. An email keeps its first letter
# and its domain ("a***@example.com" - enough to tell accounts apart while debugging); an Indian mobile number (10 digits
# starting 6-9, with or without +91 / 0) becomes "[phone]". Broker order ids (15+ digits) and epoch times are untouched.
_EMAIL = re.compile(r"\b([A-Za-z0-9])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
_PHONE = re.compile(r"(?<![\d+])(?:\+91[\s-]?|0)?[6-9]\d{9}(?!\d)")


def redact_personal(text: str) -> str:
    if not text:
        return text
    text = _EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", text)
    return _PHONE.sub("[phone]", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_personal(record.getMessage()),
        }
        for key, value in (_context.get() or {}).items():
            payload[key] = redact_personal(value) if isinstance(value, str) else value
        payload.update(trace_ids())
        if record.exc_info:
            payload["exc_info"] = redact_personal(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def trace_ids() -> Dict[str, str]:
    """AUD-G2.1: {"trace_id", "span_id"} of the active span (hex, as tracing backends show them); {} when tracing is off
    or no span is active - so a log line can be found from its trace and the other way round."""
    if _otel_trace is None:
        return {}
    try:
        ctx = _otel_trace.get_current_span().get_span_context()
    except Exception:  # noqa: BLE001 - a log line is never lost to tracing
        return {}
    if not ctx.is_valid:
        return {}
    return {"trace_id": format(ctx.trace_id, "032x"), "span_id": format(ctx.span_id, "016x")}


# GS-1: OAuth redirects carry one-time credentials in the query (`code`, `state`; some providers `id_token`,
# `access_token`). uvicorn's access log prints the full path with its query, so those values are blanked there.
# M74: the broker sign-in callbacks add Kite's `request_token` and Fyers' `auth_code`.
_SECRET_QUERY = re.compile(r"(?<=[?&])(code|state|id_token|access_token|refresh_token|appsecret_proof|request_token|auth_code)"
                           r"=[^&#\s]*", re.IGNORECASE)


def redact_query(text: str) -> str:
    return _SECRET_QUERY.sub(lambda m: f"{m.group(1)}=[redacted]", text)


class QueryRedactionFilter(logging.Filter):
    """Blanks OAuth credentials in the request line of an access-log record (its args hold the path), and (M73) in the
    outbound HTTP client's request lines, whose URL arg is an object (httpx.URL), not a str."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and record.args:
            record.args = tuple(redact_query(a) if isinstance(a, str) and "=" in a
                                else redact_query(str(a)) if not isinstance(a, (int, float)) and _SECRET_QUERY.search(str(a))
                                else a for a in record.args)
        elif isinstance(record.msg, str) and "=" in record.msg and not record.args:
            record.msg = redact_query(record.msg)
        return True


def install_access_log_redaction() -> None:
    """Idempotent: adds the filter to uvicorn's access logger once."""
    for name in ("uvicorn.access", "httpx", "httpcore"):     # M73: the client's lines too (Graph appsecret_proof)
        target = logging.getLogger(name)
        if not any(isinstance(f, QueryRedactionFilter) for f in target.filters):
            target.addFilter(QueryRedactionFilter())


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
    install_access_log_redaction()


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
