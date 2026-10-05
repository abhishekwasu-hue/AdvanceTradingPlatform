"""Phase BC: a deterministic market sentiment score for Indian equities, -100 (risk-off) to +100
(risk-on), from market data only - no model, no social media, every input a number the trader can
check on their own screen:

* PCR and the day's OI change from the NIFTY option chain (`option_chain/analysis.py`),
* India VIX level and its day change (the market-memory cue),
* breadth: advances vs declines across the index heavyweights, through the tenant's own quotes,
* the global mood from the free overnight cues (`ai/global_cues.mood`),
* FII/DII net flows - behind a provider seam that stays **off** until a source with clear terms is
  configured (`FII_DII_SOURCE`), so the component is simply missing and the weights renormalise.

Weights live in `DEFAULT_WEIGHTS` and can be overridden per deployment with the `SENTIMENT_WEIGHTS`
environment variable (JSON). `news_score` is a separate -100..+100 read of the last day's feed
items (Phase BB classification) and is reported next to the market score, never mixed into it.
The score is background for the Copilot's plan and brief (and the Phase BD thesis); it is never a
signal input, an order or a risk check on its own.
"""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import global_cues
from app.ai.interview import tr
from app.db.models import MarketSnapshotRecord
from app.instruments.master import underlying_of
from app.news_feed.classify import HEAVYWEIGHTS
from app.option_chain.analysis import analyze_option_chain

logger = logging.getLogger(__name__)

KIND = "SENTIMENT"
SYMBOL = "MARKET"
DEFAULT_WEIGHTS: Dict[str, float] = {"pcr": 0.25, "vix": 0.20, "breadth": 0.20, "global": 0.20, "fii_dii": 0.15}
COMPONENTS = tuple(DEFAULT_WEIGHTS)
RISK_ON, RISK_OFF = 25.0, -25.0
BREADTH_SYMBOLS = tuple(sorted(HEAVYWEIGHTS))      # the index heavyweights (one quote call, well inside the broker budget)
CHAIN_UNDERLYING = "NIFTY 50"
NEWS_HOURS = 24
DIRECTION_SIGN = {"BULLISH": 1.0, "BEARISH": -1.0, "NEUTRAL": 0.0}


def _clamp(value: float, lo: float = -100.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(value)))


def weights() -> Dict[str, float]:
    """DEFAULT_WEIGHTS, with SENTIMENT_WEIGHTS (JSON, e.g. {"vix": 0.3}) overriding named keys."""
    out = dict(DEFAULT_WEIGHTS)
    raw = os.environ.get("SENTIMENT_WEIGHTS", "").strip()
    if raw:
        try:
            override = json.loads(raw)
            for key, value in (override or {}).items():
                if key in out and isinstance(value, (int, float)) and value >= 0:
                    out[key] = float(value)
        except ValueError:
            logger.warning("SENTIMENT_WEIGHTS is not valid JSON - defaults in use")
    return out


# --- component scorers (each -100..+100, or None when the input is missing) ------------------------
def pcr_score(pcr: Optional[float], call_oi_change: Optional[float] = None, put_oi_change: Optional[float] = None) -> Optional[float]:
    """PCR 1.0 -> 0; 1.5 -> +100; 0.5 -> -100 (put writing = support, call writing = resistance),
    tilted up to +/-20 by which side added more open interest today."""
    if pcr is None or pcr <= 0:
        return None
    score = _clamp((pcr - 1.0) * 200.0)
    if call_oi_change is not None and put_oi_change is not None and (abs(call_oi_change) + abs(put_oi_change)) > 0:
        tilt = (put_oi_change - call_oi_change) / (abs(call_oi_change) + abs(put_oi_change))
        score = _clamp(score + 20.0 * tilt)
    return round(score, 1)


def vix_score(vix: Optional[float], change_pct: Optional[float] = None) -> Optional[float]:
    """VIX 12 -> +50, 16 -> 0, 20 -> -50 (linear, capped at 24/8); a rising VIX today costs up to 30 more."""
    if vix is None or vix <= 0:
        return None
    level = _clamp(-(vix - 16.0) * 12.5)
    move = _clamp(-(change_pct or 0.0) * 3.0, -30.0, 30.0)
    return round(_clamp(level + move), 1)


def breadth_score(advances: int, declines: int) -> Optional[float]:
    total = advances + declines
    if total <= 0:
        return None
    return round(_clamp((advances - declines) / total * 100.0), 1)


def global_score(mood: Optional[dict]) -> Optional[float]:
    """global_cues.mood() scores about +/-1.5 at full risk-on/off; mapped onto +/-100."""
    if not mood or mood.get("score") is None:
        return None
    return round(_clamp(float(mood["score"]) / 1.5 * 100.0), 1)


