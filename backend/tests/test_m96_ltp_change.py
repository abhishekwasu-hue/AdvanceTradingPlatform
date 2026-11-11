"""M96 L1 follow-up: the live chain's per-leg `ltp_change` / `ltp_change_basis` (GET /api/option-chain/{u}/live, from
chain_view.build_view). First the broker's own previous-session close of the contract (each adapter parses it where its
chain or quote carries one), else the organisation's first reading of the day of that contract (the OI-change cache
rule), else None - never invented. Per adapter parse, the view, the baseline rules."""
import asyncio
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from app.brokers.angel_one import AngelOneBroker
from app.brokers.dhan import DhanBroker
from app.brokers.fyers import FyersBroker
from app.brokers.models import BrokerCredentials, Instrument, OptionChain, OptionChainRow, Quote
from app.brokers.shoonya import ShoonyaBroker
from app.brokers.upstox import UpstoxBroker
from app.brokers.zerodha import ZerodhaBroker
from app.option_chain import chain_view
from tests.test_oc1_option_chain_view import memory_cache  # noqa: F401 - the in-memory cache fixture

run = asyncio.run
NOW = datetime(2026, 10, 21, 4, 30, tzinfo=timezone.utc)
EXPIRY = date(2026, 10, 27)


def _mock(handler, base=""):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=base)


# ---------------------------------------------------------------- the rule

def test_price_change_prefers_the_previous_close_then_the_first_reading_then_none():
    assert chain_view.price_change(12.5, 10.0, 11.0) == (2.5, "previous_close")
    assert chain_view.price_change(12.5, None, 11.0) == (1.5, "first_reading")
    assert chain_view.price_change(12.5, 0.0, None) == (None, None)
    assert chain_view.price_change(None, 10.0, 11.0) == (None, None)
    assert chain_view.price_change(0.0, 10.0, 11.0) == (None, None)            # 0 = not traded, no price


def _chain(prev: bool):
    rows = [OptionChainRow(strike=100, call_ltp=12.0, put_ltp=3.0, call_oi=10, put_oi=20,
                           call_prev_close=10.0 if prev else None, put_prev_close=4.0 if prev else None),
            OptionChainRow(strike=110, call_ltp=5.0, put_ltp=None, call_oi=5, put_oi=8)]
    return OptionChain(underlying="NIFTY", expiry=EXPIRY.isoformat(), underlying_ltp=104.0, rows=rows)


def test_build_view_legs_carry_ltp_change_with_its_basis():
    view = chain_view.build_view(_chain(prev=True), spot=104.0, future=None, expiry=EXPIRY, now=NOW, oi=chain_view.OiChange("none", None))
    by = {r["strike"]: r for r in view["rows"]}
    assert (by[100]["call"]["ltp_change"], by[100]["call"]["ltp_change_basis"]) == (2.0, "previous_close")
    assert (by[100]["put"]["ltp_change"], by[100]["put"]["ltp_change_basis"]) == (-1.0, "previous_close")
    assert (by[110]["call"]["ltp_change"], by[110]["call"]["ltp_change_basis"]) == (None, None)       # unknown: null
    first = chain_view.LtpFirst(since=NOW, prices={110.0: (4.0, None)})
    view = chain_view.build_view(_chain(prev=True), spot=104.0, future=None, expiry=EXPIRY, now=NOW, oi=chain_view.OiChange("none", None),
                                 ltp_first=first)
    by = {r["strike"]: r for r in view["rows"]}
    assert (by[110]["call"]["ltp_change"], by[110]["call"]["ltp_change_basis"]) == (1.0, "first_reading")
    assert by[100]["call"]["ltp_change_basis"] == "previous_close"                                    # the close wins
    assert by[110]["put"]["ltp_change"] is None


