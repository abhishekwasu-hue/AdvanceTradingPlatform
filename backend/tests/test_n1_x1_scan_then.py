"""N1 U4 X1: scan Then - Notify / One-click (flag scan_then off by default; intents need execution_core).

A scan rule's match is watched by the ported level gate (Daily trend from the lake, Z7a zones as areas, closed
confirmation bars); a CONFIRMED setup becomes an execution-core intent with the gate's ticket, which the core routes to
a notification or a PAPER entry proposal. Nothing on this path reaches an order: the guard at the bottom walks the Z10-c
call graph. The gate run here is the L1 golden case of a live entry (Trade's own output), so the setup is a real one."""
import ast
import asyncio
import gzip
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy import select

from app.db.models import (AiActionRecord, AlertEventRecord, AlertRuleRecord, ExecutionIntentRecord, MdCandleRecord, NotificationRecord,
                           OrderRecord, ScanThenTemplateRecord)
from app.execution.core import dispatcher, intents
from app.market_data.calendar import IST
from app.price_action.daily_trend import fold as daily_fold
from app.price_action.level_gate import gate as G
from app.price_action.level_gate import status as ST
from app.screener.runtime import SymbolData
from app.screener.then import areas as A
from app.screener.then import runner, templates as T, trend
from tests.order_path_graph import CallGraph
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import _tenant
from tests.test_z10c_order_paths import GATED
from tests.test_z10c_tenancy_fixes import _member
from tests.test_n1_e1_execution_core import _flag

UTC = timezone.utc
BACKEND = Path(__file__).resolve().parents[1]
GOLDEN = BACKEND / "tests" / "fixtures" / "level_gate" / "golden.json.gz"


def _run(coro):
    return asyncio.run(coro)


def _email(tag):
    return f"n1x1-{tag}-{uuid.uuid4().hex[:6]}@example.com"


# --- the L1 golden live entry the runner replays ----------------------------------------------------------------------

def _golden():
    with gzip.open(GOLDEN, "rt", encoding="utf-8") as fh:
        return json.load(fh)


GOLD = _golden()
CASE = next(c for c in GOLD["prefix"] if c["out"]["entry"] and c["series"] == 0 and c["ctx"] == "up" and not c["settings"])
STATE = next(c["state"] for c in GOLD["contexts"] if c["series"] == 0 and c["name"] == "up" and not c["settings"])
CTX = G.context_from_state(SimpleNamespace(trend=STATE["trend"], band=None, phase=STATE["phase"], counter=STATE["counter"],
                                           protected=SimpleNamespace(price=STATE["protected"]), origin_risk=STATE["origin_risk"],
                                           mature=STATE["mature"]), {}, asof="2026-09-22")
AREA = tuple(CASE["area"])
OTHERS = [tuple(a) for a in CASE["others"]]


def _bars(upto: int) -> pd.DataFrame:
    bars = GOLD["series"][0]["bars"]
    df = pd.DataFrame({k: v[:upto + 1] for k, v in bars.items() if k != "timestamp"})
    df.index = pd.to_datetime(bars["timestamp"][:upto + 1]).tz_localize(IST)          # a broker frame: IST-aware bar starts
    return df


LAST = pd.Timestamp(GOLD["series"][0]["bars"]["timestamp"][CASE["m"]]).tz_localize(IST)
NOW = (LAST + pd.Timedelta(minutes=5, seconds=20)).to_pydatetime()                     # the commitment candle has just closed


def _fetch(upto=None, calls=None):
    async def fetch(session, tenant_id, symbols, exchange, tf, lookback, user_id=None):
        if calls is not None:
            calls.append((tenant_id, tuple(symbols), tf, user_id))
        return [SymbolData(symbol=s, frames={tf: _bars(CASE["m"] if upto is None else upto)}) for s in symbols], {}, "test"
    return fetch


def _patch_market(monkeypatch, ctx=None, areas=None, warm=None):
    """`warm`: a set of keys whose Daily context is computed (None: every key is warm); a key the real warm-up cached
    counts as computed too."""
    def cold(key, today, clock=None):
        return warm is not None and key not in warm and (key, today) not in trend._CACHE

    async def context(session, key, today, gs=None, clock=None, allow_cold=True):
        if cold(key, today):
            if not allow_cold:
                return {**trend._failed(trend.R_PENDING), "pending": True}
            warm.add(key)
        return dict(ctx if ctx is not None else CTX)

    async def zones(session, key, tenant_id, at):
        return list(areas if areas is not None else [AREA] + OTHERS)
    monkeypatch.setattr(trend, "daily_context", context)
    monkeypatch.setattr(trend, "is_cold", cold)
    monkeypatch.setattr(A, "areas_for", zones)


def _setup(tag, mode="ONE_CLICK", flags=("scan_then", "execution_core"), symbol="GATEDEMO", symbols=None):
    t = _tenant(_email(tag), with_credentials=False)
    for name in flags:
        _flag(name, t["tenant_id"])

    async def go():
        async with _session_factory() as session:
            rule = AlertRuleRecord(tenant_id=t["tenant_id"], created_by=t["user_id"], name="Demand scan", kind="screen", base_tf="5m",
                                   status="active", exchange="NSE")
            session.add(rule)
            await session.flush()
            for sym in symbols or [symbol]:
                session.add(AlertEventRecord(tenant_id=t["tenant_id"], rule_id=rule.id, symbol=sym, condition_hash="x" * 8,
                                             bar_time=(LAST - pd.Timedelta(minutes=10)).to_pydatetime(), idem_key=uuid.uuid4().hex,
                                             priority="normal", values_json="{}", status="sent"))
            session.add(ScanThenTemplateRecord(tenant_id=t["tenant_id"], rule_id=rule.id, user_id=t["user_id"], mode=mode, confirm_tf="5m",
                                               gate_settings_json=json.dumps(CASE["settings"]), enabled=True))
            await session.commit()
            return rule.id
    return t, _run(go())


