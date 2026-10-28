"""N2-2 (P1 spec 1.4, docs/design/N2_P1FGH.md): experience levels behind the `experience_levels` flag (off by default).

Covers:
- the level from the user's own PAPER record;
- the gates: futures (Intermediate), naked short options (Advanced), single-leg and structures, in the order pipeline;
- the explicit opt-out for one IST day, with a reason: logged, counted, never twice, the caller's own;
- exits never gated;
- the two routes, and nothing gated while the flag is off.
"""
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import select

from app.core import config as app_config
from app.core.enums import InstrumentKind, OptionStrategy, OrderSide, SignalDirection
from app.core.models import RiskConfig
from app.db.models import AuditLogRecord, ExperienceOptOutRecord, TradeRecord, User
from app.execution.multileg import execute_structure
from app.instruments.spreads import resolve_structure
from app.journal.review import advice_in
from app.paper import experience
from app.platform import controls
from tests.test_auth_api import _session_factory, client
from tests.test_contract_execution import BUY, FUT, WRITE, _execute, _FnoBroker, _load_master, _resolve, _signal, _user
from tests.test_contract_rules import TODAY
from tests.test_multileg import SPOT, _OptionBroker, _rules
from tests.test_trading_worker import _tenant

UTC = timezone.utc


def _run(coro):
    return asyncio.run(coro)


def _email(tag: str) -> str:
    return f"n2x-{tag}-{uuid.uuid4().hex[:6]}@example.com"


def _flag(tenant_id: int, on: bool = True) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            entry = flags.get(experience.FLAG) or {"on": False, "tenants": []}
            tenants = set(entry.get("tenants") or [])
            (tenants.add if on else tenants.discard)(tenant_id)
            flags[experience.FLAG] = {"on": False, "tenants": sorted(tenants)}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
            if on:
                # DEF-0 D4: the gates apply only to a user who turned on "Limit me by my experience level" - these tests'
                # users all did (the organisation's flag alone gates nobody: tests/test_def0_exits_and_gates.py)
                from app.me import prefs
                for user in list(await session.scalars(select(User).where(User.tenant_id == tenant_id))):
                    await prefs.save("my_limits", b'{"limit_by_experience": true}', None, user, session)
    _run(go())


def _closed_trades(tenant_id: int, user_id: int, n: int, *, days: int, pnl: float = 50.0) -> None:
    """n closed PAPER trades spread over `days` IST days (one or more a day), priced from platform quotes."""
    async def go():
        async with _session_factory() as session:
            now = datetime.now(UTC)
            for i in range(n):
                entry = now - timedelta(days=1 + i % days, minutes=i)
                session.add(TradeRecord(tenant_id=tenant_id, user_id=user_id, mode="PAPER", symbol="RELIANCE", strategy_id="manual",
                                        direction="LONG", entry_time=entry, entry_price=100.0, quantity=1.0, stop_loss=95.0, target1=110.0,
                                        exchange="NSE", pnl=pnl, charges=0.0, exit_time=entry + timedelta(minutes=5), exit_price=100.0 + pnl,
                                        exit_reason="Target 1"))
            await session.commit()
    _run(go())


def _opt_out(user_id: int, gate: str, reason: str):
    async def go():
        async with _session_factory() as session:
            return await experience.opt_out(session, await session.get(User, user_id), gate, reason)
    return _run(go())


# --- pure: classification and levels -------------------------------------------------------------------------------------

def _leg(role, right=None, ratio=1, kind=InstrumentKind.OPTION):
    return SimpleNamespace(role=role, ratio=ratio, contract=SimpleNamespace(kind=kind, right=right))


def test_contract_gates():
    assert experience.contract_gates(SimpleNamespace(kind=InstrumentKind.FUTURE, entry_side=OrderSide.SELL)) == ["futures"]
    assert experience.contract_gates(SimpleNamespace(kind=InstrumentKind.FUTURE, entry_side=OrderSide.BUY)) == ["futures"]
    assert experience.contract_gates(SimpleNamespace(kind=InstrumentKind.OPTION, entry_side=OrderSide.SELL)) == ["naked_short_options"]
    assert experience.contract_gates(SimpleNamespace(kind=InstrumentKind.OPTION, entry_side=OrderSide.BUY)) == []
    assert experience.contract_gates(SimpleNamespace(kind=InstrumentKind.UNDERLYING, entry_side=OrderSide.SELL)) == []


