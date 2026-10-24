"""UI2-4a1: the caller's own UI preferences, stored on the server per user so phone and desktop agree.

* `GET /api/me/prefs` returns every namespace: the stored value merged over the namespace's defaults, and its version
  (0 = never saved, the defaults).
* `PUT /api/me/prefs/{namespace}` replaces one namespace with the JSON body, validated against that namespace's schema
  (unknown keys and wrong types are refused) and capped at MAX_BODY_BYTES. An optional `?version=` makes it a
  compare-and-set: a stale version gets 409 with the current value; without it the save simply replaces.
* Namespaces are an allow-list (NAMESPACES). `chart` is the first; `setup` (UI2-7, UI-8) holds the setup wizard's own
  marks - skips, the chip's dismissal, the first automatic opening - and (P3) the Dashboard's Get started card's marks.
  Whether a step is done is never stored: app/me/setup.py works it out on every read from the rows that prove it.
  `oc_analyses` (OC3) holds the user's saved Option chain analyses ("My analyses"): a name and a validated analysis spec
  each (app/option_chain/ask_spec.py), at most MAX_SAVED_ANALYSES, sized so the most a user can save stays inside
  MAX_BODY_BYTES (no table, no migration).
  `fno_baskets` (FNO-2) holds the user's saved F&O baskets (Save / Load on the basket panel): a name, the underlying,
  MIS / NRML and the legs (expiry, strike, CE / PE - or FUT with no strike, FNO-3 - Buy / Sell, lots, the planning price and
  a sold leg's or a future's stop), at most MAX_SAVED_BASKETS of at most MAX_BASKET_LEGS legs, sized so the most a user
  can save stays inside MAX_BODY_BYTES. A saved basket is a plan: loading it places nothing.
  `appearance` holds the text size (Normal / Large / Extra large) applied to every page.
  `tours` (MO2 part C) holds the first-time guidance marks: the welcome tour finished, the pages whose tips were seen and
  "Skip all tips". Only marks: what a tour says lives in the frontend (src/tours/content/).
  `trading_defaults` (Settings redesign part C, docs/design/TRADING_DEFAULTS.md) holds "My trading defaults": values that
  PRE-FILL a new paper bot, the full deployment form and the chain order ticket in the frontend, each marked "from your
  defaults" and changeable there. Only existing deployment / ExitRules / ticket fields, with the same limits; every field
  is empty (None) by default. The server never reads it to trade: no order path, worker or risk check imports it. Never
  here: the mode (PAPER / LIVE), broker routing, the LIVE order style, or any LIVE field.
  `daily_rhythm` (Master Order 4) holds whether the daily digests also go to the user's own Telegram chat (OFF by
  default) and which of the four; the worker's "already sent" marks live in the internal row `daily_rhythm_sent`, which
  is not a namespace here (no route reads or writes it).
  `fno` (FNO-8B-28, owner decision 23 Oct) holds the user's own liquidity limits for the F&O confirm step ("max bid-ask
  spread %", "min open interest"), OFF by default. While they are on, the "Before you place" liquidity line also says
  whether the strike is inside his own limits. Information only: the frontend reads it to word that line; no order path,
  worker or risk check imports it, it never holds back an order and exits are never touched.
  `smart_execution` (terminal master Part 7) holds the user's own numbers for the smart-execution rules: when the
  physical-settlement warning starts and how near the money counts, whether it also goes to Telegram, and the protection
  band of a marketable limit where a broker takes no market orders. Read by `app/execution/smart_execution.py`.
  `notices` (TT12-1) holds when a one-time notice was seen (app/me/notices.py), so it never shows twice on any device.
  `option_chain` (B4, UI-H bug lane) holds his own Option chain settings: the PCR zone edges that word "What the chain
  shows" (defaults = the values used before). Display words only.
* Per user and tenant: every read and write filters on both, and nothing in the URL names another user, so another
  member or organisation can neither read nor change it. Any role (a VIEWER too) may change their own. Not behind a
  flag: these are the user's own display settings, nothing is computed from them, and they place nothing.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any, Dict, List, Literal, Optional, Tuple, Type

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import User, UserPrefsRecord
from app.db.session import get_session
from app.option_chain.ask_spec import AnalysisSpec

MAX_BODY_BYTES = 16 * 1024
HEX = r"^#[0-9a-fA-F]{6}$"


class _Prefs(BaseModel):
    # strict: "true" is not a boolean and 1 is not a theme; extra keys are refused, so a typo never saves silently
    model_config = ConfigDict(extra="forbid", strict=True)


class CandleColors(_Prefs):
    """A custom up / down candle colour pair (`#rrggbb`). Absent = the theme's own colours."""
    up: str = Field(pattern=HEX)
    down: str = Field(pattern=HEX)

    @model_validator(mode="after")
    def _distinct(self) -> "CandleColors":
        if self.up.lower() == self.down.lower():
            raise ValueError("up and down candles need different colours")
        return self


