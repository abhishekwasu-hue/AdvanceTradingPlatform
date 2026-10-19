"""Z5 / P1-d (P1 spec 1.1, 1.2, 2.2; Phase 0): risk by default and the plan-before-trade PAPER ticket behind the flag
`risk_by_default`, and the India VIX gate behind `vix_gate` (both off by default). Sizing from the user's cap (strictest
wins with the organisation's), the logged and counted override, the cost line, the daily loss lockout - new PAPER entries
and proposals only: exits, Exit now and stops always work - the entries-per-day limit, per-user isolation, and the VIX
gate with parity cases from the Trade repo's tests."""
import asyncio
import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core import config as app_config
from app.core.enums import SignalDirection
from app.core.models import RiskConfig
from app.db.models import AuditLogRecord, MarketSnapshotRecord, RiskLockoutRecord, TradePlanRecord, TradeRecord, User
from app.execution import approval, india_costs
from app.execution.signal_execution import execute_signal_for_user
from app.market_data.calendar import IST
from app.platform import controls
from app.risk_engine import by_default, vix_gate
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import OPEN_NOW, _FakeBroker, _deploy, _get, _tenant, _worker
from tests.test_z3_approval_entries import _price, _sig
from tests.test_z3_approval_entries import _flag_for as _approval_flag
from tests.test_z3b_paper_account import _member, _paper_flag

UTC = timezone.utc


def _run(coro):
    return asyncio.run(coro)


def _tenant_t(tag: str) -> dict:
    return _tenant(f"z5d-{tag}-{uuid.uuid4().hex[:6]}@example.com")


def _flag(name: str, tenant_id: int, on: bool = True) -> None:
    """One flag for one organisation (the suite shares one database), the other flags untouched."""
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            entry = flags.get(name) or {"on": False, "tenants": []}
            tenants = set(entry.get("tenants") or [])
            (tenants.add if on else tenants.discard)(tenant_id)
            flags[name] = {"on": False, "tenants": sorted(tenants)}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


def _org(t: dict, *, capital=1_000_000.0, risk_pct=1.0) -> None:
    """The organisation's own risk settings: a capital like the PAPER account's, so the user's cap is what binds."""
    body = RiskConfig(capital=capital, risk_per_trade_pct=risk_pct, max_daily_loss_pct=5.0, max_trades_per_day=50, max_open_positions=10,
                      dd_level_1_pct=20.0, dd_level_2_pct=25.0, stop_cooldown_minutes=0).model_dump()
    out = client.put("/api/risk-settings", headers=t["headers"], json=body)
    assert out.status_code == 200, out.text


def _earlier_today(minutes: float) -> datetime:
    """`minutes` before now, but never before today's IST midnight: the risk checks count the IST day, and a test run
    just after midnight must still see its trades as today's (the wall clock never decides a test)."""
    from app.market_data.calendar import trading_day_start
    now = datetime.now(UTC)
    start = trading_day_start(now)
    return max(now - timedelta(minutes=minutes), min(now, start + timedelta(seconds=max(1.0, 60 - minutes))))


def _closed(t: dict, pnl: float, *, minutes_ago=30, symbol="RELIANCE", mode="PAPER", user_id=None) -> int:
    async def go():
        async with _session_factory() as session:
            exit_at = _earlier_today(minutes_ago)
            trade = TradeRecord(tenant_id=t["tenant_id"], user_id=user_id or t["user_id"], mode=mode, symbol=symbol, strategy_id="plan_other",
                                direction="LONG", entry_time=max(exit_at - timedelta(minutes=5), _earlier_today(10_000)), entry_price=100.0,
                                quantity=1000.0, stop_loss=90.0, target1=120.0, exchange="NSE", pnl=pnl, charges=20.0, exit_time=exit_at,
                                exit_price=100.0 + pnl / 1000.0, exit_reason="Exit now (web)")
            session.add(trade)
            await session.commit()
            return trade.id
    return _run(go())


def _open(t: dict, *, symbol="RELIANCE", stop=95.0, entry=100.0) -> int:
    async def go():
        async with _session_factory() as session:
            trade = TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="PAPER", symbol=symbol, strategy_id="plan_other",
                                direction="LONG", entry_time=_earlier_today(5), entry_price=entry, quantity=10.0,
                                stop_loss=stop, target1=entry * 1.1, exchange="NSE")
            session.add(trade)
            await session.commit()
            return trade.id
    return _run(go())


def _plan(**kw) -> dict:
    body = {"symbol": "RELIANCE", "direction": "LONG", "setup": "PULLBACK_AT_LEVEL", "entry": 100.0, "stop_loss": 98.0, "target": 104.0,
            "invalidation": "Close back below the level", "checklist": {k: True for k in ("trend_aligned", "level_identified", "pullback_confirmed",
                                                                                            "commitment_candle", "no_event", "liquidity_ok")}}
    body.update(kw)
    return body


