"""M64 (owner order 30 Oct + amendment): Settings > Risk limits - every order-time risk check a switch, OFF by default.

RM-6: each check allows / blocks / resizes with its message text; with every check off the typed lots are placed; a
defined-risk credit spread passes risk-based sizing; an undefined-risk short with no stop falls back to the typed lots
with a warning; the freeze quantity splits, never refuses; a daily-loss hit stops entries but never an exit (ADR-0004);
the switches page round-trips; the migration keeps what the trader saved ON and switches the rest OFF, and goes down.
"""
import asyncio
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import select

from app.core.enums import AssetClass, SignalDirection, SignalGrade
from app.core.models import RiskConfig, Signal
from app.db.models import RiskEventRecord, RiskSettingsRecord, TradeRecord, User
from app.execution.contract_execution import written_lot_cap
from app.execution.signal_execution import execute_signal_for_user
from app.execution.smart_execution import slice_units
from app.instruments.models import ContractSpec
from app.risk_engine import risk_limits, risk_manager
from app.risk_engine.risk_manager import RiskManager, TradingDayState
from app.trading.persistence import build_trading_day_state
from tests.test_auth_api import _register, _session_factory, client

BACKEND = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 30, 6, 0, tzinfo=UTC)
LOT = 75
SPEC = ContractSpec(symbol="NIFTY OPT", exchange="NFO", asset_class=AssetClass.INDEX_OPTION, description="test", lot_size=float(LOT),
                    tick_size=0.05)


def _run(coro):
    return asyncio.run(coro)


def _signal(entry=100.0, stop=90.0, direction=SignalDirection.LONG, rr=None) -> Signal:
    return Signal(symbol="NIFTY OPT", strategy_id="m64", strategy_name="m64", direction=direction, timestamp=NOW, entry=entry,
                  stop_loss=stop, target1=None, risk_reward=rr, score=0, grade=SignalGrade.VALID, reasons=[], timeframe_combo="manual")


# M68 PR-2: every entry reaching the risk engine carries the trader's own size (one lot here unless a test names one).
def _decide(cfg: RiskConfig, state: TradingDayState | None = None, *, lots: float | None = 1, signal: Signal | None = None):
    return RiskManager(cfg).validate_and_size(signal or _signal(), state or TradingDayState(), contract_spec=SPEC,
                                              requested_quantity=lots * LOT if lots else None)


# --- every check: allow / block / resize, with the message ---------------------------------------------------------------

def test_with_every_check_off_the_typed_lots_are_placed():
    assert _decide(RiskConfig(), lots=7).quantity == 7 * LOT
    unsized = _decide(RiskConfig(), lots=None)                                     # M68 PR-2: nothing typed - not placed
    assert not unsized.approved and unsized.reasons == [risk_manager.NEEDS_LOTS]
    # values stored but switched off read as not set
    stored = RiskConfig(capital=100_000, risk_per_trade_pct=0.5, max_open_positions=1, max_trades_per_day=1, max_lots_per_order=2,
                        min_risk_reward=3.0, max_daily_loss_pct=1.0, max_consecutive_losses=1, margin_safety_pct=50.0)
    off = risk_limits.apply_switches(stored, [])
    busy = TradingDayState(trades_today=9, open_positions=9, consecutive_losses=9, daily_pnl=-50_000)
    decision = _decide(off, busy, lots=7, signal=_signal(rr=0.5))
    assert decision.approved and decision.quantity == 7 * LOT and off.margin_share() == 1.0 and not off.event_rules


def test_max_lots_per_order_offers_the_smaller_size():
    cfg = RiskConfig(max_lots_per_order=5)
    assert _decide(cfg, lots=5).approved
    refused = _decide(cfg, lots=8)
    assert not refused.approved and refused.reasons == ["Max lots per order is 5 - this order is 8 lots. Place 5 lots?"]
    item = refused.blocks[0]
    assert item["smaller_lots"] == 5 and item["link"] == "/settings/risk-limits#max_lots_per_order" and item["link_text"] == "Change limit"
    # a size risk-based sizing worked out is lowered to it, said in a note
    sized = _decide(RiskConfig(capital=10_000_000, risk_per_trade_pct=1.0, max_lots_per_order=3))   # 1,00,000 / 750 = 133 lots
    assert sized.approved and sized.quantity == 3 * LOT and "your max lots per order is 3" in sized.warnings[0]


