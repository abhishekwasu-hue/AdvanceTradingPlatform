"""M76 MP-12 (owner order M76 D-2; amendment: facilities, not enforcement): a trigger plan's dry run.

Proofs: nothing is written or sent (no order, trade, resting order, proposal, notification or plan change; the order
paths and the live engine's timing are never reached; a saved plan's row is untouched); no look-ahead (a bar still
forming, or one that closed before the session's arming, never counts; every method reading is made as of a closed
candle's end); the SAME evaluator as the live engine (the candle-close replay meets the plan on the candle the live
`trigger_plans.run` fires on, at the same trigger price; method kinds read through `plan_conditions.read`); N is his own
and bounded; the ticket's own read-only checks say what each order would send; another organisation's plan is not found.
"""
import asyncio
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import func, select

from app.charts import price_action_layer as pal
from app.db.models import AiActionRecord, NotificationRecord, OrderRecord, PaperRestingOrderRecord, TradeRecord, TriggerPlanRecord
from app.execution import plan_conditions as PC
from app.execution import plan_dry_run as pdr
from app.execution import trigger_plans
from app.market_lake import own_bars
from app.trading import trigger_rules
from app.trading.trigger_rules import ABOVE, BELOW, CLOSE, TOUCH
from tests.test_auth_api import _session_factory, client
from tests.test_fno1_chain_ticket import _contract, _expiry, _org_tenant, _price
from tests.test_m75_w8_trigger_plans import SPOT, _body, _create, _plan
from tests.test_z5_p1d_risk_by_default import _flag

IST = trigger_rules.IST


def _run(coro):
    return asyncio.run(coro)


def _open(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST).astimezone(UTC)


def _minutes(d: date, closes: list[float]) -> pd.DataFrame:
    """One session's one-minute bars from its open, each bar's low / high = its close +- 1."""
    rows = []
    for i, c in enumerate(closes):
        ts = _open(d) + timedelta(minutes=i)
        rows.append({"timestamp": pd.Timestamp(ts), "bar_end": pd.Timestamp(ts + timedelta(minutes=1)), "open": c, "high": c + 1.0,
                     "low": c - 1.0, "close": c, "volume": 1.0})
    return pd.DataFrame(rows)


def _bars(frame: pd.DataFrame) -> list[pdr.Bar]:
    return pdr.bars_of(frame)


def _lake(monkeypatch, frame: pd.DataFrame, seen: list | None = None):
    async def load(session, key, tf, start, end, private=None):
        if seen is not None:
            seen.append((tf, start, end))
        return frame, "lake"

    async def scope(session, user, key):
        return None
    monkeypatch.setattr(pal, "load_frame", load)
    monkeypatch.setattr(own_bars, "scope_for", scope)


@pytest.fixture
def setup(monkeypatch):
    t = _org_tenant("mp12")
    _flag(trigger_plans.FLAG, t["tenant_id"])
    expiry = _expiry()
    strike = 30000.0
    symbol = _contract(expiry, strike)
    _price(monkeypatch, 100.0)
    return t, expiry, strike, symbol


# --- pure: no look-ahead, his window, his sessions --------------------------------------------------------------------------

def test_touch_reads_only_closed_bars_after_arming_inside_his_window():
    d = date(2026, 3, 2)
    frame = _minutes(d, [24600.0] * 10 + [24705.0] + [24600.0] * 5)
    bars = _bars(frame)
    c = trigger_plans.Condition(level=24700.0, side=ABOVE, mode=TOUCH)
    armed = pdr.session_start(d)
    hit_end = _open(d) + timedelta(minutes=11)
    bar, _ = pdr.replay_touch(c, bars, armed, hit_end)
    assert bar is not None and bar.timestamp == _open(d) + timedelta(minutes=10)
    # the touching bar is still forming one second before it closes: never read
    assert pdr.replay_touch(c, bars, armed, hit_end - timedelta(seconds=1))[0] is None
    # armed after that bar closed: it never counts
    assert pdr.replay_touch(c, bars, hit_end, _open(d) + timedelta(hours=1))[0] is None
    # his own window (IST): the touch at 09:25 is outside 09:30-15:00
    win = trigger_plans.Condition(level=24700.0, side=ABOVE, mode=TOUCH, window_start="09:30", window_end="15:00")
    assert pdr.replay_touch(win, bars, armed, _open(d) + timedelta(hours=1))[0] is None
    below = trigger_plans.Condition(level=24599.5, side=BELOW, mode=TOUCH)
    assert pdr.replay_touch(below, bars, armed, hit_end)[0] is not None