def _preview(t, **kw):
    out = client.post("/api/paper/ticket/preview", headers=t["headers"], json=_plan(**kw))
    assert out.status_code == 200, out.text
    return out.json()


def _place(t, **kw):
    out = client.post("/api/paper/ticket", headers=t["headers"], json=_plan(**kw))
    assert out.status_code == 200, out.text
    return out.json()


def _user(user_id) -> User:
    async def go():
        async with _session_factory() as session:
            return await session.get(User, user_id)
    return _run(go())


def _enter(user_id, symbol="RELIANCE", direction=SignalDirection.LONG, entry=100.0, stop=98.0, target=104.0):
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            sig = _sig(entry=entry, stop=stop, target=target, direction=direction, symbol=symbol)
            result, order = await execute_signal_for_user(session, user, mode="PAPER", strategy_id="ema_rsi_scalper_1m", signal=sig,
                                                          idempotency_key=f"z5d:{uuid.uuid4().hex}")
            return result.executed, json.loads(order.reasons_json or "[]"), (result.trade.quantity if result.trade else None)
    return _run(go())


VIX_NOW = datetime(2026, 10, 9, 11, 0, tzinfo=IST)            # a Friday, after the 09:20 check: the gate's clock in these tests


def _vix_clock(monkeypatch, now=VIX_NOW) -> None:
    monkeypatch.setattr(vix_gate, "_now", lambda: now.astimezone(UTC))


def _vix(t: dict, last, prev_close, *, minutes_ago=5, day=None, error=None) -> None:
    """A stored live India VIX reading (as `vix_gate.capture` writes it), `minutes_ago` before VIX_NOW."""
    async def go():
        async with _session_factory() as session:
            at = (VIX_NOW - timedelta(minutes=minutes_ago)).astimezone(UTC)
            payload = {"last": last, "prev_close": prev_close, "change_pct": round((last / prev_close - 1) * 100, 2) if last and prev_close else None,
                       "date": (day or VIX_NOW.date()).isoformat(), "error": error}
            session.add(MarketSnapshotRecord(tenant_id=t["tenant_id"], kind=vix_gate.KIND, symbol="INDIA VIX", exchange="NSE", timeframe="live",
                                             source="upstox", last_price=last, change_pct=payload["change_pct"], payload_json=json.dumps(payload),
                                             captured_at=at))
            await session.commit()
    _run(go())


# --- flags and settings -------------------------------------------------------------------------------------------------

def test_flags_registered_off_by_default_and_the_api_is_hidden_while_off():
    for name in (by_default.FLAG, vix_gate.FLAG):
        assert name in controls.FEATURE_FLAGS and name in controls.DEFAULT_OFF_FLAGS
    t = _tenant_t("off")
    for method, path in (("get", "/api/paper/risk"), ("get", "/api/paper/plans"), ("post", "/api/paper/ticket/preview"), ("post", "/api/paper/ticket")):
        out = getattr(client, method)(path, headers=t["headers"], **({"json": _plan()} if method == "post" else {}))
        assert out.status_code == 404 and out.headers.get("X-Feature-Disabled") == by_default.FLAG
    _flag(by_default.FLAG, t["tenant_id"])
    assert client.get("/api/paper/risk", headers=t["headers"]).status_code == 200


def test_flag_off_changes_nothing_in_the_pipeline(monkeypatch):
    """Off: the organisation's own sizing only (no user cap), no lockout, no VIX gate."""
    t = _tenant_t("plain")
    _org(t, risk_pct=1.0)
    _closed(t, -40_000.0)                                    # far past any user limit, inside the organisation's 5%
    executed, reasons, qty = _enter(t["user_id"])
    assert executed and qty == 5000.0                        # 1% of 10 lakh / 2 per unit


