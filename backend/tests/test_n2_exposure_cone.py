"""N2-4 (P1 spec 1.5, docs/design/N2_P1FGH.md): the exposure / correlation view and the expected-move cone, behind the
`decision_tools` flag.

Covers:
- the pure statistics: returns, HV, correlation, beta and the cone, by hand;
- the book by underlying and the assembled view;
- the API on seeded daily lake bars: beta to the NIFTY 50 and correlated pairs as built into the data, sectors from
  the NSE classification, the caller's own positions only;
- the cone from the historical volatility or a typed IV;
- flag off, and no advice wording.
"""
import asyncio
import math
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.db.models import ClassificationRecord, MdCandleRecord, SecurityRecord, TradeRecord
from app.decision_tools import exposure, market, stats
from app.journal.review import advice_in
from app.market_data.calendar import IST
from app.market_lake.bhavcopy_backfill import bar_end
from tests.test_auth_api import _session_factory, client
from tests.test_n2_decision_tools import _flag
from tests.test_trading_worker import _tenant
from tests.test_z3b_paper_account import _member

UTC = timezone.utc


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _own_rows_removed():
    """The lake rows these tests seed (source "test-n2", version 99, NSE_INDEX:NIFTY50 among them) are removed before
    and after each test: left behind, they outrank other tests' seeds of the same keys (test_s10_etf_whatsapp's index
    change turned negative on the dates where the two overlapped)."""
    from sqlalchemy import delete

    def clear():
        async def go():
            async with _session_factory() as session:
                await session.execute(delete(MdCandleRecord).where(MdCandleRecord.source == "test-n2"))
                await session.commit()
        _run(go())
    clear()
    yield
    clear()


def _email(tag: str) -> str:
    return f"n2e-{tag}-{uuid.uuid4().hex[:6]}@example.com"


def _sessions(n: int):
    """The last n weekdays before today (IST), oldest first."""
    day = datetime.now(UTC).astimezone(IST).date() - timedelta(days=1)
    out = []
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day -= timedelta(days=1)
    return out[::-1]


def _seed(key: str, returns, start: float = 100.0) -> None:
    days = _sessions(len(returns) + 1)
    closes = [start]
    for r in returns:
        closes.append(closes[-1] * math.exp(r))

    async def go():
        async with _session_factory() as session:
            for d, c in zip(days, closes):
                await session.merge(MdCandleRecord(instrument_key=key, timeframe="day", ts=bar_end(d), source="test-n2", version=99,
                                                   open=c, high=c, low=c, close=c, volume=1))
            await session.commit()
    _run(go())


def _base(n=70):
    return [0.01 * math.sin(1.3 * t) + 0.002 * math.cos(0.7 * t) for t in range(n)]


# --- pure ------------------------------------------------------------------------------------------------------------

def test_statistics_by_hand():
    d = [date(2026, 1, i) for i in range(1, 6)]
    closes = {d[0]: 100.0, d[1]: 110.0, d[2]: 99.0, d[3]: 0.0, d[4]: 108.9}
    rets = stats.log_returns(closes)
    assert rets[d[1]] == pytest.approx(math.log(1.1)) and rets[d[2]] == pytest.approx(math.log(0.9)) and d[3] not in rets
    assert rets[d[4]] == pytest.approx(math.log(1.1))                      # the zero close is skipped, not divided by
    xs = [0.01, -0.02, 0.015, 0.0, -0.005]
    assert stats.correlation(xs, [2 * x for x in xs]) == pytest.approx(1.0)
    assert stats.correlation(xs, [-x for x in xs]) == pytest.approx(-1.0)
    assert stats.beta([2 * x for x in xs], xs) == pytest.approx(2.0)
    assert stats.correlation(xs, [1.0] * 5) is None and stats.beta(xs, [0.0] * 5) is None and stats.correlation([1, 2], [1, 2]) is None
    flat = {date(2026, 1, 1) + timedelta(days=i): 100.0 * 1.01 ** i for i in range(30)}
    assert stats.hv(flat, 20) == pytest.approx(0.0, abs=1e-12)            # a constant return has no spread
    assert stats.hv(flat, 40) is None
    row = stats.cone(100.0, 0.252, [1, 252])
    assert row[1]["move_1sd"] == pytest.approx(25.2) and row[1]["high_1sd"] == pytest.approx(round(100 * math.exp(0.252), 2))
    assert row[0]["low_2sd"] == pytest.approx(round(100 * math.exp(-2 * 0.252 * math.sqrt(1 / 252)), 2))