def test_close_replay_is_the_shared_rule_and_never_a_forming_candle():
    d = date(2026, 3, 2)
    closes = [24600.0] * 20 + [24720.0] * 3 + [24600.0] * 7 + [24710.0] * 15     # a 15-minute candle closes above only at 10:15
    bars = _bars(_minutes(d, closes))
    c = trigger_plans.Condition(level=24700.0, side=ABOVE, mode=CLOSE, timeframe_minutes=15)
    armed = pdr.session_start(d)
    end = _open(d) + timedelta(minutes=45)
    candle, read = _run(pdr.replay_close(c, bars, armed, end, *SPOT))
    shared = trigger_rules.close_trigger(c.level, c.side, bars, 15, end, armed_at=armed)
    assert candle == shared and candle is not None and candle.end == _open(d) + timedelta(minutes=45) and read == 3
    # one minute earlier the 10:00-10:15 candle is still forming: nothing
    assert _run(pdr.replay_close(c, bars, armed, end - timedelta(minutes=1), *SPOT))[0] is None


def test_his_sessions_come_from_the_stored_bars():
    days = [date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 5)]                   # 4 March has no bars (a holiday)
    starts = [_open(d) + timedelta(minutes=i) for d in days for i in range(3)]
    assert pdr.last_sessions(starts, 2) == days[1:]
    assert pdr.last_sessions(starts, 10) == days


# --- the same evaluator as the live engine ----------------------------------------------------------------------------------

def test_method_replay_reads_through_the_live_reading_as_of_each_closed_candle(monkeypatch):
    d = date(2026, 3, 2)
    frame = _minutes(d, [24600.0] * 120)
    frame15 = frame.iloc[::15].reset_index(drop=True)
    frame15["bar_end"] = frame15["timestamp"] + pd.Timedelta(minutes=15)
    _lake(monkeypatch, frame15)
    now = _open(d) + timedelta(minutes=95)                                          # the 10:45-11:00 candle is forming
    hit_at = _open(d) + timedelta(minutes=60)
    calls = []

    async def read(session, user, symbol, exchange, c, armed_at, at):
        calls.append((armed_at, at))
        assert at <= now                                                            # never a moment after "now"
        assert at in set(frame15["bar_end"].map(lambda x: x.to_pydatetime()))      # a pass right after a candle closed
        if at >= hit_at:
            return PC.Reading(PC.SOURCE_ATP, hit=PC.Hit(PC.SOURCE_ATP, hit_at, 24610.0, "Met by ATP's chart readers: test."),
                              last_close=24610.0)
        return PC.Reading(PC.SOURCE_ATP, last_close=24600.0)
    monkeypatch.setattr(PC, "read", read)
    c = trigger_plans.Condition(kind=PC.STRUCTURE, timeframe_minutes=15, event="BOS", direction="up")
    out = _run(pdr.replay(None, object(), *SPOT, c, 3, now))
    assert [r["state"] for r in out["sessions"]] == [pdr.TRIGGERED]
    row = out["sessions"][0]
    assert row["at"] == hit_at.isoformat() and row["price"] == 24610.0 and row["source"] == PC.SOURCE_ATP
    assert row["armed_at"] == pdr.session_start(d).isoformat() and all(a == pdr.session_start(d) for a, _ in calls)
    assert [t for _, t in calls] == [_open(d) + timedelta(minutes=15 * k) for k in range(1, 5)]   # stops at the fire: once


def test_a_longer_method_replay_is_refused_before_it_starts(monkeypatch):
    d = date(2026, 3, 2)
    frame = _minutes(d, [24600.0] * (pdr.MAX_READS + 5))
    _lake(monkeypatch, frame)

    async def never(*a, **k):
        raise AssertionError("read after the bound")
    monkeypatch.setattr(PC, "read", never)
    c = trigger_plans.Condition(kind=PC.STRUCTURE, timeframe_minutes=1, event="BOS", direction="up")
    with pytest.raises(pdr.DryRunError, match="at most"):
        _run(pdr.replay(None, object(), *SPOT, c, 1, _open(d) + timedelta(days=1)))


