"""CP6-15: the losses-in-a-row pause ends.

Before CP6-15 the pause never ended: live / PAPER counted the last closed trades of all time and both backtest engines
reset the counter only on a win - with no new trade there was no win. Now the trader's setting decides:
"Pause until the next session" (the default: only the current Indian trading session's losses count) or "Pause until I
resume" (only the losses since the last "Resume after losses in a row"; a backtest cannot resume, so the pause holds to
the end of the run and the result says so). The same scripted sequence gives the same allow / refuse decisions in the
live RiskManager-with-persistence, app/backtest/engine.py and app/backtest/options_engine.py. Exits are never blocked.
"""
import asyncio
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd
import pytest
from sqlalchemy import delete, select

from app.backtest import engine as underlying_engine
from app.backtest import options_engine
from app.backtest.options import to_utc
from app.backtest.repro import result_hash
from app.core.enums import OptionPosition, OptionStrategy, SignalDirection, SignalGrade
from app.core.models import RiskConfig, Signal
from app.db.models import AuditLogRecord, RiskSettingsRecord, TradeRecord, User
from app.market_data.calendar import trading_day_start
from app.risk_engine import loss_streak
from app.risk_engine.loss_streak import BacktestLossStreak
from app.risk_engine.risk_manager import RiskManager
from app.trading.persistence import build_trading_day_state, loss_streak_status
from tests.test_auth_api import _register, _session_factory, client

LIMIT = 3
PRICE = 24_500.0
# One scripted sequence: per session (IST day), the outcome each entry attempt WOULD have if it is allowed.
# L = the next bar reaches the stop, W = the next bar reaches the target.
DAYS = ("2026-10-07", "2026-10-08", "2026-10-09")          # Wed / Thu / Fri - no NIFTY expiry on these days
SCRIPT: Tuple[Tuple[str, ...], ...] = (("L", "L", "L", "W", "L"), ("L", "W", "L", "L"), ("L", "L", "L", "W"))
ATTEMPT_EVERY = 6                                           # bars between attempts (the trade closes on the bar after)
FIRST_ATTEMPT = 10                                          # bar index of the first attempt each day

EXPECTED = {
    # session 1: three losses -> paused for the rest of it; session 2 starts again (L W L L); session 3 starts again
    "next_session": [True, True, True, False, False, True, True, True, True, True, True, True, False],
    # until I resume: nobody resumes a test, so after the third loss every later attempt is refused, in every session
    "until_resume": [True, True, True, False, False, False, False, False, False, False, False, False, False],
}


def _run(coro):
    return asyncio.run(coro)


def _attempts() -> List[Tuple[pd.Timestamp, str]]:
    out = []
    for day, outcomes in zip(DAYS, SCRIPT):
        start = pd.Timestamp(f"{day} 09:15")
        for k, outcome in enumerate(outcomes):
            out.append((start + pd.Timedelta(minutes=FIRST_ATTEMPT + k * ATTEMPT_EVERY), outcome))
    return out


def _frame() -> pd.DataFrame:
    """Flat bars at PRICE; on the bar after each attempt the range reaches 1.5% below (L) or above (W) - one side only."""
    moves = {ts + pd.Timedelta(minutes=1): outcome for ts, outcome in _attempts()}
    rows, index = [], []
    for day in DAYS:
        for ts in pd.date_range(f"{day} 09:15", periods=FIRST_ATTEMPT + ATTEMPT_EVERY * 6, freq="1min"):
            high, low = PRICE + 0.05, PRICE - 0.05
            if moves.get(ts) == "L":
                low = PRICE * 0.985
            elif moves.get(ts) == "W":
                high = PRICE * 1.015
            rows.append({"open": PRICE, "high": high, "low": low, "close": PRICE, "volume": 1000.0})
            index.append(ts)
    return pd.DataFrame(rows, index=pd.DatetimeIndex(index))


def _signal(ts, direction=SignalDirection.LONG) -> Signal:
    return Signal(symbol="NIFTY 50", strategy_id="cp6_15_script", strategy_name="script", direction=direction, timestamp=ts, entry=PRICE,
                  stop_loss=PRICE * 0.99, target1=PRICE * 1.01, target2=None, risk_reward=1.0, score=80, grade=SignalGrade.HIGH_QUALITY)


