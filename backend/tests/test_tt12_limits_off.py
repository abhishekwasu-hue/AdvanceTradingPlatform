"""TT-12 (owner decision, 24 Oct): every platform-default limit of risk by default (risk per trade 1 %, daily loss 2 %,
10 entries a day, minimum reward:risk 1.5) starts OFF for every user until he sets his own value. A value counts only
when the user set it himself (`_set` in the stored JSON; a row saved before TT-12 counts a value only when it differs
from the old default). Unset = off: no cap of his own, no lockout, no entries cap, no reward:risk filter. The "loosen
only from tomorrow" rule holds for values he set. Never applied to exits (ADR-0004): Exit now, a stop and Exit all close
whatever the limits. Parity: with nothing set the PAPER pipeline sizes exactly as the backtest engines' RiskManager does
(they never read these limits)."""
import ast
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.models import RiskConfig
from app.db.models import PaperAccountRecord, RiskLockoutRecord, TradeRecord, User
from app.platform.controls import clamp_config, risk_ceilings
from app.risk_engine import by_default
from app.risk_engine.risk_manager import RiskManager, TradingDayState
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import OPEN_NOW, _FakeBroker, _deploy, _get, _worker
from tests.test_z3_approval_entries import _price, _sig
from tests.test_z3b_paper_account import _paper_flag
from tests.test_z5_p1d_risk_by_default import _closed, _enter, _flag, _mine, _open, _org, _preview, _run, _tenant_t

TODAY = date(2026, 10, 24)
OLD = json.dumps({"risk_per_trade_pct": 1.0, "risk_per_trade_amount": 0.0, "daily_loss_pct": 2.0, "max_trades_per_day": 10, "min_rr": 1.5,
                  "max_sector_pct": 0.0, "max_correlated_risk_pct": 0.0, "max_open_positions": 0})


def _stored(t: dict) -> dict:
    async def go():
        async with _session_factory() as session:
            row = await session.scalar(select(PaperAccountRecord).where(PaperAccountRecord.user_id == t["user_id"]))
            return json.loads(row.risk_settings_json) if row is not None and row.risk_settings_json else {}
    return _run(go())


def _store(t: dict, text: str) -> None:
    """Writes a stored settings row as an earlier build left it (a row saved before TT-12)."""
    async def go():
        from app.paper.service import get_account
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            account = await get_account(session, user)
            account.risk_settings_json = text
            await session.commit()
    _run(go())


# --- reading what the user set ------------------------------------------------------------------------------------------

def test_nothing_stored_means_every_limit_off():
    s = by_default.settings_on(None, TODAY)
    assert s == by_default.RiskSettings.defaults()
    assert (s.risk_per_trade_pct, s.daily_loss_pct, s.max_trades_per_day, s.min_rr) == (0.0, 0.0, 0, 0.0)
    assert by_default.risk_cap(1_000_000, s) == {"amount": None, "binding": None, "by_pct": None}
    assert by_default.daily_loss_breached(1_000_000, s, [-900_000], 0.0)["breached"] is False
    assert by_default.set_on(None, TODAY) == []


def test_a_row_saved_before_tt12_keeps_only_the_values_that_differ_from_the_old_defaults():
    # Every key equal to the old platform default: he never chose them (a save wrote every key) - all off.
    assert by_default.settings_on(OLD, TODAY) == by_default.RiskSettings.defaults()
    assert by_default.set_on(OLD, TODAY) == []
    # He changed the daily loss to 3 % and the entries to 5: those are his and stay; the rest is off.
    mixed = json.dumps({**json.loads(OLD), "daily_loss_pct": 3.0, "max_trades_per_day": 5})
    s = by_default.settings_on(mixed, TODAY)
    assert (s.risk_per_trade_pct, s.daily_loss_pct, s.max_trades_per_day, s.min_rr) == (0.0, 3.0, 5, 0.0)
    assert by_default.set_on(mixed, TODAY) == ["daily_loss_pct", "max_trades_per_day"]
    # A loosening he asked for that waits for tomorrow is his too, even when it lands on an old default value.
    waiting = json.dumps({**json.loads(OLD), "max_trades_per_day": 4, "_pending": {"values": {"max_trades_per_day": 10}, "from": "2026-10-25"}})
    assert by_default.settings_on(waiting, TODAY).max_trades_per_day == 4
    assert by_default.settings_on(waiting, date(2026, 10, 25)).max_trades_per_day == 10
    assert "max_trades_per_day" in by_default.set_on(waiting, date(2026, 10, 25))


