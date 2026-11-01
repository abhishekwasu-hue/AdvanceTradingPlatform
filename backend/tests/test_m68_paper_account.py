"""M68 PR-7 (owner order M68, TS1-064 .. 071, owner decision D4 - provisional default M68Q-4): the Paper account -
no pre-chosen starting capital, brokerage or slippage.

Capital: Not set => a new paper account has none, its balance / equity / drawdown % need it, and a new paper entry places
nothing ("Needs your setting: Starting capital") until he sets it (at the ticket, the account, or Trade settings); any
amount above 0 (no ATP floor or ceiling); an account made before M68 keeps its stored capital (migration, TS-6). Charges:
Not set => statutory charges only - no brokerage, no slippage - labelled so; his own brokerage / slippage are used for the
paper fill, the close, the cost lines, the Options Builder and backtests (parity). Exits are never blocked (ADR-0004).
"""
import asyncio
import importlib.util
import json
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.backtest.engine import run_backtest
from app.core.enums import SignalDirection, SignalGrade
from app.core.models import RiskConfig, Signal
from app.db.models import PaperAccountRecord, TradeRecord, User
from app.execution import india_costs
from app.execution.paper_broker import PaperBroker
from app.execution.paper_charges import STATUTORY_ONLY, PaperCharges, charges_from
from app.execution.signal_execution import execute_signal_for_user
from app.me import trade_settings as ts
from app.paper import paper_settings, service
from app.risk_engine import by_default
from app.trading.position_monitor import close_position
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import _tenant
from tests.test_z3b_paper_account import _paper_flag

BACKEND = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.real_paper_account      # M68 PR-7: each user's own (empty) Paper account settings
URL = "/api/me/trade-settings"
NEEDS = paper_settings.needs_capital()


def _run(coro):
    return asyncio.run(coro)


def _user_tenant(tag: str, *, flag: bool = True) -> dict:
    t = _tenant(f"m68p7-{tag}-{uuid.uuid4().hex[:6]}@example.com")
    if flag:
        _paper_flag(t["tenant_id"])
    return t


def _save(t: dict, **values) -> None:
    out = client.put(URL, headers=t["headers"], json={"values": {f"paper_account.{k}": v for k, v in values.items()}})
    assert out.status_code == 200, out.text


def _with_user(t: dict, fn):
    async def go():
        async with _session_factory() as session:
            return await fn(session, await session.get(User, t["user_id"]))
    return _run(go())


def _signal(entry: float = 100.0) -> Signal:
    return Signal(symbol="RELIANCE", strategy_id="m68p7", strategy_name="m68p7", timestamp=datetime.now(UTC),
                  direction=SignalDirection.LONG, entry=entry, stop_loss=entry * 0.98,
                  target1=entry * 1.04, grade=SignalGrade.A1, confidence=0.8, reasons=[])


def _execute(t: dict):
    async def go(session, user):
        return await execute_signal_for_user(session, user, mode="PAPER", strategy_id="m68p7", signal=_signal(),
                                             idempotency_key=f"m68p7:{uuid.uuid4().hex}", max_quantity=1)
    return _with_user(t, go)


# --- the registry: every Paper account field is wired and Not set for a new user -----------------------------------------

def test_every_paper_account_field_is_wired_and_not_set_for_a_new_user():
    t = _user_tenant("reg")
    view = client.get(URL, headers=t["headers"]).json()
    section = next(s for s in view["sections"] if s["id"] == "paper_account")
    fields = {f["key"]: f for f in section["fields"]}
    for name in ("capital", "charges_model", "brokerage_per_order", "slippage_pct"):
        f = fields[f"paper_account.{name}"]
        assert f["wired"] is True and f["value"] is None and f["saved"] is False
    assert fields["paper_account.statutory_charges"]["fact"] is True
    for f in ts.FIELDS:
        if f.section == "paper_account" and not f.fact:
            assert f.rows and f.pr == 7


def test_the_capital_has_no_floor_or_ceiling_of_atps_only_above_zero():
    t = _user_tenant("bounds")
    for amount in (5_000.0, 500_000_000.0):                    # below the old 10,000 floor, above the old 10 crore cap
        _save(t, capital=amount)
    bad = client.put(URL, headers=t["headers"], json={"values": {"paper_account.capital": 0}})
    assert bad.status_code == 422


# --- the account: Not set until he chooses; his saved default starts a new account ------------------------------------------

def test_a_new_account_has_no_capital_and_the_figures_that_need_it_say_so():
    t = _user_tenant("new")
    body = client.get("/api/paper/account", headers=t["headers"]).json()
    assert body["starting_capital"] is None and body["balance"] is None and body["equity"] is None
    assert body["capital_needed"] is True and body["capital_saved"] is False and body["max_drawdown_pct"] is None
    assert body["charges_model"]["words"] == "Statutory charges only: no brokerage, no slippage"
    ladder = client.get("/api/paper/ladder", headers=t["headers"]).json()
    dd = next(c for c in ladder["criteria"] if c["key"] == "max_drawdown_pct")
    assert dd["value"] is None and dd["met"] is False


