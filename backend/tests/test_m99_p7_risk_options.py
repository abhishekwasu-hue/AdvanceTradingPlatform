"""M99 P7 (owner order M99, flag `risk_options`): the trader's own risk and management options.

1. Daily loss lock: once the daily loss limit is reached, new entries stay refused for the rest of the IST trading day
   even after the P&L recovers; exits and square-offs still run; it ends at the next trading day.
2. Size step-down after losses in a row: the trader's % of his own size, whole lots, never above it; back to full size
   after his wins in a row or at the next trading day; the same in the backtest as in the worker's entry check.
3. Scale-out: his share of the lots at his level (R), once, never on a loss; the worker closes it through the one
   partial-exit path (fno_position_actions.exit_part), the backtest closes the same lots.
4. Stagnation flag: shown, never an action.
5. Journal metrics: max drawdown, streaks, breach count - hand-computed; counted only.
Each option OFF (the default, or the flag OFF) changes nothing.
"""
import asyncio
import importlib.util
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
import sqlalchemy as sa
from sqlalchemy import select

from app.backtest import engine as underlying_engine
from app.core.enums import SignalDirection, SignalGrade
from app.core.models import RiskConfig, Signal
from app.db.models import RiskSettingsRecord, TradeRecord, User
from app.journal.stats import Decision, risk_metrics
from app.platform import controls
from app.risk_engine import risk_limits, risk_options
from app.risk_engine.risk_manager import RiskManager, TradingDayState
from app.trading.exit_rules import ExitRules, apply_exit_rules, scale_out_level, scale_out_lots
from tests.test_auth_api import _session_factory, client

BACKEND = Path(__file__).resolve().parents[1]
IST = ZoneInfo("Asia/Kolkata")


def _run(coro):
    return asyncio.run(coro)


def _signal(entry=100.0, stop=95.0) -> Signal:
    return Signal(symbol="P7", strategy_id="p7", strategy_name="p7", direction=SignalDirection.LONG, timestamp=datetime.now(UTC),
                  entry=entry, stop_loss=stop, grade=SignalGrade.VALID)


# --- 2. the step-down: pure -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("oldest_first, losses, wins, down", [
    ([], 2, 2, False),
    ([-1], 2, 2, False),
    ([-1, -1], 2, 2, True),                    # two losses in a row
    ([-1, -1, 5], 2, 2, True),                 # one win is not two
    ([-1, -1, 5, 5], 2, 2, False),             # two wins in a row bring the size back
    ([-1, -1, 5, -1, 5], 2, 2, True),          # the wins are not in a row
    ([-1, -1, 5, 5, -1], 2, 2, False),         # one loss after the reset is not two
    ([5, 5, -1, -1], 2, None, True),           # no reset by wins
    ([-1, 0], 2, 2, True),                     # a zero P&L counts as a loss (the losses-in-a-row rule)
    ([-1, -1, -1, 5, 5, 5], 3, 3, False),
])
def test_step_down_state_machine(oldest_first, losses, wins, down):
    assert risk_options.stepped_down(list(reversed(oldest_first)), losses, wins) is down


def test_step_size_whole_lots_at_least_one_and_never_above_his_own():
    assert risk_options.apply_step(4 * 75, 75, 50.0) == (2 * 75, risk_options.apply_step(4 * 75, 75, 50.0)[1])
    qty, note = risk_options.apply_step(3 * 75, 75, 50.0)                 # 1.5 lots rounds down to 1
    assert qty == 75 and note == "Smaller size after losses in a row: 1 lot instead of 3 lots (50% of your size, your own setting)."
    assert risk_options.apply_step(75, 75, 10.0) == (75, None)            # never below one lot, never refused
    assert risk_options.apply_step(2 * 75, 75, 100.0) == (150, None)      # 100 % = his own size
    assert risk_options.apply_step(2 * 75, 75, None) == (150, None)


def test_step_pct_reads_the_reset_he_chose():
    cfg = RiskConfig(step_down_losses=2, step_down_size_pct=50.0, step_down_reset="next_day")
    assert risk_options.step_pct(cfg, [-1, -1], [-1]) is None             # yesterday's losses: a new day starts at full size
    assert risk_options.step_pct(cfg, [-1, -1], [-1, -1]) == 50.0
    wins = cfg.model_copy(update={"step_down_reset": "wins", "step_down_wins": 2})
    assert risk_options.step_pct(wins, [-1, -1], []) == 50.0              # the losses of yesterday still count
    assert risk_options.step_pct(wins, [5, 5, -1, -1], []) is None
    assert risk_options.step_pct(cfg.model_copy(update={"step_down_reset": None}), [-1, -1], [-1, -1]) is None   # not fully set
    assert risk_options.step_pct(wins.model_copy(update={"step_down_wins": None}), [-1, -1], []) is None