def fii_dii_score(net_fii_crore: Optional[float]) -> Optional[float]:
    """Net FII cash flow in crore: +2,000 -> about +76, -2,000 -> about -76 (tanh)."""
    if net_fii_crore is None:
        return None
    return round(_clamp(math.tanh(net_fii_crore / 2000.0) * 100.0), 1)


def news_score(items: List[dict]) -> Optional[dict]:
    """Separate read of the day's feed: severity- and confidence-weighted direction, -100..+100."""
    weight_sum = signed = 0.0
    counted = 0
    for item in items:
        c = item.get("classification") or {}
        sev = float(c.get("severity") or 0)
        if sev <= 0:
            continue
        conf = float(c.get("confidence") or 0.5)
        sign = DIRECTION_SIGN.get(str(c.get("direction", "NEUTRAL")).upper(), 0.0)
        weight_sum += sev * conf
        signed += sev * conf * sign
        counted += 1
    if not counted or weight_sum <= 0:
        return None
    score = round(_clamp(signed / weight_sum * 100.0), 1)
    return {"score": score, "items": counted, "label": label_for(score)}


def label_for(score: float) -> str:
    return "RISK_ON" if score >= RISK_ON else "RISK_OFF" if score <= RISK_OFF else "NEUTRAL"


def compute(components: Dict[str, Optional[float]], raw: Optional[Dict[str, object]] = None, weights_in: Optional[Dict[str, float]] = None) -> dict:
    """Weighted average of the components that are present; the weights renormalise over them.
    `raw` carries the inputs behind each component (shown to the trader, never hidden)."""
    w = weights_in or weights()
    present = {k: v for k, v in components.items() if v is not None and k in w and w[k] > 0}
    total_w = sum(w[k] for k in present)
    score = round(sum(w[k] * present[k] for k in present) / total_w, 1) if total_w > 0 else 0.0
    out_components = {}
    for key in COMPONENTS:
        out_components[key] = {"score": components.get(key), "weight": round(w.get(key, 0.0) / total_w, 3) if (key in present and total_w > 0) else 0.0,
                               "configured_weight": w.get(key, 0.0), "input": (raw or {}).get(key)}
    return {"score": score, "label": label_for(score) if present else "UNKNOWN", "components": out_components,
            "missing": [k for k in COMPONENTS if k not in present], "coverage": round(total_w / sum(w.values()), 2) if sum(w.values()) else 0.0}


# --- reading the inputs ----------------------------------------------------------------------------
def fii_dii_provider() -> Optional[dict]:
    """The FII/DII seam. Off unless FII_DII_SOURCE names a configured source; today none is wired
    (NSE's daily FII/DII page has no stated API terms; NSDL's FPI monitor is monthly). Returns
    {"net_fii_crore": float, "net_dii_crore": float, "as_of": iso} or None."""
    return None if not os.environ.get("FII_DII_SOURCE", "").strip() else None


async def read_chain(broker, underlying: str = CHAIN_UNDERLYING) -> Optional[dict]:
    try:
        chain = await broker.get_option_chain(underlying_of(underlying))
    except Exception as exc:  # noqa: BLE001 - a chain outage leaves the component missing
        logger.info("Sentiment: option chain unavailable: %s", exc)
        return None
    if chain is None or not chain.rows:
        return None
    analysis = analyze_option_chain(chain)
    return {"pcr": analysis.pcr, "call_oi_change": analysis.total_call_oi_change, "put_oi_change": analysis.total_put_oi_change,
            "max_pain": analysis.max_pain, "expiry": analysis.expiry, "bias": analysis.bias.value if hasattr(analysis.bias, "value") else str(analysis.bias)}


async def read_breadth(broker, symbols=BREADTH_SYMBOLS) -> Optional[dict]:
    try:
        quotes = await broker.get_quote(list(symbols))
    except Exception as exc:  # noqa: BLE001
        logger.info("Sentiment: heavyweight quotes unavailable: %s", exc)
        return None
    advances = declines = unchanged = 0
    for q in (quotes or {}).values():
        close = float(getattr(q, "close", 0.0) or 0.0)
        ltp = float(getattr(q, "ltp", 0.0) or 0.0)
        if close <= 0 or ltp <= 0:
            continue
        if ltp > close:
            advances += 1
        elif ltp < close:
            declines += 1
        else:
            unchanged += 1
    if advances + declines + unchanged == 0:
        return None
    return {"advances": advances, "declines": declines, "unchanged": unchanged, "universe": len(symbols)}


def from_memory(memory: dict, now: Optional[datetime] = None) -> Dict[str, object]:
    """VIX and the global mood from the market memory the worker already keeps."""
    now = now or datetime.now(timezone.utc)
    vix_row = next((c for c in memory.get("cues", []) if c.get("symbol") == "INDIA VIX"), None)
    vix = {"vix": float(vix_row["last_price"]), "change_pct": vix_row.get("change_pct")} if vix_row and vix_row.get("last_price") else None
    mood = global_cues.mood(memory.get("globals", []), now) if memory.get("globals") else None
    return {"vix": vix, "global": mood}


