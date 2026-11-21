"""P2-8 Journal v2 J1 (docs/plans/journal-v2-PLAN.md section 2): broker-app trades synced into the journal (matched to
ATP's own orders, never twice), the stored chart picture of an ATP trade (erased with the account), a spread as ONE
entry, and the plan-vs-actual lines (differences only, never advice). Every user, symbol and fill here is made up."""
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import func, select

from app.db.models import JournalBrokerFillRecord, JournalManualTradeRecord, JournalSnapshotRecord, MdCandleRecord, User
from app.journal import broker_sync, plan_actual, snapshots, spread
from app.journal.broker_sync import BrokerFill, OpenEntry, plan_sync
from app.journal.review import advice_in
from tests.test_auth_api import _session_factory, client
from tests.test_m62_journal_layer import _h, _run, _trade, flag_on, store  # noqa: F401  (pytest fixtures)
from tests.test_trading_api import _register_user_and_get_id

BACKEND = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 11, 20, 4, 0, tzinfo=UTC)          # a made-up morning


def _fill(fid, side, qty, price, minutes, symbol="DEMOX", order=None, broker="upstox"):
    return BrokerFill(broker, fid, order or f"O{fid}", symbol, side, float(qty), float(price), T0 + timedelta(minutes=minutes))


# --- J1-1: the pure sync plan -----------------------------------------------------------------------------------------

def test_plan_sync_matches_atp_orders_skips_seen_and_pairs_the_rest():
    fills = [_fill("F1", "BUY", 10, 100, 0), _fill("F2", "SELL", 10, 104, 30),
             _fill("F3", "BUY", 5, 50, 5, symbol="DEMOY", order="ATP-ORDER-1"),       # ATP placed this order
             _fill("F4", "SELL", 3, 200, 10, symbol="DEMOZ"), _fill("F4", "SELL", 3, 200, 10, symbol="DEMOZ"),   # sent twice
             _fill("F5", "BUY", 1, 9, 1, symbol="DEMOW")]
    plan = plan_sync(fills, {"ATP-ORDER-1": 77}, {("upstox", "F5")}, [])
    assert [(f.fill_id, tid) for f, tid in plan.matched] == [("F3", 77)]
    assert plan.seen == 2                                                            # F4's copy and the stored F5
    trades = {t.symbol: (t.direction, t.quantity, t.entry_price, t.exit_price) for t, _ in plan.trades}
    assert trades == {"DEMOX": ("LONG", 10.0, 100.0, 104.0), "DEMOZ": ("SHORT", 3.0, 200.0, None)}
    assert plan_sync(fills, {"ATP-ORDER-1": 77}, {("upstox", "F5")}, []).trades[0][0].key() == plan.trades[0][0].key()   # same in, same out


def test_plan_sync_closes_open_broker_entries_first_and_splits_a_partial_close():
    open_long = OpenEntry(9, "DEMOX", "LONG", 10.0, T0 - timedelta(days=1))
    plan = plan_sync([_fill("C1", "SELL", 4, 110, 0), _fill("C2", "SELL", 2, 112, 5)], {}, set(), [open_long])
    (c,) = plan.closes
    assert (c.entry_id, c.quantity, c.rest, c.exit_time) == (9, 6.0, 4.0, T0 + timedelta(minutes=5))
    assert c.exit_price == round((4 * 110 + 2 * 112) / 6, 4) and not plan.trades
    # a sell bigger than the open long closes it and opens a short with the rest
    plan = plan_sync([_fill("C3", "SELL", 15, 120, 0)], {}, set(), [open_long])
    assert plan.closes[0].rest == 0.0 and [(t.direction, t.quantity) for t, _ in plan.trades] == [("SHORT", 5.0)]


def test_to_fill_refuses_incomplete_rows():
    now = T0
    good = SimpleNamespace(trade_id="X1", order_id="O1", symbol="demox", transaction_type=SimpleNamespace(value="BUY"), quantity=2,
                           price=10.5, timestamp=datetime(2026, 11, 20, 9, 30))
    f = broker_sync.to_fill("upstox", good, now)
    assert (f.symbol, f.side, f.at.tzinfo is not None) == ("DEMOX", "BUY", True)
    for bad in (dict(trade_id=""), dict(quantity=0), dict(price=-1), dict(transaction_type="HOLD")):
        assert broker_sync.to_fill("upstox", SimpleNamespace(**{**good.__dict__, **bad}), now) is None


