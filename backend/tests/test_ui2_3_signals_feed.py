"""UI2-3 (UI v2 proposal §7, UI-4): `GET /api/signals/feed` - the Signals inbox, read-only, behind `ui_v2` (off).

- the row shape every source shares, and each source's rows (proposals, execution intents, scan matches, Autopilot fills,
  the signal generator), with the dedup rules (a fill from an approved proposal, an intent that became a proposal);
- scoping: another organisation's rows and another member's rows never appear; organisation-wide proposals do;
- expiry: an overdue PROPOSED proposal reads "expired" and leaves "Waiting" - without the feed changing the row;
- tabs, filters, the 30-day window and the cursor; empty; flag off; login required; and no writes at all.
All data here is made up for the test."""
import asyncio
import inspect
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, true

from app.db.models import (AiActionRecord, AlertEventRecord, AlertRuleRecord, AuditLogRecord, ExecutionIntentRecord, NotificationRecord,
                           SignalHistoryRecord, StrategyDeploymentRecord, TradeRecord)
from app.signals_feed import service
from tests.test_amw2_watchlists import _set_flag
from tests.test_auth_api import _register, _session_factory, client
from tests.test_trading_worker import _upgrade_plan
from tests.test_z3_members import member

URL = "/api/signals/feed"
UTC = timezone.utc
ITEM_KEYS = {"id", "at", "symbol", "exchange", "direction", "setup", "entry", "stop", "target", "rr", "source", "mode", "status",
             "expires_at", "snoozed_until", "reasons", "action_id", "outcome"}


def _run(coro):
    return asyncio.run(coro)


def _owner(tag: str, *, v2: bool = True) -> dict:
    headers = {"Authorization": f"Bearer {_register(f'ui23-{tag}-{uuid.uuid4().hex[:6]}@example.com')}"}
    me = client.get("/api/auth/me", headers=headers).json()
    if v2:
        _set_flag("ui_v2", me["tenant_id"], on=True)
    return {"headers": headers, "tenant_id": me["tenant_id"], "user_id": me["id"]}


def _now() -> datetime:
    return datetime.now(UTC)


def _today(seconds_ago: int = 5) -> datetime:
    """A moment earlier today (IST), never before 00:00 IST even right after midnight."""
    return max(_now() - timedelta(seconds=seconds_ago), service.today_start(_now()) + timedelta(seconds=1))


def _add(*rows):
    async def go():
        async with _session_factory() as session:
            session.add_all(rows)
            await session.commit()
            return [r.id for r in rows]
    return _run(go())


def _proposal(t, *, owner=None, status="PROPOSED", symbol="DEMO1", direction="LONG", source="deployment", deployment_id=None,
              created=None, expires_in=timedelta(minutes=15), snoozed_until=None, trade_id=None, result=None, tenant_id=None) -> int:
    created = created or _today()
    signal = {"symbol": symbol, "strategy_id": "demo_strategy", "strategy_name": "Demo breakout", "direction": direction,
              "timestamp": created.isoformat(), "entry": 100.0, "stop_loss": 98.0, "target1": 104.0, "target2": None,
              "risk_reward": 2.0, "score": 70, "grade": "B", "reasons": ["Made-up reason one", "Made-up reason two", "Three", "Four"],
              "timeframe_combo": "5min"}
    evidence = {"proposal_price": 100.0, "stop_loss": 98.0, "target1": 104.0, "source": source, "symbol": symbol, "direction": direction,
                "risk_reward": 2.0, "strategy_id": "demo_strategy", "mode": "PAPER", "exchange": "NSE", "signal": signal}
    row = AiActionRecord(tenant_id=tenant_id or t["tenant_id"], deployment_id=deployment_id, trade_id=trade_id, action="ENTRY", rule="ENTRY_SIGNAL",
                         reason="Made-up entry proposal", evidence_json=json.dumps(evidence), status=status, created_at=created,
                         expires_at=created + expires_in, snoozed_until=snoozed_until, result=result,
                         idempotency_key=f"ui23:{uuid.uuid4().hex}", owner_user_id=t["user_id"] if owner is None else owner)
    return _add(row)[0]


def _deployment(t, *, mode="PAPER") -> int:
    return _add(StrategyDeploymentRecord(tenant_id=t["tenant_id"], strategy_id="demo_strategy", symbol="DEMO2", exchange="NSE",
                                         timeframe="5min", mode=mode, status="ACTIVE", created_by=t["user_id"]))[0]


