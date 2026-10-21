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
Nothing here places, changes or suggests an order (ADR-0006).
"""
import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_role
from app.core.enums import UserRole
from app.db.models import OIAlertLogRecord, OIBannerSettingRecord, User
from app.db.session import get_session
from app.brokers.models import OptionChain
from app.market_data.calendar import family_holidays, market_session_status
from app.market_data.candles_routes import chains_rate_limit, ltp_rate_limit
from app.platform.controls import flag_enabled
from app.option_chain import chain_view, oi_regime, snapshots
from app.option_chain.snapshots import IST

router = APIRouter(prefix="/api/option-chain", tags=["option-chain"])


def _underlying(underlying: str) -> str:
    try:
        return snapshots.normalise_underlying(underlying)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _exchange(session: AsyncSession, tenant_id: int, underlying: str) -> str:
    _, own = await snapshots.settings_rows(session, tenant_id, underlying)
    return own.exchange if own is not None else "NSE"


async def _day_view(session: AsyncSession, user: User, underlying: str, day: Optional[date]):
    now = datetime.now(timezone.utc)
    settings = await snapshots.settings_for(session, user.tenant_id, underlying)
    day = day or now.astimezone(IST).date()
    slots = await snapshots.day_chains(session, underlying, day)
    status = await market_session_status(session, now, await _exchange(session, user.tenant_id, underlying))
    flags = snapshots.staleness(await snapshots.latest_capture(session, underlying), now, settings, status.is_open and day == now.astimezone(IST).date())
    return settings, day, slots, flags


async def _no_byob(session: AsyncSession, user: User) -> None:
    """BT1b: the OI banner is built from platform-wide option-chain reads made through other organisations' broker
    sessions, so with `byob_data_tier` on it is off (409, Tier C) until it runs on the user's own session."""
    from app.market_data import live_access
    if await live_access.byob_on(session, user.tenant_id):
        raise live_access.LiveDataRefused("The OI banner is read from a shared option-chain collector, not from your own broker session, "
                                          "so it is off while the bring-your-own-broker data tier is on.")


async def _banner(session: AsyncSession, user: User, underlying: str) -> Dict[str, Any]:
    settings, day, slots, flags = await _day_view(session, user, underlying, None)
    timeline = snapshots.replay(underlying, slots, settings, day)
    if not timeline:
        return {"underlying": underlying, "date": day.isoformat(), "banner": None, **flags,
                "message": oi_regime.INSUFFICIENT_HISTORY_MESSAGE}
    return {"underlying": underlying, "date": day.isoformat(), "banner": snapshots.entry_json(timeline[-1]), **flags}


@router.get("/banners")
async def banners(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _no_byob(session, user)
    names = sorted(u for t, u, _ in await snapshots.enabled_underlyings(session) if t == user.tenant_id)
    return {"banners": [await _banner(session, user, u) for u in names]}


@router.get("/{underlying}/banner")
async def banner(underlying: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _no_byob(session, user)
    return await _banner(session, user, _underlying(underlying))


@router.get("/{underlying}/history")
async def history(underlying: str, day: Optional[date] = Query(default=None, alias="date"), interval: int = Query(default=5),
                  user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    if interval not in (5, 10, 15):
        raise HTTPException(status_code=422, detail="interval must be 5, 10 or 15")
    await _no_byob(session, user)
    underlying = _underlying(underlying)
    settings, day, slots, flags = await _day_view(session, user, underlying, day)
    timeline = snapshots.replay(underlying, slots, settings, day)
    rows = [snapshots.entry_json(e) for _, e in oi_regime.aggregate_history(timeline, interval, lambda e: e.slot_start.astimezone(IST))]
    return {"underlying": underlying, "date": day.isoformat(), "interval": interval, "rows": rows, **flags}


@router.get("/{underlying}/strikes")
async def strikes(underlying: str, day: Optional[date] = Query(default=None, alias="date"),
                  user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _no_byob(session, user)
    underlying = _underlying(underlying)
    settings, day, slots, flags = await _day_view(session, user, underlying, day)
    base = await snapshots.baselines(session, underlying, day)
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
    default = chain_view.default_expiry([i.day for i in items], today)
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
    if expiry is None:
        holidays = await family_holidays(session, _holiday_family(name))
        items, _ = await chain_view.expiry_list(session, name, today, holidays)
        expiry = chain_view.default_expiry([i.day for i in items], today)
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
    oi = await chain_view.oi_change_state(chain, tenant_id=user.tenant_id, underlying=name, now=now, market_open=status.is_open)
    view = chain_view.build_view(chain, spot=spot, future=future, expiry=expiry or chain_view.parse_expiry(chain.expiry), now=now, oi=oi)
    view["market_open"] = status.is_open
    return view