# --- J1-1: the sync through the route ---------------------------------------------------------------------------------

def _count(model, **where):
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(func.count()).select_from(model).filter_by(**where))
    return _run(go())


def test_broker_sync_route_journals_outside_trades_once_and_matches_atp_orders(flag_on, monkeypatch):  # noqa: F811
    token, uid, tenant = _run(_register_user_and_get_id("p28-sync@example.com"))
    atp = _trade(tenant, uid, symbol="DEMOY", broker_order_id="ATP-ORDER-9", mode="LIVE")
    now = datetime.now(UTC)
    book = [_fill("S1", "BUY", 10, 100, 0), _fill("S2", "SELL", 10, 103, 20),
            _fill("S3", "BUY", 5, 50, 1, symbol="DEMOY", order="ATP-ORDER-9")]
    book = [BrokerFill(f.broker, f.fill_id, f.order_id, f.symbol, f.side, f.quantity, f.price, now - timedelta(hours=2) + (f.at - T0))
            for f in book]

    async def own_records(session, user, *a, **k):
        return [SimpleNamespace(broker_name="upstox", tenant_id=user.tenant_id, user_id=user.id)]

    async def read_book(record, at):
        return list(book), None
    monkeypatch.setattr("app.market_data.entitlement.own_records", own_records)
    monkeypatch.setattr(broker_sync, "_read_book", read_book)
    r = client.post("/api/journal/broker-sync", headers=_h(token))
    assert r.status_code == 200, r.text
    assert (r.json()["added"], r.json()["matched"], r.json()["already"]) == (1, 1, 0)
    again = client.post("/api/journal/broker-sync", headers=_h(token)).json()
    assert (again["added"], again["matched"], again["already"]) == (0, 0, 3)          # nothing twice
    assert _count(JournalManualTradeRecord, user_id=uid, origin="BROKER") == 1
    assert _count(JournalBrokerFillRecord, user_id=uid, status="MATCHED", trade_id=atp) == 1
    rows = client.get("/api/journal/entries", headers=_h(token)).json()["entries"]
    synced = [x for x in rows if x["kind"] == "manual"]
    assert len(synced) == 1 and synced[0]["source_label"].startswith("From your broker") and synced[0]["origin"] == "BROKER"
    # another member never reads this member's synced entries
    other, _, _ = _run(_register_user_and_get_id("p28-sync-other@example.com"))
    assert client.get(f"/api/journal/manual/{synced[0]['id']}", headers=_h(other)).status_code == 404


def test_broker_sync_says_how_to_connect_when_no_broker_is_linked(flag_on):  # noqa: F811
    token, _, _ = _run(_register_user_and_get_id("p28-sync-none@example.com"))
    body = client.post("/api/journal/broker-sync", headers=_h(token)).json()
    assert body["brokers"] == 0 and body["added"] == 0 and "Sign in to your broker" in body["messages"][0]


def test_broker_sync_route_is_behind_the_flag():
    token, _, _ = _run(_register_user_and_get_id("p28-sync-off@example.com"))
    assert client.post("/api/journal/broker-sync", headers=_h(token)).status_code == 503


def test_worker_sync_is_due_once_after_the_close_on_trading_days():
    ist_close = datetime(2026, 11, 20, 10, 20, tzinfo=UTC)   # 15:50 IST, a Friday
    assert broker_sync.due_day(ist_close, set()) == "2026-11-20"
    assert broker_sync.due_day(ist_close - timedelta(minutes=10), set()) is None    # 15:40 IST: not yet
    assert broker_sync.due_day(ist_close + timedelta(days=1), set()) is None        # Saturday


# --- J1-2: the stored chart picture -----------------------------------------------------------------------------------

