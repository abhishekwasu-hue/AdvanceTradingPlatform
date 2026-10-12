"""U7 LG3: point-in-time fundamentals and the value / growth Legends cards (Graham, Greenblatt, O'Neil).

* the store: every write of a period appends a version only when its figures change; `known_from` = the day after the
  cited publication for a first version (never before its period ends, never after its ingestion), the ingestion for a
  restatement - so a restated figure never reaches back; `load_versions(until)` and `as_of` never return later knowledge;
* the measures (EPS / sales growth, earnings yield, return on capital, Graham number, NCAV per share) from those
  versions; trailing = four consecutive quarters or the last year; stale periods give NaN;
* the runtime: a bar sees only the versions known by its close; adding later versions or bars leaves earlier values
  unchanged (the Z10-a causal cases cover every function as well);
* the gate: `screener_fundamentals` off -> hidden from the registry, refused in a screen; alert rules refuse them;
* the cards: lint, review status of the new sources, a positive and a negative fixture each (the negative: the same
  figures known only after the last bar), flag `screener_legends_lg3` (off by default).
Synthetic figures only - no provider, no network. Nothing here places an order.
"""
import asyncio
import math
import uuid
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import delete, select

from app.core.enums import PeriodType
from app.db.models import CompanyRecord, FinancialPeriodVersionRecord
from app.fundamentals import pit
from app.fundamentals.models import FinancialPeriod, SourceCitation
from app.platform import controls
from app.screener import compile_screen
from app.screener import fundamentals as F
from app.screener import legends as lg
from app.screener.runtime import SymbolData, evaluate, run_screen
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner
from tests.test_z8_tx1_taxonomy import _set_flags

UTC = timezone.utc
LG3_CARDS = ["legends.graham_net_net", "legends.graham_number_value", "legends.greenblatt_yield_and_return", "legends.oneil_growth_breakout"]


def _run(coro):
    return asyncio.run(coro)


def _period(label, end, *, kind=PeriodType.QUARTER, pub=None, **figs):
    base = dict(revenue=1000.0, ebitda=200.0, pat=100.0, eps=10.0)
    base.update(figs)
    return FinancialPeriod(period_type=kind, period_label=label, period_end_date=end,
                           source=SourceCitation(source="test filing", publication_date=pub) if pub else None, **base)


def _v(label, end, known, *, kind="QUARTER", version=1, **figs):
    return pit.Version(kind, label, end, version, figs, known)


# --- the store -------------------------------------------------------------------------------------------------------
def test_known_from_rules():
    ing = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    end = date(2026, 3, 31)
    assert pit.known_from(version=1, publication_date=date(2026, 5, 10), period_end=end, ingested_at=ing) == datetime(2026, 5, 11, 3, 45, tzinfo=UTC)
    assert pit.known_from(version=1, publication_date=None, period_end=end, ingested_at=ing) == ing                     # no citation date
    assert pit.known_from(version=1, publication_date=date(2026, 7, 1), period_end=end, ingested_at=ing) == ing         # "published" later
    assert pit.known_from(version=1, publication_date=date(2026, 3, 1), period_end=end, ingested_at=ing) == ing         # before its own end
    assert pit.known_from(version=2, publication_date=date(2026, 5, 10), period_end=end, ingested_at=ing) == ing         # a restatement


def test_versions_are_appended_only_on_change_and_never_reach_back():
    sym = f"LG3PIT{uuid.uuid4().hex[:6].upper()}"

    async def go():
        async with _session_factory() as session:
            company = CompanyRecord(symbol=sym, name="Test", sector="Test", industry="Test")
            session.add(company)
            await session.flush()
            t1, t2 = datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC)
            q = _period("Q4FY26", date(2026, 3, 31), pub=date(2026, 5, 10), eps=12.0)
            first = await pit.record_version(session, company.id, q, now=t1)
            same = await pit.record_version(session, company.id, q, now=t1 + timedelta(days=1))
            restated = await pit.record_version(session, company.id, _period("Q4FY26", date(2026, 3, 31), pub=date(2026, 5, 10), eps=9.0), now=t2)
            await session.commit()
            assert first is not None and same is None and restated is not None and restated.version == 2
            assert restated.known_from.replace(tzinfo=UTC) == t2                                    # not back to 11 May
            before = await pit.load_versions(session, [sym.lower()], until=datetime(2026, 7, 1, tzinfo=UTC))
            after = await pit.load_versions(session, [sym])
            assert [v.version for v in before[sym]] == [1]
            assert [v.get("eps") for v in pit.as_of(after[sym], datetime(2026, 8, 1, tzinfo=UTC))] == [12.0]
            assert [v.get("eps") for v in pit.as_of(after[sym], datetime(2026, 9, 2, tzinfo=UTC))] == [9.0]
            assert pit.as_of(after[sym], datetime(2026, 5, 11, 3, 0, tzinfo=UTC)) == []                  # before it was public
            await session.execute(delete(FinancialPeriodVersionRecord).where(FinancialPeriodVersionRecord.company_id == company.id))
            await session.execute(delete(CompanyRecord).where(CompanyRecord.id == company.id))
            await session.commit()
    _run(go())


