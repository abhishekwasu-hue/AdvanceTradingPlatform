"""Pre-open charts from ATP's own job (owner 4 Nov: "Default yes" - A: ATP sends the pre-open charts; the last unfilled
gap as a band; the official close with every distance from it; the phase in the title).

Once per trading day, from PREOPEN_CHARTS_AT_IST (operational time only, never reading logic) for PREOPEN_CHARTS_WINDOW_MINUTES:
per organisation whose flag `preopen_charts` is ON (admin / owner only, OFF by default) and whose signal engine runner
settings are stored (`engine_runner.SETTINGS_KEY`: whose charts = the runner's user, which symbols = the runner's
symbols, the same data windows), and only while the private package is installed:

1. The engine request of the last closed bar, built exactly as the Copilot's engine read builds it
   (`app.ai.tools.engine_read.build_request`: his stored rows, nothing after that bar - no look-ahead).
2. The official close (`own_bars.official_close`: NSE's index close file, else his broker's Daily history) when it is
   the close of the newest session in the bars; a provisional or older close is not used (the private module then
   shows the newest bar's close, labelled "last close").
3. The private package draws Daily / 1H / 15M (`atp_private.preopen.build`, in a thread) - the method lives only there.
4. The charts go to the runner user's own Telegram chat (`screener.evidence_store._telegram_target`: the organisation's
   own bot for a chat linked to it, else the platform bot for "Connect Telegram"), one photo per chart with its caption.

One run per organisation per day: a `runtime_counters` row (name DAY_SENT, key "<tenant>:<day>") is claimed before
sending (its unique key keeps a second worker out) and holds the outcome for the admin. No new table, no migration.
PAPER only: nothing here places, blocks or changes an order or an exit (ADR-0004); the AI is never involved (ADR-0006).
"""
from __future__ import annotations

import asyncio
import importlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.core import config as app_config
from app.db.models import RuntimeCounterRecord, User

logger = logging.getLogger(__name__)

FLAG = "preopen_charts"
MODULE = "atp_private.preopen"
DAY_SENT = "preopen_charts_day"               # runtime_counters name: one row per (organisation, trading day)

Send = Callable[[str, str, bytes, str], Awaitable[None]]


@dataclass
class PreopenReport:
    skipped: str | None = None
    tenants: int = 0
    sent: int = 0
    problems: list[str] = field(default_factory=list)


def _private() -> Any:
    """The private package's pre-open module, or None (absent / broken: the job does nothing, never a crash)."""
    try:
        module = importlib.import_module(MODULE)
    except Exception as exc:  # noqa: BLE001 - a missing or broken private package must not break ATP
        logger.info("Pre-open charts off: %s not importable (%s)", MODULE, exc.__class__.__name__)
        return None
    return module if callable(getattr(module, "build", None)) else None


def in_window(now: datetime) -> date | None:
    """The IST day when `now` is inside the send window, else None (operational time only)."""
    from app.market_data.calendar import IST
    at = app_config.PREOPEN_CHARTS_AT_IST
    if at is None:
        return None
    local = now.astimezone(IST)
    start = local.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    return local.date() if start <= local < start + timedelta(minutes=app_config.PREOPEN_CHARTS_WINDOW_MINUTES) else None


def usable_close(official: dict[str, Any] | None, newest_day: date | None) -> tuple[float, str] | None:
    """The official close when it is the newest session's own close (never provisional, never older than the bars)."""
    if not official or official.get("provisional") or official.get("close") is None or newest_day is None:
        return None
    if str(official.get("date")) != newest_day.isoformat():
        return None
    return float(official["close"]), str(official["date"])


def _newest_day(request: Any) -> date | None:
    from app.engine_provider import frames as F
    days = [f.bars[-1].start.astimezone(F.IST).date() for f in (request.frames or {}).values() if f is not None and f.bars]
    return max(days) if days else None


async def _claim(session: Any, tenant_id: int, day: date) -> RuntimeCounterRecord | None:
    row = RuntimeCounterRecord(name=DAY_SENT, key=f"{tenant_id}:{day.isoformat()}", value=json.dumps({"state": "running"}))
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return None
    return row


async def _telegram_send(token: str, chat: str, png: bytes, caption: str) -> None:
    from app.ai.vision_audit import send_photo
    await send_photo(token, chat, png, caption)


