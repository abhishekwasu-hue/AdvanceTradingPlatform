"""TERM-CON C0 (docs/plans/terminal-conclusion-PLAN.md): the Terminal's Conclusion read.

The flag, the route's gates, the caller-only scope, PAPER = LIVE, the signal language on every signal line, the sections
built from the existing reads, the facts that need him - and that the package reaches no order path and no AI call.
"""
from __future__ import annotations

import asyncio
import pathlib
import uuid
from datetime import UTC, datetime, timedelta

from app.conclusion import FLAG, FLAGS
from app.conclusion import sections as S
from app.db.models import OrderRecord, Tenant, TradeRecord
from app.platform import controls
from app.platform.flag_catalog import FLAG_INFO
from app.signals import language as L
from tests.test_amw2_watchlists import _set_flag
from tests.test_auth_api import _session_factory, client
from tests.test_phase_l_ai import _owner

URL = "/api/terminal/conclusion"


def _run(coro):
    return asyncio.run(coro)


def _who(tag: str, flags=FLAGS) -> dict:
    headers, me = _owner(f"tc0-{tag}-{uuid.uuid4().hex[:6]}@example.com")
    for flag in flags:
        _set_flag(flag, me["tenant_id"], on=True)
    return {"headers": headers, "tenant_id": me["tenant_id"], "user_id": me["id"]}


def _add(model, **row) -> int:
    async def go():
        async with _session_factory() as session:
            item = model(**row)
            session.add(item)
            await session.commit()
            return item.id
    return _run(go())


def _trade(who: dict, **over) -> int:
    now = datetime.now(UTC)
    row = dict(tenant_id=who["tenant_id"], user_id=who["user_id"], mode="PAPER", symbol="RELIANCE", strategy_id="manual",
               direction="LONG", entry_time=now - timedelta(minutes=30), entry_price=100.0, quantity=10.0, stop_loss=90.0)
    row.update(over)
    return _add(TradeRecord, **row)


def _order(who: dict, **over) -> int:
    row = dict(tenant_id=who["tenant_id"], user_id=who["user_id"], mode="PAPER", strategy_id="manual_fno", symbol="RELIANCE",
               direction="LONG", quantity=10.0, status="POSITION_OPEN")
    row.update(over)
    return _add(OrderRecord, **row)


def _section(body: dict, key: str) -> dict:
    return next(s for s in body["sections"] if s["key"] == key)


# --- the flag and the gates ------------------------------------------------------------------------------------------

def test_flag_is_registered_off_unfinished_and_needs_the_terminal():
    assert FLAG == "terminal_conclusion" and FLAG in controls.FEATURE_FLAGS
    assert FLAG not in controls.DEFAULT_ON_FLAGS
    info = FLAG_INFO[FLAG]
    assert info.status == "unfinished" and info.depends_on == ("ui_v2", "terminal") and info.area == "trading"


def test_route_needs_a_login_and_every_flag():
    assert client.get(URL, params={"symbol": "NIFTY"}).status_code == 401
    for missing in FLAGS:
        if missing == "terminal":
            continue                                   # ON by default for every tenant (T-D1)
        who = _who(f"no-{missing}", flags=tuple(f for f in FLAGS if f != missing))
        got = client.get(URL, params={"symbol": "NIFTY"}, headers=who["headers"])
        assert got.status_code == 404 and got.headers.get("X-Feature-Disabled") == missing


# --- the read: his own rows only, PAPER = LIVE -----------------------------------------------------------------------

