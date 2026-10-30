"""N1 F1: the portfolio risk view of a user's own PAPER book (flag portfolio_risk, off by default).

The model is pure (`app/portfolio/risk.py`); `risk_data` gathers the user's own trades, the sector bucket (U1-b), the
beta to NIFTY 50 from the lake's Daily closes known at yesterday's close, and the PAPER capital basis. Trade's rules
mirrored here: the one-sided direction warning (`database.compute_portfolio_risk_summary`) and the index bucket of
correlated risk (`trading_engine.check_portfolio_risk_cap`; ATP's INDEX bucket holds every index). Read-only: the
route reaches no order path."""
import asyncio
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from types import SimpleNamespace

import numpy as np
import pytest
from sqlalchemy import select

from app.db.models import ClassificationRecord, MdCandleRecord, SymbolHistoryRecord, TradeRecord, User
from app.market_data.calendar import IST
from app.platform.controls import DEFAULT_OFF_FLAGS
from app.portfolio import risk as R
from app.portfolio import risk_data as D
from tests.order_path_graph import CallGraph
from tests.test_auth_api import _session_factory, client
from tests.test_n1_e1_execution_core import _flag
from tests.test_trading_worker import _tenant
from tests.test_z10c_order_paths import GATED
from tests.test_z10c_tenancy_fixes import _member

UTC = timezone.utc


def _run(coro):
    return asyncio.run(coro)


def _email(tag):
    return f"n1f1-{tag}-{uuid.uuid4().hex[:6]}@example.com"


def _sym(tag):
    return f"F1{tag}{uuid.uuid4().hex[:5].upper()}"


def _t(id_=1, symbol="ABC", direction="LONG", qty=10.0, entry=100.0, stop=95.0, mark=None, kind="UNDERLYING", group=None, role=None,
       meta=None, underlying=None, udir=None):
    return SimpleNamespace(id=id_, symbol=symbol, direction=direction, quantity=qty, entry_price=entry, stop_loss=stop, mark_price=mark,
                           instrument_kind=kind, leg_group_id=group, leg_role=role, group_meta=json.dumps(meta) if meta else None,
                           underlying_symbol=underlying, underlying_direction=udir)


# --- the pure model ------------------------------------------------------------------------------------------------------

def test_positions_count_a_structure_once_and_value_at_the_mark():
    trades = [_t(1, "ABC", mark=110.0), _t(2, "XYZ", "SHORT", qty=5, entry=200.0, stop=210.0),
              _t(3, "NIFTY24OCT25000PE", "SHORT", qty=50, entry=80, stop=160, kind="OPTION", group="g1", role="SHORT",
                 meta={"risk_per_unit": 40.0, "quantity": 50}, underlying="NIFTY", udir="LONG"),
              _t(4, "NIFTY24OCT24800PE", "LONG", qty=50, entry=40, stop=0, kind="OPTION", group="g1", role="LONG", underlying="NIFTY",
                 udir="LONG")]
    ps = R.positions_of(trades)
    assert [p.key for p in ps] == ["1", "2", "group:g1"]
    abc, xyz, spread = ps
    assert abc.long_notional == 1100.0 and not abc.priced_at_entry and abc.risk == 50.0 and abc.direction == R.BULLISH
    assert xyz.short_notional == 1000.0 and xyz.priced_at_entry and xyz.risk == 50.0 and xyz.direction == R.BEARISH
    assert spread.kind == "STRUCTURE" and spread.underlying == "NIFTY" and spread.risk == 2000.0 and spread.trade_ids == [3, 4]
    assert spread.direction == R.BULLISH and spread.short_notional == 4000.0 and spread.long_notional == 2000.0
    # Without group_meta: the short legs' risk (R4's fallback).
    bare = R.positions_of([_t(5, "X", "SHORT", kind="OPTION", group="g2", role="SHORT", entry=10, stop=30, qty=2, underlying="BANKNIFTY"),
                           _t(6, "Y", "LONG", kind="OPTION", group="g2", role="LONG", entry=5, stop=0, qty=2, underlying="BANKNIFTY")])
    assert bare[0].risk == 40.0 and bare[0].direction == R.NEUTRAL


def test_the_one_sided_warning_follows_trade_s_rule():
    """Trade `compute_portfolio_risk_summary`: at two or more directional positions, all on one side; neutral ignored."""
    assert R.one_sided_warning(1, 0) is None and R.one_sided_warning(0, 0) is None
    assert "2 directional positions are bullish" in R.one_sided_warning(2, 0)
    assert "3 directional positions are bearish" in R.one_sided_warning(0, 3)
    assert R.one_sided_warning(2, 1) is None
    v = R.view(R.positions_of([_t(1, "A"), _t(2, "B"), _t(3, "C", kind="OPTION")]), {}, {}, 10_000.0)
    assert v["direction"] == {"bullish": 2, "bearish": 0, "neutral": 1, "warning": R.one_sided_warning(2, 0)}