def test_first_reading_builds_like_the_oi_change_and_never_on_a_closed_market(memory_cache):  # noqa: F811
    chain = _chain(prev=False)
    closed = run(chain_view.ltp_first_state(chain, tenant_id=5, underlying="NIFTY", now=NOW, market_open=False))
    assert closed.prices is None and memory_cache.store == {}
    first = run(chain_view.ltp_first_state(chain, tenant_id=5, underlying="NIFTY", now=NOW, market_open=True))
    assert first.since == NOW and first.prices is None                                           # building
    view = chain_view.build_view(chain, spot=104.0, future=None, expiry=EXPIRY, now=NOW, oi=chain_view.OiChange("none", None), ltp_first=first)
    assert all(r[s]["ltp_change"] is None for r in view["rows"] for s in ("call", "put"))
    later_rows = [r.model_copy(update={"call_ltp": (r.call_ltp or 0) + 1.5}) for r in chain.rows]
    later = chain.model_copy(update={"rows": later_rows})
    ready = run(chain_view.ltp_first_state(later, tenant_id=5, underlying="NIFTY", now=NOW + timedelta(minutes=6), market_open=True))
    assert ready.since == NOW and ready.prices == {100.0: (12.0, 3.0), 110.0: (5.0, None)}       # the first reading kept
    view = chain_view.build_view(later, spot=104.0, future=None, expiry=EXPIRY, now=NOW, oi=chain_view.OiChange("none", None), ltp_first=ready)
    by = {r["strike"]: r for r in view["rows"]}
    assert (by[100]["call"]["ltp_change"], by[100]["call"]["ltp_change_basis"]) == (1.5, "first_reading")
    assert by[100]["put"]["ltp_change"] == 0.0
    other = run(chain_view.ltp_first_state(chain, tenant_id=6, underlying="NIFTY", now=NOW + timedelta(minutes=6), market_open=True))
    assert other.prices is None                                                                   # per organisation


def test_no_first_reading_is_kept_when_the_broker_sends_every_close(memory_cache):  # noqa: F811
    state = run(chain_view.ltp_first_state(_chain(prev=True).model_copy(update={"rows": _chain(prev=True).rows[:1]}),
                                           tenant_id=5, underlying="NIFTY", now=NOW, market_open=True))
    assert state.prices is None and state.since is None and memory_cache.store == {}


# ---------------------------------------------------------------- per adapter

def test_upstox_reads_market_data_close_price():
    near = date.today() + timedelta(days=3)

    def handler(request):
        return httpx.Response(200, json={"status": "success", "data": [{
            "strike_price": 24000, "underlying_spot_price": 24010.5,
            "call_options": {"market_data": {"ltp": 120.0, "close_price": 100.0, "oi": 10, "prev_oi": 8}},
            "put_options": {"market_data": {"ltp": 80.0, "close_price": 0, "oi": 10}}}]})
    broker = UpstoxBroker(BrokerCredentials(api_key="k", access_token="t"), client=_mock(handler, UpstoxBroker.BASE_URL))

    async def no_master(exchange="NSE"):
        return []
    broker.get_instruments = no_master  # type: ignore[method-assign]
    row = run(broker.get_option_chain("NIFTY", near)).rows[0]
    assert row.call_prev_close == 100.0 and row.put_prev_close is None                           # 0 = not known


def test_dhan_reads_previous_close_price():
    broker = DhanBroker(BrokerCredentials(access_token="t", client_id="1"), client=_mock(lambda r: httpx.Response(500)))

    async def resolve(symbol, exchange="NSE", for_order=False):
        return "IDX_I", "13", "INDEX"

    async def request(method, path, json_body=None):
        if path.endswith("expirylist"):
            return {"data": ["2026-10-28"]}
        return {"data": {"last_price": 26050.0, "oc": {"26000.000000": {
            "ce": {"last_price": 210.5, "oi": 100, "previous_close_price": 200.0},
            "pe": {"last_price": 180.0, "oi": 100}}}}}
    broker._resolve = resolve  # type: ignore[method-assign]
    broker._request = request  # type: ignore[method-assign]
    row = run(broker.get_option_chain("NIFTY", date(2026, 10, 28))).rows[0]
    assert row.call_prev_close == 200.0 and row.put_prev_close is None


def test_fyers_reads_ltpch_as_ltp_minus_the_previous_close():
    broker = FyersBroker(BrokerCredentials(api_key="APP-1", access_token="t"), client=_mock(lambda r: httpx.Response(500)))

    async def ticker(symbol, exchange="NSE"):
        return "NSE:NIFTY50-INDEX"

    async def request(method, url, params=None, json_body=None, authenticated=True):
        return {"data": {"expiryData": [{"date": "28-10-2026", "expiry": "1761645600"}], "optionsChain": [
            {"option_type": "", "strike_price": -1, "ltp": 26050.0},
            {"option_type": "CE", "strike_price": 26000, "ltp": 210.5, "ltpch": 10.5, "oi": 5},
            {"option_type": "PE", "strike_price": 26000, "ltp": 180.0, "oi": 7}]}}
    broker._ticker = ticker  # type: ignore[method-assign]
    broker._request = request  # type: ignore[method-assign]
    row = run(broker.get_option_chain("NIFTY")).rows[0]
    assert row.call_prev_close == 200.0 and row.put_prev_close is None


