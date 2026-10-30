"""Analyze a chart: "Option strikes from your settings" (owner order 10, the result screen).

The analysis never picks a strike. This module only shows what the caller's OWN option deployment settings on the
same symbol (strike rule, offset, wing width, expiry rule - Deployments page) would work out to if the setup's trigger
level were the price at that moment. Everything returned is flagged `source: "your_settings"`; in Image-only mode the
reference price was read from a picture, so the strikes are approximate and the page says so.

Which deployments count (CLAUDE.md §3: no rule nobody stated, so nothing is invented here):
- the caller's organisation's OPTION deployments, ACTIVE or PAUSED, on the same underlying as the chart;
- whose structure fits the setup's side: a single option on either side, the bull put spread and the call ratio spread
  on the bull-put side, the bear call spread and the put ratio spread on the bear-call side. Neutral structures
  (iron condor, straddles, strangles, butterflies, calendars) and free-form legs are not listed (OPEN_QUESTIONS CA-9).
The strike ladder comes from ATP's instrument master (no broker call); without it the settings are shown without
strikes.

Text uses structure words only (signal_language): "main strike", "wing", never buy / sell / target / stop.
Nothing here places, proposes or changes an order.
"""
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ExpiryRule, InstrumentKind, OptionPosition, OptionStrategy, SignalDirection, StrikeRule
from app.db.models import StrategyDeploymentRecord
from app.instruments import master
from app.instruments.contracts import ContractResolutionError, ContractRules, option_right, select_expiry, select_strike
from app.instruments.spreads import RATIO_SHORTS, plan_side, structure_sides

IST = ZoneInfo("Asia/Kolkata")
SOURCE = "your_settings"
LIVE_STATUSES = ("ACTIVE", "PAUSED")
MAX_DEPLOYMENTS = 10

SIDE_DIRECTION = {"bull_put": SignalDirection.LONG, "bear_call": SignalDirection.SHORT}
# The structures that fit each side of the method's setup (see the module docstring).
FITS: Dict[str, frozenset] = {
    "bull_put": frozenset({OptionStrategy.SINGLE, OptionStrategy.BULL_PUT_SPREAD, OptionStrategy.CALL_RATIO_SPREAD}),
    "bear_call": frozenset({OptionStrategy.SINGLE, OptionStrategy.BEAR_CALL_SPREAD, OptionStrategy.PUT_RATIO_SPREAD}),
}
STRUCTURE_NAMES = {
    OptionStrategy.BULL_PUT_SPREAD: "Bull put spread", OptionStrategy.BEAR_CALL_SPREAD: "Bear call spread",
    OptionStrategy.CALL_RATIO_SPREAD: "Call ratio spread", OptionStrategy.PUT_RATIO_SPREAD: "Put ratio spread",
}
EXPIRY_TEXT = {ExpiryRule.NEAREST: "nearest expiry", ExpiryRule.NEXT: "next expiry", ExpiryRule.MONTHLY: "monthly expiry",
               ExpiryRule.ROLL: "nearest expiry (the next on expiry day)"}
RIGHT_TEXT = {"CE": "call", "PE": "put"}

NO_SIDE = "No setup is formed or forming, so there are no strikes to work out."
NO_SYMBOL = "The symbol could not be read from the picture. Type it in to see the strikes your settings work out to."
NO_REFERENCE = "The trigger level has no price, so the strikes cannot be worked out. Your settings are listed below."
NO_MASTER = "The option list for this symbol is not loaded yet, so the strikes cannot be worked out."
NONE_FIT = "None of your option deployments on this symbol fits this side. Strike settings live on the Deployments page."
APPROXIMATE = "Worked out from a price read from the picture, so they are approximate. Check them on the option chain."


def normalise_symbol(raw: Optional[str]) -> Optional[str]:
    """`NSE:NIFTY` / `nifty 50` -> `NIFTY 50`-style upper case without an exchange prefix; None when empty."""
    text = (raw or "").strip()
    if ":" in text:
        text = text.split(":", 1)[1]
    text = " ".join(text.upper().split())
    return text or None


def _strike_text(rule: StrikeRule, offset: int) -> str:
    if rule == StrikeRule.ATM or offset <= 0:
        return "at the money"
    return f"{offset} strike{'s' if offset != 1 else ''} {'out of' if rule == StrikeRule.OTM else 'in'} the money"


def describe(dep: StrategyDeploymentRecord, side: str) -> str:
    """The deployment's strike settings in plain, structure-only words."""
    strategy = OptionStrategy(dep.option_strategy or "SINGLE")
    rules = ContractRules.from_deployment(dep)
    expiry = EXPIRY_TEXT.get(rules.expiry_rule, "nearest expiry")
    main = _strike_text(rules.strike_rule, rules.strike_offset)
    if strategy == OptionStrategy.SINGLE:
        right = option_right(SIDE_DIRECTION[side], rules.position or OptionPosition.BUY)
        return f"Single {RIGHT_TEXT[right]} option · main strike {main} · {expiry}"
    width = int(dep.spread_width or 0)
    wing = f"{width} strike{'s' if width != 1 else ''} away"
    if strategy in (OptionStrategy.CALL_RATIO_SPREAD, OptionStrategy.PUT_RATIO_SPREAD):
        return f"{STRUCTURE_NAMES[strategy]} · main strike {main} · {RATIO_SHORTS} × second strike {wing} · {expiry}"
    return f"{STRUCTURE_NAMES[strategy]} · main strike {main} · wing {wing} · {expiry}"


def _leg_label(strategy: OptionStrategy, index: int, ratio: int) -> str:
    if index == 0:
        return "Main strike"
    if strategy in (OptionStrategy.CALL_RATIO_SPREAD, OptionStrategy.PUT_RATIO_SPREAD):
        return f"Second strike (× {ratio})"
    return "Wing"