class _T:
    def __init__(self, id, symbol, kind, direction, qty, entry, stop=None, underlying=None, mode="PAPER"):
        self.id, self.symbol, self.instrument_kind, self.direction, self.quantity, self.entry_price = id, symbol, kind, direction, qty, entry
        self.stop_loss, self.underlying_symbol, self.mode, self.expiry = stop, underlying, mode, None


def test_book_and_assembly():
    rows = exposure.book([_T(1, "AAA", "UNDERLYING", "LONG", 10, 100.0, 95.0),
                          _T(2, "AAA", "UNDERLYING", "SHORT", 4, 110.0, 115.0),
                          _T(3, "NIFTY24OCT25000CE", "OPTION", "LONG", 75, 120.0, None, "NIFTY 50"),
                          _T(4, "NIFTY24OCT25100CE", "OPTION", "SHORT", 75, 90.0, 180.0, "NIFTY 50")])
    a, n = rows["AAA"], rows["NIFTY 50"]
    assert a["legs"] == 2 and a["notional"] == 1440.0 and a["net_quantity"] == 6.0 and a["risk_at_stop"] == 70.0 and a["legs_without_stop"] == []
    assert n["option_premium"] == 75 * 120.0 - 75 * 90.0 and n["risk_at_stop"] == 75 * 90.0 and n["legs_without_stop"] == ["NIFTY24OCT25000CE"]
    base = {date(2026, 1, 1) + timedelta(days=i): r for i, r in enumerate(_base(30))}
    view = exposure.assemble(rows, {"AAA": "IT", "NIFTY 50": "Index"}, {"AAA": {d: 2 * r for d, r in base.items()}, "NIFTY 50": base}, base, days=90)
    by = {u["underlying"]: u for u in view["underlyings"]}
    assert by["AAA"]["beta"] == pytest.approx(2.0) and by["NIFTY 50"]["beta"] == pytest.approx(1.0) and by["AAA"]["sector"] == "IT"
    assert view["pairs"] == [{"a": "AAA", "b": "NIFTY 50", "r": 1.0, "days": 30}] and view["move_together"] == view["pairs"]
    assert view["totals"] == {"underlyings": 2, "legs": 4, "risk_at_stop": 70.0 + 6750.0, "notional": 1440.0}
    assert [s["sector"] for s in view["sectors"]] == ["Index", "IT"]                # largest risk first
    short = exposure.assemble(rows, {}, {"AAA": dict(list(base.items())[:10]), "NIFTY 50": base}, base, days=90)
    assert {u["underlying"]: u["beta"] for u in short["underlyings"]}["AAA"] is None and short["pairs"][0]["r"] is None
    assert advice_in(view["note"]) == []


def test_lake_keys():
    assert market.lake_key("NIFTY 50") == "NSE_INDEX:NIFTY50" and market.lake_key("NIFTY") == "NSE_INDEX:NIFTY50"
    assert market.lake_key("reliance") == "NSE:RELIANCE" and market.index_name("BANKNIFTY") == "NIFTY BANK"


# --- API -----------------------------------------------------------------------------------------------------------------

def _open(t, symbol, kind, direction, qty, entry, stop=None, underlying=None):
    async def go():
        async with _session_factory() as session:
            session.add(TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="PAPER", symbol=symbol, strategy_id="manual",
                                    direction=direction, entry_time=datetime.now(UTC) - timedelta(hours=1), entry_price=entry, quantity=qty,
                                    stop_loss=stop, exchange="NSE", instrument_kind=kind, underlying_symbol=underlying))
            await session.commit()
    _run(go())