def test_the_import_path_records_versions():
    from app.fundamentals import ingest
    sym = f"LG3IMP{uuid.uuid4().hex[:6].upper()}"

    async def go():
        async with _session_factory() as session:
            company = CompanyRecord(symbol=sym, name="Test", sector="Test", industry="Test")
            session.add(company)
            await session.flush()
            await ingest.upsert_financial_periods(session, company, [_period("Q1", date(2026, 6, 30)), _period("Q2", date(2026, 9, 30))], None)
            await ingest.upsert_financial_periods(session, company, [_period("Q1", date(2026, 6, 30), eps=11.0)], None)
            await session.commit()
            rows = (await session.scalars(select(FinancialPeriodVersionRecord).where(FinancialPeriodVersionRecord.company_id == company.id))).all()
            assert sorted((r.period_label, r.version) for r in rows) == [("Q1", 1), ("Q1", 2), ("Q2", 1)]
            await session.execute(delete(FinancialPeriodVersionRecord).where(FinancialPeriodVersionRecord.company_id == company.id))
            await session.execute(delete(CompanyRecord).where(CompanyRecord.id == company.id))
            await session.commit()
    _run(go())


# --- the measures ----------------------------------------------------------------------------------------------------
K0 = datetime(2025, 1, 1, tzinfo=UTC)
QUARTERS = [date(2025, 3, 31), date(2025, 6, 30), date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31)]


def _book(eps_last=15.0, rev_last=1500.0):
    out = [_v(f"Q{i}", end, K0, eps=10.0, revenue=1000.0, ebit=200.0) for i, end in enumerate(QUARTERS[:-1])]
    out.append(_v("Q4", QUARTERS[-1], K0, eps=eps_last, revenue=rev_last, ebit=300.0, shares_outstanding=100.0, shareholders_equity=5000.0,
                  total_assets=9000.0, current_assets=6000.0, current_liabilities=1000.0, cash_and_equivalents=500.0, total_debt=1500.0))
    return out


def test_the_measures_from_known_versions():
    st = F.state(_book())
    assert st["eps_growth"] == pytest.approx(50.0) and st["sales_growth"] == pytest.approx(50.0)          # Q4 vs Q0 (a year apart)
    assert st["ebit"] == pytest.approx(200 * 3 + 300)                                                       # four consecutive quarters
    assert st["roc"] == pytest.approx(900 / (9000 - 1000 - 500) * 100)
    assert st["graham_number"] == pytest.approx(math.sqrt(22.5 * (10 * 3 + 15) * 50))
    assert st["ncav"] == pytest.approx((6000 - (9000 - 5000)) / 100)
    annual = [_v("FY25", date(2025, 3, 31), K0, kind="ANNUAL", ebit=800.0, eps=40.0)]
    assert F.state(annual)["ebit"] == 800.0 and F.state(annual)["eps_growth"] is None                       # no quarters: no growth
    loss = F.state(_book(eps_last=-50.0))
    assert loss["graham_number"] is None                                                                    # negative earnings: none


def _bars(start, n, close=100.0):
    idx = pd.date_range(start, periods=n, freq="1D", tz="UTC") + pd.Timedelta(hours=3, minutes=45)
    c = np.full(n, close)
    return pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": np.full(n, 1000.0)}, index=idx)