class _Scripted:
    """Asks to enter on every scripted attempt bar and nowhere else."""
    id = "cp6_15_script"
    timeframes = ["1min"]
    params: Dict = {}

    def __init__(self):
        self.at = {ts for ts, _ in _attempts()}

    def min_history(self):
        return {"1min": 5}

    def analyze(self, data, symbol):
        ts = data["1min"].index[-1]
        if ts in self.at:
            return _signal(ts)
        return Signal(symbol=symbol, strategy_id=self.id, strategy_name="script", direction=SignalDirection.NO_TRADE, timestamp=ts)


def _risk(mode: str) -> RiskConfig:
    return RiskConfig(capital=1_000_000, risk_per_trade_pct=1.0, max_consecutive_losses=LIMIT, min_risk_reward=1.0, loss_streak_pause=mode)


def _decisions(result) -> List[bool]:
    entered = {pd.Timestamp(t.entry_time) for t in result.trades}
    return [ts in entered for ts, _ in _attempts()]


def _outcomes_hold(result):
    """The engine's trades close as scripted (L -> a loss, W -> a win), so the decisions are compared on the same facts."""
    by_entry = {pd.Timestamp(t.entry_time): t for t in result.trades}
    for ts, outcome in _attempts():
        trade = by_entry.get(ts)
        if trade is not None:
            assert (trade.pnl > 0) == (outcome == "W"), (ts, outcome, trade.exit_reason, trade.pnl)


def _engine_decisions(mode: str):
    result = underlying_engine.run_backtest(_Scripted(), _frame(), "NIFTY 50", "1min", _risk(mode))
    _outcomes_hold(result)
    return _decisions(result), result


def _options_decisions(mode: str):
    cfg = options_engine.OptionBacktestConfig(option_strategy=OptionStrategy.SINGLE, option_position=OptionPosition.BUY, implied_volatility=0.14,
                                              max_lots=1, premium_stop_pct=30.0)  # DEF-0: the old built-in 30% set explicitly
    result = options_engine.run_option_backtest(_Scripted(), _frame(), "NIFTY 50", "1min", _risk(mode), cfg)
    _outcomes_hold(result)
    # every attempt it did not take was refused by the risk engine's losses in a row, nothing else
    assert all("consecutive losses" in key.lower() for key in result.options["signals_skipped"]), result.options["signals_skipped"]
    return _decisions(result), result


def _new_user(email: str) -> dict:
    token = _register(email)
    headers = {"Authorization": f"Bearer {token}"}
    return {"headers": headers, **client.get("/api/auth/me", headers=headers).json()}


def _save_mode(me: dict, mode: str, limit: int = LIMIT) -> dict:
    body = {**client.get("/api/risk-settings", headers=me["headers"]).json(), "max_consecutive_losses": limit, "min_risk_reward": 1.0,
            "loss_streak_pause": mode}
    res = client.put("/api/risk-settings", headers=me["headers"], json=body)
    assert res.status_code == 200, res.text
    return res.json()


def _live_decisions(me: dict, mode: str) -> List[bool]:
    """RiskManager-with-persistence: at each attempt the state is built from the stored trades at that moment; an approved
    attempt is stored as a trade closed on the next bar with the scripted outcome."""
    _save_mode(me, mode)

    async def go():
        out = []
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            cfg = await _tenant_cfg(session, me["tenant_id"])
            for ts, outcome in _attempts():
                now = to_utc(ts)
                state = await build_trading_day_state(session, user, now=now)
                approved = RiskManager(cfg).validate_and_size(_signal(ts), state).approved
                out.append(approved)
                if approved:
                    session.add(TradeRecord(tenant_id=me["tenant_id"], user_id=me["id"], mode="PAPER", symbol="NIFTY 50", strategy_id="cp6_15_script",
                                            direction="LONG", entry_time=now, entry_price=PRICE, quantity=1, stop_loss=PRICE * 0.99,
                                            exit_time=now + timedelta(minutes=1), exit_price=PRICE * (1.01 if outcome == "W" else 0.99),
                                            pnl=245.0 if outcome == "W" else -245.0))
                    await session.commit()
            await session.execute(delete(TradeRecord).where(TradeRecord.tenant_id == me["tenant_id"]))
            await session.commit()
        return out
    return _run(go())


async def _tenant_cfg(session, tenant_id: int) -> RiskConfig:
    from app.risk_engine.routes import get_tenant_risk_config
    cfg = await get_tenant_risk_config(tenant_id, session)
    assert cfg is not None
    return cfg


# --- the shared rule ---------------------------------------------------------------------------------------------------

