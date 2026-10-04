"""Global cues for the Copilot's market memory: what the world did overnight, read before the
Indian open and through the day - US index futures and indices, Asia, Brent crude, gold, the dollar
index, USD/INR and the US 10-year yield.

The data comes from free public sources with no key: Yahoo Finance's chart endpoint first, Stooq's
daily CSV when Yahoo does not answer for a symbol. Both are delayed (about 15 minutes or more),
unofficial and can change without notice, so this is background for the plan and the guide - never
an input to a signal, an order or a risk check. One fetch serves every tenant (`CACHE_SECONDS`); a
symbol that fails is simply missing from the read.

GIFT Nifty has no free, stable public source; the US index futures, which trade almost round the
clock, are the overnight mood it mostly reflects, so they stand in for it.

`GLOBAL_CUES_ENABLED=false` turns the fetch off (the tests do; so can an operator without internet).
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import httpx

from app.ai.interview import tr
from app.db.models import MarketSnapshotRecord

logger = logging.getLogger(__name__)

KIND = "GLOBAL"
CACHE_SECONDS = 600
TIMEOUT_SECONDS = 8.0
STALE_DAYS = 4          # older than this (a long weekend is fine) and the quote is not shown
SOURCE_NOTE_EN = "free public data (Yahoo Finance / Stooq), delayed about 15 minutes"
SOURCE_NOTE_MR = "मोफत सार्वजनिक माहिती (Yahoo Finance / Stooq), सुमारे 15 मिनिटे उशिरा"
GIFT_NOTE_EN = "GIFT Nifty has no free reliable source; the US index futures stand in for the overnight mood."
GIFT_NOTE_MR = "GIFT Nifty साठी मोफत भरवशाचा source नाही; रात्रीच्या मूडसाठी US index futures पाहतो."
YAHOO_URLS = ("https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d",
              "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d")
STOOQ_URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"
HEADERS = {"User-Agent": "Mozilla/5.0 (AMW Algorithmic Trading; market background)", "Accept": "application/json,text/csv,*/*"}


@dataclass(frozen=True)
class Market:
    key: str
    en: str
    mr: str
    group: str        # US | ASIA | COMMODITY | FX | RATES
    yahoo: str
    stooq: str
    effect: int       # +1: up is good for Indian equities, -1: up is bad, 0: shown only
    threshold: float  # a move of this % (or more) counts fully in the mood


MARKETS: Tuple[Market, ...] = (
    Market("SP500_FUT", "S&P 500 futures", "S&P 500 futures", "US", "ES=F", "es.f", 1, 0.5),
    Market("NASDAQ_FUT", "Nasdaq 100 futures", "Nasdaq 100 futures", "US", "NQ=F", "nq.f", 1, 0.6),
    Market("SP500", "S&P 500", "S&P 500", "US", "^GSPC", "^spx", 1, 0.5),
    Market("NASDAQ", "Nasdaq Composite", "Nasdaq Composite", "US", "^IXIC", "^ndq", 1, 0.6),
    Market("NIKKEI", "Nikkei 225 (Japan)", "Nikkei 225 (जपान)", "ASIA", "^N225", "^nkx", 1, 0.6),
    Market("HANG_SENG", "Hang Seng (Hong Kong)", "Hang Seng (हाँगकाँग)", "ASIA", "^HSI", "^hsi", 1, 0.7),
    Market("BRENT", "Brent crude", "Brent कच्चे तेल", "COMMODITY", "BZ=F", "cb.f", -1, 1.5),
    Market("GOLD", "Gold", "सोने", "COMMODITY", "GC=F", "gc.f", 0, 1.0),
    Market("DXY", "US dollar index", "US dollar index", "FX", "DX-Y.NYB", "dx.f", -1, 0.4),
    Market("USDINR", "USD/INR", "USD/INR (रुपया)", "FX", "INR=X", "usdinr", -1, 0.3),
    Market("US10Y", "US 10-year yield", "US 10-वर्ष yield", "RATES", "^TNX", "10usy.b", -1, 2.0),
)
BY_KEY: Dict[str, Market] = {m.key: m for m in MARKETS}

_cache: Dict[str, object] = {"at": 0.0, "quotes": [], "errors": []}


def enabled() -> bool:
    return os.environ.get("GLOBAL_CUES_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")


def _quote(market: Market, last: float, prev: float, first: Optional[float], as_of: Optional[datetime], source: str) -> dict:
    return {"key": market.key, "label": market.en, "group": market.group, "last": round(last, 4), "prev_close": round(prev, 4),
            "change_pct": round((last / prev - 1) * 100, 2) if prev else 0.0,
            "change_5d_pct": round((last / first - 1) * 100, 2) if first else None,
            "as_of": as_of.isoformat() if as_of else None, "source": source}


def parse_yahoo(market: Market, payload: dict) -> Optional[dict]:
    """Last price, previous session close and the 5-session change from a v8 chart response
    (range=5d, interval=1d). The last daily bar is the current (or last) session."""
    try:
        result = (payload.get("chart") or {}).get("result") or []
        if not result:
            return None
        res = result[0]
        meta = res.get("meta") or {}
        stamps = res.get("timestamp") or []
        closes = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
        series = [(t, float(c)) for t, c in zip(stamps, closes) if c is not None]
        if len(series) < 2:
            return None
        last = meta.get("regularMarketPrice")
        last = float(last) if last is not None else series[-1][1]
        prev = series[-2][1]
        when = meta.get("regularMarketTime") or series[-1][0]
        as_of = datetime.fromtimestamp(int(when), tz=timezone.utc) if when else None
        return _quote(market, last, prev, series[0][1], as_of, "yahoo")
    except (TypeError, ValueError, KeyError, IndexError, AttributeError):
        return None


def parse_stooq(market: Market, text: str) -> Optional[dict]:
    """The last two closes (and five sessions back) from Stooq's daily CSV."""
    try:
        rows = [r for r in csv.DictReader(io.StringIO(text)) if r.get("Close") not in (None, "", "N/D")]
        if len(rows) < 2:
            return None
        rows = rows[-6:]
        last, prev = float(rows[-1]["Close"]), float(rows[-2]["Close"])
        as_of = datetime.fromisoformat(rows[-1]["Date"]).replace(tzinfo=timezone.utc)
        return _quote(market, last, prev, float(rows[0]["Close"]), as_of, "stooq")
    except (TypeError, ValueError, KeyError):
        return None


