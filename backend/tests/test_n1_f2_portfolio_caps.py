"""N1 F2: the user's own portfolio caps on new PAPER entries (flag portfolio_risk_caps, with portfolio_risk; both off).

Entry refusals only, before a proposal and again at Approve. Trade's rules are mirrored on hand-built books
(`trading_engine.check_portfolio_risk_cap`: projected bucket max loss > cap refuses, equal passes, unknown capital
refuses; `max_open_positions`: open >= max refuses). Exits never consult the caps (ADR-0004): the call graph from
every exit path never reaches `app.portfolio.caps`, and a position over every cap still closes."""
import asyncio
import json
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.db.models import OrderRecord, TradeRecord, User
from app.execution import approval
from app.portfolio import caps as C
from app.portfolio import risk as R
from app.platform.controls import DEFAULT_OFF_FLAGS
from app.risk_engine import by_default
from tests.order_path_graph import CallGraph
from tests.test_auth_api import _session_factory, client
from tests.test_n1_e1_execution_core import _flag
from tests.test_trading_worker import _tenant
from tests.test_z10c_order_paths import GATED
from tests.test_z3_approval_entries import _flag_for, _price, _sig

UTC = timezone.utc


def _run(coro):
    return asyncio.run(coro)


def _email(tag):
    return f"n1f2-{tag}-{uuid.uuid4().hex[:6]}@example.com"


def _pos(underlying, risk=0.0, gross=0.0, key=None):
    return R.Position(key=key or uuid.uuid4().hex, symbol=underlying, underlying=underlying, kind="UNDERLYING", direction=R.BULLISH,
                      long_notional=gross, short_notional=0.0, risk=risk, priced_at_entry=False)


CAND = C.Candidate(symbol="NIFTYFUT", underlying="NIFTY", entry=100.0, stop=90.0, quantity=10.0)     # risk 100, notional 1,000


# --- the rules (pure; Trade's semantics) ----------------------------------------------------------------------------------

def test_open_positions_cap_refuses_at_the_limit():
    lim = C.Limits(max_open_positions=2)
    assert C.refusals([_pos("A")], {}, CAND, "INDEX", lim, 10_000, 0) == []
    out = C.refusals([_pos("A"), _pos("B")], {}, CAND, "INDEX", lim, 10_000, 0)
    assert len(out) == 1 and "2 open positions already (your limit is 2)" in out[0]


def test_correlated_risk_cap_pools_the_bucket_and_equal_passes():
    """Trade: existing bucket max loss + the new trade's > cap % of capital refuses; NIFTY, BANKNIFTY (and every other
    index) are one bucket; a stock in another sector does not count."""
    buckets = {"NIFTY": R.INDEX_BUCKET, "BANKNIFTY": R.INDEX_BUCKET, "INFY": "Information Technology"}
    lim = C.Limits(max_correlated_risk_pct=5.0)                     # 5% of 10,000 = 500
    book = [_pos("BANKNIFTY", risk=400.0), _pos("INFY", risk=900.0)]
    assert C.refusals(book, buckets, CAND, R.INDEX_BUCKET, lim, 10_000, 0) == []                       # 400 + 100 = 500
    over = C.refusals([_pos("BANKNIFTY", risk=401.0)], buckets, CAND, R.INDEX_BUCKET, lim, 10_000, 0)
    assert len(over) == 1 and "INDEX 401 + this entry 100 = 501" in over[0]
    # Unclassified names are never pooled with each other.
    assert C.refusals([_pos("ODD", risk=450.0)], {}, C.Candidate("X", "X", 100.0, 90.0, 10.0), "UNCLASSIFIED:X", lim, 10_000, 0) == []


def test_sector_exposure_cap_and_unknown_capital():
    lim = C.Limits(max_sector_pct=20.0)                              # 2,000
    buckets = {"INFY": "Information Technology"}
    cand = C.Candidate("TCS", "TCS", 100.0, 95.0, 10.0)
    assert C.refusals([_pos("INFY", gross=1000.0)], buckets, cand, "Information Technology", lim, 10_000, 0) == []
    out = C.refusals([_pos("INFY", gross=1000.01)], buckets, cand, "Information Technology", lim, 10_000, 0)
    assert out and "exposure in Information Technology" in out[0]
    assert "capital basis is 0" in C.refusals([], {}, cand, "X", lim, 0.0, 0)[0]                         # fail closed
    assert C.refusals([_pos("A")] * 50, {}, cand, "X", C.Limits(), 0.0, 0) == []                         # no limit set