def test_engine_applies_the_step_to_his_own_and_to_risk_based_size():
    cfg = RiskConfig(step_down_losses=2, step_down_size_pct=50.0, step_down_reset="next_day", lot_size=1)
    down = TradingDayState(today_pnls=[-1.0, -2.0], closed_pnls=[-1.0, -2.0])
    typed = RiskManager(cfg).validate_and_size(_signal(), down, requested_quantity=4)
    assert typed.approved and typed.quantity == 2 and any("Smaller size after losses in a row" in w for w in typed.warnings)
    risk = cfg.model_copy(update={"capital": 100_000, "risk_per_trade_pct": 1.0})    # 1,000 / 5 = 200 units
    sized = RiskManager(risk).validate_and_size(_signal(), down)
    assert sized.approved and sized.quantity == 100
    full = RiskManager(cfg).validate_and_size(_signal(), TradingDayState(), requested_quantity=4)
    assert full.approved and full.quantity == 4 and not full.warnings
    # with the max lots per order, the smaller of the two applies (the step first)
    capped = RiskManager(cfg.model_copy(update={"max_lots_per_order": 1})).validate_and_size(_signal(), down, requested_quantity=4)
    assert capped.approved is False and capped.blocks[0]["check"] == "max_lots_per_order"
    assert "this order is 2 lots" in capped.reasons[0]                    # the stepped size meets his max lots


# --- 1. the daily loss lock: pure / engine ----------------------------------------------------------------------------------

def test_lock_holds_after_the_pnl_recovers_only_when_he_keeps_it():
    base = RiskConfig(daily_loss_rupees=1000.0)
    recovered = TradingDayState(daily_pnl=200.0, day_trough_pnl=-1200.0)
    assert RiskManager(base).validate_and_size(_signal(), recovered, requested_quantity=1).approved       # main's behaviour
    locked = RiskManager(base.model_copy(update={"daily_loss_lock": True})).validate_and_size(_signal(), recovered, requested_quantity=1)
    assert not locked.approved and locked.blocks[0]["check"] == "daily_loss_lock"
    assert "daily loss limit of ₹1,000 was reached earlier today" in locked.reasons[0] and "exits still work" in locked.reasons[0]
    marked = TradingDayState(daily_pnl=0.0, daily_loss_marked=True)       # the worker saw it through open marks
    assert not RiskManager(base.model_copy(update={"daily_loss_lock": True})).validate_and_size(_signal(), marked, requested_quantity=1).approved
    # a lock with no daily loss limit set holds nothing
    assert RiskManager(RiskConfig(daily_loss_lock=True)).validate_and_size(_signal(), recovered, requested_quantity=1).approved


def test_lock_wording_when_the_limit_is_reached_now():
    out = RiskManager(RiskConfig(daily_loss_rupees=1000.0, daily_loss_lock=True)).validate_and_size(
        _signal(), TradingDayState(daily_pnl=-1500.0), requested_quantity=1)
    assert out.reasons == ["Today's loss ₹1,500 has reached your daily loss limit of ₹1,000. New entries stay off for the rest of "
                           "today (until 00:00 IST), even if the P&L recovers; exits still work."]


# --- off = no behaviour change (parity with main) ----------------------------------------------------------------------------

@pytest.mark.parametrize("state, qty", [
    (TradingDayState(), 3), (TradingDayState(daily_pnl=-500.0, day_trough_pnl=-900.0, closed_pnls=[-1, -1, -1], today_pnls=[-1, -1]), 3),
])
def test_options_off_change_nothing(state, qty):
    for cfg in (RiskConfig(), RiskConfig(daily_loss_rupees=1000.0), RiskConfig(capital=100_000, risk_per_trade_pct=1.0)):
        a = RiskManager(cfg).validate_and_size(_signal(), state, requested_quantity=qty)
        b = RiskManager(risk_options.without_options(cfg.model_copy(update={
            "daily_loss_lock": True, "step_down_losses": 1, "step_down_size_pct": 10.0, "step_down_reset": "next_day"}))
        ).validate_and_size(_signal(), state, requested_quantity=qty)
        assert a.model_dump() == b.model_dump()


