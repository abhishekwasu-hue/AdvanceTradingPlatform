"""PW5: the Phase 0 proposal card. A proposal can be snoozed (its organisation only, PROPOSED only, audited, hidden from
the pending list until it wakes, announced again once, never past its expiry, never in the way of an exit), Telegram
gets a one-time "Snooze 1h" button next to Approve / Reject, and with the `broker_app_links` flag on (off by default) an
exit proposal carries the facts of the closing order for a "Trade in your broker app" link - no broker API call."""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select, update

from app.ai import monitor, proposal_card
from app.core import config as app_config
from app.db.models import AiActionRecord, AuditLogRecord, BrokerAccountRecord, NotificationRecord, StrategyDeploymentRecord, TradeRecord
from app.live import routes as live
from app.platform import controls
from tests.test_auth_api import _session_factory, client
from tests.test_phase_be_telegram_inbound import _callback, _dispatch, _no_shared_rate_limit, _post, _setup, _Telegram  # noqa: F401 - fixture
from tests.test_trading_worker import _deploy, _tenant

UTC = timezone.utc
BACKEND = Path(__file__).resolve().parents[1]


def _run(coro):
    return asyncio.run(coro)


def _email(tag: str) -> str:
    return f"pw5-{tag}-{uuid.uuid4().hex[:6]}@example.com"


def _propose(t, action="PAUSE_DEPLOYMENT", dep_id=None, trade_id=None, now=None):
    async def go():
        async with _session_factory() as session:
            rows = await monitor.raise_proposals(session, t["tenant_id"], [monitor.Proposal(dep_id, trade_id, action, f"PW5_{uuid.uuid4().hex[:8]}",
                                                                                            "Deployment lost its last 3 trades today.", {"losses": [-1, -2, -3]})],
                                                 now or datetime.now(UTC))
            return rows[0].id
    return _run(go())


def _row(action_id) -> AiActionRecord:
    async def go():
        async with _session_factory() as session:
            return await session.get(AiActionRecord, action_id)
    return _run(go())


def _audit(tenant_id):
    async def go():
        async with _session_factory() as session:
            return [(r.event, r.detail) for r in await session.scalars(select(AuditLogRecord).where(AuditLogRecord.tenant_id == tenant_id))]
    return _run(go())


def _notes(action_id):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(NotificationRecord).where(NotificationRecord.ai_action_id == action_id).order_by(NotificationRecord.id)))
    return _run(go())


def _wake(now):
    async def go():
        async with _session_factory() as session:
            return await monitor.wake_snoozed(session, now)
    return _run(go())


def _flag(on: bool):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            flags["broker_app_links"] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


def _utc(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _ids(headers, query=""):
    return [a["id"] for a in client.get(f"/api/ai/actions{query}", headers=headers).json()]


def _open_trade(t, *, direction="LONG", quantity=10.0, symbol="RELIANCE", dep_id=None, mode="PAPER"):
    async def go():
        async with _session_factory() as session:
            trade = TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode=mode, symbol=symbol, strategy_id="ema_pullback", direction=direction,
                                entry_time=datetime.now(UTC) - timedelta(hours=3), entry_price=100.0, quantity=quantity, stop_loss=95.0,
                                exchange="NSE", deployment_id=dep_id)
            session.add(trade)
            await session.commit()
            return trade.id
    return _run(go())


# --- snooze ---------------------------------------------------------------------------------------------------------