def test_the_default_is_next_session_and_the_default_limit_is_unchanged():
    # DEF-0 (owner, 28 Oct): no built-in losses-in-a-row limit - not set (None) until the trader sets one
    assert RiskConfig().loss_streak_pause == "next_session" and RiskConfig().max_consecutive_losses is None
    with pytest.raises(ValueError):
        RiskConfig(loss_streak_pause="forever")


def test_backtest_counter_resets_at_the_session_only_for_next_session():
    nxt, held = BacktestLossStreak("next_session", 2), BacktestLossStreak("until_resume", 2)
    for c in (nxt, held):
        assert c.closed(-1.0, "t1") == 1 and c.closed(0.0, "t2") == 2        # a zero P&L counts as a loss, as before
    assert nxt.new_session() == 0 and held.new_session() == 2
    assert nxt.report()["pauses"] == 1 and not nxt.report()["held_to_end"] and "next session" in nxt.report()["note"]
    assert held.report()["held_to_end"] and "nobody can resume a test" in held.report()["note"]
    assert BacktestLossStreak("next_session", 2).report() is None              # never reached: nothing to say
    assert loss_streak.streak_of([-1.0, 0.0, 5.0, -1.0]) == 2 and loss_streak.streak_of([None, -1.0]) == 0


# --- parity: one script, three paths, both modes ------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["next_session", "until_resume"])
def test_the_same_script_gives_the_same_decisions_live_and_in_both_backtest_engines(mode):
    me = _new_user(f"cp615-parity-{mode}@example.com")
    live = _live_decisions(me, mode)
    engine, engine_result = _engine_decisions(mode)
    options, options_result = _options_decisions(mode)
    assert live == engine == options == EXPECTED[mode]
    for result in (engine_result, options_result):
        report = result.loss_streak
        assert report is not None and report["mode"] == mode and report["limit"] == LIMIT
        if mode == "until_resume":
            assert report["held_to_end"] and report["pauses"] == 1
            assert "stayed paused to the end of this test" in report["note"] and "nobody can resume a test" in report["note"]
        else:
            assert not report["held_to_end"] and report["pauses"] == 2 and "each pause ended at the next session" in report["note"]


def test_regression_the_backtest_pause_now_ends_at_the_next_session():
    # Before CP6-15 the counter reset only on a win: after session 1's three losses no trade was ever taken again.
    decisions, _ = _engine_decisions("next_session")
    assert decisions[5] is True and sum(decisions[5:]) > 0
    decisions, _ = _options_decisions("next_session")
    assert decisions[5] is True


def test_a_run_whose_pause_never_engages_reports_nothing_and_hashes_without_the_field():
    result = underlying_engine.run_backtest(_Scripted(), _frame(), "NIFTY 50", "1min", _risk("next_session").model_copy(update={"max_consecutive_losses": 50}))
    assert result.loss_streak is None
    assert result_hash(result) == result_hash(result.model_copy())   # stable, and "loss_streak" left out like "execution"


def test_engine_versions_were_bumped_because_results_change():
    assert underlying_engine.ENGINE_VERSION == "5" and options_engine.ENGINE_VERSION == "9-options"


# --- live / PAPER -------------------------------------------------------------------------------------------------------

def _store_losses(me: dict, times: List[datetime]):
    async def go():
        async with _session_factory() as session:
            for t in times:
                session.add(TradeRecord(tenant_id=me["tenant_id"], user_id=me["id"], mode="PAPER", symbol="A", strategy_id="s", direction="LONG",
                                        entry_time=t - timedelta(minutes=5), entry_price=100.0, quantity=1, stop_loss=99.0,
                                        exit_time=t, exit_price=99.0, pnl=-1.0))
            await session.commit()
    _run(go())


def _state(me: dict, now: datetime):
    async def go():
        async with _session_factory() as session:
            return await build_trading_day_state(session, await session.get(User, me["id"]), now=now)
    return _run(go())


def test_regression_the_live_pause_now_ends_at_the_next_session():
    # Before CP6-15 the live counter read the last 20 closed trades of all time: yesterday's losses paused today too.
    me = _new_user("cp615-live-next@example.com")
    now = datetime.now(timezone.utc)
    start = trading_day_start(now)
    _store_losses(me, [start - timedelta(hours=3, minutes=m) for m in range(5)])   # five losses in the previous session
    state = _state(me, now)
    assert state.consecutive_losses == 0
    assert RiskManager(RiskConfig(max_consecutive_losses=LIMIT, min_risk_reward=1.0)).validate_and_size(_signal(now), state).approved
    # three losses this session pause again, for this session only
    _store_losses(me, [max(start + timedelta(minutes=1), now - timedelta(minutes=m)) for m in (3, 2, 1)])
    assert _state(me, now).consecutive_losses == 3
    assert _state(me, start + timedelta(days=1, minutes=1)).consecutive_losses == 0