def _runner(now=NOW, fetch=None, **kw):
    async def go():
        async with _session_factory() as session:
            return await runner.run_due(session, now, fetch=fetch or _fetch(), **kw)
    return _run(go())


def _rows(model, tenant_id):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(model).where(model.tenant_id == tenant_id).order_by(model.id)))
    return _run(go())


def _template(rule_id):
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(ScanThenTemplateRecord).where(ScanThenTemplateRecord.rule_id == rule_id))
    return _run(go())


def _drain(now=None):
    """Within the intent's time window: it was raised at the commitment candle's close (NOW)."""
    async def go():
        async with _session_factory() as session:
            return await dispatcher.drain(session, now or (NOW + timedelta(minutes=1)))
    return _run(go())


# --- pure pieces --------------------------------------------------------------------------------------------------------

def test_template_checks():
    assert T.check_mode("one_click") == "ONE_CLICK" and T.check_mode("AUTO_PAPER") == "AUTO_PAPER"
    with pytest.raises(T.TemplateError, match="Auto LIVE"):
        T.check_mode("AUTO_LIVE")
    with pytest.raises(T.TemplateError):
        T.check_mode("SOMETHING")
    assert T.check_gate({"min_rr": 2, "wait_next_candle": True}) == {"min_rr": 2.0, "wait_next_candle": True}
    for bad in ({"min_rr": 0.5}, {"max_armed_bars": 99}, {"wait_next_candle": 1}, {"trend_retry_min": 5}, {"min_rr": "3"},
                {"max_armed_bars": 2.5}):                                       # review of X1b: an integer is never truncated
        with pytest.raises(T.TemplateError):
            T.check_gate(bad)
    assert T.FLAG in __import__("app.platform.controls", fromlist=["x"]).DEFAULT_OFF_FLAGS


def test_area_of_widens_a_level_and_orders_the_edges():
    assert A.area_of(102.0, 100.0) == (100.0, 102.0)
    lo, hi = A.area_of(20000.0, 20000.0)
    assert hi > lo and abs((hi - lo) - 20000.0 * A.LEVEL_WIDTH) < 0.02 and (lo + hi) / 2 == pytest.approx(20000.0, abs=0.01)
    assert A.area_of(0.0, 5.0) is None


def test_status_codes_follow_trade():
    assert ST.status_of({"entry": True, "state": G.CONFIRMED}) == ST.CONFIRMED_ENTRY
    assert ST.status_of({"state": G.ARMED, "absorbing": True}) == "SKIPPED_GATE_ABSORPTION"
    assert ST.status_of({"state": G.ARMED, "low_volume": True}) == "SKIPPED_GATE_LOW_VOLUME"
    assert ST.status_of({"state": G.ARMED, "reason": G.R_NO_COMMIT}) == "SKIPPED_GATE_WAITING_COMMITMENT"
    assert ST.status_of({"state": G.DISARMED, "reason": f"{G.R_RR} (1.2 < 3)"}) == "SKIPPED_GATE_RR"
    assert ST.status_of({"state": G.DISARMED, "reason": G.R_AGAINST}) == "SKIPPED_GATE_AGAINST_TREND"
    assert ST.status_of({"state": G.CONFIRMED, "entry": False}) == "SKIPPED_GATE_ALREADY_CONFIRMED"
    assert ST.status_of({"state": "ERROR", "reason": "x"}) == "SKIPPED_GATE_ERROR"
    assert ST.status_of({"state": G.IDLE, "reason": "price not at area"}) == "SKIPPED_GATE_NOT_AT_AREA"


# --- the Daily trend bridge (L2-5) ----------------------------------------------------------------------------------------