def _classify(symbol: str, sector: str) -> None:
    isin = ("INE" + uuid.uuid4().hex[:9]).upper()

    async def go():
        async with _session_factory() as session:
            session.add(SecurityRecord(isin=isin, symbol=symbol, name=symbol, series="EQ", source="test", checksum="t"))
            session.add(ClassificationRecord(isin=isin, scheme="NSE", sector=sector, valid_from=date(2020, 1, 1), valid_to=None, source="test", checksum="t"))
            await session.commit()
    _run(go())


def test_flag_off_hides_both_routes():
    t = _tenant(_email("off"))
    for path in ("/api/decision/exposure", "/api/decision/expected-move?instrument=NSE:RELIANCE"):
        r = client.get(path, headers=t["headers"])
        assert r.status_code == 404 and r.headers.get("X-Feature-Disabled") == "decision_tools", path


def test_exposure_from_the_lake_is_the_callers_own():
    tag = uuid.uuid4().hex[:5].upper()
    a, b, c = f"AAA{tag}", f"BBB{tag}", f"CCC{tag}"
    base = _base()
    _seed("NSE_INDEX:NIFTY50", base, 25000.0)
    _seed(f"NSE:{a}", base, 100.0)                                          # moves with the index: beta 1
    _seed(f"NSE:{b}", [2 * r for r in base], 50.0)                          # twice as much: beta 2, r = 1 with A
    _seed(f"NSE:{c}", [0.01 * math.cos(2.9 * i + 1.0) for i in range(70)], 80.0)
    _classify(a, "Information Technology")
    owner = _tenant(_email("owner"))
    _flag(owner["tenant_id"])
    member = _member(owner)
    _open(owner, a, "UNDERLYING", "LONG", 10, 100.0, 95.0)
    _open(owner, b, "UNDERLYING", "SHORT", 20, 50.0, 52.0)
    _open(owner, c, "UNDERLYING", "LONG", 5, 80.0, 0.0)                     # no stop (stored as 0)
    _open(member, "SOMEOTHER", "UNDERLYING", "LONG", 1, 10.0, 9.0)

    body = client.get("/api/decision/exposure?days=150", headers=owner["headers"]).json()
    by = {u["underlying"]: u for u in body["underlyings"]}
    assert set(by) == {a, b, c}                                             # never the member's position
    assert by[a]["beta"] == pytest.approx(1.0, abs=1e-3) and by[b]["beta"] == pytest.approx(2.0, abs=1e-3)
    assert by[a]["sector"] == "Information Technology" and by[b]["sector"] == "Unclassified"
    assert by[c]["legs_without_stop"] == [c] and by[a]["risk_at_stop"] == 50.0 and by[b]["risk_at_stop"] == 40.0
    pairs = {(p["a"], p["b"]): p for p in body["pairs"]}
    assert pairs[(a, b)]["r"] == pytest.approx(1.0, abs=1e-3) and pairs[(a, b)]["days"] == 70
    assert [(p["a"], p["b"]) for p in body["move_together"]] == [(a, b)]
    assert body["market_days"] >= 70 and body["totals"]["risk_at_stop"] == 90.0
    mine = client.get("/api/decision/exposure", headers=member["headers"]).json()
    assert [u["underlying"] for u in mine["underlyings"]] == ["SOMEOTHER"] and mine["underlyings"][0]["beta"] is None
    for text in (body["note"],):
        assert advice_in(text) == []


