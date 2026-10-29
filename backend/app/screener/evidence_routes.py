"""U8 EV1/EV2: the evidence API - the stored pack of a matched symbol, its image, and "send to my Telegram".

GET  /api/screener/evidence/runs/{run_id}            the run's evidence ids by symbol
GET  /api/screener/evidence/{evidence_id}?debug=      the pack (`evidence/1`); failed conditions' drawings only with debug
                                                       + telegram_linked / telegram_reason / telegram_message (SCR-FIX C4)
GET  /api/screener/evidence/{evidence_id}/image.png   the server-rendered chart (theme=light|dark), the Telegram image
POST /api/screener/evidence/{evidence_id}/telegram    the image + caption to the caller's OWN linked private chat; refused
                                                       with 409 {reason, message} - not_linked / bot_not_configured /
                                                       blocked_by_user / send_failed / not_owner (SCR-FIX C4)
GET  /api/screener/evidence/{evidence_id}/replay?at=  EV3 (flag `evidence_context`): the pack recomputed as of an
                                                       earlier bar of its window, from the shared lake's bars

Behind `screener_v2` and `evidence_charts` (both off by default). With `evidence_charts` off every route answers 404 -
the feature is hidden, not advertised. Reads follow `evidence_store.readable` (organisation; with BT1 `byob_data_tier`
on, the user whose broker session read the bars). Informational: nothing here places, proposes or changes an order.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.rate_limit import user_rate_limit
from app.db.models import ScreenRunRecord, User
from app.db.session import get_session
from app.platform.controls import require_flag
from app.screener import evidence_store

router = APIRouter(prefix="/api/screener/evidence", tags=["screener"])
telegram_rate_limit = user_rate_limit("evidence_telegram", limit=10, window_seconds=60)
image_rate_limit = user_rate_limit("evidence_image", limit=30, window_seconds=60)      # rendering is CPU work
replay_rate_limit = user_rate_limit("evidence_replay", limit=60, window_seconds=60)    # replay steps a bar at a time
REPLAY_TFS = ("1m", "3m", "5m", "15m", "30m", "1h", "1d")       # the lake loader's timeframes (no weekly / monthly yet)
REPLAY_NOTE = ("Replayed from the shared market-data history as of this bar - the stored pack's own bars came from the run "
               "and can differ slightly.")
NOT_FOUND = "Evidence not found"


async def _gate(session: AsyncSession, user: User) -> None:
    await require_flag(session, "screener_v2", user.tenant_id)
    if not await evidence_store.enabled(session, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not Found")


async def _row(session: AsyncSession, user: User, evidence_id: int) -> Any:
    await _gate(session, user)
    row = await evidence_store.readable(session, user, evidence_id)
    if row is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return row


@router.get("/runs/{run_id}")
async def run_evidence(run_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _gate(session, user)
    run = await session.get(ScreenRunRecord, run_id)
    if run is None or run.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Run not found")            # another organisation's run: no hint
    if run.user_id != user.id and await evidence_store._byob(session, user):
        raise HTTPException(status_code=404, detail="Run not found")            # BT1b: a run read the runner's own feed
    rows = await evidence_store.for_run(session, user, run_id)
    return {"run_id": run_id, "evidence": {r.symbol: r.id for r in rows}}


@router.get("/{evidence_id}")
async def get_evidence(evidence_id: int, debug: bool = False, user: User = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    row = await _row(session, user, evidence_id)
    pack = evidence_store.load(row)
    # SCR-FIX B6: whether the replay controls show at all (the same checks the replay route refuses with)
    replay_ok = bool(await evidence_store.context_enabled(session, user.tenant_id)) and replay_plan(pack)[0] is None
    return {**evidence_store.view(pack, debug=debug), "evidence_id": row.id, "pack_sha256": row.pack_sha256,
            "can_send": row.user_id == user.id, "replay_available": replay_ok,
            **await evidence_store.telegram_state(session, row, user)}


@router.get("/{evidence_id}/image.png", dependencies=[Depends(image_rate_limit)])
async def evidence_image(evidence_id: int, theme: Literal["light", "dark"] = "light", user: User = Depends(get_current_user),
                         session: AsyncSession = Depends(get_session)) -> Response:
    row = await _row(session, user, evidence_id)
    try:
        png, _caption = await asyncio.to_thread(evidence_store.image_and_caption, row, theme)       # off the event loop
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "private, no-store"})


@router.post("/{evidence_id}/telegram", dependencies=[Depends(telegram_rate_limit)])
async def send_evidence(evidence_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    row = await _row(session, user, evidence_id)
    result = await evidence_store.send_to_own_chat(session, row, user)
    if not result["sent"]:      # SCR-FIX C4: a machine reason code with its plain sentence (the page picks the action)
        raise HTTPException(status_code=409, detail={"reason": result["reason"], "message": result["message"]})
    return result


def _refuse(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


@router.get("/{evidence_id}/replay", dependencies=[Depends(replay_rate_limit)])
async def replay_evidence(evidence_id: int, at: datetime, debug: bool = False, user: User = Depends(get_current_user),
                          session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """U8 EV3: the pack as of an earlier bar of its window (`at` = that bar's close), recomputed by the same builder from
    the shared lake's bars that ended by `at` (the CH3 loader: shared-licensed sources only, corrections point-in-time) -
    so the user can step back and see when each condition became true. Nothing after `at` is read; nothing is stored.
    Refused (422) where a replay could not be the screen's own computation: a cross-sectional screen (its ranks need
    the whole universe), market-zone functions (the zone timeline is not attached here), the U7 LG3 fundamentals
    functions (nor are the point-in-time figures), N3 S8a's ownership and event functions (nor are the patterns and
    events), a weekly / monthly screen, or
    a time outside the stored pack's window. Option-chain conditions show in the strip only (a chain is a snapshot of
    its own time)."""
    row = await _row(session, user, evidence_id)
    if not await evidence_store.context_enabled(session, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not Found")
    if at.tzinfo is None:
        raise _refuse("`at` must carry its time zone (for example 2026-10-09T15:30:00+05:30)")
    at = at.astimezone(timezone.utc)
    pack = evidence_store.load(row)
    base_tf = str(pack["base_tf"])
    refusal, ast, validated, key = replay_plan(pack)
    if refusal is not None or key is None:
        raise _refuse(refusal or "this instrument has no shared history")
    times = [datetime.fromisoformat(t) for t in pack["bars"]["t"]]
    import pandas as pd

    from app.screener.runtime import bar_close
    closes = list(bar_close(pd.DatetimeIndex(times), base_tf))
    if not closes or at < closes[0].to_pydatetime() or at > datetime.fromisoformat(pack["as_of"]["bar_close"]):
        raise _refuse("`at` must be a bar of the pack's window (its first bar's close up to the pack's own bar)")
    from app.charts import price_action_layer as PAL
    start = replay_start(validated, base_tf, len(times), at, key)
    try:
        frame, source = await PAL.load_frame(session, key, base_tf, start, at)
        daily = await replay_daily(session, validated, base_tf, at, key)
    except PAL.WindowTooLarge as exc:
        raise _refuse(str(exc)) from None
    if not len(frame):
        raise HTTPException(status_code=404, detail="no shared history for this instrument and bar")
    replayed = await asyncio.to_thread(_replay_pack, frame, ast, validated, pack, base_tf, at, debug, source, daily)
    return {**evidence_store.view(replayed, debug=debug), "evidence_id": row.id, "can_send": False,
            "replay": {"at": at.isoformat(), "source": source, "note": REPLAY_NOTE, "stored_as_of": pack["as_of"]}}


def replay_plan(pack: Dict[str, Any]) -> Tuple[Optional[str], Any, Any, Optional[str]]:
    """(refusal, ast, validated, lake key): why this pack cannot be replayed (None when it can), from the pack alone, so
    the evidence view says up front whether replay is available (SCR-FIX B6: no replay buttons beside "not available")."""
    base_tf = str(pack["base_tf"])
    if base_tf not in REPLAY_TFS:
        return (f"replay reads {', '.join(REPLAY_TFS)} bars from the shared history; a {base_tf} screen is not replayed yet",
                None, None, None)
    from app.screener import compile_screen, nodes
    from app.screener.evidence import ZONE_CALLS
    ast, validated = compile_screen(pack["screen"]["text"], base_tf=base_tf, params=pack["screen"].get("params") or {})
    if ast is None or not validated.ok:
        return "the stored screen no longer passes validation", None, None, None
    if validated.cross_sectional:
        return "a cross-sectional screen ranks a whole universe - its replay would not be the run's computation", None, None, None
    if any(isinstance(node, nodes.Call) and node.name in ZONE_CALLS for node in nodes.walk(ast)):
        return "market-zone conditions are not replayed yet (the zone timeline is not attached to a replay)", None, None, None
    from app.screener import fundamentals as fundamentals_gate
    if fundamentals_gate.flagged_calls(ast):
        # U7 LG3: the point-in-time fundamentals are not attached to a replay either - refused, never replayed as NaN
        return ("fundamentals conditions are not replayed yet (the point-in-time figures are not attached to a replay)",
                None, None, None)
    from app.screener import ownership as ownership_gate
    if ownership_gate.flagged_calls(ast):
        # N3 S8a: nor are the shareholding patterns and events - refused with the reason, never replayed as NaN
        return ("ownership and event conditions are not replayed yet (the shareholding patterns and events are not "
                "attached to a replay)", None, None, None)
    from app.price_action.zones.store import normalise_key
    try:
        key: Optional[str] = normalise_key(f"{pack.get('exchange') or 'NSE'}:{pack['symbol']}")
    except ValueError:
        key = None
    if key is None or key == "*":
        return "this instrument has no shared history", None, None, None
    return None, ast, validated, key


def daily_need(validated: Any, base_tf: str) -> Dict[str, int]:
    """The daily-or-coarser part of an intraday screen's lookback ({} for a daily screen, or one that reads no daily
    context) - what the run attaches as its "1d" frame (`routes.fetch_frames`, `_attach_daily`)."""
    from app.screener import nodes
    daily = nodes.TF_MINUTES["1d"]
    if nodes.TF_MINUTES.get(base_tf, daily) >= daily:
        return {}
    return {tf: int(b) for tf, b in validated.fetch_lookback.items() if nodes.TF_MINUTES.get(tf, 0) >= daily}


async def replay_daily(session: AsyncSession, validated: Any, base_tf: str, at: datetime, key: str) -> Any:
    """SCR-FIX B6: an intraday screen that reads @1d / @1w / @1M ran on the broker's daily history (its "1d" frame, sized
    by the screen's daily lookback); the replay attaches the lake's daily bars the same way, closed by `at`, so the replay
    equals the run instead of resampling 1d from the short intraday window. None when the screen needs none."""
    need = daily_need(validated, base_tf)
    if not need:
        return None
    from app.charts import price_action_layer as PAL
    from app.screener.routes import daily_fetch_days
    frame, _source = await PAL.load_frame(session, key, "1d", at - timedelta(days=daily_fetch_days(need, "1d") + 1), at)
    return frame if len(frame) else None


def replay_start(validated: Any, base_tf: str, window: int, at: datetime, key: str) -> datetime:
    """The first day a replay reads: the screen's own warm-up (its fetch lookback, as the run sizes it) plus the pack's
    window, before `at` - the same history the run would have had, so the recomputed indicators are the run's."""
    from app.price_action.zones import job
    need = max([int(v) for v in validated.fetch_lookback.values()] or [1]) + int(window)
    return at - timedelta(days=job.warmup_days(base_tf, need, job.exchange_session(key)) + 1)


