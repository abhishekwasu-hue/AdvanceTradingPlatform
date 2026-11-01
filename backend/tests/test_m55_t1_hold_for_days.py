"""M55 T1 (owner order): defined-risk credit structures held for days (holding SWING, PAPER).

A bull put / bear call / iron condor / iron butterfly bot may be marked "Hold for days": it is not squared off at the
close (worker and option backtest), its margin is checked at entry, it exits by the user's own structure exits (CX / SX)
or settles on its expiry day. A naked single option written overnight, an undefined-risk or ratio structure held
overnight, and every intraday bot keep the rules they had.
"""
import asyncio
from datetime import UTC, date, datetime, time, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select

from app.backtest.options_engine import OptionBacktestConfig, run_option_backtest
from app.core.enums import OptionStrategy, SignalDirection
from app.core.models import RiskConfig
from app.db.models import TradeRecord, User
from app.execution.multileg import execute_structure
from app.instruments.spreads import can_hold_for_days, resolve_structure
from app.paper import fno_basket
from app.trading.position_monitor import monitor_open_positions
from tests.def0_legacy import legacy
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_contract_rules import TODAY, _load_master
from tests.test_deployments_api import _auth, _create, _store_broker
from tests.test_multileg import SPOT, _OptionBroker, _premium, _rules
from tests.test_phase_w_option_backtest import _OneShot
from tests.test_trading_worker import _deploy, _signal, _tenant
from tests.utils import make_series

E1 = date(2026, 10, 1)        # the nearest expiry for TODAY in the master fixture


def _run(coro):
    return asyncio.run(coro)


# --- the deployment API -----------------------------------------------------------------------------------------

def test_only_defined_risk_credit_structures_can_be_held_for_days():
    assert all(can_hold_for_days(s) for s in ("BULL_PUT_SPREAD", "BEAR_CALL_SPREAD", "IRON_CONDOR", "IRON_BUTTERFLY"))
    assert not any(can_hold_for_days(s) for s in ("SINGLE", "SHORT_STRADDLE", "SHORT_STRANGLE", "CALL_RATIO_SPREAD", "CUSTOM",
                                                   "LONG_STRADDLE", "CALENDAR_SPREAD", "nonsense"))


def test_deployment_api_hold_for_days_rules():
    _load_master()
    headers = _auth("m55-t1-api@example.com")
    _store_broker(headers, token_status="VALID")
    option = {"symbol": "NIFTY 50", "instrument_kind": "OPTION"}
    held = _create(headers, **option, option_strategy="BULL_PUT_SPREAD", holding="SWING", timeframe="1min")
    assert held.status_code == 201, held.text
    assert held.json()["holding"] == "SWING" and held.json()["timeframe"] == "1min"   # the user's own intraday timeframe
    # Still refused: a naked single written overnight, undefined-risk / ratio structures held overnight.
    naked = _create(headers, **option, option_position="WRITE", holding="SWING")
    assert naked.status_code == 400
    for structure in ("SHORT_STRADDLE", "SHORT_STRANGLE", "CALL_RATIO_SPREAD", "LONG_STRADDLE"):
        assert _create(headers, **option, option_strategy=structure, holding="SWING").status_code == 400, structure
    # An intraday strategy held on daily candles makes no sense; a long single overnight is unchanged (daily candles).
    assert _create(headers, **option, option_strategy="BULL_PUT_SPREAD", holding="SWING", timeframe="day").status_code == 400
    assert _create(headers, **option, option_position="BUY", holding="SWING").status_code == 400
    # A daily strategy may hold a credit structure too (on its daily candles).
    daily = _create(headers, **option, strategy_id="swing_breakout_d", option_strategy="IRON_CONDOR", holding="SWING", timeframe="day")
    assert daily.status_code == 201, daily.text
    for dep in (held, daily):
        assert client.post(f"/api/deployments/{dep.json()['id']}/stop", headers=headers, json={"reason": "test done"}).status_code == 200


# --- execution: margin at entry, the holding on every leg -------------------------------------------------------------

def _structure(strategy=OptionStrategy.BULL_PUT_SPREAD, direction=SignalDirection.LONG):
    async def go():
        async with _session_factory() as session:
            return await resolve_structure(session, "NIFTY 50", _rules(), strategy, direction, spread_width=2, spot=SPOT, today=TODAY)
    return _run(go())


def _execute(t, *, holding, key, strategy=OptionStrategy.BULL_PUT_SPREAD, stop=None, deployment_id=None):
    structure = _structure(strategy)

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            return await execute_structure(session, user, mode="PAPER", strategy_id="ema_rsi_scalper_1m", signal=_signal(),
                                           structure=structure, rules=_rules(), target_credit_pct=None, stop_credit_pct=stop,
                                           idempotency_key=key, quote_broker=_OptionBroker(), deployment_id=deployment_id,
                                           risk_config=legacy(capital=5_000_000, risk_per_trade_pct=1.0), holding=holding)
    return _run(go())


def _open(tenant_id):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_(None))
                                              .order_by(TradeRecord.id)))
    return _run(go())


def _all(tenant_id):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == tenant_id).order_by(TradeRecord.id)))
    return _run(go())


