"""LD-7 (500-user load test): fewer repeated reads and evaluations on the screener, the BYOB fan-in and login - with the
same answers.

* a screen run's funnel reads its stages back from the full evaluation (`runtime.ScreenMemo`): the same matches and funnel
  as before, on every kind of screen, and the stages are not evaluated a second time;
* `SessionSymbols.admit_many` answers as `admit` called once per symbol, with one read and one write of the set;
* the worker's fan-in reads every session's open positions with one select per cycle, and each session still gets only
  its own user's positions (BT1b);
* a login appends its audit row after the session row, right before the commit (the audit chain's lock is held for the
  shortest window), and the chain still verifies.
"""
import asyncio
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, List

import numpy as np
import pandas as pd
from sqlalchemy import event, select

from app.audit.log import verify_audit_chain
from app.db.models import AuditLogRecord, TradeRecord, UserSessionRecord
from app.market_data.fanin import Priority, SessionRef, SessionSymbols, UserSessionFanIn
from app.platform import controls
from app.screener import compile_screen
from app.screener import runtime
from app.screener.routes import screen_and_funnel
from app.screener.runtime import SymbolData, run_screen, stage_survivors
from tests.test_auth_api import _session_factory, client
from tests.test_phase_aa_market_data_api import _store_credentials
from tests.test_phase_k_commercial import _owner

UTC = timezone.utc
run = asyncio.run

SCREENS = [
    "close > SMA(close, 20) AND RSI(14) > 40 AND PctChange(close, 5) > -10",       # the load test's screen
    "close@1w > SMA(close, 4)@1w AND RSI(14) > 30",                                # a coarser timeframe
    "Rank(PctChange(close, 2)) <= 2 AND close > 60",                               # cross-sectional: no memo
    "(close > 60 OR close < 0) AND NOT close > 150 AND (EMA(close, 9) > EMA(close, 21) AND volume > 0)",
    "close > SMA(close, 200) AND close > 0",                                       # not enough history for some
    "close > 60 OR RSI(14) < 30",                                                  # one stage
    "CrossAbove(EMA(close, 9), EMA(close, 21)) AND ATR(14) > 0 AND close BETWEEN 50 AND 500",
]


def _universe() -> List[SymbolData]:
    rng = np.random.default_rng(7)
    out = []
    for i, bars in enumerate([280, 280, 280, 150, 280, 30, 280]):
        idx = pd.date_range("2025-06-02", periods=bars, freq="B", tz="UTC")
        c = (40 + 20 * i) * np.exp(np.cumsum(rng.normal(0, 0.02, bars)))
        out.append(SymbolData(f"S{i}", {"1d": pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1e5},
                                                           index=idx)}))
    out.append(SymbolData("EMPTY", {"1d": pd.DataFrame(columns=["open", "high", "low", "close", "volume"])}))
    return out


def test_the_funnel_with_the_memo_is_the_funnel_without_it():
    for text in SCREENS:
        ast, validated = compile_screen(text, base_tf="1d")
        assert validated.ok, text
        before_matches = run_screen(ast, validated, _universe(), base_tf="1d")
        before = stage_survivors(ast, validated, _universe(), base_tf="1d", matches=before_matches)
        matches, funnel = screen_and_funnel(ast, validated, _universe(), base_tf="1d", params={}, vectorized=False)
        assert matches == before_matches, text
        assert funnel == before, text
        # the vectorised path (flag screener_vectorized, off by default) ignores the memo and answers the same
        v_matches, v_funnel = screen_and_funnel(ast, validated, _universe(), base_tf="1d", params={}, vectorized=True)
        assert v_matches == before_matches and v_funnel == before, text


def test_the_stages_are_read_back_not_evaluated_again(monkeypatch):
    ast, validated = compile_screen(SCREENS[0], base_tf="1d")
    calls = {"n": 0}
    original = runtime._Eval._ev

    def counted(self, node, tf):
        calls["n"] += 1
        return original(self, node, tf)

    monkeypatch.setattr(runtime._Eval, "_ev", counted)
    universe = _universe()
    run_screen(ast, validated, universe, base_tf="1d", memo={})
    full_only = calls["n"]
    calls["n"] = 0
    screen_and_funnel(ast, validated, universe, base_tf="1d", params={}, vectorized=False)
    assert calls["n"] == full_only                    # the three stages cost no evaluation of their own
    calls["n"] = 0
    matches = run_screen(ast, validated, universe, base_tf="1d")
    stage_survivors(ast, validated, universe, base_tf="1d", matches=matches)
    assert calls["n"] > full_only                     # without the memo they did


