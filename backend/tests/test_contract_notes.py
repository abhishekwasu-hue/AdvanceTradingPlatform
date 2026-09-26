"""Phase D4: contract-note ingestion - broker-agnostic CSV parsing, matching by order id then by
fill, actual charges and P&L applied to trades, dry run, duplicate refusal, tenant isolation."""
import asyncio
import io
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.contract_notes.parser import ContractNoteParseError, parse_contract_note_csv, parse_date
from app.contract_notes.service import MATCH_FILL, MATCH_ORDER_ID, match_legs
from app.db.models import AuditLogRecord, ContractNoteLineRecord, TradeRecord, User
from tests.test_auth_api import _register, _session_factory, client
from tests.test_position_monitor import _trade, _user

ENTRY_TS = datetime(2026, 9, 25, 4, 0, tzinfo=timezone.utc)   # 09:30 IST on 25 Sep
EXIT_TS = datetime(2026, 9, 25, 6, 0, tzinfo=timezone.utc)


def _run(coro):
    return asyncio.run(coro)


def _headers(user: User) -> dict:
    token = client.post("/api/auth/login", json={"email": user.email, "password": "S3cur3Pass!"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _closed_trade(user: User, **overrides) -> int:
    fields = dict(mode="LIVE", direction="LONG", symbol="RELIANCE", entry_time=ENTRY_TS, exit_time=EXIT_TS, entry_price=100.0,
                  exit_price=104.0, exit_reason="Target 1", quantity=100, charges=12.5, pnl=387.5,
                  broker_order_id="ORD-ENTRY-1", sl_order_id="ORD-SL-1", exit_order_id="ORD-EXIT-1")
    fields.update(overrides)
    return _trade(user, **fields)


def _load(trade_id: int) -> TradeRecord:
    async def go():
        async with _session_factory() as session:
            return await session.get(TradeRecord, trade_id)
    return _run(go())


ZERODHA_STYLE = """symbol,trade_date,exchange,segment,series,trade_type,quantity,price,order_id,brokerage,stt,exchange_charges,gst,sebi_charges,stamp_duty
RELIANCE,2026-09-25,NSE,EQ,EQ,buy,100,100.00,ORD-ENTRY-1,20,0,3.45,4.22,0.10,0.30
RELIANCE,2026-09-25,NSE,EQ,EQ,sell,100,104.00,ORD-EXIT-1,20,2.60,3.59,4.25,0.10,0
"""

TOTAL_ONLY = """Symbol;Date;Buy/Sell;Qty;Rate;Order No;Total Charges
TCS;25/09/2026;B;50;3500.00;;45.20
TCS;25/09/2026;S;50;3520.00;;46.10
"""


def test_parser_handles_component_charges_and_aliases():
    parsed = parse_contract_note_csv(ZERODHA_STYLE.encode())
    assert len(parsed.legs) == 2 and parsed.warnings == []
    buy, sell = parsed.legs
    assert buy.side == "BUY" and buy.order_id == "ORD-ENTRY-1" and buy.trade_date == date(2026, 9, 25)
    assert buy.charges == 28.07 and buy.breakdown["brokerage"] == 20
    assert sell.charges == 30.54
    assert parsed.total_charges == 58.61 and parsed.note_date == date(2026, 9, 25)


def test_parser_handles_semicolons_total_column_and_dd_mm_yyyy():
    parsed = parse_contract_note_csv(TOTAL_ONLY.encode("utf-8-sig"))
    assert [l.side for l in parsed.legs] == ["BUY", "SELL"]
    assert parsed.legs[0].trade_date == date(2026, 9, 25) and parsed.legs[0].order_id is None
    assert parsed.total_charges == 91.3


def test_parser_rejects_unrecognisable_header_and_empty_rows():
    try:
        parse_contract_note_csv(b"foo,bar\n1,2\n")
    except ContractNoteParseError as exc:
        assert "symbol" in str(exc) and "foo" in str(exc)
    else:
        raise AssertionError("expected a parse error")
    try:
        parse_contract_note_csv(b"symbol,side,qty,price\n,,,\n")
    except ContractNoteParseError as exc:
        assert "No readable" in str(exc)
    parsed = parse_contract_note_csv(b"symbol,side,qty,price\nINFY,BUY,10,1500\n")
    assert parsed.legs[0].charges == 0 and any("No charge columns" in w for w in parsed.warnings)


def test_parse_date_formats():
    assert parse_date("2026-09-25 10:15:00") == date(2026, 9, 25)
    assert parse_date("25-Sep-2026") == date(2026, 9, 25)
    assert parse_date("2026-09-25T04:00:00Z") == date(2026, 9, 25)
    assert parse_date("garbage") is None and parse_date("") is None


def test_match_prefers_order_ids_then_falls_back_to_fills():
    user = _user("cn-match@example.com")
    with_ids = _load(_closed_trade(user))
    paper = _load(_closed_trade(user, mode="PAPER", symbol="TCS", entry_price=3500.0, exit_price=3520.0, quantity=50,
                                broker_order_id=None, sl_order_id=None, exit_order_id=None))
    legs = parse_contract_note_csv(ZERODHA_STYLE.encode()).legs + parse_contract_note_csv(TOTAL_ONLY.encode()).legs
    matches = match_legs(legs, [with_ids, paper])
    assert {m for _, m in matches[with_ids.id]} == {MATCH_ORDER_ID} and len(matches[with_ids.id]) == 2
    assert {m for _, m in matches[paper.id]} == {MATCH_FILL} and len(matches[paper.id]) == 2


def test_upload_applies_actual_charges_and_pnl_and_is_auditable():
    user = _user("cn-upload@example.com")
    trade_id = _closed_trade(user)
    headers = _headers(user)

    preview = client.post("/api/contract-notes", headers=headers, data={"apply": "false", "broker_name": "zerodha"},
                          files={"file": ("tradebook.csv", ZERODHA_STYLE.encode(), "text/csv")})
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["applied"] is False and body["note_id"] is None and body["matched"] == 2
    assert body["trades_updated"][0]["new_charges"] == 58.61 and body["trades_updated"][0]["new_pnl"] == 341.39
    assert _load(trade_id).charges == 12.5  # dry run changed nothing

    applied = client.post("/api/contract-notes", headers=headers, data={"broker_name": "zerodha"},
                          files={"file": ("tradebook.csv", ZERODHA_STYLE.encode(), "text/csv")})
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["applied"] is True and body["note_id"] and body["unmatched"] == []
    trade = _load(trade_id)
    assert trade.charges == 58.61 and trade.pnl == 341.39 and trade.charges_source == "CONTRACT_NOTE"
    assert trade.contract_note_id == body["note_id"]

    listed = client.get("/api/contract-notes", headers=headers).json()
    assert listed[0]["id"] == body["note_id"] and listed[0]["matched_lines"] == 2 and listed[0]["note_date"] == "2026-09-25"
    detail = client.get(f"/api/contract-notes/{body['note_id']}", headers=headers).json()
    assert len(detail["lines"]) == 2 and all(l["match_method"] == "ORDER_ID" and l["trade_id"] == trade_id for l in detail["lines"])
    assert detail["lines"][0]["breakdown"]["brokerage"] == 20

    trades_api = client.get("/api/trades", headers=headers).json()
    mine = next(t for t in trades_api if t["id"] == trade_id)
    assert mine["charges_source"] == "CONTRACT_NOTE" and mine["exit_order_id"] == "ORD-EXIT-1"

    duplicate = client.post("/api/contract-notes", headers=headers, files={"file": ("again.csv", ZERODHA_STYLE.encode(), "text/csv")})
    assert duplicate.status_code == 409

    async def audit():
        async with _session_factory() as session:
            return await session.scalar(select(AuditLogRecord).where(AuditLogRecord.event == "contract_note_ingested", AuditLogRecord.tenant_id == user.tenant_id))
    row = _run(audit())
    assert row is not None and "matched=2" in row.detail


def test_unmatched_legs_are_reported_and_never_touch_trades():
    user = _user("cn-unmatched@example.com")
    trade_id = _closed_trade(user, broker_order_id="X-1", sl_order_id="X-2", exit_order_id="X-3", symbol="INFY")
    headers = _headers(user)
    resp = client.post("/api/contract-notes", headers=headers, files={"file": ("n.csv", ZERODHA_STYLE.encode(), "text/csv")})
    assert resp.status_code == 200
    body = resp.json()
    assert body["matched"] == 0 and len(body["unmatched"]) == 2 and body["trades_updated"] == []
    assert _load(trade_id).charges == 12.5 and _load(trade_id).charges_source == "ESTIMATED"


def test_bad_files_are_rejected_clearly():
    user = _user("cn-bad@example.com")
    headers = _headers(user)
    assert client.post("/api/contract-notes", headers=headers, files={"file": ("x.csv", b"foo,bar\n1,2\n", "text/csv")}).status_code == 400
    assert client.post("/api/contract-notes", headers=headers, files={"file": ("x.csv", b"   ", "text/csv")}).status_code == 400
    assert client.post("/api/contract-notes", headers=headers, files={"file": ("x.csv", b"\xff\xfe\x00bad", "text/csv")}).status_code == 400


def test_contract_notes_are_tenant_scoped_and_trader_only():
    owner = _user("cn-tenant-a@example.com")
    _closed_trade(owner)
    headers = _headers(owner)
    note_id = client.post("/api/contract-notes", headers=headers, files={"file": ("a.csv", ZERODHA_STYLE.encode(), "text/csv")}).json()["note_id"]

    other = _user("cn-tenant-b@example.com")
    other_headers = _headers(other)
    assert client.get(f"/api/contract-notes/{note_id}", headers=other_headers).status_code == 404
    assert client.get("/api/contract-notes", headers=other_headers).json() == []
    # The other tenant uploading the same file matches nothing of tenant A's.
    b = client.post("/api/contract-notes", headers=other_headers, files={"file": ("a.csv", ZERODHA_STYLE.encode(), "text/csv")}).json()
    assert b["matched"] == 0 and b["applied"] is True
