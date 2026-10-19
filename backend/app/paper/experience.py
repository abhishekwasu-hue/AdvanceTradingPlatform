"""N2-2 (P1 spec 1.4, docs/design/N2_P1FGH.md): experience levels gate complexity, with an explicit, logged opt-out.

Each user has a level, derived from their own PAPER record (closed trades priced from platform quotes, IST sessions with
a PAPER trade, the largest drawdown - the same counted trades as the account and the ladder). The level cannot be typed
in; it moves only with the record.

| Level | Reached at | Opens |
|---|---|---|
| Beginner | (start) | cash underlyings, bought options, hedged option structures |
| Intermediate | the PAPER ladder's sessions, trades and drawdown criteria | futures |
| Advanced | `EXPERIENCE_ADVANCED_MIN_TRADES` closed trades over `EXPERIENCE_ADVANCED_MIN_SESSIONS` sessions, drawdown within the ladder's limit | naked short options |

A naked short option is a written option entered on its own, or a structure whose sold lots of one right (CE or PE)
outnumber its bought lots of that right (short straddle, 1:2 ratio spread).

Below the level a gate needs, the order pipeline refuses the new entry (`entry_refusals`). The refusal is called from
`signal_execution.execute_signal_for_user` and `multileg.execute_structure`, next to the F&O ban check. Without a
resolved contract (TradingView webhook, public API, console, approval, underlying deployments) the raw symbol is
classified (`symbol_gates`: instrument master, contract registry, symbol shape). The user may opt
out of one gate for the rest of the IST day:

- they type a reason (at least `EXPERIENCE_OPT_OUT_MIN_REASON_CHARS` characters);
- it is stored (`experience_opt_outs`), written to the audit log and counted on the level card (today, last 7 days,
  total).

Exits never come here (ADR-0004). Nothing here places an order. With the flag off nothing is gated.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.core import config as app_config
from app.core.enums import InstrumentKind, OrderSide
from app.db.models import ExperienceOptOutRecord, PaperAccountRecord, User
from app.market_data.calendar import IST

FLAG = "experience_levels"
BEGINNER, INTERMEDIATE, ADVANCED = "BEGINNER", "INTERMEDIATE", "ADVANCED"
LEVELS: Tuple[str, ...] = (BEGINNER, INTERMEDIATE, ADVANCED)
LEVEL_LABELS = {BEGINNER: "Beginner", INTERMEDIATE: "Intermediate", ADVANCED: "Advanced"}
GATE_NAKED = "naked_short_options"
GATE_FUTURES = "futures"
# gate -> (the level that opens it, label)
GATES: Dict[str, Tuple[str, str]] = {
    GATE_FUTURES: (INTERMEDIATE, "Futures"),
    GATE_NAKED: (ADVANCED, "Naked short options"),
}
EXITS_NOTE = "Exits are never gated: stops, targets, time exits and Exit now always work."


class OptOutError(ValueError):
    """A refused opt-out (unknown gate, reason too short)."""


class NothingToOptOut(ValueError):
    """The user's level already opens this gate."""


async def enabled(session: AsyncSession, tenant_id: int) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, FLAG, tenant_id)


# --- classification (pure) ---------------------------------------------------------------------------------------------

def contract_gates(contract: Any) -> List[str]:
    """The gates a single-leg entry on a resolved contract needs: a future -> futures; a written option -> naked short."""
    kind = getattr(contract, "kind", None)
    if kind == InstrumentKind.FUTURE:
        return [GATE_FUTURES]
    if kind == InstrumentKind.OPTION and getattr(contract, "entry_side", None) == OrderSide.SELL:
        return [GATE_NAKED]
    return []