def test_candle_close_parity_with_the_live_engine(setup, monkeypatch):
    """The live `trigger_plans.run`, passed after each 15-minute close, fires on the same candle at the same price."""
    t, expiry, strike, symbol = setup
    d = (datetime.now(IST) + timedelta(days=1)).date()                              # after the plan is armed (now)
    closes = [24600.0 + 3 * i for i in range(60)]                                     # crosses 24700 near minute 34
    frame = _minutes(d, closes)
    plan = _create(t, _body(expiry, strike, condition={"level": 24700.0, "side": ABOVE, "mode": "CLOSE", "timeframe_minutes": 15}))
    end = _open(d) + timedelta(minutes=60)
    _lake(monkeypatch, frame)

    async def dry():
        async with _session_factory() as session:
            from app.db.models import User
            user = await session.get(User, t["user_id"])
            c = trigger_plans.condition_of(await session.get(TriggerPlanRecord, plan["id"]))
            return await pdr.dry_run(session, user, underlying="NIFTY", symbol=SPOT[0], exchange=SPOT[1], condition=c, orders=[],
                                     choice="AUTO", sessions=1, now=end)
    out = _run(dry())
    row = out["sessions"][0]
    assert row["state"] == pdr.TRIGGERED and out["placed"] == 0
    bars = _bars(frame)

    async def candles(symbol_, exchange):
        return bars
    fired_at = None
    for k in range(1, 5):
        now = _open(d) + timedelta(minutes=15 * k, seconds=1)
        async def go(now=now):
            async with _session_factory() as session:
                return await trigger_plans.run(session, t["tenant_id"], _prices({symbol: 100.0}), candles=candles, now=now)
        if _run(go())["triggered"]:
            fired_at = _open(d) + timedelta(minutes=15 * k)
            break
    live = _plan(plan["id"])
    assert fired_at is not None and live.status == "DONE"
    assert row["at"] == fired_at.isoformat() and row["price"] == pytest.approx(live.trigger_price)


def _prices(table):
    async def lookup(symbol, exchange="NFO"):
        return float(table[symbol])
    return lookup


# --- the route: nothing written or sent; his own; bounded ---------------------------------------------------------------------

async def _counts(user_id: int, tenant_id: int) -> dict:
    async with _session_factory() as session:
        out = {}
        for name, model, col in (("orders", OrderRecord, OrderRecord.tenant_id), ("trades", TradeRecord, TradeRecord.tenant_id),
                                 ("resting", PaperRestingOrderRecord, PaperRestingOrderRecord.tenant_id),
                                 ("proposals", AiActionRecord, AiActionRecord.tenant_id),
                                 ("notifications", NotificationRecord, NotificationRecord.tenant_id),
                                 ("plans", TriggerPlanRecord, TriggerPlanRecord.tenant_id)):
            out[name] = await session.scalar(select(func.count()).select_from(model).where(col == tenant_id))
        return out


def _forbid_sending(monkeypatch):
    from app.execution import broker_gtt, resting_book
    from app.notifications import service
    from app.paper import fno_ticket

    def boom(name):
        async def f(*a, **k):
            raise AssertionError(f"a dry run reached {name}")
        return f
    monkeypatch.setattr(fno_ticket, "place", boom("the ticket's place"))
    monkeypatch.setattr(trigger_plans, "fire", boom("the plan's fire"))
    monkeypatch.setattr(trigger_plans, "run", boom("the plan engine's pass"))
    monkeypatch.setattr(resting_book, "arm_exit", boom("the resting book"))
    monkeypatch.setattr(service, "notify", boom("a notification"))
    monkeypatch.setattr(broker_gtt, "handle", boom("the broker GTT"))
    monkeypatch.setattr(broker_gtt, "on_plan_change", boom("the broker GTT"))
    monkeypatch.setattr(PC, "seen", boom("the live engine's timing"))


def _dry(t, body, status=200):
    """The route, with a guard that records every flush carrying a change (an insert, update or delete) meanwhile."""
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    changes: list = []

    def record(session, _ctx, _instances):
        changes.extend(type(o).__name__ for o in (*session.new, *session.dirty, *session.deleted))
    event.listen(Session, "before_flush", record)
    try:
        out = client.post("/api/trigger-plans/dry-run", headers=t["headers"], json=body)
    finally:
        event.remove(Session, "before_flush", record)
    assert out.status_code == status, out.text
    assert changes == [], changes                                                  # nothing flushed: nothing written
    return out.json()


