"""Trading Terminal T1 (docs/owner-orders/ATP_SESSION_TRADING_TERMINAL.md, review TT-15, TT-16, TT-19, TT-25):

- the flag `terminal`: registered, OFF by default, in the catalog as "unfinished" on top of `ui_v2`, and kept OUT of the
  trader pack until the owner approves the screenshots;
- the prefs namespace `terminal`: the layout rules (inside the 12 columns, no overlap, unique panels and names, at most 10
  layouts, the layout in use known) and the size proof - 10 layouts at their largest fit inside the 16 KB cap;
- `GET /api/terminal/status`: 404 while either flag is off, login required, and the caller's OWN rows only (a teammate's
  positions never count), with the margin as a labelled estimate and the mode shown, never changed;
- `?mine=true` on the existing positions / orders reads (the Terminal's panels): the caller's own rows only, and the
  default answer unchanged.
"""
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from app.db.models import OrderRecord, TradeRecord
from app.me import prefs
from app.platform import controls, trader_pack
from app.platform.flag_catalog import FLAG_INFO
from app.terminal import status as S
from tests.test_amw2_watchlists import _set_flag
from tests.test_auth_api import _session_factory, client
from tests.test_phase_l_ai import _owner


def _run(coro):
    return asyncio.run(coro)


def _who(tag: str, flags=("ui_v2", "terminal")) -> dict:
    headers, me = _owner(f"tt1-{tag}-{uuid.uuid4().hex[:6]}@example.com")
    for flag in flags:
        _set_flag(flag, me["tenant_id"], on=True)
    return {"headers": headers, "tenant_id": me["tenant_id"], "user_id": me["id"]}


