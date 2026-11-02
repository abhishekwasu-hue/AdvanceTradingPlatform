"""M68 PR-4 (owner order M68, TS1-035 to 040, 042 to 047, TS1-100, TS1-102 to 104): Options - no pre-chosen expiry,
strike, distance, side, width, ratio, delta tolerance, strike window, expiry-day roll, delivery warning or risk-free rate,
and no ATP ceiling on a distance, a width, a ratio or the lots.

Per value: Not set => the form / API asks ("Needs your setting: <field>", a 422 at the field); saved in Settings > Trade
settings > Options (or an older store he saved, TS-6) => used; the request's own value wins. A structure's definition is
kept (a straddle sells at the money, a credit structure writes). The migration keeps every stored width and goes down.
"""
import asyncio
import importlib.util
import json
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi import HTTPException

from app.backtest.routes import OptionBacktestBody, own_options
from app.core.enums import ExpiryRule, OptionStrategy, SignalDirection
from app.db.models import StrategyDeploymentRecord, User, UserPrefsRecord
from app.deployments.routes import ContractRulesRequest, DeploymentCreateRequest
from app.execution import smart_execution
from app.instruments import option_defaults
from app.instruments.contracts import ContractResolutionError, ContractRules
from app.instruments.spreads import plan_structure
from app.me import trade_settings as ts
from app.option_chain import chain_view
from tests.test_auth_api import _session_factory, client
from tests.test_contract_rules import _load_master
from tests.test_deployments_api import _auth, _create, _me, _store_broker

BACKEND = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.real_option_defaults      # M68: each user's own (empty) Options settings
URL = "/api/me/trade-settings"
LADDER = [float(k) for k in range(24000, 25001, 50)]


def _run(coro):
    return asyncio.run(coro)


def _fresh(prefix: str) -> dict:
    headers = _auth(f"{prefix}-{uuid.uuid4().hex[:8]}@example.com")
    _store_broker(headers, token_status="VALID")
    me = _me(headers)
    return {"headers": headers, "user_id": me["id"], "tenant_id": me["tenant_id"]}


def _save(t: dict, **values) -> None:
    out = client.put(URL, headers=t["headers"], json={"values": {f"options.{k}": v for k, v in values.items()}})
    assert out.status_code == 200, out.text


def _user(t: dict) -> User:
    async def go():
        async with _session_factory() as session:
            return await session.get(User, t["user_id"])
    return _run(go())


def _load(t: dict) -> option_defaults.OptionDefaults:
    async def go():
        async with _session_factory() as session:
            return await option_defaults.load(session, await session.get(User, t["user_id"]))
    return _run(go())


def _needs(response) -> dict:
    assert response.status_code == 422, response.text
    return {tuple(d["loc"][1:]): d["msg"] for d in response.json()["detail"] if d.get("type") == "needs_setting"}


# --- the bot API: asks, never fills -------------------------------------------------------------------------------------