def _option(token, symbol, strike, right, expiry="2026-10-28", name="NIFTY", itype=None):
    return Instrument(instrument_token=token, exchange="NFO", tradingsymbol=symbol, name=name, instrument_type=itype or right,
                      expiry=expiry, strike=strike)


def test_zerodha_reads_the_quotes_previous_close():
    broker = ZerodhaBroker(BrokerCredentials(api_key="k", access_token="t"), client=_mock(lambda r: httpx.Response(500)))
    assert ZerodhaBroker._quote_from("NFO:X", {"last_price": 12.0, "ohlc": {"close": 10.0}}).prev_close == 10.0

    async def instruments(exchange="NFO"):
        return [_option("1", "NIFTYCE", 26000.0, "CE"), _option("2", "NIFTYPE", 26000.0, "PE")]

    async def quotes(symbols):
        return {"NFO:NIFTYCE": Quote(symbol="NFO:NIFTYCE", ltp=12.0, prev_close=10.0), "NFO:NIFTYPE": Quote(symbol="NFO:NIFTYPE", ltp=3.0)}
    broker.get_instruments = instruments  # type: ignore[method-assign]
    broker.get_quote = quotes  # type: ignore[method-assign]
    row = run(broker.get_option_chain("NIFTY")).rows[0]
    assert row.call_prev_close == 10.0 and row.put_prev_close is None


def test_shoonya_reads_c_from_getquotes():
    import json
    from urllib.parse import parse_qs

    def handler(request):
        if request.url.path.endswith("/QuickAuth"):
            return httpx.Response(200, json={"stat": "Ok", "susertoken": "s", "actid": "FA1"})
        if request.url.path.endswith("/GetOptionChain"):
            return httpx.Response(200, json={"stat": "Ok", "values": [{"token": "111", "strprc": "50000", "optt": "CE", "exch": "NFO"},
                                                                       {"token": "112", "strprc": "50000", "optt": "PE", "exch": "NFO"}]})
        token = json.loads(parse_qs(request.content.decode())["jData"][0])["token"]
        return httpx.Response(200, json={"stat": "Ok", "lp": "250.5", "c": "240.0" if token == "111" else "0", "oi": "10"})
    creds = BrokerCredentials(client_id="FA1", api_key="vc", api_secret="sec", pin="pw", totp_secret="JBSWY3DPEHPK3PXP")
    broker = ShoonyaBroker(creds, client=_mock(handler, ShoonyaBroker.BASE_URL))
    run(broker.authenticate())
    row = run(broker.get_option_chain("BANKNIFTY")).rows[0]
    assert row.call_prev_close == 240.0 and row.put_prev_close is None


def test_angel_one_reads_the_full_quotes_close():
    from tests.test_phase_ac_angel_one import SECRET
    broker = AngelOneBroker(BrokerCredentials(api_key="k", client_id="A1", pin="1", totp_secret=SECRET), client=_mock(lambda r: httpx.Response(500)))

    async def instruments(exchange="NFO"):
        return [_option("1", "NIFTY28OCT2626000CE", 26000.0, "CE", itype="OPTIDX"),
                _option("2", "NIFTY28OCT2626000PE", 26000.0, "PE", itype="OPTIDX")]

    async def ltp(symbol, exchange="NSE"):
        return 26050.0

    async def quotes(tokens, mode="FULL"):
        return [{"symbolToken": "1", "ltp": 12.0, "close": 10.0}, {"symbolToken": "2", "ltp": 3.0, "close": 0}]
    broker.get_instruments = instruments  # type: ignore[method-assign]
    broker.get_ltp_for_symbol = ltp  # type: ignore[method-assign]
    broker._quotes = quotes  # type: ignore[method-assign]
    row = run(broker.get_option_chain("NIFTY", date(2026, 10, 28))).rows[0]
    assert row.call_prev_close == 10.0 and row.put_prev_close is None


@pytest.mark.parametrize("module", ["kotak_neo", "groww"])
def test_kotak_and_groww_chains_carry_no_close_so_the_first_reading_applies(module):
    import inspect

    from app.brokers import groww, kotak_neo
    source = inspect.getsource({"kotak_neo": kotak_neo.KotakNeoBroker, "groww": groww.GrowwBroker}[module].get_option_chain)
    assert "prev_close" not in source