def test_switched_off_checks_read_as_not_set():
    cfg = RiskConfig(daily_loss_rupees=1000.0, daily_loss_lock=True, step_down_losses=2, step_down_size_pct=50.0, step_down_wins=2,
                     step_down_reset="wins", stagnation_bars=5, stagnation_move_pct=0.2, stagnation_bar_minutes=5)
    off = risk_limits.apply_switches(cfg, ["daily_loss"])
    assert off.daily_loss_lock is False and off.step_down_losses is None and off.step_down_reset is None and off.stagnation_bars is None
    assert off.daily_loss_rupees == 1000.0
    on = risk_limits.apply_switches(cfg, ["daily_loss", "daily_loss_lock", "loss_step_down"])
    assert on.daily_loss_lock is True and on.step_down_losses == 2 and on.step_down_reset == "wins" and on.stagnation_bars is None


def test_the_page_asks_for_what_an_option_needs():
    ask = {"loss_step_down": {"enabled": True, "values": {"step_down_losses": 2, "step_down_size_pct": 50}}}
    _, _, problems = risk_limits.plan_save(None, {"checks": ask}, {}, options=True)
    assert problems == [risk_options.STEP_NEEDS_RESET]
    _, _, problems = risk_limits.plan_save(None, {"checks": ask, "step_down_reset": "wins"}, {}, options=True)
    assert problems == [risk_options.STEP_NEEDS_WINS]
    _, sw, problems = risk_limits.plan_save(None, {"checks": ask, "step_down_reset": "next_day"}, {}, options=True)
    assert problems == [] and sw["enabled"] == ["loss_step_down"]
    _, _, problems = risk_limits.plan_save(None, {"checks": {"daily_loss_lock": {"enabled": True}}}, {}, options=True)
    assert problems == [risk_options.LOCK_NEEDS_LIMIT]
    _, _, problems = risk_limits.plan_save(None, {"checks": {"stagnation_flag": {"enabled": True, "values": {
        "stagnation_bars": 5, "stagnation_move_pct": 0.3, "stagnation_bar_minutes": 7}}}}, {}, options=True)
    assert problems == [risk_options.STAGNATION_BAR_WORDS]
    # with the flag OFF an option is an unknown check, never saved
    _, _, problems = risk_limits.plan_save(None, {"checks": {"daily_loss_lock": {"enabled": True}}}, {}, options=False)
    assert problems == ["One of the limits is not known. Reload the page and try again."]
    assert {c["id"] for c in risk_limits.view(None, {})["checks"]}.isdisjoint(risk_limits.OPTION_CHECKS)
    assert {c["id"] for c in risk_limits.view(None, {}, options=True)["checks"]} >= risk_limits.OPTION_CHECKS
    assert all(not c["enabled"] for c in risk_limits.view(None, {}, options=True)["checks"])      # every option OFF by default


# --- 2. step-down in a backtest (the worker's same entry check) ----------------------------------------------------------------

def _scripted_backtest(cfg: RiskConfig, exit_rules: ExitRules | None = None):
    from tests.test_cp6_15_loss_streak import _frame, _Scripted
    return underlying_engine.run_backtest(_Scripted(), _frame(), "NIFTY 50", "1min", cfg, exit_rules=exit_rules)


def _quantities(result) -> list[float]:
    return [t.quantity for t in sorted(result.trades, key=lambda t: pd.Timestamp(t.entry_time))]


def test_backtest_step_down_next_day_and_wins():
    base = RiskConfig(capital=1_000_000, risk_per_trade_pct=1.0, min_risk_reward=1.0)
    plain = _quantities(_scripted_backtest(base))
    full = plain[0]
    assert len(plain) == 13 and set(plain) == {full}                      # no option: every entry at the same size
    half = risk_options.apply_step(full, 1, 50.0)[0]
    # script L L L W L | L W L L | L L L W (tests/test_cp6_15_loss_streak.py)
    nd = _quantities(_scripted_backtest(base.model_copy(update={"step_down_losses": 2, "step_down_size_pct": 50.0, "step_down_reset": "next_day"})))
    F, H = full, half
    assert nd == [F, F, H, H, H, F, F, F, F, F, F, H, H]
    wins = _quantities(_scripted_backtest(base.model_copy(update={"step_down_losses": 2, "step_down_size_pct": 50.0, "step_down_reset": "wins",
                                                                 "step_down_wins": 1})))
    assert wins == [F, F, H, H, F, F, H, F, F, H, H, H, H]