# Review M1 follow-up: raw symbols arrive in many spellings (TradingView, Upstox, Dhan, Shoonya, Yahoo-style suffixes).
# Classify what is plainly a derivative; a symbol that is neither in the master nor plainly cash-shaped FAILS CLOSED
# (the futures gate applies), as the P1-d ticket refuses what it cannot read as a cash underlying.
_PREFIX = re.compile(r"^([A-Z_]+):")
_SUFFIX = re.compile(r"\.([A-Z]{1,4})$")
DERIVATIVE_VENUES = {"NFO", "BFO", "MCX", "CDS", "NCDEX"}
CASH_VENUES = {"NSE", "BSE", "NS", "BO"}
_CONTINUOUS = re.compile(r"\d+!$")                                              # NIFTY1!, CRUDEOIL1! (TradingView {{ticker}})
_FUTURE_PATTERNS = (re.compile(r"FUT$"), re.compile(r"\bFUT\b"), re.compile(r"\d{2}[A-Z]{3}\d{2}F$"))   # ...OCTFUT, 'X FUT 30 OCT 26', Shoonya ...26F
_OPTION_PATTERNS = (re.compile(r"\d[\s-]*(CE|PE)$"),                            # NIFTY24OCT25000CE, 'NIFTY 25000 CE', Dhan ...-26000-CE
                    re.compile(r"\d+(?:\.\d+)?\s+(CE|PE)\b"),                   # Upstox 'NIFTY 26000 CE 30 OCT 26'
                    re.compile(r"\d{2}[A-Z]{3}\d{2}[CP]\d+(?:\.\d+)?$"))          # Shoonya NIFTY30OCT26C26000
_CASH_SHAPE = re.compile(r"^[A-Z][A-Z0-9&-]*$")                                 # RELIANCE, M&M, BAJAJ-AUTO, RELIANCE-EQ
GATE_UNRECOGNISED = "unrecognised_instrument"                                  # treated as the futures gate (fail closed)


def normalise_symbol(symbol: str) -> Tuple[str, Optional[str], Optional[str]]:
    """(core, exchange prefix, suffix): 'MCX:CRUDEOIL' -> ('CRUDEOIL', 'MCX', None); 'RELIANCE.NS' -> ('RELIANCE', None, 'NS')."""
    text = (symbol or "").strip().upper()
    prefix = suffix = None
    m = _PREFIX.match(text)
    if m:
        prefix, text = m.group(1), text[m.end():].strip()
    m = _SUFFIX.search(text)
    if m:
        suffix, text = m.group(1), text[:m.start()].strip()
    return text, prefix, suffix


def _is_commodity(core: str) -> bool:
    from app.core.enums import AssetClass
    from app.instruments.registry import get_contract_spec
    root = re.match(r"[A-Z]+", core)
    for name in {core, root.group(0) if root else core}:
        spec = get_contract_spec(name)
        if spec is not None and spec.asset_class == AssetClass.COMMODITY:
            return True
    return False


def _is_index(core: str) -> bool:
    from app.instruments.master import INDEX_SYMBOLS
    return core in INDEX_SYMBOLS or core in set(INDEX_SYMBOLS.values())


def classify_symbol(symbol: str, *, master_types: Sequence[Tuple[str, str]] = ()) -> str:
    """"cash" | "future" | "option" | "unrecognised" for a raw symbol. `master_types`: the instrument master's
    (exchange, instrument_type) rows for it (they win when present)."""
    from app.brokers.contract_symbols import parse_contract
    if master_types:
        kinds = {(kind or "").upper() for _, kind in master_types}
        if "FUT" in kinds or any((ex or "").upper() in DERIVATIVE_VENUES - {"NFO", "BFO"} for ex, _ in master_types):
            return "future"
        if kinds & {"CE", "PE"}:
            return "option"
        if kinds & {"EQ", "INDEX", "BE"}:
            return "cash"
    core, prefix, suffix = normalise_symbol(symbol)
    if not core:
        return "unrecognised"
    parsed = parse_contract(core)
    if parsed is not None:
        return "future" if parsed.right == "FUT" else "option"
    if any(p.search(core) for p in _OPTION_PATTERNS):
        return "option"
    if (prefix in DERIVATIVE_VENUES or suffix in DERIVATIVE_VENUES or _CONTINUOUS.search(core) or _is_commodity(core)
            or any(p.search(core) for p in _FUTURE_PATTERNS)):
        return "future"
    if (prefix in (None, *CASH_VENUES)) and (suffix in (None, *CASH_VENUES)) and (_CASH_SHAPE.match(core) or _is_index(core)):
        return "cash"
    return "unrecognised"