def test_structure_gates_flag_only_an_unhedged_short_side():
    hedged = {
        "bull put": [_leg("SHORT", "PE"), _leg("LONG", "PE")],
        "iron condor": [_leg("SHORT", "PE"), _leg("LONG", "PE"), _leg("SHORT", "CE"), _leg("LONG", "CE")],
        "long butterfly": [_leg("LONG", "CE"), _leg("SHORT", "CE", 2), _leg("LONG", "CE")],
        "long straddle": [_leg("LONG", "CE"), _leg("LONG", "PE")],
        "calendar": [_leg("SHORT", "CE"), _leg("LONG", "CE")],
    }
    for name, legs in hedged.items():
        assert experience.structure_gates(legs) == [], name
    naked = {
        "short straddle": [_leg("SHORT", "CE"), _leg("SHORT", "PE")],
        "call ratio 1:2": [_leg("LONG", "CE"), _leg("SHORT", "CE", 2)],
        "put spread with a naked call": [_leg("SHORT", "PE"), _leg("LONG", "PE"), _leg("SHORT", "CE")],
    }
    for name, legs in naked.items():
        assert experience.structure_gates(legs) == ["naked_short_options"], name
    assert experience.structure_gates([_leg("LONG", None, kind=InstrumentKind.FUTURE), _leg("SHORT", "CE")]) == ["futures", "naked_short_options"]


def test_levels_move_with_the_record_and_advanced_needs_intermediate(monkeypatch):
    monkeypatch.setattr(app_config, "PAPER_LADDER_MIN_TRADES", 10)
    monkeypatch.setattr(app_config, "PAPER_LADDER_MIN_SESSIONS", 5)
    monkeypatch.setattr(app_config, "PAPER_LADDER_MAX_DRAWDOWN_PCT", 10.0)
    monkeypatch.setattr(app_config, "EXPERIENCE_ADVANCED_MIN_TRADES", 40)
    monkeypatch.setattr(app_config, "EXPERIENCE_ADVANCED_MIN_SESSIONS", 20)
    assert experience.derive_level(0, 0, 0.0)[0] == "BEGINNER"
    assert experience.derive_level(10, 4, 0.0)[0] == "BEGINNER"
    assert experience.derive_level(10, 5, 0.0)[0] == "INTERMEDIATE"
    assert experience.derive_level(10, 5, 12.0)[0] == "BEGINNER"           # drawdown beyond the ladder's limit
    level, steps = experience.derive_level(40, 20, 3.0)
    assert level == "ADVANCED" and [s["reached"] for s in steps] == [True, True]
    assert steps[0]["opens"] == ["Futures"] and steps[1]["opens"] == ["Naked short options"]
    assert experience.allowed_by_level("INTERMEDIATE", "futures") and not experience.allowed_by_level("INTERMEDIATE", "naked_short_options")
    text = experience.refusal_text("naked_short_options", "BEGINNER", {"closed_trades": 3, "sessions": 2})
    assert text.startswith("Naked short options open at the Advanced experience level; yours is Beginner") and advice_in(text) == []


# --- API ---------------------------------------------------------------------------------------------------------------

def test_flag_registered_off_and_routes_hidden_while_off():
    assert experience.FLAG in controls.FEATURE_FLAGS and experience.FLAG in controls.DEFAULT_OFF_FLAGS
    t = _tenant(_email("off"))
    r = client.get("/api/paper/experience", headers=t["headers"])
    assert r.status_code == 404 and r.headers.get("X-Feature-Disabled") == "experience_levels"
    assert client.post("/api/paper/experience/opt-out", headers=t["headers"], json={"gate": "futures", "reason": "x" * 20}).status_code == 404


