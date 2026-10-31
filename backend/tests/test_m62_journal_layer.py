"""M62 JN-1 / JN-2 / JN-5 (backend): capture on PAPER entry and exit, the trader's own journal layer (notes, own tags,
rule checklist, image attachments with the size limit), manual (outside-ATP) trades, the server-drawn chart snapshots,
tenant isolation (another organisation gets 404) and the journal-status / journal-backfill logic (idempotent).
"""
import asyncio
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import func, select

from app.core.enums import SignalDirection
from app.core.models import Trade
from app.db.models import (
    JournalAttachmentRecord,
    JournalEntryRecord,
    JournalManualTradeRecord,
    MdCandleRecord,
    OrderRecord,
    TradeRecord,
    User,
)
from app.journal import capture, ops
from app.platform import controls
from tests.test_auth_api import _session_factory, client
from tests.test_trading_api import _register_user_and_get_id

BACKEND = Path(__file__).resolve().parents[1]
NOW = datetime.now(UTC)


def _run(coro):
    return asyncio.run(coro)


def _set_flag(on: bool, tenants=()) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            flags["discipline_engine"] = {"on": on, "tenants": sorted(int(t) for t in tenants)}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())
    capture.reset_flag_cache()


@pytest.fixture
def flag_on():
    _set_flag(True)
    yield
    _set_flag(False)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("CHART_ANALYZE_DATA_DIR", str(tmp_path / "private"))
    return tmp_path / "private"


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def _user(uid):
    async def go():
        async with _session_factory() as session:
            return await session.get(User, uid)
    return _run(go())


def _trade(tenant_id, user_id, **kw):
    async def go():
        async with _session_factory() as session:
            values = dict(mode="PAPER", symbol="M62TEST", strategy_id="setupM", direction="LONG", entry_time=NOW - timedelta(hours=2),
                          entry_price=100.0, quantity=10.0, stop_loss=98.0, target1=104.0)
            values.update(kw)
            rec = TradeRecord(tenant_id=tenant_id, user_id=user_id, **values)
            session.add(rec)
            await session.commit()
            return rec.id
    return _run(go())