def test_an_option_bot_with_nothing_set_is_asked_for_each_choice_at_its_field():
    _load_master()
    t = _fresh("m68o-ask")
    asked = _needs(_create(t["headers"], symbol="NIFTY 50", instrument_kind="OPTION"))
    assert set(asked) == {("expiry_rule",), ("option_position",), ("strike_rule",)}
    assert asked[("expiry_rule",)] == "Needs your setting: Expiry (Settings › Trade settings › Options)"
    assert _needs(_create(t["headers"], symbol="NIFTY 50", instrument_kind="FUTURE")) == {("expiry_rule",): option_defaults.needs_setting("expiry_rule")}
    away = _needs(_create(t["headers"], symbol="NIFTY 50", instrument_kind="OPTION", expiry_rule="NEAREST", option_position="BUY", strike_rule="OTM"))
    assert set(away) == {("strike_offset",)}                                  # no silent "1 strike away"
    width = _needs(_create(t["headers"], symbol="NIFTY 50", instrument_kind="OPTION", option_strategy="BULL_PUT_SPREAD", expiry_rule="NEAREST",
                           strike_rule="ATM"))
    assert set(width) == {("spread_width",)}                                 # no silent width 2
    condor = _needs(_create(t["headers"], symbol="NIFTY 50", instrument_kind="OPTION", option_strategy="IRON_CONDOR", expiry_rule="NEAREST",
                            spread_width=2))
    assert set(condor) == {("strike_offset",)}                                # no silent "1 strike out of the money"
    ratio = _needs(_create(t["headers"], symbol="NIFTY 50", instrument_kind="OPTION", option_strategy="CALL_RATIO_SPREAD", expiry_rule="NEAREST",
                           strike_rule="ATM", spread_width=2))
    assert set(ratio) == {("ratio_shorts",)}                                 # no silent 1:2
    delta = _needs(_create(t["headers"], symbol="NIFTY 50", instrument_kind="OPTION", expiry_rule="NEAREST", option_position="BUY",
                           strike_rule="ATM", strike_filters={"target_delta": 0.3}))
    assert set(delta) == {("strike_filters", "delta_tolerance"), ("strike_filters", "search_steps")}


def test_a_structure_keeps_its_own_definition_and_the_bot_stores_only_his_choices():
    _load_master()
    t = _fresh("m68o-def")
    straddle = _create(t["headers"], symbol="NIFTY 50", instrument_kind="OPTION", option_strategy="SHORT_STRADDLE", expiry_rule="NEXT")
    assert straddle.status_code == 201, straddle.text                       # a straddle names its strike; no width either
    body = straddle.json()
    assert body["option_position"] == "WRITE" and body["expiry_rule"] == "NEXT" and body["spread_width"] is None


def test_his_own_trade_settings_fill_what_the_form_did_not_send():
    _load_master()
    t = _fresh("m68o-own")
    _save(t, expiry_rule="NEXT", strike_rule="OTM", strike_offset=3, option_position="WRITE", spread_width=4, ratio=3,
          delta_tolerance=0.05, strike_search_steps=8)
    single = _create(t["headers"], symbol="NIFTY 50", instrument_kind="OPTION", strike_filters={"target_delta": 0.3})
    assert single.status_code == 201, single.text
    b = single.json()
    assert (b["expiry_rule"], b["strike_rule"], b["strike_offset"], b["option_position"]) == ("NEXT", "OTM", 3, "WRITE")
    assert b["strike_filters"] == {"target_delta": 0.3, "delta_tolerance": 0.05, "search_steps": 8}
    t2 = _fresh("m68o-own2")
    _save(t2, expiry_rule="NEAREST", strike_rule="ATM", spread_width=4, ratio=3)
    ratio = _create(t2["headers"], symbol="NIFTY 50", instrument_kind="OPTION", option_strategy="CALL_RATIO_SPREAD")
    assert ratio.status_code == 201, ratio.text
    assert ratio.json()["spread_width"] == 4 and ratio.json()["ratio_shorts"] == 3
    assert "sell 3 4 step(s) higher" in ratio.json()["contract_rules"]
    # the form's own value wins over his saved one
    t3 = _fresh("m68o-own3")
    _save(t3, expiry_rule="MONTHLY", option_position="WRITE", strike_rule="ATM")
    sent = _create(t3["headers"], symbol="NIFTY 50", instrument_kind="OPTION", expiry_rule="NEAREST", option_position="BUY")
    assert sent.status_code == 201 and (sent.json()["expiry_rule"], sent.json()["option_position"]) == ("NEAREST", "BUY")