def test_an_explicit_marker_counts_a_value_equal_to_the_old_default():
    text = json.dumps({"min_rr": 1.5, "daily_loss_pct": 2.0, "_set": ["min_rr"]})
    s = by_default.settings_on(text, TODAY)
    assert s.min_rr == 1.5 and s.daily_loss_pct == 0.0                # 2.0 is stored but not his: off


# --- saving: off -> a value at once, the friction only for values he set ------------------------------------------------

def test_setting_a_limit_that_was_off_applies_at_once_and_is_marked_his():
    text, applied, deferred, _ = by_default.plan_change(None, {"risk_per_trade_pct": 2.0, "max_trades_per_day": 0, "min_rr": 1.2}, TODAY)
    assert applied == {"risk_per_trade_pct": 2.0, "max_trades_per_day": 0, "min_rr": 1.2} and deferred == {}
    stored = json.loads(text)
    assert stored["_set"] == ["max_trades_per_day", "min_rr", "risk_per_trade_pct"] and "daily_loss_pct" not in stored
    s = by_default.settings_on(text, TODAY)
    assert (s.risk_per_trade_pct, s.daily_loss_pct, s.max_trades_per_day, s.min_rr) == (2.0, 0.0, 0, 1.2)


def test_a_legacy_row_with_old_default_values_is_not_held_by_the_friction():
    # The old 2 % daily loss he never chose is off, so 5 % is a new limit: at once, not "from tomorrow".
    _, applied, deferred, _ = by_default.plan_change(OLD, {"daily_loss_pct": 5.0}, TODAY)
    assert applied == {"daily_loss_pct": 5.0} and deferred == {}


def test_loosening_or_clearing_a_value_he_set_waits_for_the_next_day():
    text, _, _, _ = by_default.plan_change(None, {"daily_loss_pct": 2.0, "min_rr": 2.0}, TODAY)
    text, applied, deferred, start = by_default.plan_change(text, {"daily_loss_pct": 4.0, "min_rr": None}, TODAY)
    assert applied == {} and deferred == {"daily_loss_pct": 4.0, "min_rr": None} and start == "2026-10-25"
    today = by_default.settings_on(text, TODAY)
    assert today.daily_loss_pct == 2.0 and today.min_rr == 2.0        # the day it protects keeps them
    tomorrow = by_default.settings_on(text, date(2026, 10, 25))
    assert tomorrow.daily_loss_pct == 4.0 and tomorrow.min_rr == 0.0
    assert by_default.set_on(text, date(2026, 10, 25)) == ["daily_loss_pct"]
    # A tightening applies at once and cancels the waiting loosening of the same key.
    text, applied, deferred, _ = by_default.plan_change(text, {"daily_loss_pct": 1.0}, TODAY)
    assert applied == {"daily_loss_pct": 1.0} and deferred == {"min_rr": None}
    # Clearing a key that is already off is nothing to wait for.
    _, applied, deferred, _ = by_default.plan_change(None, {"min_rr": None}, TODAY)
    assert applied == {"min_rr": None} and deferred == {}


def test_the_api_starts_off_clears_with_null_and_shows_what_is_set():
    t = _tenant_t("tt12-api")
    _flag(by_default.FLAG, t["tenant_id"])
    body = client.get("/api/paper/risk", headers=t["headers"]).json()
    assert body["set"] == [] and body["defaults"] == by_default.RiskSettings.defaults().as_dict()
    assert body["risk_cap"]["amount"] is None and body["today"]["max_entries"] == 0 and body["lockout"] is None
    out = client.put("/api/paper/risk", headers=t["headers"], json={"daily_loss_pct": 3.0}).json()
    assert out["set"] == ["daily_loss_pct"] and out["change"]["applied"] == {"daily_loss_pct": 3.0}
    out = client.put("/api/paper/risk", headers=t["headers"], json={"daily_loss_pct": None})
    assert out.status_code == 200 and out.json()["change"]["deferred"] == {"daily_loss_pct": None}
    assert out.json()["pending"]["values"] == {"daily_loss_pct": None}
    # A key not sent stays as it is; a value out of bounds is still refused.
    assert client.put("/api/paper/risk", headers=t["headers"], json={"min_rr": 2.0}).json()["set"] == ["daily_loss_pct", "min_rr"]
    assert client.put("/api/paper/risk", headers=t["headers"], json={"daily_loss_pct": 0}).status_code == 422
    assert _stored(t)["_set"] == ["daily_loss_pct", "min_rr"]