def test_status_opt_out_logged_counted_once_and_personal():
    owner = _tenant(_email("owner"))
    _flag(owner["tenant_id"])
    other = _tenant(_email("other"))
    _flag(other["tenant_id"])
    body = client.get("/api/paper/experience", headers=owner["headers"]).json()
    assert body["level"] == "BEGINNER" and body["record"] == {"closed_trades": 0, "sessions": 0, "max_drawdown_pct": 0.0}
    assert {g["key"]: g["allowed"] for g in body["gates"]} == {"futures": False, "naked_short_options": False}
    assert body["opt_outs"]["total"] == 0 and [lvl["key"] for lvl in body["levels"]] == ["BEGINNER", "INTERMEDIATE", "ADVANCED"]

    post = lambda h, **b: client.post("/api/paper/experience/opt-out", headers=h, json=b)   # noqa: E731
    assert post(owner["headers"], gate="futures", reason="short").status_code == 422
    assert post(owner["headers"], gate="margin", reason="x" * 30).status_code == 422
    ok = post(owner["headers"], gate="futures", reason="  Practising index futures on paper today  ")
    assert ok.status_code == 200 and ok.json()["created"] is True
    gates = {g["key"]: g for g in ok.json()["gates"]}
    assert gates["futures"]["opted_out_today"] is True and gates["futures"]["allowed"] is True and gates["naked_short_options"]["allowed"] is False
    again = post(owner["headers"], gate="futures", reason="another reason, same day")
    assert again.json()["created"] is False and again.json()["opt_outs"]["today"] == 1 and again.json()["opt_outs"]["total"] == 1
    recent = again.json()["opt_outs"]["recent"]
    assert recent == [{"gate": "futures", "gate_label": "Futures", "level": "BEGINNER", "reason": "Practising index futures on paper today",
                       "day": again.json()["day"]}]

    async def audits():
        async with _session_factory() as session:
            return list(await session.scalars(select(AuditLogRecord).where(AuditLogRecord.user_id == owner["user_id"], AuditLogRecord.event == "experience_opt_out")))
    rows = _run(audits())
    assert len(rows) == 1 and "futures" in rows[0].detail and "Practising" in rows[0].detail

    theirs = client.get("/api/paper/experience", headers=other["headers"]).json()
    assert theirs["opt_outs"]["total"] == 0 and all(not g["opted_out_today"] for g in theirs["gates"])


def test_a_level_that_already_opens_the_gate_has_nothing_to_opt_out(monkeypatch):
    monkeypatch.setattr(app_config, "PAPER_LADDER_MIN_TRADES", 3)
    monkeypatch.setattr(app_config, "PAPER_LADDER_MIN_SESSIONS", 3)
    t = _tenant(_email("inter"))
    _flag(t["tenant_id"])
    _closed_trades(t["tenant_id"], t["user_id"], 3, days=3)
    body = client.get("/api/paper/experience", headers=t["headers"]).json()
    assert body["level"] == "INTERMEDIATE" and body["record"]["closed_trades"] == 3 and body["record"]["sessions"] == 3
    r = client.post("/api/paper/experience/opt-out", headers=t["headers"], json={"gate": "futures", "reason": "x" * 20})
    assert r.status_code == 409 and "already opens futures" in r.text


# --- the order pipeline --------------------------------------------------------------------------------------------------

def test_pipeline_gates_futures_and_written_options_until_level_or_opt_out():
    _load_master()
    user = _user(_email("fno"))
    fut = _resolve(FUT, SignalDirection.SHORT, spot=None)
    _flag(user.tenant_id)
    result, order, trade = _execute(user, mode="PAPER", contract=fut, rules=FUT, quote_broker=_FnoBroker(premium=24580.0),
                                    signal=_signal(SignalDirection.SHORT))
    assert not result.executed and order.status == "REJECTED" and trade is None
    assert any(r.startswith("Futures open at the Intermediate experience level; yours is Beginner") for r in result.reasons)

    written = _resolve(WRITE)
    result, order, _ = _execute(user, mode="PAPER", contract=written, rules=WRITE, quote_broker=_FnoBroker(premium=120.0))
    assert not result.executed and any(r.startswith("Naked short options open at the Advanced") for r in result.reasons)

    bought = _resolve(BUY)
    result, _, trade = _execute(user, mode="PAPER", contract=bought, rules=BUY, quote_broker=_FnoBroker(premium=120.0))
    assert result.executed, result.reasons                                  # a bought option is never gated

    _opt_out(user.id, "futures", "Practising futures on paper today")
    result, _, trade = _execute(user, mode="PAPER", contract=fut, rules=FUT, quote_broker=_FnoBroker(premium=24580.0),
                                signal=_signal(SignalDirection.SHORT).model_copy(update={"timestamp": datetime(2026, 9, 28, 5, 0, tzinfo=UTC)}))
    assert result.executed, result.reasons
    assert trade.instrument_kind == "FUTURE"

    _flag(user.tenant_id, on=False)                                         # flag off: nothing gated
    result, _, _ = _execute(user, mode="PAPER", contract=written, rules=WRITE, quote_broker=_FnoBroker(premium=120.0),
                            signal=_signal().model_copy(update={"timestamp": datetime(2026, 9, 28, 5, 30, tzinfo=UTC)}))
    assert not any("experience level" in r for r in result.reasons)