def test_settings_defaults_bounds_and_ceilings():
    t = _tenant_t("settings")
    _flag(by_default.FLAG, t["tenant_id"])
    body = client.get("/api/paper/risk", headers=t["headers"]).json()
    assert body["settings"] == by_default.RiskSettings.defaults().as_dict()
    assert body["settings"] == {"risk_per_trade_pct": app_config.RISK_DEFAULT_RISK_PER_TRADE_PCT, "risk_per_trade_amount": app_config.RISK_DEFAULT_RISK_PER_TRADE_AMOUNT,
                                "daily_loss_pct": app_config.RISK_DEFAULT_DAILY_LOSS_PCT, "max_trades_per_day": app_config.RISK_DEFAULT_MAX_TRADES_PER_DAY,
                                "min_rr": app_config.RISK_DEFAULT_MIN_RR,
                                # N1 F2: the portfolio caps, 0 = no limit by default
                                "max_sector_pct": 0.0, "max_correlated_risk_pct": 0.0, "max_open_positions": 0}
    assert body["capital_basis"] == app_config.PAPER_STARTING_CAPITAL and body["lockout"] is None
    assert "never locked" in body["exits"]
    assert client.put("/api/paper/risk", headers=t["headers"], json={"risk_per_trade_pct": 3.0}).status_code == 422     # above the 2% ceiling
    assert client.put("/api/paper/risk", headers=t["headers"], json={"daily_loss_pct": 0.0}).status_code == 422
    assert client.put("/api/paper/risk", headers=t["headers"], json={"bogus": 1}).status_code == 422
    out = client.put("/api/paper/risk", headers=t["headers"], json={"risk_per_trade_pct": 0.5, "min_rr": 2.0})
    assert out.status_code == 200 and out.json()["settings"]["risk_per_trade_pct"] == 0.5 and out.json()["settings"]["min_rr"] == 2.0

    async def audited():
        async with _session_factory() as session:
            return list(await session.scalars(select(AuditLogRecord.event).where(AuditLogRecord.tenant_id == t["tenant_id"],
                                                                                 AuditLogRecord.event == "paper_risk_settings_set")))
    assert _run(audited())


def test_stored_settings_out_of_bounds_fall_back_to_defaults():
    s = by_default.RiskSettings.from_json(json.dumps({"risk_per_trade_pct": 99, "daily_loss_pct": 3.0, "max_trades_per_day": True, "min_rr": "x"}))
    d = by_default.RiskSettings.defaults()
    assert s.risk_per_trade_pct == d.risk_per_trade_pct and s.daily_loss_pct == 3.0 and s.max_trades_per_day == d.max_trades_per_day and s.min_rr == d.min_rr
    assert by_default.RiskSettings.from_json("not json") == d
    eff, notes = by_default.effective_settings(by_default.RiskSettings(4.0, 0.0, 9.0, 5, 1.5), {"risk_per_trade_pct": 2.0, "max_daily_loss_pct": 5.0})
    assert eff.risk_per_trade_pct == 2.0 and eff.daily_loss_pct == 5.0 and len(notes) == 2


# --- pure rules ---------------------------------------------------------------------------------------------------------

def test_sizing_whole_lots_from_the_cap_and_the_stricter_cap():
    s = by_default.size(100.0, 98.0, 10_000.0, 1)
    assert s["quantity"] == 5000 and s["risk_amount"] == 10_000.0
    s = by_default.size(200.0, 190.0, 10_000.0, 75)                       # 750 per lot -> 13 lots
    assert s["lots"] == 13 and s["quantity"] == 975 and s["risk_amount"] == 9750.0
    assert by_default.size(100.0, 50.0, 1000.0, 75)["lots"] == 0           # one lot risks more than the cap
    assert by_default.size(100.0, 100.0, 1000.0, 1)["lots"] == 0           # no stop distance, no size
    settings = by_default.RiskSettings(1.0, 5000.0, 2.0, 10, 1.5)
    assert by_default.risk_cap(1_000_000, settings) == {"amount": 5000.0, "binding": "amount", "by_pct": 10_000.0}
    assert by_default.risk_cap(100_000, settings)["binding"] == "pct"


def test_cap_config_lowers_only_and_keeps_whole_lots():
    cfg = RiskConfig(capital=1_000_000, risk_per_trade_pct=1.0)
    same, note = by_default.cap_config(cfg, 20_000)
    assert same is cfg and note is None                                    # the organisation's 10,000 is stricter
    capped, note = by_default.cap_config(cfg, 3_000)
    assert note and capped.capital * capped.risk_per_trade_pct / 100 >= 3_000 > 2_999.99
    from app.risk_engine.risk_manager import RiskManager, TradingDayState
    decision = RiskManager(capped).validate_and_size(_sig(entry=100.0, stop=99.97), TradingDayState())
    assert decision.approved and decision.quantity == 100_000                # 3000 / 0.03 exactly, no lot lost to rounding


def test_reward_risk_and_levels():
    assert by_default.reward_risk(100, 98, 104) == 2.0
    assert by_default.reward_risk(100, 100, 104) is None
    assert by_default.level_errors("LONG", 100, 98, 104) == []
    assert len(by_default.level_errors("LONG", 100, 101, 99)) == 2
    assert by_default.level_errors("SHORT", 100, 102, 96) == []
    assert len(by_default.level_errors("SHORT", 100, 99, 101)) == 2


