"""Phase V3: the Risk Guardian system prompt, its runtime context and the schema mapping.

The spec's prompt ("AMW Strategy Architect") is a versioned template whose `{{...}}` slots the
platform fills on every request from the tenant's live state - capital, risk settings and
ceilings, open risk and drawdown from the guardian, the last trades, upcoming market events,
the market regime the page read, the user's language. The model answers in that language;
the JSON stays in English.

The spec's STRATEGY_SCHEMA is wider than what the platform deploys, so it is **mapped**, not
copied: the rule set is the platform's `CustomStrategyConfig` (what the Strategy Builder and the
backtester run), and the structure / risk / trade-management blocks become a
`DeploymentSuggestion` - the Autopilot settings the strategy should be deployed with (option
structure, expiry and strike rules, credit target/stop, exit rules, regime filter). Sizing,
portfolio caps, cool-downs and the drawdown ladder are not asked of the model at all: the
engine derives them (Phase V1), so the prompt tells the model what will happen rather than
asking it to promise. The compliance validator (Phase V2) reads the suggestion too.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ExpiryRule, InstrumentKind, OptionPosition, OptionStrategy, StrikeRule
from app.core.models import RiskConfig
from app.db.models import Tenant, TradeRecord, User
from app.instruments.spreads import PAYOFF_STRUCTURES, RATIO_SPREADS, UNDEFINED_RISK, is_debit
from app.risk_engine import guardian

PROMPT_VERSION = "guardian-v3.0"
MIN_STOP_ATR_MULTIPLE = 1.0
DEFAULT_INSTRUMENTS = "NSE equities; NIFTY, BANKNIFTY, FINNIFTY, SENSEX options and futures (index options share one correlated risk bucket)"
LANGUAGE_NAMES = {"en": "English", "mr": "Marathi", "hi": "Hindi", "gu": "Gujarati", "ta": "Tamil", "te": "Telugu", "kn": "Kannada", "bn": "Bengali"}


class SuggestedExitRules(BaseModel):
    trailing_stop_pct: Optional[float] = Field(default=None, gt=0, le=50)
    break_even_at_r: Optional[float] = Field(default=None, gt=0, le=10)
    time_exit_minutes: Optional[int] = Field(default=None, ge=1, le=375)
    time_exit_at: Optional[str] = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

    @property
    def active(self) -> bool:
        return any(v is not None for v in self.model_dump().values())


class DeploymentSuggestion(BaseModel):
    """What the model proposes to deploy the rule set as; every field maps onto the Autopilot
    form (deployments API). Unknown or out-of-range values are dropped with a warning, never
    guessed."""
    symbol: Optional[str] = Field(default=None, max_length=50)
    instrument_kind: InstrumentKind = InstrumentKind.UNDERLYING
    option_strategy: OptionStrategy = OptionStrategy.SINGLE
    option_position: Optional[OptionPosition] = None
    expiry_rule: Optional[ExpiryRule] = None
    strike_rule: Optional[StrikeRule] = None
    strike_offset: int = Field(default=0, ge=0, le=10)
    spread_width: int = Field(default=2, ge=1, le=20)
    target_credit_pct: Optional[float] = Field(default=None, ge=5, le=95)
    stop_credit_pct: Optional[float] = Field(default=None, ge=10, le=500)
    exit_rules: SuggestedExitRules = Field(default_factory=SuggestedExitRules)
    regime_filter: List[str] = Field(default_factory=list, max_length=5)
    next_step: str = Field(default="backtest", pattern="^(backtest|paper_trade|small_live)$")

    @property
    def is_credit_structure(self) -> bool:
        return self.instrument_kind == InstrumentKind.OPTION and self.option_strategy != OptionStrategy.SINGLE and not is_debit(self.option_strategy)

    @property
    def undefined_risk(self) -> bool:
        return self.option_strategy in UNDEFINED_RISK or self.option_strategy in RATIO_SPREADS or (
            self.instrument_kind == InstrumentKind.OPTION and self.option_strategy == OptionStrategy.SINGLE and self.option_position == OptionPosition.WRITE)

    def describe(self) -> str:
        if self.instrument_kind == InstrumentKind.UNDERLYING:
            base = "trade the underlying"
        elif self.instrument_kind == InstrumentKind.FUTURE:
            base = f"{(self.expiry_rule or ExpiryRule.NEAREST).value.lower()} future"
        elif self.option_strategy == OptionStrategy.SINGLE:
            base = f"{'write' if self.option_position == OptionPosition.WRITE else 'buy'} a {(self.strike_rule or StrikeRule.ATM).value}{self.strike_offset or ''} option, {(self.expiry_rule or ExpiryRule.NEAREST).value.lower()} expiry"
        else:
            base = f"{self.option_strategy.value.lower().replace('_', ' ')}, {(self.expiry_rule or ExpiryRule.NEAREST).value.lower()} expiry"
        extras = []
        if self.exit_rules.break_even_at_r:
            extras.append(f"break-even at {self.exit_rules.break_even_at_r:g}R")
        if self.exit_rules.trailing_stop_pct:
            extras.append(f"trail {self.exit_rules.trailing_stop_pct:g}%")
        if self.regime_filter:
            extras.append("regimes " + "/".join(self.regime_filter))
        return base + (" · " + ", ".join(extras) if extras else "")


def parse_suggestion(raw: Any) -> Tuple[Optional[DeploymentSuggestion], List[str]]:
    """Tolerant: a missing block is None; a block with bad values loses those values (warned)."""
    if not isinstance(raw, dict) or not raw:
        return None, []
    data = dict(raw)
    warnings: List[str] = []
    for key in ("instrument_kind", "option_strategy", "option_position", "expiry_rule", "strike_rule"):
        if key in data and isinstance(data[key], str):
            data[key] = data[key].strip().upper()
    regimes = data.get("regime_filter")
    if isinstance(regimes, list):
        from app.ai.regime import REGIMES
        kept = [str(r).upper() for r in regimes if str(r).upper() in REGIMES]
        if len(kept) != len(regimes):
            warnings.append("deployment: unknown regime names dropped from regime_filter")
        data["regime_filter"] = kept
    if isinstance(data.get("next_step"), str):
        data["next_step"] = data["next_step"].strip().lower()
    for attempt in range(6):
        try:
            return DeploymentSuggestion.model_validate(data), warnings
        except ValidationError as exc:
            dropped = False
            for error in exc.errors():
                loc = error.get("loc") or ()
                if not loc:
                    continue
                head = loc[0]
                if head == "exit_rules" and len(loc) > 1 and isinstance(data.get("exit_rules"), dict):
                    data["exit_rules"].pop(loc[1], None)
                    warnings.append(f"deployment: exit rule {loc[1]} dropped ({error.get('msg')})")
                    dropped = True
                elif head in data:
                    data.pop(head, None)
                    warnings.append(f"deployment: {head} dropped ({error.get('msg')})")
                    dropped = True
            if not dropped:
                break
    warnings.append("deployment: suggestion could not be read - ignored")
    return None, warnings


@dataclass
class RuntimeContext:
    account_capital: float
    currency: str
    risk_profile: str
    allowed_instruments: str
    open_risk: float
    open_risk_pct: float
    current_drawdown_pct: float
    drawdown_mode: str
    recent_trade_summary: str
    event_calendar: List[str]
    market_regime: str
    user_language: str
    max_risk_per_trade_pct: float
    ceiling_risk_per_trade_pct: float
    max_portfolio_risk_pct: float
    daily_loss_limit_pct: float
    cooldown_minutes: int
    dd_level_1_pct: float
    dd_level_2_pct: float
    event_size_cut_pct: float
    min_stop_atr_multiple: float = MIN_STOP_ATR_MULTIPLE
    allow_naked: bool = False
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self) -> dict:
        return asdict(self)


def risk_profile_for(risk_per_trade_pct: float) -> str:
    if risk_per_trade_pct <= 0.5:
        return "conservative"
    if risk_per_trade_pct <= 1.0:
        return "moderate"
    return "aggressive"


async def recent_trade_summary(session: AsyncSession, tenant_id: int, limit: int = 10) -> str:
    rows = list(await session.scalars(select(TradeRecord).where(
        TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.isnot(None)).order_by(TradeRecord.exit_time.desc()).limit(limit)))
    if not rows:
        return "no closed trades yet"
    wins = sum(1 for r in rows if (r.pnl or 0) > 0)
    net = sum(float(r.pnl or 0.0) for r in rows)
    stops = sum(1 for r in rows if guardian.is_stop_exit(r.exit_reason))
    modes = sorted({r.mode for r in rows})
    streak = 0
    for r in rows:
        if (r.pnl or 0) < 0:
            streak += 1
        else:
            break
    text = f"last {len(rows)} closed trades ({'/'.join(modes)}): {wins} won, {len(rows) - wins} lost, net {net:,.0f}, {stops} stopped out"
    if streak >= 3:
        text += f"; the last {streak} in a row lost"
    return text


async def build_runtime_context(session: AsyncSession, tenant: Tenant, user: User, cfg: RiskConfig, ceilings: Dict[str, float], *,
                                regime: Optional[str] = None, language: str = "en", symbol: Optional[str] = None) -> RuntimeContext:
    status = await guardian.status(session, tenant.id, cfg)
    live, paper = status["modes"]["LIVE"], status["modes"]["PAPER"]
    # LIVE numbers when the tenant trades live, else PAPER's: the AI should reason about the book that exists.
    mode = "LIVE" if (live["closed_trades"] or live["open_risk_total"]) else "PAPER"
    picked = live if mode == "LIVE" else paper
    horizon = datetime.now(guardian.IST).date() + timedelta(days=7)
    events = [e for e in await _events_until(session, tenant.id, horizon)]
    return RuntimeContext(
        account_capital=float(cfg.capital), currency=getattr(tenant, "base_currency", None) or "INR",
        risk_profile=risk_profile_for(float(cfg.risk_per_trade_pct)),
        allowed_instruments=(f"{symbol.upper()} (requested); " if symbol else "") + DEFAULT_INSTRUMENTS,
        open_risk=float(picked["open_risk_total"]), open_risk_pct=float(picked["open_risk_pct"]),
        current_drawdown_pct=float(picked["drawdown_pct"]), drawdown_mode=mode,
        recent_trade_summary=await recent_trade_summary(session, tenant.id),
        event_calendar=events, market_regime=(regime or "UNKNOWN").upper(), user_language=(language or "en").lower()[:5],
        max_risk_per_trade_pct=float(cfg.risk_per_trade_pct), ceiling_risk_per_trade_pct=float(ceilings.get("risk_per_trade_pct", 2.0)),
        max_portfolio_risk_pct=float(cfg.max_portfolio_risk_pct), daily_loss_limit_pct=float(cfg.max_daily_loss_pct),
        cooldown_minutes=int(cfg.stop_cooldown_minutes), dd_level_1_pct=float(cfg.dd_level_1_pct), dd_level_2_pct=float(cfg.dd_level_2_pct),
        event_size_cut_pct=float(cfg.event_size_cut_pct),
    )


async def _events_until(session: AsyncSession, tenant_id: int, horizon) -> List[str]:
    from app.db.models import MarketEventRecord
    today = datetime.now(guardian.IST).date()
    rows = await session.scalars(select(MarketEventRecord).where(
        MarketEventRecord.event_date >= today, MarketEventRecord.event_date <= horizon,
        (MarketEventRecord.tenant_id.is_(None)) | (MarketEventRecord.tenant_id == tenant_id)).order_by(MarketEventRecord.event_date))
    out = []
    for e in rows:
        scope = e.underlying or "all symbols"
        action = "no new entries" if (e.action or "").upper() == "BLOCK" else f"size cut {e.size_cut_pct if e.size_cut_pct is not None else 'default'}%"
        out.append(f"{e.event_date.isoformat()} {e.kind} ({scope}): {action}" + (f" - {e.description}" if e.description else ""))
    return out


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get((code or "en").lower()[:2], code or "English")


def build_system_prompt(ctx: RuntimeContext) -> str:
    events = "\n".join(f"  - {e}" for e in ctx.event_calendar) or "  - none in the next 7 days"
    structures = ", ".join(s.value for s in OptionStrategy)
    return f"""You are AMW Strategy Architect - the AI inside AMW Algorithmic Trading SaaS. Users describe trading ideas in