def test_his_saved_capital_starts_a_new_account_and_an_unset_account_takes_it():
    t = _user_tenant("saved")
    _save(t, capital=250_000)
    body = client.get("/api/paper/account", headers=t["headers"]).json()
    assert body["starting_capital"] == 250_000.0 and body["balance"] == 250_000.0 and body["capital_saved"] is True
    u = _user_tenant("later")
    assert client.get("/api/paper/account", headers=u["headers"]).json()["starting_capital"] is None
    _save(u, capital=40_000)
    assert client.get("/api/paper/account", headers=u["headers"]).json()["starting_capital"] == 40_000.0


def test_setting_the_capital_any_amount_above_zero_and_the_lock_after_a_trade():
    t = _user_tenant("put")
    assert client.put("/api/paper/account", headers=t["headers"], json={"starting_capital": 5_000}).json()["starting_capital"] == 5_000.0
    out = client.put("/api/paper/account", headers=t["headers"], json={"starting_capital": 3_000_000_000})
    assert out.status_code == 200 and out.json()["starting_capital"] == 3_000_000_000.0 and out.json()["capital_saved"] is True
    assert client.put("/api/paper/account", headers=t["headers"], json={"starting_capital": 0}).status_code == 422

    async def trade(session, user):
        session.add(TradeRecord(tenant_id=user.tenant_id, user_id=user.id, symbol="RELIANCE", strategy_id="x", direction="LONG",
                                entry_time=datetime.now(UTC), entry_price=100.0, quantity=1, stop_loss=98.0, mode="PAPER"))
        await session.commit()
    _with_user(t, trade)
    assert client.put("/api/paper/account", headers=t["headers"], json={"starting_capital": 9_000}).status_code == 409


def test_an_account_still_not_set_takes_his_first_capital_even_after_a_trade():
    t = _user_tenant("late")
    client.get("/api/paper/account", headers=t["headers"])

    async def trade(session, user):
        session.add(TradeRecord(tenant_id=user.tenant_id, user_id=user.id, symbol="RELIANCE", strategy_id="x", direction="LONG",
                                entry_time=datetime.now(UTC), entry_price=100.0, quantity=1, stop_loss=98.0, mode="PAPER"))
        await session.commit()
    _with_user(t, trade)
    out = client.put("/api/paper/account", headers=t["headers"], json={"starting_capital": 75_000})
    assert out.status_code == 200 and out.json()["starting_capital"] == 75_000.0


# --- the first paper order asks; an automated entry places nothing and says which setting -----------------------------------

def test_a_paper_entry_without_a_capital_places_nothing_then_his_capital_lets_it_through():
    t = _user_tenant("entry")
    result, order = _execute(t)
    assert result.executed is False and order.trade_id is None and NEEDS in json.loads(order.reasons_json)
    client.put("/api/paper/account", headers=t["headers"], json={"starting_capital": 200_000})
    result, order = _execute(t)
    assert result.executed is True, result.reasons


def test_without_the_paper_account_feature_no_capital_is_asked():
    t = _user_tenant("noflag", flag=False)
    result, _ = _execute(t)
    assert result.executed is True and NEEDS not in (result.reasons or [])


def test_a_percent_limit_without_a_capital_is_not_measured_from_a_guess():
    t = _user_tenant("pct")

    async def check(session, user):
        assert await by_default.capital_basis(session, user) is None
        settings = by_default.RiskSettings(risk_per_trade_pct=1.0, risk_per_trade_amount=0.0, daily_loss_pct=2.0, max_trades_per_day=0,
                                           min_rr=0.0, max_sector_pct=0.0, max_correlated_risk_pct=0.0, max_open_positions=0)
        cap = by_default.risk_cap(None, settings)
        assert cap["amount"] is None and cap["needs_capital"] is True
        assert by_default.daily_loss_breached(None, settings, [-50_000.0], 0.0)["breached"] is False
        assert by_default.risk_cap(100_000.0, settings)["amount"] == 1_000.0
    _with_user(t, check)


def test_the_terminal_funds_and_the_risk_budget_need_his_capital():
    t = _user_tenant("funds")

    async def check(session, user):
        from app.paper.fno_insights import paper_balance
        from app.terminal.status import funds_of
        funds = (await funds_of(session, user, []))["funds"]
        assert funds["balance"] is None and funds["available"] is None and funds["needs_capital"] is True
        assert (await paper_balance(session, user))["amount"] is None
    _with_user(t, check)


