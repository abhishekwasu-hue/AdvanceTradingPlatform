"""B2 (UI-H bug lane): `data_as_of` is the chain's capture time while open and the last session close while closed - never
the request time; a closed market's missing or strike-rounded spot is the index's last close from our own candles,
labelled; and the owner's read-only `probe-chain` reports the broker's raw spot beside the ATM strike and our last close.
Made-up chains only (strikes 100-140)."""
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.brokers.models import OptionChain
from app.option_chain import as_of, probe
from tests.test_auth_api import client
from tests.test_oc1_option_chain_view import _chain, _setup_routes, memory_cache  # noqa: F401 - the fixture
from tests.test_oc1_option_chain_view import _v2_owner as _oc1_owner
from tests.test_oc2_option_chain_analyses import EXPIRY, FIVE, URL, _ChainBroker, _install, _name, _v2_owner
from tests.test_phase_aa_market_data_api import _store_credentials
from tests.test_phase_k_commercial import _owner

IST = ZoneInfo("Asia/Kolkata")
SAT_NOON = datetime(2026, 10, 24, 12, 0, tzinfo=IST)
FRI_CLOSE = datetime(2026, 10, 23, 15, 30, tzinfo=IST)
OWN_LAST_CLOSE = 129.5          # tests.test_phase_aa_market_data_api._Broker: 30 one-minute bars, the last close 100.5 + 29


def _closed(monkeypatch):
    monkeypatch.setattr(as_of, "as_of_for", lambda now, holidays, exchange: as_of.AsOf(FRI_CLOSE, True))


def _open(monkeypatch):
    monkeypatch.setattr(as_of, "as_of_for", lambda now, holidays, exchange: as_of.AsOf(now, False))


def test_as_of_is_the_read_time_while_open_and_the_last_close_while_closed():
    closed = as_of.as_of_for(SAT_NOON, set(), "NSE")
    assert closed == as_of.AsOf(FRI_CLOSE, True)
    tue_10 = datetime(2026, 10, 27, 10, 0, tzinfo=IST)
    assert as_of.as_of_for(tue_10, set(), "NSE") == as_of.AsOf(tue_10, False)
    # A holiday Friday: Thursday's close; BSE (SENSEX) keeps the NSE clock.
    assert as_of.as_of_for(SAT_NOON, {FRI_CLOSE.date()}, "BSE").at == datetime(2026, 10, 22, 15, 30, tzinfo=IST)


def test_strike_rounded():
    assert as_of.strike_rounded(120.0, FIVE) and as_of.strike_rounded(None, FIVE) and as_of.strike_rounded(0.0, FIVE)
    assert not as_of.strike_rounded(118.0, FIVE)


def _analyses(monkeypatch, spot):
    name = _name()
    _install(monkeypatch, _ChainBroker({name: OptionChain(underlying=name, expiry=EXPIRY, underlying_ltp=spot, rows=FIVE.rows)}))
    headers, me = _v2_owner("b2")
    _store_credentials(me)
    res = client.get(URL, headers=headers, params={"symbol": name})
    assert res.status_code == 200, res.text
    return res.json()


def test_analyses_closed_market_is_as_of_the_last_close_with_our_own_spot(monkeypatch):
    _closed(monkeypatch)
    body = _analyses(monkeypatch, 120.0)                       # the broker's after-hours spot = a strike
    assert body["data_as_of"] == FRI_CLOSE.isoformat() and body["market_closed"] is True
    assert body["spot"] == OWN_LAST_CLOSE and body["spot_source"] == "last_close"
    body = _analyses(monkeypatch, None)                        # no spot at all
    assert body["spot"] == OWN_LAST_CLOSE and body["spot_source"] == "last_close"
    body = _analyses(monkeypatch, 118.0)                       # a real price: the broker's stays
    assert body["spot"] == 118.0 and body["spot_source"] == "broker" and body["data_as_of"] == FRI_CLOSE.isoformat()