def test_the_view_buckets_weights_and_leaves_out_what_it_cannot_know():
    trades = [_t(1, "INFY", qty=10, entry=100, stop=90), _t(2, "TCS", qty=5, entry=200, stop=190),
              _t(3, "NIFTYFUT", "SHORT", qty=1, entry=1000, stop=1020, kind="FUT", underlying="NIFTY"),
              _t(4, "BANKNIFTYFUT", "SHORT", qty=1, entry=500, stop=520, kind="FUT", underlying="BANKNIFTY"),
              _t(5, "ODD", qty=1, entry=50, stop=45), _t(6, "INFY24OCT2000CE", qty=10, entry=10, stop=5, kind="OPTION", underlying="INFY")]
    buckets = {"INFY": "Information Technology", "TCS": "Information Technology", "NIFTY": R.INDEX_BUCKET, "BANKNIFTY": R.INDEX_BUCKET}
    betas = {"INFY": R.Beta(0.8, 200, True, "lake"), "TCS": R.Beta(0.5, 200, True, "lake"), "NIFTY": R.Beta(1.0, 0, True, "definition"),
             "BANKNIFTY": R.Beta(1.2, 200, True, "lake")}
    v = R.view(R.positions_of(trades), buckets, betas, 10_000.0)
    rows = {r["bucket"]: r for r in v["buckets"]}
    it = rows["Information Technology"]
    assert it["positions"] == 3 and it["gross_notional"] == 2100.0 and it["open_risk"] == 200.0 and it["gross_pct"] == 21.0
    assert set(it["symbols"]) == {"INFY", "TCS", "INFY24OCT2000CE"}
    idx = rows[R.INDEX_BUCKET]                       # NIFTY and BANKNIFTY together (R4; Trade's index-options bucket)
    assert idx["positions"] == 2 and idx["open_risk"] == 40.0 and idx["net_notional"] == -1500.0
    assert rows["UNCLASSIFIED:ODD"]["positions"] == 1                     # never pooled with another name
    assert v["open_risk"] == {"total": 245.0, "pct": 2.45}
    assert v["exposure"]["gross"] == 3650.0 and v["exposure"]["net"] == 650.0
    # beta: 0.8*1000 + 0.5*1000 + 1.0*-1000 + 1.2*-500 + 1.0*50 (ODD unknown -> 1.0); the option is not weighted
    assert v["beta"]["net"] == pytest.approx(-250.0) and v["beta"]["unknown"] == ["ODD"] and v["beta"]["not_weighted"] == ["INFY24OCT2000CE"]
    assert R.view([], {}, {}, 0.0)["open_risk"]["pct"] is None


def test_beta_from_closes():
    rng = np.random.default_rng(7)
    days = [str(date(2025, 1, 1) + timedelta(days=i)) for i in range(301)]
    index, stock = {}, {}
    i_px = s_px = 100.0
    for d in days:
        index[d], stock[d] = i_px, s_px
        r = rng.normal(0, 0.01)
        i_px *= 1 + r
        s_px *= 1 + 1.3 * r + rng.normal(0, 0.002)
    b = R.beta_from_closes(stock, index, 250, 120)
    assert b.known and b.sessions == 250 and b.value == pytest.approx(1.3, abs=0.05)
    short = R.beta_from_closes(dict(list(stock.items())[:100]), index, 250, 120)
    assert not short.known and short.value == 1.0 and short.sessions == 99
    assert not R.beta_from_closes({d: 1.0 for d in days}, {d: 5.0 for d in days}, 250, 120).known


# --- the inputs (lake, classification) -----------------------------------------------------------------------------------

def _close_rows(key, source, closes, start=date(2025, 6, 2)):
    rows = []
    day = start
    for c in closes:
        while day.weekday() >= 5:
            day += timedelta(days=1)
        end = datetime.combine(day, dtime(15, 30), IST).astimezone(UTC)
        rows.append(MdCandleRecord(instrument_key=key, timeframe="day", ts=end, source=source, version=1, open=c, high=c, low=c, close=c,
                                   volume=1, ingested_at=datetime(2025, 1, 1, tzinfo=UTC)))
        day += timedelta(days=1)
    return rows, day


