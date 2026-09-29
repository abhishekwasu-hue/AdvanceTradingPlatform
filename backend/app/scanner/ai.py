"""Phase Y (master prompt V4.2): the AI scanner - plain language in, the same deterministic
scanner out.

Two calls, both through the tenant's LLM provider seam (`app/ai/settings.py::provider_for`;
the rule-based provider when none is configured or the plan has no AI features):

* `plan_scan` - "NIFTY stocks in an uptrend near support with RSI above 55 and PCR over 1.2"
  becomes a `ScanPlan`: indicator conditions in the Strategy Builder's `Condition` DSL,
  structure filters and option filters. The plan is validated against the real enums (an
  unknown indicator or filter is dropped with a warning, never guessed), shown to the user and
  run by the ordinary `run_scanner` only when they press Run. Without an LLM the plan comes from
  the NLU parser plus keyword rules.
* `read_scan` - the matches of a scan (symbol, close, labels the deterministic engine produced)
  plus the regime the platform's own classifier reads on each symbol's candles are handed to the
  model, which ranks them and says why, what would invalidate the read and what the sensible
  next step is (backtest, paper). Raw candles never go to the model - only what the engine
  concluded. The answer is validated and carries a fixed disclaimer; nothing here is an order
  (master prompt rule 3: scanner != order) and nothing here is executed.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import settings as ai_settings
from app.ai.providers import LLMProvider, ProviderError, RuleBasedProvider
from app.ai.regime import REGIMES, classify_regime
from app.audit.log import write_audit_log
from app.billing.service import meter
from app.core.models import bars_to_dataframe
from app.db.models import Tenant, User
from app.observability.metrics import AI_PROVIDER_CALLS
from app.scanner.models import (
    OptionFilter, OptionFilterType, ScannerMatch, ScannerRequest, ScannerResult, StructureFilter, StructureFilterType,
)
from app.strategy_engine.declarative import Condition
from app.strategy_engine.nlu_parser import parse_strategy_description

logger = logging.getLogger(__name__)

PROMPT_VERSION = "scanner-v1.0"
INDICATORS = ("EMA", "SMA", "RSI", "ADX", "PLUS_DI", "MINUS_DI", "ATR", "SUPERTREND", "CLOSE", "OPEN", "HIGH", "LOW")
OPERATORS = ("GT", "LT", "GTE", "LTE", "CROSSES_ABOVE", "CROSSES_BELOW")
TIMEFRAMES = ("1min", "3min", "5min", "15min", "30min", "60min", "1d")
MAX_MATCHES_TO_READ = 40
DISCLAIMER = ("An AI read of what the deterministic scanner matched. It is analysis, not advice and not a signal: "
              "nothing here is executed. Backtest a strategy built from these conditions and paper-trade it before any live deployment.")


# --- models -----------------------------------------------------------------------------------

class ScanPlan(BaseModel):
    """What the scanner will run - the user sees and edits this before pressing Run."""
    timeframe: str = "5min"
    symbols: List[str] = Field(default_factory=list)          # symbols named in the request, if any
    indicator_conditions: List[Condition] = Field(default_factory=list)
    structure_filters: List[StructureFilter] = Field(default_factory=list)
    option_filters: List[OptionFilter] = Field(default_factory=list)
    explanation: str = ""
    warnings: List[str] = Field(default_factory=list)
    provider: str = "rule_based"
    model: str = ""
    prompt_version: str = PROMPT_VERSION

    @property
    def filter_count(self) -> int:
        return len(self.indicator_conditions) + len(self.structure_filters) + len(self.option_filters)


class RankedSymbol(BaseModel):
    symbol: str
    score: int = Field(ge=0, le=100)
    thesis: str
    risks: str = ""
    next_step: str = ""
    regime: Optional[str] = None


class ScanRead(BaseModel):
    summary: str
    ranked: List[RankedSymbol] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    provider: str = "rule_based"
    model: str = ""
    prompt_version: str = PROMPT_VERSION
    disclaimer: str = DISCLAIMER


# --- prompts ----------------------------------------------------------------------------------

def plan_system_prompt(language: str = "en") -> str:
    return (
        "You translate a trader's plain-language screening request into a JSON scan plan for a deterministic market scanner. "
        "You never invent data, never give trade instructions and never add filters the trader did not ask for.\n\n"
        "Answer with ONE JSON object only, no prose, no markdown fences:\n"
        "{\n"
        '  "timeframe": one of ' + json.dumps(list(TIMEFRAMES)) + ",\n"
        '  "symbols": [uppercase NSE symbols or index names the request names, else []],\n'
        '  "indicator_conditions": [ {"left": OPERAND, "operator": OP, "right": OPERAND} ],\n'
        '  "structure_filters": [ {"filter_type": STRUCTURE, "tolerance_pct": number} ],\n'
        '  "option_filters": [ {"filter_type": OPTION, "operator": "GT|LT|GTE|LTE", "value": number, "tolerance_pct": number} ],\n'
        '  "explanation": one or two sentences in the trader\'s language (' + language + ") saying what the plan screens for,\n"
        '  "warnings": [anything in the request the scanner cannot express]\n'
        "}\n"
        'OPERAND is {"type": "indicator", "indicator": NAME, "period": int} or {"type": "value", "value": number}; '
        "NAME is one of " + json.dumps(list(INDICATORS)) + " (CLOSE/OPEN/HIGH/LOW take no period). OP is one of " + json.dumps(list(OPERATORS)) + ".\n"
        "STRUCTURE is one of " + json.dumps([s.value for s in StructureFilterType]) + " (tolerance_pct only matters for NEAR_SUPPORT/NEAR_RESISTANCE).\n"
        "OPTION is one of " + json.dumps([o.value for o in OptionFilterType]) + " (operator/value only for PCR; tolerance_pct only for NEAR_MAX_PAIN).\n"
        "Every filter is AND-combined. Volume, fundamentals, news and anything not listed above cannot be screened: put it in warnings."
    )


def read_system_prompt(language: str = "en") -> str:
    return (
        "You are a market-structure analyst reviewing the output of a deterministic scanner. You receive, per symbol, the "
        "filters it cleared (exact labels), its last close and the regime the platform's own classifier read (with the numbers "
        "behind it). You have no other data: do not invent prices, volumes, news or targets, and do not tell the trader to buy or sell.\n\n"
        "Rank the symbols by how well the evidence supports a trade idea. Answer with ONE JSON object only:\n"
        "{\n"
        '  "summary": two or three sentences in the trader\'s language (' + language + ") about the set as a whole,\n"
        '  "ranked": [ {"symbol": str, "score": 0-100, "thesis": why it ranks here from the labels and regime, '
        '"risks": what would invalidate the read, "next_step": what to verify before acting} ],\n'
        '  "warnings": [caveats: thin evidence, conflicting labels, unknown regime]\n'
        "}\n"
        "Score conservatively: a symbol whose regime contradicts its labels (e.g. bullish structure in TRENDING_DOWN) scores low; "
        "UNKNOWN regime caps the score at 60. The next step is always to backtest and paper-trade, never to place an order."
    )


def _extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object in the answer")
    return json.loads(text[start:end + 1])


# --- plan parsing -----------------------------------------------------------------------------

def parse_plan(raw: dict) -> ScanPlan:
    """Tolerant: each filter is validated on its own; a bad one is dropped with a warning."""
    warnings: List[str] = [str(w) for w in (raw.get("warnings") or []) if str(w).strip()][:10]
    timeframe = str(raw.get("timeframe") or "5min")
    if timeframe not in TIMEFRAMES:
        warnings.append(f"timeframe '{timeframe}' not supported - using 5min")
        timeframe = "5min"
    symbols = [str(s).strip().upper() for s in (raw.get("symbols") or []) if str(s).strip()][:50]
    conditions: List[Condition] = []
    for item in (raw.get("indicator_conditions") or [])[:12]:
        try:
            conditions.append(Condition.model_validate(item))
        except (ValidationError, TypeError) as exc:
            warnings.append(f"indicator condition dropped: {_first_error(exc)}")
    structures: List[StructureFilter] = []
    for item in (raw.get("structure_filters") or [])[:12]:
        try:
            structures.append(StructureFilter.model_validate(item))
        except (ValidationError, TypeError) as exc:
            warnings.append(f"structure filter dropped: {_first_error(exc)}")
    options: List[OptionFilter] = []
    for item in (raw.get("option_filters") or [])[:12]:
        try:
            flt = OptionFilter.model_validate(item)
            if flt.filter_type == OptionFilterType.PCR and (flt.operator is None or flt.value is None):
                warnings.append("PCR filter dropped: needs an operator and a value")
                continue
            options.append(flt)
        except (ValidationError, TypeError) as exc:
            warnings.append(f"option filter dropped: {_first_error(exc)}")
    plan = ScanPlan(timeframe=timeframe, symbols=symbols, indicator_conditions=conditions, structure_filters=structures,
                    option_filters=options, explanation=str(raw.get("explanation") or "")[:1000], warnings=warnings)
    if plan.filter_count == 0:
        plan.warnings.append("No filter could be built from the request - add conditions by hand")
    return plan


def _first_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        return f"{'.'.join(str(p) for p in first.get('loc', ()))}: {first.get('msg')}"[:120]
    return str(exc)[:120]


_STRUCTURE_KEYWORDS: Tuple[Tuple[str, StructureFilterType], ...] = (
    (r"\bdown ?trend|\bfalling\b|\bbearish trend", StructureFilterType.TREND_DOWNTREND),
    (r"\bup ?trend|\brising\b|\bbullish trend|\btrending up", StructureFilterType.TREND_UPTREND),
    (r"\brang(e|ing)\b|\bsideways\b|\bconsolidat", StructureFilterType.TREND_RANGE),
    (r"\bbearish (break|bos)|\bbreak(down| of structure down)", StructureFilterType.BOS_BEARISH),
    (r"\bbullish (break|bos)|\bbreak ?out\b|\bbreak of structure", StructureFilterType.BOS_BULLISH),
    (r"\bbearish choch|\bchange of character (down|bearish)", StructureFilterType.CHOCH_BEARISH),
    (r"\bbullish choch|\bchoch\b|\bchange of character", StructureFilterType.CHOCH_BULLISH),
    (r"\bbearish (candle|pattern)|\bengulfing bear|\bshooting star", StructureFilterType.PATTERN_BEARISH),
    (r"\bbullish (candle|pattern)|\bengulfing\b|\bhammer\b", StructureFilterType.PATTERN_BULLISH),
    (r"\bnear resistance|\bat resistance|\bresistance\b", StructureFilterType.NEAR_RESISTANCE),
    (r"\bnear support|\bat support|\bsupport\b", StructureFilterType.NEAR_SUPPORT),
)
_PCR = re.compile(r"\bpcr\b\s*(?:is\s*)?(above|over|greater than|>=?|below|under|less than|<=?)\s*([0-9]*\.?[0-9]+)", re.I)
_OP_WORDS = {"above": "GT", "over": "GT", "greater than": "GT", ">": "GT", ">=": "GTE", "below": "LT", "under": "LT", "less than": "LT", "<": "LT", "<=": "LTE"}
_INDEX_WORDS = {"nifty": "NIFTY 50", "banknifty": "NIFTY BANK", "bank nifty": "NIFTY BANK", "finnifty": "NIFTY FIN SERVICE", "sensex": "SENSEX"}


_COMPARISON = re.compile(r"^(.+?)\s+(crosses above|crosses below|greater than or equal to|less than or equal to|greater than|less than|"
                         r"above|over|below|under|>=|<=|>|<)\s+(.+)$", re.I)
_COMPARISON_OPS = {"crosses above": "CROSSES_ABOVE", "crosses below": "CROSSES_BELOW", "greater than or equal to": "GTE", "less than or equal to": "LTE",
                   "greater than": "GT", "less than": "LT", "above": "GT", "over": "GT", "below": "LT", "under": "LT", ">=": "GTE", "<=": "LTE", ">": "GT", "<": "LT"}
_CLAUSE_SPLIT = re.compile(r"[.;\n,]+|\band\b|\bwith\b", re.I)


def _bare_conditions(text: str) -> List[Condition]:
    """"RSI(14) above 60", "EMA 20 > EMA 50": comparisons stated without the Strategy Builder's
    "go long when" phrasing, parsed with the same operand grammar."""
    from app.strategy_engine.nlu_parser import _parse_operand
    out: List[Condition] = []
    for clause in _CLAUSE_SPLIT.split(text):
        m = _COMPARISON.match(clause.strip().rstrip("."))
        if not m:
            continue
        left, right = _parse_operand(re.sub(r"^(when|where|if)\s+", "", m.group(1).strip(), flags=re.I)), _parse_operand(m.group(3).strip())
        if left is None or right is None or (left.type == "value" and right.type == "value"):
            continue
        try:
            out.append(Condition(left=left, operator=_COMPARISON_OPS[m.group(2).lower()], right=right))
        except (ValidationError, KeyError):
            continue
    return out


def rule_based_plan(text: str) -> ScanPlan:
    """No model: the Strategy Builder's NLU parser for indicator conditions (plus bare comparisons
    the parser's "go long when" grammar would skip), keyword rules for structure and option
    filters. Deterministic, so the same words always give the same plan."""
    lowered = text.lower()
    parsed = parse_strategy_description(text, name="scan")
    conditions = list(parsed.config.long_conditions) + list(parsed.config.short_conditions)
    seen_labels = {c.label() for c in conditions}
    for extra in _bare_conditions(text):
        if extra.label() not in seen_labels:
            conditions.append(extra)
            seen_labels.add(extra.label())
    warnings = list(parsed.warnings)
    structures: List[StructureFilter] = []
    seen = set()
    for pattern, kind in _STRUCTURE_KEYWORDS:
        if re.search(pattern, lowered) and kind not in seen:
            # "near support" and "resistance" both matching from one word would double up.
            if kind in (StructureFilterType.NEAR_SUPPORT, StructureFilterType.NEAR_RESISTANCE) and any(
                    s.filter_type in (StructureFilterType.NEAR_SUPPORT, StructureFilterType.NEAR_RESISTANCE) for s in structures):
                continue
            structures.append(StructureFilter(filter_type=kind))
            seen.add(kind)
    options: List[OptionFilter] = []
    m = _PCR.search(text)
    if m:
        options.append(OptionFilter(filter_type=OptionFilterType.PCR, operator=_OP_WORDS.get(m.group(1).lower(), "GT"), value=float(m.group(2))))
    if re.search(r"\b(bullish|positive) (option|oi|chain)|\bput writing", lowered):
        options.append(OptionFilter(filter_type=OptionFilterType.BIAS_BULLISH))
    if re.search(r"\b(bearish|negative) (option|oi|chain)|\bcall writing", lowered):
        options.append(OptionFilter(filter_type=OptionFilterType.BIAS_BEARISH))
    if "max pain" in lowered:
        options.append(OptionFilter(filter_type=OptionFilterType.NEAR_MAX_PAIN))
    symbols = [name for word, name in _INDEX_WORDS.items() if re.search(rf"\b{word}\b", lowered)]
    symbols += [s for s in re.findall(r"\b[A-Z]{3,12}\b", text) if s not in {"RSI", "EMA", "SMA", "ADX", "ATR", "PCR", "AND", "THE", "NSE", "BSE", "OI"} and s not in symbols]
    # Longest first, so "15min" is not read as "5min"; "1 min" and "1min" both count.
    compact = lowered.replace(" ", "")
    tf = next((t for t in sorted(TIMEFRAMES, key=len, reverse=True) if re.search(rf"(?<!\d){re.escape(t)}", compact)), parsed.config.timeframe or "5min")
    parts = [c.label() for c in conditions] + [s.label() for s in structures] + [o.label() for o in options]
    plan = ScanPlan(timeframe=tf, symbols=list(dict.fromkeys(symbols))[:50], indicator_conditions=conditions, structure_filters=structures,
                    option_filters=options, explanation=("Deterministic parse (no external model): screens for " + "; ".join(parts)) if parts else
                    "Deterministic parse (no external model): nothing recognised", warnings=warnings, provider="rule_based", model=RuleBasedProvider.model)
    if plan.filter_count == 0:
        plan.warnings.append("No filter could be built from the request - add conditions by hand")
    return plan


async def plan_scan(session: AsyncSession, tenant: Tenant, user: User, text: str, *, language: str = "en",
                    provider: Optional[LLMProvider] = None) -> ScanPlan:
    provider = provider or await ai_settings.provider_for(session, tenant)
    if isinstance(provider, RuleBasedProvider):
        plan = rule_based_plan(text)
    else:
        try:
            raw = await provider.complete(plan_system_prompt(language), f"REQUEST:\n{text.strip()}", max_tokens=1500)
            AI_PROVIDER_CALLS.labels(provider=provider.name, outcome="ok").inc()
            plan = parse_plan(_extract_json(raw))
            plan.provider, plan.model = provider.name, provider.model
            await ai_settings.mark_used(session, tenant.id)
        except (ProviderError, ValueError, json.JSONDecodeError) as exc:
            AI_PROVIDER_CALLS.labels(provider=provider.name, outcome="error").inc()
            await ai_settings.mark_used(session, tenant.id, error=str(exc))
            plan = rule_based_plan(text)
            plan.warnings.insert(0, f"{provider.name} did not answer usably ({str(exc)[:120]}); deterministic parse used instead")
    await write_audit_log(session, tenant.id, user.id, "ai_scan_planned",
                          f"{plan.provider}/{plan.model}: {plan.filter_count} filters from {len(text)} chars")
    await meter(session, tenant.id, "ai_scanner", 1, source="api", metadata={"kind": "plan", "provider": plan.provider}, commit=False)
    await session.commit()
    return plan


# --- reading a result -------------------------------------------------------------------------

def _regimes_for(request: ScannerRequest, matches: List[ScannerMatch]) -> Dict[str, dict]:
    by_symbol = {s.symbol: s for s in request.symbols}
    out: Dict[str, dict] = {}
    for match in matches:
        entry = by_symbol.get(match.symbol)
        if entry is None or not entry.candles:
            out[match.symbol] = {"kind": "UNKNOWN", "confidence": 0.0, "reasons": ["no candles supplied"]}
            continue
        try:
            regime = classify_regime(bars_to_dataframe(entry.candles))
            out[match.symbol] = {"kind": regime.kind, "confidence": round(regime.confidence, 2), "reasons": regime.reasons[:4]}
        except Exception as exc:  # noqa: BLE001 - a regime read failing is a caveat, not a failure
            out[match.symbol] = {"kind": "UNKNOWN", "confidence": 0.0, "reasons": [f"regime not computed: {exc}"[:120]]}
    return out


def rule_based_read(matches: List[ScannerMatch], regimes: Dict[str, dict]) -> ScanRead:
    """No model: a transparent score from how many filter categories matched and whether the
    regime agrees with the labels' direction."""
    ranked: List[RankedSymbol] = []
    warnings: List[str] = []
    for m in matches:
        labels = m.matched_indicator_labels + m.matched_structure_labels + m.matched_option_labels
        regime = regimes.get(m.symbol, {"kind": "UNKNOWN", "confidence": 0.0, "reasons": []})
        kind = regime["kind"]
        text = " ".join(labels).lower()
        bullish = any(w in text for w in ("bullish", "uptrend", "support", "put writing"))
        bearish = any(w in text for w in ("bearish", "downtrend", "resistance", "call writing"))
        score = 40 + 10 * sum(bool(x) for x in (m.matched_indicator_labels, m.matched_structure_labels, m.matched_option_labels))
        agree = (bullish and kind == "TRENDING_UP") or (bearish and kind == "TRENDING_DOWN")
        conflict = (bullish and kind == "TRENDING_DOWN") or (bearish and kind == "TRENDING_UP")
        if agree:
            score += 15
        if conflict:
            score -= 20
        if kind == "UNKNOWN":
            score = min(score, 60)
        if kind == "VOLATILE":
            score -= 10
        score = max(0, min(100, score))
        thesis = f"Cleared {len(labels)} filter(s): {', '.join(labels) or 'none'}. Regime {kind}" + (f" ({regime['confidence']:.0%})" if regime.get("confidence") else "") + \
                 (" agrees with the labels." if agree else " conflicts with the labels." if conflict else ".")
        risks = ("Regime unknown - too few bars to judge context. " if kind == "UNKNOWN" else "") + \
                ("Volatile regime - stops need room. " if kind == "VOLATILE" else "") + \
                ("Labels and regime point different ways. " if conflict else "") + "Filters describe the last bar only; the state can flip on the next one."
        ranked.append(RankedSymbol(symbol=m.symbol, score=score, thesis=thesis, risks=risks.strip(),
                                   next_step="Backtest a strategy with these conditions on this symbol, then paper-trade it.", regime=kind))
        if kind == "UNKNOWN":
            warnings.append(f"{m.symbol}: regime unknown")
    ranked.sort(key=lambda r: -r.score)
    summary = (f"{len(ranked)} symbol(s) cleared every filter. Scores add 10 per filter category matched, 15 when the regime agrees with "
               f"the labels' direction and subtract 20 when it conflicts; unknown regimes cap at 60." if ranked else "No symbol cleared every filter - nothing to rank.")
    return ScanRead(summary=summary, ranked=ranked, warnings=warnings[:10], provider="rule_based", model=RuleBasedProvider.model)


