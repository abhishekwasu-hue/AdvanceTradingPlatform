"""M94 PR-7 (owner order M94, spec docs/design/M94_INTRADAY_PACK.md R1-R3): the three optional risk tools.

R1 the position-size calculator sizes with the engine's own risk-based sizing (rounded down to the lot or to whole
shares; zero / negative refused; places and saves nothing). R2 the daily loss limit refuses a new entry with its reason
and never an exit, a close or a square-off. R3 the profit give-back stop tracks today's peak, refuses new entries once
the trader's own % is given back (for the rest of that day), never an exit, starts afresh the next trading day, and with
the check off (the default) changes nothing.
"""
import asyncio
import importlib.util
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from sqlalchemy import select

from app.core.enums import AssetClass, SignalDirection, SignalGrade
from app.core.models import RiskConfig, Signal
from app.db.models import RiskEventRecord, RiskSettingsRecord, TradeRecord, User
from app.execution.signal_execution import execute_signal_for_user
from app.instruments.models import ContractSpec
from app.risk_engine import giveback, risk_limits, size_calculator
from app.risk_engine.risk_manager import RiskManager, TradingDayState
from app.trading.persistence import build_trading_day_state
from tests.test_auth_api import _session_factory, client

BACKEND = Path(__file__).resolve().parents[1]
IST = ZoneInfo("Asia/Kolkata")


def _run(coro):
    return asyncio.run(coro)


def _signal(entry=100.0, stop=95.0) -> Signal:
    return Signal(symbol="M94", strategy_id="m94", strategy_name="m94", direction=SignalDirection.LONG, timestamp=datetime.now(UTC),
                  entry=entry, stop_loss=stop, grade=SignalGrade.VALID)


# --- R1: the position-size calculator -------------------------------------------------------------------------------------

def _calc(**body):
    return size_calculator.calculate(body)


def test_r1_shares_round_down_to_whole_shares_and_equal_the_engine():
    out = _calc(account=500_000, risk_pct=1, entry=101.0, stop=94.0, unit="SHARES")    # 5,000 / 7 = 714.28 -> 714
    assert out["ok"] and out["quantity"] == 714 and out["lots"] == 714 and out["lot_size"] == 1
    assert out["risk_amount"] == 5000.0 and out["at_risk"] == pytest.approx(714 * 7.0) and out["at_risk"] <= out["risk_amount"]
    engine = RiskManager(RiskConfig(capital=500_000, risk_per_trade_pct=1)).validate_and_size(_signal(101.0, 94.0), TradingDayState())
    assert engine.approved and engine.quantity == out["quantity"]                 # the same sizing as an entry


def test_r1_lots_round_down_to_whole_lots_and_less_than_one_lot_is_never_rounded_up():
    out = _calc(account=1_000_000, risk_pct=1, entry=24_000.0, stop=23_960.0, unit="LOTS", lot_size=75)   # 10,000 / 3,000 a lot = 3.33
    assert out["ok"] and out["lots"] == 3 and out["quantity"] == 225 and out["per_lot"] == 3000.0
    spec = ContractSpec(symbol="NIFTY OPT", exchange="NFO", asset_class=AssetClass.INDEX_OPTION, description="t", lot_size=75.0, tick_size=0.05)
    engine = RiskManager(RiskConfig(capital=1_000_000, risk_per_trade_pct=1)).validate_and_size(
        _signal(24_000.0, 23_960.0), TradingDayState(), contract_spec=spec)
    assert engine.quantity == out["quantity"]
    small = _calc(risk_rupees=2000, entry=24_000.0, stop=23_960.0, unit="LOTS", lot_size=75)
    assert small["ok"] and small["quantity"] == 0 and small["lots"] == 0 and "never rounded up" in small["note"]
    # a short (stop above the entry) sizes the same way; the smaller of % and rupees applies, as in the engine
    short = _calc(account=1_000_000, risk_pct=1, risk_rupees=6000, entry=100.0, stop=104.0, unit="SHARES")
    assert short["quantity"] == 1500 and short["risk_amount"] == 6000.0


