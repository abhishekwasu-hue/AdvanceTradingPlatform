"""Phase V1: the Risk Guardian's engine rules.

The AI strategy builder's "Pro Trader Risk Guardian" spec lists rules every strategy must obey.
Most were already in the engine (stop before entry, size from risk, strictest-wins limits, a
stop that only tightens, defined-risk structures). The ones that were not are here, and they
run on *every* entry - single leg and multi-leg, PAPER and LIVE, whatever built the strategy -
because a rule that only lives in a prompt is a rule the engine cannot keep:

* **R10 - cool-down after a stop-out.** No re-entry in an underlying for
  `stop_cooldown_minutes` after a position in it was closed by a stop (the stop level, a
  premium floor/ceiling, a structure stop, a breached short strike). Target, time and
  square-off exits do not start a cool-down; neither does a kill switch, which blocks by itself.
* **P2/P3 - the drawdown ladder.** Equity is capital plus the realised P&L of every closed
  trade in that mode (PAPER and LIVE are separate ladders). At `dd_level_1_pct` below the peak
  the risk per trade is cut by the trader's own `dd_cut_pct` (P2; M68 PR-5, no cut while it is not set); at `dd_level_2_pct` new entries are paused until the
  drawdown recovers or the operator raises the level after a review (P3). The peak never moves
  down, so a hot streak never raises the size (P4): the multiplier is at most 1.
* **M8 - event blackout.** `market_events` rows (tenant's own, or global ones the operator
  keeps: budget, RBI policy, expiry, results) name a date, optionally a time window and an
  underlying (or the whole index bucket). A BLOCK event refuses entries; a SIZE_CUT one scales
  the risk per trade by `1 - size_cut_pct/100`.
* **R4 - portfolio risk with correlated buckets.** The loss if every open stop hits, plus this
  trade's max loss, must stay within `max_portfolio_risk_pct` of capital. NIFTY, BANKNIFTY,
  FINNIFTY, SENSEX and the other indices form one bucket so short-vol positions across them are
  never counted as diversified; the verdict names the bucket the new trade joins.
* **Ceilings.** `platform.controls.risk_ceilings` clamp every tenant setting at runtime and
  refuse settings above them at the API.

R6 (never add to a loser) holds by construction: a deployment carries one open position and
the engine has no add-to-position path. R7 (a stop only moves the favourable way) is
`trading/exit_rules.py`. Everything here returns reasons and a size multiplier; the callers
(`signal_execution`, `multileg`) reject or shrink the risk per trade before sizing.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import RiskConfig
from app.db.models import MarketEventRecord, TradeRecord
from app.instruments.master import underlying_of
from app.market_data.calendar import IST

logger = logging.getLogger(__name__)

INDEX_UNDERLYINGS = frozenset({"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "SENSEX", "BANKEX"})
INDEX_BUCKET = "INDEX"
# Exit reasons that mean "the market took us out": the ones a cool-down follows.
STOP_EXIT_MARKERS = ("stop", "premium floor", "premium ceiling", "breached")
# M68 PR-5 (TS1-054): the first drawdown step cuts the size by the trader's own `RiskConfig.dd_cut_pct` (was a fixed half).
EVENT_ACTIONS = ("BLOCK", "SIZE_CUT")
EVENT_KINDS = ("BUDGET", "RBI_POLICY", "EXPIRY", "RESULTS", "FED", "ELECTION", "OTHER")


def drawdown_cut(cfg: RiskConfig) -> float:
    """The first drawdown step's size cut as a fraction (0..1): the trader's own `dd_cut_pct`; Not set = 0 (no cut)."""
    pct = cfg.dd_cut_pct
    if pct is None or not 0 < float(pct) <= 100:
        return 0.0
    return float(pct) / 100.0


def bucket_for(symbol: str) -> str:
    """The correlation bucket a symbol's risk counts in: every index in one, else the name."""
    underlying = underlying_of(symbol)
    return INDEX_BUCKET if underlying in INDEX_UNDERLYINGS else underlying


def is_stop_exit(reason: Optional[str]) -> bool:
    text = (reason or "").lower()
    return any(marker in text for marker in STOP_EXIT_MARKERS)


def trade_underlying(trade: TradeRecord) -> str:
    return underlying_of(trade.underlying_symbol or trade.symbol)


@dataclass
class DrawdownState:
    equity: float
    peak: float
    drawdown_pct: float
    closed_trades: int

    def as_dict(self) -> dict:
        return {"equity": round(self.equity, 2), "peak": round(self.peak, 2), "drawdown_pct": round(self.drawdown_pct, 2),
                "closed_trades": self.closed_trades}