def parse_read(raw: dict, matches: List[ScannerMatch], regimes: Dict[str, dict]) -> ScanRead:
    known = {m.symbol for m in matches}
    ranked: List[RankedSymbol] = []
    warnings = [str(w) for w in (raw.get("warnings") or []) if str(w).strip()][:10]
    for item in (raw.get("ranked") or [])[:MAX_MATCHES_TO_READ]:
        try:
            symbol = str(item.get("symbol", "")).upper().strip()
            if symbol not in known:
                warnings.append(f"model named {symbol or '?'} which the scanner did not match - dropped")
                continue
            score = int(round(float(item.get("score", 50))))
            if regimes.get(symbol, {}).get("kind") == "UNKNOWN":
                score = min(score, 60)
            ranked.append(RankedSymbol(symbol=symbol, score=max(0, min(100, score)), thesis=str(item.get("thesis") or "")[:800],
                                       risks=str(item.get("risks") or "")[:600], next_step=str(item.get("next_step") or "")[:300] or
                                       "Backtest a strategy with these conditions, then paper-trade it.", regime=regimes.get(symbol, {}).get("kind")))
        except (TypeError, ValueError, AttributeError) as exc:
            warnings.append(f"ranked entry dropped: {str(exc)[:100]}")
    missing = known - {r.symbol for r in ranked}
    if missing:
        warnings.append("not ranked by the model: " + ", ".join(sorted(missing)))
    ranked.sort(key=lambda r: -r.score)
    return ScanRead(summary=str(raw.get("summary") or "")[:1500], ranked=ranked, warnings=warnings)