class ChartIndicators(_Prefs):
    """The panes a chart opens with (the owner's brief: volume and RSI on)."""
    volume: bool = True
    rsi: bool = True


class ChartPrefs(_Prefs):
    """`chart`: one setting place for every chart (both chart tabs, Signals, Positions, the Option chain)."""
    theme: Literal["app", "light", "dark"] = Field(default="app", description="app = follow the app theme (which follows the device)")
    grid: bool = False
    candle_colors: Optional[CandleColors] = None
    indicators: ChartIndicators = Field(default_factory=ChartIndicators)
    reader_overlay: bool = Field(default=False, description="the 'Reader zones & lines' overlay")


EARLIEST_MARK = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _mark_time(value: Optional[datetime]) -> Optional[datetime]:
    """A setup mark's time: with a time zone, from 2020-01-01 to one day from now (a clock a little ahead is fine)."""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("the time needs a time zone")
    if not EARLIEST_MARK <= value <= datetime.now(timezone.utc) + timedelta(days=1):
        raise ValueError("the time must be between 2020-01-01 and tomorrow")
    return value


MarkTime = Annotated[Optional[datetime], AfterValidator(_mark_time)]


class SetupStepMark(_Prefs):
    """One setup step's own mark. Only the skip is stored; "done" is worked out on every read (app/me/setup.py)."""
    skipped_at: MarkTime = Field(default=None, description="when 'Skip for now' was chosen; null = not skipped")


class SetupSteps(_Prefs):
    broker: SetupStepMark = Field(default_factory=SetupStepMark)
    risk: SetupStepMark = Field(default_factory=SetupStepMark)
    strategy: SetupStepMark = Field(default_factory=SetupStepMark)
    alerts: SetupStepMark = Field(default_factory=SetupStepMark)


class SetupPrefs(_Prefs):
    """`setup` (UI v2 §11, UI-8): the setup wizard's marks, so it resumes on any device. localStorage is only a cache."""
    steps: SetupSteps = Field(default_factory=SetupSteps)
    opened_at: MarkTime = Field(default=None, description="the wizard was first opened (P3: nothing opens it by itself any more)")
    dismissed_at: MarkTime = Field(default=None, description="the dashboard's Setup chip was closed")
    # P3: the Dashboard's "Get started" card (app/me/setup.py get_started_view). Hidden for good once its three steps are
    # done or the trader hides it; Help -> "Show Get started" clears the mark (and sets `get_started_shown_at` when every
    # step is already done, so the card shows its three ticks until it is hidden again).
    get_started_hidden_at: MarkTime = Field(default=None, description="the Get started card was hidden for good")
    get_started_shown_at: MarkTime = Field(default=None, description="the Get started card was brought back from Help with every step done")


MAX_SAVED_ANALYSES = 8
ANALYSIS_ID = r"^[a-z0-9]{4,16}$"
ANALYSIS_NAME = r"^[A-Za-z0-9][A-Za-z0-9 %()+\-/.,:<>=]{0,39}$"


