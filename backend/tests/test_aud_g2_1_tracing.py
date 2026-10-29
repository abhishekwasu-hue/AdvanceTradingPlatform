"""AUD-G2.1: OpenTelemetry tracing (app/observability/tracing.py) - OFF unless an OTLP endpoint is configured, the trace id
in the JSON logs (app/core/logging_config.py), URL redaction on spans, and start-up never failing because of tracing.

The "on" path runs in a child process: instrumenting patches httpx / SQLAlchemy / the tracer provider process-wide, and
the rest of the suite must keep running exactly as without tracing. No test makes a network call (an in-memory exporter).
"""
import json
import logging
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from app.core.logging_config import JsonFormatter, bind_log_context, trace_ids
from app.observability import tracing

BACKEND = Path(__file__).resolve().parents[1]


def _record(msg: str = "hello") -> logging.LogRecord:
    return logging.LogRecord("x", logging.INFO, __file__, 1, msg, (), None)


def test_off_without_an_endpoint(monkeypatch):
    for name in tracing.ENDPOINT_VARS:
        monkeypatch.delenv(name, raising=False)
    assert tracing.enabled() is False
    assert tracing.init("api") is False
    assert tracing.is_on() is False


def test_sdk_disabled_wins_over_an_endpoint(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector.invalid:4318")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    assert tracing.enabled() is False
    monkeypatch.delenv("OTEL_SDK_DISABLED")
    assert tracing.enabled() is True
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "  ")
    assert tracing.enabled() is False


def test_api_import_leaves_tracing_off():
    import app.main  # noqa: F401 - importing the app runs its tracing.init("api")
    assert tracing.is_on() is False
    assert not getattr(app.main.app, "_is_instrumented_by_opentelemetry", False)


def test_missing_packages_never_stop_start_up(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "opentelemetry.sdk.trace", None)     # "import" now raises ImportError
    monkeypatch.setitem(tracing._state, "provider", None)
    with caplog.at_level(logging.WARNING, logger="app.observability.tracing"):
        assert tracing.init("api", force=True) is False
    assert "Tracing not started" in caplog.text
    assert tracing.is_on() is False


def test_no_trace_keys_without_an_active_span():
    line = json.loads(JsonFormatter().format(_record()))
    assert "trace_id" not in line and "span_id" not in line
    assert trace_ids() == {}


def test_trace_and_span_ids_in_the_json_line_inside_a_span():
    from opentelemetry.sdk.trace import TracerProvider
    tracer = TracerProvider().get_tracer("test")              # a local provider: the global one is untouched
    with bind_log_context(request_id="r-1"):
        with tracer.start_as_current_span("work") as span:
            line = json.loads(JsonFormatter().format(_record("mail a.user@example.com")))
            ctx = span.get_span_context()
    assert line["trace_id"] == format(ctx.trace_id, "032x") and len(line["trace_id"]) == 32
    assert line["span_id"] == format(ctx.span_id, "016x") and len(line["span_id"]) == 16
    assert line["request_id"] == "r-1"                         # existing behaviour kept
    assert line["message"] == "mail a***@example.com"          # redact_personal kept


def test_redact_url_hides_oauth_codes_and_personal_data():
    out = tracing.redact_url("/api/brokers/upstox/oauth/callback?code=SECRET1&state=SECRET2")
    assert "SECRET1" not in out and "SECRET2" not in out
    out = tracing.redact_url("https://api.example.com/x?access_token=TOK123&email=a.user@example.com&n=9876543210")
    assert "TOK123" not in out and "a.user@" not in out and "9876543210" not in out


_CHILD = textwrap.dedent('''
    import asyncio, json, logging, io
    import httpx, httpcore

    async def _fake_pool(self, request):          # the transport's connection pool answers: no network call
        return httpcore.Response(200, headers=[(b"content-type", b"application/json")], content=b'{"ok": true}')
    httpcore.AsyncConnectionPool.handle_async_request = _fake_pool
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from app.core.logging_config import JsonFormatter
    from app.observability import tracing

    exporter = InMemorySpanExporter()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    app = FastAPI()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    log = logging.getLogger("child")
    log.addHandler(handler)
    log.setLevel(logging.INFO)

    @app.get("/api/brokers/upstox/oauth/callback")
    async def callback(code: str = "", state: str = ""):
        async with engine.connect() as conn:
            await conn.execute(text("select 1"))
        async with httpx.AsyncClient() as client:
            await client.get("https://broker.example/v2/orders?access_token=TOKSECRET")
        log.info("inside the request")
        return {"ok": True}

    assert tracing.enabled() is True
    assert tracing.init("api", app=app, engine=engine, exporter=exporter) is True
    assert tracing.init("api", app=app, engine=engine, exporter=exporter) is True     # idempotent
    with TestClient(app) as client:
        assert client.get("/api/brokers/upstox/oauth/callback?code=CODESECRET&state=STATESECRET").status_code == 200
    tracing._state["provider"].force_flush()
    spans = exporter.get_finished_spans()
    dump = json.dumps([dict(s.attributes or {}) for s in spans])
    print(json.dumps({
        "names": [s.name for s in spans],
        "kinds": sorted({s.kind.name for s in spans}),
        "trace_ids": sorted({format(s.context.trace_id, "032x") for s in spans}),
        "leaks": [w for w in ("CODESECRET", "STATESECRET", "TOKSECRET") if w in dump],
        "log": json.loads(stream.getvalue().strip().splitlines()[-1]),
    }))
''')


def test_on_path_traces_request_sql_and_httpx_with_trace_id_in_logs():
    env = {k: v for k, v in os.environ.items() if not k.startswith("OTEL_")}
    env["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://collector.invalid:4318"   # never contacted: an in-memory exporter is passed
    env["PYTHONPATH"] = str(BACKEND)
    proc = subprocess.run([sys.executable, "-c", _CHILD], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-3000:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert {"SERVER", "CLIENT"} <= set(out["kinds"])                   # the API request and the outgoing / SQL calls
    assert any(n.startswith("GET") and "callback" in n for n in out["names"])
    assert any(n.lower().startswith("select") for n in out["names"]), out["names"]      # the SQL statement (SQLAlchemy)
    assert "GET" in out["names"], out["names"]                                          # the outgoing httpx call
    assert len(out["trace_ids"]) == 1                                  # one request, one trace
    assert out["leaks"] == []                                          # no OAuth code / token in any span
    assert out["log"]["trace_id"] == out["trace_ids"][0]
    assert len(out["log"]["span_id"]) == 16