def test_chart_picture_is_stored_on_first_draw_and_erased_with_the_account(flag_on, store, monkeypatch):  # noqa: F811
    token, uid, tenant = _run(_register_user_and_get_id("p28-snap@example.com"))
    entry_at = datetime.now(UTC) - timedelta(minutes=45)
    trade_id = _trade(tenant, uid, symbol="P28SNAP", entry_time=entry_at)

    async def seed():
        async with _session_factory() as session:
            for i in range(40):
                session.add(MdCandleRecord(instrument_key="NSE:P28SNAP", timeframe="1m", ts=entry_at - timedelta(minutes=39 - i),
                                           source="test", version=1, open=99.0, high=101.0, low=98.5, close=100.0, volume=1))
            await session.commit()
    _run(seed())
    first = client.get(f"/api/journal/trades/{trade_id}/chart?at=entry", headers=_h(token))
    assert first.status_code == 200 and first.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert _count(JournalSnapshotRecord, trade_id=trade_id, at="entry") == 1

    async def never(*a, **k):
        raise AssertionError("drawn again")
    monkeypatch.setattr("app.journal.entries.chart_png", never)
    second = client.get(f"/api/journal/trades/{trade_id}/chart?at=entry", headers=_h(token))
    assert second.status_code == 200 and second.content == first.content          # read from the store
    entry = client.get(f"/api/journal/trades/{trade_id}", headers=_h(token)).json()
    assert entry["snapshots"] == {"entry": True, "exit": False}

    async def erase():
        from app.retention.service import _erase_journal
        async with _session_factory() as session:
            user = await session.get(User, uid)
            path = await session.scalar(select(JournalSnapshotRecord.image_path).where(JournalSnapshotRecord.trade_id == trade_id))
            await _erase_journal(session, user)
            await session.commit()
            return path
    path = _run(erase())
    assert _count(JournalSnapshotRecord, user_id=uid) == 0 and not (store / path).exists()


def test_a_picture_seconds_after_the_fill_is_shown_but_not_kept(flag_on, store):  # noqa: F811
    token, uid, tenant = _run(_register_user_and_get_id("p28-snap-new@example.com"))
    entry_at = datetime.now(UTC) - timedelta(minutes=2)
    trade_id = _trade(tenant, uid, symbol="P28FRESH", entry_time=entry_at)

    async def seed():
        async with _session_factory() as session:
            for i in range(20):
                session.add(MdCandleRecord(instrument_key="NSE:P28FRESH", timeframe="1m", ts=entry_at - timedelta(minutes=19 - i),
                                           source="test", version=1, open=99.0, high=101.0, low=98.5, close=100.0, volume=1))
            await session.commit()
    _run(seed())
    assert client.get(f"/api/journal/trades/{trade_id}/chart?at=entry", headers=_h(token)).status_code == 200
    assert _count(JournalSnapshotRecord, trade_id=trade_id) == 0


def _flag_named(name: str, value: dict | None) -> dict | None:
    """Sets one stored flag (None removes it) and returns what was stored before."""
    async def go():
        from app.platform import controls
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            before = flags.get(name)
            if value is None:
                flags.pop(name, None)
            else:
                flags[name] = value
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
            return before
    return _run(go())


def test_worker_retries_a_failed_read_at_most_three_times(monkeypatch):
    calls = []

    async def fake_sync(session, user, *, now=None):
        calls.append(user.id)
        return {"messages": ["The broker did not send its trade list just now."], "read": 0}
    monkeypatch.setattr(broker_sync, "sync_user", fake_sync)

    async def linked():
        from app.db.models import BrokerCredentialRecord
        _, uid, tenant = await _register_user_and_get_id("p28-worker@example.com")
        async with _session_factory() as session:
            session.add(BrokerCredentialRecord(tenant_id=tenant, user_id=uid, broker_name="upstox", account_label="primary",
                                               encrypted_payload="made-up"))
            await session.commit()
        return uid
    uid = _run(linked())
    before = {n: _flag_named(n, {"on": True, "tenants": []}) for n in ("discipline_engine", "ui_v2")}
    after_close = datetime(2026, 11, 20, 10, 20, tzinfo=UTC)             # 15:50 IST on a trading day

    async def run(at):
        async with _session_factory() as session:
            return await broker_sync.run_due(session, at)
    try:
        for i in range(5):
            _run(run(after_close + timedelta(minutes=5 * i)))
        assert calls.count(uid) == 3                                        # three tries, then the day is left alone
    finally:
        for name, value in before.items():
            _flag_named(name, value)