class SavedAnalysis(_Prefs):
    """One saved analysis: the user's own name for the tab and the spec, validated exactly as `/api/option-chain/eval`
    validates it (an expression outside the whitelist is refused here too)."""
    name: str = Field(pattern=ANALYSIS_NAME, max_length=40)
    spec: AnalysisSpec
    saved_at: MarkTime = Field(default=None)


class OcAnalysesPrefs(_Prefs):
    """`oc_analyses` (OC3): the user's saved Option chain analyses, by id in tab order. Evaluating one costs no AI."""
    items: Dict[Annotated[str, StringConstraints(pattern=ANALYSIS_ID)], SavedAnalysis] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _at_most(self) -> "OcAnalysesPrefs":
        if len(self.items) > MAX_SAVED_ANALYSES:
            raise ValueError(f"at most {MAX_SAVED_ANALYSES} saved analyses; delete one first")
        return self


MAX_SAVED_BASKETS = 8
MAX_BASKET_LEGS = 12                                     # the basket panel's own limit (app/paper/fno_basket.py)
BASKET_ID = r"^[a-z0-9]{4,16}$"
BASKET_NAME = r"^[A-Za-z0-9][A-Za-z0-9 &()+\-/.,:]{0,39}$"
BASKET_SYMBOL = r"^[A-Z0-9&_\- ]{1,30}$"
MAX_PRICE = 1e7


class SavedBasketLeg(_Prefs):
    expiry: date
    strike: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)   # None for a future (FNO-3)
    right: Literal["CE", "PE", "FUT"]
    side: Literal["BUY", "SELL"]
    lots: int = Field(ge=1, le=500)
    price: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)
    stop_loss: Optional[float] = Field(default=None, gt=0, lt=MAX_PRICE, allow_inf_nan=False)

    @model_validator(mode="after")
    def _strike_for_options_only(self) -> "SavedBasketLeg":
        if (self.right == "FUT") != (self.strike is None):
            raise ValueError("an option needs its strike; a future has none")
        return self


class SavedBasket(_Prefs):
    name: str = Field(pattern=BASKET_NAME, max_length=40)
    underlying: str = Field(pattern=BASKET_SYMBOL)
    product: Literal["MIS", "NRML"] = "MIS"
    legs: List[SavedBasketLeg] = Field(min_length=1, max_length=MAX_BASKET_LEGS)
    saved_at: MarkTime = Field(default=None)


class FnoBasketsPrefs(_Prefs):
    """`fno_baskets` (FNO-2): the user's saved baskets, by id. Loading one fills the basket panel; it places nothing."""
    items: Dict[Annotated[str, StringConstraints(pattern=BASKET_ID)], SavedBasket] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _at_most(self) -> "FnoBasketsPrefs":
        if len(self.items) > MAX_SAVED_BASKETS:
            raise ValueError(f"at most {MAX_SAVED_BASKETS} saved baskets; delete one first")
        return self


class AppearancePrefs(_Prefs):
    """`appearance` (handover item 1): the text size every page uses, so phone and desktop agree. One root scale on the
    page (frontend `src/theme.ts`): normal = 100 %, large = 112.5 %, xlarge = 125 %."""
    text_size: Literal["normal", "large", "xlarge"] = "normal"


# MO2 part C: the pages that have a page tour (frontend src/tours/pages.ts TOUR_IDS, kept equal by a test). An allow-list,
# so a stored mark is always one of these and the namespace stays small.
TOUR_IDS: Tuple[str, ...] = ("dashboard", "broker", "charts", "option-chain", "signals", "positions", "portfolio", "risk", "settings", "copilot")
TourId = Literal["dashboard", "broker", "charts", "option-chain", "signals", "positions", "portfolio", "risk", "settings", "copilot"]


def _required_mark(value: datetime) -> datetime:
    """A seen-page mark: the same time-zone and range rules as every other mark (never None: the type refuses it)."""
    return _mark_time(value) or value