def test_cost_line_matches_the_dated_cost_model_and_the_break_even():
    day = date(2026, 10, 9)
    line = by_default.cost_line(direction="LONG", entry=100.0, stop=98.0, target=104.0, quantity=1000, day=day, slippage_pct=0.02, brokerage_per_order=20)
    trip = india_costs.round_trip(day, day, 100.0, 104.0, 1000, "EQUITY_INTRADAY", brokerage_per_order=20)
    assert line["items"]["stt"] == round(trip["stt"], 2) and line["items"]["gst"] == round(trip["tax"], 2)
    assert line["items"]["brokerage"] == 40.0 and line["items"]["slippage"] == round(204.0 * 1000 * 0.0002, 2)
    assert line["total"] == pytest.approx(sum(line["items"].values()), abs=0.05)
    flat = india_costs.round_trip(day, day, 100.0, 100.0, 1000, "EQUITY_INTRADAY", brokerage_per_order=20)["total"] + 200.0 * 1000 * 0.0002
    assert line["break_even_move"] == pytest.approx(flat / 1000, abs=1e-4) and line["break_even_pct"] == pytest.approx(flat / 1000 / 100 * 100, abs=1e-4)
    assert line["net_reward"] == pytest.approx(4000 - line["total"], abs=0.01) and line["net_risk"] == pytest.approx(2000 + line["at_stop_total"], abs=0.01)
    assert line["net_reward_risk"] < 2.0 and "contract note" in line["basis"]
    short = by_default.cost_line(direction="SHORT", entry=100.0, stop=102.0, target=96.0, quantity=1000, day=day)
    assert short["items"]["stt"] == round(india_costs.round_trip(day, day, 100.0, 96.0, 1000, "EQUITY_INTRADAY", sold_first=True)["stt"], 2)


def test_daily_loss_check_counts_the_worst_point_and_open_marks():
    s = by_default.RiskSettings(1.0, 0.0, 2.0, 10, 1.5)
    assert by_default.daily_loss_breached(1_000_000, s, [-5000, -16_000, 30_000], 0.0)["breached"] is True     # hit, then recovered
    assert by_default.daily_loss_breached(1_000_000, s, [-19_999], 0.0)["breached"] is False
    assert by_default.daily_loss_breached(1_000_000, s, [-20_000], 0.0)["breached"] is True                    # at the limit
    assert by_default.daily_loss_breached(1_000_000, s, [-15_000], -6_000.0)["breached"] is True               # open marks count
    out = by_default.daily_loss_breached(1_000_000, s, [], 0.0)
    assert out["limit"] == 20_000 and out["breached"] is False


# --- the ticket ---------------------------------------------------------------------------------------------------------

def test_ticket_preview_sizes_from_the_cap_and_shows_the_cost_line():
    t = _tenant_t("preview")
    _org(t)
    _flag(by_default.FLAG, t["tenant_id"])
    view = _preview(t)
    s = view["sizing"]
    assert s["user_cap"] == 10_000.0 and s["cap"] == 10_000.0 and s["binding"] == "your cap"
    assert s["suggested_quantity"] == 5000 and s["quantity"] == 5000 and s["risk_amount"] == 10_000.0 and s["gauge"] == 1.0
    assert view["reward_risk"] == 2.0 and view["can_place"] and view["confirm_label"] == "Place PAPER: BUY 5000 RELIANCE"
    assert view["costs"]["total"] > 0 and view["costs"]["break_even_move"] > 0 and view["checklist_complete"] is True
    assert {c["key"] for c in view["checklist"] if c["auto"]} == {"rr_ok", "size_within_cap"}
    # The organisation's own limit is stricter: it binds (ADR-0003).
    _org(t, capital=100_000.0, risk_pct=1.0)
    s = _preview(t)["sizing"]
    assert s["cap"] == 1000.0 and s["binding"] == "the organisation's risk per trade" and s["suggested_quantity"] == 500
    # Problems: levels, reward:risk, time stop; a skipped checklist only warns.
    _org(t)
    bad = _preview(t, stop_loss=101.0)
    assert not bad["can_place"] and any("below the entry" in e for e in bad["errors"])
    low = _preview(t, target=102.0)
    assert not low["can_place"] and any("below your minimum" in r for r in low["refusals"])
    assert any("Time stop" in e for e in _preview(t, time_stop="16:10")["errors"])
    skipped = _preview(t, checklist={"trend_aligned": True})
    assert skipped["can_place"] and skipped["checklist_complete"] is False and any("discipline miss" in w for w in skipped["warnings"])
    assert any("whole number of lots" in e for e in _preview(t, quantity=0.5)["errors"])