def test_a_draft_dry_run_replays_and_shows_what_would_be_sent_and_writes_nothing(setup, monkeypatch):
    t, expiry, strike, symbol = setup
    today = datetime.now(IST).date()
    days = [today - timedelta(days=3), today - timedelta(days=2)]
    frame = pd.concat([_minutes(days[0], [24600.0] * 30), _minutes(days[1], [24600.0] * 10 + [24720.0] * 5)], ignore_index=True)
    _lake(monkeypatch, frame)
    _forbid_sending(monkeypatch)
    before = _run(_counts(t["user_id"], t["tenant_id"]))
    body = _body(expiry, strike)
    draft = {"sessions": 5, "underlying": "NIFTY", "condition": body["condition"], "orders": body["orders"], "approval": "AUTO"}
    out = _dry(t, draft)
    assert out["dry_run"] is True and out["mode"] == "PAPER" and out["placed"] == 0
    assert [r["session"] for r in out["sessions"]] == [d.isoformat() for d in days]          # the stored sessions only
    assert [r["state"] for r in out["sessions"]] == [pdr.NOT_MET, pdr.TRIGGERED]
    hit = out["sessions"][1]
    assert hit["at"] == (_open(days[1]) + timedelta(minutes=11)).isoformat() and hit["state_words"] == "Would have triggered"
    sent = out["on_trigger"]
    assert sent["choice"] == "AUTO" and sent["ask_first"] is False and len(sent["orders"]) == 1
    assert sent["orders"][0]["can_place"] is True and sent["orders"][0]["fill_price"] == 100.0
    assert _run(_counts(t["user_id"], t["tenant_id"])) == before                              # nothing written


def test_a_saved_plan_dry_run_leaves_the_plan_and_the_live_engine_untouched(setup, monkeypatch):
    t, expiry, strike, symbol = setup
    today = datetime.now(IST).date()
    _lake(monkeypatch, _minutes(today - timedelta(days=1), [24600.0] * 5 + [24750.0] * 5))
    plan = _create(t, _body(expiry, strike))
    row_before = _plan(plan["id"])
    PC.clear()
    timing = dict(PC._NEXT)  # noqa: SLF001
    _forbid_sending(monkeypatch)
    before = _run(_counts(t["user_id"], t["tenant_id"]))
    out = _dry(t, {"sessions": 2, "plan_id": plan["id"]})
    assert out["plan_id"] == plan["id"] and out["triggered"] == 1 and out["on_trigger"]["orders"][0]["label"]
    row = _plan(plan["id"])
    for col in ("status", "last_price", "last_checked_at", "trigger_price", "triggered_at", "note", "results_json", "updated_at", "armed_at"):
        assert getattr(row, col) == getattr(row_before, col), col
    assert dict(PC._NEXT) == timing  # noqa: SLF001
    assert _run(_counts(t["user_id"], t["tenant_id"])) == before


def test_his_own_plan_only_his_own_n_and_the_flag(setup, monkeypatch):
    t, expiry, strike, _ = setup
    _lake(monkeypatch, _minutes(date(2026, 3, 2), [24600.0] * 5))
    plan = _create(t, _body(expiry, strike))
    other = _org_tenant("mp12-other")
    _flag(trigger_plans.FLAG, other["tenant_id"])
    assert client.post("/api/trigger-plans/dry-run", headers=other["headers"], json={"sessions": 1, "plan_id": plan["id"]}).status_code == 404
    _dry(t, {"plan_id": plan["id"]}, status=422)                                               # N is his own: no default
    _dry(t, {"sessions": pdr.MAX_SESSIONS + 1, "plan_id": plan["id"]}, status=422)
    _dry(t, {"sessions": 1, "plan_id": plan["id"], "underlying": "NIFTY"}, status=422)         # one or the other
    _dry(t, {"sessions": 1, "underlying": "NIFTY"}, status=422)
    bad = _dry(t, {"sessions": 1, "underlying": "NIFTY", "condition": {"level": 24700.0, "side": ABOVE, "mode": "CLOSE"}}, status=422)
    assert "timeframe" in bad["detail"]
    _flag(trigger_plans.FLAG, t["tenant_id"], on=False)
    assert client.post("/api/trigger-plans/dry-run", headers=t["headers"], json={"sessions": 1, "plan_id": plan["id"]}).status_code == 404


def test_no_stored_bars_says_so(setup, monkeypatch):
    t, *_ = setup
    _lake(monkeypatch, pd.DataFrame(columns=["timestamp", "bar_end", "open", "high", "low", "close", "volume"]))
    out = _dry(t, {"sessions": 3, "underlying": "NIFTY", "condition": {"level": 24700.0, "side": ABOVE, "mode": "TOUCH"}})
    assert out["sessions"] == [] and out["note"] == pdr.NO_BARS and out["on_trigger"] is None