def _daily_rows(key, days=260, seed=5):
    import numpy as np
    rng = np.random.default_rng(seed)
    price, rows = 1000.0, []
    for i, day in enumerate(pd.bdate_range("2025-09-01", periods=days)):
        drift = 0.004 if (i // 25) % 3 != 2 else -0.006
        o = price
        c = o * (1 + drift + rng.normal(0, 0.008))
        end = pd.Timestamp(day).tz_localize(IST) + pd.Timedelta(hours=15, minutes=30)
        rows.append(MdCandleRecord(instrument_key=key, timeframe="1d", ts=end.tz_convert(UTC).to_pydatetime(), source="nse_bhav:cm", version=1,
                                   open=round(o, 2), high=round(max(o, c) * 1.004, 2), low=round(min(o, c) * 0.996, 2), close=round(c, 2),
                                   volume=1000, ingested_at=datetime(2025, 1, 1, tzinfo=UTC)))
        price = c
    return rows


def test_daily_context_from_the_lake_matches_the_ported_fold_and_is_computed_once_per_day(monkeypatch):
    key = f"NSE:X1T{uuid.uuid4().hex[:5].upper()}"
    rows = _daily_rows(key)
    today = (rows[-1].ts.astimezone(IST) + timedelta(days=1)).date()

    # Review L1: a broker's or a stream's Daily bars are never read (end-of-day sources only), even on a day the
    # bhavcopy has no bar or when they differ.
    last = rows[-1]
    foreign = [MdCandleRecord(instrument_key=key, timeframe="1d", ts=last.ts - timedelta(hours=1), source=src, version=9, open=1.0,
                              high=1.0, low=1.0, close=1.0, volume=1, ingested_at=datetime(2025, 1, 1, tzinfo=UTC))
               for src in ("broker:kite", "stream:upstox")]
    foreign.append(MdCandleRecord(instrument_key=key, timeframe="1d", ts=last.ts, source="broker:kite", version=9, open=1.0, high=1.0,
                                  low=1.0, close=1.0, volume=1, ingested_at=datetime(2025, 1, 1, tzinfo=UTC)))

    async def seed():
        async with _session_factory() as session:
            session.add_all(rows + foreign)
            await session.commit()
    _run(seed())
    trend.clear_cache()
    reads = []
    real = trend.lake_daily

    async def counting(session, k, d):
        reads.append(k)
        return await real(session, k, d)
    monkeypatch.setattr(trend, "lake_daily", counting)

    async def go():
        async with _session_factory() as session:
            first = await trend.daily_context(session, key, today)
            second = await trend.daily_context(session, key, today, {"allow_parent_correction": True})
            bars = await real(session, key, today)
            return first, second, bars
    first, second, bars = _run(go())
    assert reads == [key]                                                       # one read for the day
    expected = G.context_from_state(daily_fold.fold(daily_fold.prepare_daily(bars, pd.Timestamp(today)))[-1], {},
                                    asof=first.get("asof"))
    assert first == expected and first["asof"] == str(rows[-1].ts.astimezone(IST).date())
    assert second["trend"] == first["trend"]
    # Today's own bar is never read (known at yesterday's close): a row ending today changes nothing.
    assert len(bars) == len(rows) and float(bars["close"].iloc[-1]) == float(last.close)


def test_daily_context_fails_closed_and_retries_later(monkeypatch):
    trend.clear_cache()
    clock = [1000.0]

    async def broken(session, k, d):
        raise RuntimeError("lake down")
    monkeypatch.setattr(trend, "lake_daily", broken)

    async def go(key):
        async with _session_factory() as session:
            return await trend.daily_context(session, key, date(2026, 9, 23), clock=lambda: clock[0])
    out = _run(go("NSE:BROKEN"))
    assert out["ok"] is False and out["sides"] == set() and "RuntimeError" in out["reason"]
    calls = []

    async def short(session, k, d):
        calls.append(k)
        return pd.DataFrame({"timestamp": pd.to_datetime(["2026-09-01 10:00"], utc=True), "open": [1.0], "high": [1.0], "low": [1.0],
                             "close": [1.0]})
    monkeypatch.setattr(trend, "lake_daily", short)
    assert _run(go("NSE:BROKEN"))["ok"] is False and calls == []                # negative cache: not retried yet
    clock[0] += trend.RETRY_SECONDS
    out = _run(go("NSE:BROKEN"))
    assert calls == ["NSE:BROKEN"] and out["ok"] is False and "too short" in out["reason"]
    assert _run(go(None))["ok"] is False


def test_areas_come_from_the_organisation_s_live_zones(monkeypatch):
    from app.price_action.zones import read
    rows = [{"zone_key": "a", "zone_type": "demand", "timeframe": "1h", "zone_low": 100.0, "zone_high": 101.0, "strength": 2.0, "state": "FRESH"},
            {"zone_key": "b", "zone_type": "sr_level", "timeframe": "1h", "zone_low": 110.0, "zone_high": 110.0, "strength": 1.0, "state": "TESTED"}]
    seen = []

    async def as_of(session, key, at, tenant_id, allow_platform_fallback=False):
        seen.append((key, tenant_id, allow_platform_fallback))
        return read.Snapshot(1, datetime(2026, 9, 23, tzinfo=UTC), None, None, rows)
    monkeypatch.setattr(read, "read_as_of", as_of)
    out = _run(A.areas_for(None, "NSE:ABC", 7, datetime.now(UTC)))
    assert (100.0, 101.0) in out and any(lo < 110.0 < hi for lo, hi in out) and seen == [("NSE:ABC", 7, False)]
    assert _run(A.areas_for(None, None, 7, datetime.now(UTC))) == []


# --- the runner -----------------------------------------------------------------------------------------------------------

def test_a_confirmed_setup_becomes_one_one_click_intent_then_a_paper_proposal(monkeypatch):
    _patch_market(monkeypatch)
    t, rule_id = _setup("oneclick")
    calls = []
    report = _runner(fetch=_fetch(calls=calls))
    assert report.submitted == 1 and calls and calls[0][2] == "5m" and calls[0][3] is None
    [intent] = _rows(ExecutionIntentRecord, t["tenant_id"])
    assert intent.status == intents.QUEUED and intent.requested_mode == "ONE_CLICK" and intent.user_id == t["user_id"]
    assert intent.source == "scan" and intent.symbol == "GATEDEMO" and intent.direction == "LONG"
    assert intent.idempotency_key == f"scan:{rule_id}:GATEDEMO:{G.BULL}:{CASE['out']['confirm_ts']}"    # no area in the key
    evidence = json.loads(intent.evidence_json)
    assert evidence["area"] == list(AREA)
    assert evidence["gate_status"] == ST.CONFIRMED_ENTRY and evidence["ticket"]["entry_price"] == CASE["out"]["entry_price"]
    assert evidence["ticket"]["sl"] == CASE["out"]["sl"] and evidence["ticket"]["study"].startswith("inside demand")
    signal = json.loads(intent.signal_json)
    assert signal["stop_loss"] == CASE["out"]["sl"] and signal["target1"] == CASE["out"]["target"] and signal["timeframe_combo"] == "5m"
    tpl = _template(rule_id)
    assert tpl.last_bar_at is not None and json.loads(tpl.last_status_json)["GATEDEMO"]["code"] == ST.CONFIRMED_ENTRY
    # The same bar again: nothing new (once per closed bar; the key would dedupe anyway).
    assert _runner(fetch=_fetch()).submitted == 0 and len(_rows(ExecutionIntentRecord, t["tenant_id"])) == 1
    _drain()
    [action] = _rows(AiActionRecord, t["tenant_id"])
    assert action.action == "ENTRY" and action.status == "PROPOSED" and action.owner_user_id == t["user_id"]
    assert _rows(OrderRecord, t["tenant_id"]) == []


def test_notify_mode_sends_a_notification_only(monkeypatch):
    _patch_market(monkeypatch)
    t, _ = _setup("notify", mode="NOTIFY")
    assert _runner().submitted == 1
    _drain()
    notes = [n for n in _rows(NotificationRecord, t["tenant_id"]) if n.event_type == "SCREEN_ALERT"]
    assert len(notes) == 1 and "Notify only" in notes[0].message
    assert _rows(AiActionRecord, t["tenant_id"]) == [] and _rows(OrderRecord, t["tenant_id"]) == []


def test_auto_paper_stays_a_proposal(monkeypatch):
    _patch_market(monkeypatch)
    t, _ = _setup("autopaper", mode="AUTO_PAPER")
    assert _runner().submitted == 1
    _drain()
    [intent] = _rows(ExecutionIntentRecord, t["tenant_id"])
    assert intent.route == "PROPOSE" and len(_rows(AiActionRecord, t["tenant_id"])) == 1 and _rows(OrderRecord, t["tenant_id"]) == []


def test_no_entry_off_the_commitment_candle_against_the_trend_or_without_areas(monkeypatch):
    # One bar later the commitment candle is no longer the last closed bar: confirmed earlier, no live entry.
    _patch_market(monkeypatch)
    t, rule_id = _setup("later")
    later = (LAST + pd.Timedelta(minutes=10, seconds=20)).to_pydatetime()
    assert _runner(now=later, fetch=_fetch(upto=CASE["m"] + 1)).submitted == 0
    assert json.loads(_template(rule_id).last_status_json)["GATEDEMO"]["code"] == "SKIPPED_GATE_ALREADY_CONFIRMED"
    # Trend unclear (fail-closed context): the arrival is refused.
    _patch_market(monkeypatch, ctx={"ok": False, "trend": "UNKNOWN", "sides": set(), "reason": "no entry: trend unclear (test)"})
    t2, rule2 = _setup("notrend")
    assert _runner().submitted == 0 and _rows(ExecutionIntentRecord, t2["tenant_id"]) == []
    assert json.loads(_template(rule2).last_status_json)["GATEDEMO"]["code"] == "SKIPPED_GATE_NO_TREND"
    _patch_market(monkeypatch, areas=[])
    t3, rule3 = _setup("noareas")
    assert _runner().submitted == 0 and json.loads(_template(rule3).last_status_json)["GATEDEMO"]["code"] == runner.NO_AREAS


def test_data_short_of_the_expected_bar_is_not_evaluated_and_retried(monkeypatch):
    _patch_market(monkeypatch)
    t, rule_id = _setup("lag")
    assert _runner(fetch=_fetch(upto=CASE["m"] - 1)).submitted == 0              # the broker has not delivered the bar yet
    tpl = _template(rule_id)
    assert tpl.last_bar_at is None and json.loads(tpl.last_status_json)["GATEDEMO"]["code"] == runner.WAITING_DATA
    assert _runner().submitted == 1                                              # retried next cycle, the bar is in


def _fetch_by(upto, calls=None):
    """Bars per symbol: `upto[symbol]` (default the commitment candle)."""
    async def fetch(session, tenant_id, symbols, exchange, tf, lookback, user_id=None):
        if calls is not None:
            calls.append(tuple(symbols))
        return [SymbolData(symbol=s, frames={tf: _bars(upto.get(s, CASE["m"]))}) for s in symbols], {}, "test"
    return fetch


def _evaluate(rule_id, fetch, cycle, now=NOW):
    """One template on its own (the runner's other templates - from other tests - never share its cycle)."""
    async def go():
        async with _session_factory() as session:
            tpl = await session.scalar(select(ScanThenTemplateRecord).where(ScanThenTemplateRecord.rule_id == rule_id))
            rule = await session.get(AlertRuleRecord, rule_id)
            report = runner.RunReport()
            await runner.evaluate_template(session, tpl, rule, now, fetch, set(), False, report, cycle)
            await session.commit()
            return report
    return _run(go())


def _cycle(seconds=60.0, cold=1):
    import time
    return runner.CycleState(deadline=time.monotonic() + seconds, cold_left=cold)


def test_one_intent_per_side_and_commitment_candle_even_with_overlapping_areas(monkeypatch):
    """Review of #214 (M1): overlapping zones each confirm the same candle; one intent, the best area in its evidence."""
    wide = (round(AREA[0] - 0.5, 2), round(AREA[1] + 0.5, 2))
    above = (round(AREA[0] + 1.0, 2), round(AREA[1] + 3.0, 2))
    _patch_market(monkeypatch, areas=[wide, AREA, above] + OTHERS)
    t, rule_id = _setup("overlap")
    assert _evaluate(rule_id, _fetch(), _cycle()).submitted == 1
    [intent] = _rows(ExecutionIntentRecord, t["tenant_id"])
    assert tuple(json.loads(intent.evidence_json)["area"]) in {wide, AREA, above}
    assert intent.idempotency_key == f"scan:{rule_id}:GATEDEMO:{G.BULL}:{CASE['out']['confirm_ts']}"
    # The pick: grade A first, then the larger reward:risk, then the tighter risk.
    b3 = {"grade": "B", "rr": 3.0, "entry_price": 100.0, "sl": 98.0}
    assert runner._pick([((1, 2), b3), ((3, 4), {**b3, "grade": "A", "rr": 2.0})])[0] == (3, 4)
    assert runner._pick([((1, 2), b3), ((3, 4), {**b3, "rr": 3.5})])[0] == (3, 4)
    assert runner._pick([((1, 2), b3), ((3, 4), {**b3, "sl": 99.0})])[0] == (3, 4)


def test_a_lagging_symbol_is_refetched_alone_and_given_up_after_n_cycles(monkeypatch):
    """Review of #214 (M2): the status keeps each symbol's evaluated bar; only lagging symbols are fetched again, and one
    still short after MAX_LAG_TRIES cycles is given up for the bar (DATA_LATE), which then lets the template's bar advance."""
    _patch_market(monkeypatch)
    t, rule_id = _setup("laggy", symbols=["GATEDEMO", "LAGGY"])
    calls = []
    fetch = _fetch_by({"LAGGY": CASE["m"] - 1}, calls)
    assert _evaluate(rule_id, fetch, _cycle()).submitted == 1                       # GATEDEMO confirmed on its bar
    status = json.loads(_template(rule_id).last_status_json)
    assert status["GATEDEMO"]["code"] == ST.CONFIRMED_ENTRY and status["LAGGY"]["code"] == runner.WAITING_DATA
    assert status["LAGGY"]["tries"] == 1 and _template(rule_id).last_bar_at is None
    for n in range(2, runner.MAX_LAG_TRIES + 1):
        assert _evaluate(rule_id, fetch, _cycle()).submitted == 0
        assert calls[-1] == ("LAGGY",)                                               # the done symbol is not fetched again
    tpl = _template(rule_id)
    assert json.loads(tpl.last_status_json)["LAGGY"]["code"] == runner.DATA_LATE and tpl.last_bar_at is not None
    before = len(calls)
    _evaluate(rule_id, fetch, _cycle())
    assert len(calls) == before and len(_rows(ExecutionIntentRecord, t["tenant_id"])) == 1   # nothing more for this bar


def test_many_cold_symbols_stop_at_the_cycle_s_fold_and_time_budget(monkeypatch):
    """Review of #214 (H1): a cycle runs at most its cold-fold budget (the rest wait as TREND_PENDING) and stops at its
    deadline; the background warm-up computes the rest, then the next cycle evaluates them all."""
    names = ["GATEDEMO", "GATEDEMOB", "GATEDEMOC", "GATEDEMOD", "GATEDEMOE"]
    warm: set = set()
    trend.clear_cache()

    async def lake(session, key, today):
        return _short_daily()
    monkeypatch.setattr(trend, "lake_daily", lake)
    _patch_market(monkeypatch, warm=warm)
    t, rule_id = _setup("cold", symbols=names)
    assert _evaluate(rule_id, _fetch(), _cycle(cold=1)).submitted == 1
    status = json.loads(_template(rule_id).last_status_json)
    assert [status[n]["code"] for n in names] == [ST.CONFIRMED_ENTRY] + [runner.TREND_PENDING] * 4
    assert warm == {"NSE:GATEDEMO"} and _template(rule_id).last_bar_at is None
    report = _evaluate(rule_id, _fetch(), _cycle(seconds=0.0, cold=1))              # out of time: nothing evaluated
    assert report.deferred == 1 and report.submitted == 0 and warm == {"NSE:GATEDEMO"}

    folded = _run(runner.warm_due(_session_factory, NOW))                           # outside the cycle
    assert folded.folded >= 4 and all((f"NSE:{n}", NOW.date()) in trend._CACHE for n in names[1:])
    assert _evaluate(rule_id, _fetch(), _cycle(cold=0)).submitted == 4
    assert _template(rule_id).last_bar_at is not None and len(_rows(ExecutionIntentRecord, t["tenant_id"])) == 5


def _short_daily():
    return pd.DataFrame({"timestamp": pd.to_datetime(["2026-09-01 10:00"], utc=True), "open": [1.0], "high": [1.0], "low": [1.0],
                         "close": [1.0]})


class _CountingFactory:
    """A session factory that knows how many of its sessions are open."""
    def __init__(self):
        self.open = 0
        self.made = 0

    def __call__(self):
        factory = self

        class _Ctx:
            async def __aenter__(self):
                factory.open += 1
                factory.made += 1
                self.session = _session_factory()
                return await self.session.__aenter__()

            async def __aexit__(self, *exc):
                factory.open -= 1
                return await self.session.__aexit__(*exc)
        return _Ctx()


def test_the_warm_up_reads_each_key_in_its_own_closed_session_and_is_bounded(monkeypatch):
    """Re-review W1: every key is read with its own session, closed before the fold; W2: at most `limit` folds per run,
    the rest reported in order and folded by the next run."""
    trend.clear_cache()
    factory = _CountingFactory()
    at_read, at_fold = [], []

    async def lake(session, key, today):
        at_read.append(factory.open)
        return _short_daily()

    def last_state(bars, today):
        at_fold.append(factory.open)
        return None, None, "too short (test)"
    monkeypatch.setattr(trend, "lake_daily", lake)
    monkeypatch.setattr(trend, "_last_state", last_state)
    keys = [f"NSE:W{i}" for i in range(10)]
    today = date(2026, 9, 23)
    first = _run(trend.warm(factory, keys, today, limit=4))
    assert first.folded == 4 and first.skipped == keys[4:] and at_read == [1] * 4 and at_fold == [0] * 4 and factory.made == 4
    second = _run(trend.warm(factory, keys, today, limit=4))
    assert second.folded == 4 and second.skipped == keys[8:]
    assert _run(trend.warm(factory, keys, today, limit=4)).skipped == [] and factory.open == 0
    assert trend.WARM_PER_RUN <= trend.MAX_ENTRIES // 2


def test_the_trend_cache_is_lru_and_drops_other_days_first(monkeypatch):
    trend.clear_cache()
    monkeypatch.setattr(trend, "MAX_ENTRIES", 3)
    today, before = date(2026, 9, 23), date(2026, 9, 22)
    entry = trend._Entry(None, None, "x", 0.0)
    trend._store(("A", before), entry)
    trend._store(("B", today), entry)
    trend._store(("C", today), entry)
    trend._store(("D", today), entry)                                   # full: yesterday's goes first
    assert ("A", before) not in trend._CACHE
    trend._get(("B", today))                                             # B used: C is now the least recent
    trend._store(("E", today), entry)
    assert list(trend._CACHE) == [("D", today), ("B", today), ("E", today)]


def test_a_key_being_folded_is_pending_for_the_cycle_and_never_folded_twice(monkeypatch):
    trend.clear_cache()
    reads = []

    async def lake(session, key, today):
        reads.append(key)
        return _short_daily()
    monkeypatch.setattr(trend, "lake_daily", lake)
    today = date(2026, 9, 23)
    trend._INFLIGHT.add(("NSE:BUSY", today))
    try:
        out = _run(trend.daily_context(None, "NSE:BUSY", today, allow_cold=True))
        assert out["pending"] is True and out["sides"] == set()
        assert _run(trend.warm(_session_factory, ["NSE:BUSY"], today)).folded == 0 and reads == []
    finally:
        trend.clear_cache()


def test_the_warm_up_takes_today_s_matches_first(monkeypatch):
    _patch_market(monkeypatch)
    t, rule_id = _setup("order")

    async def universe():
        async with _session_factory() as session:
            rule = await session.get(AlertRuleRecord, rule_id)
            rule.universe_json = json.dumps(["AAAORDER", "GATEDEMO"])
            await session.commit()
    _run(universe())

    async def keys():
        async with _session_factory() as session:
            return await runner.watched_keys(session, NOW)
    got = _run(keys())
    assert "NSE:AAAORDER" in got and got.index("NSE:GATEDEMO") < got.index("NSE:AAAORDER") and got.count("NSE:GATEDEMO") == 1


def test_long_and_short_on_the_same_candle_are_both_refused(monkeypatch):
    """Re-review: two areas confirming opposite sides on one candle conflict - neither is sent, with the reason."""
    _patch_market(monkeypatch)
    t, rule_id = _setup("conflict")
    real = G.safe_evaluate

    def both(bars, area, ctx, others, start, gs):
        r = real(bars, AREA, ctx, others, start, gs)
        return {**r, "side": G.BEAR} if area != AREA and r.get("entry") else r
    monkeypatch.setattr(G, "safe_evaluate", both)
    assert _evaluate(rule_id, _fetch(), _cycle()).submitted == 0 and _rows(ExecutionIntentRecord, t["tenant_id"]) == []
    st = json.loads(_template(rule_id).last_status_json)["GATEDEMO"]
    assert st["code"] == runner.CONFLICT and "both confirmed on the same candle" in st["lines"][0]


def test_stop_cancels_the_warm_up_and_the_vision_drain():
    from app.workers.trading_worker import TradingWorker

    async def go():
        worker = TradingWorker(_session_factory, cycle_seconds=60)
        worker._then_warm_task = asyncio.get_running_loop().create_task(asyncio.sleep(60))
        worker._vision_task = asyncio.get_running_loop().create_task(asyncio.sleep(60))
        worker.stop()
        await asyncio.sleep(0)
        return worker._then_warm_task.cancelled(), worker._vision_task.cancelled()
    assert _run(go()) == (True, True)


def test_a_template_owner_who_may_not_trade_is_skipped(monkeypatch):
    """Review of #214 (L3): the owner lost the trading role or scope - nothing is watched, with the reason."""
    from app.db.models import User
    _patch_market(monkeypatch)
    t, rule_id = _setup("cantrade")

    async def demote():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            user.role = "VIEWER"
            await session.commit()
    _run(demote())
    assert _evaluate(rule_id, _fetch(), _cycle()).submitted == 0
    tpl = _template(rule_id)
    assert "may not trade" in (tpl.last_problem or "") and _rows(ExecutionIntentRecord, t["tenant_id"]) == []


def test_nothing_runs_without_both_flags(monkeypatch):
    _patch_market(monkeypatch)
    t, rule1 = _setup("noflag", flags=("scan_then",))
    t2, rule2 = _setup("nocore", flags=("execution_core",))
    _runner()                                   # other tests' templates may still run; these two never do
    for tenant, rule_id in ((t, rule1), (t2, rule2)):
        assert _rows(ExecutionIntentRecord, tenant["tenant_id"]) == [] and _template(rule_id).last_status_json is None


# --- the routes -----------------------------------------------------------------------------------------------------------

def test_then_routes(monkeypatch):
    t, rule_id = _setup("routes", flags=("execution_core",))
    url = f"/api/alerts/rules/{rule_id}/then"
    assert client.get(url, headers=t["headers"]).status_code == 503                # flag off
    _flag("scan_then", t["tenant_id"])
    got = client.get(url, headers=t["headers"]).json()
    assert got["configured"] is True and got["mode"] == "ONE_CLICK"
    live = client.put(url, headers=t["headers"], json={"mode": "AUTO_LIVE"})
    assert live.status_code == 422 and "Auto LIVE" in live.text
    assert client.put(url, headers=t["headers"], json={"mode": "NOTIFY", "gate": {"min_rr": 0.1}}).status_code == 422
    assert client.put(url, headers=t["headers"], json={"mode": "NOTIFY", "confirm_tf": "1h"}).status_code == 422
    ok = client.put(url, headers=t["headers"], json={"mode": "NOTIFY", "confirm_tf": "15m", "gate": {"min_rr": 2.5}})
    assert ok.status_code == 200 and ok.json()["gate"] == {"min_rr": 2.5} and ok.json()["confirm_tf"] == "15m"
    member = _member(_email("member"), t["tenant_id"], "USER")
    assert client.put(url, headers=member, json={"mode": "NOTIFY"}).status_code == 403   # not the rule's creator
    viewer = _member(_email("viewer"), t["tenant_id"], "VIEWER")
    assert client.put(url, headers=viewer, json={"mode": "NOTIFY"}).status_code == 403
    # Review of #214 (L2): the last check per symbol is the template owner's and the OWNER's only.
    async def set_status(created_by="keep"):
        async with _session_factory() as session:
            tpl = await session.scalar(select(ScanThenTemplateRecord).where(ScanThenTemplateRecord.rule_id == rule_id))
            tpl.last_status_json, tpl.last_problem = json.dumps({"GATEDEMO": {"code": "WAITING_DATA", "lines": ["x"]}}), "p"
            if created_by != "keep":
                (await session.get(AlertRuleRecord, rule_id)).created_by = created_by
            await session.commit()
    _run(set_status())
    mine = client.get(url, headers=t["headers"]).json()
    assert mine["status"]["GATEDEMO"]["code"] == "WAITING_DATA" and mine["last_problem"] == "p"
    theirs = client.get(url, headers=member).json()
    assert theirs["configured"] is True and theirs["status"] == {} and theirs["last_problem"] is None
    # Info: an ownerless rule (its creator left) is the OWNER's to take over, never a member's.
    _run(set_status(created_by=None))
    assert client.put(url, headers=member, json={"mode": "NOTIFY"}).status_code == 403
    assert client.put(url, headers=t["headers"], json={"mode": "NOTIFY"}).status_code == 200
    # Review of X1b (MED): the OWNER saving a template another member set must confirm the take-over.
    async def set_template_user(user_id):
        async with _session_factory() as session:
            tpl = await session.scalar(select(ScanThenTemplateRecord).where(ScanThenTemplateRecord.rule_id == rule_id))
            tpl.user_id = user_id
            await session.commit()
    member_id = client.get("/api/auth/me", headers=member).json()["id"]
    _run(set_template_user(member_id))
    seen = client.get(url, headers=t["headers"]).json()
    assert seen["set_by_you"] is False and seen["may_change"] is True and seen["user_id"] == member_id
    assert client.get(url, headers=member).json()["may_change"] is False                  # (the rule is ownerless now)
    silent = client.put(url, headers=t["headers"], json={"mode": "NOTIFY"})
    assert silent.status_code == 409 and "Saving makes you its owner" in silent.text and _template(rule_id).user_id == member_id
    taken = client.put(url, headers=t["headers"], json={"mode": "NOTIFY", "take_over": True})
    assert taken.status_code == 200 and taken.json()["set_by_you"] is True and _template(rule_id).user_id == t["user_id"]
    other = _tenant(_email("other"), with_credentials=False)
    _flag("scan_then", other["tenant_id"])
    assert client.get(url, headers=other["headers"]).status_code == 404                  # another organisation's rule
    assert client.delete(url, headers=t["headers"]).json()["configured"] is False
    assert _template(rule_id) is None


# --- the guard ------------------------------------------------------------------------------------------------------------

def test_scan_then_is_not_an_order_path():
    """No function of the scan Then package reaches a broker order method (Z10-c graph), and the package names no order
    pipeline or broker; its routes write template rows only."""
    graph = CallGraph(gated=GATED)
    mine = {name for name in graph.functions if name.startswith("app.screener.then.")}
    assert "app.screener.then.runner.run_due" in mine and "app.screener.then.routes.put_then" in mine
    assert not mine & graph.reaching()
    forbidden = {"place_order", "modify_order", "place_stop_loss_order", "exit_position", "execute_signal_for_user", "execute_structure",
                 "OrderRouter", "get_broker_adapter", "build_adapter", "propose_entry", "dispatch_one", "drain"}
    for path in sorted((BACKEND / "app" / "screener" / "then").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            name = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None
            assert name not in forbidden, (path.name, name)


# --- X1b: the intents list and the frontend sample ------------------------------------------------------------------------

SAMPLE = BACKEND.parent / "frontend" / "src" / "screener" / "then" / "thenSample.json"


def _confirmed_rule(monkeypatch, tag):
    _patch_market(monkeypatch)
    t, rule_id = _setup(tag)
    assert _runner().submitted == 1
    _drain()
    return t, rule_id


def test_the_intents_list_is_the_caller_s_own(monkeypatch):
    t, rule_id = _confirmed_rule(monkeypatch, "intents")
    rows = client.get(f"/api/alerts/rules/{rule_id}/then/intents", headers=t["headers"]).json()
    assert len(rows) == 1 and rows[0]["gate_status"] == ST.CONFIRMED_ENTRY and rows[0]["route"] == "PROPOSE"
    assert rows[0]["ai_action_id"] is not None and rows[0]["stop_loss"] == CASE["out"]["sl"] and rows[0]["study"].startswith("inside demand")
    member = _member(_email("intents-member"), t["tenant_id"], "USER")
    assert client.get(f"/api/alerts/rules/{rule_id}/then/intents", headers=member).json() == []     # not theirs


def test_then_payloads_match_the_frontend_sample(monkeypatch):
    """frontend/src/screener/then/thenSample.json is the server's own payload (the Then drawer's tests render it): the
    same keys as the live GET /then and GET /then/intents, so the sample cannot drift from the API."""
    t, rule_id = _confirmed_rule(monkeypatch, "sample")
    template = client.get(f"/api/alerts/rules/{rule_id}/then", headers=t["headers"]).json()
    intents_ = client.get(f"/api/alerts/rules/{rule_id}/then/intents", headers=t["headers"]).json()
    sample = json.loads(SAMPLE.read_text())
    assert set(sample["template"]) == set(template)
    assert set(sample["template"]["gate_defaults"]) == set(template["gate_defaults"])
    assert set(sample["intents"][0]) == set(intents_[0])
    assert set(next(iter(sample["template"]["status"].values()))) == set(next(iter(template["status"].values())))
    assert not any(m == "AUTO_LIVE" for m in sample["template"]["modes"])
    assert template["status_shown"] is True and sample["template"]["status_shown"] is True
    member = _member(_email("sample-member"), t["tenant_id"], "USER")
    theirs = client.get(f"/api/alerts/rules/{rule_id}/then", headers=member).json()
    assert theirs["status_shown"] is False and theirs["status"] == {} and set(theirs) == set(template)
    # Review of X1b: the batch read gives every visible rule's template in the same shape (one read for the panel)
    batch = client.get("/api/alerts/then", headers=t["headers"]).json()["templates"]
    assert batch[str(rule_id)] == template and template["set_by_you"] is True and template["may_change"] is True
    their_batch = client.get("/api/alerts/then", headers=member).json()["templates"]
    assert their_batch[str(rule_id)]["status"] == {} and their_batch[str(rule_id)]["may_change"] is False


# --- shared-session safety (review of #219's pattern, applied to X1) ---------------------------------------------------------

def _bad_sql():
    from sqlalchemy import text
    return text("SELECT * FROM no_such_table_x1")


def test_a_failing_fetch_rolls_back_its_savepoint_and_later_templates_still_run(monkeypatch):
    """runner.py fetch: a SQL error inside the bars read is confined to its savepoint; the cycle goes on and commits."""
    _patch_market(monkeypatch)
    t_bad, bad_rule = _setup("fetchbad")
    t_ok, ok_rule = _setup("fetchok")
    seen = []

    async def fetch(session, tenant_id, symbols, exchange, tf, lookback, user_id=None):
        if tenant_id == t_bad["tenant_id"]:
            seen.append(session.in_nested_transaction())
            await session.execute(_bad_sql())
        return await _fetch()(session, tenant_id, symbols, exchange, tf, lookback, user_id)
    _runner(fetch=fetch)
    assert seen == [True]
    assert (_template(bad_rule).last_problem or "").startswith("bars: OperationalError")
    assert len(_rows(ExecutionIntentRecord, t_ok["tenant_id"])) == 1 and _rows(ExecutionIntentRecord, t_bad["tenant_id"]) == []


def test_each_template_runs_in_its_own_savepoint(monkeypatch):
    """runner.py run_due: a template that fails with a SQL error leaves its problem, and the templates after it run."""
    _patch_market(monkeypatch)
    t_bad, bad_rule = _setup("tplbad")
    t_ok, ok_rule = _setup("tplok")
    seen = []
    real = A.areas_for

    async def zones(session, key, tenant_id, at):
        if tenant_id == t_bad["tenant_id"]:
            seen.append(session.in_nested_transaction())
            await session.execute(_bad_sql())
        return await real(session, key, tenant_id, at)
    monkeypatch.setattr(A, "areas_for", zones)
    report = _runner()
    assert seen == [True] and any("template" in p and "OperationalError" in p for p in report.problems)
    assert "OperationalError" in (_template(bad_rule).last_problem or "")
    assert len(_rows(ExecutionIntentRecord, t_ok["tenant_id"])) == 1 and _rows(ExecutionIntentRecord, t_bad["tenant_id"]) == []


def test_a_cold_trend_read_that_fails_leaves_the_session_usable(monkeypatch):
    """trend.py allow_cold: the lake read on the caller's session runs in a savepoint."""
    from sqlalchemy import text
    trend.clear_cache()
    seen = []

    async def broken(session, key, today):
        seen.append(session.in_nested_transaction())
        await session.execute(_bad_sql())
    monkeypatch.setattr(trend, "lake_daily", broken)

    async def go():
        async with _session_factory() as session:
            out = await trend.daily_context(session, "NSE:X1SAVE", date(2026, 9, 23), allow_cold=True)
            ok = (await session.execute(text("SELECT 1"))).scalar()
            await session.commit()
            return out, ok
    out, ok = _run(go())
    assert seen == [True] and out["ok"] is False and "OperationalError" in out["reason"] and ok == 1
    trend.clear_cache()