# --- 3. scale-out ---------------------------------------------------------------------------------------------------------------

def test_scale_out_rule_level_lots_and_never_on_a_loss():
    rules = ExitRules(scale_out_at_r=1.0, scale_out_pct=50.0)
    assert rules.active and rules.scale_out_ready and "scale out 50% at 1R" in rules.describe()
    assert not ExitRules(scale_out_at_r=1.0).scale_out_ready and not ExitRules(scale_out_pct=50.0).scale_out_ready
    assert scale_out_level(rules, direction="LONG", entry_price=100.0, initial_stop=95.0, high=104.9, low=104.9) is None
    assert scale_out_level(rules, direction="LONG", entry_price=100.0, initial_stop=95.0, high=105.0, low=105.0) == 105.0
    assert scale_out_level(rules, direction="SHORT", entry_price=100.0, initial_stop=105.0, high=95.0, low=95.0) == 95.0
    assert scale_out_level(rules, direction="SHORT", entry_price=100.0, initial_stop=105.0, high=101.0, low=101.0) is None   # a loss
    assert [scale_out_lots(rules, n) for n in (1, 2, 3, 4, 5)] == [0, 1, 1, 2, 2]
    assert scale_out_lots(ExitRules(scale_out_at_r=1.0, scale_out_pct=99.0), 4) == 3       # always a rest
    upd = apply_exit_rules(rules, direction="LONG", entry_price=100.0, initial_stop=95.0, current_stop=95.0, best_price=None,
                           high=106.0, low=100.0, entry_time=datetime.now(UTC), now=datetime.now(UTC))
    assert upd.scale_out_level == 105.0 and upd.stop_loss == 95.0 and not upd.stop_changed      # it never moves the stop itself
    assert ExitRules.from_json(rules.to_json()) == rules


def _scale_frame() -> pd.DataFrame:
    """Flat bars at 1000; an entry at bar 10; bar 11 reaches +0.6 % (the 0.5R level, not the 1 % target); bar 13 the target."""
    idx = pd.date_range("2026-10-07 09:15", periods=20, freq="1min")
    rows = []
    for i, _ in enumerate(idx):
        high = 1006.0 if i == 11 else 1010.5 if i == 13 else 1000.5
        rows.append({"open": 1000.0, "high": high, "low": 999.5, "close": 1000.0, "volume": 1000.0})
    return pd.DataFrame(rows, index=idx)


class _Once:
    id = "p7_once"
    timeframes = ["1min"]
    params: dict = {}

    def min_history(self):
        return {"1min": 5}

    def analyze(self, data, symbol):
        ts = data["1min"].index[-1]
        if ts == pd.Timestamp("2026-10-07 09:25"):
            return Signal(symbol=symbol, strategy_id=self.id, strategy_name="once", direction=SignalDirection.LONG, timestamp=ts, entry=1000.0,
                          stop_loss=990.0, target1=1010.0, risk_reward=1.0, score=80, grade=SignalGrade.HIGH_QUALITY)
        return Signal(symbol=symbol, strategy_id=self.id, strategy_name="once", direction=SignalDirection.NO_TRADE, timestamp=ts)


def test_backtest_scale_out_closes_his_share_at_his_level_then_the_rest_at_the_target():
    cfg = RiskConfig(min_risk_reward=1.0, lot_size=1)
    rules = ExitRules(scale_out_at_r=0.5, scale_out_pct=50.0)
    plain = underlying_engine.run_backtest(_Once(), _scale_frame(), "P7TEST", "1min", cfg.model_copy(update={"capital": 100_000, "risk_per_trade_pct": 0.4}))
    assert len(plain.trades) == 1 and plain.trades[0].exit_price == 1010.0
    out = underlying_engine.run_backtest(_Once(), _scale_frame(), "P7TEST", "1min", cfg.model_copy(update={"capital": 100_000, "risk_per_trade_pct": 0.4}),
                                         exit_rules=rules)
    held = plain.trades[0].quantity
    assert len(out.trades) == 2
    part, rest = out.trades
    assert part.exit_reason == "Scale-out (50% at 0.5R)" and part.exit_price == 1005.0
    assert part.quantity == scale_out_lots(rules, int(held)) and rest.quantity == held - part.quantity
    assert rest.exit_reason == plain.trades[0].exit_reason and rest.exit_price == plain.trades[0].exit_price


# --- the API flows: lock, step-down, scale-out in the worker, stagnation, journal ---------------------------------------------