def _structure(t, strategy, key, **rules):
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            structure = await resolve_structure(session, "NIFTY 50", _rules(**rules), strategy,
                                                SignalDirection.LONG, spread_width=2, spot=SPOT, today=TODAY)
            return await execute_structure(session, user, mode="PAPER", strategy_id="ema_rsi_scalper_1m", signal=_signal(), structure=structure,
                                           rules=_rules(**rules), target_credit_pct=None, stop_credit_pct=None, idempotency_key=key,
                                           quote_broker=_OptionBroker(), risk_config=RiskConfig(capital=1_000_000, risk_per_trade_pct=1.0))
    return _run(go())


def test_structures_unhedged_short_side_is_gated_hedged_passes():
    _load_master()
    t = _tenant(_email("struct"))
    _flag(t["tenant_id"])
    straddle = _structure(t, OptionStrategy.SHORT_STRADDLE, "n2x-straddle")
    assert not straddle.executed and any("Naked short options open at the Advanced" in r for r in straddle.reasons)
    assert all(o.status == "REJECTED" for o in straddle.orders)
    spread = _structure(t, OptionStrategy.BULL_PUT_SPREAD, "n2x-bullput")
    assert spread.executed, spread.reasons


def test_exits_are_never_gated():
    """A futures position opened before the flag came on still exits at its stop with the gate shut."""
    _load_master()
    user = _user(_email("exit"))
    fut = _resolve(FUT, SignalDirection.SHORT, spot=None)
    result, _, trade = _execute(user, mode="PAPER", contract=fut, rules=FUT, quote_broker=_FnoBroker(premium=24580.0),
                                signal=_signal(SignalDirection.SHORT))
    assert result.executed, result.reasons
    _flag(user.tenant_id)

    async def gated():
        async with _session_factory() as session:
            return await experience.entry_refusals(session, await session.get(User, user.id), ["futures"])
    assert _run(gated())                                                    # the gate is shut for new entries

    async def close():
        from app.trading.position_monitor import close_position
        async with _session_factory() as session:
            row = await session.get(TradeRecord, trade.id)
            outcome = await close_position(session, row, float(row.stop_loss) + 1.0, "Stop loss", user_id=user.id)
            return outcome, await session.get(TradeRecord, trade.id)
    outcome, row = _run(close())
    assert outcome.closed and row.exit_time is not None and row.exit_reason == "Stop loss"


def test_opt_out_rows_are_the_users_own():
    t = _tenant(_email("rows"))
    _flag(t["tenant_id"])
    _opt_out(t["user_id"], "naked_short_options", "Testing a covered idea on paper")

    async def rows():
        async with _session_factory() as session:
            return list(await session.scalars(select(ExperienceOptOutRecord).where(ExperienceOptOutRecord.user_id == t["user_id"])))
    got = _run(rows())
    assert len(got) == 1 and got[0].tenant_id == t["tenant_id"] and got[0].gate == "naked_short_options" and got[0].level == "BEGINNER"


def test_migration_is_additive_and_single_head():
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory
    backend = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1
    text = Path(script.get_revision("a4c6e8f0b2d1").path).read_text()
    assert "ROLLBACK_SAFE = True" in text and "'experience_opt_outs'" in text


# --- review M1: raw-symbol entries (webhook, public API, console, approval, underlying deployments) ---------------------

