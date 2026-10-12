"""U7 LG3: the ScreenQL fundamentals functions - point in time - and their flag gate.

* Data: `app.fundamentals.pit` - the versions of each company's reported figures with the time they became public. A
  bar sees, per period, the latest version known by the bar's CLOSE (`pit.as_of`): never a figure published later,
  never a restatement before it was made (a later version counts only from its ingestion). Price-based measures use
  that bar's own close. So a value at bar t never changes when later bars or later versions are added (tested; Z10-a
  causal cases with a synthetic timeline built from the bars up to each report).
* Measures (NaN where the figures are missing, non-positive where a ratio needs them positive, or stale - the latest
  period ended more than STALE_DAYS before the bar):
    EPSGrowthYoY / SalesGrowthYoY  latest quarter vs the same quarter a year before, % (both quarters known);
    EarningsYield                  trailing EBIT / enterprise value (close x shares + debt - cash), %;
    ReturnOnCapital                trailing EBIT / (total assets - current liabilities - cash), %;
    GrahamNumber                   sqrt(22.5 x trailing EPS x book value per share), a price;
    NCAVPerShare                   (current assets - total liabilities) / shares, a price (liabilities = assets - equity).
  Trailing = the last four quarters when they are consecutive, else the latest annual period.
* Flag `screener_fundamentals` (off by default), the U6 BK1 / Z7a-3 pattern: the functions stay in the one registry;
  while the flag is off the registry endpoint hides them and a screen using one is refused with the reason. Alert rules
  do not take them yet (refused with the reason; OPEN_QUESTIONS LG3-3). Research only - matches, never orders.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.screener import nodes
from app.screener.registry import FUNDAMENTALS_FLAG as FLAG

STALE_DAYS = 456                  # a year and a quarter: an annual report plus its filing window
MEASURES = ("eps_growth", "sales_growth", "earnings_yield", "roc", "graham_number", "ncav")
QUARTER_DAYS = (80, 100)          # consecutive quarters end this many days apart
YEAR_DAYS = (350, 380)            # the same quarter a year before


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


async def attach(session: AsyncSession, universe: List[Any], end: Optional[datetime] = None) -> None:
    """Every symbol's versions known by `end` (now) in one read, onto its SymbolData (an empty list when none)."""
    from app.fundamentals import pit
    found = await pit.load_versions(session, [d.symbol for d in universe], until=end or datetime.now(timezone.utc))
    for data in universe:
        data.fundamentals = found.get(data.symbol.strip().upper(), [])


# ------------------------------------------------------------------------------------------------------- the measures
def _is_quarter(v: Any) -> bool:
    return str(v.period_type).upper().startswith("Q")


def _quarters(known: Sequence[Any]) -> List[Any]:
    return sorted((v for v in known if _is_quarter(v)), key=lambda v: v.period_end)


def _trailing(known: Sequence[Any], field: str) -> Optional[float]:
    """The last four quarters' sum when they are consecutive and all have the figure, else the latest annual figure."""
    qs = _quarters(known)[-4:]
    if len(qs) == 4 and all(QUARTER_DAYS[0] <= (b.period_end - a.period_end).days <= QUARTER_DAYS[1] for a, b in zip(qs, qs[1:])):
        vals = [_figure(q, field) for q in qs]
        if all(v is not None for v in vals):
            return float(sum(v for v in vals if v is not None))
    years = sorted((v for v in known if not _is_quarter(v)), key=lambda v: v.period_end)
    return _figure(years[-1], field) if years else None


def _figure(v: Any, field: str) -> Optional[float]:
    x = v.get(field)
    if x is None and field == "ebit":                                   # EBIT not reported: EBITDA - depreciation
        e, d = v.get("ebitda"), v.get("depreciation")
        x = None if e is None or d is None else e - d
    return None if x is None or not math.isfinite(x) else float(x)


def _balance(known: Sequence[Any], field: str) -> Optional[float]:
    """The latest period (by end date) that reports a balance-sheet figure."""
    for v in sorted(known, key=lambda v: v.period_end, reverse=True):
        x = _figure(v, field)
        if x is not None:
            return x
    return None


def _yoy(known: Sequence[Any], field: str) -> Optional[float]:
    qs = _quarters(known)
    if not qs:
        return None
    last = qs[-1]
    prev = next((q for q in reversed(qs[:-1]) if YEAR_DAYS[0] <= (last.period_end - q.period_end).days <= YEAR_DAYS[1]), None)
    a, b = (None, None) if prev is None else (_figure(last, field), _figure(prev, field))
    if a is None or b is None or b <= 0:
        return None
    return (a - b) / b * 100.0


def state(known: Sequence[Any]) -> Dict[str, Optional[float]]:
    """The price-free parts of every measure from the versions known at one time."""
    shares = _balance(known, "shares_outstanding")
    equity = _balance(known, "shareholders_equity")
    assets = _balance(known, "total_assets")
    current_assets = _balance(known, "current_assets")
    current_liab = _balance(known, "current_liabilities")
    cash = _balance(known, "cash_and_equivalents") or 0.0
    debt = _balance(known, "total_debt") or 0.0
    ebit = _trailing(known, "ebit")
    eps = _trailing(known, "eps")
    capital = None if assets is None or current_liab is None else assets - current_liab - cash
    bvps = None if equity is None or not shares else equity / shares
    ncav = None if None in (current_assets, assets, equity) or not shares else (current_assets - (assets - equity)) / shares  # type: ignore[operator]
    return {"eps_growth": _yoy(known, "eps"), "sales_growth": _yoy(known, "revenue"), "ebit": ebit, "shares": shares, "net_debt": debt - cash,
            "roc": None if ebit is None or not capital or capital <= 0 else ebit / capital * 100.0,
            "graham_number": math.sqrt(22.5 * eps * bvps) if eps is not None and bvps is not None and eps > 0 and bvps > 0 else None,
            "ncav": ncav}


def series(data: Any, f: pd.DataFrame, tf: str, measure: str) -> pd.Series:
    """One measure on every bar of `f`: the versions known by the bar's close, its own close for price-based measures."""
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
        known_at = [v for v, k in zip(versions, known_ns) if k <= points[s]]
        best: Dict[Any, Any] = {}
        for v in known_at:
            key = (v.period_type, v.period_label)
            if key not in best or v.version > best[key].version:
                best[key] = v
        known = list(best.values())
        if not known:
            continue
        st = state(known)
        rows = np.flatnonzero(seg == s)
        latest_end = max(v.period_end for v in known)
        fresh = closes_ns[rows] <= pd.Timestamp(latest_end + timedelta(days=STALE_DAYS), tz="UTC").value
        rows = rows[fresh]
        if measure in ("eps_growth", "sales_growth", "roc", "graham_number", "ncav"):
            value = st[measure]
            if value is not None:
                out[rows] = value
        elif st["ebit"] is not None and st["shares"]:
            ev = close[rows] * st["shares"] + (st["net_debt"] or 0.0)
            with np.errstate(divide="ignore", invalid="ignore"):
                out[rows] = np.where(ev > 0, st["ebit"] / ev * 100.0, np.nan)
    return pd.Series(out, index=f.index, dtype=float)


__all__ = ["FLAG", "MEASURES", "STALE_DAYS", "flagged_calls", "refusal", "alert_refusal", "enabled", "blocked_calls", "attach", "state", "series"]