def _tenant(tag: str) -> dict:
    from tests.test_trading_worker import _tenant as worker_tenant
    return worker_tenant(f"p7-{tag}-{uuid.uuid4().hex[:6]}@example.com")


def _flag(tenant_id: int, on: bool = True) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            entry = flags.get(risk_options.FLAG) or {"on": False, "tenants": []}
            tenants = set(entry.get("tenants") or [])
            (tenants.add if on else tenants.discard)(tenant_id)
            flags[risk_options.FLAG] = {"on": False, "tenants": sorted(tenants)}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


def _put(t: dict, body: dict):
    return client.put("/api/risk-settings/limits", headers=t["headers"], json=body)


def _today(minutes_ago: float) -> datetime:
    now = datetime.now(UTC)
    day_start = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    return max(now - timedelta(minutes=minutes_ago), day_start + timedelta(seconds=1))


def _closed(t: dict, pnl: float, symbol: str, minutes_ago: float = 1.0) -> None:
    async def go():
        async with _session_factory() as session:
            session.add(TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="PAPER", symbol=symbol, strategy_id="s",
                                    direction="LONG", entry_time=_today(minutes_ago + 30), entry_price=100.0, quantity=10, stop_loss=98.0,
                                    target1=110.0, exit_time=_today(minutes_ago), exit_price=100.0 + pnl / 10, exit_reason="Target", pnl=pnl))
            await session.commit()
    _run(go())


def _entry(t: dict, key: str, qty: float = 1):
    from app.execution.signal_execution import execute_signal_for_user
    from tests.test_live_execution import _signal as live_signal

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            return await execute_signal_for_user(session, user, mode="PAPER", strategy_id="ema_rsi_scalper_1m", signal=live_signal(),
                                                 idempotency_key=key, max_quantity=qty, holding="INTRADAY", order_style="MARKET")
    return _run(go())


def _state(t: dict, now: datetime | None = None) -> TradingDayState:
    from app.trading.persistence import build_trading_day_state

    async def go():
        async with _session_factory() as session:
            return await build_trading_day_state(session, await session.get(User, t["user_id"]), now=now)
    return _run(go())


def _row(t: dict) -> RiskSettingsRecord:
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(RiskSettingsRecord).where(RiskSettingsRecord.tenant_id == t["tenant_id"]))
    return _run(go())


def test_flag_off_the_page_shows_no_option_and_refuses_one():
    t = _tenant("off")
    view = client.get("/api/risk-settings/limits", headers=t["headers"]).json()
    assert {c["id"] for c in view["checks"]}.isdisjoint(risk_limits.OPTION_CHECKS) and "step_down_reset" not in view
    assert _put(t, {"checks": {"daily_loss_lock": {"enabled": True}}}).status_code == 422
    assert client.get("/api/risk-settings/stagnation", headers=t["headers"]).json() == {"on": False, "positions": []}


def test_flag_off_a_stored_option_is_never_read():
    t = _tenant("stored")
    _flag(t["tenant_id"])
    assert _put(t, {"checks": {"daily_loss": {"enabled": True, "values": {"daily_loss_rupees": 1000}}, "daily_loss_lock": {"enabled": True}}}).status_code == 200
    _flag(t["tenant_id"], on=False)
    _closed(t, -1500.0, "L1", minutes_ago=5)
    _closed(t, 2000.0, "W1")
    result, _ = _entry(t, f"p7-stored-{uuid.uuid4().hex[:6]}")
    assert result.executed, result.reasons                                # the lock is not read with the flag OFF
    state = _state(t)
    assert state.day_trough_pnl == 0.0 and state.closed_pnls == [] and not state.daily_loss_marked


