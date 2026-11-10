"""M94 PR-3 (S9 Circuit Watch): the day's price band in the broker quote (Quote.upper / lower_circuit_limit) and
`BandDistancePct(side)` - the last bar only, from the user's own broker quote. Upstox and Kite fill the band from their
documented `upper_circuit_limit` / `lower_circuit_limit` fields; a broker or a symbol without one is "not checked: price
band not available" with the reason - never a computed band, a pass, a no-match or a 0. Recorded-shape fixtures with
made-up symbols and prices only (no real data)."""
import asyncio
import gzip
import json
import types

import httpx
import numpy as np
import pandas as pd
import pytest

from app.brokers.angel_one import AngelOneBroker
from app.brokers.base import BrokerInterface
from app.brokers.contract_symbols import wrap_contract_symbols
from app.brokers.dhan import DhanBroker
from app.brokers.fyers import FyersBroker
from app.brokers.models import BrokerCredentials, Quote, circuit_limits
from app.brokers.upstox import UpstoxBroker
from app.brokers.zerodha import ZerodhaBroker
from app.screener import bands as B
from app.screener import compile_screen
from app.screener.registry import FUNCTIONS, LAST_BAR_ONLY
from app.screener.routes import missing_data
from app.screener.runtime import SymbolData, run_screen
from app.screener.venues import unavailable


def run(coro):
    return asyncio.run(coro)


# --- the Quote fields and the adapters ------------------------------------------------------------------------------

def test_the_quote_band_fields_are_missing_by_default_never_invented():
    q = Quote(symbol="X", ltp=100.0)
    assert q.upper_circuit_limit is None and q.lower_circuit_limit is None


def test_circuit_limits_keeps_only_a_real_positive_band():
    assert circuit_limits({"upper_circuit_limit": 110.0, "lower_circuit_limit": "90.5"}) == {"upper_circuit_limit": 110.0,
                                                                                               "lower_circuit_limit": 90.5}
    assert circuit_limits({}) == {"upper_circuit_limit": None, "lower_circuit_limit": None}
    # an index answers 0 (no band); junk and non-finite values are not a band either
    assert circuit_limits({"upper_circuit_limit": 0, "lower_circuit_limit": 0}) == {"upper_circuit_limit": None, "lower_circuit_limit": None}
    assert circuit_limits({"upper_circuit_limit": "n/a", "lower_circuit_limit": float("nan")}) == {"upper_circuit_limit": None,
                                                                                                   "lower_circuit_limit": None}
    assert circuit_limits(None) == {"upper_circuit_limit": None, "lower_circuit_limit": None}


def _kite(handler) -> ZerodhaBroker:
    return ZerodhaBroker(BrokerCredentials(api_key="k", access_token="t"),
                         client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=ZerodhaBroker.BASE_URL))


# Kite Connect `GET /quote` entry shape (documented fields; made-up values)
KITE_QUOTE = {"status": "success", "data": {
    "NSE:BANDCO": {"instrument_token": 1, "last_price": 104.0, "volume": 5000, "net_change": 4.0,
                   "ohlc": {"open": 100.0, "high": 105.0, "low": 99.0, "close": 100.0},
                   "lower_circuit_limit": 90.0, "upper_circuit_limit": 110.0, "last_trade_time": "2026-10-20 10:15:00",
                   "depth": {"buy": [{"price": 103.9, "quantity": 10}], "sell": [{"price": 104.1, "quantity": 5}]}},
    "NSE:NIFTY 50": {"instrument_token": 2, "last_price": 25000.0, "ohlc": {"close": 24900.0},
                     "lower_circuit_limit": 0, "upper_circuit_limit": 0},
    "NSE:NOBAND": {"instrument_token": 3, "last_price": 50.0, "ohlc": {"close": 49.0}},
}}


