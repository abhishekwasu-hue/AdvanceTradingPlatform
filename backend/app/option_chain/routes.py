"""OI Banner O2: the history API (read-only; numbers carry their data timestamps).

* `GET /api/option-chain/banners`                the tenant's enabled underlyings with their current banner (dashboard)
* `GET /api/option-chain/{underlying}/banner`   the latest slot's banner, with stale / market-closed flags
* `GET /api/option-chain/{underlying}/history`  the day's banner timeline (`interval` 5, 10 or 15 minutes, newest first)
* `GET /api/option-chain/{underlying}/strikes`  per-strike call/put OI through the day (the window of the latest slot)
* `GET /api/option-chain/{underlying}/alerts` the tenant's alert decisions today (sent or held back, with the reason);
  `POST .../alerts/snooze` (minutes) and `POST .../alerts/mute-today` pause them - read-only actions, never an order
  `POST .../alerts/test` sends one test alert through the organisation's channels
* `GET /api/option-chain/{underlying}/settings` and `PUT` (owner) - the tenant's settings for the underlying; "*" is
  the tenant default for every underlying. `enabled` asks the collector to follow the underlying.
* OC1 (the automatic Option chain page, app/option_chain/chain_view.py):
  `GET /api/option-chain/{underlying}/expiries` the next expiries (IST) and the default one (the next on an expiry day);
  `GET /api/option-chain/{underlying}/live?expiry=` the broker's chain through the caller's own session (the same read as
  `POST /api/market-data/option-chains`), the spot and near-month future prices, the summary (ATM, PCR, max pain,
  ATM IV, days to expiry, highest OI strikes), IV and Greeks per leg and the OI-change state. Reads only. Both are
  behind the `ui_v2` flag (404 while it is off), like the other v2-only reads (app/signals_feed/routes.py).

Every reading is recomputed from the stored strikes with the caller's tenant settings (app/option_chain/snapshots.py).
B1: with the own-broker data tier on, the banner / history / strikes reads use the caller's OWN session snapshots
(app/option_chain/own_snapshots.py; stored by the Option chain analyses read) - empty until his first read.
Nothing here places, changes or suggests an order (ADR-0006).
"""
import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_role
from app.brokers.models import OptionChain
from app.core.enums import UserRole
from app.db.models import OIAlertLogRecord, OIBannerSettingRecord, User
from app.db.session import get_session
from app.market_data.calendar import family_holidays, market_session_status
from app.market_data.candles_routes import chains_rate_limit, ltp_rate_limit
from app.market_lake import chain_store
from app.option_chain import as_of, chain_view, oi_regime, own_snapshots, snapshots
from app.option_chain.snapshots import IST
from app.platform.controls import flag_enabled

router = APIRouter(prefix="/api/option-chain", tags=["option-chain"])


def _underlying(underlying: str) -> str:
    try:
        return snapshots.normalise_underlying(underlying)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _exchange(session: AsyncSession, tenant_id: int, underlying: str) -> str:
    _, own = await snapshots.settings_rows(session, tenant_id, underlying)
    return own.exchange if own is not None else "NSE"


async def _byob(session: AsyncSession, user: User) -> bool:
    from app.market_data import live_access
    return await live_access.byob_on(session, user.tenant_id)


async def _day_view(session: AsyncSession, user: User, underlying: str, day: Optional[date]):
    """(settings, day, slots, flags). With the own-broker tier on (B1) the slots are the user's OWN stored chain reads
    (app/option_chain/own_snapshots.py), never the organisation's collector slots."""
    now = datetime.now(timezone.utc)
    settings = await snapshots.settings_for(session, user.tenant_id, underlying)
    day = day or now.astimezone(IST).date()
    if await _byob(session, user):
        slots = own_snapshots.one_expiry(await own_snapshots.day(session, user, underlying, day), day)
        latest = slots[-1].captured_at if slots else None
    else:
        slots = await snapshots.day_chains(session, underlying, day, tenant_id=user.tenant_id)
        latest = await snapshots.latest_capture(session, underlying, tenant_id=user.tenant_id)
    status = await market_session_status(session, now, await _exchange(session, user.tenant_id, underlying))
    flags = snapshots.staleness(latest, now, settings, status.is_open and day == now.astimezone(IST).date())
    return settings, day, slots, flags