async def _fetch_one(client: httpx.AsyncClient, market: Market) -> Tuple[Optional[dict], Optional[str]]:
    problem = None
    for url in YAHOO_URLS:
        try:
            resp = await client.get(url.format(symbol=market.yahoo), headers=HEADERS)
            if resp.status_code == 200:
                quote = parse_yahoo(market, resp.json())
                if quote:
                    return quote, None
                problem = "yahoo: no data"
            else:
                problem = f"yahoo: HTTP {resp.status_code}"
        except (httpx.HTTPError, ValueError) as exc:
            problem = f"yahoo: {type(exc).__name__}"
    try:
        resp = await client.get(STOOQ_URL.format(symbol=market.stooq), headers=HEADERS)
        if resp.status_code == 200:
            quote = parse_stooq(market, resp.text)
            if quote:
                return quote, None
            problem = f"{problem}; stooq: no data"
        else:
            problem = f"{problem}; stooq: HTTP {resp.status_code}"
    except httpx.HTTPError as exc:
        problem = f"{problem}; stooq: {type(exc).__name__}"
    return None, f"{market.key}: {problem}"


async def fetch_all(client: Optional[httpx.AsyncClient] = None, *, force: bool = False) -> Tuple[List[dict], List[str]]:
    """Every market's quote, from the shared cache when it is younger than CACHE_SECONDS. Returns
    (quotes, errors); both empty when the feature is off."""
    if not enabled():
        return [], []
    if not force and time.monotonic() - float(_cache["at"]) < CACHE_SECONDS and _cache["quotes"]:
        return list(_cache["quotes"]), list(_cache["errors"])
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=True)
    try:
        results = await asyncio.gather(*(_fetch_one(client, m) for m in MARKETS))
    finally:
        if own:
            await client.aclose()
    quotes = [q for q, _ in results if q]
    errors = [e for _, e in results if e]
    if errors:
        logger.info("Global cues: %d of %d markets unavailable: %s", len(errors), len(MARKETS), "; ".join(errors)[:500])
    _cache.update(at=time.monotonic(), quotes=quotes, errors=errors)
    return quotes, errors


def reset_cache() -> None:
    _cache.update(at=0.0, quotes=[], errors=[])


def snapshot_rows(tenant_id: int, quotes: List[dict], now: datetime) -> List[MarketSnapshotRecord]:
    return [MarketSnapshotRecord(tenant_id=tenant_id, kind=KIND, symbol=q["key"], exchange="GLOBAL", timeframe="day", source=q["source"][:40],
                                 last_price=q["last"], change_pct=q["change_pct"], payload_json=json.dumps(q), captured_at=now)
            for q in quotes]


# --- reading the cues ------------------------------------------------------------------------------

def _fresh(item: dict, now: datetime) -> bool:
    as_of = (item.get("payload") or {}).get("as_of")
    if not as_of:
        return True
    try:
        when = datetime.fromisoformat(as_of)
    except ValueError:
        return True
    return now - (when if when.tzinfo else when.replace(tzinfo=timezone.utc)) <= timedelta(days=STALE_DAYS)


