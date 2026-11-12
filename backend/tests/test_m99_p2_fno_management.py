"""M99 P2 follow-up: the trade-management options on a bot that reads the underlying and trades its future or a single
option of it (app/strategy_templates/management.py, exits.MANAGEMENT_CLASSES).

* read on the bars the bot READS (the underlying's structure): the side and the stop are the underlying's;
* a future: the new stop is the underlying stop transplanted by the entry's offset (contract_execution, the one mapping);
* a single option: the new stop is its underlying stop, and the option exits when the underlying crosses it
  (exit_logic.check_contract_exit, the one mapping) - its premium stop is never touched or made up;
* the options backtest engine = the worker's replay of the same `management_step`;
* exits are never blocked (never later than without the options; a failure leaves every other exit in place);
* the refused cases in plain words: a structure; the trail on a single option with the premium stop only.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from app.backtest.options_engine import OptionBacktestConfig, run_option_backtest
from app.core.models import RiskConfig
from app.execution.contract_execution import underlying_stop_fields, underlying_stop_view
from app.quality.lookahead import synthetic_bars
from app.strategy_templates import compile as C
from app.strategy_templates import exits as X
from app.strategy_templates import library
from app.strategy_templates import management as M
from app.strategy_templates.adapter import TemplateStrategy
from app.trading.exit_logic import check_contract_exit
from tests.test_m99_p2_intraday import _Market, _strategy

RISK = RiskConfig(capital=10_000_000, risk_per_trade_pct=2.0, min_risk_reward=0.5)
# A single bought option held past the session (no square-off) with a far premium stop, so the underlying exits are reached
SINGLE = OptionBacktestConfig(implied_volatility=0.14, premium_stop_pct=95.0, intraday=False)
SPOT = 240.0                                                          # synthetic bars scaled to an index's level


def _bars(seed, total=900, scale=SPOT):
    df = synthetic_bars(total, tf="15m", seed=seed)
    df[["open", "high", "low", "close"]] *= scale
    return df


def _h():
    t = library.get("H2")
    return TemplateStrategy(t, C.resolve_settings(t, "5m"))


# ============================================================================================ the one mapping per instrument
def test_the_stop_view_and_its_mapping_per_instrument():
    under = SimpleNamespace(instrument_kind="UNDERLYING", stop_loss=95.0, initial_stop_loss=90.0, underlying_stop_loss=None)
    fut = SimpleNamespace(instrument_kind="FUTURE", stop_loss=24_470.0, initial_stop_loss=24_470.0, underlying_stop_loss=24_400.0)
    opt = SimpleNamespace(instrument_kind="OPTION", stop_loss=70.0, initial_stop_loss=70.0, underlying_stop_loss=24_400.0)
    premium_only = SimpleNamespace(instrument_kind="OPTION", stop_loss=70.0, initial_stop_loss=70.0, underlying_stop_loss=None)
    assert underlying_stop_view(under) == 95.0 and underlying_stop_fields(under, 96.0) == {"stop_loss": 96.0}
    # a future: the entry's transplant offset (70) both ways
    assert underlying_stop_view(fut) == pytest.approx(24_400.0) and underlying_stop_fields(fut, 24_420.0) == {"stop_loss": 24_490.0}
    # a future whose own exit rules already tightened its stop on the future is seen tightened on the underlying too
    assert underlying_stop_view(replace_ns(fut, stop_loss=24_500.0)) == pytest.approx(24_430.0)
    # a single option: its underlying stop; the premium stop is never touched
    assert underlying_stop_view(opt) == 24_400.0 and underlying_stop_fields(opt, 24_420.0) == {"underlying_stop_loss": 24_420.0}
    assert underlying_stop_view(premium_only) is None
    # a future with no offset recorded carries nothing (no stop is guessed)
    assert underlying_stop_fields(replace_ns(fut, initial_stop_loss=None), 24_420.0) == {}


def replace_ns(ns, **kw):
    return SimpleNamespace(**{**vars(ns), **kw})


def test_no_underlying_stop_is_no_trail_but_the_bar_exits_still_run():
    m = M.Management(trail="MA", trail_n=5, trail_ma="SMA", max_bars=2)
    got = M.step(m, direction=1, entry=None, initial_stop=None, current_stop=None, highs=[1, 2], lows=[0, 1], closes=[1, 2], level=1.5)
    assert got.stop is None and got.exit_reason == "Bars in the trade (2)"


# ================================================================================================== the worker (DB)
def _contract_case(m: M.Management, *, kind, symbol, direction, stop, underlying_direction, underlying_stop, price=101.0,
                   leg_group_id=None, strat=None):
    from sqlalchemy import select

    from app.db.models import StrategyDeploymentRecord, TradeRecord
    from app.workers.trading_worker import TradingWorker
    from tests.test_auth_api import _session_factory
    from tests.test_trading_worker import _tenant

    df = _bars(3, scale=1.0).iloc[:420]
    bar_start = df.index[-1].to_pydatetime()
    a = _tenant(f"m99fno-{uuid.uuid4().hex[:6]}@example.com")
    strat = strat or _strategy(m=m)

    async def go():
        async with _session_factory() as session:
            dep = StrategyDeploymentRecord(holding="INTRADAY", order_style="MARKET", tenant_id=a["tenant_id"], strategy_id=strat.id,
                                           symbol="NIFTY 50", exchange="NSE", timeframe="15min", mode="PAPER", broker_name="upstox",
                                           status="ACTIVE", created_by=a["user_id"], management=m.to_json(), instrument_kind=kind)
            session.add(dep)
            await session.flush()
            row = TradeRecord(tenant_id=a["tenant_id"], user_id=a["user_id"], deployment_id=dep.id, symbol=symbol, strategy_id=dep.strategy_id,
                              direction=direction, mode="PAPER", entry_time=bar_start - timedelta(minutes=45), entry_price=100.0,
                              quantity=50.0, stop_loss=stop, initial_stop_loss=stop, target1=None, instrument_kind=kind,
                              underlying_symbol="NIFTY 50", underlying_direction=underlying_direction,
                              underlying_stop_loss=underlying_stop, leg_group_id=leg_group_id, exchange="NFO")
            session.add(row)
            await session.commit()
            market = _Market(price)
            closed = await TradingWorker(_session_factory)._template_manage(session, dep, strat, {"15min": df}, market, a["user_id"])
            await session.commit()
            got = await session.scalar(select(TradeRecord).where(TradeRecord.id == row.id))
            return closed, got, market.asked
    return asyncio.run(go()), df, strat


def _trailing_side(m):
    """The side (and a far stop) on which the trail moves on the fixture's last bar."""
    df = _bars(3, scale=1.0).iloc[:420]
    strat = _strategy(m=m)
    side, far = next((d, f) for d, f in (("LONG", 0.01), ("SHORT", 1e6))
                     if strat.management_step({"15min": df}, d, entry=None, initial_stop=f, current_stop=f, after=3).stop)
    return side, far, strat.management_step({"15min": df}, side, entry=None, initial_stop=far, current_stop=far, after=3)