# --- charges: Not set = statutory only; his own brokerage / slippage are used everywhere -------------------------------------

def test_charges_not_set_are_statutory_only_and_his_own_are_read():
    assert charges_from({}) == STATUTORY_ONLY and STATUTORY_ONLY.words == "Statutory charges only: no brokerage, no slippage"
    own = charges_from({"paper_account.charges_model": "STATUTORY_BROKERAGE", "paper_account.brokerage_per_order": 15.0,
                        "paper_account.slippage_pct": 0.05})
    assert (own.brokerage_per_order, own.slippage_pct) == (15.0, 0.05)
    assert own.words == "Statutory charges, your brokerage ₹15 per order, your slippage 0.05% each side"
    only = charges_from({"paper_account.charges_model": "STATUTORY", "paper_account.brokerage_per_order": 15.0})
    assert only.brokerage_per_order == 0.0                      # statutory only: his saved brokerage is not added
    missing = charges_from({"paper_account.charges_model": "STATUTORY_BROKERAGE"})
    assert missing.brokerage_per_order == 0.0 and "not set" in missing.words


def test_the_paper_broker_and_the_cost_model_carry_no_brokerage_or_slippage_of_atps():
    broker = PaperBroker()
    assert broker.brokerage_per_order == 0.0 and broker.slippage_pct == 0.0
    day = date(2026, 6, 1)
    assert india_costs.leg_cost(day, "buy", 100.0, 75, "OPTION")["brokerage"] == 0.0
    assert india_costs.leg_cost(day, "buy", 100.0, 75, "OPTION", brokerage_per_order=20.0)["brokerage"] > 0.0
    assert PaperCharges(brokerage_per_order=20.0, slippage_pct=0.1).broker()._slip(100.0, SignalDirection.LONG) == pytest.approx(100.1)


def test_a_paper_fill_and_its_close_pay_his_own_charges_and_keep_them():
    t = _user_tenant("fill")
    client.put("/api/paper/account", headers=t["headers"], json={"starting_capital": 500_000})
    _save(t, charges_model="STATUTORY_BROKERAGE", brokerage_per_order=25, slippage_pct=0.1)
    result, order = _execute(t)
    assert result.executed is True, result.reasons

    async def close(session, user):
        trade = await session.get(TradeRecord, order.trade_id)
        assert trade.entry_price == pytest.approx(100.1)       # his 0.1 % slippage on the buy
        await close_position(session, trade, 110.0, "Test exit", user_id=user.id)
        await session.refresh(trade)
        expected = PaperBroker(brokerage_per_order=25.0).estimate_round_trip_costs(
            trade.entry_price, 110.0, trade.quantity, "UNDERLYING", entry_date=trade.entry_time, exit_date=trade.exit_time,
            delivery=(trade.holding or "") == "SWING")
        assert trade.charges == pytest.approx(expected) and trade.charges > 50.0 - 0.01   # two orders at ₹25
        return trade.charges
    stored = _with_user(t, close)
    _save(t, brokerage_per_order=1)                             # a later change never rewrites the closed trade
    assert _with_user(t, lambda s, u: s.get(TradeRecord, order.trade_id)).charges == stored


def test_an_exit_without_a_capital_still_closes():
    t = _user_tenant("exit")

    async def go(session, user):
        trade = TradeRecord(tenant_id=user.tenant_id, user_id=user.id, symbol="RELIANCE", strategy_id="x", direction="LONG",
                            entry_time=datetime.now(UTC), entry_price=100.0, quantity=1, stop_loss=98.0, mode="PAPER")
        session.add(trade)
        await session.commit()
        outcome = await close_position(session, trade, 99.0, "Exit now", user_id=user.id)
        assert outcome.closed is True                           # ADR-0004: no capital is ever asked for an exit
        assert await paper_settings.capital_of(session, user) is None
    _with_user(t, go)


def test_the_options_builder_cost_line_starts_from_his_brokerage_else_none():
    from tests.test_z5_p1d_risk_by_default import _flag as _tenant_flag
    t = _user_tenant("ob")
    _tenant_flag("options_builder", t["tenant_id"])
    body = {"legs": [{"direction": "BUY", "option_type": "CE", "strike": 24000, "premium": 100, "lots": 1, "lot_size": 75,
                      "expiry": "2030-01-30", "iv": 0.15}], "spot": 24000, "as_of": "2030-01-02"}
    out = client.post("/api/options-builder/evaluate", headers=t["headers"], json=body)
    assert out.status_code == 200, out.text
    assert out.json()["costs"]["brokerage_per_order"] == 0.0 and "Statutory charges only" in out.json()["costs"]["charges_words"]
    _save(t, charges_model="STATUTORY_BROKERAGE", brokerage_per_order=12)
    assert client.post("/api/options-builder/evaluate", headers=t["headers"], json=body).json()["costs"]["brokerage_per_order"] == 12.0
    assert client.post("/api/options-builder/evaluate", headers=t["headers"],
                       json={**body, "brokerage_per_order": 7}).json()["costs"]["brokerage_per_order"] == 7.0