def test_ticket_places_one_paper_order_at_the_live_price_and_stores_the_plan(monkeypatch):
    t = _tenant_t("place")
    _org(t)
    _flag(by_default.FLAG, t["tenant_id"])
    _price(monkeypatch, None)
    out = _place(t)
    assert out["status"] == "REFUSED" and any("No live price" in r for r in out["reasons"])
    _price(monkeypatch, 103.0)                                  # far from the plan's entry: update the plan
    out = _place(t)
    assert out["status"] == "REFUSED" and any("update the plan" in r for r in out["reasons"])
    _price(monkeypatch, 100.2)
    from app.paper import ticket as ticket_module
    monkeypatch.setattr(ticket_module, "wall_clock", lambda: datetime(2026, 10, 9, 10, 0, tzinfo=IST))
    out = _place(t, time_stop="15:00")
    assert out["status"] == "PLACED", out["reasons"]
    trade = _get(TradeRecord, out["trade_id"])
    # Re-priced at 100.2 (plus the paper fill's slippage): 2.2 per unit to the stop, so 4545 keeps the risk within 10,000.
    assert trade.mode == "PAPER" and trade.user_id == t["user_id"] and trade.quantity == 4545
    # The paper broker's fill slippage (0.02%) is on top of the sized risk, as for every PAPER entry.
    assert out["quantity"] == 5000 and out["filled"]["quantity"] == 4545
    assert out["filled"]["risk_amount"] <= 10_000.0 + 4545 * 100.2 * 0.0002 + 0.01
    assert trade.strategy_id == "plan_pullback_at_level" and json.loads(trade.exit_rules)["time_exit_at"] == "15:00"
    assert out["costs"]["total"] > 0 and out["checklist_complete"] is True and out["override"] is False
    plans = client.get("/api/paper/plans", headers=t["headers"]).json()
    assert [p["status"] for p in plans] == ["PLACED", "REFUSED", "REFUSED"] and plans[0]["trade_id"] == trade.id


def test_override_needs_a_reason_is_bounded_logged_and_counted(monkeypatch):
    t = _tenant_t("override")
    _org(t, risk_pct=2.0)                                          # the organisation allows 20,000; the user's cap is 10,000
    _flag(by_default.FLAG, t["tenant_id"])
    _price(monkeypatch, 100.0)
    over = _preview(t, quantity=8000)
    assert not over["can_place"] and any("needs an override" in r for r in over["refusals"]) and over["sizing"]["gauge"] == 1.6
    assert any("reason of at least" in r for r in _preview(t, quantity=8000, override=True, override_reason="short")["refusals"])
    beyond = _preview(t, quantity=12_000, override=True, override_reason="High conviction setup, documented")
    assert not beyond["can_place"] and any("at most 20,000.00" in r for r in beyond["refusals"])
    ok = _preview(t, quantity=8000, override=True, override_reason="High conviction setup, documented")
    assert ok["can_place"] and any("logged and counted" in w for w in ok["warnings"])
    placed = _place(t, quantity=8000, override=True, override_reason="High conviction setup, documented")
    assert placed["status"] == "PLACED" and placed["override"] is True and placed["risk_amount"] == 16_000.0
    assert _get(TradeRecord, placed["trade_id"]).quantity == 8000
    status = client.get("/api/paper/risk", headers=t["headers"]).json()
    assert status["overrides"] == {"today": 1, "total": 1}

    async def audit():
        async with _session_factory() as session:
            return list(await session.scalars(select(AuditLogRecord.detail).where(AuditLogRecord.tenant_id == t["tenant_id"],
                                                                                  AuditLogRecord.event == "risk_cap_override")))
    rows = _run(audit())
    assert len(rows) == 1 and "High conviction" in rows[0]
    # The organisation's limit is never passed, override or not.
    _org(t, risk_pct=1.0)
    capped = _preview(t, quantity=8000, override=True, override_reason="High conviction setup, documented")
    assert not capped["can_place"] and any("at most 10,000.00" in r for r in capped["refusals"])


# --- the daily loss lockout (exits allowed) ----------------------------------------------------------------------------