def test_a_bar_sees_only_what_was_known_by_its_close_and_restatements_count_from_then():
    f = _bars("2026-05-01", 60)
    known = datetime(2026, 5, 20, 3, 45, tzinfo=UTC)
    book = [dataclasses_replace(v, known_from=known) for v in _book()]
    restated = _v("Q4", QUARTERS[-1], datetime(2026, 6, 10, 3, 45, tzinfo=UTC), version=2,
                  **{**book[-1].figures, "eps": 20.0})
    s = evaluate(compile_screen("EPSGrowthYoY()", base_tf="1d")[0], SymbolData("X", {"1d": f}, fundamentals=book + [restated]), base_tf="1d")
    closes = f.index + pd.Timedelta(days=1)                                                                 # a daily bar's close
    assert s[closes < known].isna().all()                                                                   # before the report
    mid = s[(closes >= known) & (closes < pd.Timestamp("2026-06-10 03:45", tz="UTC"))]
    late = s[closes >= pd.Timestamp("2026-06-10 03:45", tz="UTC")]
    assert len(mid) and np.allclose(mid, 50.0) and len(late) and np.allclose(late, 100.0)                    # the restated EPS from then
    head = evaluate(compile_screen("EPSGrowthYoY()", base_tf="1d")[0], SymbolData("X", {"1d": f.iloc[:30]}, fundamentals=book), base_tf="1d")
    pd.testing.assert_series_equal(head, s.iloc[:30])                                                       # later knowledge changes nothing
    ey = evaluate(compile_screen("EarningsYield()", base_tf="1d")[0], SymbolData("X", {"1d": f}, fundamentals=book), base_tf="1d")
    assert ey.iloc[-1] == pytest.approx(900 / (100 * 100 + 1500 - 500) * 100)
    stale = _bars("2027-09-01", 5)
    assert evaluate(compile_screen("NCAVPerShare()", base_tf="1d")[0], SymbolData("X", {"1d": stale}, fundamentals=book), base_tf="1d").isna().all()


def dataclasses_replace(v, **kw):
    import dataclasses
    return dataclasses.replace(v, **kw)


# --- the gate --------------------------------------------------------------------------------------------------------
def test_the_flag_hides_and_refuses_and_alerts_refuse():
    headers, _ = _owner(f"lg3-gate-{uuid.uuid4().hex[:8]}@example.com")
    _set_flags(screener_v2=True)
    try:
        reg = client.get("/api/screener/registry", headers=headers).json()["entries"]
        assert "EarningsYield" not in reg and "SMA" in reg
        v = client.post("/api/screener/validate", json={"source": "EarningsYield() > 5", "base_tf": "1d"}, headers=headers).json()
        assert not v["ok"] and any("screener_fundamentals" in p["message"] for p in v["problems"])
        _set_flags(screener_fundamentals=True)
        assert "EarningsYield" in client.get("/api/screener/registry", headers=headers).json()["entries"]
        assert client.post("/api/screener/validate", json={"source": "EarningsYield() > 5", "base_tf": "1d"}, headers=headers).json()["ok"]
        r = client.post("/api/alerts/rules", json={"name": "f", "kind": "instrument", "symbol": "ABC", "condition": "EarningsYield() > 5",
                                                   "base_tf": "1d"}, headers=headers)
        assert r.status_code == 422 and "do not read fundamentals" in str(r.json()), r.text
    finally:
        _set_flags(screener_v2=False, screener_fundamentals=False)
    assert {"screener_fundamentals", "screener_legends_lg3"} <= controls.DEFAULT_OFF_FLAGS


def test_attach_loads_versions_known_by_now():
    async def go():
        async with _session_factory() as session:
            data = [SymbolData("NOSUCHCO", {})]
            await F.attach(session, data)
            return data[0].fundamentals
    assert _run(go()) == []


# --- the cards -------------------------------------------------------------------------------------------------------
def test_the_lg3_cards_pass_lint_with_their_review_status():
    assert lg.lint() == [] and lg.link_problems() == []
    lib = lg.load()
    assert [c.id for c in lib.of(("lg3",))] == LG3_CARDS
    review = {c.id: c.review for c in lib.of(("lg3",))}
    assert review == {"legends.graham_net_net": "unverified", "legends.graham_number_value": "unverified",
                      "legends.greenblatt_yield_and_return": "verified", "legends.oneil_growth_breakout": "verified"}
    assert all(lib.sources[s].review_note for s in ("graham_1949", "graham_dodd_1934", "greenblatt_2006", "oneil_1988"))
    assert "Magic Formula" in lg.TRADEMARK_TERMS and lg.wording_problems("x", "strong returns") and not lg.wording_problems("x", "return on capital")


