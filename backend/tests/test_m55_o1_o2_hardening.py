"""M55 O1 (a): the request-body limit also holds for a streamed (chunked) body, counted as it arrives; the declared
Content-Length fast path stays. M55 O2: the compute routes are signed in and share the per-user analysis limit.
Uvicorn's proxy trust (O1 b) is checked against the value the prod overlay sets (tests/test_hostinger_deploy.py)."""
import asyncio
import json

import pytest

from app import main
from app.core import rate_limit
from tests.test_auth_api import _register, client

_H: dict = {}


def _h() -> dict:
    if not _H:
        _H["Authorization"] = f"Bearer {_register('m55-o2@example.com')}"
    return _H


def _chunks(body: bytes, size: int = 512):
    """An iterator body: the test client sends it chunked, with no Content-Length."""
    for i in range(0, len(body), size):
        yield body[i:i + size]


# --- O1 (a): streamed bodies ----------------------------------------------------------------------------------------

def test_a_chunked_body_over_the_limit_gets_413(monkeypatch):
    monkeypatch.setattr(main, "MAX_REQUEST_BODY_BYTES", 4096)
    body = json.dumps({"text": "x" * 10_000}).encode()
    r = client.post("/api/custom-strategies/parse", content=_chunks(body), headers={**_h(), "Content-Type": "application/json"})
    assert r.status_code == 413, r.text
    assert "content-length" not in {k.lower() for k in r.request.headers}          # really streamed


def test_a_small_chunked_body_passes(monkeypatch):
    monkeypatch.setattr(main, "MAX_REQUEST_BODY_BYTES", 4096)
    rate_limit.reset("analysis")
    body = json.dumps({"text": "Buy when RSI crosses above 60."}).encode()
    r = client.post("/api/custom-strategies/parse", content=_chunks(body, 8), headers={**_h(), "Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    assert len(r.json()["config"]["long_conditions"]) == 1


def test_the_declared_length_fast_path_still_refuses_before_the_app(monkeypatch):
    monkeypatch.setattr(main, "MAX_REQUEST_BODY_BYTES", 4096)
    r = client.post("/api/custom-strategies/parse", content=b"{" + b" " * 5000 + b"}", headers={"Content-Type": "application/json"})
    assert r.status_code == 413                                                       # before auth: nothing was read


def _drive(app, chunks):
    """Runs the middleware around `app` over an ASGI request whose body arrives in `chunks`; returns the sent messages."""
    messages = [{"type": "http.request", "body": c, "more_body": i < len(chunks) - 1} for i, c in enumerate(chunks)]
    sent = []

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/raw", "headers": [(b"transfer-encoding", b"chunked")]}
    asyncio.run(main._LimitRequestBody(app)(scope, receive, send))
    return sent


async def _reads_the_stream_itself(scope, receive, send):
    """A handler outside FastAPI's body parsing: it reads every chunk, then answers 200."""
    while True:
        message = await receive()
        if not message.get("more_body"):
            break
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def test_the_middleware_answers_413_for_a_raw_stream_reader(monkeypatch):
    monkeypatch.setattr(main, "MAX_REQUEST_BODY_BYTES", 100)
    over = _drive(_reads_the_stream_itself, [b"a" * 60, b"b" * 60])
    assert over[0]["type"] == "http.response.start" and over[0]["status"] == 413
    under = _drive(_reads_the_stream_itself, [b"a" * 40, b"b" * 40])
    assert under[0]["status"] == 200


# --- O2: signed in, within the analysis limit ------------------------------------------------------------------------

_BAR = {"timestamp": "2026-09-28T03:45:00+00:00", "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000}
ROUTES = [
    ("/api/strategies/ema_rsi_scalper_1m/signal", {"symbol": "X", "candles": {"1min": [_BAR]}}),
    ("/api/strategies/ema_rsi_scalper_1m/signal/enrich", {"symbol": "X", "candles": {"1min": [_BAR]}}),
    ("/api/strategies/ema_rsi_scalper_1m/paper-execute", {"symbol": "X", "candles": {"1min": [_BAR]}}),
    ("/api/strategies/ema_rsi_scalper_1m/chart-run", {"symbol": "X", "timeframe": "1min", "candles": [_BAR]}),
    ("/api/quant/factors", {"symbols": []}),
    ("/api/quant/risk", {"symbols": []}),
    ("/api/fundamentals/companies/NOSUCH/analysis/sector-specific?sector_key=banking", None),
    ("/api/fundamentals/companies/NOSUCH/analysis/valuation", {}),
    ("/api/fundamentals/companies/NOSUCH/analysis/dcf", {}),
    ("/api/fundamentals/companies/NOSUCH/analysis/score", {}),
    ("/api/fundamentals/fusion", {"fundamental_score": 50, "technical_score": 50, "technical_direction": "LONG", "horizon": "Swing"}),
    ("/api/fundamentals/screener", {}),
    ("/api/custom-strategies/parse", {"text": "Buy when RSI crosses above 60."}),
]
_IDS = [path.split("?")[0].replace("/api/", "") for path, _ in ROUTES]


@pytest.mark.parametrize(("path", "body"), ROUTES, ids=_IDS)
def test_each_compute_route_refuses_an_anonymous_caller(path, body):
    assert client.post(path, json=body).status_code == 401


@pytest.mark.parametrize(("path", "body"), ROUTES, ids=_IDS)
def test_each_compute_route_answers_a_signed_in_caller(path, body):
    rate_limit.reset("analysis")
    r = client.post(path, json=body, headers=_h())
    # Past the sign-in and the limiter: the route's own answer (an unknown company is 404, an empty request 400/422).
    assert r.status_code not in (401, 403, 429) and r.status_code < 500, (path, r.status_code, r.text[:200])


def test_every_compute_route_shares_the_per_user_analysis_limit(monkeypatch):
    monkeypatch.setattr(rate_limit, "redis_backed", lambda: False)
    rate_limit.reset("analysis")
    parse = ROUTES[-1]
    statuses = [client.post(parse[0], json=parse[1], headers=_h()).status_code for _ in range(61)]
    assert statuses.count(429) == 1 and statuses[-1] == 429
    for path, body in ROUTES:                                     # the same bucket: every route is now refused
        r = client.post(path, json=body, headers=_h())
        assert r.status_code == 429, (path, r.status_code)
        assert r.headers.get("Retry-After") == "60"
    other = {"Authorization": f"Bearer {_register('m55-o2-other@example.com')}"}
    assert client.post(parse[0], json=parse[1], headers=other).status_code == 200     # per user, not global
    rate_limit.reset("analysis")


def test_the_trusted_proxy_list_admits_the_caddy_network_and_nothing_else():
    from uvicorn.middleware.proxy_headers import _TrustedHosts
    trusted = _TrustedHosts("127.0.0.1,10.250.250.0/24")          # docker-compose.prod.yml's default
    assert "10.250.250.9" in trusted and "127.0.0.1" in trusted
    assert "172.18.0.5" not in trusted and "203.0.113.7" not in trusted


def test_empty_or_missing_candles_are_a_422_not_a_500():
    rate_limit.reset("analysis")
    base = "/api/strategies/ema_rsi_scalper_1m"
    assert client.post(f"{base}/signal", json={"symbol": "X", "candles": {"1min": []}}, headers=_h()).status_code == 422
    assert client.post(f"{base}/signal/enrich", json={"symbol": "X", "candles": {}}, headers=_h()).status_code == 422
    assert client.post(f"{base}/paper-execute", json={"symbol": "X", "candles": {"1min": []}}, headers=_h()).status_code == 422
