"""M68 PR-0 (owner order M68, TS-2): Settings > Trade settings - ONE registry of every trading choice a user makes.

The rule (M68, binding): ATP never pre-chooses, caps or silently changes a trading choice. Every field below starts
"Not set" (None); no value is pre-filled by ATP. What stays fixed is only what the exchange or the broker enforces; those
are listed here too, as facts (`fact=True`): shown as information, never stored, never a control.

The registry (`FIELDS`) declares each field once: its section (the seven TS-2 sections), key, plain-English label, an
optional glossary key for the (?) (frontend src/ui/glossary.ts), its kind / unit / allowed values / bounds, the TS-1 audit
rows it closes (docs/design/M68_TS1_AUDIT.md), the fix PR that moves the value onto it and where the value lives today.
`wired` is False until that PR makes an order path, a bot, the paper account or a form read the field: until then nothing
reads it (no behaviour change in PR-0), the user does not see it (no dead control), and only a platform admin
(SUPER_ADMIN) sees and may save it, on the page's Advanced part. Bounds are only "a real number of the right sign" (and a
% at most 100): the audit's ATP-imposed caps (TS1-034, 046, 050, 068) are not carried over.

Storage: the per-user prefs store (app/me/prefs.py), namespace `trade_settings`, as ``{"values": {key: value}}``. The
saved marker (TS-6): a key present in `values` is a value the user saved himself; an absent key is "Not set". No table,
no migration.

Routes (per user and tenant; USER and ADMIN alike, each only his own):
* ``GET /api/me/trade-settings`` - every section with its fields: value (or null = "Not set") and `saved`.
* ``PUT /api/me/trade-settings`` ``{"values": {key: value | null}}`` - saves only the fields sent (null clears one back to
  "Not set"); every other field is left exactly as it was, never filled. A wrong key, kind or value is refused (422) and
  nothing is saved.

Overrides are NOT in PR-0. Their API shape, for the PRs that add them: the same body with a scope,
``PUT /api/me/trade-settings?strategy=<id>`` / ``?bot=<id>`` (stored on that strategy / bot), and an order carries its own
values in the order body; ``GET /api/me/trade-settings?bot=<id>`` will return the effective value of each field with
``from: "order" | "bot" | "strategy" | "user" | null``. Admin ceilings stay per organisation (M68Q-5) and are not saved
through this route (`scope="organisation"`).
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.enums import UserRole
from app.db.models import User
from app.db.session import get_session

NAMESPACE = "trade_settings"
PAGE_PATH = "/settings/trade-settings"
Kind = Literal["choice", "number", "whole", "switch", "time", "legs", "fact"]
TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
BIG = 1e9                       # "a finite number": the only top a value with no natural top has
MAX_LEGS = 6                    # TS1-047: the broker's basket limit for one structure (a fact)


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    # Where a user sets these choices today, until the section's PR moves them here (None: nowhere yet).
    today: tuple[str, str] | None = None


SECTIONS: tuple[Section, ...] = (
    Section("order_defaults", "Order defaults", ("My trading defaults", "/settings/trading-defaults")),
    Section("position_size", "Position size", ("My trading defaults", "/settings/trading-defaults")),
    Section("exits", "Exits", ("My trading defaults", "/settings/trading-defaults")),
    Section("options", "Options", ("My trading defaults", "/settings/trading-defaults")),
    Section("risk_limits", "Risk limits", ("Risk limits", "/settings/risk-limits")),
    Section("automation", "Automation", ("My trading defaults", "/settings/trading-defaults")),
    Section("paper_account", "Paper account", None),
)
SECTION_IDS = tuple(s.id for s in SECTIONS)


@dataclass(frozen=True)
class SettingField:
    section: str
    name: str
    label: str
    kind: Kind
    help: str
    pr: int                                          # the M68 fix PR that wires it (docs/design/M68_TS1_AUDIT.md plan)
    rows: tuple[str, ...] = ()                       # the TS-1 audit rows it closes ("TS1-001" ...)
    unit: str = ""
    choices: tuple[tuple[str, str], ...] = ()        # (value, plain label)
    low: float | None = None                      # inclusive, unless low_open
    high: float | None = None                     # inclusive
    low_open: bool = False                           # True: the value must be ABOVE `low`
    term: str | None = None                       # glossary key for the (?)
    today: str = ""                                  # where the value lives today (for the PR that moves it)
    wired: bool = False
    scope: Literal["user", "organisation"] = "user"
    flag: str | None = None                       # M68 PR-6: shown / saved only while this feature flag is on for him

    @property
    def key(self) -> str:
        return f"{self.section}.{self.name}"

    @property
    def fact(self) -> bool:
        return self.kind == "fact"


def _f(section: str, name: str, label: str, kind: Kind, help: str, pr: int, *rows: int, **kw: Any) -> SettingField:
    return SettingField(section, name, label, kind, help, pr, tuple(f"TS1-{r:03d}" for r in rows), **kw)


def _pct(**kw: Any) -> dict[str, Any]:
    return {"unit": "%", "low": 0.0, "high": 100.0, "low_open": True, **kw}


def _pos(unit: str, **kw: Any) -> dict[str, Any]:
    return {"unit": unit, "low": 0.0, "high": BIG, "low_open": True, **kw}


def _count(unit: str, low: float = 1, **kw: Any) -> dict[str, Any]:
    return {"unit": unit, "low": low, "high": BIG, **kw}


TOUCH_CLOSE = (("TOUCH", "When the price touches it"), ("CLOSE", "When a candle closes past it"))
EXIT_MEASURE = (("POINTS", "Points from entry"), ("PCT", "% from entry"), ("RUPEES", "Rupees on the trade"))

O, P, X, OP, R, A, PA = SECTION_IDS
FIELDS: tuple[SettingField, ...] = (
    # --- Order defaults (PR-1) -------------------------------------------------------------------------------------------
    _f(O, "holding", "Holding", "choice", "Close the position the same day, or carry it forward.", 1, 5, 6, 7, 8,
       choices=(("INTRADAY", "Intraday"), ("SWING", "Carry forward")), today="bot form holding; strategy_deployments.holding", wired=True),
    _f(O, "product", "Product", "choice", "The broker product for a futures or options order.", 1, 1, 2, 4,
       choices=(("MIS", "MIS (intraday)"), ("NRML", "NRML (carry forward)")), term="mis",
       today="trading_defaults.ticket.product; saved basket product", wired=True),
    _f(O, "order_type", "Order type", "choice", "How an order is sent to the market.", 1, 1, 2, 3, 10, 12,
       choices=(("MARKET", "Market"), ("LIMIT", "Limit"), ("SL", "SL (stop-limit)"), ("SL-M", "SL-M (stop at market)"),
                ("PROTECTED_LIMIT", "Limit with price protection")), term="sl",
       today="trading_defaults.ticket.order_type; strategy_deployments.order_style", wired=True),
    _f(O, "price_protection_pct", "Price protection", "number", "A market order is sent as a limit this far past the price.",
       1, 11, 12, 13, **_pct(), today="smart_execution.protection_band_pct; order_style protection", wired=True),
    _f(O, "stop_limit_band_pct", "Stop-limit gap", "number",
       "Where the exchange takes no SL-M order, the limit price sits this far past your trigger price.", 1, 14,
       **_pct(), term="sl-m", today="operator setting STOP_LIMIT_BAND_PCT", wired=True),
    _f(O, "trigger_mode", "Trigger on", "choice", "When a waiting order or a stop counts as reached.", 1, 15,
       choices=TOUCH_CLOSE, today="paper resting order trigger_mode", wired=True),
    _f(O, "trigger_source", "Trigger price from", "choice", "Which price a trigger on an option watches.", 1, 15,
       choices=(("PREMIUM", "The option's own price"), ("SPOT", "The index or stock price")),
       today="paper resting order trigger_source", wired=True),
    _f(O, "freeze_quantity", "Freeze quantity", "fact",
       "An order above the exchange's freeze quantity is split into smaller orders automatically.", 1, 87),
    _f(O, "market_protection", "Market order protection", "fact",
       "Your broker adds its own price band to a market order sent from an app (a SEBI rule).", 1, 16),
    _f(O, "order_type_support", "Order types your broker takes", "fact",
       "Where your broker or the exchange takes no market or SL-M order, a limit or an SL order is sent instead.", 1, 92, 94),

    # --- Position size (PR-2) --------------------------------------------------------------------------------------------
    _f(P, "mode", "Size by", "choice", "How the size of a new entry is worked out.", 2, 17,
       choices=(("FIXED_LOTS", "Fixed lots"), ("FIXED_QUANTITY", "Fixed quantity"), ("PCT_CAPITAL", "% of capital"),
                ("RISK_BASED", "Risk-based (Risk limits)")), today="none: one lot when no size is given", wired=True),
    _f(P, "lots", "Lots", "whole", "The number of lots for a new entry.", 2, 17, 19, 20, 21, 22, 23, **_count("lots"),
       term="lot", today="trading_defaults.max_lots / ticket.lots", wired=True),
    _f(P, "quantity", "Quantity", "whole", "The number of shares or units for a new entry.", 2, 17, **_count("units"), wired=True),
    _f(P, "capital_pct", "Share of capital", "number", "The value of a new entry as a share of your capital.", 2, 17, **_pct(),
       wired=True),
    _f(P, "lot_size", "Lot size", "fact", "Set by the exchange for each contract. A size is a whole number of lots.", 2, 18, 88,
       term="lot"),
    _f(P, "fanout_mode", "Extra paper accounts: size by", "choice", "How an extra paper account sizes a copied entry.", 2, 24,
       choices=(("MULTIPLIER", "The main size times a number"), ("FIXED", "Fixed lots"), ("PCT_CAPITAL", "% of its capital")),
       today="paper_sub_accounts.sizing_mode", wired=True),
    _f(P, "fanout_value", "Extra paper accounts: value", "number", "The number for the way chosen above.", 2, 24, **_pos(""),
       today="paper_sub_accounts.sizing_value", wired=True),

    # --- Exits (PR-3) ----------------------------------------------------------------------------------------------------
    _f(X, "stop_loss_kind", "Stop-loss measured in", "choice", "How your stop-loss distance is counted.", 3,
       choices=EXIT_MEASURE, term="stop", today="strategy_exits.measure"),
    _f(X, "stop_loss_value", "Stop-loss", "number", "How far from the entry the trade is closed to limit the loss.", 3,
       **_pos(""), term="stop", today="strategy_exits.sl_value; trading_defaults.premium_stop_pct"),
    _f(X, "target_kind", "Target measured in", "choice", "How your target distance is counted.", 3, choices=EXIT_MEASURE,
       term="target", today="strategy_exits.measure"),
    _f(X, "target_value", "Target", "number", "How far from the entry the trade is closed to take the profit.", 3, **_pos(""),
       term="target", today="strategy_exits.target_value"),
    _f(X, "trailing_kind", "Trailing stop by", "choice", "How the stop follows the price.", 3,
       choices=(("POINTS", "Points"), ("PCT", "%"), ("ATR", "ATR multiple")), term="atr", today="strategy_exits.trail_kind"),
    _f(X, "trailing_value", "Trailing stop", "number", "How far behind the best price the stop follows.", 3, **_pos(""),
       today="trading_defaults.exit_rules.trailing_stop_pct"),
    _f(X, "break_even_at_r", "Move the stop to entry at", "number", "Once the trade gains this many R, the stop moves to the entry.",
       3, 34, **_pos("R"), term="r-multiple", today="trading_defaults.exit_rules.break_even_at_r"),
    _f(X, "time_exit_minutes", "Close after", "whole", "A position is closed this many minutes after its entry.", 3, 34,
       **_count("minutes"), today="trading_defaults.exit_rules.time_exit_minutes"),
    _f(X, "time_exit_at", "Close at a time", "time", "A position is closed at this time.", 3, 33,
       today="trading_defaults.exit_rules.time_exit_at"),
    _f(X, "square_off_time", "Square-off time", "time",
       "Intraday positions are closed at this time. Not set: ATP closes nothing by itself.", 3, 26, 27,
       today="fixed 15:15 (MCX 23:15, CDS 16:45) for intraday bots and backtests"),
    _f(X, "incomplete_legs_action", "A multi-leg order fills only in part", "choice",
       "What happens to the filled legs when you do not answer in time.", 3, 30,
       choices=(("CLOSE_FILLED", "Close the filled legs"), ("KEEP", "Keep them")), today="smart_execution.on_no_answer"),
    _f(X, "incomplete_legs_answer_minutes", "Time to answer", "whole", "Minutes you have to answer before your choice above applies.",
       3, 30, **_count("minutes"), today="smart_execution.answer_minutes"),
    _f(X, "combined_measure", "Combined exit measured on", "choice", "What an exit for a whole structure watches.", 3, 31,
       choices=(("MTM_INR", "Profit or loss in rupees"), ("NET_PREMIUM", "Net premium"), ("SPOT", "The index or stock price")),
       term="premium", today="group exit measure"),
    _f(X, "combined_check_mode", "Combined exit checked", "choice", "When a combined exit counts as reached.", 3, 31,
       choices=TOUCH_CLOSE, today="group exit check_mode"),
    _f(X, "close_if_broker_stop_rejected", "Close a position whose broker stop is refused", "switch",
       "On: a position is closed when the broker refuses its stop order.", 3, 32, today="operator setting LIVE_EXIT_IF_NO_STOP"),
    _f(X, "broker_square_off", "Your broker's intraday close", "fact",
       "Your broker closes intraday (MIS) positions itself near the market close.", 3, 26, term="mis"),
    _f(X, "exits_always_work", "Exits always work", "fact", "No setting holds back a stop, a target or Exit now.", 3, 97),

    # --- Options (PR-4) --------------------------------------------------------------------------------------------------
    _f(OP, "expiry_rule", "Expiry", "choice", "Which expiry a new option or future trades.", 4, 35, 37, 39,
       choices=(("NEAREST", "Nearest"), ("NEXT", "Next"), ("MONTHLY", "Monthly")), term="expiry",
       today="trading_defaults.expiry_rule"),
    _f(OP, "strike_rule", "Strike", "choice", "Which strike a new option trades.", 4, 35, 37, 39,
       choices=(("ATM", "At the money (ATM)"), ("ITM", "In the money"), ("OTM", "Out of the money")), term="atm",
       today="trading_defaults.strike_rule"),
    _f(OP, "strike_offset", "Strikes away from ATM", "whole", "For in or out of the money: how many strikes away.", 4, 35, 37, 39,
       **_count("strikes", 0), term="strike", today="trading_defaults.strike_offset"),
    _f(OP, "option_position", "Option side", "choice", "Whether a new option position is bought or written.", 4, 35, 37,
       choices=(("BUY", "Bought"), ("WRITE", "Written (sold)")), today="trading_defaults.option_position"),
    _f(OP, "spread_width", "Spread width", "whole", "The distance between a spread's legs, in listed strikes.", 4, 23, 36, 37, 39,
       **_count("strikes"), today="trading_defaults.spread_width; strategy_deployments.spread_width"),
    _f(OP, "ratio", "Ratio spread", "whole", "Sold legs for each bought leg in a ratio spread.", 4, 42, **_count("sold legs")),
    _f(OP, "custom_legs", "Custom structure legs", "legs", "The legs a custom structure starts with.", 4, 38),
    _f(OP, "hedge", "Add a protective bought leg", "switch", "On: a written option gets a bought leg further out.", 4),
    _f(OP, "hedge_offset", "Protective leg distance", "whole", "How many strikes further out the bought leg is.", 4,
       **_count("strikes")),
    _f(OP, "delta_tolerance", "Delta tolerance", "number", "How far from your target delta a strike may be.", 4, 43,
       unit="", low=0.0, high=1.0, low_open=True, term="delta"),
    _f(OP, "strike_search_steps", "Strikes searched", "whole", "How many strikes either side are looked at for your filter.", 4, 43,
       **_count("strikes")),
    _f(OP, "expiry_day_roll", "On an expiry day, open the next expiry", "switch",
       "On: on its expiry day the chain and the builders open the next expiry.", 4, 44, term="expiry",
       today="option_chain.expiry_day_next"),
    _f(OP, "settlement_warning", "Physical delivery warning", "switch",
       "On: you are warned before a stock option near its strike reaches expiry.", 4, 45, today="smart_execution.settlement_*"),
    _f(OP, "settlement_days_before", "Warn this many days before expiry", "whole", "When the delivery warning starts.", 4, 45,
       **_count("days", 0), today="smart_execution.settlement_days_before"),
    _f(OP, "settlement_band_pct", "Near the strike means within", "number", "How close to its strike an option counts as near.",
       4, 45, **_pct(), today="smart_execution.settlement_band_pct"),
    _f(OP, "risk_free_rate_pct", "Risk-free rate", "number", "The interest rate used to work out the Greeks.", 4, 100,
       unit="%", low=0.0, high=100.0, term="greeks", today="fixed 7 %"),
    _f(OP, "listed_strikes", "Listed strikes and expiries", "fact", "The exchange lists the strikes and expiries you can trade.",
       4, 47, term="strike"),
    _f(OP, "physical_settlement", "Delivery at expiry", "fact",
       "A stock option held at expiry settles by delivery of the shares (an exchange rule).", 4, 45),

    # --- Risk limits (PR-5). The order-time checks themselves stay on Settings > Risk limits (M64, one store): the
    # losses-in-a-row pause end (TS1-051), two bots on opposite sides (TS1-052) and the first drawdown step's cut (TS1-054)
    # live there too (risk_settings), see ON_RISK_LIMITS_PAGE. Read by app/risk_engine/own_risk.py. ------------------------
    _f(R, "overnight_short_options", "Hold written straddles and strangles overnight", "switch",
       "On: a bot that carries forward may also hold a written straddle or strangle (no wings) for days.", 5, 41,
       today="defined-risk spreads only", wired=True),
    _f(R, "vix_gate", "India VIX check", "switch",
       "On: a fast rise of India VIX pauses new long entries on NIFTY, BANKNIFTY and SENSEX for the day (also when it "
       "cannot be read after the check time); a high level cuts their size. Uses your values below.", 5, 56,
       term="india-vix", today="admin flag + operator settings", wired=True),
    _f(R, "vix_change_pct", "India VIX rise that pauses entries", "number", "A rise of this much pauses new long entries.", 5, 56,
       **_pct(), term="india-vix", wired=True),
    _f(R, "vix_level", "India VIX level that cuts the size", "number", "At or above this level the size is cut.", 5, 56,
       **_pos("points"), term="india-vix", wired=True),
    _f(R, "vix_size_cut_pct", "Size cut at that level", "number", "How much the size is cut at that level.", 5, 56, **_pct(),
       wired=True),
    _f(R, "experience_trades", "Experience level: trades", "whole",
       "With \"Limit me by my experience level\" on: closed paper trades before naked short options open.", 5, 57,
       **_count("trades"), today="100 trades (operator setting)", wired=True),
    _f(R, "experience_sessions", "Experience level: sessions", "whole",
       "With \"Limit me by my experience level\" on: sessions with a paper trade before naked short options open.", 5, 57,
       **_count("sessions"), today="60 sessions (operator setting)", wired=True),
    _f(R, "event_default_action", "A new market event day", "choice",
       "What a market event day you add does to new entries; the form starts from it.", 5, 58,
       choices=(("BLOCK", "Pause new entries"), ("SIZE_CUT", "Cut the size")), today="market event action SIZE_CUT", wired=True),
    _f(R, "ceiling_risk_per_trade_pct", "Team ceiling: risk per trade", "number", "The most any member may set.", 5, 48,
       **_pct(), scope="organisation", wired=True, today="platform ceiling 2 %"),
    _f(R, "ceiling_daily_loss_pct", "Team ceiling: daily loss", "number", "The most any member may set.", 5, 48, **_pct(),
       scope="organisation", wired=True, today="platform ceiling 5 %"),
    _f(R, "ceiling_portfolio_risk_pct", "Team ceiling: risk across positions", "number", "The most any member may set.", 5, 48,
       **_pct(), scope="organisation", wired=True, today="platform ceiling 10 %"),
    _f(R, "ceiling_drawdown_pause_pct", "Team ceiling: drawdown pause", "number", "The most any member may set.", 5, 48, **_pct(),
       scope="organisation", wired=True, today="platform ceiling 25 %"),
    _f(R, "ceiling_warn_at_pct", "Team ceiling: warn at", "number", "A notice when a member's use reaches this share of the limit.",
       5, 59, **_pct(), scope="organisation", wired=True, today="fixed 80 %"),
    _f(R, "broker_exchange_checks", "Broker and exchange checks", "fact",
       "Margin available, F&O ban, market open and the broker's own rejections always apply, and so does your kill switch.",
       5, 95, term="margin"),

    # --- Automation (PR-6) -----------------------------------------------------------------------------------------------
    _f(A, "approval_mode", "New entries", "choice",
       "Whether your bots, alerts, API signals and paper plan tickets place an entry by themselves or ask you first. "
       "A bot follows this unless you chose New entries for that bot itself; Ask me first also turns your paper plan "
       "ticket's Place into a request you approve.", 6, 60, 61,
       choices=(("AUTO", "Placed automatically"), ("APPROVAL", "Ask me first")), today="trading_defaults.entry_mode; bot entry_mode",
       wired=True),
    _f(A, "approval_entry_minutes", "An entry request waits", "whole", "Minutes an entry waiting for your answer stays open.", 6, 62,
       **_count("minutes"), today="operator setting (15 minutes)", wired=True),
    _f(A, "approval_exit_minutes", "An exit request waits", "whole", "Minutes an exit waiting for your answer stays open.", 6, 62,
       **_count("minutes"), today="operator setting (30 minutes)"),
    _f(A, "requote_drift_pct", "New price after a move of", "number", "A waiting entry is offered at the new price after this move.",
       6, 62, **_pct(), today="APPROVAL_REQUOTE_MAX_DRIFT_PCT", wired=True),
    _f(A, "max_requotes", "New prices offered at most", "whole", "How many times a waiting entry is offered at a new price.", 6, 62,
       **_count("times", 0), today="APPROVAL_MAX_REQUOTES", wired=True),
    _f(A, "window_start", "New entries from", "time", "No new entry before this time. Exits always work.", 6, 26, 28),
    _f(A, "window_end", "New entries until", "time", "No new entry after this time. Exits always work.", 6, 26, 28,
       today="fixed 15:00 (MCX 23:00, CDS 16:30) for intraday bots"),
    _f(A, "reentry", "Re-enter after an exit", "switch", "On: a bot may enter again on a new signal after an exit.", 6),
    _f(A, "reentry_max", "Re-entries at most", "whole", "How many times a bot may re-enter.", 6, **_count("times")),
    _f(A, "scan_auto_max", "Scanner entries at most", "whole", "Paper entries the scanner may place by itself in a day.", 6, 63,
       **_count("entries"), today="SCAN_AUTO_PAPER_MAX_PER_DAY", wired=True, flag="scan_auto_paper"),
    _f(A, "scan_auto_cooldown_minutes", "Scanner wait per symbol", "whole", "Minutes before the scanner enters the same symbol again.",
       6, 63, **_count("minutes", 0), today="operator setting (30 minutes)", wired=True, flag="scan_auto_paper"),
    _f(A, "scan_auto_arming_days", "Scanner stays armed for", "whole", "Days the scanner keeps placing paper entries once armed.",
       6, 63, **_count("days"), today="operator setting (7 days)", wired=True, flag="scan_auto_paper"),
    _f(A, "market_hours", "Market hours and holidays", "fact", "Orders reach the market only while the exchange is open.", 6, 84),

    # --- Paper account (PR-7) --------------------------------------------------------------------------------------------
    _f(PA, "capital", "Starting capital", "number", "The money your paper account starts with.", 7, 64, 65, 66, 67, **_pos("₹"),
       term="paper", today="₹10,00,000 (server) / ₹1,00,000 (Setup)"),
    _f(PA, "charges_model", "Charges", "choice", "What a paper trade pays.", 7, 69,
       choices=(("STATUTORY", "Statutory charges only"), ("STATUTORY_BROKERAGE", "Statutory charges + my brokerage")),
       today="₹20 per order + 0.02 % slippage"),
    _f(PA, "brokerage_per_order", "Brokerage per order", "number", "What your broker charges for one order.", 7, 69, 70, 71,
       unit="₹", low=0.0, high=BIG, today="fixed ₹20"),
    _f(PA, "slippage_pct", "Slippage", "number", "How far from the price a paper fill is counted, each side.", 7, 69,
       unit="%", low=0.0, high=100.0, today="fixed 0.02 %"),
    _f(PA, "statutory_charges", "Statutory charges", "fact", "STT, exchange, stamp duty, GST and the SEBI fee are set by law.", 7, 90),
)
# TS1-049 / 050 / 053 ("Risk limits.*", one store): the M64 order-time checks, each its own switch, stay on Settings > Risk
# limits (app/risk_engine/risk_limits.py CHECKS) - the Risk limits section links there instead of declaring them twice.
ON_RISK_LIMITS_PAGE: tuple[str, ...] = ("TS1-049", "TS1-050", "TS1-051", "TS1-052", "TS1-053", "TS1-054")
# The organisation-scope ceilings (TS1-048 / 059) are set by a platform admin (app/platform/controls.py); a member sees
# them here read-only. field key -> the ceiling's key there.
CEILING_OF: dict[str, str] = {"risk_limits.ceiling_risk_per_trade_pct": "risk_per_trade_pct",
                              "risk_limits.ceiling_daily_loss_pct": "max_daily_loss_pct",
                              "risk_limits.ceiling_portfolio_risk_pct": "max_portfolio_risk_pct",
                              "risk_limits.ceiling_drawdown_pause_pct": "dd_level_2_pct",
                              "risk_limits.ceiling_warn_at_pct": "warn_at_pct"}
BY_KEY: dict[str, SettingField] = {f.key: f for f in FIELDS}
assert len(BY_KEY) == len(FIELDS), "a Trade settings key is declared twice"
assert all(f.section in SECTION_IDS for f in FIELDS)


# --- values ----------------------------------------------------------------------------------------------------------------

def _number_ok(f: SettingField, v: float) -> str | None:
    if not math.isfinite(v):
        return "must be a number"
    if f.low is not None and (v <= f.low if f.low_open else v < f.low):
        return f"must be more than {f.low:g}" if f.low_open else f"must be at least {f.low:g}"
    if f.high is not None and v > f.high:
        return f"must be at most {f.high:g}"
    return None


def _legs_ok(v: Any) -> str | None:
    if not isinstance(v, list) or not 1 <= len(v) <= MAX_LEGS:
        return f"must be 1 to {MAX_LEGS} legs"
    for leg in v:
        if not isinstance(leg, dict) or set(leg) != {"right", "side", "strike_rule", "offset", "ratio"}:
            return "each leg needs right, side, strike_rule, offset and ratio"
        if leg["right"] not in ("CE", "PE") or leg["side"] not in ("BUY", "SELL") or leg["strike_rule"] not in ("ATM", "ITM", "OTM"):
            return "a leg has a value that is not allowed"
        for k, top in (("offset", 20), ("ratio", 4)):        # TS1-047: listed strikes / the broker's basket (facts)
            n = leg[k]
            if isinstance(n, bool) or not isinstance(n, int) or not (0 if k == "offset" else 1) <= n <= top:
                return f"a leg's {k} is not allowed"
    return None


def problem(f: SettingField, v: Any) -> str | None:
    """Why `v` is not a valid value of `f` (plain words), or None. None itself (Not set) is always valid."""
    if v is None:
        return None
    if f.kind == "choice":
        return None if isinstance(v, str) and v in {c for c, _ in f.choices} else "is not one of the choices"
    if f.kind == "switch":
        return None if isinstance(v, bool) else "must be on or off"
    if f.kind == "time":
        return None if isinstance(v, str) and TIME.match(v) else "must be a time like 09:30"
    if f.kind == "whole":
        if isinstance(v, bool) or not isinstance(v, int):
            return "must be a whole number"
        return _number_ok(f, float(v))
    if f.kind == "number":
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return "must be a number"
        return _number_ok(f, float(v))
    if f.kind == "legs":
        return _legs_ok(v)
    return "is set by the exchange or the broker"           # a fact is never stored


def storable(f: SettingField) -> bool:
    return not f.fact and f.scope == "user"


def clean(values: Any) -> dict[str, Any]:
    """A stored `values` map as this release reads it: unknown keys, facts and values that no longer validate are dropped
    (that field reads "Not set"); the rest is kept, so one bad value never loses the others."""
    if not isinstance(values, dict):
        return {}
    out: dict[str, Any] = {}
    for key, v in values.items():
        f = BY_KEY.get(key)
        if f is not None and storable(f) and v is not None and problem(f, v) is None:
            out[key] = float(v) if f.kind == "number" else v
    return out


def is_admin(user: User) -> bool:
    return user.role == UserRole.SUPER_ADMIN.value


def visible(f: SettingField, admin: bool, off: frozenset[str] = frozenset()) -> bool:
    """A user sees a fact and a wired field; an unwired field (nothing reads it yet) only a platform admin, under Advanced.
    A field of a feature that is off for him (`off`: flag names, M68 PR-6) is not shown to a user at all."""
    if f.flag is not None and f.flag in off and not admin:
        return False
    return f.fact or f.wired or admin


async def flags_off(session: AsyncSession, tenant_id: int) -> frozenset[str]:
    """The feature flags of the registry's fields that are off for this organisation."""
    from app.platform.controls import flag_enabled
    off: set[str] = set()
    for name in {f.flag for f in FIELDS if f.flag is not None}:
        if not await flag_enabled(session, name, tenant_id):
            off.add(name)
    return frozenset(off)