def test_an_unsized_entry_is_taken_at_the_user_s_risk_cap():
    s = C.sized(C.Candidate("A", "A", 200.0, 190.0), risk_cap_amount=1000.0)
    assert s == {"quantity": 100.0, "risk": 1000.0, "notional": 20_000.0}
    assert C.sized(C.Candidate("A", "A", 200.0, 190.0, 5.0), 1000.0) == {"quantity": 5.0, "risk": 50.0, "notional": 1000.0}


def test_settings_keys_bounds_and_loosening_waits_a_day():
    assert C.FLAG in DEFAULT_OFF_FLAGS and C.BASE_FLAG in DEFAULT_OFF_FLAGS
    s = by_default.RiskSettings.from_json(json.dumps({"max_open_positions": 3, "max_sector_pct": 25, "max_correlated_risk_pct": 99}))
    assert s.max_open_positions == 3 and isinstance(s.max_open_positions, int) and s.max_sector_pct == 25.0
    assert s.max_correlated_risk_pct == 0.0                         # out of bounds -> the default (no limit)
    assert by_default.validate_settings({"max_correlated_risk_pct": 60}, {}) != []
    today = date(2026, 10, 12)
    text, applied, deferred, _ = by_default.plan_change(None, {"max_open_positions": 2}, today)      # 0 -> 2 tightens
    assert applied == {"max_open_positions": 2} and not deferred
    _, applied, deferred, start = by_default.plan_change(text, {"max_open_positions": 0}, today)    # back to no limit: tomorrow
    assert deferred == {"max_open_positions": 0} and not applied and start == "2026-10-13"
    eff, _ = by_default.effective_settings(s, {"risk_per_trade_pct": 0.1})
    assert eff.max_open_positions == 3 and eff.risk_per_trade_pct == 0.1


# --- before a proposal and at Approve -------------------------------------------------------------------------------------

def _limits(t, **values):
    async def go():
        async with _session_factory() as session:
            from app.paper.ticket import save_settings
            user = await session.get(User, t["user_id"])
            await save_settings(session, user, values)
    _run(go())


def _open(t, symbol, qty=10.0, entry=100.0, stop=98.0):
    async def go():
        async with _session_factory() as session:
            row = TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="PAPER", symbol=symbol, strategy_id="f2", direction="LONG",
                              entry_time=datetime.now(UTC) - timedelta(minutes=5), entry_price=entry, quantity=qty, stop_loss=stop)
            session.add(row)
            await session.commit()
            return row.id
    return _run(go())


def _try_propose(t, symbol="RELIANCE"):
    async def go():
        async with _session_factory() as session:
            try:
                row = await approval.propose_entry(session, t["tenant_id"], signal=_sig(symbol=symbol), strategy_id="f2", source="tradingview",
                                                   idempotency_key=f"f2:{uuid.uuid4().hex}", user_id=t["user_id"])
                return row.id
            except approval.EntriesPaused as exc:
                return str(exc)
    return _run(go())


def test_caps_refuse_a_proposal_only_with_both_flags_on():
    t = _tenant(_email("propose"), with_credentials=False)
    _limits(t, max_open_positions=1)
    _open(t, f"F2A{uuid.uuid4().hex[:4].upper()}")
    assert isinstance(_try_propose(t), int)                            # flags off: nothing changes
    _flag(C.FLAG, t["tenant_id"])
    assert isinstance(_try_propose(t), int)                            # portfolio_risk still off: still nothing
    _flag(C.BASE_FLAG, t["tenant_id"])
    refused = _try_propose(t)
    assert isinstance(refused, str) and "1 open positions already (your limit is 1)" in refused


def test_the_caps_are_checked_again_at_approve(monkeypatch):
    t = _tenant(_email("approve"))
    _flag_for(t["tenant_id"])
    for name in (C.FLAG, C.BASE_FLAG):
        _flag(name, t["tenant_id"])
    _limits(t, max_open_positions=1)
    action_id = _try_propose(t)
    assert isinstance(action_id, int)
    _open(t, f"F2B{uuid.uuid4().hex[:4].upper()}")                       # the book changed after the proposal
    _price(monkeypatch, 100.0)
    body = client.post(f"/api/ai/actions/{action_id}/approve", headers=t["headers"], json={}).json()
    assert body["status"] == "FAILED" and "your limit is 1" in body["result"] and "nothing was placed" in body["result"]

    async def orders():
        async with _session_factory() as session:
            return list(await session.scalars(select(OrderRecord).where(OrderRecord.tenant_id == t["tenant_id"])))
    assert _run(orders()) == []


# --- exits never consult the caps (ADR-0004) --------------------------------------------------------------------------------

