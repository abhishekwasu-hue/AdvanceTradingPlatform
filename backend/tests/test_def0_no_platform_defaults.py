"""DEF-0 (owner order, 28 Oct): no platform defaults in order placement / risk. Every limit is the trader's own setting;
unset = off (no limit, no gate). Capital for any % = the trader's own capital, else the PAPER account balance - never a
built-in 100,000. PR 1: D1 RiskConfig, D2 hard-coded capital, D5 the override multiple, D10 the migration."""
import ast
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.core import config as app_config
from app.core.enums import SignalDirection, SignalGrade
from app.core.models import RiskConfig, Signal
from app.risk_engine.risk_manager import RiskManager, TradingDayState
from tests.test_fno1_chain_ticket import LOT, _body, _contract, _expiry, _preview
from tests.test_trading_worker import _tenant
from tests.test_z3_approval_entries import _price
from tests.test_z3b_paper_account import _paper_flag
from tests.test_z5_p1d_risk_by_default import _flag, _org

BACKEND = Path(__file__).resolve().parents[1]
LIMITS = ("capital", "risk_per_trade_pct", "max_daily_loss_pct", "max_trades_per_day", "max_open_positions", "max_consecutive_losses",
          "max_portfolio_risk_pct", "stop_cooldown_minutes", "dd_level_1_pct", "dd_level_2_pct", "event_size_cut_pct")


def _signal(entry: float = 100.0, stop: float = 95.0) -> Signal:
    return Signal(symbol="SBIN", strategy_id="t", strategy_name="t", direction=SignalDirection.LONG, timestamp=datetime.now(timezone.utc),
                  entry=entry, stop_loss=stop, target1=entry + 10, risk_reward=2.0, score=1, grade=SignalGrade.VALID, reasons=[],
                  timeframe_combo="t")


# --- D1: RiskConfig ------------------------------------------------------------------------------------------------------

def test_every_limit_is_not_set_by_default():
    cfg = RiskConfig()
    assert all(getattr(cfg, name) is None for name in LIMITS), {n: getattr(cfg, n) for n in LIMITS}
    assert cfg.min_risk_reward == 0.0
    assert cfg.risk_amount() is None and cfg.daily_loss_amount() is None


def test_the_capital_is_the_traders_own_else_the_paper_balance():
    assert RiskConfig().with_capital(1_000_000.0).capital == 1_000_000.0
    assert RiskConfig(capital=250_000.0).with_capital(1_000_000.0).capital == 250_000.0
    assert RiskConfig().with_capital(None).capital is None


def test_no_limit_set_means_no_gate_and_the_traders_own_size():
    state = TradingDayState(trades_today=500, daily_pnl=-10_000_000.0, consecutive_losses=50, open_positions=99)
    decision = RiskManager(RiskConfig()).validate_and_size(_signal(), state, requested_quantity=40)
    assert decision.approved and decision.quantity == 40
    one_lot = RiskManager(RiskConfig()).validate_and_size(_signal(), TradingDayState())
    assert one_lot.approved and one_lot.quantity == 1


@pytest.mark.parametrize("field, value, state, words", [
    ("max_trades_per_day", 3, TradingDayState(trades_today=3), "You made 3 new entries today; your limit is 3."),
    ("max_open_positions", 2, TradingDayState(open_positions=2), "You hold 2 open positions; your limit is 2."),
    ("max_consecutive_losses", 4, TradingDayState(consecutive_losses=4), "4 losses in a row (your limit is 4)"),
])
def test_each_limit_applies_once_the_trader_sets_it(field, value, state, words):
    decision = RiskManager(RiskConfig(**{field: value})).validate_and_size(_signal(), state)
    assert not decision.approved and any(words in r for r in decision.reasons)


def test_the_daily_loss_limit_and_the_risk_per_trade_apply_on_the_capital_they_read():
    cfg = RiskConfig(max_daily_loss_pct=2.0).with_capital(1_000_000.0)              # 20,000 a day on the PAPER balance
    assert not RiskManager(cfg).validate_and_size(_signal(), TradingDayState(daily_pnl=-20_000.0)).approved
    assert RiskManager(cfg).validate_and_size(_signal(), TradingDayState(daily_pnl=-19_000.0)).approved
    sized = RiskManager(RiskConfig(risk_per_trade_pct=0.5).with_capital(1_000_000.0)).validate_and_size(_signal(), TradingDayState())
    assert sized.approved and sized.quantity == 1000                                   # 5,000 / 5 per share