async def read_scan(session: AsyncSession, tenant: Tenant, user: User, request: ScannerRequest, result: ScannerResult, *,
                    language: str = "en", provider: Optional[LLMProvider] = None) -> ScanRead:
    matches = result.matches[:MAX_MATCHES_TO_READ]
    regimes = _regimes_for(request, matches)
    provider = provider or await ai_settings.provider_for(session, tenant)
    if isinstance(provider, RuleBasedProvider) or not matches:
        read = rule_based_read(matches, regimes)
    else:
        payload = {"filters": {"indicator": [c.label() for c in request.indicator_conditions], "structure": [s.label() for s in request.structure_filters],
                               "option": [o.label() for o in request.option_filters]},
                   "matches": [{"symbol": m.symbol, "close": m.close, "labels": m.matched_indicator_labels + m.matched_structure_labels + m.matched_option_labels,
                                "regime": regimes.get(m.symbol)} for m in matches],
                   "regimes_possible": list(REGIMES) + ["UNKNOWN"]}
        try:
            raw = await provider.complete(read_system_prompt(language), "SCAN RESULT:\n" + json.dumps(payload), max_tokens=3000)
            AI_PROVIDER_CALLS.labels(provider=provider.name, outcome="ok").inc()
            read = parse_read(_extract_json(raw), matches, regimes)
            read.provider, read.model = provider.name, provider.model
            await ai_settings.mark_used(session, tenant.id)
        except (ProviderError, ValueError, json.JSONDecodeError) as exc:
            AI_PROVIDER_CALLS.labels(provider=provider.name, outcome="error").inc()
            await ai_settings.mark_used(session, tenant.id, error=str(exc))
            read = rule_based_read(matches, regimes)
            read.warnings.insert(0, f"{provider.name} did not answer usably ({str(exc)[:120]}); deterministic read used instead")
    if len(result.matches) > MAX_MATCHES_TO_READ:
        read.warnings.append(f"only the first {MAX_MATCHES_TO_READ} of {len(result.matches)} matches were read")
    await write_audit_log(session, tenant.id, user.id, "ai_scan_read", f"{read.provider}/{read.model}: {len(read.ranked)} ranked of {len(matches)}")
    await meter(session, tenant.id, "ai_scanner", 1, source="api", metadata={"kind": "read", "provider": read.provider, "matches": len(matches)}, commit=False)
    await session.commit()
    return read
