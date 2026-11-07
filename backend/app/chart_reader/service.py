"""M86 W1 / W2 / W4: one chart read by the private reader (docs/design/M86_PLAN.md, "Public side").

`read(...)`:
- source "lake": each chart timeframe of the settings is ATP's OWN chart of the stored candles, made exactly as Analyze a
  chart's hybrid mode makes it (app/chart_analyze/hybrid.read -> build_reading, app/chart_analyze/reading_png.render),
  and ATP's structure facts of the same readings (hybrid.facts) go with it as plain text lines -> "Advanced analysis".
- source "upload": the user's OWN Analyze a chart item (app/chart_analyze/service.own: his tenant and his user only),
  its stored picture alone, no facts -> "AI only".
- W2: when the strategy has two-chart conditions and the symbol has a pair in the settings, the pair's chart (the main
  timeframe, the same bars window: the same "as of" minute) is attached automatically; the reader's own
  `second_chart_missing` stays in the result as it says.
- Cache (Redis, app.cache.client, TTL from `cache_hours`, capped by the cache's own ceiling): keyed by the tenant, the
  strategy, the reader's version, the model, the effort, the symbol, the timeframes and each chart's structure
  fingerprint. A hit returns the stored result with `cached: true`, makes no call and costs nothing.
- Cost (W4): the estimate comes from the settings' token estimate and prices; the user's own daily cap (prefs
  `chart_reader.chart_reader_daily_cap_usd`, None = OFF, the default) is checked against today's `llm_calls` spend of the
  feature plus the estimate. Every real run writes ONE `llm_calls` row (tenant, user, provider, model, prompt version,
  tokens, the cost from the usage the reader returns, status). The reader's prompt and answer are never stored: the text
  columns hold the fixed placeholder and the hashes are of the placeholder.
- The API client is `anthropic.Anthropic(api_key=...)` made per call from the server environment variable the settings
  name; the call runs in a worker thread. No key -> a plain "not connected" refusal.
The result dict the reader returns is passed through unchanged; ATP adds `cached`, `estimate_usd`, `cost_usd`, `label`,
`source`, `charts` and `pair_attached`. Nothing here places, blocks or changes an order (ADR-0006). Nothing of the
reader's text is logged.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import client as cache_client
from app.chart_analyze import hybrid, reading_png
from app.chart_analyze.limits import day_start
from app.chart_reader import loader
from app.db.models import LlmCallRecord, Tenant, User

logger = logging.getLogger(__name__)

FEATURE = "chart_reader"
PROVIDER = "anthropic"
PLACEHOLDER = "[private method - not stored]"
PREFS_NAMESPACE = "chart_reader"
CAP_KEY = "chart_reader_daily_cap_usd"
CACHE_PREFIX = "chart_reader:v1:"
MEDIA_PNG = "image/png"
SOURCES = ("lake", "upload")
LABEL_LAKE = hybrid.LABEL_CODE_AI          # the M59 wording, one label for the product: "Advanced analysis (code + AI)"
LABEL_UPLOAD = hybrid.LABEL_AI_ONLY        # "AI only"
UPLOAD_INSTRUMENT = "Uploaded chart"
NOT_CONNECTED = "The chart reader reads with your own Claude key. Connect it in Settings > AI; nothing runs until you press Read."
# Owner decision (7 Nov, "(b) user chi swatahchi AI key, pratyek user swatahchi key vaparel"): every user's reads run on that
# user's OWN Claude key (Settings > AI, consent accepted) - never a server key; the user's own-key caps apply.
OWN_KEY_PROVIDER = "anthropic"
FAILED = "The chart reader could not finish this read. Try again in a minute."


@dataclass(frozen=True)
class Chart:
    label: str
    symbol: str | None
    timeframe: str | None
    paired: bool
    png: bytes
    fingerprint: str
    facts: dict[str, Any] | None


def refused(status: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"message": message, "code": code, **extra})


# --- pure parts ---------------------------------------------------------------------------------------------------------

def api_key(settings: dict[str, Any]) -> str | None:
    """The reader's key from the environment variable the settings name (then the fallback name); never logged."""
    for name in (settings.get("api_key_env"), settings.get("api_key_fallback_env")):
        value = os.environ.get(str(name or "")) if name else None
        if value and value.strip():
            return value.strip()
    return None