async def _no_byob(session: AsyncSession, user: User) -> None:
    """BT1b: the OI collector's settings and alerts follow the organisation's collector, which skips an organisation with
    `byob_data_tier` on, so they are off (409, Tier C) there. The banner, history and strike reads are not refused: with
    the tier on they are built from the user's own session snapshots (B1, `_day_view`)."""
    from app.market_data import live_access
    if await live_access.byob_on(session, user.tenant_id):
        raise live_access.LiveDataRefused("The OI banner is read from a shared option-chain collector, not from your own broker session, "
                                          "so it is off while the bring-your-own-broker data tier is on.")


async def _collector_day_views(session: AsyncSession, user: User, underlyings: List[str]) -> Dict[str, Tuple[Any, date, Any, Dict[str, Any]]]:
    """AUD-G2.1: `_day_view` (today, the organisation's collector slots - not the own-broker tier) for several underlyings
    in a fixed number of queries: the dashboard banners read every enabled underlying at once."""
    if not underlyings:
        return {}
    now = datetime.now(timezone.utc)
    day = now.astimezone(IST).date()
    rows = await snapshots.settings_rows_many(session, user.tenant_id, underlyings)
    slots = await snapshots.day_chains_many(session, underlyings, day, tenant_id=user.tenant_id)
    latest = await snapshots.latest_captures(session, underlyings, tenant_id=user.tenant_id)
    statuses: Dict[str, Any] = {}
    out: Dict[str, Tuple[Any, date, Any, Dict[str, Any]]] = {}
    for u in rows:
        default_row, own = rows[u]
        settings = snapshots.resolve_settings(user.tenant_id, u, default_row, own)
        exchange = own.exchange if own is not None else "NSE"
        if exchange not in statuses:
            statuses[exchange] = await market_session_status(session, now, exchange)
        flags = snapshots.staleness(latest[u], now, settings, statuses[exchange].is_open and day == now.astimezone(IST).date())
        out[u] = (settings, day, slots[u], flags)
    return out


async def _banner(session: AsyncSession, user: User, underlying: str) -> Dict[str, Any]:
    return _banner_from(underlying, *await _day_view(session, user, underlying, None))


def _banner_from(underlying: str, settings: Any, day: date, slots: Any, flags: Dict[str, Any]) -> Dict[str, Any]:
    timeline = snapshots.replay(underlying, slots, settings, day)
    if not timeline:
        return {"underlying": underlying, "date": day.isoformat(), "banner": None, **flags,
                "message": oi_regime.INSUFFICIENT_HISTORY_MESSAGE}
    return {"underlying": underlying, "date": day.isoformat(), "banner": snapshots.entry_json(timeline[-1]), **flags}