def test_snooze_hides_the_proposal_until_it_wakes_once_and_never_extends_the_expiry():
    t = _tenant(_email("snooze"))
    dep = _deploy(t, symbol="NIFTY 50")
    action_id = _propose(t, dep_id=dep)
    expires_before = _utc(_row(action_id).expires_at)
    assert action_id in _ids(t["headers"], "?status=PROPOSED")

    out = client.post(f"/api/ai/actions/{action_id}/snooze", headers=t["headers"], json={"minutes": 60})
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["status"] == "PROPOSED" and body["decided_by"] is None and body["snoozed_until"]
    wake_at = _utc(datetime.fromisoformat(body["snoozed_until"]))
    assert timedelta(minutes=59) < wake_at - datetime.now(UTC) <= timedelta(minutes=60)
    assert _utc(_row(action_id).expires_at) == expires_before                                    # the expiry did not move

    # Hidden from the pending list (and so from the badge); listed on request and in the full history.
    assert action_id not in _ids(t["headers"], "?status=PROPOSED")
    assert action_id in _ids(t["headers"], "?status=PROPOSED&snoozed=only")
    assert action_id in _ids(t["headers"], "?status=PROPOSED&snoozed=show")
    assert action_id in _ids(t["headers"])
    assert any(e == "ai_action_snoozed" and f"#{action_id}" in (d or "") and "via web" in (d or "") for e, d in _audit(t["tenant_id"]))

    # Nothing wakes early; at the time it is announced again exactly once, and it is pending again.
    announced = len(_notes(action_id))
    assert _wake(datetime.now(UTC)) == 0
    assert _wake(wake_at + timedelta(seconds=1)) >= 1
    notes = _notes(action_id)
    assert len(notes) == announced + 1 and notes[-1].title.startswith("Reminder: AI proposes") and notes[-1].event_type == "AI_PROPOSAL"
    assert _row(action_id).snoozed_until is None and _row(action_id).status == "PROPOSED"
    assert _wake(wake_at + timedelta(minutes=5)) == 0 and len(_notes(action_id)) == announced + 1       # once
    assert action_id in _ids(t["headers"], "?status=PROPOSED")
    assert _utc(_row(action_id).expires_at) == expires_before

    # A snooze past the expiry is cut at the expiry; a snoozed proposal can still be decided at any time.
    long = client.post(f"/api/ai/actions/{action_id}/snooze", headers=t["headers"], json={"until": (datetime.now(UTC) + timedelta(days=3)).isoformat()})
    assert long.status_code == 200 and _utc(datetime.fromisoformat(long.json()["snoozed_until"])) == expires_before
    assert any(e == "ai_action_snoozed" and "cut at the expiry" in (d or "") for e, d in _audit(t["tenant_id"]))
    decided = client.post(f"/api/ai/actions/{action_id}/approve", headers=t["headers"], json={})
    assert decided.status_code == 200 and decided.json()["status"] == "EXECUTED"


def test_snooze_only_for_proposed_actions_of_the_callers_organisation():
    t = _tenant(_email("own"))
    other = _tenant(_email("other"))
    action_id = _propose(t, dep_id=_deploy(t, symbol="BANKNIFTY"))
    assert client.post(f"/api/ai/actions/{action_id}/snooze", headers=other["headers"], json={"minutes": 15}).status_code == 404
    assert _row(action_id).snoozed_until is None
    assert not any(e == "ai_action_snoozed" for e, _ in _audit(other["tenant_id"]))
    assert action_id not in _ids(other["headers"], "?status=PROPOSED&snoozed=only")
    # The body is either minutes or until, within bounds.
    for bad in ({}, {"minutes": 15, "until": datetime.now(UTC).isoformat()}, {"minutes": 0}, {"minutes": 24 * 60 + 1},
                {"until": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()}, {"until": (datetime.now(UTC) + timedelta(days=8)).isoformat()}):
        assert client.post(f"/api/ai/actions/{action_id}/snooze", headers=t["headers"], json=bad).status_code == 422, bad
    # Decided proposals cannot be snoozed.
    assert client.post(f"/api/ai/actions/{action_id}/reject", headers=t["headers"], json={"note": "fine"}).status_code == 200
    refused = client.post(f"/api/ai/actions/{action_id}/snooze", headers=t["headers"], json={"minutes": 15})
    assert refused.status_code == 409 and "only PROPOSED" in refused.json()["detail"]
    assert client.post("/api/ai/actions/999999/snooze", headers=t["headers"], json={"minutes": 15}).status_code == 404


def test_a_proposal_that_expires_while_snoozed_expires_and_is_not_announced():
    t = _tenant(_email("expiry"))
    action_id = _propose(t, dep_id=_deploy(t, symbol="INFY"))

    async def soon():
        async with _session_factory() as session:
            await session.execute(update(AiActionRecord).where(AiActionRecord.id == action_id).values(expires_at=datetime.now(UTC) + timedelta(minutes=10)))
            await session.commit()
    _run(soon())
    body = client.post(f"/api/ai/actions/{action_id}/snooze", headers=t["headers"], json={"minutes": 60}).json()
    expires = _utc(_row(action_id).expires_at)
    assert _utc(datetime.fromisoformat(body["snoozed_until"])) == expires                          # cut at the expiry
    announced = len(_notes(action_id))
    _wake(expires + timedelta(seconds=1))
    row = _row(action_id)
    assert row.status == "EXPIRED" and "expired while snoozed" in (row.result or "") and len(_notes(action_id)) == announced
    assert client.post(f"/api/ai/actions/{action_id}/snooze", headers=t["headers"], json={"minutes": 5}).status_code == 409