def mood(globals_: List[dict], now: Optional[datetime] = None) -> dict:
    """A risk-on / risk-off read for Indian equities: each scored market contributes its move
    against its threshold (capped at one), signed by how it usually affects India. US futures stand
    in for the US cash indices when both are present (they are the newer read)."""
    now = now or datetime.now(timezone.utc)
    by_key = {g["symbol"]: g for g in globals_ if g.get("change_pct") is not None and _fresh(g, now)}
    skip = {"SP500", "NASDAQ"} if "SP500_FUT" in by_key or "NASDAQ_FUT" in by_key else set()
    score, parts = 0.0, []
    for key, item in by_key.items():
        m = BY_KEY.get(key)
        if m is None or m.effect == 0 or key in skip:
            continue
        contribution = max(-1.0, min(1.0, float(item["change_pct"]) / m.threshold)) * m.effect
        score += contribution
        parts.append((abs(contribution), key))
    label = "POSITIVE" if score >= 1.5 else "NEGATIVE" if score <= -1.5 else "MIXED"
    order = {m.key: i for i, m in enumerate(MARKETS)}
    return {"label": label, "score": round(score, 2), "drivers": [k for _, k in sorted(parts, key=lambda p: (-p[0], order[p[1]]))[:3]]}


def _pct(v: float) -> str:
    return f"{v:+.2f}%"


def view(lang: str, globals_: List[dict], now: Optional[datetime] = None) -> List[str]:
    """Plain sentences on what the world did and what it usually means for an Indian trader - the
    likely effect, never an instruction. Empty when there is no fresh global read."""
    now = now or datetime.now(timezone.utc)
    by_key = {g["symbol"]: g for g in globals_ if g.get("change_pct") is not None and _fresh(g, now)}
    if not by_key:
        return []
    m = mood(globals_, now)
    words = {"POSITIVE": tr(lang, "positive", "सकारात्मक"), "NEGATIVE": tr(lang, "negative", "नकारात्मक"), "MIXED": tr(lang, "mixed", "संमिश्र")}
    drivers = ", ".join(f"{tr(lang, BY_KEY[k].en, BY_KEY[k].mr)} {_pct(by_key[k]['change_pct'])}" for k in m["drivers"])
    lines = [tr(lang, f"Global cues: {words[m['label']]}" + (f" ({drivers})." if drivers else "."),
                f"जागतिक संकेत: {words[m['label']]}" + (f" ({drivers})." if drivers else "."))]

    us = by_key.get("SP500_FUT") or by_key.get("SP500")
    if us and abs(us["change_pct"]) >= 0.5:
        if us["change_pct"] < 0:
            lines.append(tr(lang, "US futures are down: NIFTY often opens lower on such days - let the first 15 minutes settle before judging the trend.",
                            "US futures खाली: अशा दिवशी NIFTY बहुतेक खाली उघडतो - trend ठरवण्याआधी पहिली 15 मिनिटे स्थिर होऊ द्या."))
        else:
            lines.append(tr(lang, "US futures are up: a firmer open is likely; a gap-up can also fade, so wait for the opening range.",
                            "US futures वर: उघडताना मजबुती शक्य; पण gap-up ओसरूही शकतो, म्हणून opening range ची वाट पाहा."))
    brent = by_key.get("BRENT")
    if brent and abs(brent["change_pct"]) >= 1.5:
        if brent["change_pct"] > 0:
            lines.append(tr(lang, f"Brent crude {_pct(brent['change_pct'])}: India imports most of its oil - pressure on oil marketers, paints, airlines and on the rupee.",
                            f"Brent कच्चे तेल {_pct(brent['change_pct'])}: भारत बहुतेक तेल आयात करतो - OMC, paint, airline शेअर आणि रुपयावर दबाव."))
        else:
            lines.append(tr(lang, f"Brent crude {_pct(brent['change_pct'])}: cheaper oil usually helps India - oil marketers, paints and airlines, and the rupee.",
                            f"Brent कच्चे तेल {_pct(brent['change_pct'])}: स्वस्त तेल भारतासाठी सहसा चांगले - OMC, paint, airline आणि रुपयाला आधार."))
    inr = by_key.get("USDINR")
    if inr and abs(inr["change_pct"]) >= 0.3:
        if inr["change_pct"] > 0:
            lines.append(tr(lang, f"The rupee weakened (USD/INR {_pct(inr['change_pct'])}): foreign investors tend to sell; IT exporters gain.",
                            f"रुपया कमजोर (USD/INR {_pct(inr['change_pct'])}): परदेशी गुंतवणूकदार (FII) विक्री करण्याची शक्यता; IT निर्यातदारांना फायदा."))
        else:
            lines.append(tr(lang, f"The rupee strengthened (USD/INR {_pct(inr['change_pct'])}): supportive for foreign buying.",
                            f"रुपया मजबूत (USD/INR {_pct(inr['change_pct'])}): परदेशी खरेदीसाठी पोषक."))
    dxy = by_key.get("DXY")
    if dxy and dxy["change_pct"] >= 0.4:
        lines.append(tr(lang, f"Dollar index {_pct(dxy['change_pct'])}: a strong dollar pulls money out of emerging markets like India.",
                        f"Dollar index {_pct(dxy['change_pct'])}: मजबूत डॉलर भारतासारख्या emerging markets मधून पैसा बाहेर ओढतो."))
    return lines


__all__ = ["MARKETS", "BY_KEY", "enabled", "fetch_all", "parse_yahoo", "parse_stooq", "snapshot_rows", "mood", "view", "reset_cache"]