def test_daily_loss_lockout_refuses_new_entries_and_proposals_but_never_exits(monkeypatch):
    t = _tenant_t("lock")
    _org(t)
    _flag(by_default.FLAG, t["tenant_id"])
    member = _member(t)
    stranger = _tenant_t("lock-other")
    _org(stranger)
    _flag(by_default.FLAG, stranger["tenant_id"])
    open_id = _open(t)
    _closed(t, -25_000.0)                                        # beyond 2% of 10 lakh, inside the organisation's 5%
    view = _preview(t)
    assert not view["can_place"] and any("Daily loss limit reached" in r and "exits stay available" in r for r in view["refusals"])
    status = client.get("/api/paper/risk", headers=t["headers"]).json()
    assert status["lockout"]["kind"] == "DAILY_LOSS" and status["lockout"]["until"] and status["today"]["used"] >= 1.0

    # Recorded once, with a notification, and kept when the day recovers.
    _closed(t, 40_000.0, minutes_ago=10)
    assert client.get("/api/paper/risk", headers=t["headers"]).json()["lockout"] is not None

    async def locks():
        async with _session_factory() as session:
            return list(await session.scalars(select(RiskLockoutRecord).where(RiskLockoutRecord.tenant_id == t["tenant_id"])))
    rows = _run(locks())
    assert len(rows) == 1 and rows[0].user_id == t["user_id"]

    # The pipeline refuses the locked user's new PAPER entry; the member of the same organisation and another
    # organisation are not touched.
    executed, reasons, _ = _enter(t["user_id"])
    assert not executed and any("Daily loss limit reached" in r for r in reasons)
    assert _enter(member["user_id"])[0] is True
    assert _enter(stranger["user_id"])[0] is True

    # No entry proposal is raised for the locked user.
    async def propose():
        async with _session_factory() as session:
            await approval.propose_entry(session, t["tenant_id"], signal=_sig(), strategy_id="x", source="tradingview",
                                         idempotency_key=f"lock:{uuid.uuid4().hex}", user_id=t["user_id"])
    with pytest.raises(approval.EntriesPaused, match="Daily loss limit"):
        _run(propose())

    # Exit now still closes the locked user's position (at the user's own live price).
    _paper_flag(t["tenant_id"])
    _price(monkeypatch, 101.0)
    out = client.post(f"/api/positions/{open_id}/exit-now", headers=t["headers"])
    assert out.status_code == 200, out.text
    assert out.json()["closed"][0]["exit_reason"].startswith("Exit now (web") and out.json()["closed"][0]["exit_price"] == 101.0


def test_a_locked_users_stop_still_closes_the_position(monkeypatch):
    t = _tenant_t("lock-stop")
    _org(t)
    _flag(by_default.FLAG, t["tenant_id"])
    _flag(vix_gate.FLAG, t["tenant_id"])
    _vix_clock(monkeypatch)
    _vix(t, 30.0, 20.0)                                           # a VIX spike too: still no exit is held
    _closed(t, -25_000.0)
    assert _preview(t)["can_place"] is False
    dep_id = _deploy(t)
    trade_id = _open(t, stop=95.0)

    async def attach():
        async with _session_factory() as session:
            trade = await session.get(TradeRecord, trade_id)
            trade.deployment_id = dep_id
            await session.commit()
    _run(attach())
    worker = _worker(monkeypatch, _FakeBroker(ltp=94.0))         # through the stop
    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.positions_closed >= 1 and _get(TradeRecord, trade_id).exit_reason == "Stop Loss"


def test_entries_per_day_limit_counts_only_the_users_own_entries(monkeypatch):
    t = _tenant_t("perday")
    _org(t)
    _flag(by_default.FLAG, t["tenant_id"])
    assert client.put("/api/paper/risk", headers=t["headers"], json={"max_trades_per_day": 2}).status_code == 200
    member = _member(t)
    assert _enter(t["user_id"])[0] and _enter(t["user_id"])[0]
    executed, reasons, _ = _enter(t["user_id"])
    assert not executed and any("entries per day" in r for r in reasons)
    assert _enter(member["user_id"])[0] is True


def test_the_users_cap_sizes_every_paper_entry_under_the_flag():
    """Approved proposals and console entries go through the same pipeline: the user's cap applies (strictest wins)."""
    t = _tenant_t("cap")
    _org(t)
    _flag(by_default.FLAG, t["tenant_id"])
    assert client.put("/api/paper/risk", headers=t["headers"], json={"risk_per_trade_pct": 0.5}).status_code == 200
    executed, reasons, qty = _enter(t["user_id"])
    assert executed and qty == 2500.0 and any("your PAPER cap" in r for r in reasons)


def test_plans_and_risk_state_are_the_users_own(monkeypatch):
    t = _tenant_t("iso")
    _org(t)
    _flag(by_default.FLAG, t["tenant_id"])
    member = _member(t)
    _price(monkeypatch, 100.0)
    assert _place(t)["status"] == "PLACED"
    assert client.get("/api/paper/plans", headers=member["headers"]).json() == []
    _closed(t, -25_000.0)
    assert client.get("/api/paper/risk", headers=t["headers"]).json()["lockout"] is not None
    assert client.get("/api/paper/risk", headers=member["headers"]).json()["lockout"] is None
    other = _tenant_t("iso-other")
    _flag(by_default.FLAG, other["tenant_id"])
    assert client.get("/api/paper/plans", headers=other["headers"]).json() == []

    async def rows():
        async with _session_factory() as session:
            return list(await session.scalars(select(TradePlanRecord).where(TradePlanRecord.tenant_id == t["tenant_id"])))
    assert {r.user_id for r in _run(rows())} == {t["user_id"]}