async def _legs(session: AsyncSession, dep: StrategyDeploymentRecord, side: str, spot: float, today: date,
                with_role: bool = False) -> Dict[str, Any]:
    """{expiry, legs} from the instrument master, or {note} when the ladder is missing or too short. `with_role` (F&O
    8b-b, "Trade this setup") adds each leg's role in the structure - "short" (written) or "long" (held), structure
    words - so the ticket / basket opens with each leg's side as the deployment's own settings give it."""
    strategy = OptionStrategy(dep.option_strategy or "SINGLE")
    rules = ContractRules.from_deployment(dep)
    direction = SIDE_DIRECTION[side]
    underlying = master.underlying_of(dep.symbol)
    if strategy == OptionStrategy.SINGLE:
        rights: Sequence[str] = [option_right(direction, rules.position or OptionPosition.BUY)]
    else:
        rights, _role = structure_sides(strategy, direction)
    out: List[Dict[str, Any]] = []
    expiry_seen: Optional[date] = None
    for right in rights:
        expiries = await master.expiries(session, underlying, instrument_type=right, on_or_after=today)
        expiry = select_expiry(expiries, rules.expiry_rule, today)
        if expiry is None:
            return {"expiry": None, "legs": None, "note": NO_MASTER}
        ladder = await master.strikes(session, underlying, expiry, instrument_type=right)
        main = select_strike(ladder, spot, right, rules.strike_rule, rules.strike_offset)
        if main is None:
            return {"expiry": None, "legs": None, "note": NO_MASTER}
        expiry_seen = expiry
        if strategy == OptionStrategy.SINGLE:
            single: Dict[str, Any] = {"label": "Main strike", "right": right, "strike": main, "ratio": 1}
            if with_role:
                single["role"] = "short" if rules.position == OptionPosition.WRITE else "long"
            out.append(single)
            continue
        _sides, primary_role = structure_sides(strategy, direction)
        try:
            planned = plan_side(strategy, right, main, ladder, int(dep.spread_width or 0), primary_role)
        except ContractResolutionError:
            return {"expiry": expiry.isoformat(), "legs": None, "note": "The option list has no strike where the wing would sit."}
        # The main strike first, then the others, as the structure is described.
        planned = sorted(planned, key=lambda leg: leg.strike != main)
        for i, leg in enumerate(planned):
            row: Dict[str, Any] = {"label": _leg_label(strategy, i, leg.ratio), "right": leg.right, "strike": leg.strike, "ratio": leg.ratio}
            if with_role:
                row["role"] = "short" if leg.role == "SHORT" else "long"
            out.append(row)
    return {"expiry": expiry_seen.isoformat() if expiry_seen else None, "legs": out, "note": None}


async def deployment_legs(session: AsyncSession, dep: StrategyDeploymentRecord, side: str, spot: float, today: date) -> Dict[str, Any]:
    """The strikes ONE deployment's own settings work out to at `spot` ({expiry, legs, note}); F&O 8b-a (the setup-aware
    chain, app/paper/fno_insights.py) reads the same rule, so the chain and this result never disagree."""
    return await _legs(session, dep, side, spot, today, with_role=True)


async def for_result(session: AsyncSession, tenant_id: int, result: Dict[str, Any], *, symbol_override: Optional[str] = None,
                     today: Optional[date] = None) -> Dict[str, Any]:
    """The "from your settings" block for one analysis result."""
    setup = result.get("setup") or {}
    side = setup.get("side") if setup.get("side") in SIDE_DIRECTION and setup.get("status") in ("formed", "forming") else None
    approximate = bool(result.get("approximate_prices", True))
    trigger = setup.get("trigger") or {}
    raw_price = trigger.get("price")
    spot = float(raw_price) if isinstance(raw_price, (int, float)) and raw_price > 0 else None
    symbol = normalise_symbol(symbol_override) or normalise_symbol((result.get("recognised") or {}).get("symbol"))
    base: Dict[str, Any] = {"source": SOURCE, "side": side or "none", "symbol": symbol,
                            "reference": {"text": "the trigger level", "price": spot, "approximate": approximate},
                            "approximate_note": APPROXIMATE if approximate else None, "items": [], "message": None}
    if side is None:
        return {**base, "message": NO_SIDE}
    if symbol is None:
        return {**base, "message": NO_SYMBOL}
    underlying = master.underlying_of(symbol)
    rows = (await session.scalars(select(StrategyDeploymentRecord).where(
        StrategyDeploymentRecord.tenant_id == tenant_id,
        StrategyDeploymentRecord.instrument_kind == InstrumentKind.OPTION.value,
        StrategyDeploymentRecord.status.in_(LIVE_STATUSES),
    ).order_by(StrategyDeploymentRecord.id))).all()
    fitting = []
    for dep in rows:
        if master.underlying_of(dep.symbol) != underlying:
            continue
        try:
            strategy = OptionStrategy(dep.option_strategy or "SINGLE")
        except ValueError:
            continue
        if strategy in FITS[side]:
            fitting.append(dep)
    if not fitting:
        return {**base, "message": NONE_FIT}
    today = today or datetime.now(IST).date()
    items = []
    for dep in fitting[:MAX_DEPLOYMENTS]:
        entry: Dict[str, Any] = {"id": dep.id, "mode": "Paper" if (dep.mode or "PAPER").upper() == "PAPER" else "Live",
                                 "symbol": dep.symbol, "settings": describe(dep, side)}
        if spot is None:
            entry.update({"expiry": None, "legs": None, "note": NO_REFERENCE})
        else:
            entry.update(await _legs(session, dep, side, spot, today))
        items.append(entry)
    return {**base, "items": items}
