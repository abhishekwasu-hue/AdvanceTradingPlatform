"""Z3-b / BT3 (beta spec, Phase 0): the per-user PAPER account behind the `paper_account` flag (off by default) - starting
capital, balance and P&L after charges, positions, journal, would-have records; scoped to the user (another member of the
same organisation and another organisation see nothing of it); the PAPER-scope pause (new PAPER entries and proposals
only, never an exit); the progression ladder (PAPER stage, criteria from data, no advice); the manual /paper trigger
ported from the Trade repo with parity cases; and the Telegram /status /pause /resume /paper commands."""
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select

from app.core import config as app_config
from app.db.models import AiActionRecord, TradeRecord, User
from app.execution import approval
from app.execution.signal_execution import execute_signal_for_user
from app.paper import manual, service
from app.platform import controls
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import OPEN_NOW, _FakeBroker, _deploy, _force_signal, _get, _signal, _tenant, _worker
from tests.test_z3_approval_entries import _flag_for, _price, _propose, _sig, quiet_alerts

UTC = timezone.utc


def _run(coro):
    return asyncio.run(coro)


def _email(tag: str) -> str:
    return f"z3b-{tag}-{uuid.uuid4().hex[:6]}@example.com"


def _paper_flag(tenant_id: int, on: bool = True) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            entry = flags.get(service.FLAG) or {"on": False, "tenants": []}
            tenants = set(entry.get("tenants") or [])
            (tenants.add if on else tenants.discard)(tenant_id)
            flags[service.FLAG] = {"on": False, "tenants": sorted(tenants)}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