def test_an_older_saved_my_trading_defaults_value_is_kept_ts6():
    t = _fresh("m68o-ts6")
    put = client.put("/api/me/prefs/trading_defaults", headers=t["headers"], json={"expiry_rule": "MONTHLY", "strike_rule": "ITM", "strike_offset": 2,
                                                                                  "option_position": "BUY", "spread_width": 5})
    assert put.status_code == 200, put.text
    own = _load(t)
    assert (own.expiry_rule, own.strike_rule, own.strike_offset, own.option_position, own.spread_width) == ("MONTHLY", "ITM", 2, "BUY", 5)
    _save(t, expiry_rule="NEXT")                                                 # Trade settings wins
    assert _load(t).expiry_rule == "NEXT"
    assert _load(_fresh("m68o-ts6b")) == option_defaults.OptionDefaults()     # a new user: everything Not set


# --- no ATP ceilings (TS1-046 / 047) --------------------------------------------------------------------------------------

def test_no_ceiling_on_lots_distance_width_or_ratio():
    req = ContractRulesRequest(instrument_kind="OPTION", max_lots=900, strike_offset=15, spread_width=30, ratio_shorts=6)
    assert (req.max_lots, req.strike_offset, req.spread_width, req.ratio_shorts) == (900, 15, 30, 6)
    leg = DeploymentCreateRequest(strategy_id="x", symbol="NIFTY 50", instrument_kind="OPTION", option_strategy="CUSTOM", expiry_rule="NEAREST",
                                  custom_legs=[{"right": "CE", "role": "SHORT", "strike_rule": "OTM", "strike_offset": 25, "ratio": 9},
                                               {"right": "CE", "role": "LONG", "strike_rule": "OTM", "strike_offset": 30, "ratio": 9}])
    assert leg.normalised().custom_legs[0].ratio == 9
    with pytest.raises(ValueError):
        DeploymentCreateRequest(strategy_id="x", symbol="NIFTY 50", custom_legs=[{"right": "CE", "role": "SHORT"}])   # a leg names its strike
    from app.paper import fno_ticket
    assert not hasattr(fno_ticket, "MAX_LOTS")
    for key in ("strike_offset", "spread_width", "ratio", "strike_search_steps"):
        assert ts.BY_KEY[f"options.{key}"].high == ts.BIG, key


def test_a_ratio_or_a_width_not_set_on_a_row_places_nothing_and_says_which_setting():
    rules = ContractRules(kind="OPTION", expiry_rule=ExpiryRule.NEAREST)
    with pytest.raises(ContractResolutionError, match="Needs your setting: Ratio spread"):
        plan_structure(OptionStrategy.CALL_RATIO_SPREAD, SignalDirection.LONG, rules, spot=24500.0, strikes_by_right={"CE": LADDER}, spread_width=2)
    with pytest.raises(ContractResolutionError, match="Needs your setting: Spread width"):
        plan_structure(OptionStrategy.BULL_PUT_SPREAD, SignalDirection.LONG, rules, spot=24500.0, strikes_by_right={"PE": LADDER}, spread_width=None)
    legs = plan_structure(OptionStrategy.CALL_RATIO_SPREAD, SignalDirection.LONG, ContractRules(kind="OPTION", ratio_shorts=3), spot=24500.0,
                          strikes_by_right={"CE": LADDER}, spread_width=2)
    assert [leg.ratio for leg in legs] == [1, 3]


# --- the backtest form (TS1-039) ------------------------------------------------------------------------------------------

def test_the_option_backtest_asks_or_takes_his_own():
    t = _fresh("m68o-bt")
    body = OptionBacktestBody(option_strategy="SINGLE")

    async def go(b):
        async with _session_factory() as session:
            await own_options(session, await session.get(User, t["user_id"]), b)
    with pytest.raises(HTTPException) as asked:
        _run(go(body))
    assert {d["loc"][-1] for d in asked.value.detail} == {"expiry_rule", "option_position", "strike_rule"}
    _save(t, expiry_rule="NEXT", option_position="BUY", strike_rule="ATM", risk_free_rate_pct=6.5)
    body = OptionBacktestBody(option_strategy="SINGLE")
    _run(go(body))
    cfg = body.to_config()
    assert (cfg.expiry_rule.value, cfg.option_position.value, cfg.strike_rule.value) == ("NEXT", "BUY", "ATM")
    assert cfg.risk_free_rate == pytest.approx(0.065)                           # TS1-100: his own rate