def test_admit_many_answers_as_admit_one_symbol_at_a_time():
    rng = random.Random(11)
    now = datetime(2026, 10, 19, 5, 0, tzinfo=UTC)

    async def go():
        for trial in range(60):
            one, many = SessionSymbols(), SessionSymbols()
            ns = f"u{trial}.c1"
            # the same history in both registries: positions, charts, an old scan
            for reg in (one, many):
                await reg.replace(ns, Priority.POSITION, [("POS", "NSE")], now - timedelta(seconds=30))
                await reg.admit(ns, "CH0", "NSE", Priority.CHART, 10, now - timedelta(seconds=20))
                await reg.admit(ns, "OLD", "NSE", Priority.SCREENER, 10, now - timedelta(seconds=10))
            symbols = [(rng.choice(["RELIANCE", "tcs", "INFY", "CH0", "POS", "SBIN", "OLD"]), rng.choice(["NSE", "nse", "BSE"]))
                       for _ in range(rng.randint(0, 6))]
            priority = rng.choice([Priority.CHART, Priority.SCREENER, Priority.WATCHLIST])
            cap, register = rng.randint(1, 6), rng.random() < 0.7
            expected = [await one.admit(ns, s, e, priority, cap, now, register=register) for s, e in symbols]
            assert await many.admit_many(ns, symbols, priority, cap, now, register=register) == expected
            assert await many.load(ns) == await one.load(ns)

    run(go())


def _flag_byob(tenants: List[int]) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            flags["byob_data_tier"] = {"on": False, "tenants": tenants}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    run(go())


def test_the_fan_in_reads_every_sessions_positions_with_one_select_and_keeps_them_apart():
    _, a = _owner(f"ld7-fan-a-{uuid.uuid4().hex[:8]}@example.com")
    _, b = _owner(f"ld7-fan-b-{uuid.uuid4().hex[:8]}@example.com")
    _, c = _owner(f"ld7-fan-c-{uuid.uuid4().hex[:8]}@example.com")       # a session with no open position
    for me in (a, b, c):
        _store_credentials(me)
    now = datetime(2026, 10, 19, 5, 0, tzinfo=UTC)                         # Monday 10:30 IST

    async def seed():
        async with _session_factory() as session:
            def trade(me: Any, symbol: str, user_id: int) -> TradeRecord:
                return TradeRecord(tenant_id=me["tenant_id"], user_id=user_id, mode="PAPER", symbol=symbol, strategy_id="s",
                                   direction="LONG", entry_time=now, entry_price=100.0, quantity=1, stop_loss=90.0, exchange="NSE")
            session.add_all([trade(a, "RELIANCE", a["id"]), trade(b, "TCS", b["id"]), trade(b, "INFY", b["id"]),
                             trade(a, "SBIN", c["id"])])                 # A's organisation, another user's row: not A's
            closed = trade(a, "ITC", a["id"])
            closed.exit_time = now
            session.add(closed)
            await session.commit()
    run(seed())
    _flag_byob([a["tenant_id"], b["tenant_id"], c["tenant_id"]])

    def ns(me: Any) -> str:
        async def go():
            async with _session_factory() as session:
                from app.db.models import BrokerCredentialRecord
                record = await session.scalar(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == me["tenant_id"]))
                return SessionRef.of(record).namespace
        return run(go())

    names = {key: ns(me) for key, me in (("a", a), ("b", b), ("c", c))}

    async def go():
        fan = UserSessionFanIn(symbols=SessionSymbols(), stream_factory=lambda adapter: None, adapter_factory=lambda r: object(),
                               streaming=False)
        selects: List[str] = []
        try:
            async with _session_factory() as session:
                engine = session.bind.sync_engine if hasattr(session.bind, "sync_engine") else session.bind

                def on_execute(conn, cursor, statement, parameters, context, executemany):
                    if statement.lstrip().upper().startswith("SELECT") and "FROM TRADES" in statement.upper():
                        selects.append(statement)

                event.listen(engine, "before_cursor_execute", on_execute)
                try:
                    report = await fan.sync(session, now, market_open=True)
                finally:
                    event.remove(engine, "before_cursor_execute", on_execute)
            assert not report.errors, report.errors
            assert {names["a"], names["b"], names["c"]} <= set(fan.sessions)
            assert len(selects) == 1, selects                                        # one select for every session
            kept = {key: set(fan.sessions[name].plan.kept) for key, name in names.items()}
            assert kept["a"] == {("RELIANCE", "NSE")}                                  # not B's, not the other user's
            assert kept["b"] == {("TCS", "NSE"), ("INFY", "NSE")}
            assert kept["c"] == set()
        finally:
            await fan.close_all()
    try:
        run(go())
    finally:
        _flag_byob([])


def test_a_login_appends_its_audit_row_after_the_session_row(monkeypatch):
    from app.auth import routes as auth_routes
    email = f"ld7-login-{uuid.uuid4().hex[:8]}@example.com"
    password = "Ld7-login-Pass1!"
    assert client.post("/api/auth/register", json={"email": email, "password": password, "full_name": "LD7"}).status_code in (200, 201)
    seen: List[int] = []
    original = auth_routes.write_audit_log

    async def spy(session, tenant_id, user_id, event_name, detail=""):
        if event_name == "user_login":
            # the session row is already written when the audit append (and its chain lock) starts
            seen.append(len(list(await session.scalars(select(UserSessionRecord).where(UserSessionRecord.user_id == user_id)))))
        return await original(session, tenant_id, user_id, event_name, detail)

    monkeypatch.setattr(auth_routes, "write_audit_log", spy)
    res = client.post("/api/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    assert seen and seen[-1] >= 2                      # registration's session and this login's

    async def check():
        async with _session_factory() as session:
            assert await session.scalar(select(AuditLogRecord).where(AuditLogRecord.event == "user_login").order_by(AuditLogRecord.id.desc()))
            assert (await verify_audit_chain(session))[0]
    run(check())