@pytest.mark.parametrize("body, problem", [
    ({"account": 0, "risk_pct": 1, "entry": 100, "stop": 95, "unit": "SHARES"}, "Account: enter an amount above 0 to use a % risk."),
    ({"account": -5, "risk_pct": 1, "entry": 100, "stop": 95, "unit": "SHARES"}, "Account: enter an amount above 0 to use a % risk."),
    ({"account": 1000, "risk_pct": 0, "entry": 100, "stop": 95, "unit": "SHARES"}, "Risk per trade: the % must be more than 0 and at most 100."),
    ({"account": 1000, "risk_pct": -1, "entry": 100, "stop": 95, "unit": "SHARES"}, "Risk per trade: the % must be more than 0 and at most 100."),
    ({"risk_rupees": 0, "entry": 100, "stop": 95, "unit": "SHARES"}, "Risk per trade: the rupee amount must be above 0."),
    ({"risk_rupees": 500, "entry": 0, "stop": 95, "unit": "SHARES"}, "Entry price: enter a price above 0."),
    ({"risk_rupees": 500, "entry": 100, "stop": -1, "unit": "SHARES"}, "Stop price: enter a price above 0."),
    ({"risk_rupees": 500, "entry": 100, "stop": 100, "unit": "SHARES"}, "The stop price is the same as the entry price"),
    ({"risk_rupees": 500, "entry": 100, "stop": 95, "unit": "LOTS", "lot_size": 0}, "Lot size: enter the contract's lot size"),
    ({"risk_rupees": 500, "entry": 100, "stop": 95, "unit": "LOTS", "lot_size": 2.5}, "Lot size: enter the contract's lot size"),
    ({"risk_rupees": 500, "entry": 100, "stop": 95}, "Size in: pick shares or F&O lots."),
    ({"entry": 100, "stop": 95, "unit": "SHARES"}, "Risk per trade: enter a % of the account or a rupee amount."),
])
def test_r1_zero_negative_and_missing_values_are_refused(body, problem):
    out = _calc(**body)
    assert not out["ok"] and any(p.startswith(problem) for p in out["problems"]), out


def test_r1_the_endpoint_sizes_and_stores_nothing():
    from tests.test_auth_api import _register
    token = _register(f"m94-calc-{uuid.uuid4().hex[:6]}@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    before = client.get("/api/risk-settings/limits", headers=headers).json()
    ok = client.post("/api/risk-settings/size-calculator", headers=headers,
                     json={"account": 200_000, "risk_pct": 1, "entry": 250.0, "stop": 246.0, "unit": "SHARES"})
    assert ok.status_code == 200 and ok.json()["quantity"] == 500, ok.text
    bad = client.post("/api/risk-settings/size-calculator", headers=headers, json={"risk_rupees": -1, "entry": 1, "stop": 2, "unit": "SHARES"})
    assert bad.status_code == 422 and bad.json()["detail"] == "Risk per trade: the rupee amount must be above 0."
    assert client.post("/api/risk-settings/size-calculator", json={}).status_code == 401
    assert client.get("/api/risk-settings/limits", headers=headers).json() == before      # nothing saved


# --- the shared entry gate, its exits, and a tenant with today's trades --------------------------------------------------

def _tenant(tag: str) -> dict:
    from tests.test_trading_worker import _tenant as worker_tenant
    return worker_tenant(f"m94-{tag}-{uuid.uuid4().hex[:6]}@example.com")


def _set_checks(t: dict, checks: dict) -> None:
    out = client.put("/api/risk-settings/limits", headers=t["headers"], json={"checks": checks})
    assert out.status_code == 200, out.text


def _today_times(minutes_ago: float) -> datetime:
    """A time inside today's Indian trading day (just after IST midnight "now - x" would be yesterday)."""
    now = datetime.now(UTC)
    day_start = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    return max(now - timedelta(minutes=minutes_ago), day_start + timedelta(seconds=1))


def _closed(t: dict, pnl: float, symbol: str) -> None:
    async def go():
        async with _session_factory() as session:
            session.add(TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="PAPER", symbol=symbol, strategy_id="s",
                                    direction="LONG", entry_time=_today_times(30), entry_price=100.0, quantity=10, stop_loss=98.0,
                                    target1=110.0, exit_time=_today_times(1), exit_price=100.0 + pnl / 10, exit_reason="Target", pnl=pnl))
            await session.commit()
    _run(go())