# --- backtests: his capital and charges (parity); none set -> no capital is guessed ----------------------------------------

def test_a_backtest_takes_his_capital_and_charges_and_guesses_none():
    from app.strategy_engine.indicator_strategies import EmaRsiScalper
    from tests.utils import make_series, noisy_uptrend
    df = make_series(noisy_uptrend(n=300))
    plain = run_backtest(EmaRsiScalper(tf="1min"), df, "TESTSYM", "1min", RiskConfig())
    assert plain.equity_curve[0] == 0.0                         # no capital set: nothing chosen for him
    own = run_backtest(EmaRsiScalper(tf="1min"), df, "TESTSYM", "1min", RiskConfig(capital=300_000.0),
                       charges=PaperCharges(brokerage_per_order=20.0))
    assert own.equity_curve[0] == 300_000.0
    if plain.trades:
        assert sum(x.charges or 0 for x in own.trades) > sum(x.charges or 0 for x in plain.trades)

    t = _user_tenant("bt")
    client.put("/api/paper/account", headers=t["headers"], json={"starting_capital": 123_000})
    _save(t, charges_model="STATUTORY_BROKERAGE", brokerage_per_order=9, slippage_pct=0.02)
    risk, charges = _with_user(t, lambda s, u: paper_settings.backtest_inputs(s, u, RiskConfig()))
    assert risk.capital == 123_000.0 and (charges.brokerage_per_order, charges.slippage_pct) == (9.0, 0.02)
    risk, _ = _with_user(t, lambda s, u: paper_settings.backtest_inputs(s, u, RiskConfig(capital=50_000.0)))
    assert risk.capital == 50_000.0                             # his organisation's / request's own capital wins


# --- the migration (up / down on a test DB; existing accounts keep their capital) ------------------------------------------

def _migration():
    path = BACKEND / "alembic/versions/c4e8a2f6b031_m68_paper_capital_not_set.py"
    spec = importlib.util.spec_from_file_location("m68_pr7_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_keeps_every_capital_marks_his_own_and_goes_down():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    module = _migration()
    assert module.down_revision == "8d2f4a6c0e19" and module.ROLLBACK_SAFE is False
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE paper_accounts (id INTEGER PRIMARY KEY, user_id INTEGER, starting_capital NUMERIC(18, 2) NOT NULL)"))
        conn.execute(sa.text("CREATE TABLE audit_logs (id INTEGER PRIMARY KEY, user_id INTEGER, event VARCHAR(100))"))
        conn.execute(sa.text("INSERT INTO paper_accounts (id, user_id, starting_capital) VALUES (1, 11, 1000000), (2, 12, 250000)"))
        conn.execute(sa.text("INSERT INTO audit_logs (id, user_id, event) VALUES (1, 12, 'paper_capital_set'), (2, 11, 'login')"))
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
        rows = conn.execute(sa.text("SELECT id, starting_capital, capital_saved FROM paper_accounts ORDER BY id")).all()
        assert [(r[0], float(r[1]), bool(r[2])) for r in rows] == [(1, 1_000_000.0, False), (2, 250_000.0, True)]   # TS-6: kept
        conn.execute(sa.text("INSERT INTO paper_accounts (id, user_id) VALUES (3, 13)"))
        assert conn.execute(sa.text("SELECT starting_capital, capital_saved FROM paper_accounts WHERE id = 3")).one()[0] is None
        with Operations.context(MigrationContext.configure(conn)):
            module.downgrade()
        rows = conn.execute(sa.text("SELECT id, starting_capital FROM paper_accounts ORDER BY id")).all()
        assert [(r[0], float(r[1])) for r in rows] == [(1, 1_000_000.0), (2, 250_000.0), (3, 1_000_000.0)]
        cols = {c["name"]: c for c in sa.inspect(conn).get_columns("paper_accounts")}
        assert "capital_saved" not in cols and not cols["starting_capital"]["nullable"]


def test_the_model_and_the_config_carry_no_paper_start_value():
    from app.core import config as app_config
    col = PaperAccountRecord.__table__.c["starting_capital"]
    assert col.nullable and col.default is None and col.server_default is None
    for name in ("PAPER_STARTING_CAPITAL", "PAPER_MIN_CAPITAL", "PAPER_MAX_CAPITAL"):
        assert not hasattr(app_config, name)


def test_service_words_for_the_refusal():
    assert NEEDS == "Needs your setting: Starting capital (Settings › Trade settings › Paper account)"
    assert paper_settings.is_capital_refusal(NEEDS)
    assert service.FLAG == "paper_account"
