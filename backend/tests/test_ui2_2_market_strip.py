"""UI2-2: `GET /api/market/strip` - the v2 dashboard's market strip (UI v2 proposal §5, §13 UI-3).

* the response shape: three indices (NIFTY 50, BANK NIFTY, INDIA VIX) with price, change and source, the market state
  and the refresh interval;
* live prices come from the batch quotes path (one broker call, counted once against the credential's budget);
* without a usable broker session (none stored, or not logged in today) the organisation's own market memory answers,
  marked "memory" with its time - another organisation's memory is never used; with neither, `ltp: null`, never 0;
* a live gap is filled from memory ("mixed");
* read-only: no INSERT / UPDATE / DELETE during the request and no order call on the broker;
* a login is required, the route is classified in the tenancy table, and it is rate limited per user.
No network: fake brokers."""
import asyncio
import inspect
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import List

import pytest
from sqlalchemy import event

from app.brokers.models import Quote
from app.core.enums import BrokerTokenStatus
from app.core import rate_limit
from app.db.models import MarketSnapshotRecord
from app.market_data import api_budget, quotes_routes, strip_routes
from app.market_data.calendar import IST
from app.main import app
from tests.route_inventory import iter_api_routes
from tests.tenancy_route_table import ROUTE_TABLE
from tests.test_auth_api import _engine, _session_factory, client
from tests.test_phase_aa_market_data_api import _Broker, _store_credentials
from tests.test_phase_k_commercial import _owner

run = asyncio.run
UTC = timezone.utc
URL = "/api/market/strip"


def _email(tag: str) -> str:
    return f"ui22-{tag}-{uuid.uuid4().hex[:8]}@example.com"


class _IndexBroker(_Broker):
    """A broker with a multi-quote call that prices the three indices (illustrative numbers); `absent` are left out.
    Any order call fails the test."""
    multi_quote = True
    PRICES = {"NIFTY 50": (24812.35, 24693.95), "NIFTY BANK": (53946.10, 54158.65), "INDIA VIX": (13.42, 13.63)}

    def __init__(self, absent=()):
        super().__init__()
        self.batch_calls: List[list] = []
        self.absent = set(absent)

    async def get_quotes_batch(self, items):
        self.batch_calls.append(list(items))
        now = datetime.now(UTC)
        return {(ex, sym): Quote(symbol=sym, ltp=self.PRICES[sym][0], prev_close=self.PRICES[sym][1], timestamp=now - timedelta(seconds=1))
                for ex, sym in items if sym in self.PRICES and sym not in self.absent}

    async def place_order(self, order):
        raise AssertionError("the market strip must never place an order")

    async def modify_order(self, order_id, quantity=None, price=None, trigger_price=None, order_type=None):
        raise AssertionError("the market strip must never modify an order")

    async def cancel_order(self, order_id):
        raise AssertionError("the market strip must never cancel an order")


@pytest.fixture(autouse=True)
def _clean():
    api_budget.reset()
    quotes_routes.reset()
    rate_limit.reset("market_strip")
    yield
    api_budget.reset()
    quotes_routes.reset()
    rate_limit.reset("market_strip")


def _grant_tokens(monkeypatch) -> List[int]:
    taken: List[int] = []

    async def take(record_id, broker, wait_seconds=None, kind="quote"):
        taken.append(record_id)
        return True
    monkeypatch.setattr(api_budget, "take", take)
    return taken


def _remember(tenant_id: int, *rows) -> None:
    async def go():
        async with _session_factory() as session:
            session.add_all([MarketSnapshotRecord(tenant_id=tenant_id, **row) for row in rows])
            await session.commit()
    run(go())


def _cue(symbol: str, last: float, prev: float, age: timedelta) -> dict:
    pct = round((last / prev - 1) * 100, 2)
    return dict(kind="CUE", symbol=symbol, exchange="NSE", timeframe="day", source="fake", last_price=last, change_pct=pct,
                payload_json=json.dumps({"last": last, "prev_close": prev, "change_pct": pct}), captured_at=datetime.now(UTC) - age)


def _by_key(body: dict) -> dict:
    return {i["key"]: i for i in body["indices"]}


# --- the market state (pure) ---------------------------------------------------------------------------------------------

def _ist(y, m, d, hh, mm) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=IST).astimezone(UTC)


