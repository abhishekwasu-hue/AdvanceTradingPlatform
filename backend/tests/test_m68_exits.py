"""M68 PR-3 (owner order M68, TS1-026 to 034, TS1-097; owner decision D1 on its provisional default M68Q-1): Exits.

ATP adds no intraday cut-off, no square-off, no incomplete-order timer and no ceiling of its own; each is the user's own
Trade settings value (app/execution/exit_settings.py). Exits are never blocked (ADR-0004): every exit path - Exit now,
the stop, the target, a group exit, the kill switch's emergency exit - still closes a position for a user who saved
nothing at all. These tests read the real, empty settings (marker `real_exit_settings`).
"""
import asyncio
import uuid
from datetime import datetime, time, timedelta, timezone

import pytest
from app.db.models import TradeRecord, User
from app.execution import exit_settings as xs
from app.execution import group_exits as gx
from app.execution import incomplete_structures as inc
from app.execution.smart_execution import SmartSettings
from app.market_data.calendar import IST
from tests.test_auth_api import _register, _session_factory, client

pytestmark = pytest.mark.real_exit_settings
UTC = timezone.utc


def _run(coro):
    return asyncio.run(coro)


def _user(tag: str) -> dict:
    token = _register(f"m68x-{tag}-{uuid.uuid4().hex[:6]}@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/auth/me", headers=headers).json()
    return {"headers": headers, "id": me["id"], "tenant_id": me["tenant_id"]}


def _trade(u: dict, **kw) -> int:
    async def go():
        async with _session_factory() as session:
            row = TradeRecord(tenant_id=u["tenant_id"], user_id=u["id"], mode="PAPER", symbol=kw.pop("symbol", "INFY"),
                              strategy_id="x", direction="LONG", entry_time=datetime.now(UTC) - timedelta(minutes=30),
                              entry_price=100.0, quantity=10, stop_loss=kw.pop("stop", 95.0), target1=kw.pop("target", 110.0),
                              exchange="NSE", holding=kw.pop("holding", "INTRADAY"), **kw)
            session.add(row)
            await session.commit()
            return row.id
    return _run(go())


def _get(trade_id: int) -> TradeRecord:
    async def go():
        async with _session_factory() as session:
            return await session.get(TradeRecord, trade_id)
    return _run(go())


def _save(u: dict, values: dict) -> None:
    got = client.put("/api/me/trade-settings", headers=u["headers"], json={"values": values})
    assert got.status_code == 200, got.text


def _ist(h: int, m: int) -> datetime:
    today = datetime.now(IST).date()
    return datetime(today.year, today.month, today.day, h, m, tzinfo=IST)


# --- the settings ---------------------------------------------------------------------------------------------------------

def test_not_set_by_default_and_read_from_trade_settings():
    s = xs.ExitSettings()
    assert all(v is None for v in vars(s).values())
    assert s.square_off_at("NSE") is None and s.entry_window_reason("NSE", time(15, 20)) is None
    s = xs.from_values({"exits.square_off_time": "15:10", "automation.window_start": "09:30", "automation.window_end": "14:45",
                        "exits.incomplete_legs_action": "KEEP", "exits.incomplete_legs_answer_minutes": 3,
                        "exits.combined_measure": "SPOT", "exits.combined_check_mode": "CLOSE",
                        "exits.close_if_broker_stop_rejected": True})
    assert s.square_off_at("MCX") == time(15, 10) and s.entries_from("NSE") == time(9, 30) and s.entries_until("NSE") == time(14, 45)
    assert (s.incomplete_legs_action, s.incomplete_legs_answer_minutes) == (xs.KEEP, 3)
    assert (s.combined_measure, s.combined_check_mode, s.close_if_broker_stop_rejected) == ("SPOT", "CLOSE", True)
    assert s.entry_window_reason("NSE", time(9, 0)) == "No new entries before 09:30 IST (your setting)"
    assert s.entry_window_reason("NSE", time(14, 45)) == "No new entries after 14:45 IST (your setting)"
    assert s.entry_window_reason("NSE", time(12, 0)) is None