# --- chain, delivery warning, Greeks rate (TS1-044 / 045 / 100 / 102) ----------------------------------------------------

def test_an_expiry_day_opens_the_nearest_unless_he_turned_the_roll_on():
    t = _fresh("m68o-roll")

    async def roll():
        async with _session_factory() as session:
            return await chain_view.next_on_expiry_day(session, await session.get(User, t["user_id"]))
    assert _run(roll()) is False                                                # Not set = the nearest
    _save(t, expiry_day_roll=True)
    assert _run(roll()) is True
    today = date(2026, 10, 27)
    assert chain_view.default_expiry([today, date(2026, 11, 3)], today) == today


def test_the_delivery_warning_is_off_until_he_sets_it():
    t = _fresh("m68o-settle")

    async def settings():
        async with _session_factory() as session:
            return await smart_execution.settings_for(session, await session.get(User, t["user_id"]))
    s = _run(settings())
    assert (s.settlement_warning, s.settlement_days_before, s.settlement_band_pct, s.settlement_telegram) == (False, None, None, False)
    _save(t, settlement_warning=True, settlement_days_before=3, settlement_band_pct=1.5)
    s = _run(settings())
    assert (s.settlement_warning, s.settlement_days_before, s.settlement_band_pct) == (True, 3, 1.5)


def test_the_greeks_rate_is_his_own_else_the_platform_rate_and_the_answer_says_which():
    from app.core.config import RISK_FREE_RATE
    t = _fresh("m68o-rate")
    assert option_defaults.risk_free_rate(_load(t)) == RISK_FREE_RATE
    _save(t, risk_free_rate_pct=6.0)
    assert option_defaults.risk_free_rate(_load(t)) == pytest.approx(0.06)
    from app.options_builder.routes import EvaluateBody, _own_rate

    async def go(b):
        async with _session_factory() as session:
            return await _own_rate(session, await session.get(User, t["user_id"]), b)
    body = EvaluateBody.model_construct(rate=None)
    assert _run(go(body)) == "yours" and body.rate == pytest.approx(0.06)
    body = EvaluateBody.model_construct(rate=0.05)
    assert _run(go(body)) == "request" and body.rate == 0.05
    other = _fresh("m68o-rate2")

    async def go2(b):
        async with _session_factory() as session:
            return await _own_rate(session, await session.get(User, other["user_id"]), b)
    body = EvaluateBody.model_construct(rate=None)
    assert _run(go2(body)) == "platform" and body.rate == RISK_FREE_RATE


def test_the_builder_suggestion_needs_his_own_pop_and_distance():
    from app.options_builder.routes import SuggestBody
    fields = SuggestBody.model_fields
    assert fields["pop_threshold_pct"].default is None and fields["strikes_otm"].default is None
    for key in ("builder_pop_pct", "builder_strikes_otm"):
        assert ts.BY_KEY[f"options.{key}"].wired
    for key in ("finder_target_delta", "finder_premium_min", "finder_premium_max", "finder_oi_wall_strikes"):
        f = ts.BY_KEY[f"options.{key}"]
        assert not f.wired and f.rows == ("TS1-104",), key                       # Not set; nothing reads them yet


# --- the migration (spread_width: no pre-chosen 2; ratio_shorts kept 1:2 on the ratio spreads made before) ---------------

