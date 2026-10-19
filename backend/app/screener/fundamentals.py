"""U7 LG3: the ScreenQL fundamentals functions - point in time - and their flag gate.

* Data: `app.fundamentals.pit` - the versions of each company's reported figures with the time they became public. A
  bar sees, per period, the latest version known by the bar's CLOSE (`pit.as_of`): never a figure published later,
  never a restatement before it was made (a later version counts only from its ingestion). Price-based measures use
  that bar's own close. So a value at bar t never changes when later bars or later versions are added (tested; Z10-a
  causal cases with a synthetic timeline built from the bars up to each report).
* Measures (NaN where the figures are missing, non-positive where a ratio needs them positive, or stale - a period the
  measure itself reads ended more than STALE_DAYS before the bar):
    EPSGrowthYoY / SalesGrowthYoY  latest quarter vs the same quarter a year before, % (both quarters known);
    EarningsYield                  trailing EBIT / enterprise value (close x shares + debt - cash), %;
    ReturnOnCapital                trailing EBIT / (total assets - current liabilities - cash), %;
    GrahamNumber                   sqrt(22.5 x trailing EPS x book value per share), a price;
    NCAVPerShare                   (current assets - total liabilities) / shares, a price (liabilities = assets - equity).
  Trailing = the last four quarters when they are consecutive, else the latest annual period.
  Per-share figures (EPS, the share count) are rebased onto the bars' split-adjusted basis (`rebase`): a split or bonus
  after a period's end scales them by the same factor as the closes before it.
* Flag `screener_fundamentals` (off by default), the U6 BK1 / Z7a-3 pattern: the functions stay in the one registry;
  while the flag is off the registry endpoint hides them and a screen using one is refused with the reason. Alert rules
  do not take them yet (refused with the reason; OPEN_QUESTIONS LG3-3). Research only - matches, never orders.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.screener import nodes
from app.screener.registry import FUNDAMENTALS_FLAG as FLAG

STALE_DAYS = 456                  # a year and a quarter: an annual report plus its filing window
MEASURES = ("eps_growth", "sales_growth", "earnings_yield", "roc", "graham_number", "ncav")
QUARTER_DAYS = (80, 100)          # consecutive quarters end this many days apart
YEAR_DAYS = (350, 380)            # the same quarter a year before
SHARE_ACTIONS = ("SPLIT", "BONUS")  # the share-count actions B4 adjusts prices for (app/market_lake/adjust.py)


# ------------------------------------------------------------------------------------------------------------- the gate
def flagged_calls(ast: Any) -> List[str]:
    from app.screener.registry import FUNCTIONS
    out: List[str] = []
    if ast is None:
        return out
    for node in nodes.walk(ast):
        spec = FUNCTIONS.get(node.name) if isinstance(node, nodes.Call) else None
        if spec is not None and spec.flag == FLAG and spec.name not in out:
            out.append(spec.name)
    return out


def refusal(names: List[str]) -> str:
    return f"{', '.join(n + '()' for n in names)}: fundamentals screens are not enabled for this organisation (flag {FLAG})"


def alert_refusal(names: List[str]) -> str:
    return f"{', '.join(n + '()' for n in names)}: alert rules do not read fundamentals yet - run the screen instead"


async def enabled(session: AsyncSession, tenant_id: Any) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, FLAG, tenant_id)


async def blocked_calls(session: AsyncSession, tenant_id: Any, ast: Any) -> List[str]:
    names = flagged_calls(ast)
    if not names or await enabled(session, tenant_id):
        return []
    return names


async def attach(session: AsyncSession, universe: List[Any], end: Optional[datetime] = None, *, tenant_id: Optional[int]) -> None:
    """Every symbol's versions known by `end` (now) that the `tenant_id` organisation may read - the shared figures and
    its own, never another organisation's (app/fundamentals/visibility.py) - in one read, onto its SymbolData (an empty
    list when none)."""
    from app.fundamentals import pit
    until = end or datetime.now(timezone.utc)
    found = await pit.load_versions(session, [d.symbol for d in universe], until=until, viewer=tenant_id)
    actions = await share_actions(session, universe, until)
    for data in universe:
        sym = data.symbol.strip().upper()
        data.fundamentals = rebase(found.get(sym, []), actions.get(sym, []))         # per-share figures on the bars' basis


# ------------------------------------------------------------------------------------------------------- the measures
Figure = Tuple[Optional[float], Optional[date]]       # a value and the period end it is current to (None: no value)


def _is_quarter(v: Any) -> bool:
    return str(v.period_type).upper().startswith("Q")


def _quarters(known: Sequence[Any]) -> List[Any]:
    return sorted((v for v in known if _is_quarter(v)), key=lambda v: v.period_end)


def _trailing(known: Sequence[Any], field: str) -> Figure:
    """The last four quarters' sum when they are consecutive and all have the figure, else the latest annual figure."""
    qs = _quarters(known)[-4:]
    if len(qs) == 4 and all(QUARTER_DAYS[0] <= (b.period_end - a.period_end).days <= QUARTER_DAYS[1] for a, b in zip(qs, qs[1:])):
        vals = [_figure(q, field) for q in qs]
        if all(v is not None for v in vals):
            return float(sum(v for v in vals if v is not None)), qs[-1].period_end
    years = sorted((v for v in known if not _is_quarter(v)), key=lambda v: v.period_end)
    if not years:
        return None, None
    x = _figure(years[-1], field)
    return (None, None) if x is None else (x, years[-1].period_end)