def test_kite_quotes_carry_the_band_from_its_documented_fields():
    broker = _kite(lambda request: httpx.Response(200, json=KITE_QUOTE))
    assert type(broker).price_bands is True
    got = run(wrap_contract_symbols(broker).get_quotes_batch([("NSE", "BANDCO"), ("NSE", "NIFTY 50"), ("NSE", "NOBAND")]))
    q = got[("NSE", "BANDCO")]
    assert (q.upper_circuit_limit, q.lower_circuit_limit) == (110.0, 90.0)
    assert q.ltp == 104.0 and q.prev_close == 100.0 and q.bid == 103.9          # the existing fields as before
    for key in (("NSE", "NIFTY 50"), ("NSE", "NOBAND")):                         # an index's 0 and an absent field: missing
        assert got[key].upper_circuit_limit is None and got[key].lower_circuit_limit is None
    one = run(broker.get_quote(["NSE:BANDCO"]))["NSE:BANDCO"]
    assert (one.upper_circuit_limit, one.lower_circuit_limit) == (110.0, 90.0)


def test_kite_single_symbol_quote_carries_the_band_too():
    single = {"status": "success", "data": {"NSE:BANDCO": KITE_QUOTE["data"]["NSE:BANDCO"]}}
    q = run(_kite(lambda request: httpx.Response(200, json=single)).get_quote_for_symbol("BANDCO", "NSE"))
    assert (q.upper_circuit_limit, q.lower_circuit_limit) == (110.0, 90.0) and q.timestamp is not None


def _upstox(handler) -> UpstoxBroker:
    return UpstoxBroker(BrokerCredentials(api_key="c", access_token="t"),
                        client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=UpstoxBroker.BASE_URL))


UPSTOX_MASTER = gzip.compress(json.dumps([
    {"instrument_key": "NSE_EQ|INE000B01011", "exchange": "NSE", "trading_symbol": "BANDCO", "instrument_type": "EQ"},
    {"instrument_key": "NSE_EQ|INE000N01012", "exchange": "NSE", "trading_symbol": "NOBAND", "instrument_type": "EQ"},
]).encode())
# Upstox v2 Full Market Quotes (`GET /market-quote/quotes`) entry shape (documented fields; made-up values)
UPSTOX_QUOTE = {"status": "success", "data": {
    "NSE_EQ:BANDCO": {"instrument_token": "NSE_EQ|INE000B01011", "symbol": "BANDCO", "last_price": 96.0, "volume": 800,
                      "net_change": -4.0, "ohlc": {"open": 100.0, "high": 100.5, "low": 95.5, "close": 96.0},
                      "lower_circuit_limit": 90.0, "upper_circuit_limit": 110.0, "last_trade_time": "1760935500000",
                      "depth": {"buy": [{"price": 95.9, "quantity": 3, "orders": 1}], "sell": [{"price": 96.1, "quantity": 2, "orders": 1}]}},
    "NSE_EQ:NOBAND": {"instrument_token": "NSE_EQ|INE000N01012", "symbol": "NOBAND", "last_price": 50.0},
}}


def _upstox_handler(request: httpx.Request) -> httpx.Response:
    if "assets.upstox.com" in str(request.url):
        return httpx.Response(200, content=UPSTOX_MASTER)
    return httpx.Response(200, json=UPSTOX_QUOTE)


def test_upstox_quotes_carry_the_band_from_its_documented_fields():
    broker = _upstox(_upstox_handler)
    assert type(broker).price_bands is True
    got = run(broker.get_quotes_batch([("NSE", "BANDCO"), ("NSE", "NOBAND")]))
    q = got[("NSE", "BANDCO")]
    assert (q.upper_circuit_limit, q.lower_circuit_limit) == (110.0, 90.0)
    assert q.ltp == 96.0 and q.prev_close == 100.0                               # the existing fields as before
    assert got[("NSE", "NOBAND")].upper_circuit_limit is None and got[("NSE", "NOBAND")].lower_circuit_limit is None
    single = run(_upstox(_upstox_handler).get_quote_for_symbol("BANDCO", "NSE"))
    assert (single.upper_circuit_limit, single.lower_circuit_limit) == (110.0, 90.0)
    keyed = run(_upstox(_upstox_handler).get_quote(["NSE_EQ|INE000B01011"]))["NSE_EQ:BANDCO"]
    assert (keyed.upper_circuit_limit, keyed.lower_circuit_limit) == (110.0, 90.0)