def _migration():
    path = BACKEND / "alembic/versions/c4e8a2f6b0d3_m68_options_not_set.py"
    spec = importlib.util.spec_from_file_location("m68_pr4_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_keeps_every_stored_width_and_goes_down():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    module = _migration()
    assert module.down_revision == "b4e6f8a0c2d5" and module.ROLLBACK_SAFE is True and module.ROLLBACK_SAFE_REASON
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE strategy_deployments (id INTEGER PRIMARY KEY, option_strategy VARCHAR(20) NOT NULL DEFAULT 'SINGLE', "
                             "spread_width INTEGER NOT NULL DEFAULT 2)"))
        conn.execute(sa.text("INSERT INTO strategy_deployments (id, option_strategy, spread_width) VALUES "
                             "(1, 'IRON_CONDOR', 3), (2, 'CALL_RATIO_SPREAD', 2), (3, 'SINGLE', 2)"))
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
        rows = conn.execute(sa.text("SELECT id, spread_width, ratio_shorts FROM strategy_deployments ORDER BY id")).all()
        assert [tuple(r) for r in rows] == [(1, 3, None), (2, 2, 2), (3, 2, None)]           # TS-6: kept; 1:2 kept
        conn.execute(sa.text("INSERT INTO strategy_deployments (id) VALUES (4)"))
        assert conn.execute(sa.text("SELECT spread_width FROM strategy_deployments WHERE id = 4")).scalar() is None
        with Operations.context(MigrationContext.configure(conn)):
            module.downgrade()
        rows = conn.execute(sa.text("SELECT id, spread_width FROM strategy_deployments ORDER BY id")).all()
        assert [tuple(r) for r in rows] == [(1, 3), (2, 2), (3, 2), (4, 2)]
        assert "ratio_shorts" not in {c["name"] for c in sa.inspect(conn).get_columns("strategy_deployments")}


def test_the_model_carries_no_default_width():
    col = StrategyDeploymentRecord.__table__.c["spread_width"]
    assert col.nullable and col.default is None and col.server_default is None
    assert StrategyDeploymentRecord.__table__.c["ratio_shorts"].nullable


def test_a_value_he_saved_that_no_longer_reads_is_dropped_not_guessed():
    t = _fresh("m68o-bad")

    async def plant():
        async with _session_factory() as session:
            session.add(UserPrefsRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], namespace="trading_defaults",
                                        value_json=json.dumps({"expiry_rule": "WEEKLY", "spread_width": 0}), version=1, updated_at=datetime.now(UTC)))
            await session.commit()
    _run(plant())
    own = _load(t)
    assert own.expiry_rule is None and own.spread_width is None


def test_a_set_but_unreadable_trade_setting_asks_and_never_takes_the_older_store(monkeypatch):
    """Review (#843): strike offset, spread width and both delivery-warning numbers read alike - a Trade-settings value
    that is set but does not read (e.g. -1) is None (he is asked); the older store's value counts only when Not set."""
    t = _fresh("m68o-unread")

    async def plant():
        async with _session_factory() as session:
            now = datetime.now(UTC)
            for namespace, value in (
                    (ts.NAMESPACE, {"values": {"options.strike_offset": -1, "options.spread_width": -1,
                                               "options.settlement_days_before": -1, "options.settlement_band_pct": -1}}),
                    ("trading_defaults", {"strike_offset": 2, "spread_width": 5}),
                    ("smart_execution", {"settlement_days_before": 3, "settlement_band_pct": 1.5})):
                session.add(UserPrefsRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], namespace=namespace,
                                            value_json=json.dumps(value), version=1, updated_at=now))
            await session.commit()
    _run(plant())
    monkeypatch.setattr(option_defaults.trade_settings, "clean", lambda v: dict(v) if isinstance(v, dict) else {})
    own = _load(t)
    assert (own.strike_offset, own.spread_width, own.settlement_days_before, own.settlement_band_pct) == (None, None, None, None)
    monkeypatch.setattr(option_defaults.trade_settings, "clean", lambda v: {})          # Not set: the older store counts
    own = _load(t)
    assert (own.strike_offset, own.spread_width, own.settlement_days_before, own.settlement_band_pct) == (2, 5, 3, 1.5)