class ToursPrefs(_Prefs):
    """`tours` (MO2 part C): the first-time guidance marks, so a tip seen on the phone is not shown again on the desktop.
    localStorage is only a paint cache. "Show all tips again" (Settings -> Help) clears all three."""
    welcome_done_at: MarkTime = Field(default=None, description="the welcome tour was finished or closed")
    skip_all_at: MarkTime = Field(default=None, description="'Skip all tips': no tour starts by itself (the ? button still replays)")
    seen: Dict[TourId, Annotated[datetime, AfterValidator(_required_mark)]] = Field(
        default_factory=dict, description="page tour id -> when its tips were finished or closed")


# Settings redesign part C: "My trading defaults" - a pre-fill, never a hidden rule. The limits are the deployment API's
# own (app/deployments/routes.py ContractRulesRequest, ExitRulesRequest) and the chain ticket's (app/paper/fno_ticket.py),
# kept equal by a test. Every field is None by default: an empty namespace pre-fills nothing.
class ExitDefaults(_Prefs):
    """The user's own exit settings (ExitRules). The time exits are available but empty unless the user sets them (SR-10)."""
    trailing_stop_pct: Optional[float] = Field(default=None, gt=0, le=50, allow_inf_nan=False)
    break_even_at_r: Optional[float] = Field(default=None, gt=0, le=10, allow_inf_nan=False)
    time_exit_minutes: Optional[int] = Field(default=None, ge=1, le=375)
    time_exit_at: Optional[str] = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class TicketDefaults(_Prefs):
    """The chain order ticket's starting lots, product and order type (frontend src/fno/ticketModel.ts newDraft)."""
    lots: Optional[int] = Field(default=None, ge=1, le=500)
    product: Optional[Literal["MIS", "NRML"]] = None
    order_type: Optional[Literal["MARKET", "LIMIT", "SL", "SL-M"]] = None


OptionStrategyName = Literal["SINGLE", "BULL_PUT_SPREAD", "BEAR_CALL_SPREAD", "IRON_CONDOR", "IRON_BUTTERFLY", "SHORT_STRADDLE",
                             "SHORT_STRANGLE", "LONG_STRADDLE", "LONG_STRANGLE", "CALENDAR_SPREAD", "CALL_RATIO_SPREAD",
                             "PUT_RATIO_SPREAD", "LONG_BUTTERFLY"]


class TradingDefaultsPrefs(_Prefs):
    """`trading_defaults`: the user's own starting values for new forms. Read by the frontend only, to pre-fill."""
    instrument_kind: Optional[Literal["UNDERLYING", "OPTION", "FUTURE"]] = None
    option_position: Optional[Literal["BUY", "WRITE"]] = None
    expiry_rule: Optional[Literal["NEAREST", "NEXT", "MONTHLY"]] = None
    strike_rule: Optional[Literal["ATM", "ITM", "OTM"]] = None
    strike_offset: Optional[int] = Field(default=None, ge=0, le=10)
    premium_stop_pct: Optional[float] = Field(default=None, ge=5, le=95, allow_inf_nan=False)
    max_lots: Optional[int] = Field(default=None, ge=1, le=500)
    # CUSTOM is left out: its legs are drawn per deployment on the full form, so there is nothing to pre-fill.
    option_strategy: Optional[OptionStrategyName] = None
    spread_width: Optional[int] = Field(default=None, ge=1, le=20)
    target_credit_pct: Optional[float] = Field(default=None, ge=5, le=95, allow_inf_nan=False)
    stop_credit_pct: Optional[float] = Field(default=None, ge=10, le=500, allow_inf_nan=False)
    # APPROVAL is applied by a form only while the organisation's `approval_only_entries` flag allows it (the API's own rule).
    entry_mode: Optional[Literal["AUTO", "APPROVAL"]] = None
    exit_rules: Optional[ExitDefaults] = None
    ticket: Optional[TicketDefaults] = None