def test_the_read_shows_only_his_own_positions_and_orders_for_the_symbol():
    owner = _who("own")
    other = _who("other")
    now = datetime.now(UTC)
    _trade(owner, mark_price=105.0, mark_time=now - timedelta(minutes=1))
    _trade(owner, symbol="TCS", mark_price=50.0)                                          # another symbol: not shown
    _trade(other, entry_price=1000.0, mark_price=500.0, quantity=100.0)                   # another user's: never shown
    _order(owner, status="FAILED", reasons_json='["The product is invalid"]')
    _order(owner)
    _order(other, status="REJECTED", reasons_json='["not his"]')
    got = client.get(URL, params={"symbol": "reliance", "expiry": "2026-11-26", "tf": "15m"}, headers=owner["headers"])
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["symbol"] == "RELIANCE" and body["tf"] == "15m" and body["fingerprint"]
    keys = [s["key"] for s in body["sections"]]
    assert "chart" not in keys                         # its own flag (read_chart) is off: the section is hidden, not an error
    assert keys == ["oi", "positions", "signals", "orders", "broker", "risk", "events"]
    pos = _section(body, "positions")
    assert len([x for x in pos["lines"] if x["text"].startswith("PAPER")]) == 1
    assert "P&L +50 ·" in pos["lines"][0]["text"] and pos["lines"][0]["tone"] == "up"
    assert "Loss if all your stops are hit: 100." in [x["text"] for x in pos["lines"]]
    orders = _section(body, "orders")
    texts = [x["text"] for x in orders["lines"]]
    assert "Today: 1 filled." in texts
    assert any("failed: The product is invalid" in t for t in texts) and not any("not his" in t for t in texts)
    assert body["needs_you"] == [{"key": "orders", "text": "1 order not placed today", "action": "orders"}]
    for sec in body["sections"]:
        assert sec["status"] in ("ok", "stale", "na")
        for x in sec["lines"]:
            assert x["source"] and "as_of" in x


def test_a_source_whose_flag_is_on_but_has_no_data_says_not_available():
    who = _who("chart", flags=FLAGS + ("read_chart",))
    body = client.get(URL, params={"symbol": "RELIANCE"}, headers=who["headers"]).json()
    chart = _section(body, "chart")
    assert chart["status"] == "na" and chart["lines"] == [] and chart["note"]


def test_paper_and_live_give_the_same_lines_except_the_mode():
    rows = [{"symbol": "NIFTY25NOV25800CE", "underlying": "NIFTY", "mode": mode, "side": "bearish", "kind": "OPTION",
             "marked_pnl": -120.0, "loss_if_stop_hit": 300.0, "stop_now": 140.0, "mark_time": "t"} for mode in ("PAPER", "LIVE")]
    paper = S.positions_section(rows[:1], "NIFTY", "t")["lines"]
    live = S.positions_section(rows[1:], "NIFTY", "t")["lines"]
    assert [x["text"].replace("PAPER", "LIVE") for x in paper] == [x["text"] for x in live]
    assert S.positions_reading(rows, "NIFTY", "t")["direction"] == "down"


def test_the_broker_check_pending_needs_him_and_says_exits_keep_running():
    async def flag_it(tid: int):
        async with _session_factory() as session:
            tenant = await session.get(Tenant, tid)
            tenant.broker_uncertain_since = datetime.now(UTC) - timedelta(minutes=5)
            tenant.broker_uncertain_reason = "order 4 FAILED"
            await session.commit()
    who = _who("broker")
    _run(flag_it(who["tenant_id"]))
    body = client.get(URL, params={"symbol": "NIFTY"}, headers=who["headers"]).json()
    broker = _section(body, "broker")
    assert broker["lines"][0]["tone"] == "warn" and "Exits keep running" in broker["lines"][0]["text"]
    assert {"key": "broker", "text": "Broker check pending", "action": "broker_check"} in body["needs_you"]


# --- pure builders ---------------------------------------------------------------------------------------------------

READ = {"bars": 120, "as_of": "2026-11-20T10:30:00+05:30",
        "structure": {"direction": "up (weak)", "last_event": {"type": "BOS", "direction": "up"}},
        "pullback": {"available": True, "trend": "UP", "words": "Daily trend: up; a pullback inside the running leg.", "as_of": "d"},
        "areas": [{"role": "demand", "low": 24180.0, "high": 24220.0, "state_words": "price has come back to it"},
                  {"role": "supply", "low": 24600.0, "high": 24650.5, "state_words": ""}],
        "commitment": {"role": "demand", "area": {"low": 24180.0, "high": 24220.0}, "bar_end": "c"}}