def test_analyses_open_market_is_as_of_the_read_and_keeps_the_broker_spot(monkeypatch):
    _open(monkeypatch)
    before = datetime.now(timezone.utc)
    body = _analyses(monkeypatch, 120.0)
    at = datetime.fromisoformat(body["data_as_of"])
    assert before - timedelta(seconds=1) <= at <= datetime.now(timezone.utc) and body["market_closed"] is False
    assert body["spot"] == 120.0 and body["spot_source"] == "broker"


def test_charts_route_closed_market_is_as_of_the_last_close(monkeypatch):
    _closed(monkeypatch)
    name = _name()
    _install(monkeypatch, _ChainBroker({name: OptionChain(underlying=name, expiry=EXPIRY, underlying_ltp=120.0, rows=FIVE.rows)}))
    headers, me = _v2_owner("b2-charts")
    _store_credentials(me)
    body = client.get("/api/option-chain/charts", headers=headers, params={"symbol": name}).json()
    assert body["state"] == "ok" and body["data_as_of"] == FRI_CLOSE.isoformat() and body["market_closed"] is True
    assert body["spot"] == OWN_LAST_CLOSE and body["spot_source"] == "last_close"


def test_chain_view_closed_market(monkeypatch, memory_cache):  # noqa: F811 - the fixture
    _closed(monkeypatch)
    headers, me = _oc1_owner(f"b2-live-{uuid.uuid4().hex[:8]}@example.com")
    _store_credentials(me)
    _setup_routes(monkeypatch, _chain(change=True, spot=110.0), market_open=False)     # 110 is a listed strike
    body = client.get("/api/option-chain/NIFTY/live?expiry=2026-10-27", headers=headers).json()
    assert body["market_open"] is False and body["data_as_of"] == FRI_CLOSE.isoformat()
    assert body["summary"]["spot"] == OWN_LAST_CLOSE and body["summary"]["spot_source"] == "last_close"


def test_probe_chain_reports_numbers_only():
    chain = OptionChain(underlying="NIFTY", expiry=EXPIRY, underlying_ltp=120.0, rows=FIVE.rows)
    when = as_of.AsOf(FRI_CLOSE, True)
    r = probe.summarise("NIFTY", chain, None, (24180.25, datetime(2026, 10, 23, 15, 29, tzinfo=IST)), when)
    assert r == {"underlying": "NIFTY", "market_closed": True, "data_as_of": "2026-10-23T15:30+05:30", "own_last_close": 24180.25,
                 "own_last_bar": "2026-10-23T15:29+05:30", "expiry": EXPIRY, "strikes": 5, "broker_spot": 120.0, "atm_strike": 120.0,
                 "spot_is_a_strike": True, "reason": None}
    lines = probe.format_chain({**r, "broker": "upstox"})
    assert lines[0] == "probe-chain NIFTY via upstox (market closed; data as of 2026-10-23T15:30+05:30)"
    assert "  broker spot (raw underlying_ltp): 120.0" in lines and "  ATM strike: 120.0; spot equals a strike: yes" in lines
    assert lines[-1] == "  our last index close: 24180.25 (bar 2026-10-23T15:29+05:30)" and len(lines) <= 6
    failed = probe.summarise("NIFTY", None, probe.scrub("BrokerAPIError: 401 abcdefghijklmnopqrstuvwxyz0123456789"), None, when)
    assert failed["strikes"] == 0 and "abcdefghijklmnop" not in failed["reason"]
    assert probe.format_chain({**failed, "broker": "upstox"})[1].startswith("  chain: 0 strikes - BrokerAPIError: 401")


def test_probe_chain_route_is_admin_only():
    headers, _ = _owner(f"b2-probe-{uuid.uuid4().hex[:8]}@example.com")
    assert client.get("/api/admin/option-chain/probe-chain?underlying=NIFTY", headers=headers).status_code == 403
    assert client.get("/api/admin/option-chain/probe-chain?underlying=NIFTY").status_code == 401
