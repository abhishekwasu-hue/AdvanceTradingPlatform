"""Signals day one (owner order 23 Oct, "Signals not empty on day one"; README row 19; OPEN_QUESTIONS SIG1-1..6).

A new user opens Signals and finds a short setup card instead of an empty page: "Pick a ready strategy and a list to
watch". The card offers a few ready strategy templates (white box: the idea, every setting with its default, the rules)
and a list (the NIFTY 50 system list by default; another index / sector list or one of his own lists). One tap ADOPTS
the template for him, with its own default settings shown before he confirms, as HIS OWN strategy:

* PAPER only and approval-only entries - by construction: the stored adoption has no mode or entry field a request can
  set (`MODE` / `ENTRIES` are constants written by the server), and nothing here creates a deployment, a proposal or an
  order. The watch only DESCRIBES where each symbol stands. To trade a symbol he starts a trading bot from the existing
  form (the template pre-filled; every entry there waits for his Approve when `approval_only_entries` is on).
* Right after adopting, the adopted logic runs once over the recent CLOSED Daily bars of every symbol on the list - the
  same `engine.compute` the scan, the backtest and the worker read (scan = backtest = live, section 8 of the templates
  order), on the same window the worker fetches (`history_bars`, R6). The page then shows the current structure state per
  symbol ("a setup formed on the latest Daily bar", "waiting for its trigger", "watching for ..."), in structure words.
  It is evaluated again when he opens the page and the last evaluation is older than STALE_SECONDS, or on "Refresh".

Data: the shared daily lake only (`md_candles`, shared-licensed end-of-day sources: NSE bhavcopy, vendor, the operator's
import - never a broker stream of one organisation), bars whose END is not after now (closed bars, no look-ahead),
adjusted for the splits / bonuses known now. `load_daily` is the one seam: when a shared history resolver lands on
main, it replaces this read. Missing data is said in plain words; there is never sample data.

Storage: one internal `user_prefs` row per user (namespace NAMESPACE - not a `/api/me/prefs` namespace, so no prefs
route reads or writes it): the adoption and the last evaluation. No table, no migration. Per user and tenant.

Signal language (CLAUDE.md section 3): every shown text is checked by `app.signals.language.check` on the `signal_own`
surface (his adopted strategy, his chosen symbols): the symbol may be named and the AREA shown as a price range (the
setup bar's range); no buy / sell / target / stop wording and no single price. A text that fails is withheld, never
rewritten.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import Float, case, cast, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MdCandleRecord, User, UserPrefsRecord
from app.market_data.calendar import IST
from app.signals import language as L
from app.strategy_templates import engine, ids, library
from app.strategy_templates.adapter import TemplateStrategy
from app.strategy_templates.language import TIMEFRAME_WORDS
from app.strategy_templates.schema import Template

NAMESPACE = "signals_day_one"          # the internal user_prefs row (never a /api/me/prefs namespace)
FLAG = "strategy_templates"            # with `ui_v2` (the Signals page's own flag)
# The ready strategies the card offers, in this order (beginner-friendly price-action / breakout / pullback ideas that run
# on Daily bars). Only those switched on for the organisation are shown (the templates route's own availability check).
OFFERS: Tuple[str, ...] = ("A5", "B1", "D3")
TIMEFRAME = "1d"                       # Daily: what the shared end-of-day lake holds for every NSE share
DEFAULT_LIST = "index-nifty50"         # the NIFTY 50 system list (app/watchlists/system.py keys)
MAX_WATCH = 60                         # symbols one watch reads (a longer list: its first MAX_WATCH, said in words)
# A display window, not a reading rule: a signal this many Daily bars back or fewer is listed as "formed recently".
RECENT_BARS = 5
STALE_SECONDS = 4 * 3600               # an older evaluation is run again when the page opens
MIN_REFRESH_SECONDS = 60               # "Refresh" more often than this answers the stored evaluation
BUDGET_SECONDS = 20.0                  # one evaluation's time box; symbols left over are "not read yet"
MODE = "PAPER"                         # by construction: no request field sets these
ENTRIES = "APPROVAL"
SHARED_SOURCES = ("nse_bhav:", "vendor:", "r2:")   # as app/charts/price_action_layer.py (never stream:* / broker:*)
DAILY_LABELS = ("day", "1d")
SYSTEM_LIST_GROUPS = ("INDEX_STOCKS", "SECTORS", "FNO")   # lists of shares (the Indices list is not a list of shares)

STATES = ("new", "armed", "recent", "watching", "warmup", "no_data", "not_read")
STATE_ORDER = {s: i for i, s in enumerate(STATES)}
TF_WORD = TIMEFRAME_WORDS[TIMEFRAME]

# What a symbol's state says, in structure words (checked by the tests and again on every render).
WAITING: Dict[str, Dict[str, str]] = {
    "D3": {"long": f"In a rising trend on the {TF_WORD} chart the pullback reached the fast moving average; waiting for a bullish "
                   "reversal there.",
           "short": f"In a falling trend on the {TF_WORD} chart the rally reached the fast moving average; waiting for a bearish "
                    "reversal there."},
}
WAITING_ANY = {"long": f"An upside setup is in place on the {TF_WORD} chart; waiting for its trigger.",
               "short": f"A downside setup is in place on the {TF_WORD} chart; waiting for its trigger."}
WATCHING: Dict[str, str] = {
    "A5": f"No setup in place on the {TF_WORD} chart; watching for a broken level to be retested from the other side.",
    "B1": f"No setup in place on the {TF_WORD} chart; watching for a close beyond a range.",
    "D3": f"No setup in place on the {TF_WORD} chart; watching for a pullback to the fast moving average in a trend.",
}
WATCHING_ANY = f"No setup in place on the {TF_WORD} chart right now."
PLAIN: Dict[str, str] = {
    "warmup": f"Not enough {TF_WORD} history yet to read this setup.",
    "no_data": f"No {TF_WORD} history for this symbol yet.",
    "not_read": "Not read yet; refresh in a moment.",
}
NO_DATA_NOTE = ("No Daily history is stored for these symbols yet, so nothing can be read. It fills after the market's "
                "daily file is loaded; nothing is shown until then.")


# --- the adoption --------------------------------------------------------------------------------------------------------

def strategy_of(strategy_id: str) -> Tuple[Template, TemplateStrategy]:
    """The template and its strategy from the stored id (KeyError: the template changed since; adopt it again)."""
    template, settings = ids.decode(strategy_id)
    return template, TemplateStrategy(template, settings)


def settings_shown(template: Template, params: Dict[str, Any]) -> List[Dict[str, str]]:
    """Every setting as the card shows it before he confirms: its label, its value in words, its help."""
    out: List[Dict[str, str]] = []
    for p in template.params:
        value = params.get(p.key, p.default)
        if p.choices:
            labels = dict(zip(p.choices, p.choice_labels or tuple(TIMEFRAME_WORDS.get(c, c) for c in p.choices)))
            words = labels.get(str(value), str(value))
        elif p.type == "bool":
            words = "Yes" if value else "No"
        else:
            words = f"{value:g}" if isinstance(value, float) else str(value)
        out.append({"key": p.key, "label": p.label, "value": words, "help": p.help})
    return out


def offer(template: Template) -> Dict[str, Any]:
    """One ready strategy on the card: white box (the idea, the rules, every default setting), on Daily bars."""
    _, settings = ids.adopt(template, TIMEFRAME)
    return {"id": template.id, "name": template.name, "idea": template.idea, "difficulty": template.difficulty,
            "family": template.family, "timeframe": TF_WORD, "settings": settings_shown(template, settings.params),
            "rules": [c.message for c in template.constraints]}


# --- the data seam: the shared daily lake ----------------------------------------------------------------------------------

def lake_key(symbol: str, exchange: str) -> Optional[str]:
    """The daily-history key of an NSE share or index ("NSE:RELIANCE", "NSE_INDEX:..."); None off NSE (not read yet)."""
    if (exchange or "NSE").upper() not in ("NSE", "NSE_INDEX"):
        return None
    from app.growth.dishes import lake_key as dish_key
    return dish_key(symbol)


def bar_start(day: date) -> pd.Timestamp:
    """A Daily bar's label: 00:00 IST of its trading day (as a broker's Daily candle), in UTC."""
    return pd.Timestamp(datetime.combine(day, datetime.min.time(), tzinfo=IST)).tz_convert("UTC")


async def load_daily(session: AsyncSession, items: Sequence[Dict[str, str]], now: datetime, bars: int) -> Dict[str, pd.DataFrame]:
    """{symbol: Daily bars} - at most `bars` CLOSED bars (END not after `now`) per symbol from the shared-licensed lake
    sources (preferred source, then the highest version known by now), one bar per trading day, adjusted for the splits /
    bonuses known now. One batched read. A symbol with no stored bar is left out (the caller says "no history")."""
    from app.market_lake.adjust import share_factor
    from app.market_lake.ingest import trade_date
    from app.screener.board import share_actions
    keys: Dict[str, str] = {}
    for item in items:
        key = lake_key(item["symbol"], item.get("exchange", "NSE"))
        if key:
            keys[key] = item["symbol"]
    if not keys:
        return {}
    days = int(bars * 7 / 5) + 21                      # calendar days that hold `bars` sessions, with room for holidays
    since = now - timedelta(days=days)
    n = len(SHARED_SOURCES)
    rank = case(*[(MdCandleRecord.source.like(f"{p}%"), i) for i, p in enumerate(SHARED_SOURCES)], else_=n)
    query = (select(MdCandleRecord.instrument_key, MdCandleRecord.ts, rank, MdCandleRecord.version, cast(MdCandleRecord.open, Float),
                    cast(MdCandleRecord.high, Float), cast(MdCandleRecord.low, Float), cast(MdCandleRecord.close, Float),
                    MdCandleRecord.volume)
             .where(MdCandleRecord.instrument_key.in_(list(keys)), MdCandleRecord.timeframe.in_(DAILY_LABELS),
                    MdCandleRecord.ts > since, MdCandleRecord.ts <= now,
                    or_(*[MdCandleRecord.source.like(f"{p}%") for p in SHARED_SOURCES]),
                    or_(MdCandleRecord.version == 1, MdCandleRecord.ingested_at <= now)))
    best: Dict[str, Dict[date, Tuple[Tuple[int, int], Tuple[float, float, float, float, float]]]] = {}
    for key, ts, r, version, o, h, low, c, v in (await session.execute(query)).all():
        t = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        day = trade_date(t)
        pref = (-int(r), int(version or 0))
        cur = best.setdefault(key, {}).get(day)
        if cur is None or pref > cur[0]:
            best[key][day] = (pref, (float(o), float(h), float(low), float(c), float(v or 0)))
    symbols = [keys[k] for k in best if k.startswith("NSE:")]
    actions = await share_actions(session, symbols, "NSE", now, since) if symbols else {}
    out: Dict[str, pd.DataFrame] = {}
    for key, per_day in best.items():
        symbol = keys[key]
        acts = actions.get(symbol.upper(), []) if key.startswith("NSE:") else []
        rows = [(d, values) for d, (_, values) in sorted(per_day.items())][-bars:]
        factors = [share_factor(acts, d) for d, _ in rows]
        out[symbol] = pd.DataFrame(
            {"open": [v[0] * f for (_, v), f in zip(rows, factors)], "high": [v[1] * f for (_, v), f in zip(rows, factors)],
             "low": [v[2] * f for (_, v), f in zip(rows, factors)], "close": [v[3] * f for (_, v), f in zip(rows, factors)],
             "volume": [v[4] / f if f else v[4] for (_, v), f in zip(rows, factors)]},
            index=pd.DatetimeIndex([bar_start(d) for d, _ in rows]))
    return out


# --- the first-run evaluation (pure) ---------------------------------------------------------------------------------------

def _shown(text: str, symbol: str) -> str:
    """The text, or the fixed withheld line when it is not in the signal language of his own signals."""
    return L.withheld(text, "signal_own", own_symbols=[symbol])


def _area(window: pd.DataFrame, bar: int, side: str) -> Optional[str]:
    """The area as a price range: the setup bar's range ("Buyer area: a–b" for an upside setup)."""
    if bar < 0 or bar >= len(window):
        return None
    low, high = float(window["low"].iloc[bar]), float(window["high"].iloc[bar])
    if not (np.isfinite(low) and np.isfinite(high)):
        return None
    return L.area_label("buyer" if side == "long" else "seller", low, high)