def test_chart_section_is_in_the_signal_language_with_areas_as_ranges():
    sec = S.chart_section(READ, "NIFTY", "15m")
    texts = [x["text"] for x in sec["lines"]]
    assert texts[0] == "15-minute structure: up, weakening; the last event was a break of structure up."
    assert "Buyer area: 24,180–24,220 (price has come back to it)." in texts
    assert "Seller area: 24,600–24,650.5." in texts
    assert "Commitment candle at the Buyer area: 24,180–24,220." in texts
    for x in sec["lines"]:
        assert x["kind"] == "signal" and L.check(x["text"], "signal_own", own_symbols=["NIFTY"]) == []
    assert S.chart_reading(READ, "15m")["direction"] == "up"
    assert S.chart_section({"bars": 0}, "NIFTY", "15m")["status"] == "na" and S.chart_reading({"bars": 0}, "15m") is None


def test_a_signal_line_that_fails_the_language_is_left_out_never_rewritten():
    lines = [S.line("Price near 24,200 - buy here.", kind="signal", source="x", as_of=None),
             S.line("15-minute structure: up.", kind="signal", source="x", as_of=None),
             S.line("PAPER · NIFTY · P&L +1,240.", kind="record", source="x", as_of=None)]
    assert [x["text"] for x in S.signal_lines(lines, "NIFTY")] == ["15-minute structure: up.", "PAPER · NIFTY · P&L +1,240."]


def test_oi_section_reads_the_banner_in_fact_words_and_says_stale_and_another_expiry():
    resp = {"banner": {"slot": "s", "direction": "BULLISH", "fact_message": "Put OI added", "pcr": 1.124, "pcr_band": "BALANCED",
                       "max_pain": 24200.0, "dte": 4, "expiry": "2026-11-26"}, "stale": True, "age_minutes": 12.0}
    sec = S.oi_section(resp, "2026-12-03")
    texts = [x["text"] for x in sec["lines"]]
    assert texts[0] == "OI: Put premium down / call premium up." and sec["lines"][0]["tone"] == "up"
    assert "PCR 1.12 (" in texts[2] and "Max pain strike 24,200." in texts and "Sessions to expiry: 4." in texts
    assert sec["status"] == "stale" and "2026-11-26 expiry" in sec["note"] and "12.0 minutes" in sec["note"]
    for x in sec["lines"]:
        assert L.call_words(x["text"]) == []
    assert S.oi_reading(resp) == {"source": "oi", "label": "OI", "tf": None, "direction": "up", "as_of": "s", "stale": True}
    assert S.oi_section({"banner": None, "message": "m"}, None)["status"] == "na"
    assert {S.OI_DIRECTION[d] for d in ("MIXED", "NEUTRAL", "SIDEWAYS")} == {"mixed", "none", "range"}


def test_symbol_matching_takes_contracts_but_never_a_longer_name():
    assert S.matches("NIFTY", "NIFTY") and S.matches("NIFTY", "NIFTY25NOV25800CE") and S.matches("nifty", "nifty 25nov".replace(" ", ""))
    assert not S.matches("NIFTY", "NIFTYNXT50") and not S.matches("NIFTY", "BANKNIFTY25NOV52000PE") and not S.matches("", "NIFTY")


def test_a_position_without_a_stop_says_so_and_adds_no_default():
    row = {"symbol": "RELIANCE", "underlying": "RELIANCE", "mode": "PAPER", "side": "bullish", "kind": "UNDERLYING",
           "marked_pnl": None, "loss_if_stop_hit": None, "stop_now": None}
    text = S.positions_section([row], "RELIANCE", "t")["lines"][0]["text"]
    assert text == "PAPER · RELIANCE · gains if the price rises · not priced yet · no stop in your settings."


# --- safety: no order path, no AI ------------------------------------------------------------------------------------

def test_the_package_reaches_no_order_path_and_no_ai_call():
    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "conclusion"
    source = "\n".join(p.read_text() for p in root.glob("*.py"))
    for word in ("place_order", "modify_order", "cancel_order", "exit_position", "app.execution", "app.brokers",
                 "provider_for_user", "narrate", "session.add(", "session.commit("):
        assert word not in source, word
    from app.main import app
    from tests.route_inventory import iter_api_routes
    assert {r.method for r in iter_api_routes(app) if r.path.startswith(URL)} == {"GET"}