class DailyRhythmPrefs(_Prefs):
    """`daily_rhythm` (Master Order 4, app/rhythm): whether the user's daily digests also go to his Telegram, and which.
    Telegram is OFF until the user turns it on; the mid-session pulse is optional and off by default. The digests on the
    Dashboard do not depend on this. Read by the worker only to decide what to send to the user's own chat."""
    telegram: bool = False
    pre_market: bool = True
    mid_session: bool = False
    closing: bool = True
    weekly: bool = True


MAX_MIN_OI = 1_000_000_000


class LiquidityLimits(_Prefs):
    """FNO-8B-28: his own liquidity limits. Off (and empty) by default: the comparison with the chain on screen is the
    default and stays. While `on`, at least one limit is set. The limits are kept when he turns them off, so turning
    them on again brings them back."""
    on: bool = False
    max_spread_pct: Optional[float] = Field(default=None, gt=0, le=100, allow_inf_nan=False,
                                            description="the widest bid-ask gap he accepts, as a percent of the price")
    min_oi: Optional[int] = Field(default=None, ge=1, le=MAX_MIN_OI, description="the least open interest he accepts")

    @model_validator(mode="after")
    def _a_limit_when_on(self) -> "LiquidityLimits":
        if self.on and self.max_spread_pct is None and self.min_oi is None:
            raise ValueError("set a bid-ask limit or an open interest limit to turn your own limits on")
        return self


class FnoPrefs(_Prefs):
    """`fno` (FNO-8B-28): the user's own F&O display settings. Read by the frontend only, to word the confirm step."""
    liquidity: LiquidityLimits = Field(default_factory=LiquidityLimits)


class SmartExecutionPrefs(_Prefs):
    """`smart_execution` (terminal master order Part 7): the user's own numbers for the smart-execution rules - the
    physical-settlement warning (how many days before expiry, how near the money counts, Telegram or not) and the
    protection band of a marketable limit where a broker takes no market orders (TM-3). The defaults are the settings'
    starting values, shown on Settings; the rules read only these (no fixed number in a rule). Information and order
    style only: never holds back an order, never touches an exit (ADR-0004)."""
    settlement_days_before: int = Field(default=4, ge=0, le=30, description="days before expiry the delivery warning starts")
    settlement_band_pct: float = Field(default=2.0, ge=0, le=20, allow_inf_nan=False,
                                       description="an option this close to its strike (% of the strike) counts as near the money")
    settlement_telegram: bool = True
    protection_band_pct: float = Field(default=0.5, ge=0.05, le=5, allow_inf_nan=False,
                                       description="a marketable limit this far past the price, where market orders are not allowed")
    # SE-7 (owner, 23 Oct): when a multi-leg order stops half way (INCOMPLETE) and he has not answered within
    # `answer_minutes`, ATP applies his own choice: close the filled legs (an exit, never blocked) or keep them.
    on_no_answer: Literal["close_filled", "keep"] = "close_filled"
    answer_minutes: int = Field(default=5, ge=1, le=120, description="minutes to answer an incomplete multi-leg order before the choice applies")


class NoticesPrefs(_Prefs):
    """`notices` (TT12-1): when a one-time notice was seen (dismissed, or made moot by the user's own action). Only
    marks; what a notice says and who gets it live in app/me/notices.py."""
    limits_not_set_seen_at: MarkTime = Field(default=None, description="the 'risk limits are now not set' notice was seen")


class PcrZonesPrefs(_Prefs):
    """B4 (UI-H bug lane): his own PCR zone edges for "What the chain shows" (app/option_chain/dish.py). The defaults are
    the values the page used before (the OI banner's bands, oi_regime.PcrBands): under 0.70 very low, under 0.90 low,
    up to 1.00 balanced, up to 1.30 high, above that very high. Display words only: nothing trades on them."""
    very_low_below: float = Field(default=0.70, gt=0, le=10, allow_inf_nan=False)
    low_below: float = Field(default=0.90, gt=0, le=10, allow_inf_nan=False)
    balanced_max: float = Field(default=1.00, gt=0, le=10, allow_inf_nan=False)
    high_max: float = Field(default=1.30, gt=0, le=10, allow_inf_nan=False)

    @model_validator(mode="after")
    def _ascending(self) -> "PcrZonesPrefs":
        edges = self.edges()
        if list(edges) != sorted(edges) or len(set(edges)) != len(edges):
            raise ValueError("the PCR zone edges must go up from very low to high")
        return self

    def edges(self) -> Tuple[float, float, float, float]:
        return (self.very_low_below, self.low_below, self.balanced_max, self.high_max)