def test_expected_move_cone_from_hv_or_a_typed_iv():
    tag = uuid.uuid4().hex[:5].upper()
    name = f"CONE{tag}"
    _seed(f"NSE:{name}", _base(), 200.0)
    t = _tenant(_email("cone"))
    _flag(t["tenant_id"])
    r = client.get(f"/api/decision/expected-move?instrument=NSE:{name}&horizon=7", headers=t["headers"])
    assert r.status_code == 200, r.text
    body = r.json()

    async def closes():
        async with _session_factory() as session:
            return await market.daily_closes(session, name, days=60)
    expected_sigma = stats.hv(_run(closes()), 20)
    assert body["method"] == "hv20" and body["sigma"] == pytest.approx(expected_sigma, abs=1e-4)
    assert [row["sessions"] for row in body["cone"]] == [1, 5, 7, 10, 20] and body["selected"]["sessions"] == 7
    assert body["selected"]["low_2sd"] < body["selected"]["low_1sd"] < body["close"] < body["selected"]["high_1sd"] < body["selected"]["high_2sd"]
    typed = client.get(f"/api/decision/expected-move?instrument={name}&iv=0.252&horizon=1", headers=t["headers"]).json()
    assert typed["method"] == "iv" and typed["selected"]["move_1sd"] == pytest.approx(round(typed["close"] * 0.252 * math.sqrt(1 / 252), 2), abs=0.01)
    assert advice_in(body["note"]) == []
    assert client.get("/api/decision/expected-move?instrument=NSE:NOSUCH" + tag, headers=t["headers"]).status_code == 404
    few = f"FEW{tag}"
    _seed(f"NSE:{few}", _base(5), 10.0)
    assert client.get(f"/api/decision/expected-move?instrument={few}", headers=t["headers"]).status_code == 422



# --- review: the pair work is capped -------------------------------------------------------------------------------

def test_beta_and_pairs_are_capped_to_the_largest_risks(monkeypatch):
    trades = [_T(i, f"S{i:03d}", "UNDERLYING", "LONG", 1, 100.0, 100.0 - (i % 50) - 1) for i in range(60)]
    rows = exposure.book(trades)
    names = exposure.computed_names(rows)
    assert len(names) == exposure.MAX_COMPUTED
    risk = {n: rows[n]["risk_at_stop"] for n in rows}
    assert min(risk[n] for n in names) >= max(risk[n] for n in rows if n not in names)
    base = {date(2026, 1, 1) + timedelta(days=i): r for i, r in enumerate(_base(30))}
    view = exposure.assemble(rows, {}, {n: base for n in names}, base, days=90)
    assert len(view["pairs"]) == exposure.MAX_COMPUTED * (exposure.MAX_COMPUTED - 1) // 2
    assert len(view["not_computed"]) == 20 and all(not u["computed"] for u in view["underlyings"] if u["underlying"] in view["not_computed"])
    assert len(view["underlyings"]) == 60                                  # every position is still listed
    with pytest.raises(ValueError):
        exposure.assemble(rows, {}, {n: base for n in rows}, base, days=90)


def test_an_options_only_underlying_ranks_by_its_premium_not_last():
    """N2 review LOW: no stop and no notional (options only) - its premium still counts, a short premium included."""
    rows = {"FUTX": {"underlying": "FUTX", "risk_at_stop": 0.0, "notional": 1000.0, "option_premium": 0.0},
            "OPTS": {"underlying": "OPTS", "risk_at_stop": 0.0, "notional": 0.0, "option_premium": -5000.0},
            "AAA": {"underlying": "AAA", "risk_at_stop": 0.0, "notional": 0.0, "option_premium": 0.0}}
    assert exposure.computed_names(rows, limit=2) == ["OPTS", "FUTX"]


def test_exposure_route_reads_the_lake_only_for_the_capped_names(monkeypatch):
    t = _tenant(_email("cap"))
    _flag(t["tenant_id"])
    monkeypatch.setattr(exposure, "MAX_COMPUTED", 2)
    for i, stop in enumerate((90.0, 95.0, 99.0)):
        _open(t, f"CAP{i}{uuid.uuid4().hex[:4].upper()}", "UNDERLYING", "LONG", 1, 100.0, stop)
    read = []
    original = market.daily_closes

    async def counting(session, name, **kw):
        read.append(name)
        return await original(session, name, **kw)
    monkeypatch.setattr(market, "daily_closes", counting)
    body = client.get("/api/decision/exposure", headers=t["headers"]).json()
    assert len(body["underlyings"]) == 3 and len(body["not_computed"]) == 1 and body["max_computed"] == 2
    assert len([n for n in read if n != market.MARKET]) == 2