@dataclass
class GuardianVerdict:
    reasons: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    size_multiplier: float = 1.0
    drawdown: Optional[DrawdownState] = None
    events: List[MarketEventRecord] = field(default_factory=list)
    cooldown_trade: Optional[TradeRecord] = None

    @property
    def allowed(self) -> bool:
        return not self.reasons


def apply_multiplier(cfg: RiskConfig, multiplier: float) -> RiskConfig:
    """A smaller risk per trade is how every sizer (single leg, structure lots) shrinks."""
    if multiplier >= 1.0 or (cfg.risk_per_trade_pct is None and cfg.risk_per_trade_rupees is None):   # DEF-0: nothing to shrink
        return cfg
    update: dict = {}
    if cfg.risk_per_trade_pct is not None:
        update["risk_per_trade_pct"] = round(cfg.risk_per_trade_pct * max(multiplier, 0.0), 6)
    if cfg.risk_per_trade_rupees is not None:        # M64: the rupee risk per trade shrinks the same way
        update["risk_per_trade_rupees"] = round(cfg.risk_per_trade_rupees * max(multiplier, 0.0), 2)
    return cfg.model_copy(update=update)


async def last_stop_out(session: AsyncSession, tenant_id: int, mode: str, underlying: str, since: datetime) -> Optional[TradeRecord]:
    rows = list(await session.scalars(select(TradeRecord).where(
        TradeRecord.tenant_id == tenant_id, TradeRecord.mode == mode, TradeRecord.exit_time.isnot(None), TradeRecord.exit_time >= since,
    ).order_by(TradeRecord.exit_time.desc())))
    wanted = underlying_of(underlying)
    for trade in rows:
        if trade_underlying(trade) == wanted and is_stop_exit(trade.exit_reason):
            return trade
    return None


async def equity_drawdown(session: AsyncSession, tenant_id: int, mode: str, capital: Optional[float]) -> DrawdownState:
    """Realised equity curve of this mode's closed trades; the peak is the high-water mark."""
    pnls = list(await session.scalars(select(TradeRecord.pnl).where(
        TradeRecord.tenant_id == tenant_id, TradeRecord.mode == mode, TradeRecord.exit_time.isnot(None),
    ).order_by(TradeRecord.exit_time, TradeRecord.id)))
    equity = peak = float(capital if capital is not None and capital > 0 else 0.0)   # DEF-0: no capital known - 0
    for pnl in pnls:
        equity += float(pnl or 0.0)
        peak = max(peak, equity)
    dd = (peak - equity) / peak * 100.0 if peak > 0 else 0.0
    return DrawdownState(equity=equity, peak=peak, drawdown_pct=max(0.0, dd), closed_trades=len(pnls))


def _trade_risk(trade: TradeRecord) -> float:
    return abs(float(trade.entry_price) - float(trade.stop_loss or 0.0)) * float(trade.quantity or 0.0)


async def open_risk_by_bucket(session: AsyncSession, tenant_id: int, mode: str) -> Dict[str, float]:
    """Loss if every open stop hits, by correlation bucket. A multi-leg group counts once, at
    its structure's risk per unit x the 1x leg quantity (Phase U ratios included)."""
    trades = list(await session.scalars(select(TradeRecord).where(
        TradeRecord.tenant_id == tenant_id, TradeRecord.mode == mode, TradeRecord.exit_time.is_(None)).order_by(TradeRecord.id)))
    out: Dict[str, float] = {}
    seen_groups: set = set()
    for trade in trades:
        bucket = bucket_for(trade.underlying_symbol or trade.symbol)
        if trade.leg_group_id:
            if trade.leg_group_id in seen_groups:
                continue
            seen_groups.add(trade.leg_group_id)
            meta = {}
            try:
                meta = json.loads(trade.group_meta or "{}")
            except ValueError:
                pass
            per_unit, base_qty = meta.get("risk_per_unit"), meta.get("quantity")
            if per_unit is not None and base_qty:
                risk = float(per_unit) * float(base_qty)
            else:
                legs = [t for t in trades if t.leg_group_id == trade.leg_group_id]
                risk = sum(_trade_risk(t) for t in legs if t.leg_role == "SHORT") or sum(_trade_risk(t) for t in legs)
        else:
            risk = _trade_risk(trade)
        out[bucket] = out.get(bucket, 0.0) + risk
    return {k: round(v, 2) for k, v in out.items()}


def _event_applies(event: MarketEventRecord, underlying: str, now_ist: datetime) -> bool:
    scope = (event.underlying or "").strip().upper()
    if scope and scope != "*":
        if scope == INDEX_BUCKET:
            if bucket_for(underlying) != INDEX_BUCKET:
                return False
        elif underlying_of(scope) != underlying_of(underlying):
            return False
    hhmm = now_ist.strftime("%H:%M")
    if event.start_time and hhmm < event.start_time:
        return False
    if event.end_time and hhmm > event.end_time:
        return False
    return True