def test_only_upstox_and_kite_say_their_quote_carries_a_band():
    assert BrokerInterface.price_bands is False
    for cls in (DhanBroker, FyersBroker, AngelOneBroker):
        assert cls.price_bands is False, cls.__name__


# --- BandDistancePct ------------------------------------------------------------------------------------------------

def _frame(close=100.0, bars=30):
    idx = pd.date_range("2026-01-01", periods=bars, freq="1D", tz="UTC")
    c = np.full(bars, close)
    return pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": np.full(bars, 1000.0)}, index=idx)


def _data(symbol, quote=None, reason=None):
    d = SymbolData(symbol, {"1d": _frame()})
    d.quote, d.quote_reason = quote, reason
    return d


def _run_screen(text, universe):
    ast, validated = compile_screen(text, base_tf="1d")
    assert validated.ok, validated.problems
    return {m.symbol: m for m in run_screen(ast, validated, universe, base_tf="1d")}


def _q(ltp, upper=110.0, lower=90.0):
    return Quote(symbol="X", ltp=ltp, upper_circuit_limit=upper, lower_circuit_limit=lower)


def test_the_distance_is_from_the_quotes_own_last_price_and_band():
    assert B.distance_pct(_q(108.0), "upper") == pytest.approx((110.0 - 108.0) / 108.0 * 100.0)
    assert B.distance_pct(_q(92.0), "lower") == pytest.approx((92.0 - 90.0) / 92.0 * 100.0)
    assert B.distance_pct(_q(100.0, upper=None), "upper") is None and B.distance_pct(None, "lower") is None
    assert B.distance_pct(_q(0.0), "upper") is None                                    # no last price: no distance
    with pytest.raises(ValueError):
        B.distance_pct(_q(100.0), "middle")


def test_near_upper_near_lower_far_and_missing_on_a_fixture():
    universe = [_data("NEARUP", _q(109.0)), _data("NEARLOW", _q(90.5)), _data("FAR", _q(100.0)),
                _data("NOBAND", _q(100.0, upper=None, lower=None)), _data("NOQUOTE", reason=B.FROM_BROKER)]
    up = _run_screen('BandDistancePct("upper") <= 2', universe)
    assert up["NEARUP"].matched is True and up["NEARUP"].reason is None
    assert up["NEARLOW"].matched is False and up["NEARLOW"].reason is None             # read, and far from the upper band
    assert up["FAR"].matched is False and up["FAR"].reason is None
    low = _run_screen('BandDistancePct("lower") <= 2', universe)
    assert low["NEARLOW"].matched is True and low["NEARUP"].matched is False and low["FAR"].matched is False
    for screen in (up, low):                                                           # missing: a reason, never a silent no-match
        assert screen["NOBAND"].matched is False and screen["NOBAND"].reason.code == "not_checked"
        assert str(screen["NOBAND"].reason) == "not checked: price band not available (not in your broker's quote for this symbol)"
        assert str(screen["NOQUOTE"].reason) == "not checked: price band not available (not available from your broker)"
        assert screen["NOQUOTE"].reason.code == "not_checked"


def test_last_bar_only_refused_back_in_time_and_off_the_history_paths():
    assert "BandDistancePct" in LAST_BAR_ONLY and FUNCTIONS["BandDistancePct"].needs == ("price_band",)
    _, v = compile_screen('Count(5, BandDistancePct("upper") <= 2) >= 2', base_tf="5m")
    assert not v.ok and any("last bar only" in p.message for p in v.problems)
    _, v = compile_screen('BandDistancePct("middle") <= 2', base_tf="1d")
    assert not v.ok                                                                    # only "upper" / "lower"
    ast = compile_screen('BandDistancePct("upper") <= 2', base_tf="1d")[0]
    assert missing_data(ast, reference=True, chain=True, band=True) == []              # the run path loads the quote now
    assert missing_data(ast) == ["BandDistancePct"]                                    # history / library / charts: refused
    assert missing_data(ast, reference=True, chain=True) == ["BandDistancePct"]
    assert unavailable(ast, "NSE") == [] and unavailable(ast, "CRYPTO")               # the Indian exchange venues carry a band