def raw_symbol_gates(symbol: str, direction: Any, *, master_types: Sequence[Tuple[str, str]] = ()) -> List[str]:
    """The gates a raw-symbol entry needs (review M1: webhook, public API, console, approval, underlying deployments -
    no resolved contract): a future -> futures; an option sold (SHORT) -> naked short options; a bought option, a cash
    share or an index -> nothing; anything else -> the futures gate, as an unrecognised instrument (fail closed)."""
    kind = classify_symbol(symbol, master_types=master_types)
    short = str(getattr(direction, "value", direction)).upper() == "SHORT"
    if kind == "future":
        return [GATE_FUTURES]
    if kind == "option":
        return [GATE_NAKED] if short else []
    if kind == "unrecognised":
        return [GATE_UNRECOGNISED]
    return []


async def symbol_gates(session: AsyncSession, symbol: str, direction: Any) -> List[str]:
    """`raw_symbol_gates` with the instrument master's rows (any broker) for the symbol, as sent and normalised."""
    from app.db.models import InstrumentRecord
    core, _, _ = normalise_symbol(symbol)
    names = sorted({(symbol or "").strip().upper(), core} - {""})
    rows = [(ex or "", kind or "") for ex, kind in await session.execute(
        select(InstrumentRecord.exchange, InstrumentRecord.instrument_type).where(InstrumentRecord.tradingsymbol.in_(names)).limit(50))]
    return raw_symbol_gates(symbol, direction, master_types=rows)


async def symbol_entry_refusals(session: AsyncSession, user: Optional[User], symbol: str, direction: Any,
                                now: Optional[datetime] = None) -> List[str]:
    """`entry_refusals` for a raw-symbol entry; the flag is checked before the symbol is looked up."""
    if user is None or not await enabled(session, user.tenant_id):
        return []
    return await entry_refusals(session, user, await symbol_gates(session, symbol, direction), now)


def structure_gates(legs: Iterable[Any]) -> List[str]:
    """The gates a structure needs: any futures leg -> futures; per right, more sold lots than bought -> naked short.
    `legs`: objects with `.role` ("SHORT"/"LONG"), `.ratio` and `.contract` (kind, right)."""
    gates: List[str] = []
    net: Dict[str, int] = {}
    for leg in legs:
        contract = leg.contract
        if getattr(contract, "kind", None) == InstrumentKind.FUTURE:
            if GATE_FUTURES not in gates:
                gates.append(GATE_FUTURES)
            continue
        right = getattr(contract, "right", None)
        if right in ("CE", "PE"):
            ratio = int(getattr(leg, "ratio", 1) or 1)
            net[right] = net.get(right, 0) + (ratio if leg.role == "SHORT" else -ratio)
    if any(v > 0 for v in net.values()):
        gates.append(GATE_NAKED)
    return gates


def derive_level(closed: int, sessions: int, max_drawdown_pct: float) -> Tuple[str, List[Dict[str, Any]]]:
    """(level, the criteria of each level above Beginner with their values) from a PAPER record."""
    dd_limit = app_config.PAPER_LADDER_MAX_DRAWDOWN_PCT

    def criteria(min_trades: int, min_sessions: int) -> List[Dict[str, Any]]:
        return [
            {"key": "closed_trades", "label": "Closed PAPER trades", "value": closed, "target": min_trades, "comparison": "at_least",
             "met": closed >= min_trades},
            {"key": "sessions", "label": "Sessions with a PAPER trade", "value": sessions, "target": min_sessions, "comparison": "at_least",
             "met": sessions >= min_sessions},
            {"key": "max_drawdown_pct", "label": "Largest drawdown, % of peak equity", "value": max_drawdown_pct, "target": dd_limit,
             "comparison": "at_most", "met": closed > 0 and max_drawdown_pct <= dd_limit},
        ]
    steps = [(INTERMEDIATE, criteria(app_config.PAPER_LADDER_MIN_TRADES, app_config.PAPER_LADDER_MIN_SESSIONS)),
             (ADVANCED, criteria(max(app_config.EXPERIENCE_ADVANCED_MIN_TRADES, app_config.PAPER_LADDER_MIN_TRADES),
                                 max(app_config.EXPERIENCE_ADVANCED_MIN_SESSIONS, app_config.PAPER_LADDER_MIN_SESSIONS)))]
    level = BEGINNER
    out = []
    for name, crit in steps:
        reached = all(c["met"] for c in crit) and level == LEVELS[LEVELS.index(name) - 1]
        if reached:
            level = name
        out.append({"key": name, "label": LEVEL_LABELS[name], "opens": [GATES[g][1] for g, (lvl, _) in GATES.items() if lvl == name],
                    "reached": reached, "criteria": crit})
    return level, out