def test_an_existing_user_keeps_the_values_he_set_through_the_api():
    t = _tenant_t("tt12-keep")
    _flag(by_default.FLAG, t["tenant_id"])
    _store(t, json.dumps({**json.loads(OLD), "risk_per_trade_pct": 0.5}))
    body = client.get("/api/paper/risk", headers=t["headers"]).json()
    assert body["set"] == ["risk_per_trade_pct"] and body["settings"]["risk_per_trade_pct"] == 0.5
    assert body["settings"]["daily_loss_pct"] == 0.0 and body["settings"]["min_rr"] == 0.0 and body["settings"]["max_trades_per_day"] == 0


# --- unset = off on every PAPER entry path -------------------------------------------------------------------------------

def test_unset_means_no_reward_risk_filter_no_lockout_no_entries_cap_and_no_cap_of_his_own():
    t = _tenant_t("tt12-off")
    _org(t, risk_pct=2.0)                                         # the organisation's own risk per trade: 20,000
    _flag(by_default.FLAG, t["tenant_id"])
    low = _preview(t, target=100.5)                               # reward:risk 0.25 - no minimum nobody stated
    assert not any("below your minimum" in r for r in low["refusals"]) and low["min_reward_risk"] is None and low["can_place"]
    s = low["sizing"]
    assert s["user_cap"] is None and s["cap"] == 20_000.0 and s["binding"] == "the organisation's risk per trade"
    _closed(t, -45_000.0)                 # 4.5 % of 10 lakh: past the old 2 %, inside the organisation's own 5 %
    assert client.get("/api/paper/risk", headers=t["headers"]).json()["lockout"] is None
    _org(t, risk_pct=0.1)                                         # small entries: the organisation's open-risk rule stays out of it

    async def flat():                                             # each entry closed again: the organisation's open-positions rule stays out of it
        async with _session_factory() as session:
            for trade in await session.scalars(select(TradeRecord).where(TradeRecord.user_id == t["user_id"], TradeRecord.exit_time.is_(None))):
                trade.exit_time, trade.exit_price, trade.pnl, trade.exit_reason = datetime.now(timezone.utc), trade.entry_price, 1.0, "test"
            await session.commit()
    for _ in range(12):                                           # past the old 10 a day
        assert _enter(t["user_id"])[0] is True
        _run(flat())

    async def locks():
        async with _session_factory() as session:
            return list(await session.scalars(select(RiskLockoutRecord).where(RiskLockoutRecord.tenant_id == t["tenant_id"])))
    assert _run(locks()) == []


def test_his_own_values_apply_once_he_sets_them():
    t = _tenant_t("tt12-own")
    _org(t)
    _flag(by_default.FLAG, t["tenant_id"])
    _mine(t, max_trades_per_day=1)
    assert any("below your minimum 1.5" in r for r in _preview(t, target=101.0)["refusals"])
    assert _enter(t["user_id"])[0] is True
    executed, reasons, _ = _enter(t["user_id"])
    assert not executed and any("entries per day" in r for r in reasons)


# --- never applied to exits (ADR-0004) -------------------------------------------------------------------------------------

STRICT = {"risk_per_trade_pct": 0.05, "risk_per_trade_amount": 1.0, "daily_loss_pct": 0.1, "max_trades_per_day": 1, "min_rr": 10.0,
          "max_open_positions": 1}