def _day(window: pd.DataFrame, bar: int) -> Optional[str]:
    if bar < 0 or bar >= len(window):
        return None
    return pd.Timestamp(window.index[bar]).tz_convert(IST).date().isoformat()


def read_symbol(strategy: TemplateStrategy, symbol: str, exchange: str, frame: Optional[pd.DataFrame]) -> Dict[str, Any]:
    """Where one symbol stands under the adopted logic, on its last closed Daily bar. The window is the worker's own
    (`history_bars`, R6) and the series is `engine.compute` - the scan / backtest / worker series (parity)."""
    row: Dict[str, Any] = {"symbol": symbol, "exchange": exchange, "state": "no_data", "direction": None, "text": PLAIN["no_data"],
                           "area": None, "setup_day": None, "bar_day": None}
    if frame is None or frame.empty:
        return row
    window = frame.iloc[-strategy.history_bars():]
    row["bar_day"] = _day(window, len(window) - 1)
    if len(window) < strategy.recipe.min_bars:
        return {**row, "state": "warmup", "text": PLAIN["warmup"]}
    series = engine.compute(strategy.recipe, {TIMEFRAME: window})
    t = len(window) - 1
    tid = strategy.template.id

    def signal(state: str, bar: int) -> Dict[str, Any]:
        side = "long" if series.direction[bar] > 0 else "short"
        setup = int(series.setup_bar[bar])
        text = library.signal_text(strategy.template, strategy.settings, side, strategy.recipe.context_timeframe)
        return {**row, "state": state, "direction": "LONG" if side == "long" else "SHORT", "text": _shown(text, symbol),
                "area": _area(window, setup, side), "setup_day": _day(window, bar)}

    if series.direction[t] != 0:
        return signal("new", t)
    if series.armed_end:
        side, setup = max(series.armed_end.items(), key=lambda kv: (kv[1], kv[0] == "long"))
        text = WAITING.get(tid, WAITING_ANY)[side]
        return {**row, "state": "armed", "direction": "LONG" if side == "long" else "SHORT", "text": _shown(text, symbol),
                "area": _area(window, setup, side), "setup_day": _day(window, setup)}
    recent = [b for b in series.signal_bars() if t - b <= RECENT_BARS]
    if recent:
        return signal("recent", recent[-1])
    if series.reason[t] == engine.REASON_WARMUP:
        return {**row, "state": "warmup", "text": PLAIN["warmup"]}
    return {**row, "state": "watching", "text": _shown(WATCHING.get(tid, WATCHING_ANY), symbol)}