def test_max_open_positions():
    cfg = RiskConfig(max_open_positions=3)
    assert _decide(cfg, TradingDayState(open_positions=2)).approved
    refused = _decide(cfg, TradingDayState(open_positions=3))
    assert refused.reasons == ["You hold 3 open positions; your limit is 3. Exit one before a new entry."]


def test_daily_loss_limit_percent_and_rupees_smaller_applies():
    cfg = RiskConfig(capital=100_000, max_daily_loss_pct=2.0, daily_loss_rupees=1_500)
    assert cfg.daily_loss_amount() == 1_500
    assert _decide(cfg, TradingDayState(daily_pnl=-1_499)).approved
    refused = _decide(cfg, TradingDayState(daily_pnl=-1_500))
    assert refused.reasons == ["Today's loss ₹1,500 has reached your daily loss limit of ₹1,500. No new entries today; exits still work."]
    assert RiskConfig(capital=100_000, max_daily_loss_pct=2.0).daily_loss_amount() == 2_000


def test_max_entries_per_day():
    cfg = RiskConfig(max_trades_per_day=10)
    assert _decide(cfg, TradingDayState(trades_today=9)).approved
    assert _decide(cfg, TradingDayState(trades_today=10)).reasons == [
        "You made 10 new entries today; your limit is 10. No new entries today; exits still work."]


def test_consecutive_loss_cooldown_in_minutes_ends():
    cfg = RiskConfig(max_consecutive_losses=3, loss_cooldown_minutes=30)
    last = NOW - timedelta(minutes=10)
    paused = _decide(cfg, TradingDayState(consecutive_losses=3, last_loss_at=last, as_of=NOW))
    assert not paused.approved and paused.reasons[0].startswith("3 losses in a row (your limit is 3): new entries pause for 30 minutes, until ")
    assert _decide(cfg, TradingDayState(consecutive_losses=3, last_loss_at=NOW - timedelta(minutes=31), as_of=NOW)).approved
    assert _decide(cfg, TradingDayState(consecutive_losses=2, last_loss_at=last, as_of=NOW)).approved
    # with no minutes the pause ends at the next session, as before
    assert "until the next session" in _decide(RiskConfig(max_consecutive_losses=3), TradingDayState(consecutive_losses=3)).reasons[0]


def test_minimum_reward_risk_only_when_set():
    assert _decide(RiskConfig(), signal=_signal(rr=0.3)).approved                  # off: no R:R filter at all
    refused = _decide(RiskConfig(min_risk_reward=1.5), signal=_signal(rr=1.2))
    assert refused.reasons == ["This trade's reward:risk is 1.20; your minimum is 1.50."]
    assert _decide(RiskConfig(min_risk_reward=1.5), signal=_signal(rr=1.5)).approved


class _MarginBroker:
    name = "fake"

    async def get_order_margin(self, order):
        return 10_000.0

    async def get_margins(self):
        class M:
            available_margin = 50_000.0
            available_cash = 50_000.0
        return M()


def test_margin_safety_share_of_available_margin():
    contract = type("C", (), {"instrument_key": "", "tradingsymbol": "NIFTY CE", "exchange": "NFO", "lot_size": LOT})()
    assert RiskConfig().margin_share() == 1.0 and RiskConfig(margin_safety_pct=80.0).margin_share() == 0.8
    lots, _ = _run(written_lot_cap(_MarginBroker(), contract, RiskConfig().margin_share(), product="MIS"))          # off: all 50,000 -> 5 lots
    assert lots == 5
    lots, _ = _run(written_lot_cap(_MarginBroker(), contract, RiskConfig(margin_safety_pct=80.0).margin_share(), product="MIS"))   # 40,000 -> 4
    assert lots == 4


