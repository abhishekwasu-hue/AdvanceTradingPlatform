"""Universe loader for Nifty 50 / 200 / 500, NSE equities list and custom watchlists.

Order: fresh cache -> local official CSV -> official URLs -> stale cache -> labelled FALLBACK.
The fallback list is never presented as the requested index.
"""
from __future__ import annotations

import io
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger(__name__)

FALLBACK_SYMBOLS = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "BHARTIARTL", "ITC", "SBIN", "LT",
    "HINDUNILVR", "KOTAKBANK", "AXISBANK", "BAJFINANCE", "MARUTI", "ASIANPAINT", "SUNPHARMA",
    "TITAN", "ULTRACEMCO", "NTPC", "POWERGRID", "ONGC", "NESTLEIND", "WIPRO", "HCLTECH",
    "TECHM", "M&M", "TATASTEEL", "JSWSTEEL", "COALINDIA", "ADANIENT", "ADANIPORTS",
    "BAJAJFINSV", "BAJAJ-AUTO", "HEROMOTOCO", "EICHERMOT", "DRREDDY", "CIPLA", "GRASIM",
    "BRITANNIA", "HINDALCO", "INDUSINDBK", "SBILIFE", "HDFCLIFE", "APOLLOHOSP",
    "TATACONSUM", "SHRIRAMFIN", "TRENT", "BEL",
]
FALLBACK_LABEL = "FALLBACK large-cap list (NOT the requested index)"
MIN_ROWS = {"Nifty 50": 45, "Nifty 200": 150, "Nifty 500": 400, "NSE Equities (EQUITY_L)": 1000}

_SYMBOL_RE = re.compile(r"^[A-Z0-9&\-]{1,20}$")


@dataclass
class Universe:
    name: str
    symbols: pd.DataFrame            # symbol, ticker, company, industry
    source: str
    retrieved_at: str
    is_fallback: bool
    errors: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.symbols)


def to_yahoo(symbol: str) -> str | None:
    s = str(symbol).strip().upper()
    if s.endswith(".NS"):
        s = s[:-3]
    return f"{s}.NS" if _SYMBOL_RE.match(s) else None


def parse_constituents(csv_text: str, min_rows: int = 50) -> pd.DataFrame:
    """Parse NSE index constituent CSV (Symbol/Company Name/Industry/Series) or EQUITY_L.csv (SYMBOL/NAME OF COMPANY/SERIES)."""
    df = pd.read_csv(io.StringIO(csv_text))
    cols = {c.strip().lower(): c for c in df.columns}
    sym = cols.get("symbol")
    if sym is None:
        raise ValueError("CSV has no Symbol column - not an NSE list")
    comp = cols.get("company name") or cols.get("name of company")
    ind = cols.get("industry")
    ser = cols.get("series")
    if ser is not None:
        df = df[df[ser].astype(str).str.strip().isin(["EQ", "BE", "BZ", "nan"])]
    out = pd.DataFrame({
        "symbol": df[sym].astype(str).str.strip().str.upper(),
        "company": df[comp].astype(str).str.strip() if comp else "",
        "industry": df[ind].astype(str).str.strip() if ind else "",
    })
    out["ticker"] = out["symbol"].map(to_yahoo)
    out = out.dropna(subset=["ticker"]).drop_duplicates("ticker").reset_index(drop=True)
    if len(out) < min_rows:
        raise ValueError(f"only {len(out)} valid symbols parsed (< {min_rows}) - file looks incomplete")
    return out[["symbol", "ticker", "company", "industry"]]


def custom_universe(text: str, name: str = "Custom watchlist") -> Universe:
    errs, rows = [], []
    for raw in re.split(r"[,\s;]+", text or ""):
        if not raw:
            continue
        t = to_yahoo(raw)
        if t is None:
            errs.append(f"Invalid symbol: {raw}")
            continue
        rows.append({"symbol": t[:-3], "ticker": t, "company": "", "industry": ""})
    df = pd.DataFrame(rows, columns=["symbol", "ticker", "company", "industry"]).drop_duplicates("ticker")
    return Universe(name, df.reset_index(drop=True), name, _now(), False, errs)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def load_universe(name: str, cfg: dict, root: Path, force_refresh: bool = False,
                  getter=requests.get) -> Universe:
    src_cfg = cfg["universe"]["sources"].get(name)
    if src_cfg is None:
        raise ValueError(f"unknown universe {name}")
    cache_dir = root / cfg["data"]["cache_dir"]
    cache_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower())
    cache_csv, cache_meta = cache_dir / f"universe_{slug}.csv", cache_dir / f"universe_{slug}.json"
    errors: list[str] = []
    min_rows = MIN_ROWS.get(name, 20)

    def from_cache(max_age_h):
        if not (cache_csv.exists() and cache_meta.exists()):
            return None
        meta = json.loads(cache_meta.read_text(encoding="utf-8"))
        age_h = (time.time() - meta["ts"]) / 3600
        if max_age_h is not None and age_h > max_age_h:
            return None
        return Universe(name, pd.read_csv(cache_csv).fillna(""), f"{meta['source']} (cached {age_h:.1f} h)",
                        meta["retrieved_at"], False, [])

    if not force_refresh and (u := from_cache(cfg["universe"]["cache_hours"])) is not None:
        return u

    def save(df, src):
        df.to_csv(cache_csv, index=False)
        ts = _now()
        cache_meta.write_text(json.dumps({"source": src, "retrieved_at": ts, "ts": time.time()}), encoding="utf-8")
        return Universe(name, df, src, ts, False, errors)

    local = root / src_cfg["local_csv"]
    if local.exists():
        try:
            return save(parse_constituents(local.read_text(encoding="utf-8-sig"), min_rows), f"Local file {local.name}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"Local CSV error: {e}")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "text/csv,*/*"}
    for url in src_cfg["urls"]:
        try:
            r = getter(url, headers=headers, timeout=cfg["data"]["timeout"])
            r.raise_for_status()
            return save(parse_constituents(r.text, min_rows), url)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{url}: {type(e).__name__}: {str(e)[:120]}")
    if (u := from_cache(None)) is not None:
        u.errors = errors + ["Online sources failed - using older cached list"]
        return u
    df = pd.DataFrame({"symbol": FALLBACK_SYMBOLS})
    df["ticker"] = df["symbol"].map(to_yahoo)
    df["company"], df["industry"] = "", ""
    return Universe(name, df, FALLBACK_LABEL, _now(), True,
                    errors + [f"Official {name} list unavailable; FALLBACK list in use"])