def _open(t: dict) -> int:
    """An open PAPER position carried from an earlier day (its mark is not today's P&L)."""
    async def go():
        async with _session_factory() as session:
            trade = TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="PAPER", symbol=f"OPEN{uuid.uuid4().hex[:4]}",
                                strategy_id="s", direction="LONG", entry_time=datetime.now(UTC) - timedelta(days=2), entry_price=100.0,
                                quantity=1, stop_loss=98.0, target1=110.0)
            session.add(trade)
            await session.commit()
            return trade.id
    return _run(go())


def _entry(t: dict, key: str):
    from tests.test_live_execution import _signal as live_signal

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            result, order = await execute_signal_for_user(session, user, mode="PAPER", strategy_id="ema_rsi_scalper_1m", signal=live_signal(),
                                                          idempotency_key=key, max_quantity=1, holding="INTRADAY", order_style="MARKET")
            return result, order
    return _run(go())


def _state(t: dict, now: datetime | None = None) -> TradingDayState:
    async def go():
        async with _session_factory() as session:
            return await build_trading_day_state(session, await session.get(User, t["user_id"]), now=now)
    return _run(go())


def _sweep(now: datetime | None = None) -> int:
    async def go():
        async with _session_factory() as session:
            return await giveback.sweep(session, now)
    return _run(go())


def _row(t: dict) -> RiskSettingsRecord:
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(RiskSettingsRecord).where(RiskSettingsRecord.tenant_id == t["tenant_id"]))
    return _run(go())


def _events(t: dict, check: str) -> list:
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(RiskEventRecord).where(RiskEventRecord.tenant_id == t["tenant_id"],
                                                                            RiskEventRecord.rule_type == f"RISK_LIMIT:{check}")))
    return _run(go())


def _exits_run_without_the_gate(t: dict, open_ids: list[int], monkeypatch) -> list[TradeRecord]:
    """A stop-out by the position monitor and a square-off close both go through while new entries are refused - and
    neither asks the entry gate (it would raise here)."""
    from app.trading.position_monitor import close_position, monitor_open_positions

    def no_gate(*args, **kwargs):
        raise AssertionError("an exit must never pass the entry risk check")
    monkeypatch.setattr(RiskManager, "validate_and_size", no_gate)

    async def go():
        async with _session_factory() as session:
            first = await session.get(TradeRecord, open_ids[0])
            first_symbol = first.symbol

            async def only_first(symbol, exchange):
                return 97.0 if symbol == first_symbol else 100.5   # through the first position's stop; the rest hold
            await monitor_open_positions(session, t["tenant_id"], only_first)
            second = await session.get(TradeRecord, open_ids[1])
            outcome = await close_position(session, second, 101.0, "Square-off")
            assert outcome.closed, outcome.warnings
            out = []
            for i in open_ids:
                trade = await session.get(TradeRecord, i)
                await session.refresh(trade)
                out.append(trade)
            return out
    closed = _run(go())
    monkeypatch.undo()
    return closed


# --- R2: the daily loss limit ---------------------------------------------------------------------------------------------

def test_r2_after_his_own_daily_loss_limit_a_new_entry_is_refused_with_the_reason_and_exits_still_run(monkeypatch):
    t = _tenant("r2")
    open_ids = [_open(t), _open(t)]
    _set_checks(t, {"daily_loss": {"enabled": True, "values": {"daily_loss_rupees": 1000}}})
    _closed(t, -1500.0, "LOSS1")
    result, order = _entry(t, f"m94-r2-{uuid.uuid4().hex[:6]}")
    assert not result.executed and order.status == "REJECTED"
    assert any("Today's loss ₹1,500 has reached your daily loss limit of ₹1,000. No new entries today; exits still work." in r
               for r in result.reasons), result.reasons
    assert len(_events(t, "daily_loss")) == 1
    closed = _exits_run_without_the_gate(t, open_ids, monkeypatch)
    assert all(c.exit_time is not None and c.pnl is not None for c in closed)          # ADR-0004
    assert {c.exit_reason for c in closed} >= {"Square-off"}