def _member(owner) -> dict:
    """A second user of the owner's organisation, through the ordinary invite flow."""
    email = _email("member")
    invite = client.post("/api/team/invites", headers=owner["headers"], json={"email": email, "role": "USER"})
    assert invite.status_code in (200, 201), invite.text
    token = parse_qs(urlparse(invite.json()["invite_url"]).query)["invite"][0]
    access = client.post(f"/api/auth/invite/{token}/accept", json={"password": "Member-Pass-2026"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {access}"}
    me = client.get("/api/auth/me", headers=headers).json()
    return {"headers": headers, "tenant_id": me["tenant_id"], "user_id": me["id"]}


def _user(user_id) -> User:
    async def go():
        async with _session_factory() as session:
            return await session.get(User, user_id)
    return _run(go())


def _trade(t, *, pnl=None, charges=0.0, exit_days_ago=None, mark=None, mode="PAPER", entry=100.0, quantity=10.0, entry_days_ago=1,
           price_source=None):
    async def go():
        async with _session_factory() as session:
            now = datetime.now(UTC)
            trade = TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode=mode, symbol="RELIANCE", strategy_id="ema_rsi_scalper_1m",
                                direction="LONG", entry_time=now - timedelta(days=entry_days_ago), entry_price=entry, quantity=quantity, stop_loss=95.0,
                                target1=110.0, exchange="NSE", pnl=pnl, charges=charges, mark_price=mark, price_source=price_source,
                                exit_time=(now - timedelta(days=exit_days_ago)) if exit_days_ago is not None else None,
                                exit_price=(entry + 1) if exit_days_ago is not None else None, exit_reason="Target 1" if exit_days_ago is not None else None)
            session.add(trade)
            await session.commit()
            return trade.id
    return _run(go())


# --- flag, account, isolation ------------------------------------------------------------------------------------------

def test_flag_registered_off_and_the_api_is_hidden_while_off():
    assert service.FLAG in controls.FEATURE_FLAGS and service.FLAG in controls.DEFAULT_OFF_FLAGS
    t = _tenant(_email("off"))
    assert client.get("/api/paper/account", headers=t["headers"]).status_code == 404
    assert client.get("/api/paper/ladder", headers=t["headers"]).status_code == 404
    _paper_flag(t["tenant_id"])
    assert client.get("/api/paper/account", headers=t["headers"]).status_code == 200


def test_new_user_starts_in_paper_with_the_default_capital_and_can_change_it_within_bounds():
    t = _tenant(_email("capital"))
    _paper_flag(t["tenant_id"])
    body = client.get("/api/paper/account", headers=t["headers"]).json()
    assert body["mode"] == "PAPER" and body["starting_capital"] == app_config.PAPER_STARTING_CAPITAL and body["balance"] == app_config.PAPER_STARTING_CAPITAL
    assert body["closed_trades"] == 0 and body["positions"] == [] and body["paused"] is False
    assert client.put("/api/paper/account", headers=t["headers"], json={"starting_capital": 5.0}).status_code == 422
    out = client.put("/api/paper/account", headers=t["headers"], json={"starting_capital": 250_000})
    assert out.status_code == 200 and out.json()["starting_capital"] == 250_000 and out.json()["balance"] == 250_000
    # Review M7: fixed once there is a PAPER trade - it is what the balance, drawdown and ladder are measured from.
    _trade(t, pnl=-5_000.0, charges=20.0, exit_days_ago=1)
    locked = client.put("/api/paper/account", headers=t["headers"], json={"starting_capital": 10_000_000})
    assert locked.status_code == 409 and "fixed" in locked.text
    assert client.get("/api/paper/account", headers=t["headers"]).json()["starting_capital"] == 250_000


def _numbers(value):
    """Every number anywhere in a JSON body."""
    if isinstance(value, bool):
        return set()
    if isinstance(value, (int, float)):
        return {float(value)}
    if isinstance(value, dict):
        return set().union(*(_numbers(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_numbers(v) for v in value)) if value else set()
    return set()


def test_balance_pnl_after_charges_positions_and_journal_are_the_users_own():
    owner = _tenant(_email("owner"))
    _paper_flag(owner["tenant_id"])
    member = _member(owner)
    stranger = _tenant(_email("stranger"))
    _paper_flag(stranger["tenant_id"])
    client.put("/api/paper/account", headers=owner["headers"], json={"starting_capital": 100_000})

    _trade(owner, pnl=480.0, charges=20.0, exit_days_ago=3, entry_days_ago=3)       # gross 500, net 480
    _trade(owner, pnl=-230.0, charges=30.0, exit_days_ago=2, entry_days_ago=2)      # gross -200, net -230
    open_id = _trade(owner, mark=103.0)                                             # open, +30 marked
    _trade(owner, pnl=999.0, charges=1.0, exit_days_ago=1, mode="LIVE")              # LIVE: not in the PAPER account
    _trade(member, pnl=5_000.0, charges=10.0, exit_days_ago=1)                       # same organisation, another user
    _trade(stranger, pnl=-7_000.0, charges=10.0, exit_days_ago=1)                    # another organisation
    _trade(owner, pnl=9_000.0, charges=1.0, exit_days_ago=1, price_source="client")   # priced from browser candles (H2)

    body = client.get("/api/paper/account", headers=owner["headers"]).json()
    assert body["realised"] == {"gross": 300.0, "charges": 50.0, "net": 250.0}
    assert body["balance"] == 100_250.0 and body["unrealised"] == 30.0 and body["equity"] == 100_280.0
    assert body["closed_trades"] == 2 and body["open_positions"] == 1 and [p["id"] for p in body["positions"]] == [open_id]
    assert body["excluded_trades"] == 1
    assert body["wins"] == 1 and body["losses"] == 1 and body["sessions"] == 3
    journal = body["journal"]
    assert [j["net_pnl"] for j in journal] == [-230.0, 480.0] and journal[1]["gross_pnl"] == 500.0 and journal[1]["charges"] == 20.0

    mine = client.get("/api/paper/account", headers=member["headers"]).json()
    assert mine["realised"]["net"] == 5_000.0 and mine["closed_trades"] == 1 and mine["starting_capital"] == app_config.PAPER_STARTING_CAPITAL
    theirs = client.get("/api/paper/account", headers=stranger["headers"]).json()
    assert theirs["realised"]["net"] == -7_000.0 and theirs["closed_trades"] == 1
    # Compare numbers, not substrings: ids and timestamps in the body can contain "5000" by chance.
    assert {5_000.0, -7_000.0, 9_000.0}.isdisjoint(_numbers(body))


def test_equity_drawdown_is_peak_to_trough_of_the_closed_trade_curve():
    assert service.equity_drawdown(100_000, []) == {"max_drawdown": 0.0, "max_drawdown_pct": 0.0}
    out = service.equity_drawdown(100_000, [1_000, -3_000, 500, -2_000, 10_000])
    # peak 101_000 -> trough 96_500: 4_500, 4.46 % of 101_000
    assert out == {"max_drawdown": 4_500.0, "max_drawdown_pct": round(4_500 / 101_000 * 100, 2)}


def test_rejected_and_expired_entry_proposals_are_would_have_records():
    t = _tenant(_email("wouldhave"))
    _paper_flag(t["tenant_id"])
    _flag_for(t["tenant_id"])
    rejected = _propose(t, entry=100.0)
    assert client.post(f"/api/ai/actions/{rejected}/reject", headers=t["headers"], json={"note": "no"}).status_code == 200
    expired = _propose(t, entry=101.0, now=datetime.now(UTC) - timedelta(hours=2))

    async def sweep():
        from app.ai import monitor
        async with _session_factory() as session:
            await monitor.expire_stale(session, datetime.now(UTC), tenant_id=t["tenant_id"])
    _run(sweep())
    body = client.get("/api/paper/account", headers=t["headers"]).json()
    records = {r["proposal_id"]: r for r in body["not_taken"]}
    assert records[rejected]["status"] == "REJECTED" and records[rejected]["proposal_price"] == 100.0 and records[rejected]["note"] == "no"
    assert records[expired]["status"] == "EXPIRED" and records[expired]["symbol"] == "RELIANCE"
    assert body["proposals"]["rejected_by_you"] == 1 and body["proposals"]["expired_unanswered"] == 1

    # Review L7: another member's proposals are theirs, not in this account's records or counts.
    from tests.test_z3_members import member
    mate = member(t)
    theirs = _propose(mate, entry=99.0)
    assert client.post(f"/api/ai/actions/{theirs}/reject", headers=mate["headers"], json={}).status_code == 200
    again = client.get("/api/paper/account", headers=t["headers"]).json()
    assert theirs not in {r["proposal_id"] for r in again["not_taken"]} and again["proposals"]["rejected_by_you"] == 1
    _paper_flag(mate["tenant_id"])
    mine = client.get("/api/paper/account", headers=mate["headers"]).json()
    assert {r["proposal_id"] for r in mine["not_taken"]} == {theirs} and mine["proposals"]["rejected_by_you"] == 1


# --- PAPER-scope pause -------------------------------------------------------------------------------------------------

def test_pause_stops_only_this_users_new_paper_entries_and_proposals_never_exits(monkeypatch):
    owner = _tenant(_email("pause"))
    _paper_flag(owner["tenant_id"])
    member = _member(owner)
    assert client.post("/api/paper/pause", headers=owner["headers"], json={"reason": "lunch"}).json()["paused"] is True

    async def enter(user_id):
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            result, order = await execute_signal_for_user(session, user, mode="PAPER", strategy_id="ema_rsi_scalper_1m", signal=_sig(),
                                                          idempotency_key=f"pause:{uuid.uuid4().hex}", origin="manual")
            return result.executed, json.loads(order.reasons_json)
    executed, reasons = _run(enter(owner["user_id"]))
    assert not executed and any("PAPER entries paused" in r and "lunch" in r for r in reasons)
    assert _run(enter(member["user_id"]))[0] is True                             # the member is not paused

    async def propose():
        async with _session_factory() as session:
            await approval.propose_entry(session, owner["tenant_id"], signal=_sig(), strategy_id="x", source="tradingview",
                                         idempotency_key=f"p:{uuid.uuid4().hex}", user_id=owner["user_id"])
    try:
        _run(propose())
        raise AssertionError("a paused user's proposal was raised")
    except approval.EntriesPaused as exc:
        assert "paused" in str(exc)

    # A paused user's deployment signal is not proposed; the open position's stop still closes it.
    _flag_for(owner["tenant_id"])
    dep_id = _deploy(owner)
    trade_id = _trade(owner, entry=100.0, entry_days_ago=0)

    async def attach():
        async with _session_factory() as session:
            trade = await session.get(TradeRecord, trade_id)
            trade.deployment_id = dep_id
            await session.commit()
    _run(attach())
    _force_signal(monkeypatch, lambda: _signal(ts=OPEN_NOW - timedelta(minutes=1)))
    worker = _worker(monkeypatch, _FakeBroker(ltp=94.0))                          # through the stop (95)
    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.positions_closed >= 1 and _get(TradeRecord, trade_id).exit_reason == "Stop Loss"

    async def entries():
        async with _session_factory() as session:
            return list(await session.scalars(select(AiActionRecord).where(AiActionRecord.tenant_id == owner["tenant_id"], AiActionRecord.action == "ENTRY")))
    assert _run(entries()) == []

    # Review L6: the pause does not outlive the flag.
    async def paused_now():
        async with _session_factory() as session:
            return await service.paused_reason(session, owner["user_id"])
    assert _run(paused_now()) is not None
    _paper_flag(owner["tenant_id"], on=False)
    assert _run(paused_now()) is None
    _paper_flag(owner["tenant_id"])
    assert client.post("/api/paper/resume", headers=owner["headers"]).json()["paused"] is False
    _, reasons = _run(enter(owner["user_id"]))
    assert not any("paused" in r for r in reasons), reasons        # (the risk guardian may still refuse after the stop-out)


# --- the ladder ----------------------------------------------------------------------------------------------------------

def test_ladder_shows_paper_stage_criteria_from_data_and_no_advice():
    t = _tenant(_email("ladder"))
    _paper_flag(t["tenant_id"])
    client.put("/api/paper/account", headers=t["headers"], json={"starting_capital": 100_000})
    for day in range(3):
        _trade(t, pnl=100.0, charges=10.0, exit_days_ago=day, entry_days_ago=day)
    body = client.get("/api/paper/ladder", headers=t["headers"]).json()
    assert body["stage"] == "PAPER" and [s["status"] for s in body["stages"]] == ["current", "locked"]
    crit = {c["key"]: c for c in body["criteria"]}
    assert crit["sessions"]["value"] == 3 and crit["sessions"]["target"] == app_config.PAPER_LADDER_MIN_SESSIONS and crit["sessions"]["met"] is False
    assert crit["closed_trades"]["value"] == 3 and crit["closed_trades"]["met"] is False
    assert crit["max_drawdown_pct"]["value"] == 0.0 and crit["max_drawdown_pct"]["met"] is True
    assert crit["discipline_score"]["met"] is None and body["measured"] == 3 and body["met"] == 1
    text = json.dumps(body).lower()
    for word in ("should", "recommend", "advice", "advise", "buy ", "sell "):
        assert word not in text, word


# --- the manual trigger (Trade repo paper/manual.py parity) -------------------------------------------------------------

def test_manual_parse_and_parity_with_the_trade_bot_levels_and_reward_risk():
    setup, error = manual.parse("/paper reliance long SL 2380 T 2460")
    assert error is None and setup == {"symbol": "RELIANCE", "direction": "LONG", "stop_loss": 2380.0, "target": 2460.0}
    assert manual.parse("/paper@AtpBot NIFTY short SL 25250 T 24800")[0]["direction"] == "SHORT"
    for bad in ("/paper", "/paper NIFTY sideways SL 1 T 2", "/paper NIFTY long 1 T 2", "/paper NIFTY long SL x T 2",
                "/paper NIFTY long SL 1 X 2", "/paper NIFTY long SL 1 T 2 extra", "/paper NIFTY long SL -1 T 2"):
        assert manual.parse(bad)[0] is None, bad
    # Expected values from the Trade repo's own side_errors / rr (bull put = long, bear call = short), computed there.
    cases = [("LONG", 25000, 24750, 25300, 0, 1.2), ("LONG", 25000, 25100, 25300, 1, 3.0), ("LONG", 25000, 24750, 24900, 1, 0.4),
             ("LONG", 25000, 25000, 25000, 2, None), ("SHORT", 25000, 25250, 24800, 0, 0.8), ("SHORT", 25000, 24900, 24800, 1, 2.0),
             ("SHORT", 25000, 25250, 25100, 1, 0.4), ("SHORT", 100, 100, None, 1, None)]
    for direction, price, stop, target, n_errors, rr in cases:
        assert len(manual.side_errors(direction, price, stop, target)) == n_errors, (direction, price, stop, target)
        assert manual.reward_risk(price, stop, target) == rr, (price, stop, target)
    assert manual.reward_risk(2400.5, 2380, 2460) == 2.9 and manual.reward_risk(101.3, 99.7, 104.9) == 2.25
    assert manual.RR_WARN == 3.0


# --- Telegram ------------------------------------------------------------------------------------------------------------

def test_telegram_status_pause_resume_and_paper_commands(monkeypatch):
    from tests.test_phase_be_telegram_inbound import _setup, _Telegram
    from app.telegram_inbound import service as tg

    async def never(*_a, **_k):
        return False
    monkeypatch.setattr(tg, "rate_limited", never)
    t = _setup(_email("tg"), monkeypatch, _Telegram())
    try:
        _paper_commands(t, monkeypatch)
    finally:
        quiet_alerts(t["tenant_id"])


def _paper_commands(t, monkeypatch):
    from tests.test_phase_be_telegram_inbound import _message, _post
    assert "not enabled" in _post(t, _message("987654", "/status")).json()["reply"]
    _paper_flag(t["tenant_id"])

    status = _post(t, _message("987654", "/status")).json()["reply"]
    assert status.startswith("Status") and "PAPER entries (/pause /resume): running" in status and "Exits (stop, target, time) always run" in status
    assert "paused" in _post(t, _message("987654", "/pause lunch break")).json()["reply"]
    assert "paused (lunch break)" in _post(t, _message("987654", "/status")).json()["reply"]
    assert "on again" in _post(t, _message("987654", "/resume")).json()["reply"]

    summary = _post(t, _message("987654", "/paper")).json()["reply"]
    assert summary.startswith("PAPER account: balance") and "Usage: /paper" in summary

    _price(monkeypatch, 2400.0)
    wrong = _post(t, _message("987654", "/paper RELIANCE long SL 2410 T 2460")).json()["reply"]
    assert wrong.startswith("Not proposed: long: SL (2410) must be below")
    reply = _post(t, _message("987654", "/paper RELIANCE long SL 2380 T 2460")).json()["reply"]
    assert reply.startswith("Manual PAPER setup: LONG RELIANCE at 2,400.00") and "reward:risk 3.0" in reply and "below" not in reply

    async def raised():
        async with _session_factory() as session:
            return list(await session.scalars(select(AiActionRecord).where(AiActionRecord.tenant_id == t["tenant_id"], AiActionRecord.action == "ENTRY")))
    rows = _run(raised())
    assert len(rows) == 1 and json.loads(rows[0].evidence_json)["source"] == "manual" and rows[0].status == "PROPOSED"
    low = _post(t, _message("987654", "/paper TCS short SL 2410 T 2390")).json()["reply"]
    assert "Reward:risk 1 is below 3" in low


# --- verification fixes -------------------------------------------------------------------------------------------------

def test_console_paper_execute_while_paused_answers_with_the_reason(monkeypatch):
    """Review V4: a paused user's console signal is refused with the reason (not a 500), and nothing is proposed."""
    from app.strategy_engine.registry import registry
    from tests.test_trading_api import _fake_long_signal, _sample_candles_payload
    monkeypatch.setattr(registry.get("ema_rsi_scalper_1m"), "analyze", lambda data, symbol: _fake_long_signal())
    t = _tenant(_email("pausedconsole"))
    _paper_flag(t["tenant_id"])
    _flag_for(t["tenant_id"])
    assert client.post("/api/paper/pause", headers=t["headers"], json={}).status_code == 200
    out = client.post("/api/strategies/ema_rsi_scalper_1m/paper-execute", headers=t["headers"],
                      json={"symbol": "TESTSYM", "candles": {"1min": _sample_candles_payload()}})
    assert out.status_code == 200 and out.json()["executed"] is False and "paused" in out.json()["reasons"][0]

    async def entries():
        async with _session_factory() as session:
            return list(await session.scalars(select(AiActionRecord).where(AiActionRecord.tenant_id == t["tenant_id"], AiActionRecord.action == "ENTRY")))
    assert _run(entries()) == []


def test_paper_account_on_closes_the_typed_price_route():
    """Review V3: with the PAPER account on, closing on a typed price is refused (Exit now prices from the platform)."""
    t = _tenant(_email("markpaper"))
    _paper_flag(t["tenant_id"])
    trade_id = _trade(t, entry_days_ago=0)
    out = client.post(f"/api/positions/{trade_id}/mark-price", headers=t["headers"], json={"current_price": 111.0})
    assert out.status_code == 409 and "Exit now" in out.text


def test_telegram_paper_prices_through_the_senders_own_entitlement(monkeypatch):
    """Review V1/M8: /paper asks the sender's own live price; below Tier A (NoLivePrice) nothing is proposed."""
    from app.ai import worker_bridge
    from app.telegram_inbound import service as tg
    from tests.test_phase_be_telegram_inbound import _message, _post, _setup, _Telegram

    async def never(*_a, **_k):
        return False
    monkeypatch.setattr(tg, "rate_limited", never)
    t = _setup(_email("tgown"), monkeypatch, _Telegram())
    try:
        _paper_flag(t["tenant_id"])
        asked: list = []

        async def factory(session, who):
            async def lookup(symbol, exchange="NSE"):
                asked.append((who.id, symbol, exchange))
                raise worker_bridge.NoLivePrice("no confirmed broker session of your own")
            return lookup
        monkeypatch.setattr(worker_bridge, "price_lookup_for_user", factory)
        monkeypatch.setattr(worker_bridge, "price_lookup_for_tenant", lambda *a, **k: (_ for _ in ()).throw(AssertionError("tenant lookup used")))
        reply = _post(t, _message("987654", "/paper RELIANCE long SL 2380 T 2460")).json()["reply"]
        assert "on your own broker session" in reply and "nothing was proposed" in reply
        assert asked == [(t["user_id"], "RELIANCE", "NSE")]
    finally:
        quiet_alerts(t["tenant_id"])