def test_raw_symbol_classification():
    g = experience.raw_symbol_gates
    fut, naked, unknown = ["futures"], ["naked_short_options"], ["unrecognised_instrument"]
    futures = ["NIFTY24OCTFUT", "CRUDEOIL", "GOLD", "NIFTY1!", "CRUDEOIL1!", "MCX:CRUDEOIL", "NIFTY FUT 30 OCT 26", "NIFTY30OCT26F",
               "NIFTY24OCTFUT.NS", "NFO:NIFTY24OCTFUT", "BANKNIFTY24OCTFUT.NFO"]
    for symbol in futures:
        assert g(symbol, "LONG") == fut, symbol
        assert g(symbol, "SHORT") == fut, symbol
    options = ["NIFTY24OCT25000CE", "NIFTY 25000 PE", "NIFTY 26000 CE 30 OCT 26", "NIFTY-OCT2026-26000-CE", "NIFTY30OCT26C26000",
               "NIFTY24OCT25000CE.NFO", "NFO:NIFTY24OCT25000PE"]
    for symbol in options:
        assert g(symbol, "SHORT") == naked, symbol
        assert g(symbol, "LONG") == [], symbol                                   # a bought option is not gated
    for symbol in ("RELIANCE", "M&M", "BAJAJ-AUTO", "NSE:RELIANCE", "RELIANCE.NS", "RELIANCE-EQ", "BSE:TCS", "TCS.BO", "NIFTY", "NIFTY 50",
                   "NIFTY BANK", "SENSEX"):
        assert g(symbol, "SHORT") == [] and g(symbol, "LONG") == [], symbol
    # Fail closed: neither in the master nor plainly cash-shaped.
    for symbol in ("SOMETHING ODD 123", "XYZ@1", "NIFTY#", "FOO:BAR", "RELIANCE.XYZ"):
        assert g(symbol, "LONG") == unknown, symbol
    # The master wins when it knows the symbol.
    assert g("NIFTY26O0124500PE", SignalDirection.SHORT, master_types=[("NFO", "PE")]) == naked
    assert g("ODDNAME", "LONG", master_types=[("NFO", "FUT")]) == fut
    assert g("GOLDM", "LONG", master_types=[("MCX", "FUTCOM")]) == fut
    assert g("SOMETHING ODD 123", "LONG", master_types=[("NSE", "EQ")]) == []
    assert experience.normalise_symbol(" mcx:crudeoil ") == ("CRUDEOIL", "MCX", None)
    assert experience.normalise_symbol("RELIANCE.NS") == ("RELIANCE", None, "NS")
    text = experience.refusal_text("unrecognised_instrument", "BEGINNER", {"closed_trades": 0, "sessions": 0})
    assert text.startswith("Unrecognised instrument; the futures gate applies") and advice_in(text) == []


def _webhook_owner(tag):
    from tests.test_tradingview_webhook import _get_token
    t = _tenant(_email(tag))
    return t, _get_token(t["headers"])


def _alert(symbol, direction="LONG", **kw):
    long = direction == "LONG"
    body = {"strategy_id": "pine_n2x", "symbol": symbol, "direction": direction, "entry": 100.0, "stop_loss": 98.0 if long else 102.0,
            "target1": 106.0 if long else 94.0, "alert_id": uuid.uuid4().hex}
    body.update(kw)
    return body


def test_webhook_entries_are_gated_by_symbol():
    t, hook = _webhook_owner("hook")
    _flag(t["tenant_id"])
    cases = {
        ("NIFTY24OCTFUT", "LONG"): "Futures open at the Intermediate",
        ("CRUDEOIL", "LONG"): "Futures open at the Intermediate",
        ("NIFTY24OCT25000CE", "SHORT"): "Naked short options open at the Advanced",
        ("NIFTY1!", "LONG"): "Futures open at the Intermediate",
        ("MCX:CRUDEOIL", "SHORT"): "Futures open at the Intermediate",
        ("NIFTY-OCT2026-26000-CE", "SHORT"): "Naked short options open at the Advanced",
        ("NIFTY30OCT26C26000", "SHORT"): "Naked short options open at the Advanced",
        ("SOMETHING ODD 123", "LONG"): "Unrecognised instrument; the futures gate applies",
    }
    for (symbol, direction), reason in cases.items():
        r = client.post(f"/api/webhooks/tradingview/{hook}", json=_alert(symbol, direction))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["executed"] is False and any(x.startswith(reason) for x in body["reasons"]), (symbol, body)
    for symbol, direction in (("NIFTY24OCT25000CE", "LONG"), ("RELIANCE", "LONG"), ("NSE:RELIANCE", "SHORT"), ("RELIANCE.NS", "LONG")):
        body = client.post(f"/api/webhooks/tradingview/{hook}", json=_alert(symbol, direction)).json()
        assert not any("experience level" in x for x in body["reasons"]), (symbol, body)
    # The day's opt-out lets the futures alert through.
    _opt_out(t["user_id"], "futures", "Practising futures alerts on paper")
    body = client.post(f"/api/webhooks/tradingview/{hook}", json=_alert("NIFTY24OCTFUT", "LONG")).json()
    assert not any("experience level" in x for x in body["reasons"]), body


def test_public_api_signal_is_gated_by_symbol():
    from tests.test_phase_k_commercial import _owner
    headers, me = _owner(_email("papi"), plan="business")
    _flag(me["tenant_id"])
    key = client.post("/api/api-keys", headers=headers, json={"name": "n2x", "scopes": ["write:signals"]}).json()["key"]
    body = {"strategy_id": "ema_rsi_scalper_1m", "symbol": "NIFTY24OCT25000PE", "direction": "SHORT", "entry": 100, "stop_loss": 110,
            "target1": 80, "idempotency_key": uuid.uuid4().hex}
    r = client.post("/api/public/v1/signals", headers={"X-API-Key": key}, json=body)
    assert r.status_code == 201, r.text
    assert r.json()["executed"] is False and any(x.startswith("Naked short options open at the Advanced") for x in r.json()["reasons"])