def allowed_by_level(level: str, gate: str) -> bool:
    return LEVELS.index(level) >= LEVELS.index(GATES[gate][0])


def refusal_text(gate: str, level: str, record: Dict[str, Any]) -> str:
    if gate == GATE_UNRECOGNISED:
        need = GATES[GATE_FUTURES][0]
        return (f"Unrecognised instrument; the futures gate applies: it opens at the {LEVEL_LABELS[need]} experience level; yours is "
                f"{LEVEL_LABELS[level]} (from your PAPER record: {record['closed_trades']} closed trades over {record['sessions']} sessions). "
                "A futures opt-out for today, with a reason, lifts this gate; it is logged and counted")
    need, label = GATES[gate]
    return (f"{label} open at the {LEVEL_LABELS[need]} experience level; yours is {LEVEL_LABELS[level]} (from your PAPER record: "
            f"{record['closed_trades']} closed trades over {record['sessions']} sessions). An opt-out for today, with a reason, "
            "lifts this gate; it is logged and counted")


# --- database --------------------------------------------------------------------------------------------------------

def ist_day(now: datetime) -> Any:
    return (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).astimezone(IST).date()


async def record(session: AsyncSession, user: User) -> Dict[str, Any]:
    """The user's own PAPER record (counted trades, as the account and ladder). Reads only - never creates the account."""
    from app.paper import service
    trades = [t for t in await service._paper_trades(session, user) if service._counted(t)]
    closed = sorted((t for t in trades if t.exit_time is not None), key=lambda t: (service._utc(t.exit_time or t.entry_time), t.id))
    sessions = {service._utc(t.entry_time).astimezone(IST).date() for t in trades if t.entry_time is not None}
    account = await session.scalar(select(PaperAccountRecord).where(PaperAccountRecord.tenant_id == user.tenant_id,
                                                                    PaperAccountRecord.user_id == user.id))
    starting = float(account.starting_capital) if account is not None else float(app_config.PAPER_STARTING_CAPITAL)
    dd = service.equity_drawdown(starting, [float(t.pnl or 0.0) for t in closed])
    return {"closed_trades": len(closed), "sessions": len(sessions), "max_drawdown_pct": float(dd["max_drawdown_pct"])}


async def _opted_out_today(session: AsyncSession, user: User, day: Any) -> List[str]:
    return list(await session.scalars(select(ExperienceOptOutRecord.gate).where(
        ExperienceOptOutRecord.tenant_id == user.tenant_id, ExperienceOptOutRecord.user_id == user.id, ExperienceOptOutRecord.day == day)))


async def entry_refusals(session: AsyncSession, user: Optional[User], gates: Sequence[str], now: Optional[datetime] = None) -> List[str]:
    """Why a new entry needing `gates` is refused for this user ([] when allowed, the flag is off, or nothing is gated).
    New entries only: the exit paths never call this."""
    if not gates or user is None or not await enabled(session, user.tenant_id):
        return []
    now = now or datetime.now(timezone.utc)
    rec = await record(session, user)
    level, _ = derive_level(rec["closed_trades"], rec["sessions"], rec["max_drawdown_pct"])
    def base(g: str) -> str:
        return GATE_FUTURES if g == GATE_UNRECOGNISED else g
    blocked = [g for g in dict.fromkeys(gates) if base(g) in GATES and not allowed_by_level(level, base(g))]
    if not blocked:
        return []
    opted = set(await _opted_out_today(session, user, ist_day(now)))
    return [refusal_text(g, level, rec) for g in blocked if base(g) not in opted]