def _bars(frame: Any) -> Any:
    """A lake frame (timestamp, bar_end and OHLCV columns) -> the screen's bars (OHLCV indexed by the bar start, UTC)."""
    import pandas as pd
    df = frame.reset_index(drop=True)
    bars = pd.DataFrame({k: df[k].astype(float) for k in ("open", "high", "low", "close")})
    bars["volume"] = df["volume"].astype(float) if "volume" in df.columns else 0.0
    bars.index = pd.DatetimeIndex(pd.to_datetime(df["timestamp"], utc=True), name="timestamp")
    return bars


def _replay_pack(frame: Any, ast: Any, validated: Any, pack: Dict[str, Any], base_tf: str, at: datetime, debug: bool,
                 source: Optional[str], daily: Any = None) -> Dict[str, Any]:
    from app.screener import evidence as ev
    from app.screener.runtime import SymbolData
    frames = {base_tf: _bars(frame)}
    if daily is not None and len(daily):
        frames["1d"] = _bars(daily)              # SCR-FIX B6: the daily context the run read, not 1d from the intraday window
    data = SymbolData(str(pack["symbol"]), frames, exchange=pack.get("exchange") or "NSE")
    return ev.build_pack(ast, validated, data, base_tf=base_tf, params=pack["screen"].get("params") or {}, as_of=at,
                         window=len(pack["bars"]["t"]), debug=True, exchange=pack.get("exchange") or "NSE",
                         data_source=f"replay:{source}" if source else "replay", screen_name=pack["screen"].get("name"), context=True)


__all__ = ["router"]