chat; you turn them into complete, executable, risk-managed strategies. Prompt version {PROMPT_VERSION}.

Your character is that of a disciplined professional trader, not a hype machine. You are built on one conviction shared
by every great trader (Paul Tudor Jones, Druckenmiller, Minervini, Seykota, Livermore, Douglas, Steenbarger): methods
differ, but ruthless risk management is universal. "Defense first, offense second." You never promise profits.

Reply in the user's language: {language_name(ctx.user_language)} ({ctx.user_language}). Every JSON key and enum value stays in English.

=== RUNTIME CONTEXT (filled by the platform; do not ask the user for these) ===
Account capital: {ctx.account_capital:,.0f} {ctx.currency}
User risk profile: {ctx.risk_profile} (risk per trade {ctx.max_risk_per_trade_pct:g}% of capital; platform ceiling {ctx.ceiling_risk_per_trade_pct:g}%)
Instruments allowed: {ctx.allowed_instruments}
Current open risk (loss if every open stop hits): {ctx.open_risk:,.0f} {ctx.currency} = {ctx.open_risk_pct:g}% of capital
Current drawdown from the {ctx.drawdown_mode} equity peak: {ctx.current_drawdown_pct:g}%
Recent trades: {ctx.recent_trade_summary}
Upcoming events (next 7 days):
{events}
Market regime (from the platform's classifier): {ctx.market_regime}

=== WHAT THE ENGINE ENFORCES ON EVERY ENTRY (you describe it, you never bypass it) ===
- Position size is DERIVED from risk, never chosen: size = capital x risk% / stop distance per unit, floored to whole lots.
  A structure is sized on its MAX LOSS (or the loss its stop accepts when the risk is undefined), never on premium or margin.
- Risk per trade <= {ctx.max_risk_per_trade_pct:g}% of capital (hard ceiling {ctx.ceiling_risk_per_trade_pct:g}%).
- Total open risk across all positions <= {ctx.max_portfolio_risk_pct:g}% of capital; NIFTY, BANKNIFTY, FINNIFTY and SENSEX positions
  count as ONE correlated bucket.
- Daily loss limit {ctx.daily_loss_limit_pct:g}% of capital -> kill switch, no new trades that day.
- No re-entry in an underlying for {ctx.cooldown_minutes} minutes after a stop-out. Never averaging down; a stop only moves the favourable way.
- Drawdown ladder: {ctx.dd_level_1_pct:g}% below the equity peak halves the size; {ctx.dd_level_2_pct:g}% pauses new entries. Size never rises after a streak.
- Event days on the calendar above block entries or cut the size by {ctx.event_size_cut_pct:g}%.
- Every stop must sit at least {ctx.min_stop_atr_multiple:g} x ATR from the entry (outside normal noise); the platform raises a tighter one.

=== NON-NEGOTIABLE RULES FOR WHAT YOU PROPOSE ===
R1. A pre-defined stop before entry: `stop_loss_atr_mult` >= {ctx.min_stop_atr_multiple:g}, at the level where the idea is invalidated.
R2. Targets at or above `min_rr`, ordered; `min_rr` >= 1.0.
R3. Option selling must be defined-risk (spreads, condors, butterflies). Propose SHORT_STRADDLE, SHORT_STRANGLE, a ratio spread or a
    naked WRITE only if the user explicitly accepts unlimited risk in their request{" (allowed on this account)" if ctx.allow_naked else " - and say that the platform will size it off the stop and require an extra acceptance"}.
R4. If the regime above is unfavourable for the idea, say so and propose a regime_filter or "no trade". Selectivity is an edge.
R5. If the user shows tilt ("recover today", "double the lots", "revenge"), slow down: keep the size rules, name the cool-down,
    suggest reviewing the journal. Never flatter a strategy; if evidence is weak, say so.
If the user asks for something these rules forbid, refuse that part in one or two sentences and give the compliant version.

=== OUTPUT: ONE JSON object and nothing else (no markdown fences) ===
{{"config": {{"name": str, "timeframe": one of ["1min","3min","5min","15min","30min","60min"],
            "long_conditions": [Condition], "short_conditions": [Condition],
            "stop_loss_atr_mult": number >= {ctx.min_stop_atr_multiple:g}, "atr_period": int 2..500, "target_rr": [number, number], "min_rr": number >= 1}},
 "deployment": {{"symbol": str|null, "instrument_kind": "UNDERLYING"|"OPTION"|"FUTURE",
                "option_strategy": one of [{structures}],
                "option_position": "BUY"|"WRITE"|null, "expiry_rule": "NEAREST"|"NEXT"|"MONTHLY"|null,
                "strike_rule": "ATM"|"ITM"|"OTM"|null, "strike_offset": int 0..10, "spread_width": int 1..20,
                "target_credit_pct": number 5..95|null, "stop_credit_pct": number 10..500|null,
                "exit_rules": {{"trailing_stop_pct": number|null, "break_even_at_r": number|null, "time_exit_minutes": int|null, "time_exit_at": "HH:MM"|null}},
                "regime_filter": [regime names, empty = any], "next_step": "backtest"|"paper_trade"|"small_live"}},
 "explanation": str in the user's language - what it does (2-3 lines), the max loss per trade in {ctx.currency} and % of capital,
                the worst case (gap / 3-5 sigma day) and what the user must accept before going live,
 "warnings": [str] - what could make it fail (regimes, costs, slippage, over-fitting, weak evidence)}}
Condition = {{"left": Operand, "operator": one of ["GT","LT","GTE","LTE","CROSSES_ABOVE","CROSSES_BELOW"], "right": Operand}}
Operand = {{"type": "value", "value": number}} or {{"type": "indicator", "indicator": one of
  ["EMA","SMA","RSI","ADX","PLUS_DI","MINUS_DI","ATR","SUPERTREND","CLOSE","OPEN","HIGH","LOW"], "period": int 1..500}}
Rules: conditions on a side are AND-combined; at most 4 per side; at least one side non-empty; conditions a human can verify on a
chart. For a credit structure use target_credit_pct 50-70 and stop_credit_pct 100-200 (the short premium doubling to tripling).
For a debit structure or an underlying trade prefer break_even_at_r 2 and a trailing stop. next_step is always "backtest" for a
new idea: backtest -> paper -> small live, never straight to full size."""


def user_message(prompt: str) -> str:
    return f"USER REQUEST:\n{prompt.strip()}"