async def events_on(session: AsyncSession, tenant_id: int, day) -> List[MarketEventRecord]:
    return list(await session.scalars(select(MarketEventRecord).where(
        MarketEventRecord.event_date == day,
        (MarketEventRecord.tenant_id.is_(None)) | (MarketEventRecord.tenant_id == tenant_id),
    ).order_by(MarketEventRecord.id)))


async def active_events(session: AsyncSession, tenant_id: int, underlying: str, now_ist: datetime) -> List[MarketEventRecord]:
    return [e for e in await events_on(session, tenant_id, now_ist.date()) if _event_applies(e, underlying, now_ist)]


async def guard_entry(session: AsyncSession, tenant_id: int, *, mode: str, underlying: str, cfg: RiskConfig,
                      now: Optional[datetime] = None, direction: Optional[str] = None, user_id: Optional[int] = None,
                      owner_id: int | None = None) -> GuardianVerdict:
    """The pre-sizing rules: cool-down (R10), drawdown ladder (P2/P3), event blackout (M8), and - with the `vix_gate`
    flag on - the India VIX gate (Z5: a spike pauses new bullish index entries, a high level cuts the size; `direction`
    is the entry's direction on the underlying). Reasons refuse the entry; the multiplier scales the risk per trade of
    an allowed one. Entries only: no exit is ever asked here (ADR-0004). `user_id`: who may see India VIX's values in the
    wording; `owner_id` (default `user_id`): whose own India VIX check applies (M68 PR-5, TS1-056)."""
    asked_at = now                      # the VIX gate keeps its own clock unless a time was given
    now = now or datetime.now(timezone.utc)
    now_ist = now.astimezone(IST)
    verdict = GuardianVerdict()

    minutes = int(cfg.stop_cooldown_minutes or 0)
    if minutes > 0:
        trade = await last_stop_out(session, tenant_id, mode, underlying, now - timedelta(minutes=minutes))
        if trade is not None:
            exit_at = trade.exit_time if trade.exit_time.tzinfo else trade.exit_time.replace(tzinfo=timezone.utc)
            ago = max(0, int((now - exit_at).total_seconds() // 60))
            verdict.cooldown_trade = trade
            verdict.reasons.append(f"Cool-down: {underlying_of(underlying)} was stopped out {ago} min ago ({trade.exit_reason}); "
                                   f"no re-entry for {minutes} min after a stop (rule R10)")

    level_1, level_2 = float(cfg.dd_level_1_pct or 0.0), float(cfg.dd_level_2_pct or 0.0)
    if level_1 > 0 or level_2 > 0:
        dd = await equity_drawdown(session, tenant_id, mode, cfg.capital)
        verdict.drawdown = dd
        if level_2 > 0 and dd.drawdown_pct >= level_2:
            verdict.reasons.append(f"Drawdown ladder: {mode} equity {dd.equity:,.0f} is {dd.drawdown_pct:.1f}% below its peak {dd.peak:,.0f} "
                                   f"(>= {level_2:g}%): new entries paused - review the journal before resuming (rule P3)")
        elif level_1 > 0 and dd.drawdown_pct >= level_1 and (cut := drawdown_cut(cfg)) > 0:
            verdict.size_multiplier *= 1.0 - cut
            verdict.notes.append(f"Drawdown ladder: {dd.drawdown_pct:.1f}% below the {mode} equity peak (>= {level_1:g}%): "
                                 f"risk per trade cut {cut * 100:g}% (rule P2)")

    # M64: the event rules apply only when the trader switched "Market event days" on (Settings > Risk limits).
    for event in (await active_events(session, tenant_id, underlying, now_ist) if cfg.event_rules else []):
        verdict.events.append(event)
        label = f"{event.kind or 'event'}{' - ' + event.description if event.description else ''}"
        if (event.action or "").upper() == "BLOCK":
            verdict.reasons.append(f"Event blackout: {label} today{_window(event)} - no new entries in {event.underlying or 'any symbol'} (rule M8)")
        else:
            cut = float(event.size_cut_pct if event.size_cut_pct is not None else (cfg.event_size_cut_pct or 0.0))
            cut = min(max(cut, 0.0), 100.0)
            if cut >= 100.0:
                verdict.reasons.append(f"Event blackout: {label} today{_window(event)} - size cut 100% (rule M8)")
            elif cut > 0:
                verdict.size_multiplier *= 1.0 - cut / 100.0
                verdict.notes.append(f"Event risk: {label} today{_window(event)} - risk per trade cut {cut:g}% (rule M8)")

    from app.risk_engine import vix_gate
    vix = await vix_gate.check(session, tenant_id, symbol=underlying, direction=direction, now=asked_at, user_id=user_id,
                               owner_id=owner_id)
    verdict.reasons.extend(vix.reasons)
    verdict.notes.extend(vix.notes)
    verdict.size_multiplier *= vix.size_multiplier
    return verdict


def _window(event: MarketEventRecord) -> str:
    if event.start_time or event.end_time:
        return f" ({event.start_time or '00:00'}-{event.end_time or '23:59'} IST)"
    return ""


async def portfolio_risk_block(session: AsyncSession, tenant_id: int, *, mode: str, underlying: str, cfg: RiskConfig,
                               new_max_loss: float) -> Tuple[Optional[str], Dict[str, float]]:
    """R4 after sizing: open risk at the stops plus this trade's max loss against the cap."""
    by_bucket = await open_risk_by_bucket(session, tenant_id, mode)
    cap_pct = float(cfg.max_portfolio_risk_pct or 0.0)
    if cap_pct <= 0 or cfg.capital is None:   # DEF-0: off until the trader sets the cap (and a capital is known)
        return None, by_bucket
    cap = cfg.capital * cap_pct / 100.0
    open_total = sum(by_bucket.values())
    total = open_total + float(new_max_loss)
    bucket = bucket_for(underlying)
    if total > cap + 1e-9:
        return (f"Portfolio risk: open risk at the stops {open_total:,.0f} + this trade {float(new_max_loss):,.0f} = {total:,.0f} "
                f"exceeds {cap_pct:g}% of capital ({cap:,.0f}); bucket {bucket} already holds {by_bucket.get(bucket, 0.0):,.0f} (rule R4)"), by_bucket
    return None, by_bucket


async def status(session: AsyncSession, tenant_id: int, cfg: RiskConfig, now: Optional[datetime] = None) -> dict:
    """What the guardian sees right now, per mode: for the Risk page and the AI's runtime context."""
    now = now or datetime.now(timezone.utc)
    now_ist = now.astimezone(IST)
    modes = {}
    for mode in ("PAPER", "LIVE"):
        dd = await equity_drawdown(session, tenant_id, mode, cfg.capital)
        buckets = await open_risk_by_bucket(session, tenant_id, mode)
        open_total = sum(buckets.values())
        multiplier, state = 1.0, "normal"
        if cfg.dd_level_2_pct and dd.drawdown_pct >= cfg.dd_level_2_pct:
            multiplier, state = 0.0, "paused"
        elif cfg.dd_level_1_pct and dd.drawdown_pct >= cfg.dd_level_1_pct and drawdown_cut(cfg) > 0:
            multiplier, state = 1.0 - drawdown_cut(cfg), "reduced"
        since = now - timedelta(minutes=int(cfg.stop_cooldown_minutes or 0))
        cooldowns = []
        if cfg.stop_cooldown_minutes:
            rows = list(await session.scalars(select(TradeRecord).where(
                TradeRecord.tenant_id == tenant_id, TradeRecord.mode == mode, TradeRecord.exit_time.isnot(None), TradeRecord.exit_time >= since)))
            seen = set()
            for trade in rows:
                key = trade_underlying(trade)
                if key in seen or not is_stop_exit(trade.exit_reason):
                    continue
                seen.add(key)
                exit_at = trade.exit_time if trade.exit_time.tzinfo else trade.exit_time.replace(tzinfo=timezone.utc)
                cooldowns.append({"underlying": key, "exit_reason": trade.exit_reason,
                                  "minutes_left": max(0, int(cfg.stop_cooldown_minutes - (now - exit_at).total_seconds() // 60))})
        capital = cfg.capital or 0.0
        modes[mode] = {
            **dd.as_dict(), "state": state, "size_multiplier": multiplier,
            "open_risk_by_bucket": buckets, "open_risk_total": round(open_total, 2),
            "open_risk_pct": round(open_total / capital * 100.0, 2) if capital > 0 else 0.0,
            # DEF-0: None = no portfolio cap set
            "portfolio_cap": round(capital * float(cfg.max_portfolio_risk_pct) / 100.0, 2) if cfg.max_portfolio_risk_pct and capital > 0 else None,
            "cooldowns": cooldowns,
        }
    return {"as_of": now.isoformat(), "settings": cfg.model_dump(), "modes": modes,
            "events_today": [event_as_dict(e) for e in await events_on(session, tenant_id, now_ist.date())]}


def event_as_dict(event: MarketEventRecord) -> dict:
    return {"id": event.id, "tenant_id": event.tenant_id, "global": event.tenant_id is None, "underlying": event.underlying,
            "event_date": event.event_date.isoformat(), "start_time": event.start_time, "end_time": event.end_time, "kind": event.kind,
            "action": event.action, "size_cut_pct": event.size_cut_pct, "description": event.description,
            "created_at": event.created_at.isoformat() if event.created_at else None}