def test_beta_and_bucket_come_from_the_lake_and_the_classification_known_yesterday(monkeypatch):
    sym = _sym("B")
    rng = np.random.default_rng(11)
    r = rng.normal(0, 0.01, 260)
    idx = list(100 * np.cumprod(1 + r))
    stk = list(50 * np.cumprod(1 + 0.7 * r + rng.normal(0, 0.001, 260)))
    nifty_key = f"NSE_INDEX:F1{uuid.uuid4().hex[:6].upper()}"
    monkeypatch.setitem(D.INDEX_KEYS, "NIFTY", nifty_key)
    rows_i, today = _close_rows(nifty_key, "nse_bhav:index", idx)
    rows_s, _ = _close_rows(f"NSE:{sym}", "nse_bhav:cm", stk)
    # Today's own bar (a huge move) is not known before today's close: it changes nothing.
    rows_s += _close_rows(f"NSE:{sym}", "nse_bhav:cm", [stk[-1] * 3], start=today)[0]
    rows_s += _close_rows(f"NSE:{sym}", "broker:kite", [1.0] * 5, start=today - timedelta(days=10))[0]   # never read

    async def seed():
        async with _session_factory() as session:
            session.add_all(rows_i + rows_s)
            isin = f"INF1{uuid.uuid4().hex[:8].upper()}"
            session.add(SymbolHistoryRecord(isin=isin, symbol=sym, valid_from=date(2020, 1, 1), source="test", checksum="x"))
            session.add(ClassificationRecord(isin=isin, scheme="NSE", sector="Capital Goods", valid_from=date(2020, 1, 1), source="test",
                                             checksum="x"))
            await session.commit()
    _run(seed())
    D.clear_cache()

    async def go():
        async with _session_factory() as session:
            return (await D.beta_of(session, sym, today), await D.bucket_of(session, sym, today), await D.bucket_of(session, "NIFTY", today),
                    await D.bucket_of(session, _sym("U"), today), await D.beta_of(session, "NIFTY", today),
                    await D.beta_of(session, "SENSEX", today), await D.beta_of(session, _sym("N"), today))
    beta, bucket, index_bucket, unknown, nifty, sensex, none = _run(go())
    assert beta.known and beta.value == pytest.approx(0.7, abs=0.05) and beta.sessions == 250 and "lake" in beta.source
    assert bucket == "Capital Goods" and index_bucket == R.INDEX_BUCKET and unknown.startswith("UNCLASSIFIED:")
    assert nifty == R.Beta(1.0, 0, True, "definition")
    assert not sensex.known and "no NSE index series" in sensex.source
    assert not none.known and none.value == 1.0


# --- the route ------------------------------------------------------------------------------------------------------------

def _trade(tenant_id, user_id, symbol, direction="LONG", qty=10.0, entry=100.0, stop=95.0, mode="PAPER", closed=False, mark=None):
    now = datetime.now(UTC)
    return TradeRecord(tenant_id=tenant_id, user_id=user_id, mode=mode, symbol=symbol, strategy_id="f1", direction=direction,
                       entry_time=now - timedelta(hours=1), entry_price=entry, quantity=qty, stop_loss=stop, mark_price=mark,
                       exit_time=now if closed else None, exit_price=entry if closed else None, pnl=0.0 if closed else None)


def test_the_route_shows_the_caller_s_own_open_paper_book_only(monkeypatch):
    t = _tenant(_email("own"), with_credentials=False)
    member_headers = _member(_email("member"), t["tenant_id"], "USER")
    mine, theirs, live, closed = _sym("M"), _sym("T"), _sym("L"), _sym("C")

    async def seed():
        async with _session_factory() as session:
            member = await session.scalar(select(User).where(User.tenant_id == t["tenant_id"], User.id != t["user_id"]))
            session.add_all([_trade(t["tenant_id"], t["user_id"], mine, mark=110.0), _trade(t["tenant_id"], member.id, theirs),
                             _trade(t["tenant_id"], t["user_id"], live, mode="LIVE"), _trade(t["tenant_id"], t["user_id"], closed, closed=True)])
            await session.commit()
    _run(seed())

    def no_broker(*a, **k):
        raise AssertionError("the risk view never reads a broker session")
    from app.market_data import live_access
    monkeypatch.setattr(live_access, "user_source", no_broker)
    url = "/api/portfolio/risk"
    off = client.get(url, headers=t["headers"])
    assert off.status_code == 503 and off.headers.get("X-Feature-Disabled") == "portfolio_risk"
    _flag("portfolio_risk", t["tenant_id"])
    v = client.get(url, headers=t["headers"]).json()
    symbols = {s for b in v["buckets"] for s in b["symbols"]}
    assert symbols == {mine} and v["positions"] == 1 and v["mode"] == "PAPER"
    assert v["exposure"]["gross"] == 1100.0 and v["open_risk"]["total"] == 50.0 and v["capital"] > 0
    other = client.get(url, headers=member_headers).json()
    assert {s for b in other["buckets"] for s in b["symbols"]} == {theirs}


def test_flag_is_off_by_default_and_the_view_is_not_an_order_path():
    assert D.FLAG in DEFAULT_OFF_FLAGS
    graph = CallGraph(gated=GATED)
    mine = {name for name in graph.functions if name.startswith(("app.portfolio.risk", "app.portfolio.routes.risk"))}
    assert "app.portfolio.risk_data.risk_view" in mine and "app.portfolio.routes.risk" in mine
    assert not mine & graph.reaching()
