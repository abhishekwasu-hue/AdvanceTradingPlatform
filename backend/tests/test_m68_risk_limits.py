"""M68 PR-5 (owner order M68, Risk limits; TS1-041, TS1-048 to 059): ATP pre-chooses no risk limit, ceiling or pause.

Owner decisions on their provisional defaults: D2 (M68Q-2) no platform ceilings unless an admin sets them (for every
organisation or one team); D3 (M68Q-3) opposite positions Not set = allowed. A limit blocks NEW entries only when the user
set it; exits are never checked (ADR-0004). Existing rows keep their saved values (TS-6).
"""
from __future__ import annotations

import asyncio
import importlib.util
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.enums import InstrumentKind, OptionPosition, OptionStrategy
from app.core.models import RiskConfig
from app.db.models import PlatformControlRecord, RiskSettingsRecord, StrategyDeploymentRecord, TradeRecord, User
from app.execution import contract_execution
from app.instruments.spreads import can_hold_for_days
from app.me import trade_settings as ts
from app.paper import experience
from app.platform import controls
from app.risk_engine import by_default, guardian, own_risk, risk_limits, vix_gate
from app.risk_engine.routes import _stored_config
from tests.test_auth_api import _register, _session_factory, client

BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "alembic" / "versions" / "aaa362c38a32_m68_risk_limits_not_set.py"


def _run(coro):
    return asyncio.run(coro)


def _me(tag: str) -> dict:
    token = _register(f"m68pr5-{tag}-{uuid.uuid4().hex[:6]}@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/auth/me", headers=headers).json()
    return {"headers": headers, "tenant_id": me["tenant_id"], "user_id": me["id"]}


def _limits(me: dict, body: dict):
    return client.put("/api/risk-settings/limits", headers=me["headers"], json=body)


def _own(me: dict, values: dict) -> dict:
    out = client.put("/api/me/trade-settings", headers=me["headers"], json={"values": values})
    assert out.status_code == 200, out.text
    return out.json()


# --- migration (TS1-051 / 052 / 053 / 054) -------------------------------------------------------------------------------