def test_a_futures_bot_trails_on_the_underlying_and_moves_the_futures_stop_by_the_entry_offset():
    m = M.Management(trail="MA", trail_n=5, trail_ma="SMA")
    side, far, want = _trailing_side(m)
    offset = 35.0                                                       # the future's premium over the index at the entry
    (closed, row, _), _, _ = _contract_case(m, kind="FUTURE", symbol="NIFTY26NOVFUT", direction=side, stop=far + offset,
                                            underlying_direction=side, underlying_stop=far)
    assert closed == 0 and want.stop is not None and row.exit_time is None
    assert row.stop_loss == pytest.approx(want.stop + offset, abs=0.011)            # the transplant of the new underlying stop
    assert row.underlying_stop_loss == pytest.approx(far)                           # the entry's record stays (the offset)
    assert row.initial_stop_loss == pytest.approx(far + offset)


def test_a_futures_bot_exits_the_future_itself_on_a_management_exit():
    (closed, row, asked), _, _ = _contract_case(M.Management(max_bars=2), kind="FUTURE", symbol="NIFTY26NOVFUT", direction="LONG",
                                                stop=10.0, underlying_direction="LONG", underlying_stop=0.01, price=24_510.0)
    assert closed == 1 and row.exit_reason == "Bars in the trade (2)" and asked == ["NIFTY26NOVFUT"]
    assert row.exit_price == pytest.approx(24_510.0, rel=2e-3)


def test_an_options_bot_moves_its_underlying_stop_and_exits_when_the_underlying_crosses_it():
    m = M.Management(trail="MA", trail_n=5, trail_ma="SMA")
    side, far, want = _trailing_side(m)
    (closed, row, _), _, _ = _contract_case(m, kind="OPTION", symbol="NIFTY26N2024500CE", direction="LONG", stop=70.0,
                                            underlying_direction=side, underlying_stop=far)
    assert closed == 0 and row.exit_time is None
    assert row.underlying_stop_loss == pytest.approx(want.stop)                     # the stop on the underlying moved
    assert row.stop_loss == pytest.approx(70.0)                                     # the premium stop is untouched
    beyond = want.stop - 0.5 if side == "LONG" else want.stop + 0.5                 # crossed the new stop, not the old one
    assert check_contract_exit(row, 101.0, beyond) == ("Stop Loss (underlying)", 101.0)
    assert check_contract_exit(replace_ns(_ns(row), underlying_stop_loss=far), 101.0, beyond) is None