EXIT_PATHS = ("app.trading.position_monitor.close_position", "app.trading.position_monitor.close_group",
              "app.trading.position_monitor.monitor_open_positions", "app.trading.position_monitor._square_off_live",
              "app.trading.stop_guard.verify_protective_stops", "app.kill_switch.routes.emergency_exit",
              "app.workers.trading_worker.TradingWorker._square_off_all", "app.ai.monitor.execute",
              # review L3: Exit now, the exit proposal, the manual close (mark price) and Telegram /exit
              "app.execution.approval.exit_now", "app.execution.approval.propose_exit", "app.trading.routes.exit_now",
              "app.trading.routes.mark_price", "app.telegram_inbound.service._exit_text")
ENTRY_ONLY = "app.execution.approval.execute_entry"      # app.ai.monitor.execute hands an approved ENTRY to it (and only it)


def test_no_exit_path_reaches_the_caps_and_the_caps_reach_no_order():
    graph = CallGraph(gated=GATED)
    caps_functions = {name for name in graph.functions if name.startswith("app.portfolio.caps.")}
    assert "app.portfolio.caps.entry_refusals" in caps_functions
    # An approved EXIT_POSITION goes through app.ai.monitor.execute too: its only call of execute_entry is under
    # `if action.action == "ENTRY"`, and without that branch nothing reaches the caps.
    import ast
    import inspect
    from app.ai import monitor
    tree = ast.parse(inspect.getsource(monitor.execute))
    branch = next(n for n in ast.walk(tree) if isinstance(n, ast.If) and "'ENTRY'" in ast.unparse(n.test))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and ast.unparse(n.func).endswith("execute_entry")]
    assert len(calls) == 1 and calls[0] in list(ast.walk(ast.Module(body=branch.body, type_ignores=[])))
    graph.functions["app.ai.monitor.execute"].edges.discard(ENTRY_ONLY)
    for start in EXIT_PATHS:
        assert start in graph.functions, start
        assert graph.reaches_any(start, caps_functions) is None, start
    assert not caps_functions & graph.reaching()
    callers = {c for f in caps_functions for c in graph.callers.get(f, ())} - caps_functions
    assert callers == {"app.execution.approval.propose_entry", "app.execution.approval.execute_entry"}


def test_a_position_over_every_cap_still_closes():
    from app.trading.position_monitor import close_position
    t = _tenant(_email("exit"), with_credentials=False)
    for name in (C.FLAG, C.BASE_FLAG):
        _flag(name, t["tenant_id"])
    _limits(t, max_open_positions=1, max_correlated_risk_pct=0.01, max_sector_pct=0.01)
    trade_ids = [_open(t, f"F2X{uuid.uuid4().hex[:4].upper()}") for _ in range(2)]

    async def go():
        async with _session_factory() as session:
            out = []
            for trade_id in trade_ids:
                out.append((await close_position(session, await session.get(TradeRecord, trade_id), 97.0, "Stop Loss")).closed)
            return out
    assert _run(go()) == [True, True]


# --- review of #219 ------------------------------------------------------------------------------------------------------------

def test_a_failed_read_rolls_back_its_savepoint_only_and_the_session_stays_usable(monkeypatch):
    """M1: the caps run on the worker's shared session; a failed read must not leave it aborted (Postgres)."""
    from sqlalchemy import text
    from app.portfolio import risk_data
    t = _tenant(_email("m1"), with_credentials=False)
    for name in (C.FLAG, C.BASE_FLAG):
        _flag(name, t["tenant_id"])
    _limits(t, max_open_positions=3)
    seen = []

    async def broken(session, user):
        seen.append(session.in_nested_transaction())
        await session.execute(text("SELECT * FROM no_such_table_f2"))
    monkeypatch.setattr(risk_data, "open_book", broken)

    async def go():
        async with _session_factory() as session:
            pending = TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="PAPER", symbol="F2M1", strategy_id="f2", direction="LONG",
                                  entry_time=datetime.now(UTC), entry_price=100.0, quantity=1.0, stop_loss=99.0)
            session.add(pending)
            await session.flush()
            user = await session.get(User, t["user_id"])
            out = await C.entry_refusals(session, user, C.candidate_of("RELIANCE", 100.0, 95.0))
            assert (await session.execute(select(TradeRecord.id).where(TradeRecord.id == pending.id))).scalar() == pending.id
            await session.commit()                                  # the outer transaction is intact
            return out, pending.id
    out, trade_id = _run(go())
    assert seen == [True] and len(out) == 1 and "could not be checked" in out[0]

    async def still_there():
        async with _session_factory() as session:
            return await session.get(TradeRecord, trade_id)
    assert _run(still_there()) is not None