def test_r2_the_wording_says_new_entries_only():
    check = risk_limits.BY_ID["daily_loss"]
    assert check.help == "Once today's loss reaches it, no new entry today. Exits always work."
    assert "no new entries today; exits still work" in risk_limits.daily_loss_block(1500, 1000)["text"].lower()


# --- R3: the profit give-back stop: pure ----------------------------------------------------------------------------------

def test_r3_peak_only_moves_up_and_a_peak_of_another_day_does_not_count():
    today = datetime(2026, 11, 10).date()
    assert giveback.peak_today(5000.0, today, today, 3000.0) == 5000.0
    assert giveback.peak_today(5000.0, today, today, 7000.0) == 7000.0
    assert giveback.peak_today(5000.0, today - timedelta(days=1), today, 3000.0) == 3000.0      # next trading day: afresh
    assert giveback.peak_today(None, None, today, -200.0) == -200.0


def test_r3_threshold_crossing():
    assert not giveback.reached(10_000.0, 7_001.0, 30.0)
    assert giveback.reached(10_000.0, 7_000.0, 30.0)           # 30 % of the peak given back
    assert giveback.reached(10_000.0, -500.0, 30.0)
    assert not giveback.reached(0.0, -5_000.0, 30.0)           # no profit, nothing to give back (the daily loss limit's job)
    assert not giveback.reached(-100.0, -5_000.0, 30.0)


def test_r3_the_engine_refuses_only_when_on_and_reached():
    cfg = RiskConfig(giveback_pct=30.0)
    rm = RiskManager(cfg)
    hit = rm.validate_and_size(_signal(), TradingDayState(daily_pnl=6_000.0, day_peak_pnl=10_000.0), requested_quantity=1)
    assert not hit.approved and hit.blocks[0]["check"] == "profit_giveback"
    assert hit.reasons[0] == ("Today's profit peaked at ₹10,000 and is ₹6,000 now: your profit give-back stop of 30% was reached today. "
                              "No new entries for the rest of today; exits still work.")
    assert rm.validate_and_size(_signal(), TradingDayState(daily_pnl=8_000.0, day_peak_pnl=10_000.0), requested_quantity=1).approved
    sticky = rm.validate_and_size(_signal(), TradingDayState(daily_pnl=9_500.0, day_peak_pnl=10_000.0, giveback_reached=True), requested_quantity=1)
    assert not sticky.approved                                  # reached earlier today: the rest of the day
    # OFF (not set) changes nothing, whatever the day did
    off = RiskManager(RiskConfig()).validate_and_size(_signal(), TradingDayState(daily_pnl=-1.0, day_peak_pnl=10_000.0, giveback_reached=True),
                                                      requested_quantity=1)
    assert off.approved and off.quantity == 1


def test_r3_the_check_is_off_by_default_and_needs_a_value():
    check = risk_limits.BY_ID["profit_giveback"]
    assert check.group == "main" and [f.key for f in check.fields] == ["giveback_pct"] and check.help.endswith("Exits always work.")
    assert risk_limits.apply_switches(RiskConfig(giveback_pct=30.0), []).giveback_pct is None
    _, _, problems = risk_limits.plan_save(None, {"checks": {"profit_giveback": {"enabled": True}}}, {})
    assert problems == ["Profit give-back stop: enter a value to switch it on."]
    _, _, problems = risk_limits.plan_save(None, {"checks": {"profit_giveback": {"enabled": True, "values": {"giveback_pct": 0}}}}, {})
    assert problems and "more than 0" in problems[0]
    _, _, problems = risk_limits.plan_save(None, {"checks": {"profit_giveback": {"enabled": True, "values": {"giveback_pct": 101}}}}, {})
    assert problems and "at most 100" in problems[0]