async def status(session: AsyncSession, user: User, now: Optional[datetime] = None) -> Dict[str, Any]:
    """The level card: level, the record, each level's criteria, each gate's state, and the opt-out counts."""
    now = now or datetime.now(timezone.utc)
    day = ist_day(now)
    rec = await record(session, user)
    level, steps = derive_level(rec["closed_trades"], rec["sessions"], rec["max_drawdown_pct"])
    opted = set(await _opted_out_today(session, user, day))
    mine = (ExperienceOptOutRecord.tenant_id == user.tenant_id, ExperienceOptOutRecord.user_id == user.id)
    total = int(await session.scalar(select(func.count(ExperienceOptOutRecord.id)).where(*mine)) or 0)
    week = int(await session.scalar(select(func.count(ExperienceOptOutRecord.id)).where(*mine, ExperienceOptOutRecord.day > day - timedelta(days=7))) or 0)
    recent = list(await session.scalars(select(ExperienceOptOutRecord).where(*mine).order_by(ExperienceOptOutRecord.id.desc()).limit(10)))
    gates = []
    for key, (need, label) in GATES.items():
        by_level = allowed_by_level(level, key)
        gates.append({"key": key, "label": label, "opens_at": need, "opens_at_label": LEVEL_LABELS[need], "allowed_by_level": by_level,
                      "opted_out_today": key in opted, "allowed": by_level or key in opted})
    return {
        "level": level, "level_label": LEVEL_LABELS[level], "levels": [{"key": BEGINNER, "label": LEVEL_LABELS[BEGINNER], "reached": True,
                                                                         "opens": [], "criteria": []}] + steps,
        "record": rec, "gates": gates,
        "opt_outs": {"today": len(opted), "last_7_days": week, "total": total,
                     "recent": [{"gate": r.gate, "gate_label": GATES.get(r.gate, (None, r.gate))[1], "level": r.level, "reason": r.reason,
                                 "day": r.day.isoformat()} for r in recent]},
        "min_reason_chars": app_config.EXPERIENCE_OPT_OUT_MIN_REASON_CHARS, "day": day.isoformat(), "exits": EXITS_NOTE,
        "as_of": now.isoformat(),
    }


async def opt_out(session: AsyncSession, user: User, gate: str, reason: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Lifts one gate for the rest of the IST day: stored, audited, counted. Once per gate per day (a second call
    returns the first, not counted twice). Refused when the level already opens the gate or the reason is too short."""
    now = now or datetime.now(timezone.utc)
    if gate not in GATES:
        raise OptOutError(f"Unknown gate '{gate}' - known: {', '.join(GATES)}")
    rec = await record(session, user)
    level, _ = derive_level(rec["closed_trades"], rec["sessions"], rec["max_drawdown_pct"])
    if allowed_by_level(level, gate):                       # before the reason: a qualified user hears 409, not 422
        raise NothingToOptOut(f"Your level ({LEVEL_LABELS[level]}) already opens {GATES[gate][1].lower()}")
    text = (reason or "").strip()
    if len(text) < app_config.EXPERIENCE_OPT_OUT_MIN_REASON_CHARS:
        raise OptOutError(f"An opt-out needs a reason of at least {app_config.EXPERIENCE_OPT_OUT_MIN_REASON_CHARS} characters")
    day = ist_day(now)
    created = True
    try:
        async with session.begin_nested():
            session.add(ExperienceOptOutRecord(tenant_id=user.tenant_id, user_id=user.id, gate=gate, level=level, reason=text[:300], day=day,
                                               created_at=now))
            await session.flush()
    except IntegrityError:                                  # already opted out of this gate today
        created = False
    if created:
        await write_audit_log(session, user.tenant_id, user.id, "experience_opt_out",
                              f"{gate} for {day.isoformat()} IST at level {level}: {text}"[:500])
    await session.commit()
    return {"created": created, **await status(session, user, now)}
