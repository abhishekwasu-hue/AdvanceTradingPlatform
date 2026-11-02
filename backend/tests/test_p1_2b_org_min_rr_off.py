"""P1-2b (owner decision TT12-4, DECISIONS 24 Oct): the organisation-level minimum reward:risk has no built-in value.
Before: RiskConfig.min_risk_reward defaulted to 1.2 (and the risk_settings column too), so every entry whose reward:risk
was below 1.2 was refused on LIVE, paper and in the equity backtest, though nobody had stated that rule (CLAUDE.md
section 3). After: 0 = not set = no R:R filter anywhere; only a value the organisation sets itself filters entries.
A stored row holding exactly the old 1.2 reads as not set (the TT12-1 rule); an organisation that now sets 1.2 itself
keeps it. Parity: the same entry decisions in the live / paper RiskManager (tenant settings from the API), the equity
backtest engine and the options backtest engine, with the limit unset and with a value set. Exits are never filtered
(ADR-0004): they do not pass through RiskManager."""
import asyncio
import math
from typing import Dict, List

import pandas as pd
import pytest
from sqlalchemy import select

from app.backtest import engine as underlying_engine
from app.backtest import options_engine
from app.core.enums import ExecutionMode, OptionPosition, OptionStrategy, SignalDirection, SignalGrade
from app.core.models import RiskConfig, Signal
from app.db.models import RiskSettingsRecord
from app.execution.router import OrderRouter
from app.risk_engine import routes as risk_routes
from app.risk_engine.risk_manager import RiskManager, TradingDayState
from tests.test_auth_api import _register, _session_factory, client

PRICE = 24_500.0
DAY = "2026-10-07"
ATTEMPTS = [pd.Timestamp(f"{DAY} 09:15") + pd.Timedelta(minutes=10 + 6 * k) for k in range(4)]
RR = 0.5                                                   # below the old built-in 1.2 and below a set minimum of 1.0


def _frame() -> pd.DataFrame:
    """Flat bars; on the bar after each attempt the high reaches the target (a win), so every taken trade closes."""
    hits = {ts + pd.Timedelta(minutes=1) for ts in ATTEMPTS}
    index = pd.date_range(f"{DAY} 09:15", periods=40, freq="1min")
    rows = [{"open": PRICE, "high": PRICE * 1.01 if ts in hits else PRICE + 0.05, "low": PRICE - 0.05, "close": PRICE, "volume": 1000.0}
            for ts in index]
    return pd.DataFrame(rows, index=index)


def _signal(ts) -> Signal:
    return Signal(symbol="NIFTY 50", strategy_id="p1_2b", strategy_name="script", direction=SignalDirection.LONG, timestamp=ts, entry=PRICE,
                  stop_loss=PRICE * 0.99, target1=PRICE * 1.005, target2=None, risk_reward=RR, score=80, grade=SignalGrade.HIGH_QUALITY)


class _Scripted:
    id = "p1_2b"
    timeframes = ["1min"]
    params: Dict = {}

    def min_history(self):
        return {"1min": 5}

    def analyze(self, data, symbol):
        ts = data["1min"].index[-1]
        if ts in ATTEMPTS:
            return _signal(ts)
        return Signal(symbol=symbol, strategy_id=self.id, strategy_name="script", direction=SignalDirection.NO_TRADE, timestamp=ts)


def _risk(min_rr=None) -> RiskConfig:
    extra = {} if min_rr is None else {"min_risk_reward": min_rr}
    return RiskConfig(capital=1_000_000, risk_per_trade_pct=1.0, max_consecutive_losses=50, max_trades_per_day=50, max_open_positions=50, **extra)


def _taken(result) -> List[bool]:
    entered = {pd.Timestamp(t.entry_time) for t in result.trades}
    return [ts in entered for ts in ATTEMPTS]


def _engine(cfg: RiskConfig) -> List[bool]:
    return _taken(underlying_engine.run_backtest(_Scripted(), _frame(), "NIFTY 50", "1min", cfg))


