"""AUD-G2.1 (owner audit 28 Oct): OpenTelemetry tracing for the API (FastAPI), the database (SQLAlchemy) and outgoing
HTTP calls (httpx - the broker calls). OFF unless configured.

Turned on only when the standard `OTEL_EXPORTER_OTLP_ENDPOINT` (or `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`) is set to an
OTLP/HTTP collector address (from the owner's own collector or tracing account; never in git). Unset = nothing is
initialised, nothing is patched and nothing leaves the server: no exporter, no thread, no network call - the same
behaviour as before this module existed. `OTEL_SDK_DISABLED=true` keeps it off even with an endpoint set.

The other standard variables are read by the SDK itself: `OTEL_SERVICE_NAME` (default `atp-api` / `atp-worker`),
`OTEL_EXPORTER_OTLP_HEADERS` (a collector's auth header - a secret, `.env` only), `OTEL_TRACES_SAMPLER` /
`OTEL_TRACES_SAMPLER_ARG` (default: every trace), `OTEL_PYTHON_FASTAPI_EXCLUDED_URLS` (paths not traced).

Privacy: before a span is recorded, its URL attributes pass the same redaction as the JSON logs (OAuth `code` / `state` /
tokens in a query string, e-mail addresses, phone numbers). SQL spans carry the statement text with placeholders, never
the bound values. Request / response headers and bodies are not captured.

Any failure here (packages missing, a bad variable) is logged once as a warning and tracing stays off: it never stops
the API or a worker from starting.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

ENDPOINT_VARS = ("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT")
_URL_ATTRIBUTES = ("http.target", "http.url", "url.full", "url.query", "url.path")
_state: dict = {"on": False, "provider": None, "httpx": False, "engines": set()}


def enabled() -> bool:
    """True only when an OTLP endpoint is configured and the SDK is not switched off."""
    if os.getenv("OTEL_SDK_DISABLED", "").strip().lower() == "true":
        return False
    return any(os.getenv(name, "").strip() for name in ENDPOINT_VARS)


def is_on() -> bool:
    return bool(_state["on"])


def redact_url(value: str) -> str:
    from app.core.logging_config import _redact_callback_query, redact_personal, redact_query
    return redact_personal(redact_query(_redact_callback_query(value)))


def redact_span(span: Any, *_: Any) -> None:
    """Rewrites a span's URL attributes in place while it is still writable."""
    attributes = getattr(span, "attributes", None) or {}
    for key in _URL_ATTRIBUTES:
        value = attributes.get(key)
        if isinstance(value, str):
            clean = redact_url(value)
            if clean != value:
                span.set_attribute(key, clean)


def _redacting_processor() -> Any:
    from opentelemetry.sdk.trace import SpanProcessor

    class _RedactUrls(SpanProcessor):
        """Every span at its start (the httpx client spans carry their URL from the start). The API's server span sets
        its URL again after the start, so the FastAPI instrumentation also calls `redact_span` as its request hook."""

        def on_start(self, span: Any, parent_context: Any = None) -> None:
            redact_span(span)

    return _RedactUrls()


def _provider(service: str, exporter: Optional[Any] = None) -> Any:
    if _state["provider"] is not None:
        return _state["provider"]
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    attributes = {} if os.getenv("OTEL_SERVICE_NAME", "").strip() else {"service.name": f"atp-{service}"}
    provider = TracerProvider(resource=Resource.create(attributes))
    provider.add_span_processor(_redacting_processor())   # first: every later processor sees the redacted values
    if exporter is None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        exporter = OTLPSpanExporter()                       # endpoint / headers / timeout from the OTEL_* variables
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _state["provider"] = provider
    return provider


def init(service: str, app: Any = None, engine: Any = None, *, exporter: Any = None, force: bool = False) -> bool:
    """Start tracing for this process when configured; returns whether it is on. Idempotent.

    `service`: "api" or "worker". `app`: the FastAPI app (instrumented before its first request). `engine`: the
    SQLAlchemy AsyncEngine (default: the app's own, app/db/session.py). `exporter` / `force` are for tests only.
    """
    if not (force or enabled()):
        return False
    try:
        provider = _provider(service, exporter)
        if not _state["httpx"]:
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
            HTTPXClientInstrumentor().instrument(tracer_provider=provider)
            _state["httpx"] = True
        if engine is None:
            from app.db.session import get_engine
            engine = get_engine()
        sync_engine = getattr(engine, "sync_engine", engine)
        if id(sync_engine) not in _state["engines"]:
            from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
            # skip_dep_check: the instrumentation declares "sqlalchemy < 2.1" but only hooks the stable engine events
            # (before/after_cursor_execute), which SQLAlchemy 2.1 keeps; tests/test_aud_g2_1_tracing.py proves the spans.
            SQLAlchemyInstrumentor().instrument(engine=sync_engine, tracer_provider=provider, skip_dep_check=True)
            _state["engines"].add(id(sync_engine))
        if app is not None and not getattr(app, "_is_instrumented_by_opentelemetry", False):
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            FastAPIInstrumentor.instrument_app(app, tracer_provider=provider, server_request_hook=redact_span)
        _state["on"] = True
    except Exception as exc:  # noqa: BLE001 - tracing is optional; it never stops the process from starting
        logger.warning("Tracing not started (%s); continuing without it", type(exc).__name__)
        return False
    return True