def evaluate(strategy: TemplateStrategy, items: Sequence[Dict[str, str]], frames: Dict[str, pd.DataFrame],
             budget: float = BUDGET_SECONDS, clock: Any = time.monotonic) -> List[Dict[str, Any]]:
    """Every symbol's row (pure; runs in a worker thread), most important first: a new setup, one waiting for its trigger,
    a recent one, then the rest; the list's own order within each. Symbols past the time box are "not read yet"."""
    started = clock()
    rows: List[Dict[str, Any]] = []
    for pos, item in enumerate(items):
        symbol, exchange = item["symbol"], item.get("exchange", "NSE")
        if clock() - started > budget:
            row: Dict[str, Any] = {"symbol": symbol, "exchange": exchange, "state": "not_read", "direction": None,
                                   "text": PLAIN["not_read"], "area": None, "setup_day": None, "bar_day": None}
        else:
            try:
                row = read_symbol(strategy, symbol, exchange, frames.get(symbol))
            except Exception:  # noqa: BLE001 - one symbol's failure is "no history", the watch goes on
                row = {"symbol": symbol, "exchange": exchange, "state": "no_data", "direction": None, "text": PLAIN["no_data"],
                       "area": None, "setup_day": None, "bar_day": None}
        rows.append({**row, "_pos": pos})
    rows.sort(key=lambda r: (STATE_ORDER[r["state"]], r["_pos"]))
    return [{k: v for k, v in r.items() if k != "_pos"} for r in rows]