def test_in_window_each_end_optional_and_across_midnight():
    assert xs.in_window(None, None, time(3, 0))
    assert xs.in_window(time(9, 30), None, time(9, 30)) and not xs.in_window(time(9, 30), None, time(9, 29))
    assert xs.in_window(None, time(15, 0), time(14, 59)) and not xs.in_window(None, time(15, 0), time(15, 0))
    assert xs.in_window(time(22, 0), time(2, 0), time(23, 0)) and xs.in_window(time(22, 0), time(2, 0), time(1, 0))
    assert not xs.in_window(time(22, 0), time(2, 0), time(12, 0))


def test_the_settings_are_each_users_own():
    a, b = _user("own-a"), _user("own-b")
    _save(a, {"exits.square_off_time": "15:05", "automation.window_end": "14:30"})

    async def go():
        async with _session_factory() as session:
            return (await xs.load(session, tenant_id=a["tenant_id"], user_id=a["id"]),
                    await xs.load(session, tenant_id=b["tenant_id"], user_id=b["id"]))
    mine, theirs = _run(go())
    assert mine.square_off_time == time(15, 5) and mine.window_end == time(14, 30)
    assert theirs == xs.ExitSettings()


# --- TS1-026 (D1): the worker's square-off ------------------------------------------------------------------------------

class _MD:
    async def get_ltp(self, symbol, exchange="NSE", now=None):
        return 101.0


def _square_off(u: dict, at: datetime) -> int:
    from app.workers.trading_worker import TradingWorker

    async def go():
        async with _session_factory() as session:
            worker = TradingWorker(_session_factory, cycle_seconds=60)
            return await worker._square_off_all(session, u["tenant_id"], _MD(), None, u["id"], families={"NSE"},
                                                today=at.date(), settle_families={"NSE"}, now_ist=at)
    return _run(go())


def test_no_square_off_by_atp_when_not_set_d1():
    u = _user("sq-none")
    tid = _trade(u)
    assert _square_off(u, _ist(15, 20)) == 0          # was squared off at 15:15 before M68 D1
    assert _get(tid).exit_time is None


def test_his_own_square_off_time_closes_his_intraday_positions_only():
    u, other = _user("sq-own"), _user("sq-other")
    _save(u, {"exits.square_off_time": "14:50"})
    mine, swing, theirs = _trade(u), _trade(u, symbol="TCS", holding="SWING"), _trade(other)
    assert _square_off(u, _ist(14, 40)) == 0           # not yet his time
    assert _square_off(u, _ist(14, 50)) == 1
    row = _get(mine)
    assert row.exit_time is not None and row.exit_reason == "Intraday square-off"
    assert _get(swing).exit_time is None               # Phase AS: a swing position is held
    assert _square_off(other, _ist(15, 25)) == 0 and _get(theirs).exit_time is None


# --- TS1-026 / 028: the entry window ----------------------------------------------------------------------------------

def test_scan_auto_paper_window_is_the_owners_own(monkeypatch):
    from app.core import config as app_config
    from app.market_data import calendar
    from app.screener.then import auto_paper as AP

    class _Open:
        is_open, reason, next_open = True, "open", None

    async def open_now(session, now=None, exchange=None):
        return _Open()
    monkeypatch.setattr(calendar, "market_session_status", open_now)
    monkeypatch.setattr(app_config, "SCAN_AUTO_PAPER_LAST_ENTRY", "")
    u = _user("window")

    async def why(at):
        async with _session_factory() as session:
            owner = await session.get(User, u["id"])
            return await AP.window_reason(session, at.astimezone(UTC), "NSE", owner)
    assert _run(why(_ist(15, 20))) is None             # no 15:00 cut-off of ATP's own any more
    _save(u, {"automation.window_start": "09:45", "automation.window_end": "14:00"})
    assert "09:45" in _run(why(_ist(9, 30))) and "14:00" in _run(why(_ist(14, 0))) and _run(why(_ist(11, 0))) is None


# --- TS1-027: backtests = live -------------------------------------------------------------------------------------------

def test_backtest_runner_uses_the_users_own_times():
    from app.backtest.routes import user_cutoffs
    u = _user("bt")

    async def cutoffs():
        async with _session_factory() as session:
            return await user_cutoffs(session, await session.get(User, u["id"]), "NSE")
    assert _run(cutoffs()) == (None, None, None)
    _save(u, {"automation.window_end": "14:30", "exits.square_off_time": "15:00"})
    assert _run(cutoffs()) == (None, time(14, 30), time(15, 0))