# --- R3: stored peak, the shared gate, exits, next day, off ---------------------------------------------------------------

def test_r3_off_by_default_changes_nothing():
    t = _tenant("r3-off")
    view = {c["id"]: c for c in client.get("/api/risk-settings/limits", headers=t["headers"]).json()["checks"]}
    assert view["profit_giveback"]["enabled"] is False and view["profit_giveback"]["fields"][0]["value"] is None
    _closed(t, 10_000.0, "WIN1")
    _sweep()
    _closed(t, -9_000.0, "LOSS1")
    state = _state(t)
    assert state.day_peak_pnl is None and state.giveback_reached is False
    row = _row(t)
    assert row is None or (row.day_peak_pnl is None and row.day_peak_on is None and row.giveback_hit_at is None)   # nothing written
    result, _ = _entry(t, f"m94-r3-off-{uuid.uuid4().hex[:6]}")
    assert result.executed, result.reasons


def test_r3_peak_tracking_refuses_new_entries_for_the_rest_of_the_day_and_never_an_exit(monkeypatch):
    t = _tenant("r3")
    open_ids = [_open(t), _open(t)]
    _set_checks(t, {"profit_giveback": {"enabled": True, "values": {"giveback_pct": 30}}})
    _closed(t, 10_000.0, "WIN1")
    assert _sweep() >= 1                                        # the worker cycle stores today's peak
    row = _row(t)
    assert row.day_peak_pnl == 10_000.0 and row.day_peak_on == datetime.now(IST).date() and row.giveback_hit_at is None
    _closed(t, -2_000.0, "LOSS1")                               # 20 % given back: below his 30 %
    assert _state(t).day_peak_pnl == 10_000.0
    ok, _ = _entry(t, f"m94-r3-a-{uuid.uuid4().hex[:6]}")
    assert ok.executed, ok.reasons
    _closed(t, -1_500.0, "LOSS2")                               # 35 % given back
    refused, order = _entry(t, f"m94-r3-b-{uuid.uuid4().hex[:6]}")
    assert not refused.executed and order.status == "REJECTED"
    assert any("your profit give-back stop of 30% was reached today" in r and "Change limit: Settings > Risk limits" in r
               for r in refused.reasons), refused.reasons
    events = _events(t, "profit_giveback")
    assert len(events) == 1 and events[0].limit_value == 30 and events[0].current_value == pytest.approx(35.0)
    _sweep()                                                    # the worker marks the day
    assert _row(t).giveback_hit_at is not None and _row(t).day_peak_pnl == 10_000.0     # the peak never moves down
    _closed(t, 3_000.0, "WIN2")                                 # back to 9,500: still the rest of the day
    state = _state(t)
    assert state.daily_pnl == pytest.approx(9_500.0) and state.giveback_reached
    again, _ = _entry(t, f"m94-r3-c-{uuid.uuid4().hex[:6]}")
    assert not again.executed
    closed = _exits_run_without_the_gate(t, open_ids, monkeypatch)
    assert all(c.exit_time is not None and c.pnl is not None for c in closed)          # ADR-0004
    # switching the check off lifts it (the engine reads it as not set)
    _set_checks(t, {"profit_giveback": {"enabled": False}})
    lifted, _ = _entry(t, f"m94-r3-d-{uuid.uuid4().hex[:6]}")
    assert lifted.executed, lifted.reasons


def test_r3_an_entry_check_counts_the_value_now_and_writes_nothing():
    t = _tenant("r3-read")
    _set_checks(t, {"profit_giveback": {"enabled": True, "values": {"giveback_pct": 50}}})
    _closed(t, 4_000.0, "WIN1")
    state = _state(t)
    assert state.day_peak_pnl == 4_000.0 and not state.giveback_reached
    assert _row(t).day_peak_pnl is None                          # only the worker cycle writes (no lock on an order path)