class OptionChainPrefs(_Prefs):
    """`option_chain` (B4): his own Option chain settings - today the PCR zone edges. Read by the analyses route to
    word the PCR zone line; no order path, worker or risk check imports it."""
    pcr_zones: PcrZonesPrefs = Field(default_factory=PcrZonesPrefs)


NAMESPACES: Dict[str, Type[_Prefs]] = {"chart": ChartPrefs, "setup": SetupPrefs, "oc_analyses": OcAnalysesPrefs,
                                       "fno_baskets": FnoBasketsPrefs, "appearance": AppearancePrefs, "tours": ToursPrefs,
                                       "trading_defaults": TradingDefaultsPrefs, "daily_rhythm": DailyRhythmPrefs,
                                       "fno": FnoPrefs, "smart_execution": SmartExecutionPrefs, "notices": NoticesPrefs,
                                       "option_chain": OptionChainPrefs}

router = APIRouter(prefix="/api/me", tags=["me"])


def defaults(namespace: str) -> Dict[str, Any]:
    return NAMESPACES[namespace]().model_dump(mode="json")


def _drop_at(data: Dict[str, Any], loc: Tuple[Any, ...]) -> bool:
    """Removes the value at `loc` (or the nearest enclosing key that exists) so its default applies; False if none."""
    for size in range(len(loc), 0, -1):
        node: Any = data
        for key in loc[:size - 1]:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, dict) and loc[size - 1] in node:
            del node[loc[size - 1]]
            return True
    return False


def stored_value(namespace: str, raw: Optional[str]) -> Dict[str, Any]:
    """A stored value as the current schema reads it: keys this release does not know (a newer release saved them) are
    dropped, and so is each value that no longer validates (that field takes its default, the rest is kept - one bad
    setup mark never loses the others); a value that still cannot be read falls back to the defaults, so a read never
    fails."""
    model = NAMESPACES[namespace]
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return defaults(namespace)
    if not isinstance(data, dict):
        return defaults(namespace)
    known = {k: v for k, v in data.items() if k in model.model_fields}
    for _ in range(16):                                  # each pass drops at least one bad value, so this ends
        try:
            return model.model_validate_json(json.dumps(known)).model_dump(mode="json")
        except ValidationError as exc:
            dropped = [_drop_at(known, tuple(e.get("loc", ()))) for e in exc.errors(include_input=False, include_url=False)]
            if not any(dropped):
                break
    return defaults(namespace)


def _own(tenant_id: int, user_id: int):
    return (UserPrefsRecord.tenant_id == tenant_id, UserPrefsRecord.user_id == user_id)


async def read_body(request: Request) -> bytes:
    """The request body, refused with 413 past MAX_BODY_BYTES (also used by `PUT /api/me/setup`)."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail=f"Preferences larger than {MAX_BODY_BYTES} bytes")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail=f"Preferences larger than {MAX_BODY_BYTES} bytes")
    return bytes(data)


def _validate(namespace: str, raw: bytes) -> Dict[str, Any]:
    try:
        return NAMESPACES[namespace].model_validate_json(raw or b"null").model_dump(mode="json")
    except ValidationError as exc:
        # The input is never echoed back (main.py's rule for 422s): where and what only.
        problems = [{"loc": list(e.get("loc", ())), "msg": e.get("msg", "invalid")} for e in exc.errors(include_input=False, include_url=False)]
        raise HTTPException(status_code=422, detail=problems) from None


@router.get("/prefs")
async def get_prefs(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Every namespace of the caller's own preferences, defaults filled in; version 0 = never saved."""
    rows = {r.namespace: r for r in await session.scalars(select(UserPrefsRecord).where(*_own(user.tenant_id, user.id)))}
    prefs, versions = {}, {}
    for namespace in NAMESPACES:
        row = rows.get(namespace)
        prefs[namespace] = stored_value(namespace, row.value_json if row else None)
        versions[namespace] = row.version if row else 0
    return {"prefs": prefs, "versions": versions}


