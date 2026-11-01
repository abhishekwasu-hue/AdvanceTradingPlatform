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
  PRE-FILL a new trading bot, the full deployment form and the chain order ticket in the frontend, each marked "from your
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
  `terminal` (Trading Terminal T1, review TT-16) holds the user's own Terminal layouts: at most MAX_TERMINAL_LAYOUTS, each a
  name, the preset it started from and its panels on a 12-column grid (position, size, open or closed, a chart's symbol),
  sized so the most a user can save stays inside MAX_BODY_BYTES (a test proves it), and which layout is in use. The
  presets themselves live in the frontend code (src/terminal/layoutModel.ts), so "reset to the preset" needs no server.
  Layout only: no order path, worker or risk check imports it.
  `notices` (TT12-1) holds when a one-time notice was seen (app/me/notices.py), so it never shows twice on any device.
  `option_chain` (B4, UI-H bug lane) holds his own Option chain settings: the PCR zone edges that word "What the chain
  shows" (defaults = the values used before). Display words only.
  `shell` (M57) holds the app shell's own display choices: whether the sidebar is the full menu or the icon rail.
  `trade_settings` (M68 PR-0) holds the user's own Settings > Trade settings, every field "Not set" until he saves it; the
  registry, its rules and its own routes are app/me/trade_settings.py.
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
from app.me import trade_settings
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


# TF-1 (owner 25 Oct): the timeframes a chart can show (the candle route's ids, frontend src/chart/timeframes.ts) and
# the starred favourites shown as one-click buttons beside the timeframe dropdown (default 5m, 15m, 1H, D).
ChartTimeframe = Literal["1min", "3min", "5min", "15min", "30min", "60min", "day", "week", "month"]
DEFAULT_TIMEFRAME_FAVOURITES: Tuple[str, ...] = ("5min", "15min", "60min", "day")


def _unique_favourites(value: List[str]) -> List[str]:
    if len(set(value)) != len(value):
        raise ValueError("a timeframe is starred only once")
    return value


class ChartPrefs(_Prefs):
    """`chart`: one setting place for every chart (both chart tabs, Signals, Positions, the Option chain)."""
    theme: Literal["app", "light", "dark"] = Field(default="app", description="app = follow the app theme (which follows the device)")
    grid: bool = False
    candle_colors: Optional[CandleColors] = None
    indicators: ChartIndicators = Field(default_factory=ChartIndicators)
    reader_overlay: bool = Field(default=False, description="the 'Reader zones & lines' overlay")
    timeframe_favourites: Annotated[List[ChartTimeframe], AfterValidator(_unique_favourites)] = Field(
        default_factory=lambda: list(DEFAULT_TIMEFRAME_FAVOURITES), max_length=9,
        description="TF-1: the starred timeframes, shown as one-click buttons beside the dropdown (in this order)")


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
    lots: int = Field(ge=1)                      # M68 PR-4 (TS1-046): no ATP ceiling
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
    product: Literal["MIS", "NRML"] | None = None      # M68 TS1-004: none until he picks one; loads with the product empty
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
    page (frontend `src/theme.ts`): normal = 100 %, large = 112.5 %, xlarge = 125 %. UI-H-DM DM4 (owner, 25 Oct): the
    "High contrast" option - stronger borders and brighter text and icons, in both themes; off by default."""
    text_size: Literal["normal", "large", "xlarge"] = "normal"
    high_contrast: bool = False


# MO2 part C: the pages that have a page tour (frontend src/tours/pages.ts TOUR_IDS, kept equal by a test). An allow-list,
# so a stored mark is always one of these and the namespace stays small.
TOUR_IDS: Tuple[str, ...] = ("dashboard", "broker", "charts", "option-chain", "signals", "positions", "portfolio", "risk", "settings", "copilot",
                             "terminal")
TourId = Literal["dashboard", "broker", "charts", "option-chain", "signals", "positions", "portfolio", "risk", "settings", "copilot",
                 "terminal"]


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
    lots: int | None = Field(default=None, ge=1)
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
    strike_offset: int | None = Field(default=None, ge=0)             # M68 PR-4 (TS1-046 / 047): no ATP ceiling
    premium_stop_pct: Optional[float] = Field(default=None, ge=5, le=95, allow_inf_nan=False)
    max_lots: int | None = Field(default=None, ge=1)
    # CUSTOM is left out: its legs are drawn per deployment on the full form, so there is nothing to pre-fill.
    option_strategy: Optional[OptionStrategyName] = None
    spread_width: int | None = Field(default=None, ge=1)
    target_credit_pct: Optional[float] = Field(default=None, ge=5, le=95, allow_inf_nan=False)
    stop_credit_pct: Optional[float] = Field(default=None, ge=10, le=500, allow_inf_nan=False)
    # M68 PR-6: its home is now Settings > Trade settings > Automation > New entries; a value saved here is still read as
    # his own (TS-6, app/execution/automation.py) when that is Not set.
    entry_mode: Optional[Literal["AUTO", "APPROVAL"]] = None
    exit_rules: Optional[ExitDefaults] = None
    ticket: Optional[TicketDefaults] = None


class MyLimitsPrefs(_Prefs):
    """`my_limits` (DEF-0, owner 28 Oct): the trader's own switches the server reads - each off until he turns it on.
    D4 `limit_by_experience`: "Limit me by my experience level" (the experience_levels gates apply only then).
    D7 `stop_required_on_sold`: "A sold option or a future needs a stop-loss" on the order ticket."""
    limit_by_experience: Optional[bool] = None
    stop_required_on_sold: Optional[bool] = None


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
    # M68 PR-4 (TS1-045): no starting values - Not set until he saves them (no warning while Not set). Their home is now
    # Settings > Trade settings > Options > Physical delivery warning (app/instruments/option_defaults.py reads that
    # first, then a value stored here). No ATP ceiling on the days or the band.
    settlement_days_before: int | None = Field(default=None, ge=0, description="days before expiry the delivery warning starts")
    settlement_band_pct: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False,
                                              description="an option this close to its strike (% of the strike) counts as near the money")
    settlement_telegram: bool | None = None
    # M68 PR-1 (TS1-013): no starting value - Not set until he saves one. Its home is now Settings > Trade settings >
    # Order defaults > Price protection (app/execution/order_defaults.py reads that first, then a value stored here).
    protection_band_pct: float | None = Field(default=None, ge=0.05, le=5, allow_inf_nan=False,
                                                 description="a marketable limit this far past the price, where market orders are not allowed")
    # SE-7 (owner, 23 Oct): when a multi-leg order stops half way (INCOMPLETE) and he has not answered within
    # `answer_minutes`, ATP applies his own choice: close the filled legs (an exit, never blocked) or keep them.
    on_no_answer: Literal["close_filled", "keep"] = "close_filled"
    answer_minutes: int = Field(default=5, ge=1, le=120, description="minutes to answer an incomplete multi-leg order before the choice applies")


# Trading Terminal T1 (TT-16, TT-17): the user's own layouts. The grid is ATP's own: 12 columns, rows of one fixed height.
MAX_TERMINAL_LAYOUTS = 10
TERMINAL_COLUMNS = 12
TERMINAL_ROWS = 64
TERMINAL_MAX_HEIGHT = 32
TERMINAL_LAYOUT_ID = r"^[a-z0-9]{4,16}$"
TERMINAL_LAYOUT_NAME = r"^[A-Za-z0-9][A-Za-z0-9 &()+\-/.,:']{0,39}$"
TerminalPreset = Literal["option_seller", "quick_trade", "multi_chart"]
TerminalPanelId = Literal["chart-1", "chart-2", "chart-3", "chart-4", "chain", "order", "positions", "orders", "basket", "bots", "payoff"]
TERMINAL_PANEL_IDS: Tuple[str, ...] = ("chart-1", "chart-2", "chart-3", "chart-4", "chain", "order", "positions", "orders", "basket",
                                       "bots", "payoff")
TERMINAL_UNDERLYING = r"^[A-Z0-9&_\- ]{1,30}$"


class TerminalPanel(_Prefs):
    """One panel of a layout: where it sits on the 12-column grid, its size, and whether it is open. A chart keeps its
    own symbol (the Multi-chart preset shows four); every other panel follows the layout's underlying."""
    id: TerminalPanelId
    x: int = Field(ge=0, lt=TERMINAL_COLUMNS)
    y: int = Field(ge=0, lt=TERMINAL_ROWS)
    w: int = Field(ge=1, le=TERMINAL_COLUMNS)
    h: int = Field(ge=1, le=TERMINAL_MAX_HEIGHT)
    open: bool = True
    symbol: Optional[str] = Field(default=None, pattern=BASKET_SYMBOL)

    @model_validator(mode="after")
    def _inside(self) -> "TerminalPanel":
        if self.x + self.w > TERMINAL_COLUMNS:
            raise ValueError(f"a panel must fit inside the {TERMINAL_COLUMNS} columns")
        if self.y + self.h > TERMINAL_ROWS:
            raise ValueError("a panel must fit inside the grid")
        if self.symbol is not None and not self.id.startswith("chart-"):
            raise ValueError("only a chart panel has its own symbol")
        return self


def _overlap(a: TerminalPanel, b: TerminalPanel) -> bool:
    return a.x < b.x + b.w and b.x < a.x + a.w and a.y < b.y + b.h and b.y < a.y + a.h


class TerminalLayout(_Prefs):
    name: str = Field(pattern=TERMINAL_LAYOUT_NAME, max_length=40)
    preset: TerminalPreset = "option_seller"
    underlying: Optional[str] = Field(default=None, pattern=TERMINAL_UNDERLYING)
    panels: List[TerminalPanel] = Field(min_length=1, max_length=len(TERMINAL_PANEL_IDS))
    saved_at: MarkTime = Field(default=None)

    @model_validator(mode="after")
    def _panels(self) -> "TerminalLayout":
        ids = [p.id for p in self.panels]
        if len(set(ids)) != len(ids):
            raise ValueError("each panel appears once in a layout")
        shown = [p for p in self.panels if p.open]
        if not shown:
            raise ValueError("a layout needs at least one open panel")
        for i, a in enumerate(shown):
            for b in shown[i + 1:]:
                if _overlap(a, b):
                    raise ValueError(f"the {a.id} and {b.id} panels overlap")
        return self


class TerminalPrefs(_Prefs):
    """`terminal` (Trading Terminal T1, TT-16): the user's own layouts by id, and the one in use - a preset's name or one
    of his layouts' ids. Empty = the "Option seller" preset (T-D1: the default for a new user)."""
    layouts: Dict[Annotated[str, StringConstraints(pattern=TERMINAL_LAYOUT_ID)], TerminalLayout] = Field(default_factory=dict)
    active: Optional[str] = Field(default=None, max_length=16)

    @model_validator(mode="after")
    def _rules(self) -> "TerminalPrefs":
        if len(self.layouts) > MAX_TERMINAL_LAYOUTS:
            raise ValueError(f"at most {MAX_TERMINAL_LAYOUTS} saved layouts; delete one first")
        names = [layout.name.strip().lower() for layout in self.layouts.values()]
        if len(set(names)) != len(names):
            raise ValueError("two layouts cannot have the same name")
        presets = ("option_seller", "quick_trade", "multi_chart")
        if self.active is not None and self.active not in presets and self.active not in self.layouts:
            raise ValueError("the layout in use must be a preset or one of your layouts")
        return self


class NoticesPrefs(_Prefs):
    """`notices` (TT12-1): when a one-time notice was seen (dismissed, or made moot by the user's own action). Only
    marks; what a notice says and who gets it live in app/me/notices.py."""
    limits_not_set_seen_at: MarkTime = Field(default=None, description="the 'risk limits are now not set' notice was seen")
    risk_limits_off_seen_at: MarkTime = Field(default=None, description="the M64 'risk limits are now off by default' notice was seen")


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
    # P1 (27 Oct): on an expiry day the chain (and the Terminal's spread / basket builder, which builds from it) opens the
    # NEXT expiry; today's stays selectable with its "expires today" notice. Read by the option-chain routes only.
    # M68 PR-4 (TS1-044): None = Not set - the nearest expiry opens (Trade settings > Options > expiry_day_roll wins).
    expiry_day_next: bool | None = None


class StrategyExitPrefs(_Prefs):
    """`strategy_exits` (SX1, owner 24 Oct): the user's own starting values for "Exits for this strategy" on the basket
    ticket / spread builder. Every field is empty by default: nothing is pre-filled until he saves his own. Read by the
    frontend only, to pre-fill (marked "from your defaults"); the server never reads it to trade."""
    measure: Optional[Literal["MTM_INR", "PREMIUM_PCT", "SPOT"]] = None
    sl_value: Optional[float] = Field(default=None, gt=0, lt=1e7, allow_inf_nan=False)
    target_value: Optional[float] = Field(default=None, gt=0, lt=1e7, allow_inf_nan=False)
    trail_kind: Optional[Literal["LOCK", "TRAIL", "LOCK_TRAIL", "BREAK_EVEN"]] = None
    trail_after: Optional[float] = Field(default=None, gt=0, lt=1e7, allow_inf_nan=False)
    trail_lock: Optional[float] = Field(default=None, ge=0, lt=1e7, allow_inf_nan=False)
    trail_step: Optional[float] = Field(default=None, gt=0, lt=1e7, allow_inf_nan=False)
    trail_by: Optional[float] = Field(default=None, gt=0, lt=1e7, allow_inf_nan=False)


class TradeSettingsPrefs(_Prefs):
    """`trade_settings` (M68 PR-0): the user's own Settings > Trade settings, ``{"values": {key: value}}``. A key present is
    a value he saved himself (the saved marker); an absent key is "Not set". Validated against the one registry
    (app/me/trade_settings.py), which also serves the page's own routes (GET / PUT /api/me/trade-settings)."""
    values: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _registry(self) -> TradeSettingsPrefs:
        trade_settings.check_stored(self.values)
        return self


class ShellPrefs(_Prefs):
    """`shell` (M57 U7-1, owner 30 Oct): the app shell's own display choices. `sidebar`: the menu from md up, the full menu
    ("expanded") or a 56 px rail of icons ("rail"); None = never chosen (the frontend then shows the full menu from
    1024 px and the rail on a tablet). Display only: nothing on the server reads it."""
    sidebar: Optional[Literal["expanded", "rail"]] = None


class AiUsagePrefs(_Prefs):
    """`ai_usage` (M72 PR-1b, M72Q-1): the user's OWN daily limit in rupees for AI answers on the platform AI key
    (app/ai/platform_key.py). None = Not set: only the operator's per-user daily cap applies. A value of his own can only
    lower it (the lower of the two is used, never the higher). Read by the platform key's cap check only; no order path,
    worker or risk check imports it, and his own connected key keeps its own limits (Connect AI)."""
    daily_cap_inr: float | None = Field(default=None, gt=0, lt=1e7, allow_inf_nan=False)


class AiPrefs(_Prefs):
    """`ai` (M72 PR-10, CP-10, M72Q-2): the language the Copilot planner ANSWERS in (English / Marathi / Hindi; the
    interface stays English) and whether the Copilot shows the browser's speech input (off by default). Read only while
    the flag `copilot_languages` is on (app/ai/languages.py); no order path, worker or risk check imports it."""
    language: Literal["en", "mr", "hi"] = "en"
    voice: bool = False


WATCH_PCT = Field(default=None, gt=0, le=100, allow_inf_nan=False)


class CopilotWatchPrefs(_Prefs):
    """`copilot_watch` (M72 PR-7, CP-7): the user's own opt-in Copilot watch. Every type is OFF until he turns it on, and a
    type with a number needs HIS number (None = Not set; ATP chooses none, M68). Read by app/ai/watch.py only, to decide
    which short information cards to raise for HIS own positions, strikes and watchlists; no order path, worker trading
    step or risk check imports it, nothing is closed or blocked (ADR-0004, ADR-0006).
    - near_levels: an open position whose mark has this % (or less) of the room left to his own invalidation or profit level;
    - strike_distance: a short option whose underlying is within this % of the strike;
    - greeks_jump: IV at his strike moved by this many IV points, or delta by this much, between two stored chain readings
      (delta needs his own risk-free rate, Trade settings > Options);
    - oi_shift: open interest at his strike changed by this % between two stored chain readings;
    - watchlist_signal: a new setup from his strategies or scans on a symbol in his own watchlists (no number);
    - telegram: also to his own Telegram chat (off by default; in the app either way)."""
    telegram: bool = False
    near_levels: bool = False
    near_levels_room_left_pct: float | None = WATCH_PCT
    strike_distance: bool = False
    strike_distance_pct: float | None = WATCH_PCT
    greeks_jump: bool = False
    iv_jump_points: float | None = WATCH_PCT
    delta_jump: float | None = Field(default=None, gt=0, le=1, allow_inf_nan=False)
    oi_shift: bool = False
    oi_shift_pct: float | None = Field(default=None, gt=0, le=10_000, allow_inf_nan=False)
    watchlist_signal: bool = False

    @model_validator(mode="after")
    def _numbers_first(self) -> CopilotWatchPrefs:
        need = {"near_levels": self.near_levels_room_left_pct is not None, "strike_distance": self.strike_distance_pct is not None,
                "greeks_jump": self.iv_jump_points is not None or self.delta_jump is not None, "oi_shift": self.oi_shift_pct is not None}
        for kind, has_number in need.items():
            if getattr(self, kind) and not has_number:
                raise ValueError(f"{kind}: set your own number first")
        return self


NAMESPACES: Dict[str, Type[_Prefs]] = {"chart": ChartPrefs, "setup": SetupPrefs, "oc_analyses": OcAnalysesPrefs,
                                       "fno_baskets": FnoBasketsPrefs, "appearance": AppearancePrefs, "tours": ToursPrefs,
                                       "trading_defaults": TradingDefaultsPrefs, "daily_rhythm": DailyRhythmPrefs,
                                       "fno": FnoPrefs, "smart_execution": SmartExecutionPrefs, "notices": NoticesPrefs,
                                       "option_chain": OptionChainPrefs, "terminal": TerminalPrefs,
                                       "strategy_exits": StrategyExitPrefs, "my_limits": MyLimitsPrefs, "shell": ShellPrefs,
                                       "trade_settings": TradeSettingsPrefs, "ai_usage": AiUsagePrefs,
                                       "copilot_watch": CopilotWatchPrefs, "ai": AiPrefs}

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
    if namespace == trade_settings.NAMESPACE:            # one write path: it saves only the fields sent (M68 PR-0)
        raise HTTPException(status_code=405, detail="Trade settings are saved at PUT /api/me/trade-settings")
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


__all__ = ["router", "NAMESPACES", "AiUsagePrefs", "OptionChainPrefs", "PcrZonesPrefs", "NoticesPrefs", "TOUR_IDS", "TerminalPrefs", "TerminalLayout", "TerminalPanel", "MAX_TERMINAL_LAYOUTS", "TERMINAL_PANEL_IDS", "FnoPrefs", "SmartExecutionPrefs", "LiquidityLimits", "MAX_MIN_OI", "DailyRhythmPrefs", "TradingDefaultsPrefs", "StrategyExitPrefs", "TradeSettingsPrefs", "ExitDefaults", "TicketDefaults", "ToursPrefs", "AppearancePrefs", "ChartPrefs", "SetupPrefs", "OcAnalysesPrefs", "SavedAnalysis", "MAX_SAVED_ANALYSES", "FnoBasketsPrefs", "MAX_SAVED_BASKETS", "MAX_BASKET_LEGS", "MAX_BODY_BYTES", "defaults", "stored_value", "save", "read_one", "read_body"]


async def own_limits(session: AsyncSession, user: User) -> Dict[str, Any]:
    """DEF-0: this user's `my_limits` switches as the schema reads them (every one None = off when he saved none)."""
    raw = await session.scalar(select(UserPrefsRecord.value_json).where(*_own(user.tenant_id, user.id),
                                                                         UserPrefsRecord.namespace == "my_limits"))
    return stored_value("my_limits", raw)