async def capture(session: AsyncSession, tenant_id: int, broker, memory: dict, *, now: Optional[datetime] = None, news_items: Optional[List[dict]] = None,
                  commit: bool = True) -> dict:
    """One sentiment read for one organisation through its own broker; stored as a SENTIMENT
    snapshot in the market memory. Returns the result dict (also the stored payload)."""
    now = now or datetime.now(timezone.utc)
    chain = await read_chain(broker)
    breadth = await read_breadth(broker)
    mem = from_memory(memory, now)
    fii = fii_dii_provider()
    components = {
        "pcr": pcr_score(chain["pcr"], chain["call_oi_change"], chain["put_oi_change"]) if chain else None,
        "vix": vix_score(mem["vix"]["vix"], mem["vix"].get("change_pct")) if mem["vix"] else None,
        "breadth": breadth_score(breadth["advances"], breadth["declines"]) if breadth else None,
        "global": global_score(mem["global"]),
        "fii_dii": fii_dii_score(fii["net_fii_crore"]) if fii else None,
    }
    raw = {"pcr": chain, "vix": mem["vix"], "breadth": breadth, "global": ({"score": mem["global"]["score"], "label": mem["global"]["label"]} if mem["global"] else None),
           "fii_dii": fii if fii else {"status": "off", "reason": "no source with clear terms configured (FII_DII_SOURCE)"}}
    result = compute(components, raw)
    result["news"] = news_score(news_items or [])
    result["as_of"] = now.isoformat()
    result["source"] = getattr(broker, "name", "broker")
    session.add(MarketSnapshotRecord(tenant_id=tenant_id, kind=KIND, symbol=SYMBOL, exchange="NSE", timeframe="day", source=result["source"][:40],
                                     last_price=result["score"], change_pct=None, bias=result["label"], payload_json=json.dumps(result, default=str), captured_at=now))
    if commit:
        await session.commit()
    return result


# --- words ----------------------------------------------------------------------------------------
LABEL_WORDS = {"RISK_ON": ("risk-on", "तेजीचा कल (risk-on)"), "RISK_OFF": ("risk-off", "सावधगिरीचा कल (risk-off)"), "NEUTRAL": ("neutral", "तटस्थ"), "UNKNOWN": ("not read yet", "अजून वाचलेला नाही")}
COMPONENT_WORDS = {"pcr": ("option chain PCR/OI", "option chain PCR/OI"), "vix": ("India VIX", "India VIX"), "breadth": ("heavyweight breadth", "मोठ्या शेअर्सची रुंदी"),
                   "global": ("global cues", "जागतिक संकेत"), "fii_dii": ("FII/DII flows", "FII/DII प्रवाह")}


def view(lang: str, result: Optional[dict]) -> List[str]:
    """Plain sentences for the plan and the brief: the score, what drove it, what is missing."""
    if not result or result.get("label") in (None, "UNKNOWN"):
        return [tr(lang, "Market sentiment: not read yet (needs a broker session for the option chain and quotes).",
                   "Market sentiment: अजून वाचलेला नाही (option chain आणि quotes साठी broker session लागतो).")]
    label = LABEL_WORDS.get(result["label"], LABEL_WORDS["UNKNOWN"])
    parts = []
    for key, comp in result.get("components", {}).items():
        if comp.get("score") is None:
            continue
        parts.append(f"{tr(lang, *COMPONENT_WORDS[key])} {comp['score']:+.0f}")
    lines = [tr(lang, f"Market sentiment {result['score']:+.0f}: {label[0]}" + (f" ({', '.join(parts)})." if parts else "."),
                f"Market sentiment {result['score']:+.0f}: {label[1]}" + (f" ({', '.join(parts)})." if parts else "."))]
    missing = [tr(lang, *COMPONENT_WORDS[k]) for k in result.get("missing", []) if k in COMPONENT_WORDS]
    if missing:
        lines.append(tr(lang, f"Not in the score (no data): {', '.join(missing)}; the weights were renormalised.",
                        f"Score मध्ये नाही (डेटा नाही): {', '.join(missing)}; weights पुन्हा समायोजित केले."))
    news = result.get("news")
    if news:
        words = LABEL_WORDS.get(news["label"], LABEL_WORDS["NEUTRAL"])
        lines.append(tr(lang, f"News score {news['score']:+.0f} ({news['items']} feed item(s), unverified): {words[0]} - reported separately, not mixed into the market score.",
                        f"News score {news['score']:+.0f} ({news['items']} feed बातम्या, unverified): {words[1]} - वेगळा दाखवला, market score मध्ये मिसळलेला नाही."))
    lines.append(tr(lang, "Sentiment is background for the plan, never a signal or an order on its own.",
                    "Sentiment हे plan साठी पार्श्वभूमी आहे; स्वतःहून signal किंवा order कधीच नाही."))
    return lines