async def own_key_record(session: AsyncSession, user: User) -> Any | None:
    """The caller's own Claude key row (consent accepted), or None. A read-only support view never opens a user's key."""
    from app.ai import byok
    from app.auth import view_as
    if view_as.active():
        return None
    record = await byok.get_key(session, user, OWN_KEY_PROVIDER)
    if record is None or not await byok.consent_accepted(session, user, OWN_KEY_PROVIDER):
        return None
    return record


async def own_key(session: AsyncSession, user: User) -> str | None:
    """The caller's own Claude key in plain text for one read (never logged, never stored), or None. Refused with the
    user's own-key cap message when that cap is reached."""
    from app.ai import byok
    from app.secrets_store.envelope import ensure_tenant_key
    record = await own_key_record(session, user)
    if record is None:
        return None
    blocked = await byok.cap_block(session, user, await byok.get_prefs(session, user))
    if blocked:
        raise refused(429, "own_key_cap", f"Your own AI key's {blocked}. It opens again when the day or month rolls over.")
    await ensure_tenant_key(session, user.tenant_id)
    return byok.open_key(record, user=user)


def make_client(key: str) -> Any:
    """A fresh sync client per read (the private reader calls it in a worker thread)."""
    import anthropic
    return anthropic.Anthropic(api_key=key)


def estimate_usd(settings: dict[str, Any], charts: int) -> float:
    tokens_in = float(settings["estimate_input_tokens_base"]) + float(settings["estimate_input_tokens_per_chart"]) * max(0, charts)
    tokens_out = float(settings["estimate_output_tokens"])
    return round((tokens_in * float(settings["price_input_per_mtok_usd"]) + tokens_out * float(settings["price_output_per_mtok_usd"])) / 1e6, 6)


def _int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else 0


def usage_tokens(result: dict[str, Any]) -> tuple[int, int, int]:
    raw = result.get("usage")
    usage: dict[str, Any] = raw if isinstance(raw, dict) else {}
    return _int(usage.get("input_tokens")), _int(usage.get("output_tokens")), _int(usage.get("cache_read_input_tokens"))


def cost_usd(settings: dict[str, Any], result: dict[str, Any]) -> float:
    tokens_in, tokens_out, cache_read = usage_tokens(result)
    return round((tokens_in * float(settings["price_input_per_mtok_usd"]) + tokens_out * float(settings["price_output_per_mtok_usd"])
                  + cache_read * float(settings["price_cache_read_per_mtok_usd"])) / 1e6, 6)


def canonical(symbol: str) -> str:
    """The symbol as the pair map names it ("NIFTY" -> "NIFTY 50"), the lake's own index names."""
    from app.ai.tools.copilot import INDEX_NAMES
    sym = " ".join(str(symbol or "").strip().upper().split())
    return INDEX_NAMES.get(sym, sym)


def pair_for(settings: dict[str, Any], symbol: str) -> str | None:
    raw = settings.get("pair")
    pair: dict[str, Any] = raw if isinstance(raw, dict) else {}
    other = pair.get(canonical(symbol))
    return str(other) if other else None