def test_market_state_names_the_phase_and_the_next_open():
    holidays = {date(2026, 11, 9): "Diwali Balipratipada"}          # a Monday
    is_open = strip_routes.market_state(_ist(2026, 10, 19, 11, 0), holidays)                  # Monday 11:00
    assert is_open["phase"] == "open" and is_open["is_open"] is True and is_open["closes_at"] == "15:30" and is_open["next_open"] is None
    pre = strip_routes.market_state(_ist(2026, 10, 19, 9, 5), holidays)
    assert pre["phase"] == "pre-open" and pre["opens_at"] == "09:15" and pre["next_open"].startswith("2026-10-19T09:15")
    early = strip_routes.market_state(_ist(2026, 10, 19, 7, 30), holidays)
    assert early["phase"] == "closed" and early["reason"] == "before-open" and early["next_open"].startswith("2026-10-19T09:15")
    after = strip_routes.market_state(_ist(2026, 10, 23, 16, 0), holidays)                    # Friday after the close
    assert after["phase"] == "closed" and after["reason"] == "after-close" and after["next_open"].startswith("2026-10-26T09:15")
    weekend = strip_routes.market_state(_ist(2026, 10, 24, 12, 0), holidays)
    assert weekend["reason"] == "weekend" and weekend["next_open"].startswith("2026-10-26T09:15")
    holiday = strip_routes.market_state(_ist(2026, 11, 9, 12, 0), holidays)
    assert holiday["phase"] == "closed" and holiday["reason"] == "holiday" and holiday["holiday"] == "Diwali Balipratipada"
    assert holiday["next_open"].startswith("2026-11-10T09:15")


# --- live prices ---------------------------------------------------------------------------------------------------------

def test_live_prices_come_in_one_broker_call_with_the_full_shape(monkeypatch):
    headers, me = _owner(_email("live"))
    _store_credentials(me)
    broker = _IndexBroker()
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: broker)
    taken = _grant_tokens(monkeypatch)
    res = client.get(URL, headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) == {"indices", "source", "broker", "market", "refresh_seconds", "fetched_at"}
    assert [i["label"] for i in body["indices"]] == ["NIFTY 50", "BANK NIFTY", "INDIA VIX"]
    assert [i["symbol"] for i in body["indices"]] == ["NIFTY 50", "NIFTY BANK", "INDIA VIX"]
    for item in body["indices"]:
        assert set(item) == {"key", "label", "symbol", "exchange", "ltp", "prev_close", "change", "change_pct", "as_of", "stale",
                             "source", "missing"}
        assert item["missing"] is False and item["source"] == "live" and item["ltp"] > 0 and item["as_of"]
    nifty = _by_key(body)["NIFTY 50"]
    if nifty["prev_close"] is not None:          # a fresh tick from the shared feed carries no close of its own
        assert nifty["change"] == pytest.approx(118.40, abs=0.01) and nifty["change_pct"] == pytest.approx(0.4795, abs=1e-3)
    assert len(broker.batch_calls) <= 1 and len(taken) <= 1                    # one broker call for the three, at most
    assert body["source"] == "live" and body["broker"] == {"broker": "upstox", "account_label": "primary"}
    assert body["market"]["phase"] in ("open", "pre-open", "closed") and body["refresh_seconds"] in (15, 300)


def test_a_live_gap_is_filled_from_the_organisations_memory(monkeypatch):
    headers, me = _owner(_email("gap"))
    _store_credentials(me)
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: _IndexBroker(absent={"INDIA VIX"}))
    _grant_tokens(monkeypatch)
    _remember(me["tenant_id"], _cue("INDIA VIX", 13.42, 13.63, timedelta(minutes=20)))
    body = client.get(URL, headers=headers).json()
    vix = _by_key(body)["INDIA VIX"]
    assert vix["source"] == "memory" and vix["ltp"] == 13.42 and vix["change"] == pytest.approx(-0.21, abs=0.01)
    assert _by_key(body)["NIFTY 50"]["source"] == "live" and body["source"] == "mixed"


# --- without a broker session ----------------------------------------------------------------------------------------------