def _ns(row):
    keys = ("instrument_kind", "direction", "stop_loss", "underlying_direction", "underlying_stop_loss", "underlying_target1",
            "underlying_target2", "target1", "target2")
    return SimpleNamespace(**{k: getattr(row, k) for k in keys})


def test_an_option_with_the_premium_stop_only_keeps_it_and_still_exits_on_the_bar_count():
    m = M.Management(trail="MA", trail_n=5, trail_ma="SMA", max_bars=2)
    (closed, row, asked), _, _ = _contract_case(m, kind="OPTION", symbol="NIFTY26N2024500CE", direction="LONG", stop=70.0,
                                                underlying_direction="LONG", underlying_stop=None, price=88.0)
    assert closed == 1 and row.exit_reason == "Bars in the trade (2)" and asked == ["NIFTY26N2024500CE"]
    assert row.underlying_stop_loss is None and row.stop_loss == pytest.approx(70.0)


def test_a_structure_leg_is_never_touched_by_the_worker():
    (closed, row, asked), _, _ = _contract_case(M.Management(max_bars=1), kind="OPTION", symbol="NIFTY26N2024500PE", direction="SHORT",
                                                stop=0.0, underlying_direction="LONG", underlying_stop=0.01, leg_group_id="g-1")
    assert closed == 0 and row.exit_time is None and asked == []


def test_a_management_failure_leaves_the_contracts_own_exits_in_place():
    """Exits are never blocked: a failing management step on an option trade is logged, the trade is left as it was, and
    the position monitor's exit on its underlying stop still fires."""
    m = M.Management(max_bars=1)
    strat = _strategy(m=m)

    def boom(*a, **k):
        raise RuntimeError("bad bars")
    strat.management_step = boom                                     # type: ignore[method-assign]
    (closed, row, asked), _, _ = _contract_case(m, kind="OPTION", symbol="NIFTY26N2024500CE", direction="LONG", stop=70.0,
                                                underlying_direction="LONG", underlying_stop=24_400.0, strat=strat)
    assert closed == 0 and row.exit_time is None and row.underlying_stop_loss == pytest.approx(24_400.0) and asked == []
    assert check_contract_exit(row, 64.0, 24_399.0) == ("Premium floor", 64.0)
    assert check_contract_exit(row, 90.0, 24_399.0) == ("Stop Loss (underlying)", 90.0)


# ================================================================================================ the options backtest engine
def _structures(result):
    return list(result.options["structures"])


def test_all_off_is_the_options_backtest_exactly_as_before():
    df = _bars(3)
    before = run_option_backtest(_strategy(), df, "NIFTY 50", "15min", RISK, SINGLE)
    same = run_option_backtest(_strategy().use_exits(X.TemplateExits(management=None)), df, "NIFTY 50", "15min", RISK, SINGLE)
    assert _structures(before) and _structures(before) == _structures(same)


def test_the_options_backtest_equals_the_workers_replay_and_exits_on_the_trailed_underlying_stop():
    """Each single-option position replayed the worker's way on the underlying's bars: on every bar close after the entry
    the frames up to that bar, `after` = the bars whose close is after the entry time, the stop as the worker stores it
    (the option's underlying stop). No bar before the engine's exit touches the replayed stop; a management exit is the
    replay's bar and reason; an underlying stop exit is at a bar that touches the replayed (trailed) stop."""
    from app.backtest.windows import bar_length
    df = _bars(3, total=1400)
    m = M.Management(trail="SWING", trail_degree=0, stall_bars=4)
    result = run_option_backtest(_strategy(m=m), df, "NIFTY 50", "15min", RISK, SINGLE)
    pos = {ts: i for i, ts in enumerate(df.index)}
    lows, highs = df["low"].to_numpy(float), df["high"].to_numpy(float)
    length = bar_length("15min")
    worker = _strategy(m=m)
    checked = trailed_stop_exits = mgmt_exits = 0
    for st in _structures(result):
        if st["exit_reason"] == "End of backtest":
            continue
        e, x = pos[pd.Timestamp(st["entry_time"])], pos[pd.Timestamp(st["exit_time"])]
        signal = worker.analyze({"15min": df.iloc[:e + 1]}, "S")
        side, initial = signal.direction.value, float(signal.stop_loss)
        long = side == "LONG"
        stop, entered, predicted = initial, df.index[e] + length + pd.Timedelta(seconds=1), None
        for j in range(e + 1, x):
            assert not (lows[j] <= stop if long else highs[j] >= stop), (st["entry_time"], j)    # the engine would be out
            frame = df.iloc[:j + 1]
            after = int(((frame.index + length) > entered).sum())
            step = worker.management_step({"15min": frame}, side, entry=None, initial_stop=stop, current_stop=stop, after=after)
            if step.stop is not None:
                stop = step.stop
            if step.exit_reason:
                predicted = (j + 1, step.exit_reason)
                break
        if predicted is not None:
            assert x == predicted[0] and (st["exit_reason"] == predicted[1] or st["exit_reason"].startswith("Expiry")), st
            mgmt_exits += int(st["exit_reason"] == predicted[1])
        else:
            assert not st["exit_reason"].startswith(("No new", "Bars in")), st["exit_reason"]
            if st["exit_reason"] == "Stop Loss (underlying)":
                assert (lows[x] <= stop) if long else (highs[x] >= stop), st
                trailed_stop_exits += int(abs(stop - initial) > 1e-9)
        checked += 1
    assert checked >= 5 and mgmt_exits >= 1 and trailed_stop_exits >= 1, (checked, mgmt_exits, trailed_stop_exits)