def test_risk_based_sizing_sizes_blocks_below_one_lot_and_never_refuses_a_short_with_no_stop():
    cfg = RiskConfig(capital=1_000_000, risk_per_trade_pct=1.0)                  # 10,000 at risk; 10 x 75 = 750 a lot
    assert _decide(cfg).quantity == 13 * LOT
    small = _decide(RiskConfig(capital=100_000, risk_per_trade_pct=0.5))         # 500 < 750
    assert small.reasons == ["Your risk per trade of ₹500 is less than one lot's risk of ₹750 at this stop-loss. Set a closer "
                             "stop-loss, or change the limit."]
    assert small.blocks[0]["link"] == "/settings/risk-limits#risk_sizing"
    naked = _decide(cfg, lots=2, signal=_signal(entry=120.0, stop=0.0, direction=SignalDirection.SHORT))
    assert naked.approved and naked.quantity == 2 * LOT and naked.warnings[0].startswith("No stop-loss is set, so risk-based sizing")
    rupees = RiskConfig(capital=1_000_000, risk_per_trade_pct=1.0, risk_per_trade_rupees=1_500)  # the smaller: 1,500 -> 2 lots
    assert _decide(rupees).quantity == 2 * LOT


def test_a_defined_risk_credit_spread_is_sized_on_its_max_loss_not_as_a_naked_short():
    """RM-4: the structure's synthetic signal (as app/execution/multileg.py builds it) - entry = credit, stop = credit + max
    loss - is defined-risk and never reads as a short with no stop."""
    credit, max_loss = 20.0, 80.0
    sizing = _signal(entry=credit, stop=credit + max_loss, direction=SignalDirection.SHORT)
    decision = _decide(RiskConfig(capital=1_000_000, risk_per_trade_pct=1.0), signal=sizing)   # 10,000 / (80 x 75) = 1 lot
    assert decision.approved and decision.quantity == LOT and not decision.warnings


def test_the_freeze_quantity_splits_an_order_and_never_refuses():
    assert slice_units(30 * LOT, LOT, 1800) == [1725, 525]                       # whole lots below the freeze quantity
    assert _decide(RiskConfig(), lots=30).quantity == 30 * LOT                    # no cap of the platform's own on the size


# --- the page, the engine's reading of the switches, the journal's record ------------------------------------------------

def _me(email: str) -> dict:
    token = _register(email)
    headers = {"Authorization": f"Bearer {token}"}
    return {"headers": headers, **client.get("/api/auth/me", headers=headers).json()}


def test_risk_limits_page_round_trip_switches_off_by_default():
    me = _me("m64-page@example.com")
    view = client.get("/api/risk-settings/limits", headers=me["headers"]).json()
    ids = [c["id"] for c in view["checks"]]
    assert ids[:8] == ["max_lots_per_order", "max_open_positions", "daily_loss", "max_entries_per_day", "loss_cooldown", "min_reward_risk",
                       "margin_safety", "risk_sizing"]
    assert view["on"] == 0 and not any(c["enabled"] for c in view["checks"]) and all(c["help"] for c in view["checks"])
    # switching on needs a value
    bad = client.put("/api/risk-settings/limits", headers=me["headers"], json={"checks": {"max_open_positions": {"enabled": True}}})
    assert bad.status_code == 422 and bad.json()["detail"] == "Max open positions: enter a value to switch it on."
    ok = client.put("/api/risk-settings/limits", headers=me["headers"], json={"checks": {
        "max_open_positions": {"enabled": True, "values": {"max_open_positions": 3}},
        "daily_loss": {"enabled": True, "values": {"daily_loss_rupees": 2000}}}})
    assert ok.status_code == 200, ok.text
    on = {c["id"]: c for c in ok.json()["checks"]}
    assert on["max_open_positions"]["enabled"] and on["daily_loss"]["enabled"] and ok.json()["on"] == 2
    # switched off keeps its value (back on restores it) and the engine reads it as not set
    off = client.put("/api/risk-settings/limits", headers=me["headers"], json={"checks": {"max_open_positions": {"enabled": False}}}).json()
    row = {c["id"]: c for c in off["checks"]}["max_open_positions"]
    assert not row["enabled"] and row["saved"] and row["fields"][0]["value"] == 3

    async def cfg():
        from app.risk_engine.routes import get_tenant_risk_config
        async with _session_factory() as session:
            return await get_tenant_risk_config(me["tenant_id"], session)
    engine_cfg = _run(cfg())
    assert engine_cfg.max_open_positions is None and engine_cfg.daily_loss_rupees == 2000


