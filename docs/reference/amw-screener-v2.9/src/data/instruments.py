"""Instrument master: one row per ticker (AMW/Yahoo convention, e.g. RELIANCE.NS) with NSE symbol, name, ISIN, series,
industry and the lists it came from. Filled from the official NSE lists the app loads (index constituents and
EQUITY_L.csv); nothing is invented. Used to resolve user input (symbol, ticker, ISIN or company name) to a ticker."""
from __future__ import annotations

import io
import re

import pandas as pd
from sqlalchemy import func, or_

from ..storage.db import instruments
from .universe import to_yahoo

ISIN_RE = re.compile(r"^IN[A-Z0-9]{9}[0-9]$")


def from_csv(text: str) -> list[dict]:
    """Parse NSE CSVs including ISIN / series when present (EQUITY_L: 'ISIN NUMBER', index files: 'ISIN Code')."""
    df = pd.read_csv(io.StringIO(text))
    cols = {c.strip().lower(): c for c in df.columns}
    sym = cols.get("symbol")
    if sym is None:
        raise ValueError("CSV has no Symbol column")
    get = lambda *names: next((cols[n] for n in names if n in cols), None)  # noqa: E731
    name, isin, ser, ind = get("company name", "name of company"), get("isin number", "isin code", "isin"), get("series"), get("industry")
    out = []
    for _, r in df.iterrows():
        t = to_yahoo(str(r[sym]).strip().upper())
        if not t:
            continue
        i = str(r[isin]).strip().upper() if isin else ""
        out.append({"ticker": t, "symbol": str(r[sym]).strip().upper(), "exchange": "NSE",
                    "name": str(r[name]).strip() if name else "", "isin": i if ISIN_RE.match(i) else None,
                    "series": str(r[ser]).strip() if ser else None, "industry": str(r[ind]).strip() if ind else None})
    return out


def upsert_universe(store, uni_df: pd.DataFrame, source: str) -> int:
    rows = [{"ticker": r.ticker, "symbol": r.symbol, "exchange": "NSE", "name": getattr(r, "company", "") or None,
             "industry": getattr(r, "industry", "") or None} for r in uni_df.itertuples(index=False)]
    return store.instruments_upsert(rows, source)


def resolve(store, text: str) -> dict:
    """{'ticker' | None, 'match': how, 'candidates': [...]} - ambiguous names are NOT guessed."""
    q = (text or "").strip()
    if not q:
        return {"ticker": None, "match": "empty", "candidates": []}
    u = q.upper()
    t = u if u.endswith(".NS") or u.startswith("^") else f"{u}.NS"
    exact = store.instruments_find(instruments.c.ticker == t, 1)
    if exact:
        return {"ticker": exact[0]["ticker"], "match": "ticker/symbol", "candidates": exact}
    if ISIN_RE.match(u):
        r = store.instruments_find(instruments.c.isin == u, 2)
        return {"ticker": r[0]["ticker"] if len(r) == 1 else None, "match": "isin" if r else "unknown isin", "candidates": r}
    r = store.instruments_find(func.lower(instruments.c.name).like(f"{q.lower()}%"), 10)
    if len(r) == 1:
        return {"ticker": r[0]["ticker"], "match": "company name (unique prefix)", "candidates": r}
    return {"ticker": None, "match": "ambiguous" if r else "not found", "candidates": r}


def search(store, q: str, limit: int = 20) -> list[dict]:
    s = f"%{(q or '').strip().lower()}%"
    return store.instruments_find(or_(func.lower(instruments.c.symbol).like(s), func.lower(instruments.c.name).like(s),
                                      func.lower(instruments.c.isin).like(s)), limit)