def view(values: dict[str, Any], admin: bool, ceilings: dict[str, float] | None = None,
         off: frozenset[str] = frozenset()) -> dict[str, Any]:
    """`ceilings`: the organisation's ceilings in force (app/platform/controls.risk_ceilings) - an organisation-scope field
    shows its ceiling read-only (null = no ceiling) and is never one the user saved. `off`: the flags off for him (PR-6)."""
    sections = []
    for s in SECTIONS:
        fields = []
        for f in FIELDS:
            if f.section != s.id or not visible(f, admin, off):
                continue
            org = f.scope == "organisation"
            value = (ceilings or {}).get(CEILING_OF.get(f.key, "")) if org else values.get(f.key)
            fields.append({"key": f.key, "label": f.label, "kind": f.kind, "help": f.help, "unit": f.unit,
                           "choices": [{"value": c, "label": label} for c, label in f.choices],
                           "low": f.low, "high": f.high, "low_open": f.low_open, "term": f.term, "fact": f.fact,
                           "wired": f.wired, "scope": f.scope, "value": value,
                           "saved": (value is not None) if org else f.key in values})
        sections.append({"id": s.id, "title": s.title, "fields": fields,
                         "today": {"label": s.today[0], "path": s.today[1]} if s.today else None})
    return {"sections": sections, "admin": admin, "page": PAGE_PATH}