def _trade(t, *, deployment_id, symbol="DEMO2", at=None, user_id=None) -> int:
    return _add(TradeRecord(tenant_id=t["tenant_id"], user_id=user_id or t["user_id"], mode="PAPER", symbol=symbol, strategy_id="demo_strategy",
                            direction="SHORT", entry_time=at or _today(), entry_price=200.0, quantity=5.0, stop_loss=204.0, target1=192.0,
                            exchange="NSE", deployment_id=deployment_id))[0]


def _history(t, *, symbol="DEMO3", at=None, direction="LONG", user_id=None) -> int:
    return _add(SignalHistoryRecord(tenant_id=t["tenant_id"], user_id=user_id or t["user_id"], strategy_id="demo_strategy", symbol=symbol,
                                    direction=direction, signal_time=at or _today(), entry=50.0, stop_loss=49.0, target1=52.0,
                                    risk_reward=2.0, score=60, grade="B", reasons_json=json.dumps(["Made-up history reason"]),
                                    created_at=at or _today()))[0]


def _rule_event(t, *, created_by, symbol="DEMO4", at=None) -> int:
    rule_id = _add(AlertRuleRecord(tenant_id=t["tenant_id"], created_by=created_by, name="Made-up gap scan", kind="instrument",
                                   symbol=symbol, condition_text="close > 1", base_tf="5m"))[0]
    return _add(AlertEventRecord(tenant_id=t["tenant_id"], rule_id=rule_id, symbol=symbol, condition_hash="0" * 64, bar_time=_today(),
                                 idem_key=uuid.uuid4().hex, priority="normal", values_json=json.dumps({"close": {"value": 101.5}, "gap_pct": 1.234}),
                                 status="sent", created_at=at or _today()))[0]


def _intent(t, *, ai_action_id=None, status="DONE", symbol="DEMO5") -> int:
    signal = {"symbol": symbol, "strategy_id": "demo_strategy", "strategy_name": "Demo scan setup", "direction": "LONG", "entry": 10.0,
              "stop_loss": 9.5, "target1": 11.0, "risk_reward": 2.0, "reasons": ["Made-up zone held"]}
    now = _today()
    return _add(ExecutionIntentRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], source="scan", source_ref="rule:1:event:1",
                                      idempotency_key=uuid.uuid4().hex, requested_mode="NOTIFY", route="NOTIFY", status=status, symbol=symbol,
                                      exchange="NSE", direction="LONG", strategy_id="demo_strategy", signal_json=json.dumps(signal),
                                      reason="Made-up routing note", ai_action_id=ai_action_id, expires_at=now + timedelta(minutes=30),
                                      created_at=now))[0]