def _png(size=(40, 30), color=(10, 120, 200)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


# --- JN-1 (a): capture fires on PAPER entry and exit ------------------------------------------------------------------

def test_capture_fires_on_paper_entry_and_exit():
    from app.trading.persistence import persist_paper_trade
    from app.trading.position_monitor import close_position
    _, uid, _ = _run(_register_user_and_get_id("m62-paper@example.com"))
    user = _user(uid)
    _set_flag(True)
    try:
        async def go():
            async with _session_factory() as session:
                rec = await persist_paper_trade(session, user, Trade(
                    symbol="NIFTY", strategy_id="setupP", direction=SignalDirection.LONG, entry_time=datetime.now(UTC) - timedelta(minutes=3),
                    entry_price=100.0, quantity=5, stop_loss=98.0, target1=104.0, expected_price=99.9))
                await capture.drain()
                async with _session_factory() as other:
                    at_entry = await other.scalar(select(JournalEntryRecord).where(JournalEntryRecord.trade_id == rec.id))
                    assert at_entry is not None and at_entry.mode == "PAPER"               # captured at the entry fill
                    assert [f["leg"] for f in json.loads(at_entry.fills_json)] == ["ENTRY"]
                outcome = await close_position(session, rec, 101.5, "Closed by hand")
                assert outcome.closed
                await capture.drain()
                async with _session_factory() as other:
                    at_exit = await other.scalar(select(JournalEntryRecord).where(JournalEntryRecord.trade_id == rec.id)
                                                 .execution_options(populate_existing=True))
                    fills = json.loads(at_exit.fills_json)
                    assert [f["leg"] for f in fills] == ["ENTRY", "EXIT"] and fills[1]["price"] == 101.5   # completed at the exit
                    assert at_exit.chart_exit_json and at_exit.mode == "PAPER"
        _run(go())
    finally:
        _set_flag(False)


# --- JN-2: notes, own tags, checklist - and another organisation gets 404 ----------------------------------------------

def test_notes_tags_checklist_and_tenant_isolation(flag_on):
    token_a, a_id, tenant_a = _run(_register_user_and_get_id("m62-a@example.com"))
    token_b, _, _ = _run(_register_user_and_get_id("m62-b@example.com"))
    trade_id = _trade(tenant_a, a_id)
    a, b = _h(token_a), _h(token_b)

    r = client.put("/api/journal/settings", headers=a, json={"custom_tags": ["Fear ", "late  entry", "fear"],
                                                              "checklist_rules": ["Trend agrees", "Waited for the commitment candle"]})
    assert r.status_code == 200, r.text
    assert r.json()["settings"]["custom_tags"] == ["fear", "late entry"]
    assert client.put("/api/journal/settings", headers=a, json={"custom_tags": ["<b>"]}).status_code == 422

    r = client.patch(f"/api/journal/trades/{trade_id}/notes", headers=a, json={"notes": "  Entered on the retest.  "})
    assert r.status_code == 200 and r.json()["notes"] == "Entered on the retest."
    r = client.patch(f"/api/journal/trades/{trade_id}/user-tags", headers=a, json={"tags": ["FEAR", "late entry"]})
    assert r.status_code == 200 and r.json()["user_tags"] == ["fear", "late entry"]
    assert client.patch(f"/api/journal/trades/{trade_id}/user-tags", headers=a, json={"tags": ["greed"]}).status_code == 422
    r = client.patch(f"/api/journal/trades/{trade_id}/checklist", headers=a, json={"items": [{"rule": "Trend agrees", "followed": True}]})
    assert r.status_code == 200
    assert r.json()["checklist"] == [{"rule": "Trend agrees", "followed": True, "retired": False},
                                     {"rule": "Waited for the commitment candle", "followed": None, "retired": False}]
    bad = client.patch(f"/api/journal/trades/{trade_id}/checklist", headers=a, json={"items": [{"rule": "A rule nobody stated", "followed": True}]})
    assert bad.status_code == 422

    entry = client.get(f"/api/journal/trades/{trade_id}", headers=a).json()
    assert entry["notes"] == "Entered on the retest." and entry["user_tags"] == ["fear", "late entry"] and entry["kind"] == "trade"
    assert entry["checklist"][0]["followed"] is True
    plan = entry["plan_view"]
    assert plan["invalidation"] == 98.0 and plan["target"] == 104.0 and plan["setup"] == "setupM" and plan["area"] is None

    # null clears an answer; the trade row keeps every pipeline column (only notes was written)
    r = client.patch(f"/api/journal/trades/{trade_id}/checklist", headers=a, json={"items": [{"rule": "Trend agrees", "followed": None}]})
    assert r.json()["checklist"][0]["followed"] is None

    async def row():
        async with _session_factory() as session:
            return await session.get(TradeRecord, trade_id)
    t = _run(row())
    assert t.notes == "Entered on the retest." and t.entry_price == 100.0 and t.stop_loss == 98.0 and t.exit_time is None

    # another organisation: every route answers 404, exactly like a trade that does not exist
    for method, path, body in (("patch", "notes", {"notes": "x"}), ("patch", "user-tags", {"tags": []}),
                               ("patch", "checklist", {"items": [{"rule": "Trend agrees", "followed": True}]})):
        assert getattr(client, method)(f"/api/journal/trades/{trade_id}/{path}", headers=b, json=body).status_code == 404
    assert client.get(f"/api/journal/trades/{trade_id}/chart", headers=b).status_code == 404
    assert client.post(f"/api/journal/trades/{trade_id}/attachments", headers=b, files={"file": ("a.png", _png(), "image/png")}).status_code == 404
    assert _run(row()).notes == "Entered on the retest."


def test_new_routes_answer_503_with_the_flag_off():
    token, uid, tenant = _run(_register_user_and_get_id("m62-off@example.com"))
    trade_id = _trade(tenant, uid)
    for method, path in (("patch", f"/api/journal/trades/{trade_id}/notes"), ("get", "/api/journal/manual"),
                         ("get", f"/api/journal/trades/{trade_id}/chart")):
        kwargs = {"json": {"notes": "x"}} if method == "patch" else {}
        r = getattr(client, method)(path, headers=_h(token), **kwargs)
        assert r.status_code == 503 and r.headers.get("X-Feature-Disabled") == "discipline_engine"


# --- JN-2: attachments (size limit, count limit, private) --------------------------------------------------------------

def test_attachments_crud_size_limit_and_isolation(flag_on, store):
    token_a, a_id, tenant_a = _run(_register_user_and_get_id("m62-files-a@example.com"))
    token_b, _, _ = _run(_register_user_and_get_id("m62-files-b@example.com"))
    a, b = _h(token_a), _h(token_b)
    trade_id = _trade(tenant_a, a_id)

    r = client.post(f"/api/journal/trades/{trade_id}/attachments", headers=a, files={"file": ("shot.png", _png(), "image/png")})
    assert r.status_code == 201, r.text
    att = r.json()
    assert att["trade_id"] == trade_id and att["width"] == 40 and att["height"] == 30
    files = list(store.rglob("*.png"))
    assert len(files) == 1 and files[0].parent.name == str(tenant_a)          # private per organisation
    got = client.get(f"/api/journal/attachments/{att['id']}", headers=a)
    assert got.status_code == 200 and got.headers["content-type"] == "image/png" and got.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert client.get(f"/api/journal/trades/{trade_id}", headers=a).json()["attachments"][0]["id"] == att["id"]

    # another organisation can neither read nor delete it
    assert client.get(f"/api/journal/attachments/{att['id']}", headers=b).status_code == 404
    assert client.delete(f"/api/journal/attachments/{att['id']}", headers=b).status_code == 404

    # not an image
    assert client.post(f"/api/journal/trades/{trade_id}/attachments", headers=a, files={"file": ("x.png", b"hello", "image/png")}).status_code == 415

    # the size limit is the trader's own setting (default 5 MB): 0.5 MB refuses a larger upload before it is decoded
    assert client.get("/api/journal/settings", headers=a).json()["settings"]["attachment_max_mb"] == 5.0
    assert client.put("/api/journal/settings", headers=a, json={"attachment_max_mb": 0.5}).status_code == 200
    big = b"\x89PNG\r\n\x1a\n" + b"\0" * (600 * 1024)
    r = client.post(f"/api/journal/trades/{trade_id}/attachments", headers=a, files={"file": ("big.png", big, "image/png")})
    assert r.status_code == 413 and "0.5 MB" in r.json()["detail"]
    assert client.put("/api/journal/settings", headers=a, json={"attachment_max_mb": 20}).status_code == 422    # above the request limit

    # the count limit
    assert client.put("/api/journal/settings", headers=a, json={"attachments_per_trade": 1}).status_code == 200
    assert client.post(f"/api/journal/trades/{trade_id}/attachments", headers=a, files={"file": ("2.png", _png(), "image/png")}).status_code == 409

    assert client.delete(f"/api/journal/attachments/{att['id']}", headers=a).status_code == 200
    assert list(store.rglob("*.png")) == [] and client.get(f"/api/journal/attachments/{att['id']}", headers=a).status_code == 404


# --- JN-2: manual (outside-ATP) trades ---------------------------------------------------------------------------------

def test_manual_trade_is_journal_data_only(flag_on, store):
    token_a, a_id, tenant_a = _run(_register_user_and_get_id("m62-manual-a@example.com"))
    token_b, _, _ = _run(_register_user_and_get_id("m62-manual-b@example.com"))
    a, b = _h(token_a), _h(token_b)

    async def counts():
        async with _session_factory() as session:
            trades = await session.scalar(select(func.count()).select_from(TradeRecord).where(TradeRecord.tenant_id == tenant_a))
            orders = await session.scalar(select(func.count()).select_from(OrderRecord).where(OrderRecord.tenant_id == tenant_a))
            return trades, orders
    before = _run(counts())

    entry_at = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    exit_at = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    body = {"symbol": "reliance", "direction": "SHORT", "quantity": 4, "entry_time": entry_at, "entry_price": 2500.0, "stop_loss": 2520.0,
            "exit_time": exit_at, "exit_price": 2480.0, "charges": 20, "setup": "Seller area", "timeframe": "15min", "notes": "Outside ATP"}
    r = client.post("/api/journal/manual", headers=a, json=body)
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["manual"] is True and m["source"] == "OUTSIDE_ATP" and m["kind"] == "manual" and m["symbol"] == "RELIANCE"
    assert m["pnl"] == 60.0 and m["r_multiple"] == 0.75                       # (2500 - 2480) x 4 - 20 = 60; risk (2520 - 2500) x 4 = 80
    assert m["plan_view"]["target"] is None                                   # nothing he did not give is filled in
    assert _run(counts()) == before                                           # no trade row, no order

    # validation: exit time and price go together; never in the future
    assert client.post("/api/journal/manual", headers=a, json={**body, "exit_price": None}).status_code == 422
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    assert client.post("/api/journal/manual", headers=a, json={**body, "entry_time": future, "exit_time": None, "exit_price": None}).status_code == 422

    mid = m["id"]
    listed = client.get("/api/journal/manual", headers=a).json()["entries"]
    assert [e["id"] for e in listed] == [mid]
    week = client.get("/api/journal/week", headers=a).json()
    if datetime.fromisoformat(week["start"]) <= datetime.fromisoformat(entry_at) < datetime.fromisoformat(week["end"]):
        assert [e["id"] for e in week["manual_entries"]] == [mid]

    # his layer works on a manual entry too
    client.put("/api/journal/settings", headers=a, json={"custom_tags": ["impatience"], "checklist_rules": ["Area marked before entry"]})
    assert client.patch(f"/api/journal/manual/{mid}/notes", headers=a, json={"notes": "Edited later"}).json()["notes"] == "Edited later"
    assert client.patch(f"/api/journal/manual/{mid}/user-tags", headers=a, json={"tags": ["impatience"]}).json()["user_tags"] == ["impatience"]
    assert client.patch(f"/api/journal/manual/{mid}/checklist", headers=a,
                        json={"items": [{"rule": "Area marked before entry", "followed": False}]}).json()["checklist"][0]["followed"] is False
    r = client.post(f"/api/journal/manual/{mid}/attachments", headers=a, files={"file": ("m.png", _png(), "image/png")})
    assert r.status_code == 201 and r.json()["manual_trade_id"] == mid

    # another organisation: 404 everywhere
    assert client.get(f"/api/journal/manual/{mid}", headers=b).status_code == 404
    assert client.patch(f"/api/journal/manual/{mid}/notes", headers=b, json={"notes": "x"}).status_code == 404
    assert client.delete(f"/api/journal/manual/{mid}", headers=b).status_code == 404
    assert client.get("/api/journal/manual", headers=b).json()["entries"] == []

    assert client.delete(f"/api/journal/manual/{mid}", headers=a).status_code == 200
    assert client.get(f"/api/journal/manual/{mid}", headers=a).status_code == 404
    assert list(store.rglob("*.png")) == []                                   # its image went with it

    async def left():
        async with _session_factory() as session:
            return (await session.scalar(select(func.count()).select_from(JournalManualTradeRecord).where(JournalManualTradeRecord.id == mid)),
                    await session.scalar(select(func.count()).select_from(JournalAttachmentRecord).where(JournalAttachmentRecord.manual_trade_id == mid)))
    assert _run(left()) == (0, 0)


# --- JN-2: chart snapshots, server-drawn from stored candles -----------------------------------------------------------

def test_chart_snapshot_is_drawn_from_stored_candles_up_to_the_instant(flag_on):
    token, uid, tenant = _run(_register_user_and_get_id("m62-chart@example.com"))
    entry_at = NOW - timedelta(minutes=30)
    trade_id = _trade(tenant, uid, symbol="M62CHART", entry_time=entry_at)

    async def seed():
        async with _session_factory() as session:
            for i in range(40):
                ts = entry_at - timedelta(minutes=39 - i)
                session.add(MdCandleRecord(instrument_key="NSE:M62CHART", timeframe="1m", ts=ts, source="test", version=1,
                                           open=99.0, high=101.0, low=98.5, close=100.0 + (i % 3) * 0.1, volume=1))
            await session.commit()
    _run(seed())
    r = client.get(f"/api/journal/trades/{trade_id}/chart?at=entry", headers=_h(token))
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert client.get(f"/api/journal/trades/{trade_id}/chart?at=exit", headers=_h(token)).status_code == 404    # still open
    other = _trade(tenant, uid, symbol="M62NOBARS")
    assert client.get(f"/api/journal/trades/{other}/chart", headers=_h(token)).status_code == 404               # no stored candles


def test_chart_plan_marks_entry_exit_and_stop():
    from app.journal import entries
    t = TradeRecord(id=1, tenant_id=1, user_id=1, mode="PAPER", symbol="ABC", strategy_id="s", direction="LONG", entry_time=NOW - timedelta(hours=1),
                    entry_price=100.0, quantity=1, stop_loss=99.0, initial_stop_loss=97.0, target1=None, exit_time=NOW, exit_price=103.0)
    plan = entries.chart_plan(entries.Ref(trade=t), "exit", timeframe="5min")
    assert plan.levels == {"ENTRY": 100.0, "SL": 97.0, "EXIT": 103.0} and plan.at == NOW and plan.timeframe == "5min"
    assert "TGT" not in plan.levels                                            # no target unless the trade carried one
    from app.ai import chart_png
    bars = [chart_png.Bar(100, 101, 99, 100.5)] * 5
    assert chart_png.render(chart_png.ChartSpec(bars, plan.levels))[:8] == b"\x89PNG\r\n\x1a\n"


# --- JN-1 (b): journal-status / journal-backfill on a fixture (idempotent) ---------------------------------------------

def test_journal_status_and_backfill_are_idempotent():
    _, a_id, tenant_a = _run(_register_user_and_get_id("m62-ops-a@example.com"))
    _, b_id, tenant_b = _run(_register_user_and_get_id("m62-ops-b@example.com"))
    closed = dict(exit_time=NOW - timedelta(hours=1), exit_price=101.0, exit_reason="Closed by hand", pnl=10.0)
    a_trades = [_trade(tenant_a, a_id, **closed) for _ in range(3)]
    _trade(tenant_a, a_id)                                                    # open: not backfilled
    _trade(tenant_b, b_id, **closed)
    _set_flag(False, tenants=[tenant_a])
    try:
        async def go():
            async with _session_factory() as session:
                report = await ops.status(session)
                mine = {t["tenant_id"]: t for t in report["tenants"] if t["tenant_id"] in (tenant_a, tenant_b)}
                assert report["flag"] == {"on": False, "tenants": [tenant_a]}
                assert mine[tenant_a]["flag_on"] and not mine[tenant_b]["flag_on"]
                assert mine[tenant_a]["closed_trades"] == 3 and mine[tenant_a]["closed_without_journal"] == 3 and mine[tenant_a]["journal_rows"] == 0
                lines = ops.format_status(report)
                assert lines[0].startswith("discipline_engine: OFF for everyone; ON for organisation(s)")

                first = await ops.backfill(session, batch=2)
                row = next(t for t in first["tenants"] if t["tenant_id"] == tenant_a)
                assert row == {"tenant_id": tenant_a, "missing_before": 3, "created": 3, "missing_after": 0}
                assert all(t["tenant_id"] != tenant_b for t in first["tenants"])     # flag off there: untouched
                again = await ops.backfill(session, tenant_a)
                assert again["tenants"] == [{"tenant_id": tenant_a, "missing_before": 0, "created": 0, "missing_after": 0}]
                with pytest.raises(ops.Refused):
                    await ops.backfill(session, tenant_b)
                rows = list(await session.scalars(select(JournalEntryRecord).where(JournalEntryRecord.trade_id.in_(a_trades))))
                assert len(rows) == 3 and all([f["leg"] for f in json.loads(r.fills_json)] == ["ENTRY", "EXIT"] for r in rows)
                after = {t["tenant_id"]: t for t in (await ops.status(session))["tenants"]}
                assert after[tenant_a]["closed_without_journal"] == 0 and after[tenant_a]["journal_rows"] == 3
                assert after[tenant_b]["closed_without_journal"] == 1
        _run(go())
    finally:
        _set_flag(False)


def test_atp_sh_has_the_two_journal_subcommands():
    sh = (BACKEND.parent / "deploy" / "atp.sh").read_text()
    assert "journal-status) cmd_journal_status" in sh and "journal-backfill) cmd_journal_backfill" in sh
    assert "dc exec -T backend python -m app.journal.ops status" in sh and "python -m app.journal.ops backfill" in sh
    assert "#   /opt/atp/deploy/atp.sh journal-status" in sh and "#   /opt/atp/deploy/atp.sh journal-backfill [--tenant ID]" in sh


def test_flag_stays_off_by_default():
    assert "discipline_engine" in controls.DEFAULT_OFF_FLAGS


def test_migration_is_additive_and_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1
    rev = script.get_revision("ef28ae662a93")
    text = Path(rev.path).read_text()
    assert "ROLLBACK_SAFE = True" in text and "'journal_manual_trades'" in text and "'journal_attachments'" in text