def structure_fingerprint(reading: dict[str, Any]) -> str:
    """The chart's structure only (each timeframe's trend / protected level / last break, the areas and where the price
    is against them, the swing levels, the trigger candle): a new bar that changes none of it keeps the cached read."""
    def brk(b: Any) -> Any:
        return {k: b.get(k) for k in ("label", "price", "direction", "time")} if isinstance(b, dict) else None
    body = {
        "symbol": reading.get("symbol"), "timeframe": reading.get("timeframe"),
        "table": [{"tf": r.get("tf"), "ready": r.get("ready"), "trend": r.get("trend"), "weak": r.get("weak"),
                   "protected": r.get("protected"), "last_break": brk(r.get("last_break"))} for r in reading.get("table") or []],
        "areas": [{k: a.get(k) for k in ("where", "low", "high", "state", "timeframe")} for a in reading.get("areas") or []],
        "levels": reading.get("levels") or [], "protected": reading.get("protected"), "last_break": brk(reading.get("last_break")),
        "trigger": (reading.get("trigger_candle") or {}).get("t") if isinstance(reading.get("trigger_candle"), dict) else None,
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _p(value: Any) -> str:
    return f"{float(value):.2f}" if isinstance(value, (int, float)) and not isinstance(value, bool) else "not known"


def _break_text(b: Any) -> str:
    if not isinstance(b, dict):
        return "none in the candles read"
    side = f" ({b['direction']})" if b.get("direction") in ("up", "down") else ""
    return f"{b.get('label') or 'Structure break'} at {_p(b.get('price'))}{side}"


def facts_text(label: str, facts: dict[str, Any]) -> str:
    """ATP's structure facts of one chart (hybrid.facts) as plain lines: structure words and price levels only - no
    candle times, no date and no clock (C.4)."""
    lines = [f"Chart: {label}", f"Last close: {_p(facts.get('last_close'))}"]
    for r in facts.get("timeframes") or []:
        if not r.get("ready"):
            lines.append(f"Timeframe {hybrid.tf_label(str(r.get('tf')))}: not enough stored candles")
            continue
        trend = {"up": "up", "down": "down", "sideways": "sideways"}.get(str(r.get("trend") or ""), "not read")
        weak = " (weak)" if r.get("weak") else ""
        lines.append(f"Timeframe {hybrid.tf_label(str(r.get('tf')))}: trend {trend}{weak}; protected level {_p(r.get('protected'))}; "
                     f"last break {_break_text(r.get('last_break'))}")
    for a in facts.get("areas") or []:
        state = ", ".join(x for x in (a.get("state"), hybrid.tf_label(str(a["timeframe"])) if a.get("timeframe") else None) if x)
        distance = "" if a.get("distance") is None else f", {_p(a.get('distance'))} points away"
        lines.append(f"Area of interest {a.get('where')}: {_p(a.get('low'))} - {_p(a.get('high'))}{f' ({state})' if state else ''}{distance}")
    for lv in facts.get("levels") or []:
        lines.append(f"{str(lv.get('kind') or 'level').capitalize()}: {_p(lv.get('price'))}")
    lines.append(f"Protected level of this chart: {_p(facts.get('protected'))}")
    lines.append(f"Last break of this chart: {_break_text(facts.get('last_break'))}")
    trigger = facts.get("trigger_candle")
    if isinstance(trigger, dict):
        lines.append(f"Trigger candle (the candle of the last break): open {_p(trigger.get('o'))}, high {_p(trigger.get('h'))}, "
                     f"low {_p(trigger.get('l'))}, close {_p(trigger.get('c'))}")
    return "\n".join(lines)


def cache_key(*, tenant_id: int, strategy: str, version: str, model: str, effort: str, source: str, symbol: str | None,
              timeframes: list[str], charts: list[Chart]) -> str:
    body = {"tenant": int(tenant_id), "strategy": strategy, "version": version, "model": model, "effort": effort, "source": source,
            "symbol": symbol, "timeframes": list(timeframes), "charts": [[c.label, c.fingerprint] for c in charts]}
    return CACHE_PREFIX + hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


def strategy_labels(reader: loader.Reader) -> list[dict[str, Any]]:
    """The reader's labels (id, name, book, author, edition, two_chart, sides) - labels only, never a spec."""
    keep = ("id", "name", "book", "author", "edition", "two_chart", "sides")
    out = []
    for s in reader.strategies() or []:
        if isinstance(s, dict) and s.get("id"):
            out.append({k: s[k] for k in keep if k in s})
    return out


# --- reads (I/O) --------------------------------------------------------------------------------------------------------

async def own_cap_usd(session: AsyncSession, user: User) -> float | None:
    """The user's own daily cap (None = Not set, OFF). Only ever his own row."""
    from app.db.models import UserPrefsRecord
    from app.me.prefs import stored_value
    raw = await session.scalar(select(UserPrefsRecord.value_json).where(
        UserPrefsRecord.tenant_id == user.tenant_id, UserPrefsRecord.user_id == user.id, UserPrefsRecord.namespace == PREFS_NAMESPACE))
    value = stored_value(PREFS_NAMESPACE, raw).get(CAP_KEY)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


async def spent_today_usd(session: AsyncSession, user: User, now: datetime) -> float:
    """What this user's chart reads cost since 00:00 UTC (the llm_calls rows of the feature)."""
    return float(await session.scalar(select(func.coalesce(func.sum(LlmCallRecord.cost_usd), 0.0)).where(
        LlmCallRecord.tenant_id == user.tenant_id, LlmCallRecord.user_id == user.id, LlmCallRecord.feature == FEATURE,
        LlmCallRecord.created_at >= day_start(now))) or 0.0)


async def check_cap(session: AsyncSession, user: User, estimate: float, now: datetime) -> None:
    cap = await own_cap_usd(session, user)
    if cap is None:
        return
    spent = await spent_today_usd(session, user, now)
    if spent + estimate > cap:
        raise refused(429, "daily_cap_reached",
                      f"Your own daily limit for chart reads is {cap:.2f} USD; {spent:.2f} USD is used today and this read is about "
                      f"{estimate:.2f} USD. It resets at 00:00 UTC, or change your limit in Settings.")


async def _render(session: AsyncSession, user: User, symbol: str, tf: str, *, paired: bool, now: datetime) -> Chart:
    try:
        pick = hybrid.check_pick(symbol, tf)
    except hybrid.PickError as exc:
        raise refused(422, exc.code, str(exc)) from None
    try:
        reading = await hybrid.read(session, user, pick, now=now)
    except hybrid.ReadingUnavailable as exc:
        raise refused(409, "reading_unavailable", str(exc)) from None
    try:
        png = await run_in_threadpool(reading_png.render, reading)
    except ValueError:
        raise refused(409, "reading_unavailable", "ATP has no stored candles to draw for this chart yet.") from None
    word = hybrid.tf_label(pick["timeframe"])
    label = f"{pick['symbol']} {word}" + (" (paired chart, same bars)" if paired else "")
    return Chart(label=label, symbol=pick["symbol"], timeframe=pick["timeframe"], paired=paired, png=png,
                 fingerprint=structure_fingerprint(reading),
                 facts=hybrid.facts(reading, width=reading_png.WIDTH, height=reading_png.HEIGHT))


async def lake_charts(session: AsyncSession, user: User, settings: dict[str, Any], symbol: str, *, two_chart: bool,
                      now: datetime) -> list[Chart]:
    """ATP's own chart of the stored candles for each timeframe; W2: the pair's main-timeframe chart for a two-chart
    strategy (the same as-of minute, so the same bars window)."""
    sym = canonical(symbol)
    timeframes = list(settings["timeframes"])
    charts = [await _render(session, user, sym, tf, paired=False, now=now) for tf in timeframes]
    other = pair_for(settings, sym) if two_chart else None
    if other:
        charts.append(await _render(session, user, other, timeframes[0], paired=True, now=now))
    return charts


async def upload_chart(session: AsyncSession, user: User, item_id: int) -> tuple[Chart, str | None]:
    """The user's own Analyze a chart item (tenant and user, M59 storage) as one chart, and the symbol stored with it."""
    from app.chart_analyze import service as ca_service
    from app.chart_analyze import storage
    row = await ca_service.own(session, user, item_id)
    data = storage.read(row.image_path or "")
    if data is None:
        raise refused(410, "image_gone", "The chart image is no longer stored.")
    pick = hybrid.stored_override(row.result_json)
    stored_symbol = pick.get("symbol")
    chart = Chart(label=stored_symbol or UPLOAD_INSTRUMENT, symbol=stored_symbol, timeframe=pick.get("timeframe"), paired=False,
                  png=data, fingerprint=hashlib.sha256(data).hexdigest(), facts=None)
    return chart, stored_symbol


def _public_charts(charts: list[Chart]) -> list[dict[str, Any]]:
    return [{"label": c.label, "symbol": c.symbol, "timeframe": c.timeframe, "paired": c.paired} for c in charts]


async def _log(session: AsyncSession, user: User, settings: dict[str, Any], result: dict[str, Any] | None, status: str,
               cost: float, prompt_version: str | None) -> None:
    from app.ai.metering import log_call
    from app.ai.providers import Completion
    tokens_in, tokens_out, cache_read = usage_tokens(result or {})
    usage = (result or {}).get("usage") if isinstance((result or {}).get("usage"), dict) else {}
    model = str((usage or {}).get("model") or settings["model"])[:80]
    await log_call(session, tenant_id=user.tenant_id, user_id=user.id, feature=FEATURE, provider=PROVIDER, model=model,
                   prompt_version=(prompt_version or "")[:40] or None, system=PLACEHOLDER, user=PLACEHOLDER, response=PLACEHOLDER,
                   status=status, result=Completion(text="", input_tokens=tokens_in, output_tokens=tokens_out, cache_read_tokens=cache_read),
                   cost_usd=cost)
    await session.commit()


def _call(reader: loader.Reader, *, instrument: str, charts: list[Chart], facts: str, strategy: str, settings: dict[str, Any],
          key: str) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"instrument": instrument, "charts": [(c.label, c.png, MEDIA_PNG) for c in charts], "facts": facts,
                              "strategy": strategy, "model": settings["model"], "effort": settings["effort"], "client": make_client(key)}
    try:
        if "max_output_tokens" in inspect.signature(reader.read).parameters:
            kwargs["max_output_tokens"] = int(settings["max_output_tokens"])
    except (TypeError, ValueError):
        pass
    return reader.read(**kwargs)