def test_viewer_cannot_place_or_change_settings():
    t = _tenant_t("viewer")
    _flag(by_default.FLAG, t["tenant_id"])
    email = f"z5d-viewer-{uuid.uuid4().hex[:6]}@example.com"
    invite = client.post("/api/team/invites", headers=t["headers"], json={"email": email, "role": "VIEWER"})
    assert invite.status_code in (200, 201), invite.text
    from urllib.parse import parse_qs, urlparse
    token = parse_qs(urlparse(invite.json()["invite_url"]).query)["invite"][0]
    access = client.post(f"/api/auth/invite/{token}/accept", json={"password": "Viewer-Pass-2026"}).json()["access_token"]
    viewer = {"Authorization": f"Bearer {access}"}
    assert client.post("/api/paper/ticket", headers=viewer, json=_plan()).status_code == 403
    assert client.put("/api/paper/risk", headers=viewer, json={"min_rr": 2.0}).status_code == 403
    assert client.get("/api/paper/risk", headers=viewer).status_code == 200


# --- the India VIX gate -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("current,prev,halted", [(13.0, 12.7, False), (14.0, 13.0, True), (10.5, 10.0, True), (None, 13.0, True), (13.0, 0.0, True),
                                                 (13.0, None, True)])
def test_vix_spike_decision_parity_with_trade(current, prev, halted):
    """Trade tests/test_check_vix_spike_halt.py: below the threshold not halted; above halted; exactly at it halted (>=);
    a missing price or a previous close of zero halts (fail-safe)."""
    assert vix_gate.spike_decision(current, prev, 5.0)[0] is halted


def _evaluate(readings, symbol="NIFTY", direction="LONG", now=None, **kw):
    params = {"spike_pct": 5.0, "high_level": 20.0, "size_cut_pct": 50.0, "max_age_minutes": 45, "underlyings": ("NIFTY", "BANKNIFTY", "SENSEX"),
              "grace_minutes": 10}
    params.update(kw)
    return vix_gate.evaluate(readings, symbol=symbol, direction=direction, now=now or datetime(2026, 10, 9, 11, 0, tzinfo=UTC), **params)


def test_vix_gate_timing_parity_with_trade():
    """Trade: before the 09:20 check nothing is halted; a VIX that could not be read halts the day (fail-safe). ATP: open
    until 09:20, open (waiting) for the grace period, then no valid reading pauses bullish index entries; never exits."""
    friday = lambda hh, mm: datetime(2026, 10, 9, hh, mm, tzinfo=IST)   # noqa: E731
    assert _evaluate([], now=friday(9, 19)).state == "before_check" and _evaluate([], now=friday(9, 19)).reasons == []
    spike_early = [{"last": 30.0, "prev_close": 13.0, "captured_at": friday(9, 16)}]
    assert _evaluate(spike_early, now=friday(9, 19)).reasons == []                    # the check has not run yet
    assert _evaluate(spike_early, now=friday(9, 21)).reasons                          # from 09:20 it counts
    waiting = _evaluate([], now=friday(9, 25))
    assert waiting.state == "waiting" and waiting.reasons == []
    failed = [{"last": None, "prev_close": 13.0, "error": "ConnectError: timeout", "captured_at": friday(9, 21)}]
    late = _evaluate(failed, now=friday(9, 31))
    assert late.state == "no_reading" and late.reasons and "ConnectError" in late.reasons[0]
    assert _evaluate(failed, now=friday(9, 31), direction="SHORT").reasons == []      # bearish entries are not touched
    assert _evaluate([], now=datetime(2026, 10, 10, 11, 0, tzinfo=IST)).reasons == []  # a Saturday: no session, no fail-safe


def test_vix_gate_rules():
    now = datetime(2026, 10, 9, 11, 0, tzinfo=UTC)
    spike = [{"last": 14.0, "prev_close": 13.0, "captured_at": now - timedelta(minutes=5)}]
    v = _evaluate(spike)
    assert v.reasons and "bullish" in v.reasons[0] and v.state == "spike"
    assert _evaluate(spike, direction="SHORT").reasons == []                         # bearish is not touched (Trade)
    assert _evaluate(spike, symbol="BANKNIFTY").reasons and _evaluate(spike, symbol="SENSEX").reasons
    assert _evaluate(spike, symbol="RELIANCE").state == "out_of_scope"                # MCX / stocks never (Trade: MCX)
    # A spike earlier in the day keeps the pause for the rest of it.
    later = spike + [{"last": 13.1, "prev_close": 13.0, "captured_at": now - timedelta(minutes=1)}]
    assert _evaluate(later).reasons
    none = _evaluate([])                                                              # after 09:20 + grace, a weekday
    assert none.reasons and none.state == "no_reading"
    high = _evaluate([{"last": 22.0, "prev_close": 21.5, "captured_at": now - timedelta(minutes=5)}], direction="SHORT")
    assert high.reasons == [] and high.size_multiplier == 0.5 and high.state == "high"
    stale = _evaluate([{"last": 22.0, "prev_close": 21.5, "captured_at": now - timedelta(minutes=90)}])
    assert stale.size_multiplier == 1.0
    assert _evaluate([{"last": 22.0, "prev_close": 21.5, "captured_at": now}], size_cut_pct=100.0).reasons
    assert _evaluate([{"last": 22.0, "prev_close": 21.5, "captured_at": now}], high_level=0.0).size_multiplier == 1.0