def test_the_older_my_limits_save_switches_its_checks_on_and_off():
    me = _me("m64-old-put@example.com")
    body = {**client.get("/api/risk-settings", headers=me["headers"]).json(), "max_trades_per_day": 5}
    assert client.put("/api/risk-settings", headers=me["headers"], json=body).status_code == 200
    view = {c["id"]: c for c in client.get("/api/risk-settings/limits", headers=me["headers"]).json()["checks"]}
    assert view["max_entries_per_day"]["enabled"] and view["max_entries_per_day"]["saved"] and not view["max_open_positions"]["enabled"]


def _tenant_user(email: str) -> dict:
    from tests.test_trading_worker import _tenant
    return _tenant(email)


def _entry(t: dict, key: str, lots: float = 1):
    from tests.test_live_execution import _signal as live_signal

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            result, order = await execute_signal_for_user(session, user, mode="PAPER", strategy_id="ema_rsi_scalper_1m", signal=live_signal(),
                                                          idempotency_key=key, max_quantity=lots, holding="INTRADAY", order_style="MARKET")
            trade = await session.get(TradeRecord, order.trade_id) if order.trade_id else None
            return result, order, trade
    return _run(go())


def test_all_off_path_places_the_typed_size_through_the_pipeline():
    t = _tenant_user("m64-all-off@example.com")
    result, order, trade = _entry(t, "m64-off-1", lots=7)
    assert result.executed, result.reasons
    assert trade.quantity == 7


def test_a_daily_loss_hit_stops_entries_logs_the_block_and_never_an_exit():
    from app.trading.position_monitor import monitor_open_positions
    t = _tenant_user("m64-daily@example.com")
    saved = client.put("/api/risk-settings/limits", headers=t["headers"], json={"checks": {
        "daily_loss": {"enabled": True, "values": {"daily_loss_rupees": 1000}}}})
    assert saved.status_code == 200, saved.text
    now = datetime.now(UTC)

    async def seed():
        async with _session_factory() as session:
            session.add(TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="PAPER", symbol="LOSS1", strategy_id="s", direction="LONG",
                                    entry_time=now - timedelta(minutes=20), entry_price=100.0, quantity=10, stop_loss=98.0, target1=110.0,
                                    exit_time=now - timedelta(minutes=5), exit_price=-100.0, exit_reason="Stop Loss", pnl=-2_000.0))
            open_trade = TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="PAPER", symbol="OPEN1", strategy_id="s",
                                     direction="LONG", entry_time=now - timedelta(days=2), entry_price=100.0, quantity=1, stop_loss=98.0,
                                     target1=110.0)
            session.add(open_trade)
            await session.commit()
            return open_trade.id
    open_id = _run(seed())
    result, order, trade = _entry(t, "m64-daily-1")
    assert not result.executed and trade is None and order.status == "REJECTED"
    assert any("has reached your daily loss limit of ₹1,000" in r and "Change limit: Settings > Risk limits" in r for r in result.reasons)

    async def after():
        async with _session_factory() as session:
            events = list(await session.scalars(select(RiskEventRecord).where(RiskEventRecord.tenant_id == t["tenant_id"],
                                                                              RiskEventRecord.rule_type == "RISK_LIMIT:daily_loss")))

            async def price_lookup(symbol, exchange):
                return 97.0                                      # through the open position's stop
            await monitor_open_positions(session, t["tenant_id"], price_lookup)
            closed = await session.get(TradeRecord, open_id)
            return events, closed
    events, closed = _run(after())
    assert len(events) == 1 and events[0].status == "BLOCK" and json.loads(events[0].metadata_json)["user_id"] == t["user_id"]
    assert events[0].limit_value == 1000 and events[0].current_value == 2000
    assert closed.exit_time is not None and closed.pnl is not None          # ADR-0004: the exit went through


def test_a_spread_counts_as_one_open_position():
    t = _tenant_user("m64-spread-one@example.com")
    now = datetime.now(UTC)

    async def go():
        async with _session_factory() as session:
            for role in ("SHORT", "LONG"):
                session.add(TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="PAPER", symbol=f"LEG{role}", strategy_id="s",
                                        direction="SHORT" if role == "SHORT" else "LONG", entry_time=now, entry_price=10.0, quantity=75,
                                        stop_loss=0.0, leg_group_id="g1", leg_role=role))
            await session.commit()
            user = await session.get(User, t["user_id"])
            return await build_trading_day_state(session, user)
    state = _run(go())
    assert state.open_positions == 1 and state.trades_today == 1