def summary(rows: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    return {s: sum(1 for r in rows if r["state"] == s) for s in STATES}


# --- lists ---------------------------------------------------------------------------------------------------------------

async def list_choices(session: AsyncSession, user: User) -> List[Dict[str, Any]]:
    """The lists he can watch: the system lists of shares (index stocks first, NIFTY 50 at the top) and, with the
    `watchlists` flag on, his own lists. Names and counts only."""
    from app.platform.controls import flag_enabled
    from app.watchlists import service as wl
    from app.watchlists import system
    out: List[Dict[str, Any]] = []
    rows = [r for r in await system.list_all(session) if r.group in SYSTEM_LIST_GROUPS and r.count > 0]
    rows.sort(key=lambda r: (r.key != DEFAULT_LIST, SYSTEM_LIST_GROUPS.index(r.group), r.position, r.name))
    for r in rows:
        out.append({"kind": "system", "key": r.key, "name": r.name, "group": system.GROUP_LABELS.get(r.group, r.group), "count": r.count,
                    "default": r.key == DEFAULT_LIST})
    if await flag_enabled(session, wl.FLAG, user.tenant_id):
        for own in await wl.list_own(session, user.tenant_id, user.id):
            items = wl.items_of(own)
            if items:
                out.append({"kind": "own", "id": own.id, "name": own.name, "group": "My lists", "count": len(items), "default": False})
    return out


async def resolve_list(session: AsyncSession, user: User, kind: str, key: Optional[str], list_id: Optional[int]
                       ) -> Optional[Tuple[Dict[str, Any], List[Dict[str, str]]]]:
    """(the list's reference and name, its items) - a system list of shares, or one of HIS OWN lists; None otherwise."""
    from app.platform.controls import flag_enabled
    from app.watchlists import service as wl
    from app.watchlists import system
    if kind == "system" and key:
        row = await system.get(session, key)
        if row is None or row.group not in SYSTEM_LIST_GROUPS:
            return None
        return {"kind": "system", "key": row.key, "name": row.name}, system.watch_items(row)
    if kind == "own" and list_id is not None and await flag_enabled(session, wl.FLAG, user.tenant_id):
        own = await wl.get_own(session, user.tenant_id, user.id, list_id)     # tenant AND user: never another member's list
        if own is None:
            return None
        return {"kind": "own", "id": own.id, "name": own.name}, [{"symbol": i["symbol"], "exchange": i["exchange"]} for i in wl.items_of(own)]
    return None


# --- the stored row ------------------------------------------------------------------------------------------------------

async def read_row(session: AsyncSession, user: User) -> Tuple[Optional[UserPrefsRecord], Dict[str, Any]]:
    row = await session.scalar(select(UserPrefsRecord).where(UserPrefsRecord.tenant_id == user.tenant_id, UserPrefsRecord.user_id == user.id,
                                                             UserPrefsRecord.namespace == NAMESPACE))
    try:
        value = json.loads(row.value_json) if row is not None else {}
    except ValueError:
        value = {}
    return row, value if isinstance(value, dict) else {}


async def write_row(session: AsyncSession, user: User, value: Dict[str, Any], now: datetime) -> None:
    """Stores his watch (one row per user and tenant); a first write racing another device updates the row it made."""
    tenant_id, user_id = user.tenant_id, user.id
    text = json.dumps(value, separators=(",", ":"))
    for attempt in (1, 2):
        row = await session.scalar(select(UserPrefsRecord).where(UserPrefsRecord.tenant_id == tenant_id, UserPrefsRecord.user_id == user_id,
                                                                 UserPrefsRecord.namespace == NAMESPACE))
        if row is None:
            session.add(UserPrefsRecord(tenant_id=tenant_id, user_id=user_id, namespace=NAMESPACE, value_json=text, version=1, updated_at=now))
        else:
            row.value_json, row.version, row.updated_at = text, row.version + 1, now
        try:
            await session.commit()
            return
        except IntegrityError:
            await session.rollback()
            if attempt == 2:
                raise


async def clear_row(session: AsyncSession, user: User) -> bool:
    row, _ = await read_row(session, user)
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True


# --- the evaluation, stored ----------------------------------------------------------------------------------------------

async def run(session: AsyncSession, strategy: TemplateStrategy, items: Sequence[Dict[str, str]], now: datetime) -> Dict[str, Any]:
    """Loads the closed Daily bars and evaluates the adopted logic once (the pandas work on a worker thread)."""
    frames = await load_daily(session, items, now, strategy.history_bars())
    rows = await asyncio.to_thread(evaluate, strategy, list(items), frames)
    days = [r["bar_day"] for r in rows if r["bar_day"]]
    return {"evaluated_at": now.isoformat(), "bar_day": max(days) if days else None, "rows": rows}


def stale(snapshot: Optional[Dict[str, Any]], now: datetime, seconds: float = STALE_SECONDS) -> bool:
    try:
        at = datetime.fromisoformat(str((snapshot or {})["evaluated_at"]))
    except (KeyError, TypeError, ValueError):
        return True
    at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return (now - at).total_seconds() > seconds


def adoption_value(template: Template, strategy_id: str, list_ref: Dict[str, Any], items: List[Dict[str, str]], truncated: bool,
                   now: datetime) -> Dict[str, Any]:
    """What is stored: the template's strategy id (its settings inside), the list, PAPER and approval-only entries."""
    return {"template_id": template.id, "strategy_id": strategy_id, "list": list_ref, "items": items, "truncated": truncated,
            "mode": MODE, "entries": ENTRIES, "adopted_at": now.isoformat()}


def answer(value: Dict[str, Any], template: Template, strategy: TemplateStrategy) -> Dict[str, Any]:
    """The adopted watch as the page shows it."""
    snapshot = value.get("snapshot") or {}
    rows = list(snapshot.get("rows") or [])
    counts = summary(rows)
    note = NO_DATA_NOTE if rows and counts["no_data"] == len(rows) else None
    return {
        "template": {"id": template.id, "name": template.name, "idea": template.idea, "timeframe": TF_WORD,
                     "settings": settings_shown(template, strategy.settings.params)},
        "strategy_id": value.get("strategy_id"), "list": value.get("list"), "truncated": bool(value.get("truncated")),
        "count": len(value.get("items") or []), "max_watch": MAX_WATCH,
        "mode": MODE, "entries": ENTRIES, "adopted_at": value.get("adopted_at"),
        "evaluated_at": snapshot.get("evaluated_at"), "bar_day": snapshot.get("bar_day"),
        "rows": rows, "summary": counts, "note": note,
    }


__all__ = ["NAMESPACE", "FLAG", "OFFERS", "TIMEFRAME", "DEFAULT_LIST", "MAX_WATCH", "RECENT_BARS", "STALE_SECONDS", "MIN_REFRESH_SECONDS",
           "MODE", "ENTRIES", "STATES", "WAITING", "WAITING_ANY", "WATCHING", "WATCHING_ANY", "PLAIN", "NO_DATA_NOTE",
           "strategy_of", "settings_shown", "offer", "lake_key", "bar_start", "load_daily", "read_symbol", "evaluate", "summary",
           "list_choices", "resolve_list", "read_row", "write_row", "clear_row", "run", "stale", "adoption_value", "answer"]