def _get(t, **params) -> dict:
    response = client.get(URL, headers=t["headers"], params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _ids(body) -> list:
    return [i["id"] for i in body["items"]]


def _counts(waiting: int, today: int, *, capped: bool = False) -> dict:
    return {"waiting": waiting, "today": today, "capped": {"waiting": capped, "today": capped}}


def _pages(t, **params) -> list:
    """Every page of a tab, in order: [(ids, next_cursor), ...]."""
    out, cursor = [], None
    for _ in range(20):
        body = _get(t, **params, **({"cursor": cursor} if cursor else {}))
        cursor = body["next_cursor"]
        out.append((_ids(body), cursor))
        if not cursor:
            return out
    raise AssertionError("the cursor never ended")


# --- access ---------------------------------------------------------------------------------------------------------

def test_login_is_required():
    assert client.get(URL).status_code == 401


def test_flag_off_answers_404_with_the_flag_named():
    t = _owner("off", v2=False)
    response = client.get(URL, headers=t["headers"])
    assert response.status_code == 404
    assert response.headers.get("X-Feature-Disabled") == "ui_v2"


def test_empty_inbox():
    t = _owner("empty")
    for tab in ("waiting", "today", "history"):
        body = _get(t, tab=tab)
        assert body["items"] == [] and body["next_cursor"] is None
        assert body["counts"] == _counts(0, 0)
        assert body["tab"] == tab


def test_bad_parameters_are_refused():
    t = _owner("bad")
    assert client.get(URL, headers=t["headers"], params={"tab": "all"}).status_code == 422
    assert client.get(URL, headers=t["headers"], params={"source": "telegram"}).status_code == 422
    assert client.get(URL, headers=t["headers"], params={"symbol": "<script>"}).status_code == 422
    response = client.get(URL, headers=t["headers"], params={"cursor": "not-a-cursor"})
    assert response.status_code == 422 and "reload" in response.json()["detail"]


# --- shape and sources --------------------------------------------------------------------------------------------------

def test_a_waiting_proposal_has_the_normalised_shape_and_links_to_the_approval_endpoint():
    t = _owner("shape")
    dep = _deployment(t)
    action_id = _proposal(t, deployment_id=dep, symbol="DEMO1")
    body = _get(t, tab="waiting")
    assert body["counts"] == _counts(1, 1)
    [item] = body["items"]
    assert set(item) == ITEM_KEYS
    assert item["id"] == f"action:{action_id}" and item["action_id"] == action_id
    assert item["symbol"] == "DEMO1" and item["direction"] == "LONG" and item["setup"] == "Demo breakout"
    assert (item["entry"], item["stop"], item["target"], item["rr"]) == (100.0, 98.0, 104.0, 2.0)
    assert item["source"] == {"kind": "autopilot", "name": f"Deployment #{dep}"}
    assert item["mode"] == "PAPER" and item["status"] == "waiting"
    assert item["reasons"] == ["Made-up reason one", "Made-up reason two", "Three"]           # at most three
    assert item["expires_at"] and item["at"]
    assert body["sources"]["proposals"] is True


def test_every_source_reaches_today_with_its_kind_and_status():
    t = _owner("sources")
    _set_flag("execution_core", t["tenant_id"], on=True)
    _set_flag("screener_v2", t["tenant_id"], on=True)
    dep = _deployment(t)
    fill = _trade(t, deployment_id=dep)
    hist = _history(t)
    event = _rule_event(t, created_by=t["user_id"])
    intent = _intent(t, status="REFUSED")
    scan_prop = _proposal(t, source="scan", symbol="DEMO6")
    ticket = _proposal(t, source="ticket", symbol="DEMO7", status="REJECTED")
    body = _get(t, tab="today")
    by_id = {i["id"]: i for i in body["items"]}
    assert by_id[f"trade:{fill}"]["source"]["kind"] == "autopilot" and by_id[f"trade:{fill}"]["status"] == "taken"
    assert by_id[f"trade:{fill}"]["direction"] == "SHORT" and by_id[f"trade:{fill}"]["rr"] == 2.0
    assert by_id[f"history:{hist}"]["source"]["kind"] == "library" and by_id[f"history:{hist}"]["status"] == "info"
    assert by_id[f"alert:{event}"]["source"] == {"kind": "scan", "name": "Made-up gap scan"}
    assert by_id[f"alert:{event}"]["reasons"] == ["close: 101.5", "gap pct: 1.23"]
    assert by_id[f"intent:{intent}"]["status"] == "not_placed" and by_id[f"intent:{intent}"]["outcome"] == "Made-up routing note"
    assert by_id[f"action:{scan_prop}"]["source"]["kind"] == "scan"
    assert by_id[f"action:{ticket}"]["source"]["kind"] == "copilot" and by_id[f"action:{ticket}"]["status"] == "skipped"
    for item in body["items"]:
        assert set(item) == ITEM_KEYS
    assert body["counts"]["today"] == len(body["items"]) == 6
    # Newest first.
    ats = [i["at"] for i in body["items"]]
    assert ats == sorted(ats, reverse=True)


def test_a_source_whose_flag_is_off_contributes_nothing():
    t = _owner("flags")
    event = _rule_event(t, created_by=t["user_id"])
    intent = _intent(t)
    body = _get(t, tab="today")
    assert f"alert:{event}" not in _ids(body) and f"intent:{intent}" not in _ids(body)
    assert body["sources"]["scan_matches"] is False and body["sources"]["intents"] is False


def test_statuses_follow_the_proposal_and_dedup_keeps_one_row_per_signal():
    t = _owner("statuses")
    _set_flag("execution_core", t["tenant_id"], on=True)
    dep = _deployment(t)
    fill = _trade(t, deployment_id=dep)
    executed = _proposal(t, status="EXECUTED", deployment_id=dep, trade_id=fill, result="PAPER order placed")
    failed = _proposal(t, status="FAILED", result="Price moved too far")
    snoozed = _proposal(t, snoozed_until=_now() + timedelta(minutes=5))
    linked_intent = _intent(t, ai_action_id=executed)
    body = _get(t, tab="today")
    by_id = {i["id"]: i for i in body["items"]}
    assert by_id[f"action:{executed}"]["status"] == "taken"
    assert f"trade:{fill}" not in by_id                       # the fill of an approved proposal is that proposal's row
    assert f"intent:{linked_intent}" not in by_id             # an intent that became a proposal is that proposal's row
    assert by_id[f"action:{failed}"]["status"] == "not_placed" and by_id[f"action:{failed}"]["outcome"] == "Price moved too far"
    assert by_id[f"action:{snoozed}"]["status"] == "snoozed" and by_id[f"action:{snoozed}"]["snoozed_until"]
    assert _ids(_get(t, tab="waiting")) == []                 # a snoozed proposal is not waiting until it wakes


# --- expiry ---------------------------------------------------------------------------------------------------------

def test_an_overdue_proposal_reads_expired_and_the_feed_does_not_change_it():
    t = _owner("expiry")
    overdue = _proposal(t, created=_today(120), expires_in=timedelta(seconds=30))
    expired = _proposal(t, status="EXPIRED")
    live = _proposal(t)
    waiting = _get(t, tab="waiting")
    assert _ids(waiting) == [f"action:{live}"] and waiting["counts"]["waiting"] == 1
    by_id = {i["id"]: i for i in _get(t, tab="today")["items"]}
    assert by_id[f"action:{overdue}"]["status"] == "expired"
    assert by_id[f"action:{expired}"]["status"] == "expired"

    async def status():
        async with _session_factory() as session:
            return (await session.get(AiActionRecord, overdue)).status
    assert _run(status()) == "PROPOSED"                       # reported, not written: the worker expires it


# --- scoping --------------------------------------------------------------------------------------------------------

def test_another_organisations_rows_never_appear():
    a = _owner("scope-a")
    b = _owner("scope-b")
    for flag in ("execution_core", "screener_v2"):
        _set_flag(flag, b["tenant_id"], on=True)
        _set_flag(flag, a["tenant_id"], on=True)
    dep_b = _deployment(b)
    org_wide_b = _add(AiActionRecord(tenant_id=b["tenant_id"], action="ENTRY", rule="ENTRY_SIGNAL", reason="Made-up", status="PROPOSED",
                                     evidence_json=json.dumps({"symbol": "DEMO9", "direction": "LONG", "proposal_price": 1.0}),
                                     created_at=_today(), expires_at=_now() + timedelta(minutes=10), owner_user_id=None,
                                     idempotency_key=uuid.uuid4().hex))[0]
    planted = [f"action:{_proposal(b)}", f"action:{org_wide_b}", f"trade:{_trade(b, deployment_id=dep_b)}", f"history:{_history(b)}",
               f"alert:{_rule_event(b, created_by=b['user_id'])}", f"intent:{_intent(b)}"]
    for tab in ("waiting", "today", "history"):
        seen = _ids(_get(a, tab=tab))
        assert not set(seen) & set(planted), tab
    assert set(planted) <= set(_ids(_get(b, tab="history")))    # B itself sees them


def test_another_members_rows_never_appear_but_organisation_wide_proposals_do():
    owner = _owner("members")
    _upgrade_plan(owner["tenant_id"])                         # a second member needs a team plan
    _set_flag("execution_core", owner["tenant_id"], on=True)
    _set_flag("screener_v2", owner["tenant_id"], on=True)
    other = member(owner)
    dep = _deployment(owner)
    theirs = [f"action:{_proposal(owner, owner=other['user_id'])}", f"trade:{_trade(owner, deployment_id=dep, user_id=other['user_id'])}",
              f"history:{_history(owner, user_id=other['user_id'])}", f"alert:{_rule_event(owner, created_by=other['user_id'])}"]
    mine = f"action:{_proposal(owner)}"
    shared = _add(AiActionRecord(tenant_id=owner["tenant_id"], action="ENTRY", rule="ENTRY_SIGNAL", reason="Made-up", status="PROPOSED",
                                 evidence_json=json.dumps({"symbol": "DEMO8", "direction": "SHORT", "proposal_price": 5.0, "source": "tradingview"}),
                                 created_at=_today(), expires_at=_now() + timedelta(minutes=10), owner_user_id=None,
                                 idempotency_key=uuid.uuid4().hex))[0]
    seen = _ids(_get(owner, tab="today"))
    assert mine in seen and f"action:{shared}" in seen
    assert not set(seen) & set(theirs)                         # the organisation OWNER does not see a member's inbox
    member_seen = _ids(_get(other, tab="today"))
    assert set(theirs) <= set(member_seen) and f"action:{shared}" in member_seen and mine not in member_seen
    assert f"action:{shared}" in _ids(_get(other, tab="waiting"))


# --- tabs, filters, window, cursor --------------------------------------------------------------------------------------

def test_source_and_symbol_filters():
    t = _owner("filters")
    dep = _deployment(t)
    auto = _proposal(t, deployment_id=dep, symbol="DEMO1")
    scan = _proposal(t, source="scan", symbol="DEMO6")
    copilot = _proposal(t, source="manual", symbol="DEMO1")
    hist = _history(t, symbol="DEMO1")
    assert _ids(_get(t, source="autopilot")) == [f"action:{auto}"]
    assert _ids(_get(t, source="scan")) == [f"action:{scan}"]
    assert _ids(_get(t, source="copilot")) == [f"action:{copilot}"]
    by_symbol = _get(t, symbol="demo1")
    assert set(_ids(by_symbol)) == {f"action:{auto}", f"action:{copilot}", f"history:{hist}"} and by_symbol["symbol"] == "DEMO1"
    assert by_symbol["counts"] == _counts(2, 3)


def test_today_starts_at_midnight_ist_and_history_keeps_30_days():
    t = _owner("window")
    start = service.today_start(_now())
    yesterday = _history(t, at=start - timedelta(hours=2))
    old = _history(t, at=_now() - timedelta(days=31))
    today = _history(t)
    assert _ids(_get(t, tab="today")) == [f"history:{today}"]
    assert _ids(_get(t, tab="history")) == [f"history:{today}", f"history:{yesterday}"]
    assert f"history:{old}" not in _ids(_get(t, tab="history"))


def test_the_cursor_pages_through_every_row_once():
    t = _owner("pages")
    base = _today(600)
    made = [_history(t, at=base + timedelta(seconds=i // 2)) for i in range(7)]      # pairs share a timestamp
    made_actions = [_proposal(t, created=base + timedelta(seconds=1)) for _ in range(2)]   # ties across sources too
    seen, cursor, pages = [], None, 0
    while True:
        body = _get(t, tab="history", limit=3, **({"cursor": cursor} if cursor else {}))
        pages += 1
        seen += _ids(body)
        assert (body["counts"] is None) == (cursor is not None)
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert pages == 3
    assert len(seen) == len(set(seen)) == 9
    assert set(seen) == {f"history:{i}" for i in made} | {f"action:{i}" for i in made_actions}


def test_a_live_deployments_proposal_says_live():
    """The same rule as the approve endpoint's step-up: a LIVE deployment (or position) makes the proposal LIVE."""
    t = _owner("live")
    live = _proposal(t, deployment_id=_deployment(t, mode="LIVE"))
    paper = _proposal(t, source="manual")
    modes = {i["action_id"]: i["mode"] for i in _get(t, tab="waiting")["items"]}
    assert modes == {live: "LIVE", paper: "PAPER"}


def test_a_filtered_history_behind_many_other_rows_still_pages_to_the_end(monkeypatch):
    """Review: SCAN_CAP=4 / BATCH=2, six newer scan proposals and three older Copilot ones - History source=copilot
    returns the three (the kind filter is in SQL, so the scan rows are never read)."""
    monkeypatch.setattr(service, "SCAN_CAP", 4)
    monkeypatch.setattr(service, "BATCH", 2)
    t = _owner("capsql")
    base = _today(600)
    copilot = [_proposal(t, source="manual", status="REJECTED", created=base + timedelta(seconds=i)) for i in range(3)]
    for i in range(6):
        _proposal(t, source="scan", status="REJECTED", created=base + timedelta(seconds=10 + i))
    pages = _pages(t, tab="history", source="copilot")
    assert pages == [([f"action:{i}" for i in reversed(copilot)], None)]


def test_a_source_stopped_at_the_scan_cap_never_ends_or_skips_the_paging(monkeypatch):
    """The same case when a filter is not in SQL (the rows are dropped after reading): the read stops at SCAN_CAP, the
    page keeps only rows at or before that point - here none - and still gives a cursor; the next pages carry on."""
    monkeypatch.setattr(service, "SCAN_CAP", 4)
    monkeypatch.setattr(service, "BATCH", 2)
    monkeypatch.setattr(service, "_kind_clause", lambda dialect, kind: true())
    t = _owner("capcursor")
    base = _today(600)
    copilot = [_proposal(t, source="manual", status="REJECTED", created=base + timedelta(seconds=i)) for i in range(3)]
    for i in range(6):
        _proposal(t, source="scan", status="REJECTED", created=base + timedelta(seconds=10 + i))
    pages = _pages(t, tab="history", source="copilot")
    assert pages[0][0] == [] and pages[0][1] is not None             # nothing kept in the first 4 rows, yet more may follow
    seen = [i for ids, _ in pages for i in ids]
    assert seen == [f"action:{i}" for i in reversed(copilot)]


def test_rows_of_other_sources_past_the_cap_point_wait_for_the_next_page(monkeypatch):
    """A page across sources never jumps over a capped source's unread rows: older rows of the other sources are held
    back until the capped source has been read down to them; every row comes once, newest first."""
    monkeypatch.setattr(service, "SCAN_CAP", 4)
    monkeypatch.setattr(service, "BATCH", 2)
    keeps = service.FeedQuery.keeps
    monkeypatch.setattr(service.FeedQuery, "keeps", lambda self, item: item["symbol"] != "HIDE" and keeps(self, item))
    t = _owner("capmix")
    base = _today(900)
    for i in range(6):
        _proposal(t, symbol="HIDE", status="REJECTED", created=base + timedelta(seconds=100 + i))
    h50, h30 = _history(t, at=base + timedelta(seconds=50)), _history(t, at=base + timedelta(seconds=30))
    a40, a20, a10 = (_proposal(t, status="REJECTED", created=base + timedelta(seconds=s)) for s in (40, 20, 10))
    pages = _pages(t, tab="history")
    assert pages[0][0] == [] and pages[0][1] is not None             # the history rows wait below the cap point
    seen = [i for ids, _ in pages for i in ids]
    assert seen == [f"history:{h50}", f"action:{a40}", f"history:{h30}", f"action:{a20}", f"action:{a10}"]


def test_a_count_that_stops_at_the_scan_cap_says_so(monkeypatch):
    monkeypatch.setattr(service, "SCAN_CAP", 4)
    monkeypatch.setattr(service, "BATCH", 2)
    t = _owner("capcount")
    for _ in range(5):
        _proposal(t, source="manual")
    counts = _get(t, tab="waiting")["counts"]
    assert counts == _counts(4, 4, capped=True)


def test_the_proposal_list_says_whose_proposal_it_is():
    """M7: the Watchtower keeps another member's entry proposals - /api/ai/actions says whose each one is."""
    t = _owner("ownerid")
    mine, shared = _proposal(t), _proposal(t, symbol="DEMO9")
    _run(_clear_owner(shared))
    response = client.get("/api/ai/actions", headers=t["headers"], params={"status": "PROPOSED"})
    assert response.status_code == 200, response.text
    owners = {a["id"]: a["owner_user_id"] for a in response.json()}
    assert owners == {mine: t["user_id"], shared: None}


async def _clear_owner(action_id: int) -> None:
    async with _session_factory() as session:
        row = await session.get(AiActionRecord, action_id)
        row.owner_user_id = None
        await session.commit()


def test_the_postgres_filters_read_the_evidence_with_the_json_operator():
    from sqlalchemy.dialects import postgresql
    sql = str(service._kind_clause("postgresql", "scan").compile(dialect=postgresql.dialect()))
    assert "CAST(ai_actions.evidence_json AS JSON) ->>" in sql and "deployment_id IS NULL" in sql


def test_a_nan_in_a_signal_is_stored_as_null_never_as_nan():
    """Review: json.dumps writes NaN, which is not JSON (Postgres refuses it). The entry proposal's evidence keeps every
    non-finite float as null, and a NaN entry price is refused like a missing one - as InvalidEntryPrice, an
    EntriesPaused, so the webhook, public API, console, Telegram, ticket, worker and dispatcher callers answer it with
    the reason (no 500, no failure streak) through the handlers they already have."""
    from app.execution import approval
    from tests.test_z3_approval_entries import _sig
    t = _owner("nan")
    nan = float("nan")

    async def go():
        async with _session_factory() as session:
            signal = _sig(stop=nan, target=float("inf")).model_copy(update={"risk_reward": nan})
            row = await approval.propose_entry(session, t["tenant_id"], signal=signal, strategy_id="demo_strategy", source="tradingview",
                                               idempotency_key=f"ui23-nan:{uuid.uuid4().hex}")
            try:
                await approval.propose_entry(session, t["tenant_id"], signal=_sig(entry=nan), strategy_id="demo_strategy", source="tradingview",
                                             idempotency_key=f"ui23-nan:{uuid.uuid4().hex}")
                refused = None
            except approval.InvalidEntryPrice as exc:              # a named refusal every caller answers like a pause
                refused = exc
            return row.evidence_json, row.reason, refused
    text, reason, refused = _run(go())
    assert "NaN" not in text and "Infinity" not in text
    evidence = json.loads(text, parse_constant=lambda name: pytest.fail(f"{name} in the stored evidence"))
    assert evidence["stop_loss"] is None and evidence["target1"] is None and evidence["risk_reward"] is None
    assert evidence["signal"]["stop_loss"] is None and evidence["signal"]["target1"] is None
    assert "nan" not in reason.lower()
    assert isinstance(refused, approval.EntriesPaused) and "no usable entry price" in str(refused)


def test_a_tradingview_alert_with_nan_is_an_answer_never_a_500():
    """The NaN arrives through a webhook body (Python's JSON reader accepts NaN). #309: the payload refuses a NaN or
    infinite level at the door - a 422 naming the field, never a 500, and no proposal without a stop is raised."""
    from tests.test_tradingview_webhook import _get_token
    from tests.test_z3_approval_entries import _actions, _email, _flag_for, _tenant
    t = _tenant(_email("ui23-nan-tv"))
    _flag_for(t["tenant_id"])
    url = f"/api/webhooks/tradingview/{_get_token(t['headers'])}"

    def alert(entry: str, stop: str, alert_id: str):
        return client.post(url, headers={"Content-Type": "application/json"},
                           content='{"strategy_id": "pine_breakout", "symbol": "RELIANCE", "direction": "LONG", "entry": %s, '
                                   '"stop_loss": %s, "target1": 106.0, "alert_id": "%s"}' % (entry, stop, alert_id))
    for entry, stop, field in (("100.0", "NaN", "stop_loss"), ("NaN", "98.0", "entry"), ("Infinity", "98.0", "entry")):
        answer = alert(entry, stop, f"ui23-nan-{field}-{entry}")
        assert answer.status_code == 422, answer.text
        assert any(err["loc"][-1] == field for err in answer.json()["detail"])
    assert _actions(t["tenant_id"], "ENTRY") == []
    fine = alert("100.0", "98.0", "ui23-nan-ok")
    assert fine.status_code == 200 and "entry proposal #" in fine.json()["reasons"][0], fine.text


def test_the_finite_json_writer():
    from app.core.json_finite import dumps
    at = datetime(2026, 10, 8, 5, 0, tzinfo=UTC)
    assert json.loads(dumps({"a": float("nan"), "b": [1.5, float("-inf")], "c": (float("inf"),), "d": at})) == {
        "a": None, "b": [1.5, None], "c": [None], "d": str(at)}


def test_evidence_that_is_not_json_matches_no_filter_and_never_fails_the_feed():
    """A row stored before the writers refused NaN (or any unreadable evidence) is left out of the filtered views and
    the request still answers."""
    t = _owner("badjson")
    good = _proposal(t, source="manual", symbol="DEMO1")
    bad = _proposal(t, source="manual", symbol="DEMO1")

    async def spoil():
        async with _session_factory() as session:
            row = await session.get(AiActionRecord, bad)
            row.evidence_json = "not json at all"
            await session.commit()
    _run(spoil())
    for params in ({"source": "copilot"}, {"symbol": "DEMO1"}, {"source": "copilot", "symbol": "DEMO1"}, {"tab": "waiting", "source": "copilot"}):
        assert _ids(_get(t, **params)) == [f"action:{good}"], params
    assert f"action:{bad}" in _ids(_get(t))                          # unfiltered, it is still listed (its levels unknown)


def test_the_postgres_filters_check_the_evidence_is_json_before_reading_it():
    from sqlalchemy.dialects import postgresql
    for clause in (service._kind_clause("postgresql", "copilot"), func.upper(service._evidence("postgresql", "symbol")) == "DEMO1"):
        sql = str(clause.compile(dialect=postgresql.dialect()))
        assert "CASE WHEN pg_input_is_valid(ai_actions.evidence_json, " in sql and "THEN CAST((CAST(ai_actions.evidence_json AS JSON) ->>" in sql


@pytest.mark.skipif(not os.environ.get("Z5_TEST_PG_URL", "").startswith("postgresql"), reason="set Z5_TEST_PG_URL to a Postgres 16+ database")
def test_on_postgres_a_nan_row_matches_no_filter_and_raises_nothing():
    """On a real Postgres (CI's service): evidence with NaN or no JSON at all never makes a filtered read fail. Uses a
    temporary table named ai_actions, so the database is left as it was."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    rows = [(1, None, '{"source": "manual", "symbol": "DEMO1"}'), (2, None, '{"source": "scan", "symbol": "DEMO2"}'),
            (3, 5, '{"source": "deployment", "symbol": "DEMO3"}'), (4, None, '{"source": "manual", "symbol": "DEMO1", "stop_loss": NaN}'),
            (5, None, "not json"), (6, 7, '{"source": "deployment", "stop_loss": NaN}')]

    async def go():
        engine = create_async_engine(os.environ["Z5_TEST_PG_URL"])
        try:
            async with engine.connect() as conn:
                await conn.execute(text("CREATE TEMP TABLE ai_actions (id int, deployment_id int, evidence_json text)"))
                for row in rows:
                    await conn.execute(text("INSERT INTO ai_actions VALUES (:i, :d, :e)"), {"i": row[0], "d": row[1], "e": row[2]})
                out = {}
                for name, clause in (("copilot", service._kind_clause("postgresql", "copilot")), ("scan", service._kind_clause("postgresql", "scan")),
                                     ("autopilot", service._kind_clause("postgresql", "autopilot")),
                                     ("symbol", func.upper(service._evidence("postgresql", "symbol")) == "DEMO1")):
                    out[name] = sorted((await conn.execute(select(AiActionRecord.id).where(clause))).scalars())
                return out
        finally:
            await engine.dispose()
    assert _run(go()) == {"copilot": [1], "scan": [2], "autopilot": [3], "symbol": [1]}


# --- read-only ------------------------------------------------------------------------------------------------------

def test_the_feed_writes_nothing():
    t = _owner("nowrite")
    _set_flag("execution_core", t["tenant_id"], on=True)
    _set_flag("screener_v2", t["tenant_id"], on=True)
    dep = _deployment(t)
    _proposal(t, created=_today(120), expires_in=timedelta(seconds=10))      # overdue: must stay PROPOSED
    _proposal(t, snoozed_until=_now() - timedelta(minutes=1))                # snooze over: must not be "woken"
    _trade(t, deployment_id=dep)
    _rule_event(t, created_by=t["user_id"])
    _intent(t)

    async def snapshot():
        async with _session_factory() as session:
            actions = [(r.id, r.status, r.snoozed_until, r.decided_at, r.executed_at, r.result)
                       for r in await session.scalars(select(AiActionRecord).where(AiActionRecord.tenant_id == t["tenant_id"]))]
            counts = []
            for model in (AiActionRecord, TradeRecord, NotificationRecord, AuditLogRecord, ExecutionIntentRecord, AlertEventRecord, SignalHistoryRecord):
                counts.append(await session.scalar(select(func.count()).select_from(model).where(model.tenant_id == t["tenant_id"])))
            return actions, counts
    before = _run(snapshot())
    for tab in ("waiting", "today", "history"):
        for source in ("all", "autopilot", "scan", "copilot"):
            _get(t, tab=tab, source=source)
    assert _run(snapshot()) == before


def test_the_service_has_no_write_or_order_path():
    text = inspect.getsource(service)
    for forbidden in (".commit(", ".add(", ".add_all(", ".delete(", ".flush(", "insert(", "update(", "execute_signal", "place_order",
                      "propose_entry", "monitor.decide"):
        assert forbidden not in text, forbidden