def test_exit_now_a_stop_and_exit_all_pass_with_the_strictest_limits(monkeypatch):
    t = _tenant_t("tt12-exits")
    _org(t)
    _flag(by_default.FLAG, t["tenant_id"])
    _paper_flag(t["tenant_id"])
    _mine(t, **STRICT)
    _closed(t, -50_000.0)                                         # far past his 0.1 % daily loss: locked for new entries
    assert client.get("/api/paper/risk", headers=t["headers"]).json()["lockout"] is not None
    assert not _enter(t["user_id"])[0]

    # Exit now.
    first = _open(t)
    _price(monkeypatch, 101.0)
    out = client.post(f"/api/positions/{first}/exit-now", headers=t["headers"])
    assert out.status_code == 200, out.text
    assert out.json()["closed"][0]["exit_price"] == 101.0
    assert _get(TradeRecord, first).exit_time is not None

    # A stop hit in the worker cycle.
    dep_id = _deploy(t)
    second = _open(t, stop=95.0)

    async def attach():
        async with _session_factory() as session:
            trade = await session.get(TradeRecord, second)
            trade.deployment_id = dep_id
            await session.commit()
    _run(attach())
    report = _run(_worker(monkeypatch, _FakeBroker(ltp=94.0)).run_cycle(now=OPEN_NOW))
    assert report.positions_closed >= 1 and _get(TradeRecord, second).exit_reason == "Stop Loss"

    # Exit all (the kill switch's emergency exit).
    third = _open(t, symbol="TCS")
    out = client.post("/api/kill-switch/emergency-exit", headers=t["headers"], json={"reason": "tt12", "prices": {"TCS": 99.0}})
    assert out.status_code == 200, out.text
    assert third in out.json()["closed_trade_ids"]
    assert _get(TradeRecord, third).exit_reason == "Emergency Exit"
    client.post("/api/kill-switch/release", headers=t["headers"], json={"scope": "TENANT"})


def test_entry_refusals_name_entries_only():
    """The limits are read on entry paths only: the lockout and the entries cap text both say exits stay available."""
    assert "exits stay available" in by_default._per_day_text(1, 1)
    assert "exits stay available" in by_default.lockout_text(-1.0, 1.0, 0.1, 1000.0, datetime(2026, 10, 24, tzinfo=timezone.utc))


# --- parity --------------------------------------------------------------------------------------------------------------

BACKTEST_ENGINES = ("app/backtest/engine.py", "app/backtest/options_engine.py")


@pytest.mark.parametrize("path", BACKTEST_ENGINES)
def test_the_backtest_engines_never_read_risk_by_default(path):
    """Both backtest engines size through the organisation's RiskManager alone; risk by default is PAPER-entry only.
    So with nothing set, PAPER and the backtests agree (next test); a limit the user sets is a PAPER discipline rule."""
    tree = ast.parse((Path(__file__).resolve().parents[1] / path).read_text())
    names = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    modules = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert "by_default" not in names and not any("by_default" in m for m in modules)


def test_with_nothing_set_the_paper_pipeline_sizes_as_the_backtest_risk_manager():
    t = _tenant_t("tt12-parity")
    _org(t, capital=1_000_000.0, risk_pct=0.5)
    _flag(by_default.FLAG, t["tenant_id"])
    executed, reasons, qty = _enter(t["user_id"])
    assert executed and not any("your PAPER cap" in r for r in reasons)

    async def backtest_qty():
        async with _session_factory() as session:
            from app.risk_engine.routes import get_tenant_risk_config
            cfg, _ = clamp_config(await get_tenant_risk_config(t["tenant_id"], session) or RiskConfig(), await risk_ceilings(session))
            user = await session.get(User, t["user_id"])
            capped, notes = await by_default.apply_user_cap(session, user, cfg)
            assert capped is cfg and notes == []                  # no cap of his own: the organisation's config unchanged
            return RiskManager(cfg).validate_and_size(_sig(), TradingDayState()).quantity
    assert _run(backtest_qty()) == qty


def test_the_ticket_preview_and_the_pipeline_agree_on_his_limits():
    """One rule, several front doors: the ticket's refusals and the pipeline's for the same user and settings."""
    t = _tenant_t("tt12-doors")
    _org(t)
    _flag(by_default.FLAG, t["tenant_id"])
    assert not any("entries per day" in r for r in _preview(t)["refusals"]) and _enter(t["user_id"])[0]
    _mine(t, max_trades_per_day=1)
    assert any("entries per day" in r for r in _preview(t)["refusals"])
    assert not _enter(t["user_id"])[0]

    async def proposal():
        async with _session_factory() as session:
            return await by_default.proposal_refusal(session, t["tenant_id"], t["user_id"])
    assert "entries per day" in (_run(proposal()) or "")