# --- TS1-030: an incomplete multi-leg order with no rule of his ---------------------------------------------------------

def test_incomplete_order_has_no_timer_when_not_set():
    none = SmartSettings(settlement_days_before=4, settlement_band_pct=2.0, settlement_telegram=True, protection_band_pct=None)
    assert not none.has_rule
    block = inc.new_block(user_id=1, mode="PAPER", strategy_id="s", deployment_id=None, account_id=None, idempotency_key="k",
                          pending=[], leg_meta={}, failure="a leg did not fill", settings=none, premiums={}, debit=False,
                          stop_credit_pct=None)
    assert block["deadline"] is None and block["on_no_answer"] is None and block["answer_minutes"] is None
    assert inc.NO_RULE in block["message"]
    assert inc.rule_text(None, None) == inc.NO_RULE
    own = SmartSettings(settlement_days_before=4, settlement_band_pct=2.0, settlement_telegram=True, protection_band_pct=None,
                        on_no_answer=inc.KEEP, answer_minutes=7)
    timed = inc.new_block(user_id=1, mode="PAPER", strategy_id="s", deployment_id=None, account_id=None, idempotency_key="k",
                          pending=[], leg_meta={}, failure="x", settings=own, premiums={}, debit=False, stop_credit_pct=None,
                          now=datetime(2026, 1, 5, 5, 0, tzinfo=UTC))
    assert timed["deadline"] == datetime(2026, 1, 5, 5, 7, tzinfo=UTC).isoformat() and timed["on_no_answer"] == inc.KEEP


def test_smart_settings_read_trade_settings_first():
    from app.execution import smart_execution
    u = _user("smart")

    async def read():
        async with _session_factory() as session:
            return await smart_execution.settings_for(session, await session.get(User, u["id"]))
    s = _run(read())
    assert s.on_no_answer is None and s.answer_minutes is None
    _save(u, {"exits.incomplete_legs_action": "CLOSE_FILLED", "exits.incomplete_legs_answer_minutes": 4})
    s = _run(read())
    assert (s.on_no_answer, s.answer_minutes) == (inc.CLOSE_FILLED, 4)


# --- TS1-031: the combined exit's check ------------------------------------------------------------------------------------

def test_combined_exit_check_is_his_own():
    ask = lambda measure, check=None: gx.GroupExitAsk(client_ref="r", trade_ids=[1], measure=measure, check_mode=check,
                                                    spot_symbol="NIFTY 50", spot_exchange="NSE", sl_value=1.0)
    assert gx.resolve_check_mode(ask(gx.MTM_INR), None).check_mode == gx.TOUCH        # the only check a ₹ exit has
    spot = gx.resolve_check_mode(ask(gx.SPOT), None)
    assert spot.check_mode is None and gx.CHECK_NEEDED in gx.ask_errors(spot, [])
    assert gx.resolve_check_mode(ask(gx.SPOT), "CLOSE").check_mode == gx.CLOSE           # his saved choice
    assert gx.resolve_check_mode(ask(gx.SPOT, gx.TOUCH), "CLOSE").check_mode == gx.TOUCH  # the order's own wins


# --- TS1-032: close a position whose broker stop is refused --------------------------------------------------------------

def test_close_if_stop_rejected_is_off_unless_he_or_the_operator_turns_it_on(monkeypatch):
    from app.core import config
    monkeypatch.setattr(config, "LIVE_EXIT_IF_NO_STOP", False)
    u = _user("stopsw")

    async def on():
        async with _session_factory() as session:
            return await xs.close_if_stop_rejected(session, tenant_id=u["tenant_id"], user_id=u["id"])
    assert _run(on()) is False
    _save(u, {"exits.close_if_broker_stop_rejected": True})
    assert _run(on()) is True
    _save(u, {"exits.close_if_broker_stop_rejected": None})
    monkeypatch.setattr(config, "LIVE_EXIT_IF_NO_STOP", True)
    assert _run(on()) is True