async def run_tenant(session: Any, cfg: Any, user: User, engine: Any, module: Any, now: datetime, *,
                     send: Send = _telegram_send) -> dict[str, Any]:
    """One organisation's charts: {"sent": n, "symbols": {...}} or {"reason": ...}. Never raises for one symbol."""
    from app.ai.tools.engine_read import build_request
    from app.market_lake import own_bars
    from app.screener.evidence_store import _telegram_target
    chat, token, reason = await _telegram_target(session, user)
    if chat is None or not token:
        return {"sent": 0, "reason": reason or "no_telegram"}
    ctx: Any = SimpleNamespace(session=session, user=user, tenant_id=user.tenant_id, now=now)   # the fields build_request reads
    out: dict[str, Any] = {"sent": 0, "symbols": {}}
    try:
        for watch in cfg.symbols:
            try:
                request, complete, _last = await build_request(ctx, cfg, watch, engine)
                if request is None:
                    out["symbols"][watch.shown] = "no closed bar to read"
                    continue
                scope = await own_bars.scope_for(session, user, watch.key)
                close = usable_close(await own_bars.official_close(session, watch.key, scope), _newest_day(request))
                built = await asyncio.to_thread(module.build, request, close)
                if built.get("problem"):
                    out["symbols"][watch.shown] = f"engine: {built['problem']}"
                    continue
                for _tf, png, caption in built.get("items") or ():
                    await send(token, chat, png, caption)
                    out["sent"] += 1
                out["symbols"][watch.shown] = {"charts": len(built.get("items") or ()), "errors": list(built.get("errors") or ())[:5],
                                               "data_complete": bool(complete), "official_close": close is not None}
            except Exception as exc:  # noqa: BLE001 - one symbol's problem stays on it
                await session.rollback()
                logger.warning("Pre-open charts for %s failed: %s", watch.shown, exc.__class__.__name__)
                out["symbols"][watch.shown] = f"failed ({exc.__class__.__name__})"
    finally:
        token = ""
    return out


async def run_due(session_factory: Any, now: datetime | None = None, *, send: Send = _telegram_send) -> PreopenReport:
    """Every organisation with `preopen_charts` ON and stored runner settings, once per trading day inside the window."""
    from app.engine_provider import frames as F
    from app.engine_provider import loader
    from app.engine_provider import sources as S
    from app.platform.controls import flag_enabled
    from app.workers import engine_runner as R
    now = (now or datetime.now(UTC)).astimezone(UTC)
    day = in_window(now)
    if day is None:
        return PreopenReport(skipped="outside the send window")
    engine, module = loader.load(), _private()
    if engine is None or module is None:
        return PreopenReport(skipped="private package not installed")
    report = PreopenReport()
    async with session_factory() as session:
        cal = await S.load_calendar(session, "NSE", day, day + timedelta(days=1))
        if not F.is_session_day(cal, day):
            return PreopenReport(skipped="not a trading day")
        stored = await R._stored_settings(session)
        for tenant_key in sorted(stored, key=str):
            try:
                tenant_id = int(tenant_key)
                if not await flag_enabled(session, FLAG, tenant_id):
                    continue
                cfg = R.parse_settings(tenant_id, stored[tenant_key])
            except (ValueError, R.SettingsProblem) as exc:
                report.problems.append(f"organisation {tenant_key}: {exc}")
                continue
            user = await session.get(User, cfg.user_id)
            if user is None or user.tenant_id != tenant_id or not user.is_active:
                report.problems.append(f"organisation {tenant_id}: the runner's user is not an active member")
                continue
            row = await _claim(session, tenant_id, day)
            if row is None:
                continue                                       # already sent (or being sent) today
            report.tenants += 1
            try:
                result = await run_tenant(session, cfg, user, engine, module, now, send=send)
            except Exception as exc:  # noqa: BLE001 - recorded for the admin, never reaches trading
                await session.rollback()
                result = {"sent": 0, "reason": f"failed ({exc.__class__.__name__})"}
            report.sent += int(result.get("sent") or 0)
            row.value = json.dumps({"state": "done", **result}, default=str)[:4000]
            await session.commit()
    return report


__all__ = ["FLAG", "MODULE", "DAY_SENT", "PreopenReport", "in_window", "usable_close", "run_tenant", "run_due"]