def _figure(v: Any, field: str) -> Optional[float]:
    x = v.get(field)
    if x is None and field == "ebit":                                   # EBIT not reported: EBITDA - depreciation
        e, d = v.get("ebitda"), v.get("depreciation")
        x = None if e is None or d is None else e - d
    return None if x is None or not math.isfinite(x) else float(x)


def _balance(known: Sequence[Any], field: str) -> Figure:
    """The latest period (by end date) that reports a balance-sheet figure."""
    for v in sorted(known, key=lambda v: v.period_end, reverse=True):
        x = _figure(v, field)
        if x is not None:
            return x, v.period_end
    return None, None


def _yoy(known: Sequence[Any], field: str) -> Figure:
    qs = _quarters(known)
    if not qs:
        return None, None
    last = qs[-1]
    prev = next((q for q in reversed(qs[:-1]) if YEAR_DAYS[0] <= (last.period_end - q.period_end).days <= YEAR_DAYS[1]), None)
    a, b = (None, None) if prev is None else (_figure(last, field), _figure(prev, field))
    if a is None or b is None or b <= 0:
        return None, None
    return (a - b) / b * 100.0, last.period_end


def _oldest(*figs: Figure) -> Optional[date]:
    """The end a measure is current to: the oldest of its inputs' (a measure is as stale as its stalest input)."""
    ends = [e for _, e in figs if e is not None]
    return min(ends) if ends else None


def state(known: Sequence[Any]) -> Dict[str, Any]:
    """The price-free parts of every measure from the versions known at one time; `ends` maps each measure to the
    period end its own inputs are current to (its staleness is judged on those periods, not on the newest one)."""
    shares, equity, assets = _balance(known, "shares_outstanding"), _balance(known, "shareholders_equity"), _balance(known, "total_assets")
    current_assets, current_liab = _balance(known, "current_assets"), _balance(known, "current_liabilities")
    cash, debt = _balance(known, "cash_and_equivalents"), _balance(known, "total_debt")
    ebit, eps = _trailing(known, "ebit"), _trailing(known, "eps")
    eps_growth, sales_growth = _yoy(known, "eps"), _yoy(known, "revenue")
    cash_v, debt_v = cash[0] or 0.0, debt[0] or 0.0
    capital = None if assets[0] is None or current_liab[0] is None else assets[0] - current_liab[0] - cash_v
    bvps = None if equity[0] is None or not shares[0] else equity[0] / shares[0]
    ncav = (None if None in (current_assets[0], assets[0], equity[0]) or not shares[0]
            else (current_assets[0] - (assets[0] - equity[0])) / shares[0])           # type: ignore[operator]
    e, b = eps[0], bvps
    return {"eps_growth": eps_growth[0], "sales_growth": sales_growth[0], "ebit": ebit[0], "shares": shares[0], "net_debt": debt_v - cash_v,
            "roc": None if ebit[0] is None or not capital or capital <= 0 else ebit[0] / capital * 100.0,
            "graham_number": math.sqrt(22.5 * e * b) if e is not None and b is not None and e > 0 and b > 0 else None,
            "ncav": ncav,
            "ends": {"eps_growth": eps_growth[1], "sales_growth": sales_growth[1],
                     "earnings_yield": _oldest(ebit, shares, cash, debt),
                     "roc": _oldest(ebit, assets, current_liab, cash),
                     "graham_number": _oldest(eps, equity, shares),
                     "ncav": _oldest(current_assets, assets, equity, shares)}}


# ------------------------------------------------------------------------------------- per-share figures on the bars' basis
def share_factor(actions: Sequence[Any], after: date) -> float:
    """The product of ratio_old / ratio_new over the splits and bonuses with an ex-date after `after` (the B4 factor)."""
    factor = 1.0
    for a in actions:
        if a.action in SHARE_ACTIONS and a.ex_date > after and a.ratio_new and a.ratio_old:
            factor *= float(a.ratio_old) / float(a.ratio_new)
    return factor