def test_vix_gate_in_the_ticket_and_pipeline_entries_only(monkeypatch):
    t = _tenant_t("vix")
    _org(t)
    _flag(by_default.FLAG, t["tenant_id"])
    _vix_clock(monkeypatch)
    _vix(t, 13.1, 13.0)
    _vix(t, 14.0, 13.0, day=date(2020, 1, 1))                     # another day's reading: says nothing about today
    assert not any("India VIX" in r for r in _preview(t, symbol="NIFTY")["refusals"])          # flag off
    _flag(vix_gate.FLAG, t["tenant_id"])
    assert not any("India VIX" in r for r in _preview(t, symbol="NIFTY")["refusals"])          # today's reading is calm
    _vix(t, 14.0, 13.0, minutes_ago=2)
    assert any("India VIX gate" in r for r in _preview(t, symbol="NIFTY")["refusals"])
    assert _preview(t, symbol="NIFTY", direction="SHORT", stop_loss=102.0, target=96.0)["can_place"]
    assert _preview(t, symbol="RELIANCE")["can_place"]
    executed, reasons, _ = _enter(t["user_id"], symbol="NIFTY")
    assert not executed and any("India VIX gate" in r for r in reasons), reasons
    status = client.get("/api/paper/risk", headers=t["headers"]).json()["vix_gate"]
    assert status["enabled"] and status["bullish_paused"] and status["latest"]["last"] == 14.0
    # Exits are never gated.
    trade_id = _open(t, symbol="NIFTY")
    _paper_flag(t["tenant_id"])
    _price(monkeypatch, 99.0)
    assert client.post(f"/api/positions/{trade_id}/exit-now", headers=t["headers"]).status_code == 200


def test_vix_high_level_cuts_the_size(monkeypatch):
    t = _tenant_t("vix-high")
    _vix_clock(monkeypatch)
    _org(t)
    _flag(by_default.FLAG, t["tenant_id"])
    _flag(vix_gate.FLAG, t["tenant_id"])
    _vix(t, 23.0, 22.5)
    view = _preview(t, symbol="NIFTY")
    assert view["can_place"] and view["sizing"]["size_multiplier"] == 0.5 and view["sizing"]["cap"] == 5000.0
    assert any("risk per trade cut 50%" in w for w in view["warnings"])
    executed, reasons, qty = _enter(t["user_id"], symbol="NIFTY")
    assert executed and qty == 2500.0


def test_under_approval_only_the_ticket_raises_a_proposal_and_approve_keeps_its_limits(monkeypatch):
    """No entry bypasses approval-only mode (Z3-a): the ticket raises an ENTRY proposal carrying the plan's quantity
    ceiling and checked cap (the override's room included); Approve places the PAPER order within them."""
    t = _tenant_t("ticket-approval")
    _org(t, risk_pct=2.0)
    _flag(by_default.FLAG, t["tenant_id"])
    _approval_flag(t["tenant_id"])
    _price(monkeypatch, 100.0)
    out = _place(t, quantity=6000, override=True, override_reason="High conviction setup, documented")
    assert out["status"] == "PROPOSED" and out["proposal"]["status"] == "PROPOSED" and out["trade_id"] is None

    async def evidence():
        from app.db.models import AiActionRecord
        async with _session_factory() as session:
            row = await session.get(AiActionRecord, out["proposal"]["id"])
            return json.loads(row.evidence_json)
    ev = _run(evidence())
    assert ev["source"] == "ticket" and ev["ticket"] == {"plan_id": out["id"], "max_quantity": 6000.0, "risk_cap_amount": 12_000.0}
    approved = client.post(f"/api/ai/actions/{out['proposal']['id']}/approve", headers=t["headers"], json={})
    assert approved.status_code == 200, approved.text
    plan = client.get("/api/paper/plans", headers=t["headers"]).json()[0]
    assert plan["proposal"]["status"] == "EXECUTED" and plan["trade_id"] and plan["filled"]["quantity"] == 6000