def test_migration_keeps_saved_values_and_is_rollback_safe_with_a_reason():
    from scripts.migration_rollback_safety import lint
    spec = importlib.util.spec_from_file_location("m68_pr5_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "aaa362c38a32" and module.ROLLBACK_SAFE is True and module.ROLLBACK_SAFE_REASON
    assert not [p for p in lint() if p.startswith(MIGRATION.name)]
    source = MIGRATION.read_text(encoding="utf-8")
    # TS-6: no saved pause / opposite-positions value is rewritten on the way up; only the 0 sentinel and the new cut
    upgrade = source[source.index("def upgrade"):source.index("def downgrade")]
    assert "loss_streak_pause =" not in upgrade and "opposite_positions =" not in upgrade
    assert "SET min_risk_reward = NULL WHERE min_risk_reward <= 0" in upgrade and "SET dd_cut_pct = 50 WHERE dd_level_1_pct IS NOT NULL" in upgrade
    downgrade = source[source.index("def downgrade"):]
    assert "'next_session' WHERE loss_streak_pause IS NULL" in downgrade and "'deny' WHERE opposite_positions IS NULL" in downgrade
    table = RiskSettingsRecord.__table__
    for name in ("loss_streak_pause", "opposite_positions", "min_risk_reward", "dd_cut_pct"):
        column = table.c[name]
        assert column.nullable and column.server_default is None and (column.default is None or column.default.arg is None), name


def test_a_saved_row_keeps_its_deny_and_pause_and_a_new_row_is_not_set():
    me = _me("ts6")
    assert _limits(me, {"checks": {}, "opposite_positions": "deny", "loss_streak_pause": "until_resume"}).status_code == 200

    async def stored():
        async with _session_factory() as session:
            return await session.scalar(select(RiskSettingsRecord).where(RiskSettingsRecord.tenant_id == me["tenant_id"]))
    row = _run(stored())
    assert (row.opposite_positions, row.loss_streak_pause) == ("deny", "until_resume")
    cfg = _stored_config(row)
    assert cfg.opposite_positions == "deny" and cfg.loss_streak_pause == "until_resume"
    # Saving other checks keeps both choices exactly
    view = _limits(me, {"checks": {"max_open_positions": {"enabled": True, "values": {"max_open_positions": 3}}}}).json()
    assert view["opposite_positions"] == "deny" and view["loss_streak_pause"] == "until_resume"
    fresh = _me("fresh")
    view = client.get("/api/risk-settings/limits", headers=fresh["headers"]).json()
    assert view["opposite_positions"] is None and view["loss_streak_pause"] is None
    assert RiskConfig().opposite_positions is None and RiskConfig().loss_streak_pause is None and RiskConfig().dd_cut_pct is None


# --- TS1-051: the pause end is asked ---------------------------------------------------------------------------------------

def test_losses_in_a_row_with_no_minutes_asks_when_the_pause_ends():
    me = _me("pause")
    on = {"checks": {"loss_cooldown": {"enabled": True, "values": {"max_consecutive_losses": 3}}}}
    refused = _limits(me, on)
    assert refused.status_code == 422 and refused.json()["detail"] == risk_limits.PAUSE_NEEDED
    assert _limits(me, {**on, "loss_streak_pause": "next_session"}).json()["loss_streak_pause"] == "next_session"
    # with his own minutes the pause end is not needed
    other = _me("pause-min")
    minutes = {"checks": {"loss_cooldown": {"enabled": True, "values": {"max_consecutive_losses": 3, "loss_cooldown_minutes": 30}}}}
    ok = _limits(other, minutes)
    assert ok.status_code == 200 and ok.json()["loss_streak_pause"] is None
    assert _limits(other, {"checks": {}, "loss_streak_pause": "sometime"}).status_code == 422
    # the older Risk page asks too
    old = client.put("/api/risk-settings", headers=_me("pause-old")["headers"], json={"max_consecutive_losses": 3})
    assert old.status_code == 400 and old.json()["detail"] == risk_limits.PAUSE_NEEDED


# --- TS1-052 (D3): opposite positions Not set = allowed --------------------------------------------------------------------

def test_opposite_positions_not_set_is_allowed_and_only_a_saved_deny_holds_an_entry():
    from app.workers.trading_worker import TradingWorker
    me = _me("opposite")

    async def setup():
        async with _session_factory() as session:
            deps = []
            for strategy in ("ema_rsi_scalper_1m", "supertrend_5m"):
                dep = StrategyDeploymentRecord(tenant_id=me["tenant_id"], strategy_id=strategy, symbol="RELIANCE", exchange="NSE",
                                               timeframe="1min", mode="PAPER", broker_name="upstox", status="ACTIVE", created_by=me["user_id"],
                                               holding="INTRADAY", order_style="MARKET")
                session.add(dep)
                deps.append(dep)
            await session.flush()
            session.add(TradeRecord(tenant_id=me["tenant_id"], user_id=me["user_id"], strategy_id="ema_rsi_scalper_1m", symbol="RELIANCE", direction="LONG",
                                    entry_time=datetime.now(UTC) - timedelta(minutes=5), entry_price=100.0, quantity=1.0, stop_loss=95.0,
                                    mode="PAPER", deployment_id=deps[0].id, exchange="NSE"))
            await session.commit()
            return deps[1].id

    other = _run(setup())

    async def reason():
        async with _session_factory() as session:
            dep = await session.get(StrategyDeploymentRecord, other)
            return await TradingWorker._opposite_position(SimpleNamespace(), session, dep, "SHORT")
    assert _run(reason()) is None                                    # no row saved: Not set = allowed
    assert _limits(me, {"checks": {}}).json()["opposite_positions"] is None
    assert _run(reason()) is None                                    # a row, the choice still Not set
    assert _limits(me, {"checks": {}, "opposite_positions": "deny"}).json()["opposite_positions"] == "deny"
    assert "another of your bots already holds a long position on RELIANCE" in (_run(reason()) or "")
    assert _limits(me, {"checks": {}, "opposite_positions": None}).json()["opposite_positions"] is None
    assert _run(reason()) is None
    assert _limits(me, {"checks": {}, "opposite_positions": "maybe"}).status_code == 422


# --- TS1-053 / TS1-050: no 0 sentinel stored, no ATP caps on a user's limit ------------------------------------------------

def test_input_bounds_are_only_finite_and_positive_and_min_rr_not_set_is_null():
    me = _me("bounds")
    body = {"checks": {"max_lots_per_order": {"enabled": True, "values": {"max_lots_per_order": 250_000}},
                       "max_open_positions": {"enabled": True, "values": {"max_open_positions": 500}},
                       "max_entries_per_day": {"enabled": True, "values": {"max_trades_per_day": 2_000}},
                       "min_reward_risk": {"enabled": True, "values": {"min_risk_reward": 25}},
                       "loss_cooldown": {"enabled": True, "values": {"max_consecutive_losses": 80, "loss_cooldown_minutes": 5_000}},
                       "risk_sizing": {"enabled": True, "values": {"risk_per_trade_pct": 0.005}}}}
    saved = _limits(me, body)
    assert saved.status_code == 200, saved.text
    values = {f["key"]: f["value"] for c in saved.json()["checks"] for f in c["fields"]}
    assert values["max_lots_per_order"] == 250_000 and values["min_risk_reward"] == 25 and values["loss_cooldown_minutes"] == 5_000
    zero = _limits(me, {"checks": {"min_reward_risk": {"enabled": True, "values": {"min_risk_reward": 0}}}})
    assert zero.status_code == 422 and "more than 0" in zero.json()["detail"]
    assert _limits(me, {"checks": {"daily_loss": {"enabled": True, "values": {"max_daily_loss_pct": 101}}}}).status_code == 422
    _limits(me, {"checks": {"min_reward_risk": {"enabled": False, "values": {"min_risk_reward": None}}}})

    async def stored():
        async with _session_factory() as session:
            return await session.scalar(select(RiskSettingsRecord.min_risk_reward).where(RiskSettingsRecord.tenant_id == me["tenant_id"]))
    assert _run(stored()) is None                                    # TS1-053: not set is NULL, not 0
    view = client.get("/api/risk-settings/limits", headers=me["headers"]).json()
    field = next(f for c in view["checks"] for f in c["fields"] if f["key"] == "min_risk_reward")
    assert field["min"] == 0 and field["min_open"] is True and field["max"] == risk_limits.BIG
    # the paper ticket's own bounds (TS1-049): no 5 % / 20 % / 500 / 10 x caps
    assert by_default.validate_settings({"risk_per_trade_pct": 8.0, "daily_loss_pct": 40.0, "max_trades_per_day": 900, "min_rr": 15.0}, {}) == []
    assert by_default.validate_settings({"risk_per_trade_pct": 0.0}, {}) and by_default.validate_settings({"daily_loss_pct": 100.5}, {})


# --- TS1-054: the first drawdown step's cut is his own ----------------------------------------------------------------------

def test_drawdown_cut_is_his_own_and_asked_with_the_first_step():
    me = _me("dd")
    step = {"checks": {"drawdown": {"enabled": True, "values": {"dd_level_1_pct": 5}}}}
    refused = _limits(me, step)
    assert refused.status_code == 422 and refused.json()["detail"] == risk_limits.DD_CUT_NEEDED
    saved = _limits(me, {"checks": {"drawdown": {"enabled": True, "values": {"dd_level_1_pct": 5, "dd_cut_pct": 30}}}})
    assert saved.status_code == 200
    cfg = client.get("/api/risk-settings", headers=me["headers"]).json()
    assert cfg["dd_level_1_pct"] == 5 and cfg["dd_cut_pct"] == 30
    assert guardian.drawdown_cut(RiskConfig(dd_cut_pct=30)) == pytest.approx(0.3)
    assert guardian.drawdown_cut(RiskConfig()) == 0.0 and guardian.drawdown_cut(RiskConfig(dd_cut_pct=0)) == 0.0
    assert not hasattr(guardian, "DRAWDOWN_CUT_MULTIPLIER")
    # the pause step alone needs no cut
    assert _limits(_me("dd-pause"), {"checks": {"drawdown": {"enabled": True, "values": {"dd_level_2_pct": 10}}}}).status_code == 200


# --- TS1-048 / 059 (D2): no ceiling unless an admin sets one -----------------------------------------------------------------

def test_no_ceiling_by_default_a_legacy_row_reads_as_none_and_a_team_ceiling_applies_to_that_team_only():
    me, other = _me("ceil-a"), _me("ceil-b")
    assert client.get("/api/risk-settings/ceilings", headers=me["headers"]).json() == {}
    assert controls._ceiling_values({"risk_per_trade_pct": 2.0, "max_daily_loss_pct": 5.0, "max_portfolio_risk_pct": 10.0,
                                     "dd_level_2_pct": 25.0, "min_stop_cooldown_minutes": 0.0}) == {}      # an old merged save
    assert controls._ceiling_values({"risk_per_trade_pct": 1.5, "dd_level_2_pct": 25.0}) == {"risk_per_trade_pct": 1.5}
    assert controls._ceiling_values({"m68": True, "values": {"risk_per_trade_pct": 2.0}}) == {"risk_per_trade_pct": 2.0}

    async def team(values, tenant_id):
        async with _session_factory() as session:
            return await controls.set_risk_ceilings(session, await session.get(User, me["user_id"]), values, tenant_id)
    shown = _run(team({"risk_per_trade_pct": 1.0, "warn_at_pct": 90}, me["tenant_id"]))
    assert shown["risk_per_trade_pct"] == 1.0 and shown["warn_at_pct"] == 90 and shown["max_daily_loss_pct"] is None
    try:
        assert client.get("/api/risk-settings/ceilings", headers=me["headers"]).json() == {"risk_per_trade_pct": 1.0, "warn_at_pct": 90.0}
        assert client.get("/api/risk-settings/ceilings", headers=other["headers"]).json() == {}
        capped = _limits(me, {"checks": {"risk_sizing": {"enabled": True, "values": {"risk_per_trade_pct": 2}}}})
        assert capped.status_code == 422 and "Ask your admin" in capped.json()["detail"]
        assert _limits(other, {"checks": {"risk_sizing": {"enabled": True, "values": {"risk_per_trade_pct": 2}}}}).status_code == 200
        # a member sees his team's ceilings read-only on Trade settings
        fields = {f["key"]: f for s in client.get("/api/me/trade-settings", headers=me["headers"]).json()["sections"] for f in s["fields"]}
        assert fields["risk_limits.ceiling_risk_per_trade_pct"]["value"] == 1.0 and fields["risk_limits.ceiling_risk_per_trade_pct"]["saved"]
        assert fields["risk_limits.ceiling_daily_loss_pct"]["value"] is None
        refused = client.put("/api/me/trade-settings", headers=me["headers"], json={"values": {"risk_limits.ceiling_risk_per_trade_pct": 5}})
        assert refused.status_code == 422
        with pytest.raises(ValueError):
            _run(team({"warn_at_pct": 120}, me["tenant_id"]))
        with pytest.raises(ValueError):
            _run(team({"risk_per_trade_pct": 0}, me["tenant_id"]))
    finally:
        _run(team({"risk_per_trade_pct": None, "warn_at_pct": None}, me["tenant_id"]))
    assert client.get("/api/risk-settings/ceilings", headers=me["headers"]).json() == {}

    async def row():
        async with _session_factory() as session:
            return await session.get(PlatformControlRecord, f"risk_ceilings:tenant:{me['tenant_id']}")
    assert '"m68": true' in _run(row()).value_json


def test_platform_wide_and_team_ceilings_merge_to_the_stricter():
    async def go():
        async with _session_factory() as session:
            await controls._set(session, controls.KEY_RISK_CEILINGS, {"m68": True, "values": {"risk_per_trade_pct": 3.0,
                                                                                               "min_stop_cooldown_minutes": 10}}, None)
            await controls._set(session, "risk_ceilings:tenant:999999", {"m68": True, "values": {"risk_per_trade_pct": 4.0,
                                                                                                  "min_stop_cooldown_minutes": 20}}, None)
            await session.commit()
            try:
                return await controls.risk_ceilings(session, 999999), await controls.risk_ceilings(session)
            finally:
                await controls._set(session, controls.KEY_RISK_CEILINGS, {"m68": True, "values": {}}, None)
                await controls._set(session, "risk_ceilings:tenant:999999", {"m68": True, "values": {}}, None)
                await session.commit()
    team, every = _run(go())
    assert team == {"risk_per_trade_pct": 3.0, "min_stop_cooldown_minutes": 20.0} and every == {"risk_per_trade_pct": 3.0,
                                                                                              "min_stop_cooldown_minutes": 10.0}
    assert controls.ceiling_violations(RiskConfig(risk_per_trade_pct=50.0, stop_cooldown_minutes=0), {}) == []


# --- TS1-055: no 80 % margin share ------------------------------------------------------------------------------------------

def test_no_config_plans_against_the_whole_available_margin():
    assert contract_execution.MARGIN_SAFETY == 1.0 and RiskConfig().margin_share() == 1.0


# --- TS1-041: written straddles / strangles overnight only with his switch ---------------------------------------------------

def test_written_straddles_are_held_for_days_only_with_the_users_switch():
    assert not can_hold_for_days(OptionStrategy.SHORT_STRADDLE) and can_hold_for_days(OptionStrategy.IRON_CONDOR)
    assert can_hold_for_days(OptionStrategy.SHORT_STRANGLE, written_overnight=True)
    assert not can_hold_for_days(OptionStrategy.SINGLE, written_overnight=True)
    from app.deployments.routes import _check_holding
    request = SimpleNamespace(holding="SWING", instrument_kind=InstrumentKind.OPTION, option_strategy=OptionStrategy.SHORT_STRADDLE,
                              option_position=OptionPosition.WRITE, timeframe="5min")
    strategy = SimpleNamespace(name="x", timeframes=["5min"])
    with pytest.raises(HTTPException) as refused:
        _check_holding(request, strategy)
    assert "Needs your setting: Hold written straddles and strangles overnight" in refused.value.detail
    _check_holding(request, strategy, written_overnight=True)          # his switch on: held for days like a spread
    me = _me("overnight")
    assert not _run(_load(me)).overnight_short_options
    _own(me, {"risk_limits.overnight_short_options": True})
    assert _run(_load(me)).overnight_short_options


async def _load(me):
    async with _session_factory() as session:
        return await own_risk.load(session, me["tenant_id"], me["user_id"])


# --- TS1-056: the India VIX check is his own ---------------------------------------------------------------------------------

def test_vix_check_not_set_pauses_nothing_and_a_missing_number_turns_only_that_part_off():
    now = datetime(2026, 10, 12, 11, 0, tzinfo=UTC)          # after the check time IST
    spike = [{"last": 15.0, "prev_close": 13.0, "captured_at": now - timedelta(minutes=5), "error": None}]
    common = {"symbol": "NIFTY", "direction": "LONG", "now": now}
    off = vix_gate._evaluate(spike, own=own_risk.OwnRisk(), **common)
    assert off.reasons == [] and off.size_multiplier == 1.0
    mine = own_risk.OwnRisk(vix_gate=True, vix_change_pct=5.0)
    assert vix_gate._evaluate(spike, own=mine, **common).state == "spike"
    no_rise = own_risk.OwnRisk(vix_gate=True, vix_level=14.0, vix_size_cut_pct=40.0)
    verdict = vix_gate._evaluate(spike, own=no_rise, **common)
    assert verdict.reasons == [] and verdict.size_multiplier == pytest.approx(0.6)
    # with no reading at all and no rise % of his own there is no fail-safe pause either
    assert vix_gate._evaluate([], own=no_rise, **common).reasons == []
    me = _me("vix")
    assert _run(_check(me)).state == "off"                         # his switch Not set
    _own(me, {"risk_limits.vix_gate": True, "risk_limits.vix_change_pct": 5.0})
    assert vix_gate.OFF_NOTE in _run(_check(me)).notes             # readings not captured for his organisation (flag off)


async def _check(me):
    async with _session_factory() as session:
        return await vix_gate.check(session, me["tenant_id"], symbol="NIFTY", direction="LONG", user_id=me["user_id"])


# --- TS1-057: experience thresholds are his own -------------------------------------------------------------------------------

def test_the_advanced_level_counts_his_own_numbers_and_names_the_missing_one():
    assert experience.derive_level(500, 300, 1.0)[0] != "ADVANCED"
    text = experience.refusal_text("naked_short_options", "INTERMEDIATE", {"closed_trades": 5, "sessions": 2}, own_risk.OwnRisk())
    assert "Needs your setting: Experience level: trades" in text
    text = experience.refusal_text("naked_short_options", "INTERMEDIATE", {"closed_trades": 5, "sessions": 2},
                                   own_risk.OwnRisk(experience_trades=50))
    assert "Needs your setting: Experience level: sessions" in text
    from app.core import config as app_config
    assert not hasattr(app_config, "EXPERIENCE_ADVANCED_MIN_TRADES") and not hasattr(app_config, "VIX_GATE_SPIKE_PCT")


# --- TS1-058: a market event's action is asked ---------------------------------------------------------------------------------

def test_a_market_event_needs_its_own_action():
    me = _me("event")
    day = datetime.now(UTC).date().isoformat()
    missing = client.post("/api/risk-guardian/events", headers=me["headers"], json={"event_date": day, "kind": "OTHER"})
    assert missing.status_code == 422
    made = client.post("/api/risk-guardian/events", headers=me["headers"], json={"event_date": day, "kind": "OTHER", "action": "BLOCK"})
    assert made.status_code == 201 and made.json()["action"] == "BLOCK"
    client.delete(f"/api/risk-guardian/events/{made.json()['id']}", headers=me["headers"])
    # his saved starting choice for the form
    _own(me, {"risk_limits.event_default_action": "SIZE_CUT"})
    assert _run(_load(me)).event_default_action == "SIZE_CUT"
    assert {c for c, _ in ts.BY_KEY["risk_limits.event_default_action"].choices} == {"BLOCK", "SIZE_CUT"}


# --- registry ------------------------------------------------------------------------------------------------------------------

def test_the_risk_limits_section_is_wired_and_every_row_has_a_home():
    rows = {f"TS1-{n:03d}" for n in (41, *range(48, 60))}
    homes = {r for f in ts.FIELDS for r in f.rows} | set(ts.ON_RISK_LIMITS_PAGE)
    assert rows - {"TS1-055"} <= homes                                 # TS1-055 is a delete (the fallback is the whole margin)
    risk = [f for f in ts.FIELDS if f.section == "risk_limits" and not f.fact]
    assert risk and all(f.wired for f in risk)
    assert all(f.key in ts.CEILING_OF for f in risk if f.scope == "organisation")
    me = _me("registry")
    keys = {f["key"] for s in client.get("/api/me/trade-settings", headers=me["headers"]).json()["sections"] for f in s["fields"]}
    assert {"risk_limits.vix_gate", "risk_limits.overnight_short_options", "risk_limits.experience_trades"} <= keys