def test_daily_lock_refuses_new_entries_all_day_exits_run_and_it_resets(monkeypatch):
    from tests.test_m94_risk_tools import _exits_run_without_the_gate, _open
    t = _tenant("lock")
    _flag(t["tenant_id"])
    open_ids = [_open(t), _open(t)]
    out = _put(t, {"checks": {"daily_loss": {"enabled": True, "values": {"daily_loss_rupees": 1000}}, "daily_loss_lock": {"enabled": True}}})
    assert out.status_code == 200, out.text
    assert {c["id"]: c["enabled"] for c in out.json()["checks"]}["daily_loss_lock"] is True
    _closed(t, -1500.0, "L1", minutes_ago=5)
    _closed(t, 2000.0, "W1")                                              # today's P&L is +500 now
    state = _state(t)
    assert state.daily_pnl == pytest.approx(500.0) and state.day_trough_pnl == pytest.approx(-1500.0)
    refused, order = _entry(t, f"p7-lock-{uuid.uuid4().hex[:6]}")
    assert not refused.executed and order.status == "REJECTED"
    assert any("was reached earlier today" in r and "exits still work" in r for r in refused.reasons), refused.reasons
    closed = _exits_run_without_the_gate(t, open_ids, monkeypatch)
    assert all(c.exit_time is not None and c.pnl is not None for c in closed)          # ADR-0004
    tomorrow = datetime.now(IST).replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=1)
    next_day = _state(t, now=tomorrow)
    assert next_day.day_trough_pnl == 0.0 and not next_day.daily_loss_marked
    cfg = RiskConfig(daily_loss_rupees=1000.0, daily_loss_lock=True)
    assert RiskManager(cfg).validate_and_size(_signal(), next_day, requested_quantity=1).approved
    # without the lock (main's behaviour) the recovered P&L lets a new entry through
    _put(t, {"checks": {"daily_loss_lock": {"enabled": False}}})
    lifted, _ = _entry(t, f"p7-lock2-{uuid.uuid4().hex[:6]}")
    assert lifted.executed, lifted.reasons


def test_the_worker_marks_the_day_and_the_mark_ends_next_day():
    t = _tenant("mark")
    _flag(t["tenant_id"])
    _put(t, {"checks": {"daily_loss": {"enabled": True, "values": {"daily_loss_rupees": 1000}}, "daily_loss_lock": {"enabled": True}}})
    saved_at = _row(t).updated_at

    async def sweep(now=None):
        async with _session_factory() as session:
            return await risk_options.sweep(session, now)
    _run(sweep())
    assert _row(t).daily_loss_locked_at is None                           # not reached: nothing written
    _closed(t, -1200.0, "L1")
    _run(sweep())
    row = _row(t)
    assert row.daily_loss_locked_at is not None and row.updated_at == saved_at
    assert _state(t).daily_loss_marked
    tomorrow = datetime.now(IST).replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=1)
    assert not _state(t, now=tomorrow).daily_loss_marked


def test_step_down_in_the_entry_check_reads_the_stored_trades():
    t = _tenant("step")
    _flag(t["tenant_id"])
    body = {"checks": {"loss_step_down": {"enabled": True, "values": {"step_down_losses": 2, "step_down_size_pct": 50, "step_down_wins": 1}}},
            "step_down_reset": "wins"}
    out = _put(t, body)
    assert out.status_code == 200, out.text
    assert out.json()["step_down_reset"] == "wins"
    _closed(t, -100.0, "L1", minutes_ago=10)
    _closed(t, -100.0, "L2", minutes_ago=5)
    state = _state(t)
    assert state.closed_pnls[:2] == [-100.0, -100.0]
    result, order = _entry(t, f"p7-step-{uuid.uuid4().hex[:6]}", qty=4)
    assert result.executed, result.reasons
    assert order.quantity == 2
    _closed(t, 300.0, "W1")
    result, order = _entry(t, f"p7-step2-{uuid.uuid4().hex[:6]}", qty=4)
    assert result.executed and order.quantity == 4                        # one win in a row brings the full size back


def _open_scaled(t: dict, rules: ExitRules, lots: int = 4) -> int:
    async def go():
        async with _session_factory() as session:
            trade = TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="PAPER", symbol=f"SC{uuid.uuid4().hex[:4]}",
                                strategy_id="s", direction="LONG", entry_time=_today(20), entry_price=100.0, quantity=lots, lot_size=1,
                                stop_loss=95.0, initial_stop_loss=95.0, target1=120.0, exit_rules=rules.to_json())
            session.add(trade)
            await session.commit()
            return trade.id
    return _run(go())


def _monitor(t: dict, price: float):
    from app.trading.position_monitor import monitor_open_positions

    async def lookup(symbol, exchange):
        return price

    async def go():
        async with _session_factory() as session:
            return await monitor_open_positions(session, t["tenant_id"], lookup)
    return _run(go())


def _rows(t: dict, symbol_prefix: str = "SC") -> list[TradeRecord]:
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == t["tenant_id"],
                                                                        TradeRecord.symbol.startswith(symbol_prefix)).order_by(TradeRecord.id)))
    return _run(go())