def _options(cfg: RiskConfig) -> List[bool]:
    ocfg = options_engine.OptionBacktestConfig(option_strategy=OptionStrategy.SINGLE, option_position=OptionPosition.BUY,
                                               implied_volatility=0.14, max_lots=1, premium_stop_pct=30.0)  # DEF-0: old 30% explicit
    return _taken(options_engine.run_option_backtest(_Scripted(), _frame(), "NIFTY 50", "1min", cfg, ocfg))


def _live(cfg: RiskConfig) -> List[bool]:
    """The live / paper path: the order router's own RiskManager on the same signals (a fresh day, nothing open)."""
    router = OrderRouter(mode=ExecutionMode.PAPER, risk_config=cfg, product="MIS", order_style="MARKET")
    return [router.risk_manager.validate_and_size(_signal(ts), TradingDayState(), requested_quantity=1).approved for ts in ATTEMPTS]


# --- defaults -----------------------------------------------------------------------------------------------------------------

def test_no_built_in_minimum():
    assert RiskConfig().min_risk_reward == 0.0
    # M68 PR-5 (TS1-053): the column is NULL = not set (no 0 sentinel default any more)
    column = RiskSettingsRecord.__table__.c.min_risk_reward
    assert column.nullable and (column.default is None or column.default.arg is None) and column.server_default is None


def test_regression_no_engine_filters_by_reward_risk_when_nothing_is_set():
    cfg = RiskConfig()
    for rr in (0.01, 0.5, 1.19):
        decision = RiskManager(cfg).validate_and_size(_signal(ATTEMPTS[0]).model_copy(update={"risk_reward": rr}), TradingDayState(),
                                                    requested_quantity=1)   # M68 PR-2: his own size
        assert decision.approved and not any("Risk/Reward" in r for r in decision.reasons), (rr, decision.reasons)
    unset = _risk()
    assert _live(unset) == _engine(unset) == _options(unset) == [True] * len(ATTEMPTS)


# --- parity with a value set ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("min_rr, expected", [(1.0, False), (0.4, True), (0.5, True)])
def test_the_same_entry_decision_in_live_paper_and_both_backtest_engines(min_rr, expected):
    cfg = _risk(min_rr)
    assert _live(cfg) == _engine(cfg) == _options(cfg) == [expected] * len(ATTEMPTS)


def test_a_multi_leg_structure_carries_no_reward_risk_as_live():
    """Live multi-leg orders size on a signal without reward:risk (app/execution/multileg.py); the backtest does the same."""
    cfg = _risk(1.0)
    ocfg = options_engine.OptionBacktestConfig(option_strategy=OptionStrategy.BULL_PUT_SPREAD, implied_volatility=0.14, max_lots=1)
    result = options_engine.run_option_backtest(_Scripted(), _frame(), "NIFTY 50", "1min", cfg, ocfg)
    assert not any("risk/reward" in k.lower() for k in result.options["signals_skipped"]), result.options["signals_skipped"]


# --- stored organisation settings ---------------------------------------------------------------------------------------------

def test_reading_the_stored_value():
    assert risk_routes.org_min_rr_read(1.2) == 0.0                          # the old built-in: not set
    assert risk_routes.org_min_rr_read(0.0) == 0.0 and risk_routes.org_min_rr_read(-1.0) == 0.0 and risk_routes.org_min_rr_read(None) == 0.0
    assert risk_routes.org_min_rr_read(float("nan")) == 0.0
    assert risk_routes.org_min_rr_read(1.5) == 1.5 and risk_routes.org_min_rr_read(2.0) == 2.0      # his own
    own = risk_routes.org_min_rr_to_store(1.2)
    assert own != 1.2 and math.isclose(own, 1.2) and risk_routes.org_min_rr_read(own) == 1.2       # 1.2 set now: kept
    assert risk_routes.org_min_rr_to_store(1.5) == 1.5 and risk_routes.org_min_rr_to_store(0.0) == 0.0