def test_the_one_time_notice_is_due_after_the_migration_and_ends_on_save():
    from tests.test_z5_p1d_risk_by_default import _flag, _tenant_t
    me = _tenant_t("m64-notice")
    _flag("ui_v2", me["tenant_id"], True)

    async def mark():
        async with _session_factory() as session:
            session.add(RiskSettingsRecord(tenant_id=me["tenant_id"], min_risk_reward=0.0, lot_size=1, max_open_positions=4,
                                           limits_switches_json=json.dumps({"enabled": ["max_open_positions"], "saved": ["max_open_positions"],
                                                                            "notice": True})))
            await session.commit()
    _run(mark())
    due = client.get("/api/me/notices", headers=me["headers"]).json()["notices"]
    assert {"id": "risk_limits_off", "text": "Risk limits are now off by default — turn on the ones you want",
            "link": "/settings/risk-limits"} in due
    client.put("/api/risk-settings/limits", headers=me["headers"], json={"checks": {}})
    assert all(n["id"] != "risk_limits_off" for n in client.get("/api/me/notices", headers=me["headers"]).json()["notices"])


# --- the migration (up / down) -------------------------------------------------------------------------------------------

def _migration():
    path = BACKEND / "alembic/versions/1f3a5c7e9b20_m64_risk_limits_switches.py"
    spec = importlib.util.spec_from_file_location("m64_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_keeps_saved_values_on_switches_the_rest_off_and_goes_down():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    module = _migration()
    assert module.ROLLBACK_SAFE is True and module.down_revision == "c7e9a1b3d5f7"
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE risk_settings (id INTEGER PRIMARY KEY, tenant_id INTEGER, capital FLOAT, risk_per_trade_pct FLOAT, "
            "max_daily_loss_pct FLOAT, max_trades_per_day INTEGER, max_open_positions INTEGER, max_consecutive_losses INTEGER, "
            "min_risk_reward FLOAT NOT NULL, max_portfolio_risk_pct FLOAT, stop_cooldown_minutes INTEGER, dd_level_1_pct FLOAT, "
            "dd_level_2_pct FLOAT, event_size_cut_pct FLOAT, lot_size INTEGER NOT NULL)"))
        conn.execute(sa.text("INSERT INTO risk_settings (id, tenant_id, risk_per_trade_pct, max_open_positions, min_risk_reward, lot_size) "
                             "VALUES (1, 1, 0.5, 3, 1.2, 1)"))      # 1.2 = the old built-in minimum: not saved by the trader
        conn.execute(sa.text("INSERT INTO risk_settings (id, tenant_id, min_risk_reward, lot_size) VALUES (2, 2, 0, 1)"))
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
        rows = {r[0]: json.loads(r[1]) for r in conn.execute(sa.text("SELECT id, limits_switches_json FROM risk_settings"))}
        assert rows[1] == {"enabled": ["max_open_positions", "risk_sizing"], "notice": True, "saved": ["max_open_positions", "risk_sizing"]}
        assert rows[2] == {"enabled": [], "notice": True, "saved": []}
        kept = conn.execute(sa.text("SELECT risk_per_trade_pct, max_open_positions, min_risk_reward FROM risk_settings WHERE id = 1")).one()
        assert tuple(kept) == (0.5, 3, 1.2)                     # no stored value changes
        with Operations.context(MigrationContext.configure(conn)):
            module.downgrade()
        columns = {c["name"] for c in sa.inspect(conn).get_columns("risk_settings")}
        assert "limits_switches_json" not in columns and "max_lots_per_order" not in columns and "max_open_positions" in columns


def test_a_row_with_no_switches_reads_on_where_a_value_is_set():
    row = RiskSettingsRecord(tenant_id=1, max_open_positions=3, min_risk_reward=1.2, lot_size=1)
    assert risk_limits.switches_of(row) == {"enabled": ["max_open_positions"], "saved": ["max_open_positions"]}
    assert risk_limits.switches_of(None) == {"enabled": [], "saved": []}


@pytest.mark.parametrize("check", [c.id for c in risk_limits.CHECKS])
def test_every_check_has_plain_words_and_a_link(check):
    item = risk_limits.block(check, "x")
    assert item["link"] == f"/settings/risk-limits#{check}" and risk_limits.BY_ID[check].help.endswith(".")
    assert risk_limits.block(check, "x", admin=True)["link_text"] == "Ask your admin"