def test_until_resume_pauses_across_sessions_and_the_resume_ends_it():
    me = _new_user("cp615-resume@example.com")
    _save_mode(me, "until_resume", limit=2)
    old = datetime.now(timezone.utc) - timedelta(days=3)
    _store_losses(me, [old, old + timedelta(minutes=1)])
    status = client.get("/api/risk-settings/loss-streak", headers=me["headers"]).json()
    assert status["paused"] and status["can_resume"] and status["losses_in_a_row"] == 2 and status["limit"] == 2
    assert status["mode_label"] == "Pause until I resume" and status["ends"] == "when you resume" and status["exits_blocked"] is False
    assert status["resumed_at"] is None
    res = client.post("/api/risk-settings/loss-streak/resume", headers=me["headers"])
    assert res.status_code == 200, res.text
    after = res.json()
    assert not after["paused"] and after["losses_in_a_row"] == 0 and after["resumed_at"]
    assert _state(me, datetime.now(timezone.utc) + timedelta(seconds=1)).consecutive_losses == 0

    async def audited():
        async with _session_factory() as session:
            rows = list(await session.scalars(select(AuditLogRecord).where(AuditLogRecord.tenant_id == me["tenant_id"],
                                                                           AuditLogRecord.event == "loss_streak_resumed")))
            row = await session.scalar(select(RiskSettingsRecord).where(RiskSettingsRecord.tenant_id == me["tenant_id"]))
            return rows, row
    rows, row = _run(audited())
    assert len(rows) == 1 and "losses_in_a_row=2 limit=2" in rows[0].detail and row.loss_streak_resumed_at is not None
    # nothing to resume now: a second resume is refused and writes no second audit row
    assert client.post("/api/risk-settings/loss-streak/resume", headers=me["headers"]).status_code == 409
    assert len(_run(audited())[0]) == 1
    # a loss after the resume counts again
    _store_losses(me, [datetime.now(timezone.utc) + timedelta(seconds=2)])
    assert _state(me, datetime.now(timezone.utc) + timedelta(seconds=3)).consecutive_losses == 1


def test_resume_is_refused_under_next_session():
    me = _new_user("cp615-resume-next@example.com")
    _save_mode(me, "next_session", limit=1)
    _store_losses(me, [datetime.now(timezone.utc) - timedelta(seconds=30)])
    status = client.get("/api/risk-settings/loss-streak", headers=me["headers"]).json()
    if status["paused"]:        # a loss 30 seconds ago is in this session unless the IST day just rolled
        assert status["ends"] == "at the next session" and not status["can_resume"]
    res = client.post("/api/risk-settings/loss-streak/resume", headers=me["headers"])
    assert res.status_code == 409 and "next session" in res.json()["detail"]


def test_the_setting_round_trips_and_defaults_without_a_row():
    me = _new_user("cp615-setting@example.com")
    assert client.get("/api/risk-settings", headers=me["headers"]).json()["loss_streak_pause"] == "next_session"
    status = client.get("/api/risk-settings/loss-streak", headers=me["headers"]).json()
    assert status["mode"] == "next_session" and not status["paused"] and status["limit"] is None      # DEF-0: not set
    assert _save_mode(me, "until_resume")["loss_streak_pause"] == "until_resume"
    bad = {**client.get("/api/risk-settings", headers=me["headers"]).json(), "loss_streak_pause": "never"}
    assert client.put("/api/risk-settings", headers=me["headers"], json=bad).status_code == 422


def test_exits_are_never_blocked_while_new_entries_are_paused():
    """ADR-0004: with the pause active the risk engine refuses a new entry, and the position monitor still closes an open
    position at its stop - exits never pass through the risk engine."""
    from app.trading.position_monitor import monitor_open_positions
    me = _new_user("cp615-exits@example.com")
    _save_mode(me, "until_resume", limit=2)
    old = datetime.now(timezone.utc) - timedelta(days=2)
    _store_losses(me, [old, old + timedelta(minutes=1)])

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            now = datetime.now(timezone.utc)
            state = await build_trading_day_state(session, user)
            decision = RiskManager(await _tenant_cfg(session, me["tenant_id"])).validate_and_size(_signal(now), state)
            assert not decision.approved and any("consecutive losses" in r for r in decision.reasons)
            assert (await loss_streak_status(session, me["tenant_id"]))["paused"]
            open_trade = TradeRecord(tenant_id=me["tenant_id"], user_id=me["id"], mode="PAPER", symbol="OPEN1", strategy_id="s", direction="LONG",
                                     entry_time=now - timedelta(minutes=10), entry_price=100.0, quantity=1, stop_loss=98.0, target1=110.0)
            session.add(open_trade)
            await session.commit()

            async def price_lookup(symbol, exchange):
                return 97.0                                  # through the stop
            await monitor_open_positions(session, me["tenant_id"], price_lookup)
            await session.refresh(open_trade)
            return open_trade
    closed = _run(go())
    assert closed.exit_time is not None and closed.pnl is not None and closed.pnl < 0