def test_a_webhook_futures_position_still_exits_with_the_gate_shut():
    t, hook = _webhook_owner("hookexit")
    body = client.post(f"/api/webhooks/tradingview/{hook}", json=_alert("NIFTY24OCTFUT", "LONG")).json()   # flag off: opens
    assert body["executed"] is True, body
    _flag(t["tenant_id"])
    assert client.post(f"/api/webhooks/tradingview/{hook}", json=_alert("NIFTY24OCTFUT", "LONG")).json()["executed"] is False

    async def close():
        from app.trading.position_monitor import close_position
        async with _session_factory() as session:
            row = await session.scalar(select(TradeRecord).where(TradeRecord.tenant_id == t["tenant_id"], TradeRecord.exit_time.is_(None)))
            outcome = await close_position(session, row, float(row.stop_loss), "Stop loss", user_id=t["user_id"])
            return outcome, await session.get(TradeRecord, row.id)
    outcome, row = _run(close())
    assert outcome.closed and row.exit_time is not None


# --- review L2: the IST day boundary -------------------------------------------------------------------------------------

def test_an_opt_out_ends_at_ist_midnight():
    t = _tenant(_email("midnight"))
    _flag(t["tenant_id"])
    before = datetime(2026, 10, 12, 18, 29, tzinfo=UTC)       # 23:59 IST, 12 Oct
    after = datetime(2026, 10, 12, 18, 31, tzinfo=UTC)        # 00:01 IST, 13 Oct

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            first = await experience.opt_out(session, user, "futures", "Late session practice on paper", now=before)
            at_before = await experience.entry_refusals(session, user, ["futures"], now=before)
            at_after = await experience.entry_refusals(session, user, ["futures"], now=after)
            second = await experience.opt_out(session, user, "futures", "Next day practice on paper", now=after)
            at_after_again = await experience.entry_refusals(session, user, ["futures"], now=after)
            return first, at_before, at_after, second, at_after_again
    first, at_before, at_after, second, at_after_again = _run(go())
    assert first["created"] and first["day"] == "2026-10-12" and at_before == []
    assert at_after and at_after[0].startswith("Futures open at the Intermediate")
    assert second["created"] and second["day"] == "2026-10-13" and second["opt_outs"]["total"] == 2 and at_after_again == []


# --- review L3: a user who already qualifies hears 409 before any reason check -----------------------------------------

def test_a_qualified_user_gets_409_even_with_a_short_reason(monkeypatch):
    monkeypatch.setattr(app_config, "PAPER_LADDER_MIN_TRADES", 2)
    monkeypatch.setattr(app_config, "PAPER_LADDER_MIN_SESSIONS", 2)
    t = _tenant(_email("q409"))
    _flag(t["tenant_id"])
    _closed_trades(t["tenant_id"], t["user_id"], 2, days=2)
    assert client.post("/api/paper/experience/opt-out", headers=t["headers"], json={"gate": "futures", "reason": "x"}).status_code == 409


def test_an_approved_futures_proposal_is_gated_at_execution(monkeypatch):
    """Approval execute passes a raw symbol to the pipeline: the gate applies there too."""
    from app.execution import approval
    from tests.test_z3_approval_entries import _flag_for, _price, _sig
    t = _tenant(_email("appr"))
    _flag_for(t["tenant_id"])
    _flag(t["tenant_id"])

    async def propose():
        async with _session_factory() as session:
            row = await approval.propose_entry(session, t["tenant_id"], signal=_sig(symbol="NIFTY24OCTFUT"), strategy_id="ema_rsi_scalper_1m",
                                               source="tradingview", idempotency_key=f"n2x:{uuid.uuid4().hex}", user_id=t["user_id"])
            return row.id
    action_id = _run(propose())
    _price(monkeypatch, 100.0)
    body = client.post(f"/api/ai/actions/{action_id}/approve", headers=t["headers"], json={}).json()
    assert "Futures open at the Intermediate experience level" in json.dumps(body), body

    async def trades():
        async with _session_factory() as session:
            return list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == t["tenant_id"])))
    assert _run(trades()) == []