async def read(session: AsyncSession, user: User, tenant: Tenant, settings: dict[str, Any], *, symbol: str | None, strategy: str,
               source: str = "lake", item_id: int | None = None, now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(UTC)).replace(second=0, microsecond=0)
    if source not in SOURCES:
        raise refused(422, "bad_source", "Pick the chart's source: ATP's stored candles or your own uploaded chart.")
    upload: tuple[Chart, str | None] | None = None
    if source == "upload":
        if item_id is None:
            raise refused(422, "item_required", "Pick one of your uploaded charts.")
        upload = await upload_chart(session, user, int(item_id))           # his own item only (404 otherwise)
    elif not symbol:
        raise refused(422, "symbol_required", "Pick the chart's symbol (for example NIFTY or RELIANCE).")
    reader = loader.load()
    if reader is None:
        raise refused(503, "reader_not_installed", loader.NOT_INSTALLED, reason=loader.reason())
    ids = {s["id"] for s in strategy_labels(reader)} | {reader.ABHI}
    if strategy not in ids:
        raise refused(422, "unknown_strategy", "Pick one of the listed strategies.")
    key = await own_key(session, user)
    if key is None:
        raise refused(503, "not_connected", NOT_CONNECTED)
    two_chart = bool(reader.two_chart_conditions(strategy))

    if upload is not None:
        charts = [upload[0]]
        instrument = canonical(symbol) if symbol else (upload[1] or UPLOAD_INSTRUMENT)
        facts, label, timeframes = "", LABEL_UPLOAD, []
    else:
        instrument = canonical(str(symbol))
        charts = await lake_charts(session, user, settings, instrument, two_chart=two_chart, now=now)
        facts = "\n\n".join(facts_text(c.label, c.facts) for c in charts if c.facts is not None)
        label, timeframes = LABEL_LAKE, list(settings["timeframes"])
    extras: dict[str, Any] = {"label": label, "source": source, "charts": _public_charts(charts),
                              "pair_attached": any(c.paired for c in charts), "estimate_usd": estimate_usd(settings, len(charts))}

    ttl = int(float(settings["cache_hours"]) * 3600)
    ckey = cache_key(tenant_id=tenant.id, strategy=strategy, version=loader.version(), model=settings["model"], effort=settings["effort"],
                     source=source, symbol=instrument, timeframes=timeframes, charts=charts)
    if ttl > 0:
        raw = await cache_client.cache_get(ckey)
        if raw:
            try:
                stored = json.loads(raw)
            except ValueError:
                stored = None
            if isinstance(stored, dict):
                return {**stored, **extras, "cached": True, "cost_usd": 0.0}

    await check_cap(session, user, float(extras["estimate_usd"]), now)
    try:
        result = await asyncio.to_thread(_call, reader, instrument=instrument, charts=charts, facts=facts, strategy=strategy,
                                         settings=settings, key=key)
    except Exception as exc:  # noqa: BLE001 - the reader or the API failed; its text is never logged
        logger.warning("chart read failed (%s) for tenant %s user %s", exc.__class__.__name__, user.tenant_id, user.id)
        await _log(session, user, settings, None, f"error: {exc.__class__.__name__}", 0.0, loader.version())
        raise refused(502, "read_failed", FAILED) from None
    if not isinstance(result, dict):
        await _log(session, user, settings, None, "error: not a result", 0.0, loader.version())
        raise refused(502, "read_failed", FAILED)
    cost = cost_usd(settings, result)
    status = str(result.get("status") or "error")
    await _log(session, user, settings, result, status if status == "ok" else f"{status[:20]}", cost,
               str(result.get("prompt_version") or loader.version()))
    logger.info("chart read: tenant %s user %s status %s cost_usd %.6f", user.tenant_id, user.id, status[:20], cost)
    if ttl > 0 and status == "ok":
        await cache_client.cache_set(ckey, json.dumps(result, default=str), ttl)
    return {**result, **extras, "cached": False, "cost_usd": cost}


__all__ = ["FEATURE", "PLACEHOLDER", "LABEL_LAKE", "LABEL_UPLOAD", "Chart", "api_key", "make_client", "estimate_usd", "cost_usd",
           "canonical", "pair_for", "structure_fingerprint", "facts_text", "cache_key", "strategy_labels", "own_cap_usd",
           "spent_today_usd", "check_cap", "lake_charts", "upload_chart", "read"]