def test_worker_scale_out_through_the_one_partial_exit_path_once(monkeypatch):
    from app.paper import fno_position_actions
    t = _tenant("scale")
    _flag(t["tenant_id"])
    rules = ExitRules(scale_out_at_r=1.0, scale_out_pct=50.0)
    trade_id = _open_scaled(t, rules)

    async def priced(session, user, trade, *, exit_only=False):
        return 105.5, "the monitor's price"
    monkeypatch.setattr(fno_position_actions, "_price", priced)
    calls = []
    real = fno_position_actions.exit_part

    async def spy(*args, **kwargs):
        calls.append(kwargs)
        return await real(*args, **kwargs)
    monkeypatch.setattr(fno_position_actions, "exit_part", spy)
    _monitor(t, 104.0)                                                    # below 1R: nothing
    assert calls == [] and len(_rows(t)) == 1
    _monitor(t, 105.5)                                                    # 1.1R: his half closes
    rows = _rows(t)
    rest = next(r for r in rows if r.id == trade_id)
    part = next(r for r in rows if r.id != trade_id)
    assert rest.exit_time is None and rest.quantity == 2 and rest.stop_loss == 95.0
    assert part.exit_time is not None and part.quantity == 2 and part.exit_reason.startswith("Scale-out 50% at 1R")
    # the same lots as the backtest closes for the same rule and holding (backtest = worker)
    assert part.quantity == scale_out_lots(rules, 4)
    assert len(calls) == 1 and calls[0]["client_ref"] == f"scale-out-{trade_id}"
    _monitor(t, 106.0)                                                    # once per position
    assert len(_rows(t)) == 2 and _rows(t)[0].quantity == 2


def test_worker_scale_out_off_with_the_flag_off_and_never_on_a_loss():
    t = _tenant("scale-off")
    trade_id = _open_scaled(t, ExitRules(scale_out_at_r=1.0, scale_out_pct=50.0))
    _monitor(t, 105.5)                                                    # flag OFF: nothing
    assert len(_rows(t)) == 1 and _rows(t)[0].quantity == 4
    _flag(t["tenant_id"])
    _monitor(t, 99.0)                                                     # a loss: never scaled out
    rows = _rows(t)
    assert len(rows) == 1 and rows[0].id == trade_id and rows[0].exit_time is None


def test_stagnation_is_shown_and_nothing_happens(monkeypatch):
    from app.market_store import reader
    t = _tenant("stag")
    _flag(t["tenant_id"])
    out = _put(t, {"checks": {"stagnation_flag": {"enabled": True, "values": {"stagnation_bars": 3, "stagnation_move_pct": 0.5,
                                                                               "stagnation_bar_minutes": 5}}}})
    assert out.status_code == 200, out.text
    trade_id = _open_scaled(t, ExitRules(), lots=1)
    start = _today(20).replace(second=0, microsecond=0)

    class _Got:
        def __init__(self, frame):
            self.frame = frame

    async def bars(session, symbol, interval, start_at, end_at, **kwargs):
        idx = pd.DatetimeIndex([start + timedelta(minutes=5 * (k + 1)) for k in range(3)])
        return _Got(pd.DataFrame({"open": [100.0] * 3, "high": [100.2, 100.3, 100.1], "low": [99.9, 99.95, 99.9], "close": [100.0] * 3}, index=idx))
    monkeypatch.setattr(reader, "read_bars", bars)
    view = client.get("/api/risk-settings/stagnation", headers=t["headers"]).json()
    item = next(p for p in view["positions"] if p["trade_id"] == trade_id)
    assert view["on"] and item["state"] == "not_moving" and item["moved_pct"] == 0.4 and item["bars"] == 3
    assert "nothing is closed" in view["note"]
    row = next(r for r in _rows(t) if r.id == trade_id)
    assert row.exit_time is None and row.quantity == 1 and row.stop_loss == 95.0       # no action of any kind
    assert risk_options.stagnation([101.0, 102.0], [100.0, 100.5], entry=100.0, bars=3, move_pct=1.0) is None   # too few bars
    assert risk_options.stagnation([101.0, 102.0], [100.0, 100.5], entry=100.0, bars=2, move_pct=1.0)["not_moving"] is False


# --- 5. journal metrics: hand-computed -------------------------------------------------------------------------------------------

def _d(i: int, pnl: float | None, risk: float = 100.0, setup: str | None = "S1") -> Decision:
    return Decision(unit=f"u{i}", setup=setup, timeframe="5min", entry_time=datetime(2026, 10, 1, tzinfo=UTC) + timedelta(hours=i),
                    pnl=pnl, risk=risk)