def test_a_snoozed_exit_proposal_never_blocks_closing_the_position():
    from app.trading.position_monitor import close_position
    t = _tenant(_email("exit"))
    dep = _deploy(t, symbol="TCS")
    trade_id = _open_trade(t, symbol="TCS", dep_id=dep)
    action_id = _propose(t, action="EXIT_POSITION", dep_id=dep, trade_id=trade_id)
    assert client.post(f"/api/ai/actions/{action_id}/snooze", headers=t["headers"], json={"minutes": 60}).status_code == 200

    async def close():
        async with _session_factory() as session:
            trade = await session.get(TradeRecord, trade_id)
            outcome = await close_position(session, trade, 101.0, "closed by hand", user_id=t["user_id"])
            return outcome.closed
    assert _run(close()) is True
    async def closed_trade():
        async with _session_factory() as session:
            return await session.get(TradeRecord, trade_id)
    assert _run(closed_trade()).exit_time is not None


def test_live_stream_signature_changes_on_snooze_and_wake():
    t = _tenant(_email("live"))
    action_id = _propose(t, dep_id=_deploy(t, symbol="SBIN"))

    async def sig():
        async with _session_factory() as session:
            return (await live.signature(session, t["tenant_id"]))["ai_actions"]
    before = _run(sig())
    body = client.post(f"/api/ai/actions/{action_id}/snooze", headers=t["headers"], json={"minutes": 15}).json()
    snoozed = _run(sig())
    assert snoozed != before
    _wake(_utc(datetime.fromisoformat(body["snoozed_until"])) + timedelta(seconds=1))
    assert _run(sig()) != snoozed


# --- Telegram --------------------------------------------------------------------------------------------------------

def test_telegram_snooze_button_is_one_time_and_the_reminder_comes_with_fresh_buttons(monkeypatch):
    telegram = _Telegram()
    t = _setup(_email("tg"), monkeypatch, telegram)
    action_id = _propose(t, dep_id=_deploy(t, symbol="NIFTY 50"))
    assert _dispatch(t, telegram) == (1, 0)
    msg = next(c for c in telegram.calls if c["method"] == "sendMessage" and "reply_markup" in c)
    rows = msg["reply_markup"]["inline_keyboard"]
    assert [b["text"].split(" ")[-1] for b in rows[0]] == ["Approve", "Reject"] and rows[1][0]["text"].endswith("Snooze 1h")
    snooze = rows[1][0]["callback_data"]
    approve = rows[0][0]["callback_data"]
    assert len(snooze) <= 64

    assert _post(t, _callback("4242", snooze)).json()["handled"] == "ignored"                       # a stranger's chat
    out = _post(t, _callback("987654", snooze)).json()
    assert out["handled"] == "callback" and "snoozed until" in out["reply"]
    row = _row(action_id)
    assert row.status == "PROPOSED" and row.decided_by is None and row.snoozed_until is not None
    assert timedelta(minutes=59) < _utc(row.snoozed_until) - datetime.now(UTC) <= timedelta(minutes=60)
    assert any(e == "ai_action_snoozed" and "via Telegram chat 987654" in (d or "") for e, d in _audit(t["tenant_id"]))
    assert "Already decided" in _post(t, _callback("987654", snooze)).json()["reply"]               # replay
    assert "Already decided" in _post(t, _callback("987654", approve)).json()["reply"]              # the siblings died with it
    assert _row(action_id).status == "PROPOSED"

    # It wakes: one reminder, sent with fresh one-time buttons.
    before = len([c for c in telegram.calls if c["method"] == "sendMessage"])
    _wake(_utc(row.snoozed_until) + timedelta(seconds=1))
    _dispatch(t, telegram)
    resent = [c for c in telegram.calls if c["method"] == "sendMessage"][before:]
    assert len(resent) == 1 and "Reminder" in resent[0]["text"]
    fresh = resent[0]["reply_markup"]["inline_keyboard"]
    assert fresh[0][0]["callback_data"] != approve and fresh[1][0]["callback_data"] != snooze
    done = _post(t, _callback("987654", fresh[0][0]["callback_data"])).json()
    assert "EXECUTED" in done["reply"] and _row(action_id).status == "EXECUTED"


# --- "Trade in your broker app" ticket --------------------------------------------------------------------------------

def test_broker_app_links_flag_is_off_by_default():
    assert "broker_app_links" in controls.FEATURE_FLAGS and "broker_app_links" in controls.DEFAULT_OFF_FLAGS
    assert app_config.KITE_PUBLISHER_API_KEY == ""