def test_snapshot_view_lists_both_points():
    assert snapshots.view([SimpleNamespace(at="exit")]) == {"entry": False, "exit": True}


# --- J1-3: a spread is one entry --------------------------------------------------------------------------------------

def _leg(i, direction, price, pnl, meta=None, gid="G1"):
    return SimpleNamespace(id=i, leg_group_id=gid, direction=direction, entry_price=price, quantity=50.0, pnl=pnl, charges=10.0,
                           exit_time=T0 if pnl is not None else None, group_meta=json.dumps(meta) if meta else None,
                           option_strategy="BULL_PUT_SPREAD", underlying_symbol="DEMOIDX")


def test_spread_summary_combines_legs_credit_and_max_loss():
    meta = {"net_credit": 30.0, "max_loss": 70.0, "quantity": 50}
    s = spread.spread_summary([_leg(2, "LONG", 20.0, -900.0), _leg(1, "SHORT", 50.0, -3000.0, meta)])
    assert (s["legs"], s["closed"], s["net_pnl"], s["credit"], s["credit_kind"]) == (2, True, -3900.0, 1500.0, "credit")
    assert (s["planned_max_loss"], s["actual_loss"], s["structure"], s["charges"]) == (3500.0, 3900.0, "Bull put spread", 20.0)
    assert plan_actual.for_spread(s) == ["Loss -₹3,900.00; the structure's planned max loss was -₹3,500.00."]
    open_one = spread.spread_summary([_leg(1, "SHORT", 50.0, None, meta), _leg(2, "LONG", 20.0, 100.0)])
    assert open_one["net_pnl"] is None and open_one["actual_loss"] is None and plan_actual.for_spread(open_one) == []


def test_fold_rows_keeps_one_row_per_structure():
    rows = [{"kind": "trade", "id": 5, "leg_group_id": "G1", "symbol": "LEG-B", "tags": [], "entry_time": "2026-11-20T09:20:00+00:00"},
            {"kind": "manual", "id": 3, "leg_group_id": None, "symbol": "OTHER", "tags": []},
            {"kind": "trade", "id": 4, "leg_group_id": "G1", "symbol": "LEG-A", "tags": [{"tag": "x"}], "entry_time": "2026-11-20T09:15:00+00:00"},
            {"kind": "trade", "id": 6, "leg_group_id": None, "symbol": "SINGLE", "tags": []}]
    summary = {"G1": {"legs": 2, "net_pnl": 120.0, "structure": "Bull put spread", "underlying": "DEMOIDX"}}
    out = spread.fold_rows(rows, summary)
    assert [r["symbol"] for r in out] == ["DEMOIDX Bull put spread", "OTHER", "SINGLE"]
    head = out[0]
    assert head["id"] == 4 and [leg["id"] for leg in head["legs"]] == [4, 5] and head["pnl"] == 120.0 and head["outcome"] == "win"
    assert head["entry_time"] == "2026-11-20T09:15:00+00:00" and head["tags"] == [{"tag": "x"}]
    assert [r["symbol"] for r in spread.fold_rows(rows, summary, "loss")] == ["OTHER", "SINGLE"]   # the structure won