def test_without_a_broker_session_the_strip_answers_from_market_memory_of_this_organisation_only():
    headers, me = _owner(_email("memory"))
    _, other = _owner(_email("memory-other"))
    tenant = me["tenant_id"]
    _remember(tenant,
              _cue("NIFTY 50", 24600.00, 24500.00, timedelta(days=1)),                      # older: not shown
              _cue("NIFTY 50", 24812.35, 24693.95, timedelta(minutes=30)),
              dict(kind="SYMBOL", symbol="NIFTY BANK", exchange="NSE", timeframe="5min", source="fake", last_price=53946.10,
                   change_pct=-0.39, payload_json="{}", captured_at=datetime.now(UTC) - timedelta(minutes=10)),
              dict(kind="CUE", symbol="INDIA VIX", exchange="NSE", timeframe="day", source="fake", last_price=14.0,
                   change_pct=1.0, payload_json="{}", captured_at=datetime.now(UTC) - timedelta(days=9)))   # too old
    _remember(other["tenant_id"], _cue("INDIA VIX", 99.99, 90.00, timedelta(minutes=5)),      # another organisation's
              _cue("NIFTY 50", 11111.11, 11000.00, timedelta(minutes=1)))
    res = client.get(URL, headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    rows = _by_key(body)
    nifty, bank, vix = rows["NIFTY 50"], rows["NIFTY BANK"], rows["INDIA VIX"]
    assert nifty["source"] == "memory" and nifty["ltp"] == 24812.35 and nifty["prev_close"] == 24693.95
    assert nifty["change"] == pytest.approx(118.40, abs=0.01) and nifty["change_pct"] == pytest.approx(0.48, abs=0.01)
    assert nifty["as_of"] and nifty["missing"] is False
    assert bank["source"] == "memory" and bank["ltp"] == 53946.10 and bank["change_pct"] == pytest.approx(-0.39, abs=0.01)
    # The other organisation's fresh VIX is never used, and this one's is too old to be the last close.
    assert vix["missing"] is True and vix["ltp"] is None and vix["change"] is None and vix["source"] is None
    assert body["source"] == "memory" and body["broker"] is None


def test_an_index_whose_captures_stopped_is_still_found_behind_many_newer_rows():
    headers, me = _owner(_email("stopped"))
    tenant = me["tenant_id"]
    _remember(tenant, _cue("INDIA VIX", 13.42, 13.63, timedelta(days=2)))                   # the VIX captures stopped two days ago
    newer = [dict(kind="SYMBOL", symbol=sym, exchange="NSE", timeframe="5min", source="fake", last_price=price + i, change_pct=0.1,
                  payload_json="{}", captured_at=datetime.now(UTC) - timedelta(minutes=i + 1))
             for i in range(40) for sym, price in (("NIFTY 50", 24800.0), ("NIFTY BANK", 53900.0))]    # 80 newer rows
    _remember(tenant, *newer)
    rows = _by_key(client.get(URL, headers=headers).json())
    assert rows["INDIA VIX"]["source"] == "memory" and rows["INDIA VIX"]["ltp"] == 13.42
    assert rows["NIFTY 50"]["ltp"] == 24800.0 and rows["NIFTY BANK"]["ltp"] == 53900.0          # each one's newest


def test_a_session_not_logged_in_today_falls_back_too_and_nothing_known_is_null_never_zero(monkeypatch):
    headers, me = _owner(_email("expired"))
    _store_credentials(me, status=BrokerTokenStatus.EXPIRED.value)
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: (_ for _ in ()).throw(AssertionError("no broker call")))
    res = client.get(URL, headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["source"] == "none" and body["broker"] is None
    for item in body["indices"]:
        assert item["missing"] is True and item["ltp"] is None and item["change_pct"] is None


# --- read-only, auth, tenancy, rate limit ----------------------------------------------------------------------------------

def test_the_strip_writes_nothing_and_never_reaches_an_order_call(monkeypatch):
    headers, me = _owner(_email("readonly"))
    _store_credentials(me)
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: _IndexBroker())
    _grant_tokens(monkeypatch)
    statements: List[str] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip().split()[0].upper())
    event.listen(_engine.sync_engine, "before_cursor_execute", capture)
    try:
        res = client.get(URL, headers=headers)
    finally:
        event.remove(_engine.sync_engine, "before_cursor_execute", capture)
    assert res.status_code == 200, res.text
    assert statements and not {"INSERT", "UPDATE", "DELETE"} & set(statements), statements
    # The route module itself names no order path.
    source = inspect.getsource(strip_routes)
    for name in ("place_order", "modify_order", "cancel_order", "execution", "session.add", "commit("):
        assert name not in source.replace("no order path", ""), name


def test_a_login_is_required():
    assert client.get(URL).status_code == 401
    assert client.get(URL, headers={"Authorization": "Bearer not-a-token"}).status_code == 401


def test_the_route_is_classified_and_is_get_only():
    assert ROUTE_TABLE[("GET", "/api/market/strip")] == "member"
    assert sorted((r.method, r.path) for r in iter_api_routes(app) if r.path == URL) == [("GET", URL)]


def test_rate_limited_per_user():
    headers, _ = _owner(_email("limit"))
    codes = [client.get(URL, headers=headers).status_code for _ in range(21)]
    assert codes[:20] == [200] * 20 and codes[20] == 429