def _year_of_quarters(known, eps=(10, 10, 10, 10, 15), rev=(1000, 1000, 1000, 1000, 1500), **last):
    out = [_v(f"Q{i}", end, known, eps=float(e), revenue=float(r), ebit=200.0) for i, (end, e, r) in enumerate(zip(QUARTERS, eps, rev))]
    out[-1] = dataclasses_replace(out[-1], figures={**out[-1].figures, "shares_outstanding": 100.0, "shareholders_equity": 5000.0, "total_assets": 9000.0,
                                                    "current_assets": 6000.0, "current_liabilities": 1000.0, "cash_and_equivalents": 500.0,
                                                    "total_debt": 1500.0, **last})
    return out


def _run_card(card_id, universe):
    card = lg.load().card(card_id)
    ast, validated = compile_screen(card.source, base_tf=card.base_tf, params=card.defaults())
    assert validated.ok, validated.problems
    return run_screen(ast, validated, universe, base_tf=card.base_tf, params=card.defaults())


EARLY, LATE = datetime(2026, 5, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)     # known before the bars / after the last bar


def test_graham_cards():
    f = _bars("2026-06-01", 30, close=10.0)                                            # NCAV/share 20, Graham number 225
    for card in ("legends.graham_net_net", "legends.graham_number_value"):
        assert _run_card(card, [SymbolData("A", {"1d": f}, fundamentals=_year_of_quarters(EARLY))])[0].matched, card
        late = _run_card(card, [SymbolData("A", {"1d": f}, fundamentals=_year_of_quarters(LATE))])[0]
        assert not late.matched, card                                                  # the same figures, known only later
    dear = _bars("2026-06-01", 30, close=300.0)
    assert not _run_card("legends.graham_number_value", [SymbolData("A", {"1d": dear}, fundamentals=_year_of_quarters(EARLY))])[0].matched


def test_greenblatt_card_ranks_within_the_list():
    f = _bars("2026-06-01", 30, close=50.0)
    good = _year_of_quarters(EARLY)
    poor = _year_of_quarters(EARLY, total_assets=90000.0, total_debt=30000.0)          # same EBIT: more capital, a dearer firm
    pricey = _bars("2026-06-01", 30, close=500.0)
    universe = [SymbolData("GOOD", {"1d": f}, fundamentals=good), SymbolData("POOR", {"1d": f}, fundamentals=poor),
                SymbolData("PRICEY", {"1d": pricey}, fundamentals=poor), SymbolData("LATE", {"1d": f}, fundamentals=_year_of_quarters(LATE))]
    got = {m.symbol: m.matched for m in _run_card("legends.greenblatt_yield_and_return", universe)}
    assert got == {"GOOD": True, "POOR": False, "PRICEY": False, "LATE": False}


def test_oneil_card_needs_growth_known_and_a_volume_breakout():
    close = list(np.linspace(80, 99, 260)) + [105.0]
    idx = pd.date_range("2025-09-01", periods=len(close), freq="1D", tz="UTC") + pd.Timedelta(hours=3, minutes=45)
    c = np.asarray(close)
    f = pd.DataFrame({"open": c - 0.5, "high": c + 0.5, "low": c - 1, "close": c, "volume": [1000.0] * (len(c) - 1) + [3000.0]}, index=idx)
    assert _run_card("legends.oneil_growth_breakout", [SymbolData("A", {"1d": f}, fundamentals=_year_of_quarters(EARLY))])[0].matched
    assert not _run_card("legends.oneil_growth_breakout", [SymbolData("A", {"1d": f}, fundamentals=_year_of_quarters(LATE))])[0].matched
    flat = _year_of_quarters(EARLY, eps=(10, 10, 10, 10, 11), rev=(1000,) * 5)
    assert not _run_card("legends.oneil_growth_breakout", [SymbolData("A", {"1d": f}, fundamentals=flat)])[0].matched


def test_lg3_cards_show_only_with_their_flag():
    headers, _ = _owner(f"lg3-api-{uuid.uuid4().hex[:8]}@example.com")
    _set_flags(screener_v2=True, screener_legends=True)
    try:
        ids = [c["id"] for c in client.get("/api/screener/legends", headers=headers).json()["cards"]]
        assert not set(LG3_CARDS) & set(ids)
        assert client.get(f"/api/screener/legends/{LG3_CARDS[0]}", headers=headers).status_code == 404
        _set_flags(screener_legends_lg3=True)
        ids = [c["id"] for c in client.get("/api/screener/legends", headers=headers).json()["cards"]]
        assert ids[-4:] == LG3_CARDS
    finally:
        _set_flags(screener_v2=False, screener_legends=False, screener_legends_lg3=False)