def check_stored(values: dict[str, Any]) -> dict[str, Any]:
    """The `trade_settings` prefs namespace's own check (app/me/prefs.py TradeSettingsPrefs): every key a storable field,
    every value valid; a field is left out rather than stored as null."""
    for key, v in values.items():
        f = BY_KEY.get(key)
        if f is None or not storable(f):
            raise ValueError(f"{key}: not a Trade setting you can save")
        why = problem(f, v) if v is not None else "leave the field out instead"
        if why:
            raise ValueError(f"{key}: {why}")
    return values


# --- routes ---------------------------------------------------------------------------------------------------------------

router = APIRouter(prefix="/api/me", tags=["me"])


class TradeSettingsSave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[str, Any]


def _changes(body: TradeSettingsSave, admin: bool, off: frozenset[str] = frozenset()) -> dict[str, Any]:
    """The fields sent, each checked against the registry; any problem refuses the whole save (422, nothing stored)."""
    problems: list[dict[str, Any]] = []
    for key, v in body.values.items():
        f = BY_KEY.get(key)
        if f is None:
            problems.append({"loc": ["values", key], "msg": "not a Trade setting"})
        elif f.fact:
            problems.append({"loc": ["values", key], "msg": "set by the exchange or the broker, not a setting"})
        elif f.scope != "user":
            problems.append({"loc": ["values", key], "msg": "set by your admin for the organisation"})
        elif not visible(f, admin, off):
            problems.append({"loc": ["values", key], "msg": "not available yet"})
        elif (why := problem(f, v)) is not None:
            problems.append({"loc": ["values", key], "msg": f"{f.label} {why}"})
    if problems:
        raise HTTPException(status_code=422, detail=problems)
    return dict(body.values)