def test_the_largest_size_uses_the_organisation_s_risk_per_trade_when_it_is_larger():
    """M2: with risk_by_default off the organisation's RiskConfig sizes the entry, which can be above the user's cap."""
    from app.core.models import RiskConfig
    from app.risk_engine.routes import get_tenant_risk_config
    t = _tenant(_email("m2"), with_credentials=False)

    async def go(pct):
        async with _session_factory() as session:
            from app.db.models import RiskSettingsRecord
            row = await session.scalar(select(RiskSettingsRecord).where(RiskSettingsRecord.tenant_id == t["tenant_id"]))
            if row is None:
                row = RiskSettingsRecord(tenant_id=t["tenant_id"], updated_by=t["user_id"])
                base = RiskConfig()
                for key in ("max_daily_loss_pct", "max_trades_per_day", "max_open_positions", "max_consecutive_losses", "min_risk_reward",
                            "lot_size", "max_portfolio_risk_pct", "stop_cooldown_minutes", "dd_level_1_pct", "dd_level_2_pct", "event_size_cut_pct"):
                    setattr(row, key, getattr(base, key))
                session.add(row)
            row.capital, row.risk_per_trade_pct = 2_000_000.0, pct
            await session.commit()
            user = await session.get(User, t["user_id"])
            settings = await by_default.user_settings(session, user)
            capital = await by_default.capital_basis(session, user)
            user_cap = by_default.risk_cap(capital, settings)["amount"]
            cfg = await get_tenant_risk_config(t["tenant_id"], session)
            return user_cap, await C.largest_risk_amount(session, user, capital, settings), cfg
    user_cap, largest, cfg = _run(go(1.5))
    assert user_cap == 10_000.0 and largest == 30_000.0                  # the organisation's 1.5% of 20 lakh sizes above the user's 1%
    user_cap, largest, _ = _run(go(5.0))                      # above the platform ceiling (2%): clamped
    assert largest == 40_000.0                                           # 5% clamped to the 2% ceiling


def test_a_contract_counts_under_its_underlying_or_is_refused(monkeypatch):
    """L1: a contract symbol is pooled with its underlying's bucket (instrument master, else the index it starts with);
    an unknown non-index derivative is refused."""
    from app.db.models import InstrumentRecord
    from app.portfolio import risk_data
    stock = f"F2L{uuid.uuid4().hex[:4].upper()}"
    contract = f"{stock}24OCT1500CE"

    async def seed():
        async with _session_factory() as session:
            session.add(InstrumentRecord(broker="test", exchange="NFO", instrument_key=f"NFO:{contract}", tradingsymbol=contract, name=stock,
                                         underlying=stock, instrument_type="CE", lot_size=1))
            await session.commit()
    _run(seed())

    async def resolve(*args):
        async with _session_factory() as session:
            return await risk_data.resolve_underlying(session, *args)
    assert _run(resolve(contract)) == stock
    assert _run(resolve("BANKNIFTY24OCT52000PE")) == "BANKNIFTY" and _run(resolve("NIFTY24OCTFUT")) == "NIFTY"
    assert _run(resolve("NIFTY 50")) == "NIFTY" and _run(resolve("RELIANCE")) == "RELIANCE"
    assert _run(resolve("ZZUNKNOWN24OCT100CE")) is None
    assert _run(resolve("ZZUNKNOWN24OCT100CE", "INFY")) == "INFY"                 # a stored underlying wins
    t = _tenant(_email("l1"), with_credentials=False)
    for name in (C.FLAG, C.BASE_FLAG):
        _flag(name, t["tenant_id"])
    _limits(t, max_correlated_risk_pct=1.0)

    async def caps_for(symbol):
        async with _session_factory() as session:
            return await C.entry_refusals(session, await session.get(User, t["user_id"]), C.candidate_of(symbol, 100.0, 99.0, quantity=1))
    assert _run(caps_for("NIFTY24OCT25000CE")) == []
    out = _run(caps_for("ZZUNKNOWN24OCT100CE"))
    assert len(out) == 1 and "underlying of ZZUNKNOWN24OCT100CE cannot be told" in out[0]


def test_a_failed_beta_read_is_retried_after_a_few_minutes(monkeypatch):
    """L2: an error is not cached for the IST day."""
    from app.portfolio import risk_data
    risk_data.clear_cache()
    calls = []
    clock = [1000.0]
    monkeypatch.setattr(risk_data.time, "monotonic", lambda: clock[0])

    async def broken(session, key, start, end):
        calls.append(key)
        raise RuntimeError("lake down")
    monkeypatch.setattr(risk_data, "_index_closes", broken)

    async def beta():
        async with _session_factory() as session:
            return await risk_data.beta_of(session, "F2BETA", date(2026, 10, 12))
    first = _run(beta())
    assert not first.known and "RuntimeError" in first.source and len(calls) == 1
    assert "tried again" in _run(beta()).source and len(calls) == 1
    clock[0] += risk_data.ERROR_RETRY_SECONDS
    _run(beta())
    assert len(calls) == 2
    risk_data.clear_cache()