@router.put("/prefs/{namespace}")
async def put_prefs(namespace: str, request: Request, version: Optional[int] = None, user: User = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Replaces one namespace of the caller's own preferences with the body (fields left out take their defaults).
    `version` (optional): the version the change was made on; a stale one is refused with 409 and the current value."""
    if namespace not in NAMESPACES:
        raise HTTPException(status_code=404, detail="Unknown preferences namespace")
    value, saved_version = await save(namespace, await read_body(request), version, user, session)
    return {"namespace": namespace, "value": value, "version": saved_version}


async def save(namespace: str, raw: bytes, version: Optional[int], user: User, session: AsyncSession) -> Tuple[Dict[str, Any], int]:
    """Validates `raw` against the namespace and stores it for the caller (the compare-and-set described above). Also
    the body of `PUT /api/me/setup` (app/me/setup.py). Returns the stored value and its new version."""
    value = _validate(namespace, raw)
    tenant_id, user_id = user.tenant_id, user.id        # read once: a rollback below expires the `user` object
    # Two attempts: when two devices save a namespace's FIRST value at the same moment, one insert loses on the unique
    # constraint. Without a version that save simply goes again, now as an update of the row the other one made; with a
    # version it is a 409 like any other stale edit.
    saved = 0
    for attempt in (1, 2):
        row = await session.scalar(select(UserPrefsRecord).where(*_own(tenant_id, user_id), UserPrefsRecord.namespace == namespace)
                                   .with_for_update())
        current = row.version if row else 0
        if version is not None and version != current:
            raise HTTPException(status_code=409, detail={"message": "the preferences changed since this edit was made", "version": current,
                                                         "current": stored_value(namespace, row.value_json if row else None)})
        now = datetime.now(timezone.utc)
        if row is None:
            row = UserPrefsRecord(tenant_id=tenant_id, user_id=user_id, namespace=namespace, value_json=json.dumps(value),
                                  version=1, updated_at=now)
            session.add(row)
        else:
            row.value_json, row.version, row.updated_at = json.dumps(value), row.version + 1, now
        saved = row.version
        try:
            await session.commit()
            break
        except IntegrityError:
            await session.rollback()
            if version is not None or attempt == 2:
                raise HTTPException(status_code=409, detail={"message": "the preferences changed since this edit was made"}) from None
    return value, saved


async def read_one(namespace: str, user: User, session: AsyncSession) -> Tuple[Dict[str, Any], int]:
    """One namespace of the caller's own preferences (defaults filled in) and its version (0 = never saved)."""
    row = await session.scalar(select(UserPrefsRecord).where(*_own(user.tenant_id, user.id), UserPrefsRecord.namespace == namespace))
    return stored_value(namespace, row.value_json if row else None), (row.version if row else 0)


__all__ = ["router", "NAMESPACES", "OptionChainPrefs", "PcrZonesPrefs", "NoticesPrefs", "TOUR_IDS", "FnoPrefs", "SmartExecutionPrefs", "LiquidityLimits", "MAX_MIN_OI", "DailyRhythmPrefs", "TradingDefaultsPrefs", "ExitDefaults", "TicketDefaults", "ToursPrefs", "AppearancePrefs", "ChartPrefs", "SetupPrefs", "OcAnalysesPrefs", "SavedAnalysis", "MAX_SAVED_ANALYSES", "FnoBasketsPrefs", "MAX_SAVED_BASKETS", "MAX_BASKET_LEGS", "MAX_BODY_BYTES", "defaults", "stored_value", "save", "read_one", "read_body"]