def test_entries_route_folds_legs_and_entry_sheet_shows_the_structure(flag_on):  # noqa: F811
    token, uid, tenant = _run(_register_user_and_get_id("p28-spread@example.com"))
    meta = json.dumps({"net_credit": 30.0, "max_loss": 70.0, "quantity": 50})
    common = dict(leg_group_id="P28G", option_strategy="BULL_PUT_SPREAD", underlying_symbol="DEMOIDX", instrument_kind="OPTION",
                  quantity=50.0, exit_time=datetime.now(UTC) - timedelta(minutes=5), exit_reason="group target")
    a = _trade(tenant, uid, symbol="DEMOIDX 100 PE", direction="SHORT", entry_price=50.0, stop_loss=80.0, exit_price=40.0, pnl=500.0,
               group_meta=meta, **common)
    _trade(tenant, uid, symbol="DEMOIDX 90 PE", direction="LONG", entry_price=20.0, stop_loss=1.0, exit_price=17.0, pnl=-150.0, **common)
    rows = client.get("/api/journal/entries", headers=_h(token)).json()["entries"]
    (row,) = [r for r in rows if r.get("spread")]
    assert row["symbol"] == "DEMOIDX Bull put spread" and len(row["legs"]) == 2 and row["pnl"] == 350.0
    assert sum(1 for r in rows if r.get("leg_group_id") == "P28G") == 1
    sheet = client.get(f"/api/journal/trades/{a}", headers=_h(token)).json()
    assert sheet["spread"]["credit"] == 1500.0 and sheet["spread"]["planned_max_loss"] == 3500.0
    assert len(sheet["spread"]["leg_rows"]) == 2 and sheet["plan_actual"] == []                      # a win: no difference


# --- J1-4: plan vs actual ---------------------------------------------------------------------------------------------

def _pva(**kw):
    base = dict(direction="LONG", plan={"entry": 100.0, "entry_source": "signal", "stop": 98.0, "target1": 106.0}, entry_price=100.0,
                quantity=10.0, stop_now=98.0, stop_history=[], exit_price=106.0, exit_reason="target", exit_filled_quantity=None)
    base.update(kw)
    return plan_actual.plan_vs_actual(**base)


def test_plan_vs_actual_lists_only_differences():
    assert _pva() == []                                                              # as planned: nothing to say
    assert _pva(entry_price=100.5) == ["Filled at 100.50; your signal's price was 100.00 (+0.50 per unit)."]
    assert _pva(stop_now=97.0, stop_history=[{"from": 98.0, "to": 97.0, "source": "manual"}]) == ["Stop moved from 98.00 to 97.00."]
    own_rule = [{"from": 98.0, "to": 100.0, "source": "exit rule: breakeven"}]
    assert _pva(stop_now=100.0, stop_history=own_rule) == []                          # his own exit rule moved it: his plan
    assert _pva(exit_price=103.0, exit_reason="Exit now (manual)") == [
        "Closed by hand at 103.00, before your stop (98.00) and your exit level (106.00)."]
    assert _pva(exit_filled_quantity=6.0) == ["Exit filled 6 of 10."]
    assert _pva(plan={"entry": 100.0, "entry_source": "fill", "stop": 98.0}, entry_price=101.0) == []   # no signal price: no line
    assert _pva(direction="SHORT", plan={"entry": 100.0, "entry_source": "signal", "stop": 102.0}, stop_now=102.0,
                exit_price=99.0, exit_reason="manual exit") == ["Closed by hand at 99.00, before your stop (102.00)."]


def test_every_plan_vs_actual_line_passes_the_advice_guard():
    lines = (_pva(entry_price=99.0, stop_now=96.0, stop_history=[{"to": 96.0, "source": "user"}] * 2, exit_price=101.0,
                  exit_reason="closed by hand", exit_filled_quantity=4.0)
             + plan_actual.for_spread({"planned_max_loss": 10.0, "actual_loss": 20.0}))
    assert len(lines) == 5
    for line in lines:
        assert advice_in(line) == [] and not any(w in line.lower() for w in ("buy", "sell", "should", "target"))


# --- the migration and the export -------------------------------------------------------------------------------------

def test_migration_is_additive_rollback_safe_and_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1
    text = Path(script.get_revision("e3a5c7e9b1d4").path).read_text()
    assert "ROLLBACK_SAFE = True" in text and "'journal_broker_fills'" in text and "'journal_snapshots'" in text
    assert "drop_" not in text.split("def downgrade")[0]


def test_account_export_and_erase_cover_the_new_tables():
    from app.me import account
    names = {name for name, _ in account._DATASETS}
    assert {"journal_outside_trades", "journal_broker_fills", "journal_snapshots"} <= names
    import inspect

    from app.retention import service
    src = inspect.getsource(service._erase_journal)
    assert "JournalSnapshotRecord" in src and "JournalBrokerFillRecord" in src