@router.get("/banners")
async def banners(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    if await _byob(session, user):              # B1: the underlyings of the user's own chain reads today
        names = await chain_store.holder_underlyings(session, user.tenant_id, user.id, own_snapshots.today(datetime.now(timezone.utc)))
        return {"banners": [await _banner(session, user, u) for u in names]}
    names = sorted(u for t, u, _ in await snapshots.enabled_underlyings(session, tenant_id=user.tenant_id) if t == user.tenant_id)
    views = await _collector_day_views(session, user, names)      # AUD-G2.1: one batch, not ~6 queries per underlying
    return {"banners": [_banner_from(u, *views[u]) for u in names]}


@router.get("/{underlying}/banner")
async def banner(underlying: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    return await _banner(session, user, _underlying(underlying))


@router.get("/{underlying}/history")
async def history(underlying: str, day: Optional[date] = Query(default=None, alias="date"), interval: int = Query(default=5),
                  user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    if interval not in (5, 10, 15):
        raise HTTPException(status_code=422, detail="interval must be 5, 10 or 15")
    underlying = _underlying(underlying)
    settings, day, slots, flags = await _day_view(session, user, underlying, day)
    timeline = snapshots.replay(underlying, slots, settings, day)
    # M61 PCR-1: every row says whether it is a session reading (calendar-aware) and whether it is partial.
    exchange = await _exchange(session, user.tenant_id, underlying)
    marks = snapshots.session_marks(slots, await family_holidays(session, exchange), exchange)
    rows = [{**snapshots.entry_json(e), **marks.get(e.slot_start, {})}
            for _, e in oi_regime.aggregate_history(timeline, interval, lambda e: e.slot_start.astimezone(IST))]
    return {"underlying": underlying, "date": day.isoformat(), "interval": interval, "rows": rows, **flags}


@router.get("/{underlying}/strikes")
async def strikes(underlying: str, day: Optional[date] = Query(default=None, alias="date"),
                  user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    underlying = _underlying(underlying)
    settings, day, slots, flags = await _day_view(session, user, underlying, day)
    if await _byob(session, user):              # B1: the first OI of the user's own reads today
        base = own_snapshots.baselines(slots)
    else:
        base = await snapshots.baselines(session, underlying, day, tenant_id=user.tenant_id)
    series = snapshots.strike_series(slots, settings)
    for s in series:
        call0, put0 = base.get(s["strike"], (None, None))
        s["baseline_call_oi"], s["baseline_put_oi"] = call0, put0
    return {"underlying": underlying, "date": day.isoformat(), "strikes": series,
            "data_as_of": slots[-1].captured_at.isoformat() if slots else None, **flags}


class SettingsBody(BaseModel):
    enabled: Optional[bool] = None
    exchange: Optional[str] = Field(default=None, min_length=2, max_length=10)
    overrides: Optional[Dict[str, Any]] = None


def _settings_json(row: Optional[OIBannerSettingRecord], effective: oi_regime.OIRegimeSettings) -> Dict[str, Any]:
    return {"enabled": bool(row.enabled) if row else False, "exchange": row.exchange if row else "NSE",
            "overrides": json.loads(row.overrides or "{}") if row else {}, "effective": effective.model_dump(),
            "updated_at": row.updated_at.isoformat() if row and row.updated_at else None}


@router.get("/{underlying}/settings")
async def get_settings(underlying: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _no_byob(session, user)
    key = snapshots.DEFAULT_KEY if underlying == snapshots.DEFAULT_KEY else _underlying(underlying)
    default_row, own = await snapshots.settings_rows(session, user.tenant_id, key)
    row = default_row if key == snapshots.DEFAULT_KEY else own
    return {"underlying": key, **_settings_json(row, await snapshots.settings_for(session, user.tenant_id, key))}


@router.put("/{underlying}/settings")
async def put_settings(underlying: str, body: SettingsBody, user: User = Depends(require_role(UserRole.OWNER)),
                       session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _no_byob(session, user)
    key = snapshots.DEFAULT_KEY if underlying == snapshots.DEFAULT_KEY else _underlying(underlying)
    default_row, own = await snapshots.settings_rows(session, user.tenant_id, key)
    row = default_row if key == snapshots.DEFAULT_KEY else own
    overrides = body.overrides if body.overrides is not None else (json.loads(row.overrides or "{}") if row else {})
    layers = [snapshots.platform_layer()] + ([] if key == snapshots.DEFAULT_KEY else [json.loads(default_row.overrides or "{}") if default_row else {}])
    try:
        oi_regime.resolve_settings(*layers, overrides)                    # refuse an invalid override before storing it
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if row is None:
        row = OIBannerSettingRecord(tenant_id=user.tenant_id, underlying=key)
        session.add(row)
    if body.enabled is not None:
        row.enabled = body.enabled and key != snapshots.DEFAULT_KEY      # the collector follows named underlyings only
    if body.exchange is not None:
        row.exchange = body.exchange.upper()
    row.overrides = json.dumps(overrides, sort_keys=True)
    row.updated_by, row.updated_at = user.id, datetime.now(timezone.utc)
    await session.commit()
    return {"underlying": key, **_settings_json(row, await snapshots.settings_for(session, user.tenant_id, key))}


# Pausing the organisation's OI alerts is for members who trade or build strategies, not for viewers / support.
# Z10-c: and it is an alert setting, so a member the owner denied `alerts:write` cannot (nor in a suspended organisation).
_member = require_role(UserRole.OWNER, UserRole.USER, UserRole.STRATEGY_CREATOR, active_tenant=True, scope="alerts:write")


class SnoozeBody(BaseModel):
    minutes: int = Field(default=60, ge=1, le=24 * 60)


async def _snooze_until(session: AsyncSession, user: User, underlying: str, until: Optional[datetime]) -> Dict[str, Any]:
    _, own = await snapshots.settings_rows(session, user.tenant_id, underlying)
    if own is None:
        own = OIBannerSettingRecord(tenant_id=user.tenant_id, underlying=underlying, overrides="{}")
        session.add(own)
    own.snoozed_until, own.updated_by, own.updated_at = until, user.id, datetime.now(timezone.utc)
    await session.commit()
    return {"underlying": underlying, "snoozed_until": until.isoformat() if until else None}


@router.post("/{underlying}/alerts/snooze")
async def snooze(underlying: str, body: SnoozeBody, user: User = Depends(_member), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    return await _snooze_until(session, user, _underlying(underlying), datetime.now(timezone.utc) + timedelta(minutes=body.minutes))


@router.post("/{underlying}/alerts/mute-today")
async def mute_today(underlying: str, user: User = Depends(_member), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    tomorrow = datetime.now(timezone.utc).astimezone(IST).date() + timedelta(days=1)
    return await _snooze_until(session, user, _underlying(underlying), datetime.combine(tomorrow, time(), IST).astimezone(timezone.utc))


@router.post("/{underlying}/alerts/resume")
async def resume(underlying: str, user: User = Depends(_member), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    return await _snooze_until(session, user, _underlying(underlying), None)


@router.get("/{underlying}/alerts")
async def alert_log(underlying: str, day: Optional[date] = Query(default=None, alias="date"), user: User = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _no_byob(session, user)
    underlying = _underlying(underlying)
    day = day or datetime.now(timezone.utc).astimezone(IST).date()
    since = datetime.combine(day, time(), IST).astimezone(timezone.utc)
    rows = await session.scalars(select(OIAlertLogRecord).where(OIAlertLogRecord.tenant_id == user.tenant_id, OIAlertLogRecord.underlying == underlying,
                                                                OIAlertLogRecord.created_at >= since, OIAlertLogRecord.created_at < since + timedelta(days=1))
                                 .order_by(OIAlertLogRecord.created_at.desc()).limit(200))
    _, own = await snapshots.settings_rows(session, user.tenant_id, underlying)
    return {"underlying": underlying, "snoozed_until": own.snoozed_until.isoformat() if own and own.snoozed_until else None,
            "alerts": [{"alert_type": r.alert_type, "old_state": r.old_state, "new_state": r.new_state, "status": r.status,
                        "slot": r.slot_start.isoformat(), "at": r.created_at.isoformat(), "notification_id": r.notification_id} for r in rows]}


@router.post("/{underlying}/alerts/test")
async def test_alert(underlying: str, user: User = Depends(_member), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Sends one test alert through the organisation's channels (the normal severity floors apply; WARNING)."""
    from app.option_chain import oi_alerts
    record = await oi_alerts.send_test(session, user.tenant_id, _underlying(underlying))
    return {"notification_id": record.id}


# ---------------------------------------------------------------------------------------------------- OC1

def _symbol(underlying: str) -> str:
    try:
        return chain_view.underlying_name(underlying)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


UI_V2_FLAG = "ui_v2"


async def _v2_only(session: AsyncSession, user: User) -> None:
    """OC1: the v2 page's reads exist only while `ui_v2` is on for the organisation (404 otherwise)."""
    if not await flag_enabled(session, UI_V2_FLAG, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": UI_V2_FLAG})


def _holiday_family(underlying: str) -> str:
    """The holiday calendar of the exchange the underlying's options trade on (BSE for SENSEX, else NSE)."""
    return chain_view.spot_instrument(underlying)[1]


@router.get("/{underlying}/expiries")
async def expiries(underlying: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """OC1: the underlying's next expiries (IST dates) and the one the page opens on."""
    await _v2_only(session, user)
    name = _symbol(underlying)
    today = chain_view.today_ist()
    holidays = await family_holidays(session, _holiday_family(name))
    items, source = await chain_view.expiry_list(session, name, today, holidays)
    default = chain_view.default_expiry([i.day for i in items], today,
                                        next_on_expiry_day=await chain_view.next_on_expiry_day(session, user))
    return {"underlying": name, "today": today.isoformat(), "expiries": [i.as_json() for i in items],
            "default": default.isoformat() if default else None, "source": source}


async def _last_price(symbol: str, exchange: str, source: Dict[str, Any], user: User, session: AsyncSession) -> Optional[float]:
    """The broker's last price through the same session as the chain; None when it cannot be read (never a guess)."""
    from app.market_data.candles_routes import ltp as ltp_read
    try:
        body = await ltp_read(symbol=symbol, exchange=exchange, broker=source.get("broker"),
                              account_label=source.get("account_label") or "primary", user=user, session=session)
    except HTTPException:
        return None
    except Exception:  # noqa: BLE001 - a missing price leaves its card empty; the chain still shows
        return None
    value = body.get("ltp") if isinstance(body, dict) else None
    return float(value) if isinstance(value, (int, float)) and value > 0 else None


# Rate limits: one chain read (`market_data_chains`) and one `market_data_ltp` token per poll - the spot and the future
# are two price reads of the same poll, each also drawing from the broker session's own API budget (api_budget).
@router.get("/{underlying}/live", dependencies=[Depends(chains_rate_limit), Depends(ltp_rate_limit)])
async def live(underlying: str, expiry: Optional[date] = Query(default=None), user: User = Depends(get_current_user),
               session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """OC1: one chain ready to show. The chain comes through `POST /api/market-data/option-chains` (the caller's own
    broker session, its entitlement and budget); 409 when no broker session can serve it."""
    from app.market_data.candles_routes import ChainsBody, option_chains
    await _v2_only(session, user)
    name = _symbol(underlying)
    now = datetime.now(timezone.utc)
    today = chain_view.today_ist(now)
    if expiry is not None and expiry < today:
        raise HTTPException(status_code=422, detail="That expiry has passed. Pick today's or a later expiry.")
    if expiry is None:
        holidays = await family_holidays(session, _holiday_family(name))
        items, _ = await chain_view.expiry_list(session, name, today, holidays)
        expiry = chain_view.default_expiry([i.day for i in items], today,
                                           next_on_expiry_day=await chain_view.next_on_expiry_day(session, user))
    fetched = await option_chains(ChainsBody(underlyings=[name], expiry=expiry), user=user, session=session)
    entry = fetched["symbols"].get(name) or {}
    if not entry.get("chain"):
        raise HTTPException(status_code=502, detail=f"Your broker sent no option chain for {name}.")
    chain = OptionChain.model_validate(entry["chain"])
    source = fetched.get("source") or {}
    spot_symbol, spot_exchange = chain_view.spot_instrument(name)
    spot = chain.underlying_ltp if chain.underlying_ltp and chain.underlying_ltp > 0 else await _last_price(spot_symbol, spot_exchange, source, user, session)
    contract = await chain_view.near_future(session, name, today)
    future = None
    if contract is not None:
        future = {"symbol": contract.symbol, "expiry": contract.expiry.isoformat(),
                  "ltp": await _last_price(contract.symbol, contract.exchange, source, user, session)}
    status = await market_session_status(session, now, spot_exchange)
    # B2: the chain's capture time, or the last session close while closed; a closed market's missing or strike-rounded
    # spot is the index's last close from our own candles ("Spot: last close").
    when = await as_of.chain_as_of(session, name, now)
    spot, spot_source = await as_of.resolve_spot(user, session, name, chain, spot, when, source.get("broker"),
                                                 source.get("account_label") or "primary")
    oi = await chain_view.oi_change_state(chain, tenant_id=user.tenant_id, underlying=name, now=now, market_open=status.is_open)
    closed = await family_holidays(session, _holiday_family(name))          # B3: days to expiry in trading days
    view = chain_view.build_view(chain, spot=spot, future=future, expiry=expiry or chain_view.parse_expiry(chain.expiry), now=now, oi=oi,
                                 holidays=closed)
    view["market_open"] = status.is_open
    view["summary"]["spot_source"] = spot_source
    view["data_as_of"] = when.at.isoformat()
    if await _byob(session, user):              # B1: his own read becomes his session snapshot (same 5-minute slots)
        await _store_own_read(session, user, name, chain, source, now)
    return view


async def _store_own_read(session: AsyncSession, user: User, name: str, chain: OptionChain, source: Dict[str, Any], now: datetime) -> None:
    """Stores the live view's chain as the user's own session snapshot - only when the session that served it is his own
    credential (same broker and account label)."""
    from app.market_data import live_access
    record = await live_access.own_session_record(session, user, now)
    if record is None or record.broker_name != source.get("broker") or record.account_label != source.get("account_label"):
        return
    await own_snapshots.record(session, user, record, snapshots.normalise_underlying(name), chain, now)