def test_r3_resets_the_next_trading_day():
    t = _tenant("r3-next")
    _set_checks(t, {"profit_giveback": {"enabled": True, "values": {"giveback_pct": 30}}})
    _closed(t, 10_000.0, "WIN1")
    _sweep()
    _closed(t, -5_000.0, "LOSS1")
    _sweep()
    assert _state(t).giveback_reached and _row(t).giveback_hit_at is not None
    tomorrow = datetime.now(IST).replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=1)
    state = _state(t, now=tomorrow)                             # yesterday's trades are not today's P&L; the stored peak is yesterday's
    assert state.daily_pnl == 0.0 and state.day_peak_pnl == 0.0 and not state.giveback_reached
    assert RiskManager(RiskConfig(giveback_pct=30.0)).validate_and_size(_signal(), state, requested_quantity=1).approved
    _sweep(tomorrow)
    row = _row(t)
    assert row.day_peak_on == tomorrow.date() and row.day_peak_pnl == 0.0 and row.giveback_hit_at is None   # the old day's mark is gone
    assert not _state(t, now=tomorrow).giveback_reached


def test_r3_the_peak_write_keeps_the_traders_last_save_time():
    t = _tenant("r3-save")
    _set_checks(t, {"profit_giveback": {"enabled": True, "values": {"giveback_pct": 30}}})
    saved_at = _row(t).updated_at
    _closed(t, 1_000.0, "WIN1")
    _sweep()
    row = _row(t)
    assert row.day_peak_pnl == 1_000.0 and row.updated_at == saved_at


def test_r3_the_ticket_preview_refuses_with_the_same_gate(monkeypatch):
    import random

    from tests import test_fno1_chain_ticket as fno1
    t = fno1._org_tenant("m94")
    expiry = fno1._expiry()
    strike = float(random.randrange(30000, 40000, 50))
    fno1._contract(expiry, strike)
    fno1._price(monkeypatch, 100.0)
    _set_checks(t, {"profit_giveback": {"enabled": True, "values": {"giveback_pct": 25}}})
    assert fno1._preview(t, fno1._body(expiry, strike))["can_place"] is True
    _closed(t, 8_000.0, "WIN1")
    _sweep()
    _closed(t, -2_000.0, "LOSS1")
    view = fno1._preview(t, fno1._body(expiry, strike))
    assert view["can_place"] is False and any("profit give-back stop of 25%" in r for r in view["refusals"]), view["refusals"]


# --- the migration ----------------------------------------------------------------------------------------------------

def _migration():
    path = next((BACKEND / "alembic" / "versions").glob("e1a3c5e7f9b2_*.py"))
    spec = importlib.util.spec_from_file_location("m94_r3_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_adds_nullable_columns_writes_nothing_and_goes_down():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    module = _migration()
    assert module.ROLLBACK_SAFE is True and module.down_revision == "d6f8a0c2e4b3"
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE risk_settings (id INTEGER PRIMARY KEY, tenant_id INTEGER, daily_loss_rupees FLOAT)"))
        conn.execute(sa.text("INSERT INTO risk_settings (id, tenant_id, daily_loss_rupees) VALUES (1, 1, 1000)"))
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
        row = conn.execute(sa.text("SELECT daily_loss_rupees, giveback_pct, day_peak_pnl, day_peak_on, giveback_hit_at FROM risk_settings")).one()
        assert tuple(row) == (1000.0, None, None, None, None)
        with Operations.context(MigrationContext.configure(conn)):
            module.downgrade()
        columns = {c["name"] for c in sa.inspect(conn).get_columns("risk_settings")}
        assert columns == {"id", "tenant_id", "daily_loss_rupees"}


def test_r3_the_worker_cycle_stores_the_peak_and_a_failure_never_breaks_the_cycle(monkeypatch):
    from tests.test_trading_worker import CLOSED_NOW, _FakeBroker, _worker
    seen = []

    async def recording(session, now=None):
        seen.append(now)
        raise RuntimeError("peak store down")
    monkeypatch.setattr(giveback, "sweep", recording)
    worker = _worker(monkeypatch, _FakeBroker())
    report = _run(worker.run_cycle(now=CLOSED_NOW))
    assert seen == [CLOSED_NOW] and any("give-back peak" in e for e in report.errors)