def _org() -> dict:
    import uuid
    token = _register(f"p12b-{uuid.uuid4().hex[:8]}@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    return {"headers": headers, **client.get("/api/auth/me", headers=headers).json()}


def _set_column(tenant_id: int, value: float) -> None:
    async def go():
        async with _session_factory() as session:
            row = await session.scalar(select(RiskSettingsRecord).where(RiskSettingsRecord.tenant_id == tenant_id))
            row.min_risk_reward = value
            await session.commit()
    asyncio.run(go())


def _tenant_cfg(tenant_id: int) -> RiskConfig:
    async def go():
        async with _session_factory() as session:
            return await risk_routes.get_tenant_risk_config(tenant_id, session)
    return asyncio.run(go())


def test_the_api_starts_unset_keeps_an_own_value_and_reads_an_old_row_as_unset():
    me = _org()
    assert client.get("/api/risk-settings", headers=me["headers"]).json()["min_risk_reward"] == 0.0
    body = client.get("/api/risk-settings", headers=me["headers"]).json()
    saved = client.put("/api/risk-settings", headers=me["headers"], json={**body, "capital": 500_000}).json()
    assert saved["min_risk_reward"] == 0.0                                  # saving other fields does not add a minimum
    _set_column(me["tenant_id"], 1.2)                                       # a row as an earlier build left it
    assert client.get("/api/risk-settings", headers=me["headers"]).json()["min_risk_reward"] == 0.0
    assert _live(_tenant_cfg(me["tenant_id"])) == [True] * len(ATTEMPTS)
    # He sets 1.2 himself: kept and applied.
    out = client.put("/api/risk-settings", headers=me["headers"], json={**body, "min_risk_reward": 1.2}).json()
    assert out["min_risk_reward"] == 1.2
    assert client.get("/api/risk-settings", headers=me["headers"]).json()["min_risk_reward"] == 1.2
    assert _live(_tenant_cfg(me["tenant_id"])) == [False] * len(ATTEMPTS)
    # Any other value of his own, and back to not set.
    assert client.put("/api/risk-settings", headers=me["headers"], json={**body, "min_risk_reward": 0.4}).json()["min_risk_reward"] == 0.4
    assert _live(_tenant_cfg(me["tenant_id"])) == [True] * len(ATTEMPTS)
    assert client.put("/api/risk-settings", headers=me["headers"], json={**body, "min_risk_reward": 0}).json()["min_risk_reward"] == 0.0
    assert client.put("/api/risk-settings", headers=me["headers"], json={**body, "min_risk_reward": -1}).status_code == 400
    # M68 PR-5 (TS1-050): no ATP top on his own minimum (was 10)
    assert client.put("/api/risk-settings", headers=me["headers"], json={**body, "min_risk_reward": 11}).json()["min_risk_reward"] == 11


def test_exits_never_pass_through_the_minimum():
    """ADR-0004: the exit paths never call RiskManager (the same source check as the other exit guards)."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "app"
    for rel in ("trading/position_monitor.py", "trading/exit_rules.py"):
        text = (root / rel).read_text()
        assert "validate_and_size" not in text and "min_risk_reward" not in text, rel


# --- the strategy's own signal builder (scanner, signals, live / paper worker and both backtests call analyze) -------------------

from app.core.enums import StrategyCategory  # noqa: E402
from app.strategy_engine.base import BaseStrategy  # noqa: E402


class _Built(BaseStrategy):
    """A strategy that builds its signals with BaseStrategy.build_signal at a first target of 1.0 R and names no `min_rr`
    of its own (the case the old built-in 1.2 filtered silently)."""
    id = "p1_2b_built"
    name = "built"
    description = "test"
    category = StrategyCategory.INDICATOR_BASED
    timeframes = ["1min"]
    default_params: Dict = {}

    def min_history(self):
        return {"1min": 5}

    def analyze(self, data, symbol):
        ts = data["1min"].index[-1]
        if ts in ATTEMPTS:
            return self.build_signal(symbol, ts, SignalDirection.LONG, PRICE, PRICE * 0.99, 80, ["test"], target_rr=(1.0, 2.0))
        return self.no_trade(symbol, ts, [])


def _signals(strategy) -> List[Signal]:
    df = _frame()
    return [strategy.analyze({"1min": df.loc[:ts]}, "NIFTY 50") for ts in ATTEMPTS]


def _all_paths(strategy_factory, cfg: RiskConfig) -> Dict[str, List[bool]]:
    """Scanner / signals (the strategy's own signal), the live / paper path (signal + the router's RiskManager) and both
    backtest engines, for the same bars."""
    sigs = _signals(strategy_factory())
    router = OrderRouter(mode=ExecutionMode.PAPER, risk_config=cfg, product="MIS", order_style="MARKET")
    ocfg = options_engine.OptionBacktestConfig(option_strategy=OptionStrategy.SINGLE, option_position=OptionPosition.BUY,
                                               implied_volatility=0.14, max_lots=1, premium_stop_pct=30.0)  # DEF-0: old 30% explicit
    return {
        "scanner": [s.is_tradeable for s in sigs],
        "live": [s.is_tradeable and router.risk_manager.validate_and_size(s, TradingDayState(), requested_quantity=1).approved for s in sigs],
        "backtest": _taken(underlying_engine.run_backtest(strategy_factory(), _frame(), "NIFTY 50", "1min", cfg)),
        "options": _taken(options_engine.run_option_backtest(strategy_factory(), _frame(), "NIFTY 50", "1min", cfg, ocfg)),
    }


def test_regression_no_strategy_default_filters_by_reward_risk():
    got = _all_paths(_Built, _risk())
    assert got == {k: [True] * len(ATTEMPTS) for k in got}, got
    assert all(s.risk_reward == 1.0 for s in _signals(_Built()))


@pytest.mark.parametrize("own, expected", [(1.2, False), (1.0, True), (0.8, True)])
def test_a_strategys_own_min_rr_applies_the_same_on_every_path(own, expected):
    got = _all_paths(lambda: _Built(min_rr=own), _risk())
    assert got == {k: [expected] * len(ATTEMPTS) for k in got}, got


@pytest.mark.parametrize("org, expected", [(1.2, False), (1.0, True)])
def test_an_organisation_minimum_applies_the_same_after_the_strategy(org, expected):
    got = _all_paths(_Built, _risk(org))
    assert got["live"] == got["backtest"] == got["options"] == [expected] * len(ATTEMPTS), got
    assert got["scanner"] == [True] * len(ATTEMPTS)          # the signal itself is not filtered; the risk engine decides


def test_the_built_in_strategies_name_no_minimum():
    # REV-B2 (owner order 30 Oct): a built-in's min_rr compared two of its own fixed constants and never acted, so it is
    # removed (tests/test_rev1_m2_builtin_reasons.py proves the trades are unchanged). A strategy's own min_rr (above)
    # and the organisation's minimum still apply.
    from app.strategy_engine.registry import registry
    for s in registry.list_all():
        assert "min_rr" not in s.params, s.id


# --- custom (no-code) strategies: no built-in minimum, a stored 1.2 = not set (TT12-1 rule), his own value kept -------------------

from app.strategy_engine import declarative as D  # noqa: E402
from app.strategy_engine.nlu_parser import parse_strategy_description  # noqa: E402


def _custom(min_rr=None, own=False) -> D.DeclarativeStrategy:
    raw = {"name": "always long", "timeframe": "1min", "atr_period": 5, "stop_loss_atr_mult": 1.0, "target_rr": [1.0, 2.0],
           "long_conditions": [{"left": {"type": "indicator", "indicator": "CLOSE"}, "operator": "GT", "right": {"type": "value", "value": 1}}]}
    if min_rr is not None:
        raw["min_rr"] = min_rr
    model = D.OwnCustomStrategyConfig if own else D.CustomStrategyConfig
    return D.DeclarativeStrategy("custom:p12b", D.CustomStrategyConfig.model_validate_json(model.model_validate(raw).model_dump_json()))


def _custom_paths(factory, cfg: RiskConfig) -> Dict[str, bool]:
    """Whether each path enters at all: the scanner's signal and the live / paper risk check on the last bar, and both
    backtest engines over the same bars."""
    sig = factory().analyze({"1min": _frame()}, "NIFTY 50")
    router = OrderRouter(mode=ExecutionMode.PAPER, risk_config=cfg, product="MIS", order_style="MARKET")
    ocfg = options_engine.OptionBacktestConfig(option_strategy=OptionStrategy.SINGLE, option_position=OptionPosition.BUY,
                                               implied_volatility=0.14, max_lots=1, premium_stop_pct=30.0)  # DEF-0: old 30% explicit
    return {"scanner": sig.is_tradeable,
            "live": sig.is_tradeable and router.risk_manager.validate_and_size(sig, TradingDayState(), requested_quantity=1).approved,
            "backtest": underlying_engine.run_backtest(factory(), _frame(), "NIFTY 50", "1min", cfg).total_trades > 0,
            "options": len(options_engine.run_option_backtest(factory(), _frame(), "NIFTY 50", "1min", cfg, ocfg).trades) > 0}


def test_custom_config_has_no_built_in_minimum_and_reads_a_stored_1_2_as_unset():
    assert D.CustomStrategyConfig.model_fields["min_rr"].default is None
    assert _custom().config.min_rr is None
    assert _custom(1.2).config.min_rr is None                               # a config saved with the old default
    assert _custom(1.5).config.min_rr == 1.5 and _custom(0.8).config.min_rr == 0.8      # his own value kept
    own = _custom(1.2, own=True)                                             # he typed exactly 1.2 himself
    assert own.config.min_rr == D.OWN_LEGACY_MIN_RR and own.params["min_rr"] == 1.2


def test_regression_no_custom_strategy_filters_by_reward_risk_when_nothing_is_set():
    for factory in (_custom, lambda: _custom(1.2)):
        assert _custom_paths(factory, _risk()) == {"scanner": True, "live": True, "backtest": True, "options": True}


@pytest.mark.parametrize("min_rr, own, expected", [(1.5, False, False), (1.2, True, False), (0.8, False, True)])
def test_a_custom_strategys_own_minimum_applies_the_same_on_every_path(min_rr, own, expected):
    got = _custom_paths(lambda: _custom(min_rr, own=own), _risk())
    assert got == {k: expected for k in got}, got


def test_the_text_parser_sets_a_minimum_only_when_the_words_state_one():
    assert parse_strategy_description("buy when rsi below 30").config.min_rr is None
    stated = parse_strategy_description("buy when rsi below 30. minimum rr 1.2").config.min_rr
    assert stated is None or D.min_rr_in_force(stated) == 1.2


def test_the_builder_api_keeps_an_own_1_2_and_a_blank_is_not_set():
    me = _org()
    body = {"name": "p12b builder", "timeframe": "5min", "stop_loss_atr_mult": 1.0, "target_rr": [1.5, 2.0],     # his own (SB-UX SU4)
            "long_conditions": [{"left": {"type": "indicator", "indicator": "RSI", "period": 14}, "operator": "LT", "right": {"type": "value", "value": 30}}]}
    blank = client.post("/api/custom-strategies", headers=me["headers"], json=body)
    assert blank.status_code == 201, blank.text
    assert blank.json()["config"]["min_rr"] is None
    own = client.put(f"/api/custom-strategies/{blank.json()['id']}", headers=me["headers"], json={**body, "min_rr": 1.2}).json()
    assert D.min_rr_in_force(own["config"]["min_rr"]) == 1.2