def test_a_held_bull_put_is_entered_with_its_margin_checked_and_every_leg_held():
    _load_master()
    t = _tenant("m55-t1-exec@example.com", with_credentials=False)
    result = _execute(t, holding="SWING", key="m55-held-1")
    assert result.executed, result.reasons
    assert any(r.startswith("Margin to hold for days") for r in result.reasons)
    assert any("Held for days" in r for r in result.reasons)
    legs = _open(t["tenant_id"])
    assert len(legs) == 2 and {leg.holding for leg in legs} == {"SWING"} and len({leg.leg_group_id for leg in legs}) == 1
    # The same structure without "Hold for days" stays intraday; an undefined-risk structure is never held, whatever is asked.
    t2 = _tenant("m55-t1-exec2@example.com", with_credentials=False)
    assert _execute(t2, holding="INTRADAY", key="m55-intra-1").executed
    assert {leg.holding for leg in _open(t2["tenant_id"])} == {"INTRADAY"}
    t3 = _tenant("m55-t1-exec3@example.com", with_credentials=False)
    straddle = _execute(t3, holding="SWING", key="m55-straddle-1", strategy=OptionStrategy.SHORT_STRADDLE, stop=100)
    assert straddle.executed, straddle.reasons
    assert {leg.holding for leg in _open(t3["tenant_id"])} == {"INTRADAY"}
    assert not any("Held for days" in r or "Margin to hold" in r for r in straddle.reasons)


def test_margin_refusal_at_entry(monkeypatch):
    """PAPER-NR (owner, 1 Nov): a short practice balance no longer refuses a PAPER entry - it is placed with the margin
    vs free balance line, below zero said so (was: refused "not enough margin to hold this spread for days")."""
    _load_master()
    t = _tenant("m55-t1-margin@example.com", with_credentials=False)

    async def small(session, user):
        return 1_000.0
    monkeypatch.setattr(fno_basket, "practice_balance", small)
    placed = _execute(t, holding="SWING", key="m55-margin-1")
    assert placed.executed, placed.reasons
    assert any(r.startswith("Practice margin: needs") and "below zero - practice only" in r for r in placed.reasons)
    assert not any("not enough margin" in r for r in placed.reasons)
    assert len(_all(t["tenant_id"])) == 2
    # The intraday bot is not asked for overnight margin (unchanged).
    assert _execute(t, holding="INTRADAY", key="m55-margin-2").executed


def test_margin_counts_the_other_held_spreads(monkeypatch):
    _load_master()
    t = _tenant("m55-t1-margin2@example.com", with_credentials=False)
    first = _execute(t, holding="SWING", key="m55-m2-1")
    assert first.executed
    need = next(r for r in first.reasons if r.startswith("Margin to hold for days"))
    required = float(need.split(":")[1].split("(")[0].replace(",", ""))

    async def balance(session, user):
        return required * 1.5          # room for one held spread, not for two
    monkeypatch.setattr(fno_basket, "practice_balance", balance)
    second = _execute(t, holding="SWING", key="m55-m2-2")
    # PAPER-NR: the other held spread's margin still counts; the shortfall is information, not a refusal.
    assert second.executed and any(r.startswith("Practice margin: needs") and "below zero - practice only" in r for r in second.reasons)


# --- the worker: no square-off, held across sessions, exits by its own rules, settles at expiry ------------------------

class _MD:
    async def get_ltp(self, symbol, exchange="NSE", now=None):
        return SPOT if symbol == "NIFTY 50" else _premium(symbol)


def _square_off(t, today):
    from app.workers.trading_worker import TradingWorker

    async def go():
        async with _session_factory() as session:
            worker = TradingWorker(_session_factory, cycle_seconds=60)
            return await worker._square_off_all(session, t["tenant_id"], _MD(), None, t["user_id"], today=today)
    return _run(go())


def test_worker_holds_the_spread_across_sessions_squares_off_intraday_and_settles_at_expiry():
    _load_master()
    t = _tenant("m55-t1-worker@example.com", with_credentials=False)
    dep = _deploy(t, status="STOPPED", symbol="NIFTY 50")
    assert _execute(t, holding="SWING", key="m55-w-held", deployment_id=dep).executed
    assert _execute(t, holding="INTRADAY", key="m55-w-intra", deployment_id=dep).executed
    held_group = next(leg.leg_group_id for leg in _open(t["tenant_id"]) if leg.holding == "SWING")
    # Session 1 close: the intraday spread is squared off at 15:15, the held one is kept.
    assert _square_off(t, TODAY) == 2
    assert {leg.leg_group_id for leg in _open(t["tenant_id"])} == {held_group}
    # Session 2 close (still before its expiry): held again.
    assert _square_off(t, TODAY + timedelta(days=1)) == 0
    assert len(_open(t["tenant_id"])) == 2
    # Its expiry day: it settles.
    assert _square_off(t, E1) == 2
    assert _open(t["tenant_id"]) == []
    held = [leg for leg in _all(t["tenant_id"]) if leg.leg_group_id == held_group]
    assert {leg.exit_reason for leg in held} == {"Expiry settlement"}
    others = [leg for leg in _all(t["tenant_id"]) if leg.leg_group_id != held_group]
    assert {leg.exit_reason for leg in others} == {"Intraday square-off"}


