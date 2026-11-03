"""REV-1 (docs/design/REV1_PLAN.md "Needs live check"): the ten read-only live checks of the full review
(docs/reviews/ATP_FULL_REVIEW.md, the two "Needs live check" lists) - the logic behind `deploy/atp.sh review-checks`,
run inside the backend container:

    python -m app.platform.review_checks

Each check prints ONE line `ok|WARN|FAIL <name>: <plain reason>`:

* ok   - the item the review asked about is not live on this site;
* WARN - it is live (the review finding applies here) - the line says how many;
* FAIL - the check itself could not run (the reason is the error's kind; the other checks still run).

READ-ONLY: one session, a read-only transaction on PostgreSQL, only SELECTs, rolled back at the end - nothing is written,
nothing is restarted. Only counts, organisation ids, flag names and strategy ids are printed - never a name, an e-mail,
a price, a secret or any AI text (the AI answers are scanned in memory for order words and only counted).

Exit codes: 0 every check ran (WARN lines included); 1 at least one check FAILED.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LlmCallRecord, NewsEventRecord, RiskSettingsRecord, TradeRecord, User

# Review section 3 item 6 names four flags; with byob_data_tier (section 2 item 1) and ai_one_key (section 3 item 4)
# these are the six flags the two "Needs live check" lists name.
SIX_FLAGS = ("byob_data_tier", "ai_one_key", "discipline_engine", "idea_library", "strategy_templates", "backtest_optimizer")
ZERO_BLOCKING = ("max_trades_per_day", "max_open_positions", "max_consecutive_losses")
RAW_STRATEGY_PREFIXES = ("custom:", "plan_")
SCAN_READ_FEATURE = "scanner_read"
# The review's own words (section 2, item 4): buy, sell, target or stop.
ORDER_WORDS = re.compile(r"\b(?:buy(?:ing|s)?|sell(?:ing|s)?|targets?|stop(?:[- ]?loss(?:es)?|s)?)\b", re.I)
SHOWN_IDS = 20                                   # at most this many ids on one line

Line = tuple[str, str, str]                      # (status, name, reason)


def _ids(values: Sequence[Any]) -> str:
    items = [str(v) for v in values]
    more = len(items) - SHOWN_IDS
    return ", ".join(items[:SHOWN_IDS]) + (f" and {more} more" if more > 0 else "")


def _flag_words(entry: dict[str, Any]) -> str:
    if entry.get("on"):
        return "ON for everyone"
    tenants = entry.get("tenants") or []
    return f"ON for organisation(s) {_ids(tenants)}" if tenants else "OFF"


async def _flags(session: AsyncSession) -> dict[str, dict[str, Any]]:
    from app.platform.controls import feature_flags
    return await feature_flags(session)


async def check_byob_flag(session: AsyncSession) -> Line:
    entry = (await _flags(session)).get("byob_data_tier") or {}
    words = _flag_words(entry)
    if words == "OFF":
        return ("ok", "byob-flag", "byob_data_tier is OFF for every organisation - the Option chain plan message is not live")
    return ("WARN", "byob-flag", f"byob_data_tier is {words} - the Option chain plan message is live there")


async def check_shared_symbol_open(session: AsyncSession) -> Line:
    grouped = (select(TradeRecord.tenant_id, TradeRecord.symbol).where(TradeRecord.exit_time.is_(None))
               .group_by(TradeRecord.tenant_id, TradeRecord.symbol)
               .having(func.count(func.distinct(TradeRecord.user_id)) > 1).subquery())
    rows = (await session.execute(select(grouped.c.tenant_id))).all()
    if not rows:
        return ("ok", "shared-symbol-open", "no organisation has two or more members with open trades in the same symbol")
    tenants = sorted({int(r[0]) for r in rows})
    return ("WARN", "shared-symbol-open", f"{len(rows)} symbol(s) held open by two or more members of one organisation "
                                          f"(organisation(s) {_ids(tenants)}) - the Terminal P&L is wrong for them")


async def check_trades_rows(session: AsyncSession) -> Line:
    rows = (await session.execute(select(TradeRecord.tenant_id, func.count()).group_by(TradeRecord.tenant_id)
                                  .order_by(func.count().desc()))).all()
    if not rows:
        return ("ok", "trades-rows", "no trade rows yet")
    total = sum(int(n) for _, n in rows)
    top_tenant, top_n = rows[0]
    return ("ok", "trades-rows", f"{total} trade row(s) in {len(rows)} organisation(s); the most: {int(top_n)} "
                                 f"(organisation {int(top_tenant)}) - the rows My trades / Orders / Money read")


def _read_fields(answer: str) -> str:
    """The free text of one stored scan-read answer that the page shows: the summary and each match's thesis / risks /
    next_step (the whole answer when it is not the JSON the read asks for)."""
    try:
        data = json.loads(answer[answer.index("{"):answer.rindex("}") + 1])
    except ValueError:
        return answer
    if not isinstance(data, dict):
        return answer
    parts = [str(data.get("summary") or "")]
    for row in data.get("ranked") or []:
        if isinstance(row, dict):
            parts += [str(row.get(k) or "") for k in ("thesis", "risks", "next_step")]
    return "\n".join(parts)


async def check_ai_read_words(session: AsyncSession) -> Line:
    stored = 0
    with_words = 0
    answers = await session.scalars(select(LlmCallRecord.response_text).where(LlmCallRecord.feature == SCAN_READ_FEATURE,
                                                                              LlmCallRecord.response_text.is_not(None)))
    for answer in answers:
        stored += 1
        if ORDER_WORDS.search(_read_fields(str(answer))):
            with_words += 1
    if not stored:
        return ("ok", "ai-read-words", "no stored AI answer of a Custom scan read (only the rule-based read ran, or none)")
    if not with_words:
        return ("ok", "ai-read-words", f"none of {stored} stored Custom scan AI answer(s) uses buy / sell / target / stop words")
    return ("WARN", "ai-read-words", f"{with_words} of {stored} stored Custom scan AI answer(s) use buy / sell / target / "
                                     "stop words (texts not printed)")


async def check_risk_zero(session: AsyncSession) -> Line:
    counts = []
    for field in ZERO_BLOCKING:
        column = getattr(RiskSettingsRecord, field)
        n = int(await session.scalar(select(func.count()).select_from(RiskSettingsRecord).where(column == 0)) or 0)
        if n:
            counts.append(f"{field} {n}")
    rows = int(await session.scalar(select(func.count()).select_from(RiskSettingsRecord)
                                    .where(or_(*(getattr(RiskSettingsRecord, f) == 0 for f in ZERO_BLOCKING)))) or 0)
    if not rows:
        return ("ok", "risk-zero", "no risk settings row holds 0 in max trades / max open positions / losses in a row")
    return ("WARN", "risk-zero", f"{rows} risk settings row(s) hold 0, which blocks every new entry ({'; '.join(counts)})")


async def check_members_per_org(session: AsyncSession) -> Line:
    rows = (await session.execute(select(User.tenant_id, func.count()).where(User.is_active.is_(True))
                                  .group_by(User.tenant_id))).all()
    if not rows:
        return ("ok", "members-per-org", "no active member yet")
    shared = sorted(int(t) for t, n in rows if int(n) > 1)
    if not shared:
        return ("ok", "members-per-org", f"{len(rows)} organisation(s), each with one active member - Performance and "
                                         "Coach / Tax read the same trades")
    largest = max(int(n) for _, n in rows)
    return ("WARN", "members-per-org", f"{len(shared)} of {len(rows)} organisation(s) have more than one active member "
                                       f"(largest {largest}; organisation(s) {_ids(shared)}) - Performance and Coach / Tax "
                                       "numbers differ there")


async def check_strategy_ids(session: AsyncSession) -> Line:
    rows = (await session.execute(select(TradeRecord.strategy_id, func.count()).group_by(TradeRecord.strategy_id)
                                  .order_by(TradeRecord.strategy_id))).all()
    if not rows:
        return ("ok", "strategy-ids", "no trade history yet")
    raw = [f"{s} ({int(n)})" for s, n in rows if str(s).startswith(RAW_STRATEGY_PREFIXES)]
    if not raw:
        return ("ok", "strategy-ids", f"{len(rows)} strategy id(s) in the history, none in the raw custom:N / plan_ form")
    return ("WARN", "strategy-ids", f"{len(raw)} of {len(rows)} strategy id(s) in the history are raw ids shown on "
                                    f"Performance / Coach: {_ids(raw)}")


async def check_ai_one_key(session: AsyncSession) -> Line:
    entry = (await _flags(session)).get("ai_one_key") or {}
    words = _flag_words(entry)
    if entry.get("on"):
        return ("ok", "ai-one-key", "ai_one_key is ON for everyone - Settings > AI shows the Connect AI panel")
    return ("WARN", "ai-one-key", f"ai_one_key is {words} - elsewhere Settings > AI shows the older card with operator / "
                                  "token-price text")


async def check_news_rows(session: AsyncSession) -> Line:
    n = int(await session.scalar(select(func.count()).select_from(NewsEventRecord)) or 0)
    return ("ok", "news-rows", f"{n} news row(s) - the list /news-events renders")


async def check_six_flags(session: AsyncSession) -> Line:
    flags = await _flags(session)
    states = [f"{name} {_flag_words(flags.get(name) or {})}" for name in SIX_FLAGS if name in flags]
    missing = [name for name in SIX_FLAGS if name not in flags]
    if missing:
        return ("WARN", "flags", f"not in this release: {', '.join(missing)}; " + "; ".join(states))
    return ("ok", "flags", "; ".join(states))


CHECKS: tuple[tuple[str, Callable[[AsyncSession], Awaitable[Line]]], ...] = (
    ("byob-flag", check_byob_flag),
    ("shared-symbol-open", check_shared_symbol_open),
    ("trades-rows", check_trades_rows),
    ("ai-read-words", check_ai_read_words),
    ("risk-zero", check_risk_zero),
    ("members-per-org", check_members_per_org),
    ("strategy-ids", check_strategy_ids),
    ("ai-one-key", check_ai_one_key),
    ("news-rows", check_news_rows),
    ("flags", check_six_flags),
)


async def run_checks(session: AsyncSession) -> list[Line]:
    """The ten checks in order; a check that raises is one FAIL line (the error's kind only) and the rest still run."""
    if (await session.connection()).dialect.name == "postgresql":
        await session.execute(text("SET TRANSACTION READ ONLY"))
    out: list[Line] = []
    try:
        for name, check in CHECKS:
            try:
                async with session.begin_nested():
                    out.append(await check(session))
            except Exception as exc:                 # one broken check must not hide the others
                out.append(("FAIL", name, f"the check could not run ({type(exc).__name__})"))
    finally:
        await session.rollback()
    return out


def format_lines(lines: Sequence[Line]) -> list[str]:
    return [f"{status} {name}: {reason}" for status, name, reason in lines]


async def _main() -> int:
    from app.db.session import _session_factory
    async with _session_factory() as session:
        lines = await run_checks(session)
    print("\n".join(format_lines(lines)))
    return 1 if any(status == "FAIL" for status, _, _ in lines) else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        print("usage: python -m app.platform.review_checks   (no arguments; read-only)")
        return 2
    return asyncio.run(_main())


if __name__ == "__main__":
    sys.exit(main())