def rebase(versions: Sequence[Any], actions: Sequence[Any]) -> List[Any]:
    """The versions with their per-share figures on the bars' basis. The screen's bars are split-adjusted to the run (the
    brokers' history and the lake's adjusted view, B4): every close before a split or bonus is scaled by its factor. A
    report's EPS and share count are as of its period end, so a split after that end is applied to them too - EPS times
    the factor, shares divided by it - and close x shares, EPS against the close and NCAV / Graham number per share
    compare like with like on every bar (a split between the period end and the bar no longer halves or doubles them)."""
    from dataclasses import replace
    if not actions:
        return list(versions)
    out = []
    for v in versions:
        k = share_factor(actions, v.period_end)
        if k == 1.0:
            out.append(v)
            continue
        figs = dict(v.figures)
        if figs.get("eps") is not None:
            figs["eps"] = float(figs["eps"]) * k
        if figs.get("shares_outstanding") is not None:
            figs["shares_outstanding"] = float(figs["shares_outstanding"]) / k
        out.append(replace(v, figures=figs))
    return out


async def share_actions(session: AsyncSession, universe: Sequence[Any], end: datetime) -> Dict[str, List[Any]]:
    """The splits and bonuses known by `end` per symbol (each (ex-date, action)'s latest version), in one read - the
    shared exchange corporate-action table, on the symbol's own exchange (NSE when none)."""
    from sqlalchemy import select

    from app.db.models import MdCorporateActionRecord
    want = {(d.symbol.strip().upper(), (getattr(d, "exchange", None) or "NSE").upper()) for d in universe}
    if not want:
        return {}
    rows = await session.scalars(select(MdCorporateActionRecord).where(
        MdCorporateActionRecord.symbol.in_(sorted({s for s, _ in want})), MdCorporateActionRecord.action.in_(SHARE_ACTIONS),
        MdCorporateActionRecord.ingested_at <= end))
    latest: Dict[Any, Any] = {}
    for r in rows:
        if (r.symbol.upper(), r.exchange.upper()) not in want:
            continue
        key = (r.symbol.upper(), r.exchange.upper(), r.ex_date, r.action)
        if key not in latest or r.version > latest[key].version:
            latest[key] = r
    out: Dict[str, List[Any]] = {}
    for (sym, _ex, _d, _a), r in sorted(latest.items(), key=lambda kv: kv[0][2]):
        out.setdefault(sym, []).append(r)
    return out


def series(data: Any, f: pd.DataFrame, tf: str, measure: str) -> pd.Series:
    """One measure on every bar of `f`: the versions known by the bar's close, its own close for price-based measures;
    NaN where the periods the measure itself reads ended more than STALE_DAYS before the bar."""
    from app.fundamentals.pit import latest_per_period
    from app.screener.runtime import ScreenRuntimeError
    from app.screener.zones import _closes_ns
    if measure not in MEASURES:
        raise ScreenRuntimeError(f"unknown fundamentals measure {measure!r}")
    versions = getattr(data, "fundamentals", None)
    if versions is None:
        raise ScreenRuntimeError(f"{data.symbol}: fundamentals were not loaded for this run")
    out = np.full(len(f), np.nan)
    if not len(f) or not versions:
        return pd.Series(out, index=f.index, dtype=float)
    closes_ns = _closes_ns(f.index, tf)
    close = f["close"].astype(float).to_numpy()
    known_ns = np.array([pd.Timestamp(v.known_from).value for v in versions], dtype="int64")
    points = np.unique(known_ns)
    seg = np.searchsorted(points, closes_ns, side="right") - 1        # the last change point at or before each close
    for s in np.unique(seg[seg >= 0]):
        known = latest_per_period(v for v, k in zip(versions, known_ns) if k <= points[s])     # own chain over shared
        if not known:
            continue
        st = state(known)
        current_to = st["ends"][measure]
        if current_to is None:
            continue
        rows = np.flatnonzero(seg == s)
        rows = rows[closes_ns[rows] <= pd.Timestamp(current_to + timedelta(days=STALE_DAYS), tz="UTC").value]
        if measure in ("eps_growth", "sales_growth", "roc", "graham_number", "ncav"):
            value = st[measure]
            if value is not None:
                out[rows] = value
        elif st["ebit"] is not None and st["shares"]:
            ev = close[rows] * st["shares"] + (st["net_debt"] or 0.0)
            with np.errstate(divide="ignore", invalid="ignore"):
                out[rows] = np.where(ev > 0, st["ebit"] / ev * 100.0, np.nan)
    return pd.Series(out, index=f.index, dtype=float)


__all__ = ["FLAG", "MEASURES", "STALE_DAYS", "flagged_calls", "refusal", "alert_refusal", "enabled", "blocked_calls", "attach", "state",
           "share_factor", "rebase", "share_actions", "series"]