def test_journal_metrics_hand_computed():
    # P&L in order: +200, -100, -150, -50, +300, 0, +100, -250 (open one ignored); risk 100 each
    pnls = [200.0, -100.0, -150.0, -50.0, 300.0, 0.0, 100.0, -250.0]
    items = [_d(i, p, setup=None if i == 4 else "S1") for i, p in enumerate(pnls)] + [_d(99, None)]
    m = risk_metrics(items)
    # running: 200, 100, -50, -100, 200, 200, 300, 50 -> peak 300, trough after it 50; earlier 200 -> -100 = 300
    assert m["max_drawdown"] == 300.0 and m["closed"] == 8
    assert m["longest_loss_streak"] == 3 and m["longest_win_streak"] == 1      # the flat result ends the win run
    assert m["streak_now"] == {"kind": "loss", "length": 1}
    # below -1R: -150 (-1.5R) and -250 (-2.5R); -100 is exactly -1R (not beyond the plan); one trade has no named setup
    assert m["breaches"] == {"loss_beyond_plan": 2, "no_named_setup": 1, "total": 3}
    assert "counted only" in m["note"]


def test_journal_metrics_on_a_synthetic_paper_history_with_the_summary():
    from app.journal.stats import summary
    pnls = [500.0, -250.0, -250.0, 750.0, -250.0, 1000.0, -500.0, 250.0]
    items = [_d(i, p, risk=250.0) for i, p in enumerate(pnls)]
    s, m = summary(items), risk_metrics(items)
    assert s["win_rate"] == 50.0 and s["avg_r"] == pytest.approx(sum(p / 250.0 for p in pnls) / 8, abs=0.01)
    assert s["expectancy"] == pytest.approx(sum(pnls) / 8)
    # running 500, 250, 0, 750, 500, 1500, 1000, 1250 -> drawdowns 500 (500->0) and 500 (1500->1000)
    assert m["max_drawdown"] == 500.0 and m["longest_loss_streak"] == 2 and m["longest_win_streak"] == 1
    assert m["breaches"]["loss_beyond_plan"] == 1                          # -500 is -2R


def test_journal_stats_route_adds_the_metrics_only_with_the_flag():
    from tests.test_m62_journal_layer import _set_flag as journal_flag
    t = _tenant("journal")
    journal_flag(True, [t["tenant_id"]])
    before = client.get("/api/journal/stats", headers=t["headers"])
    assert before.status_code == 200, before.text
    assert "risk_metrics" not in before.json()
    _flag(t["tenant_id"])
    after = client.get("/api/journal/stats", headers=t["headers"]).json()
    journal_flag(False)
    assert "risk_metrics" in after and after["risk_metrics"]["breaches"]["total"] >= 0


# --- the migration ---------------------------------------------------------------------------------------------------------------

def test_migration_adds_nullable_columns_writes_nothing_and_goes_down():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = next((BACKEND / "alembic" / "versions").glob("b4d6f8a0c2e5_*.py"))
    spec = importlib.util.spec_from_file_location("m99_p7_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.ROLLBACK_SAFE is True and module.down_revision == "a3c5e7f9b1d4"
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE risk_settings (id INTEGER PRIMARY KEY, tenant_id INTEGER, daily_loss_rupees FLOAT)"))
        conn.execute(sa.text("INSERT INTO risk_settings (id, tenant_id, daily_loss_rupees) VALUES (1, 1, 1000)"))
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
        row = conn.execute(sa.text("SELECT daily_loss_rupees, step_down_losses, step_down_size_pct, step_down_wins, step_down_reset, "
                                   "stagnation_bars, stagnation_move_pct, stagnation_bar_minutes, daily_loss_locked_at FROM risk_settings")).one()
        assert tuple(row) == (1000.0, None, None, None, None, None, None, None, None)
        with Operations.context(MigrationContext.configure(conn)):
            module.downgrade()
        assert {c["name"] for c in sa.inspect(conn).get_columns("risk_settings")} == {"id", "tenant_id", "daily_loss_rupees"}


def test_book_reference_numbers_are_never_defaults():
    cfg = RiskConfig()
    assert not risk_options.any_on(cfg) and cfg.daily_loss_lock is False
    assert all(getattr(cfg, k) is None for k in risk_options.OPTION_FIELDS)
    assert ExitRules().scale_out_at_r is None and ExitRules().scale_out_pct is None
    assert json.loads(ExitRules(scale_out_at_r=1.0, scale_out_pct=50.0).to_json()) == {"scale_out_at_r": 1.0, "scale_out_pct": 50.0}