async def _ceilings(user: User, session: AsyncSession) -> dict[str, float]:
    from app.platform.controls import risk_ceilings
    return await risk_ceilings(session, user.tenant_id)


async def _read(user: User, session: AsyncSession) -> tuple[dict[str, Any], int]:
    from app.me import prefs  # prefs imports this module for its namespace
    value, version = await prefs.read_one(NAMESPACE, user, session)
    return clean(value.get("values")), version


@router.get("/trade-settings")
async def get_trade_settings(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """The caller's own Trade settings: every section and field, its value or null ("Not set") and whether he saved it."""
    values, _version = await _read(user, session)
    return view(values, is_admin(user), await _ceilings(user, session), await flags_off(session, user.tenant_id))


@router.put("/trade-settings")
async def put_trade_settings(body: TradeSettingsSave, user: User = Depends(get_current_user),
                             session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Saves only the fields sent (null: back to "Not set"); every other field stays exactly as it was."""
    from app.me import prefs
    admin = is_admin(user)
    off = await flags_off(session, user.tenant_id)
    changes = _changes(body, admin, off)
    for attempt in (1, 2, 3):                                  # another device saved in between: merge onto its value
        values, version = await _read(user, session)
        for key, v in changes.items():
            if v is None:
                values.pop(key, None)
            else:
                values[key] = float(v) if BY_KEY[key].kind == "number" else v
        try:
            await prefs.save(NAMESPACE, json.dumps({"values": values}).encode(), version, user, session)
            return view(values, admin, await _ceilings(user, session), off)
        except HTTPException as exc:
            if exc.status_code != 409 or attempt == 3:
                raise
    raise HTTPException(status_code=409, detail={"message": "the settings changed since this edit was made"})


__all__ = ["router", "FIELDS", "BY_KEY", "SECTIONS", "SECTION_IDS", "SettingField", "Section", "check_stored", "NAMESPACE",
           "PAGE_PATH", "ON_RISK_LIMITS_PAGE", "CEILING_OF", "clean", "problem", "storable", "view", "visible", "is_admin"]