# --- the loader (the user's own broker quote, batched) --------------------------------------------------------------

class _Banded:
    price_bands = True
    quote_batch_max = 2

    def __init__(self, quotes, fail=False):
        self.quotes, self.fail, self.calls = quotes, fail, []

    async def get_quotes_batch(self, items):
        self.calls.append(list(items))
        if self.fail:
            raise RuntimeError("broker down")
        return {p: self.quotes[p[1]] for p in items if p[1] in self.quotes}


class _Unbanded(_Banded):
    price_bands = False


def _patch(monkeypatch, adapter, *, credential=True, tokens=True):
    from fastapi import HTTPException

    from app.brokers import token_lifecycle
    from app.market_data import api_budget, reader_credential

    async def pick(session, tenant_id, user_id, broker=None, account_label="primary"):
        if not credential:
            raise HTTPException(status_code=409, detail=reader_credential.NO_READER)
        return types.SimpleNamespace(id=7, broker_name="fake")

    async def take(record_id, broker, wait_seconds=None, **_k):
        return tokens
    monkeypatch.setattr(reader_credential, "credential_for_reader", pick)
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record: adapter)
    monkeypatch.setattr(api_budget, "take", take)


def _universe(*symbols):
    return [SymbolData(s, {"1d": _frame()}) for s in symbols]


def test_attach_reads_the_users_own_quotes_in_batches(monkeypatch):
    adapter = _Banded({"A": _q(109.0), "B": _q(100.0), "C": _q(95.0, upper=None, lower=None)})
    _patch(monkeypatch, adapter)
    universe = _universe("A", "B", "C", "D")
    run(B.attach(None, universe, tenant_id=1, user_id=1, exchange="NSE"))
    assert adapter.calls == [[("NSE", "A"), ("NSE", "B")], [("NSE", "C"), ("NSE", "D")]]     # one call per batch size
    by = {d.symbol: d for d in universe}
    assert by["A"].quote.upper_circuit_limit == 110.0 and by["A"].quote_reason is None
    assert by["D"].quote is None and by["D"].quote_reason == B.NOT_IN_QUOTE
    got = _run_screen('BandDistancePct("upper") <= 2', universe)
    assert got["A"].matched is True and got["B"].matched is False
    assert got["C"].reason.code == "not_checked" and got["D"].reason.code == "not_checked"


def test_attach_asks_no_broker_without_bands_and_says_why(monkeypatch):
    adapter = _Unbanded({"A": _q(109.0)})
    _patch(monkeypatch, adapter)
    universe = _universe("A")
    run(B.attach(None, universe, tenant_id=1, user_id=1, exchange="NSE"))
    assert adapter.calls == [] and universe[0].quote is None and universe[0].quote_reason == B.FROM_BROKER


def test_attach_without_an_own_credential_a_budget_or_a_working_call_is_not_checked(monkeypatch):
    for kwargs, adapter, why in (({"credential": False}, _Banded({}), B.NO_QUOTE), ({"tokens": False}, _Banded({}), B.BUDGET),
                                 ({}, _Banded({}, fail=True), B.NO_QUOTE)):
        _patch(monkeypatch, adapter, **kwargs)
        universe = _universe("A")
        run(B.attach(None, universe, tenant_id=1, user_id=1, exchange="NSE"))
        assert universe[0].quote is None and universe[0].quote_reason == why
        got = _run_screen('BandDistancePct("lower") <= 2', universe)
        assert got["A"].matched is False and str(got["A"].reason).startswith(B.NOT_AVAILABLE)