# --- the ticket of the owner's screenshot: a new user with no settings ----------------------------------------------------

@pytest.fixture
def fresh(monkeypatch):
    t = _tenant(f"def0-{uuid.uuid4().hex[:6]}@example.com")
    _flag("ui_v2", t["tenant_id"])
    _paper_flag(t["tenant_id"])
    expiry = _expiry()
    strike = 22700.0
    _contract(expiry, strike)
    _price(monkeypatch, 131.5)
    return t, expiry, strike


def test_a_new_user_with_no_settings_can_place_the_sold_option_of_the_screenshot(fresh):
    t, expiry, strike = fresh
    view = _preview(t, _body(expiry, strike, side="SELL", product="NRML", stop_loss=150.0))
    assert view["refusals"] == [] and view["can_place"] is True, view["refusals"]
    assert view["risk"]["risk_per_trade"] is None                                       # nothing set, nothing pre-filled
    assert view["max_loss"]["value"] == pytest.approx((150.0 - 131.5) * LOT)


def test_the_same_ticket_is_refused_only_once_the_trader_sets_a_risk_per_trade(fresh):
    t, expiry, strike = fresh
    _org(t, capital=100_000.0, risk_pct=0.5)                                            # his own 500 a trade
    view = _preview(t, _body(expiry, strike, side="SELL", product="NRML", stop_loss=150.0))
    assert not view["can_place"] and view["risk"]["risk_per_trade"] == pytest.approx(500.0)


# --- D2 / CI guard: no numeric literal defaults for these limits in the model, the table or the interview ----------------

def _class_defaults(path: Path, cls: str):
    tree = ast.parse(path.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    for item in node.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) and item.target.id in LIMITS:
            yield item.target.id, ast.unparse(item.value) if item.value is not None else ""


def test_no_numeric_default_for_a_limit_in_risk_config_or_the_risk_settings_table():
    for name, value in _class_defaults(BACKEND / "app/core/models.py", "RiskConfig"):
        assert value == "None", f"RiskConfig.{name} = {value}"
    for name, value in _class_defaults(BACKEND / "app/db/models.py", "RiskSettingsRecord"):
        assert "default" not in value and "nullable=True" in value, f"RiskSettingsRecord.{name}: {value}"


def test_no_built_in_capital_in_the_ai_and_portfolio_paths():
    for rel in ("app/ai/interview.py", "app/ai/monitor.py", "app/portfolio/routes.py", "app/paper/fno_insights.py"):
        text = (BACKEND / rel).read_text()
        assert not re.search(r"\b100_?000(\.0)?\b", text), rel


def test_paper_starting_capital_is_the_fallback_not_100000():
    assert app_config.PAPER_STARTING_CAPITAL != 100_000.0


# --- D10: the migration ----------------------------------------------------------------------------------------------------

def test_the_migration_backs_up_then_nulls_only_the_old_platform_values():
    text = (BACKEND / "alembic/versions/d0f0a1b2c3d4_def0_risk_limits_optional.py").read_text()
    assert text.index("CREATE TABLE risk_settings_def0_backup") < text.index("UPDATE risk_settings SET capital = NULL")
    for column, old in (("capital", "100000"), ("risk_per_trade_pct", "0.5"), ("max_daily_loss_pct", "3"), ("max_trades_per_day", "20"),
                        ("max_open_positions", "3"), ("max_consecutive_losses", "4"), ("max_portfolio_risk_pct", "6"),
                        ("stop_cooldown_minutes", "30"), ("dd_level_1_pct", "5"), ("dd_level_2_pct", "10"), ("event_size_cut_pct", "50")):
        assert f"UPDATE risk_settings SET {column} = NULL WHERE {column} = {old}" in text
    assert "ROLLBACK_SAFE = False" in text