# --- TS1-033 / 034: no ATP lead and no ATP ceilings -------------------------------------------------------------------

def test_no_atp_exit_ceilings():
    from app.deployments.routes import ExitRulesRequest
    from app.me.prefs import ExitDefaults
    for model in (ExitRulesRequest, ExitDefaults):
        rules = model(trailing_stop_pct=75, break_even_at_r=25, time_exit_minutes=3 * 24 * 60)
        assert (rules.trailing_stop_pct, rules.break_even_at_r, rules.time_exit_minutes) == (75, 25, 4320)
        for bad in ({"trailing_stop_pct": 100}, {"break_even_at_r": 0}, {"time_exit_minutes": 0}):
            with pytest.raises(ValueError):
                model(**bad)


def test_ticket_time_stop_only_later_than_now():
    from app.core import config
    from app.execution import approval
    assert not hasattr(config, "TICKET_TIME_STOP_MIN_LEAD_MINUTES")
    now = _ist(11, 0).astimezone(UTC)
    evidence = lambda at: {"ticket": True, "exit_rules": f'{{"time_exit_at": "{at}"}}'}
    assert approval.ticket_time_stop_passed(evidence("11:01"), now) is None            # was refused: inside the 5-minute lead
    assert "no longer later than now" in approval.ticket_time_stop_passed(evidence("11:00"), now)


# --- ADR-0004: every exit path closes with no saved setting ---------------------------------------------------------------

def test_exit_now_never_refuses_for_a_missing_price(monkeypatch):
    from app.ai import worker_bridge

    async def no_lookup(session, who):
        return None
    monkeypatch.setattr(worker_bridge, "price_lookup_for_user", no_lookup)
    u = _user("exitnow")
    tid = _trade(u)
    from app.execution import approval

    async def go():
        async with _session_factory() as session:
            trade, user = await session.get(TradeRecord, tid), await session.get(User, u["id"])
            return await approval.exit_now(session, trade, user)
    outcomes = _run(go())
    assert outcomes[0].closed and _get(tid).exit_price == 100.0
    assert _get(tid).exit_reason == "Exit now (web, entry price (no price was seen))"


def test_stop_and_target_close_with_no_settings():
    from app.trading.position_monitor import monitor_open_positions
    u = _user("stoptgt")
    stop, target = _trade(u, symbol="SBIN"), _trade(u, symbol="ITC")
    prices = {"SBIN": 94.0, "ITC": 111.0}

    async def lookup(symbol, exchange="NSE"):
        return prices[symbol]

    async def go():
        async with _session_factory() as session:
            return await monitor_open_positions(session, u["tenant_id"], lookup, user_id=u["id"], families={"NSE"})
    outcomes = _run(go())
    assert sum(1 for o in outcomes if o.closed) == 2
    assert _get(stop).exit_time is not None and _get(target).exit_time is not None


def test_group_exit_and_emergency_exit_close_with_no_settings(monkeypatch):
    from app.ai import worker_bridge
    from app.paper import fno_groups

    async def no_lookup(session, who):
        return None
    monkeypatch.setattr(worker_bridge, "price_lookup_for_user", no_lookup)
    u = _user("groupx")
    leg = _trade(u, symbol="NIFTY 25000 CE")

    async def group():
        async with _session_factory() as session:
            return await fno_groups.exit_group(session, await session.get(User, u["id"]), leg)
    out = _run(group())
    assert out["status"] == "CLOSED" and _get(leg).exit_time is not None     # no quote at all: closed, not refused

    other = _trade(u, symbol="EXITME")
    got = client.post("/api/kill-switch/emergency-exit", headers=u["headers"], json={"reason": "m68", "prices": {"EXITME": 101.5}})
    assert got.status_code == 200 and other in got.json()["closed_trade_ids"]
    assert _get(other).exit_reason == "Emergency Exit"


def test_square_off_fixture_is_not_applied_here():
    """The marker makes these tests read the real, empty settings (the suite's other tests run as a user who saved the
    pre-M68 times - tests/conftest.py)."""
    async def go():
        async with _session_factory() as session:
            return await xs.load(session)
    assert type(_run(go())) is xs.ExitSettings