def _member(owner: dict) -> dict:
    email = f"tt1-member-{uuid.uuid4().hex[:6]}@example.com"
    invite = client.post("/api/team/invites", headers=owner["headers"], json={"email": email, "role": "USER"})
    assert invite.status_code in (200, 201), invite.text
    token = parse_qs(urlparse(invite.json()["invite_url"]).query)["invite"][0]
    access = client.post(f"/api/auth/invite/{token}/accept", json={"password": "Member-Pass-2026"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {access}"}
    me = client.get("/api/auth/me", headers=headers).json()
    return {"headers": headers, "tenant_id": me["tenant_id"], "user_id": me["id"]}


def _trade(who: dict, **over) -> int:
    now = datetime.now(timezone.utc)
    row = dict(tenant_id=who["tenant_id"], user_id=who["user_id"], mode="PAPER", symbol="RELIANCE", strategy_id="manual",
               direction="LONG", entry_time=now - timedelta(minutes=30), entry_price=100.0, quantity=10.0, stop_loss=90.0)
    row.update(over)

    async def go():
        async with _session_factory() as session:
            trade = TradeRecord(**row)
            session.add(trade)
            await session.commit()
            return trade.id
    return _run(go())


def _order(who: dict, **over) -> int:
    row = dict(tenant_id=who["tenant_id"], user_id=who["user_id"], mode="PAPER", strategy_id="manual_fno", symbol="RELIANCE",
               direction="LONG", quantity=10.0, status="POSITION_OPEN")
    row.update(over)

    async def go():
        async with _session_factory() as session:
            order = OrderRecord(**row)
            session.add(order)
            await session.commit()
            return order.id
    return _run(go())


# --- the flag ------------------------------------------------------------------------------------------------------------

def test_flag_is_registered_off_by_default_unfinished_and_out_of_the_trader_pack():
    assert "terminal" in controls.FEATURE_FLAGS
    assert "terminal" in controls.DEFAULT_OFF_FLAGS
    info = FLAG_INFO["terminal"]
    assert info.status == "unfinished" and info.depends_on == ("ui_v2",) and info.area == "trading"
    assert "terminal" not in trader_pack.PACK                              # TT-25: never in the pack before the owner's approval
    assert "terminal" in trader_pack.excluded() and "owner" in trader_pack.excluded()["terminal"]


# --- the prefs namespace -------------------------------------------------------------------------------------------------

def _panel(pid: str, x: int, y: int, w: int, h: int, **over) -> dict:
    return {"id": pid, "x": x, "y": y, "w": w, "h": h, "open": True, **over}


SELLER = [_panel("chart-1", 0, 0, 6, 10), _panel("chain", 6, 0, 3, 10), _panel("order", 9, 0, 3, 10),
          _panel("positions", 0, 10, 5, 7), _panel("basket", 5, 10, 3, 7), _panel("orders", 8, 10, 4, 7),
          _panel("bots", 0, 17, 6, 6, open=False), _panel("payoff", 6, 17, 6, 6, open=False)]


def _put(who: dict, body, **params):
    return client.put("/api/me/prefs/terminal", headers={**who["headers"], "Content-Type": "application/json"},
                      content=json.dumps(body), params=params)


def test_terminal_prefs_default_to_the_option_seller_preset_and_round_trip():
    who = _who("prefs")
    got = client.get("/api/me/prefs", headers=who["headers"]).json()
    assert got["prefs"]["terminal"] == {"layouts": {}, "active": None} and got["versions"]["terminal"] == 0
    body = {"layouts": {"mine01": {"name": "My seller desk", "preset": "option_seller", "underlying": "BANKNIFTY", "panels": SELLER}},
            "active": "mine01"}
    saved = _put(who, body, version=0)
    assert saved.status_code == 200, saved.text
    value = saved.json()["value"]
    assert value["active"] == "mine01" and value["layouts"]["mine01"]["underlying"] == "BANKNIFTY"
    assert [p["id"] for p in value["layouts"]["mine01"]["panels"]] == [p["id"] for p in SELLER]
    assert _put(who, body, version=0).status_code == 409                 # a stale save from another device


@pytest.mark.parametrize("bad, why", [
    ([_panel("chart-1", 8, 0, 6, 10)], "outside the 12 columns"),
    ([_panel("chart-1", 0, 0, 6, 10), _panel("chain", 5, 0, 3, 10)], "overlap"),
    ([_panel("chart-1", 0, 0, 6, 10), _panel("chart-1", 6, 0, 6, 10)], "a panel twice"),
    ([_panel("chart-1", 0, 0, 6, 10, open=False)], "nothing open"),
    ([_panel("chain", 0, 0, 6, 10, symbol="NIFTY 50")], "a symbol on a non-chart panel"),
    ([_panel("ladder", 0, 0, 6, 10)], "an unknown panel"),
    ([_panel("chart-1", 0, 0, 0, 10)], "no width"),
])
def test_terminal_layout_rules_refuse(bad, why):
    who = _who("rules")
    got = _put(who, {"layouts": {"bad001": {"name": "Bad", "panels": bad}}})
    assert got.status_code == 422, why


def test_closed_panels_may_overlap_and_names_and_counts_are_checked():
    who = _who("names")
    closed_overlap = [_panel("chart-1", 0, 0, 6, 10), _panel("chain", 0, 0, 6, 10, open=False)]
    assert _put(who, {"layouts": {"ok0001": {"name": "Fine", "panels": closed_overlap}}}).status_code == 200
    twins = {"aaaa01": {"name": "Desk", "panels": SELLER}, "aaaa02": {"name": "desk ", "panels": SELLER}}
    assert _put(who, {"layouts": twins}).status_code == 422                # the same name twice
    assert _put(who, {"layouts": {"aaaa01": {"name": "Desk", "panels": SELLER}}, "active": "nope01"}).status_code == 422
    assert _put(who, {"layouts": {}, "active": "quick_trade"}).status_code == 200
    eleven = {f"lay{i:03d}": {"name": f"Layout {i}", "panels": SELLER} for i in range(prefs.MAX_TERMINAL_LAYOUTS + 1)}
    assert _put(who, {"layouts": eleven}).status_code == 422               # the 11th layout is refused


def test_ten_layouts_at_their_largest_fit_inside_the_size_cap():
    # Every panel, every chart with a 30-character symbol, 40-character names, every optional field set.
    panels = []
    for i, pid in enumerate(prefs.TERMINAL_PANEL_IDS):
        extra = {"symbol": "X" * 30} if pid.startswith("chart-") else {}
        panels.append(_panel(pid, 0, i * 5, 12, 5, open=True, **extra))
    layouts = {f"layoutid{i:08d}": {"name": f"L{i:02d}" + "N" * 37, "preset": "multi_chart", "underlying": "U" * 30, "panels": panels,
                                   "saved_at": "2026-10-24T10:00:00+00:00"} for i in range(prefs.MAX_TERMINAL_LAYOUTS)}
    body = {"layouts": layouts, "active": "layoutid00000009"}
    value = prefs.TerminalPrefs.model_validate_json(json.dumps(body)).model_dump(mode="json")
    size = len(json.dumps(value).encode())
    assert size < prefs.MAX_BODY_BYTES, size
    who = _who("size")
    saved = _put(who, body)
    assert saved.status_code == 200, saved.text


def test_layouts_are_per_user_never_a_teammates():
    owner = _who("iso")
    member = _member(owner)
    assert _put(owner, {"layouts": {"own001": {"name": "Owner desk", "panels": SELLER}}, "active": "own001"}).status_code == 200
    seen = client.get("/api/me/prefs", headers=member["headers"]).json()
    assert seen["prefs"]["terminal"] == {"layouts": {}, "active": None} and seen["versions"]["terminal"] == 0


def test_the_tour_id_is_allowed():
    assert "terminal" in prefs.TOUR_IDS


# --- GET /api/terminal/status ---------------------------------------------------------------------------------------------

def test_status_needs_both_flags_and_a_login():
    assert client.get("/api/terminal/status").status_code == 401
    plain = _who("off", flags=())
    got = client.get("/api/terminal/status", headers=plain["headers"])
    assert got.status_code == 404 and got.headers.get("X-Feature-Disabled") == "ui_v2"
    v2_only = _who("v2only", flags=("ui_v2",))
    got = client.get("/api/terminal/status", headers=v2_only["headers"])
    assert got.status_code == 404 and got.headers.get("X-Feature-Disabled") == "terminal"


def test_status_reads_only_the_callers_own_trades():
    owner = _who("own")
    member = _member(owner)
    now = datetime.now(timezone.utc)
    # The owner: one open position marked +50, one closed today +200 (after charges).
    _trade(owner, mark_price=105.0, mark_time=now - timedelta(minutes=1))
    _trade(owner, exit_time=now - timedelta(minutes=5), exit_price=120.0, pnl=200.0)
    # The teammate: a big open loss and a big booked win - never in the owner's figures.
    _trade(member, entry_price=1000.0, mark_price=500.0, quantity=100.0, mark_time=now)
    _trade(member, exit_time=now - timedelta(minutes=2), exit_price=2000.0, pnl=99_999.0)
    got = client.get("/api/terminal/status", headers=owner["headers"])
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["open"] == {"count": 1, "priced": 1, "mtm": 50.0, "as_of": body["open"]["as_of"]}
    assert body["today"]["booked"] == 200.0 and body["today"]["mtm"] == 250.0
    # The cash position's full value is the margin estimate; the balance counts only his own closed trades.
    assert body["margin"]["used"] == 1000.0 and body["margin"]["estimated"] is True and body["margin"]["not_estimated"] == 0
    assert body["funds"]["balance"] - body["funds"]["available"] == 1000.0
    assert body["mode"]["mode"] == "PAPER"
    assert body["kill_switch"]["engaged"] is False
    assert body["feed"]["state"] in ("off", "none")
    mine = client.get("/api/terminal/status", headers=member["headers"]).json()
    assert mine["open"]["mtm"] == -50_000.0 and mine["today"]["booked"] == 99_999.0


def test_status_shows_the_kill_switch_and_never_changes_the_mode():
    who = _who("kill")
    flags_before = client.get("/api/system/features", headers=who["headers"]).json()
    assert client.post("/api/kill-switch/tenant/engage", headers=who["headers"], json={"reason": "test"}).status_code == 200
    body = client.get("/api/terminal/status", headers=who["headers"]).json()
    assert body["kill_switch"]["engaged"] is True and body["kill_switch"]["scope"] == "account"
    assert "Exits still work" in body["kill_switch"]["message"]
    assert client.get("/api/system/features", headers=who["headers"]).json() == flags_before
    client.post("/api/kill-switch/tenant/disengage", headers=who["headers"])


def test_margin_estimate_hedged_spread_long_option_and_naked_short():
    def t(i, symbol, direction, price, kind="OPTION", group=None):
        return TradeRecord(id=i, tenant_id=1, user_id=1, mode="PAPER", symbol=symbol, strategy_id="manual_fno", direction=direction,
                           entry_time=datetime.now(timezone.utc), entry_price=price, quantity=75.0, stop_loss=0.0,
                           instrument_kind=kind, leg_group_id=group)
    short = t(1, "NIFTY 25000 CE 26 NOV 26", "SHORT", 120.0, group="g1")
    hedge = t(2, "NIFTY 25100 CE 26 NOV 26", "LONG", 80.0, group="g1")
    bought = t(3, "NIFTY 24000 PE 26 NOV 26", "LONG", 50.0)
    naked = t(4, "NIFTY 23000 PE 26 NOV 26", "SHORT", 30.0)
    out = S.margin_estimate([short, hedge, bought, naked], [{"trade_ids": [1, 2]}])
    # The spread at its max loss ((100 - 40 credit) x 75 = 4,500), the bought put its premium (50 x 75 = 3,750); the naked
    # short needs the broker and is counted, never made up.
    assert out["used"] == 4500.0 + 3750.0
    assert out["not_estimated"] == 1 and out["estimated"] is True and "not your broker's number" in out["note"]
    assert S.single_margin(t(5, "NIFTY FUT 26 NOV 26", "LONG", 25000.0, kind="FUTURE")) is None


# --- ?mine=true on the existing reads ------------------------------------------------------------------------------------

def test_mine_filters_positions_and_orders_to_the_caller_and_the_default_is_unchanged():
    owner = _who("mine")
    member = _member(owner)
    own_trade = _trade(owner, symbol="OWNSYM")
    other_trade = _trade(member, symbol="TEAMSYM")
    own_order = _order(owner, symbol="OWNSYM")
    other_order = _order(member, symbol="TEAMSYM")
    every = {p["id"] for p in client.get("/api/positions", headers=owner["headers"]).json()}
    assert {own_trade, other_trade} <= every                               # the organisation's, as before
    mine = {p["id"] for p in client.get("/api/positions", headers=owner["headers"], params={"mine": "true"}).json()}
    assert own_trade in mine and other_trade not in mine
    every = {o["id"] for o in client.get("/api/orders", headers=owner["headers"]).json()}
    assert {own_order, other_order} <= every
    mine = {o["id"] for o in client.get("/api/orders", headers=owner["headers"], params={"mine": "true"}).json()}
    assert own_order in mine and other_order not in mine


def test_the_frontend_layout_model_keeps_the_same_limits():
    import re
    from pathlib import Path
    source = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "terminal" / "layoutModel.ts").read_text(encoding="utf-8")
    ids = re.search(r"PANEL_IDS: readonly PanelId\[\] = \[(.*?)\];", source, re.S)
    assert ids and tuple(re.findall(r'"([a-z0-9-]+)"', ids.group(1))) == prefs.TERMINAL_PANEL_IDS
    assert f"MAX_LAYOUTS = {prefs.MAX_TERMINAL_LAYOUTS};" in source
    assert f"COLUMNS = {prefs.TERMINAL_COLUMNS};" in source and f"ROWS = {prefs.TERMINAL_ROWS};" in source
    assert f"MAX_HEIGHT = {prefs.TERMINAL_MAX_HEIGHT};" in source
    rule = re.search(r"NAME_RULE = /(.*?)/;", source)
    assert rule and rule.group(1) == prefs.TERMINAL_LAYOUT_NAME