def test_ticket_only_with_the_flag_and_only_for_an_exit_on_an_open_position(monkeypatch):
    t = _tenant(_email("ticket"))
    dep = _deploy(t, symbol="RELIANCE")
    trade_id = _open_trade(t, symbol="RELIANCE", dep_id=dep, direction="LONG", quantity=10)
    exit_id = _propose(t, action="EXIT_POSITION", dep_id=dep, trade_id=trade_id)
    pause_id = _propose(t, dep_id=dep)

    def listed():
        return {a["id"]: a for a in client.get("/api/ai/actions?status=PROPOSED", headers=t["headers"]).json()}

    _flag(False)
    items = listed()
    assert items[exit_id]["ticket"] is None and items[exit_id]["mode"] == "PAPER" and items[pause_id]["ticket"] is None

    _flag(True)
    try:
        items = listed()
        assert items[pause_id]["ticket"] is None                                                   # a pause has no instrument
        assert items[exit_id]["ticket"] == {"broker": "upstox", "exchange": "NSE", "symbol": "RELIANCE", "side": "SELL", "quantity": 10,
                                            "instrument_kind": "UNDERLYING", "publisher_api_key": None}
        # Zerodha: the publisher key rides along only when one is configured.
        async def to_zerodha():
            async with _session_factory() as session:
                await session.execute(update(StrategyDeploymentRecord).where(StrategyDeploymentRecord.id == dep).values(broker_name="zerodha"))
                await session.commit()
        _run(to_zerodha())
        assert listed()[exit_id]["ticket"]["broker"] == "zerodha" and listed()[exit_id]["ticket"]["publisher_api_key"] is None
        monkeypatch.setattr(app_config, "KITE_PUBLISHER_API_KEY", "publisher-key-for-tests")
        assert listed()[exit_id]["ticket"]["publisher_api_key"] == "publisher-key-for-tests"

        # Another organisation never sees the ticket, the proposal or the position.
        other = _tenant(_email("ticket-other"))
        assert exit_id not in {a["id"] for a in client.get("/api/ai/actions", headers=other["headers"]).json()}

        # A closed position has nothing to trade.
        async def close():
            async with _session_factory() as session:
                await session.execute(update(TradeRecord).where(TradeRecord.id == trade_id).values(exit_time=datetime.now(UTC), exit_price=101.0))
                await session.commit()
        _run(close())
        assert listed()[exit_id]["ticket"] is None
    finally:
        _flag(False)


def test_ticket_for_short_side_fractional_quantity_and_the_account_broker():
    trade = TradeRecord(tenant_id=1, user_id=1, mode="PAPER", symbol="INFY", strategy_id="x", direction="SHORT", entry_time=datetime.now(UTC),
                        entry_price=1.0, quantity=5.0, stop_loss=2.0, exchange="BSE", instrument_kind="UNDERLYING")
    assert proposal_card.ticket_for(trade, "Zerodha", "k")["side"] == "BUY"
    assert proposal_card.ticket_for(trade, "Zerodha", "k")["publisher_api_key"] == "k"
    assert proposal_card.ticket_for(trade, "dhan", "k")["publisher_api_key"] is None               # the key is Zerodha's only
    trade.quantity = 0.5
    assert proposal_card.ticket_for(trade, "zerodha", "k") is None                                 # no whole quantity, no ticket
    trade.quantity, trade.direction = 5.0, "FLAT"
    assert proposal_card.ticket_for(trade, "zerodha", "k") is None

    t = _tenant(_email("acct"))
    trade_id = _open_trade(t, symbol="HDFCBANK")

    async def account_broker():
        async with _session_factory() as session:
            account = BrokerAccountRecord(tenant_id=t["tenant_id"], broker_name="zerodha", account_label="pw5", status="ACTIVE", is_default=False)
            session.add(account)
            await session.flush()
            trade = await session.get(TradeRecord, trade_id)
            trade.broker_account_id = account.id
            await session.commit()
            return await proposal_card._broker_of(session, t["tenant_id"], trade, None)
    assert _run(account_broker()) == "zerodha"


# --- migration -------------------------------------------------------------------------------------------------------

def test_migration_is_the_single_head_and_chains_onto_the_previous_one():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == ["d4e8a1c6f205"]
    rev = script.get_revision("d4e8a1c6f205")
    assert rev.down_revision == "e9c1a3b5d7f9"