def test_a_held_single_swing_position_is_unchanged_on_any_day():
    """Long single options / cash held overnight: never closed by the square-off (not part of a held structure)."""
    t = _tenant("m55-t1-single@example.com", with_credentials=False)

    async def seed():
        async with _session_factory() as session:
            session.add(TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="PAPER", symbol="NIFTY26OCT24500CE", exchange="NFO",
                                    strategy_id="x", direction="LONG", entry_time=datetime.now(UTC), entry_price=100, quantity=75,
                                    stop_loss=0, holding="SWING", instrument_kind="OPTION", expiry=E1))
            await session.commit()
    _run(seed())
    assert _square_off(t, E1) == 0 and len(_open(t["tenant_id"])) == 1


def test_a_held_spread_exits_by_its_own_structure_stop_on_a_later_session():
    _load_master()
    t = _tenant("m55-t1-stop@example.com", with_credentials=False)
    assert _execute(t, holding="SWING", key="m55-s-held", stop=100).executed
    assert _square_off(t, TODAY) == 0                       # kept overnight
    legs = _open(t["tenant_id"])
    short = next(leg for leg in legs if leg.leg_role == "SHORT")
    long_ = next(leg for leg in legs if leg.leg_role == "LONG")

    async def prices(symbol, exchange):
        if symbol == short.symbol:
            return short.entry_price * 3                    # the spread's value is far past his own stop
        if symbol == long_.symbol:
            return long_.entry_price
        return SPOT

    async def go():
        async with _session_factory() as session:
            return await monitor_open_positions(session, t["tenant_id"], prices, now=datetime.now(UTC))
    outcomes = _run(go())
    assert sum(1 for o in outcomes if o.closed) == 2
    assert all("Spread stop" in (leg.exit_reason or "") for leg in _all(t["tenant_id"]))


# --- the option backtest: the held spread is not squared off; others still are; no look-ahead ---------------------------

def _two_sessions():
    monday = make_series(list(np.linspace(24500, 24510, 375)), start="2026-10-05 09:15")      # Monday, the whole session
    tuesday = make_series(list(np.linspace(24510, 24505, 370)), start="2026-10-06 09:15")     # Tuesday = NIFTY expiry
    return pd.concat([monday, tuesday])


def _bt(frame, **kw):
    # M68 PR-3 (D1): a user who saved "new entries until 15:00" and "square-off 15:15" (none is ATP's own any more)
    kw = {"entries_until": time(15, 0), "square_off_at": time(15, 15), **kw}
    cfg = OptionBacktestConfig(option_strategy=OptionStrategy.BULL_PUT_SPREAD, implied_volatility=0.14, max_lots=1, **kw)
    return run_option_backtest(_OneShot(), frame, "NIFTY 50", "1min", RiskConfig(capital=5_000_000, risk_per_trade_pct=1.0), cfg)


def test_backtest_holds_the_spread_overnight_and_squares_off_the_rest():
    frame = _two_sessions()
    held = _bt(frame, hold_for_days=True)
    trade = held.trades[0]
    assert trade.exit_reason.startswith("Expiry settlement") and held.options["expiry_settlements"] == 1
    assert pd.Timestamp(trade.exit_time).date() == date(2026, 10, 6) and held.options.get("held_for_days") is True
    intraday = _bt(frame)
    assert intraday.trades[0].exit_reason.startswith("Square-off") and "held_for_days" not in intraday.options
    # "Hold for days" means nothing on a structure that may not be held: the square-off stays.
    cfg = OptionBacktestConfig(option_strategy=OptionStrategy.SHORT_STRADDLE, implied_volatility=0.14, max_lots=1, stop_credit_pct=100,
                               hold_for_days=True, entries_until=time(15, 0), square_off_at=time(15, 15))
    straddle = run_option_backtest(_OneShot(), frame, "NIFTY 50", "1min", RiskConfig(capital=5_000_000, risk_per_trade_pct=1.0), cfg)
    assert straddle.trades, straddle.options["signals_skipped"]
    assert straddle.trades[0].exit_reason.startswith("Square-off")


def test_backtest_hold_for_days_has_no_look_ahead():
    """The entry of the held spread (time, legs, premiums) is the same whether or not the next session's bars exist."""
    frame = _two_sessions()
    monday_only = frame[frame.index < pd.Timestamp("2026-10-06", tz=frame.index.tz)]
    full, cut = _bt(frame, hold_for_days=True), _bt(monday_only, hold_for_days=True)
    a, b = full.options["structures"][0], cut.options["structures"][0]
    assert [(leg["strike"], leg["right"], leg["entry_price"]) for leg in a["legs"]] == \
           [(leg["strike"], leg["right"], leg["entry_price"]) for leg in b["legs"]]
    assert full.trades[0].entry_time == cut.trades[0].entry_time
    # On Monday's bars alone nothing squares it off at 15:15.
    assert not cut.trades[0].exit_reason.startswith("Square-off")