# --- the Copilot's honest test (CP6-2) says it plainly -------------------------------------------------------------------

def test_the_copilot_answer_says_the_pause_held_to_the_end():
    from app.ai.copilot_chat import _pause_line
    held = _pause_line({"mode": "until_resume", "limit": 3, "pauses": 1, "first_paused_at": "2026-10-07T04:10:00+00:00", "held_to_end": True})
    assert held.startswith("After 3 losses in a row (on 7 Oct 2026), new entries paused and stayed paused to the end of the test")
    assert "nobody can resume a test" in held and "Pause until I resume" in held
    nxt = _pause_line({"mode": "next_session", "limit": 3, "pauses": 2, "first_paused_at": "2026-10-07T04:10:00+00:00", "held_to_end": False})
    assert nxt == "New entries paused 2 times after 3 losses in a row (first on 7 Oct 2026); each pause ended at the next session, as your setting says."


def test_the_honest_test_and_its_answer_say_the_pause_held_to_the_end():
    from app.ai import copilot_chat as chat
    from app.ai import draft_backtest as dbt
    from app.ai import strategy_builder as sb
    from app.ai.output_filter import findings
    from tests.test_copilot_v2_cp6_backtest import BOTH, _candles
    cfg = sb.config_of(sb.build(BOTH, "NIFTY", 1.5))
    risk = RiskConfig(capital=200000.0, risk_per_trade_pct=1.0, max_consecutive_losses=1, loss_streak_pause="until_resume")
    out = dbt.honest_test(cfg, _candles(), "NIFTY", "15min", risk, holdout=None)
    assert out["loss_streak"]["held_to_end"] and out["loss_streak"]["limit"] == 1
    ans = chat.Answer("strategy", "Strategy builder")
    chat._test_lines(ans, out)
    text = " ".join(ans.lines())
    assert "After 1 loss in a row" in text
    assert "stayed paused to the end of the test" in text and "nobody can resume a test" in text
    assert findings(text) == []
    # the default setting: each pause ended at the next session, said too
    out = dbt.honest_test(cfg, _candles(), "NIFTY", "15min", risk.model_copy(update={"loss_streak_pause": "next_session"}), holdout=None)
    ans = chat.Answer("strategy", "Strategy builder")
    chat._test_lines(ans, out)
    assert "each pause ended at the next session" in " ".join(ans.lines())


# --- the migration -----------------------------------------------------------------------------------------------------

def test_migration_is_additive_rollback_safe_reversible_and_the_only_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from scripts.migration_rollback_safety import lint, risky_operations

    root = Path(__file__).resolve().parents[1]
    path = root / "alembic" / "versions" / "b5d7f9a1c3e6_cp6_15_loss_streak_pause.py"
    spec = importlib.util.spec_from_file_location("cp6_15_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.ROLLBACK_SAFE is True and module.revision == "b5d7f9a1c3e6" and module.down_revision == "a4c6e8f0b2d4"
    source = path.read_text(encoding="utf-8")
    assert risky_operations(source) == [] and not hasattr(module, "ROLLBACK_SAFE_REASON")
    assert not [p for p in lint() if p.startswith(path.name)]
    assert "server_default='next_session'" in source and "sa.String(length=16)" in source and "DateTime(timezone=True), nullable=True" in source
    assert "drop_column('risk_settings', 'loss_streak_resumed_at')" in source and "drop_column('risk_settings', 'loss_streak_pause')" in source
    script = ScriptDirectory.from_config(Config(str(root / "alembic.ini")))
    heads = script.get_heads()
    # one head, with CP6-15 on its chain (LAB-18, the exits-as-settings and View-as-user migrations chain after it)
    assert len(heads) == 1 and "b5d7f9a1c3e6" in {r.revision for r in script.walk_revisions("base", heads[0])}
