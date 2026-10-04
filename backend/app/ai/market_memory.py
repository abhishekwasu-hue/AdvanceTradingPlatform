"""Phase AR: the Copilot's market memory - the shopkeeper who knows his stock before you walk in.

Every 15 minutes while NSE is open (and once after the close) the trading worker reads the market
for each tenant that uses the Copilot, through the tenant's own broker session, and stores it:

* **Symbol snapshots** - for each watched symbol (NIFTY 50, NIFTY BANK, the symbols in the
  tenant's trader profiles and active deployments; at most `MAX_WATCH`): the same market read the
  strategy interview does (`interview.analyse_market`: today's move and VWAP, regime on 5-minute
  and the higher timeframe, market structure, nearest support / resistance, volatility, bias).
* **Market cues** - India VIX (fear gauge) and the main indices' day change, from daily candles.
  These come from the broker the tenant already has; global markets (GIFT Nifty, US indices,
  crude, dollar) need an outside data feed and are not here yet.

The memory makes the plan faster to trust and richer: it can say how the bias moved over the last
days and what the fear gauge says, not only what the last few hours of candles show. Reads are
`latest()` (newest snapshot per symbol, newest cues, one row per day of history) and
`describe()` (sentences for a plan, in the trader's language).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import interview as iv
from app.ai.interview import tr
from app.core.models import OHLCVBar, bars_to_dataframe
from app.db.models import MarketSnapshotRecord, StrategyDeploymentRecord, TraderProfileRecord
from app.instruments import master as instrument_master
from app.market_data.calendar import IST

logger = logging.getLogger(__name__)

DEFAULT_WATCH = ("NIFTY 50", "NIFTY BANK")
CUES: Tuple[Tuple[str, str], ...] = (("INDIA VIX", "NSE"), ("NIFTY 50", "NSE"), ("NIFTY BANK", "NSE"), ("SENSEX", "BSE"))
MAX_WATCH = 8
INTERVAL_MINUTES = 15
INTRADAY_LOOKBACK_DAYS = 10
DAILY_LOOKBACK_DAYS = 15
HISTORY_DAYS = 5
FRESH_MINUTES = 45


def exchange_for(symbol: str) -> str:
    return instrument_master.INDEX_EXCHANGE.get(instrument_master.underlying_of(symbol), "NSE")


async def watchlist(session: AsyncSession, tenant_id: int) -> List[str]:
    """The default indices, then the symbols this tenant's traders asked about, then the ones it
    deploys - de-duplicated, at most MAX_WATCH."""
    out: List[str] = list(DEFAULT_WATCH)
    for answers_json in await session.scalars(select(TraderProfileRecord.answers_json).where(TraderProfileRecord.tenant_id == tenant_id)):
        try:
            sym = (json.loads(answers_json or "{}").get("symbol") or "").strip().upper()
        except ValueError:
            sym = ""
        if sym:
            out.append(sym)
    for sym in await session.scalars(select(StrategyDeploymentRecord.symbol).where(
            StrategyDeploymentRecord.tenant_id == tenant_id, StrategyDeploymentRecord.status == "ACTIVE")):
        out.append((sym or "").strip().upper())
    seen: List[str] = []
    for sym in out:
        if sym and sym not in seen:
            seen.append(sym)
    return seen[:MAX_WATCH]


def _df(bars: Sequence[OHLCVBar]) -> pd.DataFrame:
    df = bars_to_dataframe(list(bars))
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df


def symbol_snapshot(tenant_id: int, symbol: str, exchange: str, bars_1min: Sequence[OHLCVBar], source: str,
                    now: datetime) -> Optional[MarketSnapshotRecord]:
    """The market read of one symbol from 1-minute bars (resampled to 5 minutes); None when there
    is not enough history to read."""
    if not bars_1min:
        return None
    df = iv._frame_at(_df(bars_1min), "1min", "5min")
    if len(df) < iv.MIN_BARS:
        return None
    market = iv.analyse_market(df, "5min", "en")
    return MarketSnapshotRecord(
        tenant_id=tenant_id, kind="SYMBOL", symbol=symbol, exchange=exchange, timeframe="5min", source=source[:40],
        last_price=market["last_price"], change_pct=market["today"]["change_pct"], bias=market["bias"],
        regime=market["regime"]["kind"], higher_regime=market["higher_regime"]["kind"], structure=market["structure"]["trend"],
        payload_json=json.dumps(market, default=str), captured_at=now)


def cue_snapshot(tenant_id: int, symbol: str, exchange: str, day_bars: Sequence[OHLCVBar], source: str,
                 now: datetime) -> Optional[MarketSnapshotRecord]:
    """Last close, change on the day and over five sessions, from daily bars."""
    if len(day_bars) < 2:
        return None
    closes = [float(b.close) for b in day_bars]
    last, prev = closes[-1], closes[-2]
    five = closes[-6] if len(closes) >= 6 else closes[0]
    payload = {"last": round(last, 2), "prev_close": round(prev, 2), "change_pct": round((last / prev - 1) * 100, 2) if prev else 0.0,
               "change_5d_pct": round((last / five - 1) * 100, 2) if five else 0.0, "date": str(day_bars[-1].timestamp)[:10]}
    return MarketSnapshotRecord(tenant_id=tenant_id, kind="CUE", symbol=symbol, exchange=exchange, timeframe="day", source=source[:40],
                                last_price=payload["last"], change_pct=payload["change_pct"], payload_json=json.dumps(payload), captured_at=now)


async def capture(session: AsyncSession, tenant_id: int, market_data_factory, broker, *, now: Optional[datetime] = None,
                  symbols: Optional[List[str]] = None, include_cues: bool = True) -> dict:
    """Reads and stores the watched symbols and the cues through `broker`. One failing symbol never
    stops the others. Returns {"symbols": n, "cues": n, "errors": [...]}."""
    now = now or datetime.now(timezone.utc)
    source = getattr(broker, "name", "broker")
    report = {"symbols": 0, "cues": 0, "errors": []}
    intraday = market_data_factory(broker, lookback_days=INTRADAY_LOOKBACK_DAYS)
    for symbol in symbols if symbols is not None else await watchlist(session, tenant_id):
        exchange = exchange_for(symbol)
        try:
            row = symbol_snapshot(tenant_id, symbol, exchange, await intraday.get_candles(symbol, exchange, "1min", now=now), source, now)
            if row is not None:
                session.add(row)
                report["symbols"] += 1
        except Exception as exc:  # noqa: BLE001 - one symbol's failure must not lose the rest
            logger.info("Market memory: %s failed for tenant %s: %s", symbol, tenant_id, exc)
            report["errors"].append(f"{symbol}: {type(exc).__name__}: {exc}"[:200])
    if include_cues:
        daily = market_data_factory(broker, lookback_days=DAILY_LOOKBACK_DAYS)
        for symbol, exchange in CUES:
            try:
                row = cue_snapshot(tenant_id, symbol, exchange, await daily.get_candles(symbol, exchange, "day", now=now), source, now)
                if row is not None:
                    session.add(row)
                    report["cues"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.info("Market memory cue %s failed for tenant %s: %s", symbol, tenant_id, exc)
                report["errors"].append(f"{symbol}: {type(exc).__name__}: {exc}"[:200])
    await session.commit()
    return report


def _row(r: MarketSnapshotRecord) -> dict:
    try:
        payload = json.loads(r.payload_json or "{}")
    except ValueError:
        payload = {}
    return {"symbol": r.symbol, "exchange": r.exchange, "kind": r.kind, "source": r.source, "last_price": r.last_price,
            "change_pct": r.change_pct, "bias": r.bias, "regime": r.regime, "higher_regime": r.higher_regime,
            "structure": r.structure, "captured_at": _utc(r.captured_at).isoformat() if r.captured_at else None, "payload": payload}


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def latest(session: AsyncSession, tenant_id: int, *, now: Optional[datetime] = None, symbol: Optional[str] = None) -> dict:
    """Newest snapshot per symbol and per cue (last 3 days), and per symbol one row per IST day for
    the last HISTORY_DAYS days (the day's last read) - how the bias moved."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=HISTORY_DAYS + 3)
    query = select(MarketSnapshotRecord).where(MarketSnapshotRecord.tenant_id == tenant_id, MarketSnapshotRecord.captured_at >= since)
    if symbol:
        query = query.where(MarketSnapshotRecord.symbol == symbol.strip().upper())
    rows = list(await session.scalars(query.order_by(MarketSnapshotRecord.captured_at.desc()).limit(5000)))
    symbols: Dict[str, dict] = {}
    cues: Dict[str, dict] = {}
    history: Dict[str, Dict[str, dict]] = {}
    for r in rows:
        captured = _utc(r.captured_at)
        if r.kind == "CUE":
            if r.symbol not in cues and captured >= now - timedelta(days=3):
                cues[r.symbol] = _row(r)
            continue
        if r.symbol not in symbols and captured >= now - timedelta(days=3):
            symbols[r.symbol] = _row(r)
        day = captured.astimezone(IST).date().isoformat()
        per_day = history.setdefault(r.symbol, {})
        if day not in per_day and len(per_day) < HISTORY_DAYS:
            per_day[day] = {"date": day, "bias": r.bias, "regime": r.regime, "last_price": r.last_price, "change_pct": r.change_pct}
    newest = max((s["captured_at"] for s in list(symbols.values()) + list(cues.values())), default=None)
    return {"symbols": list(symbols.values()), "cues": list(cues.values()),
            "history": {k: sorted(v.values(), key=lambda d: d["date"]) for k, v in history.items()}, "updated_at": newest}


def vix_text(lang: str, vix: float) -> str:
    if vix < 12:
        return tr(lang, f"India VIX {vix:.1f}: very calm - small moves, option premiums cheap", f"India VIX {vix:.1f}: खूप शांत - लहान हालचाली, option premium स्वस्त")
    if vix < 16:
        return tr(lang, f"India VIX {vix:.1f}: normal", f"India VIX {vix:.1f}: सामान्य")
    if vix < 20:
        return tr(lang, f"India VIX {vix:.1f}: elevated - wider swings, keep size small", f"India VIX {vix:.1f}: वाढलेला - मोठे चढ-उतार, size लहान ठेवा")
    return tr(lang, f"India VIX {vix:.1f}: high fear - big gaps possible; a beginner should mostly watch", f"India VIX {vix:.1f}: जास्त भीती - मोठे gap शक्य; नवशिक्याने शक्यतो फक्त पाहावे")


def describe(lang: str, memory: dict, symbol: str, now: Optional[datetime] = None) -> List[str]:
    """Sentences for the plan's "market background" section: the cues and how the symbol's bias
    moved over the last days. Empty when the memory has nothing fresh."""
    now = now or datetime.now(timezone.utc)
    lines: List[str] = []
    cues = {c["symbol"]: c for c in memory.get("cues", [])}
    vix = cues.get("INDIA VIX")
    if vix and vix.get("last_price"):
        lines.append(vix_text(lang, float(vix["last_price"])) + tr(lang, f" ({vix['change_pct']:+.1f}% on the day).", f" (दिवसात {vix['change_pct']:+.1f}%)."))
    moves = [f"{name} {c['change_pct']:+.2f}%" for name, c in cues.items() if name != "INDIA VIX" and c.get("change_pct") is not None]
    if moves:
        lines.append(tr(lang, "Last session: ", "मागचे सत्र: ") + ", ".join(moves) + ".")
    hist = memory.get("history", {}).get(symbol.strip().upper(), [])
    if len(hist) >= 2:
        words = {"BULLISH": tr(lang, "up", "तेजी"), "BEARISH": tr(lang, "down", "मंदी"), "NEUTRAL": tr(lang, "neutral", "तटस्थ")}
        trail = " → ".join(f"{d['date'][5:]} {words.get(d['bias'] or '', '-')}" for d in hist)
        lines.append(tr(lang, f"{symbol.upper()} bias over the last days: {trail}.", f"{symbol.upper()} चा गेल्या काही दिवसांचा कल: {trail}."))
        biases = [d["bias"] for d in hist if d["bias"]]
        if len(biases) >= 3 and len(set(biases[-3:])) == 1 and biases[-1] != "NEUTRAL":
            lines.append(tr(lang, "The same bias three sessions running - an established trend; trade with it, not against it.",
                            "सलग तीन सत्रे एकच कल - trend प्रस्थापित आहे; त्याच्या सोबत trade करा, विरुद्ध नाही."))
        elif len(set(biases[-3:])) == 3:
            lines.append(tr(lang, "The bias changed every session lately - a choppy market; smaller size, fewer trades.",
                            "अलीकडे प्रत्येक सत्रात कल बदलला - choppy market; size लहान, trades कमी."))
    if lines and memory.get("updated_at"):
        try:
            age = int((now - datetime.fromisoformat(memory["updated_at"])).total_seconds() // 60)
            lines.append(tr(lang, f"(Market memory updated {age} min ago from your broker.)", f"(Market चा साठा {age} मिनिटांपूर्वी तुमच्या broker कडून अद्ययावत.)"))
        except (TypeError, ValueError):
            pass
    return lines