@pytest.mark.parametrize("m", [M.Management(stall_bars=2), M.Management(max_bars=4), M.Management(trail="ATR", trail_n=14, trail_k=1.5),
                               M.Management(trail="MA", trail_n=10, trail_ma="EMA")], ids=["stall", "bars", "atr", "ma"])
def test_an_option_position_is_never_out_later_than_its_own_first_underlying_stop(m):
    df = _bars(3, total=1400)
    pos = {ts: i for i, ts in enumerate(df.index)}
    lows, highs = df["low"].to_numpy(float), df["high"].to_numpy(float)
    plain = _strategy()
    rows = [st for st in _structures(run_option_backtest(_strategy(m=m), df, "NIFTY 50", "15min", RISK, SINGLE))
            if st["exit_reason"] != "End of backtest"]
    assert len(rows) >= 3
    for st in rows:
        e, x = pos[pd.Timestamp(st["entry_time"])], pos[pd.Timestamp(st["exit_time"])]
        signal = plain.analyze({"15min": df.iloc[:e + 1]}, "S")
        first, long = float(signal.stop_loss), signal.direction.value == "LONG"
        hit = next((j for j in range(e + 1, len(df)) if (lows[j] <= first if long else highs[j] >= first)), None)
        assert hit is None or x <= hit, (st["entry_time"], st["exit_reason"], x, hit)


# ==================================================================================================== the refused cases
def test_the_refused_cases_in_plain_words():
    h = _h()
    caps = h.capabilities()
    stall, trail = M.Management(stall_bars=2).to_json(), M.Management(trail="SWING", trail_degree=0).to_json()
    # a multi-leg structure keeps its own credit stop / target
    assert X.TemplateExits(management=stall).problems(X.OPTION_STRUCTURE, caps) == [
        "A spread keeps its own stop and target on the credit; the trade-management options are for the symbol itself, "
        "a future or a single option"]
    with pytest.raises(ValueError):
        X.checked(h, X.TemplateExitsBody(management=X.ManagementBody(stall_bars=2)), "OPTION", "BULL_PUT_SPREAD")
    # a single option with the premium stop only has no underlying stop to trail; the bar exits fit
    premium = X.TemplateExits(stop_source="PREMIUM_PCT", management=trail).problems(X.OPTION_SINGLE, caps)
    assert premium == ["The trailing stop moves your stop on the underlying; with only your premium stop there is none to "
                       "move - pick the template's own level as the stop, or turn the trail off"]
    assert X.TemplateExits(stop_source="PREMIUM_PCT", management=stall).problems(X.OPTION_SINGLE, caps) == []
    # what fits: a future and a single option with the template's own level as the stop
    for cls_, kind in ((X.FUTURE, "FUTURE"), (X.OPTION_SINGLE, "OPTION")):
        assert X.TemplateExits(management=trail).problems(cls_, caps) == [], cls_
        assert X.checked(h, X.TemplateExitsBody(management=X.ManagementBody(stall_bars=2)), kind, "SINGLE").manage().stall_bars == 2


def test_the_form_is_offered_the_options_on_a_future_and_a_single_option():
    from app.strategy_templates.routes import _management_choices
    assert _management_choices()["classes"] == ["FUTURE", "OPTION_SINGLE", "UNDERLYING"]


def test_the_options_backtest_refuses_a_structure_before_the_run():
    from fastapi import HTTPException

    from app.backtest.routes import with_template_exits
    body = SimpleNamespace(template_exits=X.TemplateExitsBody(management=X.ManagementBody(stall_bars=2)),
                           options=SimpleNamespace(option_strategy="BULL_PUT_SPREAD"))
    with pytest.raises(HTTPException) as exc:
        with_template_exits(_h(), body)
    assert "A spread keeps its own stop" in str(exc.value.detail)
