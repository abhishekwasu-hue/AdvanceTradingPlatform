import { friendlyError } from "../api/errors";
import { humanise, positionRows, pricedInRupees, todayPnl, type Tone } from "../dashboard/model";
import type {
  GuardianModeStatus, LossStreakPause, LossStreakStatus, MarketEvent, PaperRiskStatus, PortfolioExposure, RiskConfig, RiskLimitType, RiskScope,
  TradeRecord,
} from "../types";
import { strategyDisplayName } from "../ui/strategyName";
import { formatDateIST, formatDateTimeIST, formatINR, formatLots, formatNumber, formatPrice, formatSignedINR } from "../ui/format";

/**
 * UI2-9a (UI v2 proposal §2 rows 14, 16, 17; §8 "Cards and numbers"; §9; §13 UI-11): what the v2 My positions, My
 * portfolio and Risk limits pages show, worked out from the existing API answers. Pure functions only - the pages render
 * these, the unit tests check them, and nothing here fetches, writes or places an order.
 *
 * Wording (§3, §9): plain English for a trader, no developer terms, no advice and no claims about results. Numbers go
 * through src/ui/format.ts. Colour is a meaning chosen here (up / down for price and P&L only, warn for a real warning)
 * and drawn with the semantic tokens by the pages.
 */

// --- My positions -----------------------------------------------------------------------------------------------------

export interface Level { label: string; value: string; tone: Tone }

export interface HeldRow {
  id: number;
  symbol: string;
  mode: "Paper" | "Live";
  paper: boolean;
  side: "Buy" | "Sell";
  /** "Buy · 2 lots (150) · avg 121.80". */
  detail: string;
  /** "On NIFTY 50 (Long):" before the levels of an option or future, which are the underlying's. */
  levelsOn: string | null;
  levels: Level[];
  /** "Iron condor · short put leg" for a leg of a multi-leg structure. */
  structure: string | null;
  /** "Credit 45.00 · most it can lose 155.00 a unit · closes at or below 20.00, or at or above 90.00". */
  structureNote: string | null;
  strategy: string;
  /** The symbol the Charts page opens: the underlying for an option or future. */
  chartSymbol: string;
  ltp: number | null;
  pnl: number | null;
  pnlPct: number | null;
  /** UX-15 (owner, 28 Oct - the broker-standard Positions row): the contract without its expiry ("NIFTY 22550 PE"),
   * the expiry as a short chip ("3 Nov", null for a stock), the net quantity signed (+ long, - short) with its lots
   * ("+65 (1 lot)") and the average entry price. */
  instrument: string;
  expiryLabel: string | null;
  qtyText: string;
  avg: number;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-11-03" -> "3 Nov" (a calendar day, no time zone shift). */
export function expiryLabel(iso: string | null | undefined): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ?? "");
  return m ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]}` : null;
}

const SYMBOL_EXPIRY = /\s+(\d{1,2})\s+([A-Za-z]{3})\s+\d{2,4}$/;

/** The contract name without a trailing expiry ("NIFTY 22550 PE 03 NOV 26" -> "NIFTY 22550 PE") when the expiry is known. */
export function instrumentName(symbol: string, expiry: string | null | undefined): string {
  return expiry || symbolExpiryLabel(symbol) ? symbol.replace(SYMBOL_EXPIRY, "") : symbol;
}

/** "3 Nov" from a contract name that ends in its expiry ("NIFTY 22550 PE 03 NOV 26"), for a position stored without one. */
export function symbolExpiryLabel(symbol: string): string | null {
  const m = SYMBOL_EXPIRY.exec(symbol);
  const month = m ? MONTHS.findIndex((x) => x.toLowerCase() === m[2].toLowerCase()) : -1;
  return m && month >= 0 ? `${Number(m[1])} ${MONTHS[month]}` : null;
}

/** "+65 (1 lot)" / "-130 (2 lots)" / "+10": the net quantity, signed by side, with its lots when the lot size is known. */
export function qtyText(quantity: number, lotSize: number | null | undefined, long: boolean): string {
  const n = `${long ? "+" : "−"}${formatNumber(Math.abs(quantity), Math.abs(quantity) % 1 ? 2 : 0)}`;
  if (!lotSize || lotSize <= 1) return n;
  const lots = Math.abs(quantity) / lotSize;
  return Number.isInteger(lots) ? `${n} (${lots} lot${lots === 1 ? "" : "s"})` : n;
}

const isDerivative = (p: TradeRecord) => !!p.instrument_kind && p.instrument_kind !== "UNDERLYING" && !!p.underlying_symbol;

function sideWord(direction: string | null | undefined): string {
  const d = (direction ?? "").toUpperCase();
  return d === "SHORT" ? "Short" : d === "LONG" ? "Long" : "";
}

/** The stop and target of a position, as the screen shows them (stop red, target green - §8). */
/** Owner fix T1-6: a position without a stop of its own reads "no stop" - never "–" or the one-tick stop a bought option
 *  without one is stored with (backend paper/fno_ticket.py position_stop: the whole premium at risk). */
export const NO_STOP = "no stop";
const PLACEHOLDER_STOP_MAX = 0.1;
export function stopText(v: number | null | undefined, placeholder = false): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return NO_STOP;
  if (placeholder && v <= PLACEHOLDER_STOP_MAX) return NO_STOP;
  return formatPrice(v);
}
/** A bought option's stored stop of one tick is the "no stop of his own" placeholder. */
function premiumPlaceholder(p: TradeRecord): boolean {
  return p.instrument_kind === "OPTION" && p.option_position !== "WRITE";
}

export function positionLevels(p: TradeRecord): { on: string | null; levels: Level[] } {
  if (isDerivative(p)) {
    const side = sideWord(p.underlying_direction);
    const levels: Level[] = [
      { label: "Stop", value: stopText(p.underlying_stop_loss), tone: "down" },
      { label: "Target", value: formatPrice(p.underlying_target1), tone: "up" },
    ];
    if (p.instrument_kind === "OPTION") {
      levels.push({ label: p.option_position === "WRITE" ? "Premium ceiling" : "Premium floor", value: stopText(p.stop_loss, premiumPlaceholder(p)), tone: "neutral" });
    } else {
      levels.push({ label: "Contract stop", value: stopText(p.stop_loss), tone: "neutral" });
    }
    return { on: `On ${p.underlying_symbol}${side ? ` (${side})` : ""}:`, levels };
  }
  return {
    on: null,
    levels: [
      { label: "Stop", value: stopText(p.stop_loss, premiumPlaceholder(p)), tone: "down" },
      { label: "Target", value: formatPrice(p.target1), tone: "up" },
    ],
  };
}

/** "Iron condor · short put leg", and its credit, worst case and exit levels (a leg of an option structure). */
export function structureLabel(p: TradeRecord): { label: string | null; note: string | null } {
  if (!p.leg_group_id) return { label: null, note: null };
  const name = humanise((p.option_strategy ?? "structure").toLowerCase());
  const role = p.leg_role ? ` · ${p.leg_role.replace(/_/g, " ").toLowerCase()} leg` : "";
  const m = p.group_meta;
  const note = m
    ? [
      `Credit ${formatPrice(m.net_credit)}`,
      m.max_loss !== null && m.max_loss !== undefined ? `most it can lose ${formatPrice(m.max_loss)} a unit` : "",
      `closes at or below ${formatPrice(m.target_value)}, or at or above ${formatPrice(m.stop_value)}`,
    ].filter(Boolean).join(" · ")
    : null;
  return { label: `${name}${role}`, note };
}

/** Every open position as a row: size, levels, live price and P&L (null until the symbol has a live price in rupees). */
export function heldRows(positions: TradeRecord[], exposure: PortfolioExposure | null | undefined): HeldRow[] {
  const base = positionRows(positions, exposure, positions.length);
  return positions.map((p, i) => {
    const row = base[i];
    const { on, levels } = positionLevels(p);
    const s = structureLabel(p);
    return {
      id: p.id, symbol: p.symbol, mode: row.mode, paper: p.mode !== "LIVE", side: row.side, detail: row.detail,
      levelsOn: on, levels, structure: s.label, structureNote: s.note, strategy: strategyDisplayName(p.strategy_id),
      chartSymbol: isDerivative(p) ? (p.underlying_symbol as string) : p.symbol,
      ltp: row.ltp, pnl: row.pnl, pnlPct: row.pnlPct,
      instrument: instrumentName(p.symbol, p.expiry), expiryLabel: expiryLabel(p.expiry) ?? symbolExpiryLabel(p.symbol),
      qtyText: qtyText(p.quantity, p.lot_size, p.direction !== "SHORT"), avg: p.entry_price,
    };
  });
}

export interface HoldingTiles {
  open: number;
  paper: number;
  live: number;
  /** Null while any open symbol has no live price in rupees (never a P&L at the entry price passed off as real). */
  openPnl: number | null;
  /** AUD-V2: "last mark 15:29 IST" when the open P&L is at stored marks, else null (live prices). */
  priceNote?: string | null;
  booked: number | null;
  riskAtStops: number | null;
  riskPct: number | null;
}

export function holdingTiles(positions: TradeRecord[] | null, exposure: PortfolioExposure | null): HoldingTiles {
  const list = positions ?? [];
  const live = list.filter((p) => p.mode === "LIVE").length;
  const pnl = todayPnl(exposure);
  return {
    open: list.length, paper: list.length - live, live,
    openPnl: list.length === 0 ? (exposure ? 0 : null) : pnl.open,
    priceNote: pnl.mark ?? null,
    booked: pnl.booked,
    riskAtStops: exposure ? exposure.risk_at_stops : null,
    riskPct: exposure ? exposure.risk_pct_of_capital : null,
  };
}

/** "3 paper · 1 live", "2 paper", "No open positions". User-friendly pass item 2: while `phase0_paper_only` is on
 * (`liveHidden`) no LIVE count and no LIVE word: "2 practice positions". */
export function openCaption(t: Pick<HoldingTiles, "open" | "paper" | "live">, liveHidden = false): string {
  if (t.open === 0) return "No open positions";
  if (liveHidden) return `${t.paper} practice ${t.paper === 1 ? "position" : "positions"}`;
  return [t.paper ? `${t.paper} paper` : "", t.live ? `${t.live} live` : ""].filter(Boolean).join(" · ");
}

/** User-friendly pass item 2: the words for "every entry" - "paper and live" only once LIVE is in play. */
export function entriesWord(liveHidden: boolean): string {
  return liveHidden ? "every entry" : "every paper and live entry";
}

/** The confirm text of "Exit now" - the page's existing wording, kept exactly (UI-11: panic exits keep their confirm). */
export function exitConfirmText(p: Pick<TradeRecord, "direction" | "symbol" | "quantity">): string {
  return `Exit ${p.direction} ${p.symbol} x${p.quantity} (PAPER) now, at the current price?`;
}

/** "Closed (target 1 hit) at 130.00: +₹1,230.00." after an exit, or a price check that reached a level. */
export function closedText(price: number | null | undefined, pnl: number | null | undefined, reason?: string | null): string {
  const what = reason ? `Closed (${humanise(reason.toLowerCase())})` : "Closed";
  return `${what} at ${formatPrice(price)}: ${formatSignedINR(pnl, { paise: true })}.`;
}

/** A failed action, as "what happened" plus the reason in plain words (the API's own sentence, never raw text). */
export function failedText(what: string, e: unknown): string {
  const why = friendlyError(e).trim();
  return why ? `${what} ${why}` : `${what} Try again in a moment.`;
}

// --- Closed trades ----------------------------------------------------------------------------------------------------

export interface ClosedRow {
  id: number;
  symbol: string;
  side: "Long" | "Short";
  mode: "Paper" | "Live";
  size: string;
  prices: string;
  when: string;
  reason: string | null;
  pnl: number | null;
  charges: string;
  chargesActual: boolean;
  strategy: string;
  notes: string | null;
  tags: string[];
}

/** Closed trades, the most recently closed first. */
export function closedRows(trades: TradeRecord[]): ClosedRow[] {
  return trades
    .filter((t) => t.exit_time !== null)
    .sort((a, b) => String(b.exit_time).localeCompare(String(a.exit_time)))
    .map((t) => ({
      id: t.id, symbol: t.symbol, side: t.direction === "SHORT" ? "Short" : "Long", mode: t.mode === "LIVE" ? "Live" : "Paper",
      size: formatLots(t.quantity, t.lot_size),
      prices: `${formatPrice(t.entry_price)} → ${formatPrice(t.exit_price)}`,
      when: formatDateIST(t.exit_time),
      reason: t.exit_reason ? humanise(t.exit_reason.toLowerCase()) : null,
      pnl: t.pnl,
      charges: formatINR(t.charges, { paise: true }),
      chargesActual: t.charges_source === "CONTRACT_NOTE",
      strategy: strategyDisplayName(t.strategy_id),
      notes: t.notes?.trim() ? t.notes.trim() : null,
      tags: t.tags ?? [],
    }));
}

// --- My portfolio -----------------------------------------------------------------------------------------------------

/** Above this share of capital in one symbol the row is marked as concentrated (today's page used the same mark). */
export const CONCENTRATED_PCT = 40;

export interface AllocationRow {
  symbol: string;
  positions: number;
  amount: number;
  /** Share of capital (from the exposure), or of the money in open positions when only entry prices are known. */
  pct: number;
  pctOf: "capital" | "invested";
  unrealised: number | null;
  risk: number | null;
  strategies: string;
  concentrated: boolean;
}

/** Where the money sits, the largest first: from the exposure when it loaded, else from entry price x quantity. */
export function allocationRows(positions: TradeRecord[] | null, exposure: PortfolioExposure | null): AllocationRow[] {
  if (exposure) {
    const priced = new Set(exposure.priced_symbols ?? []);
    const live = pricedInRupees(exposure);
    return [...(exposure.by_symbol ?? [])]
      .sort((a, b) => b.notional - a.notional)
      .map((r) => ({
        symbol: r.symbol, positions: r.positions, amount: r.notional, pct: r.pct_of_capital, pctOf: "capital",
        unrealised: live && priced.has(r.symbol) ? r.unrealised_pnl : null,
        risk: r.risk_at_stop, strategies: r.strategies.map((s) => strategyDisplayName(s)).join(", "),
        concentrated: r.pct_of_capital > CONCENTRATED_PCT,
      }));
  }
  const by = new Map<string, { amount: number; positions: number; strategies: Set<string> }>();
  for (const p of positions ?? []) {
    const cur = by.get(p.symbol) ?? { amount: 0, positions: 0, strategies: new Set<string>() };
    cur.amount += Math.abs(p.entry_price * p.quantity);
    cur.positions += 1;
    if (p.strategy_id) cur.strategies.add(strategyDisplayName(p.strategy_id));
    by.set(p.symbol, cur);
  }
  const total = [...by.values()].reduce((a, v) => a + v.amount, 0);
  return [...by.entries()]
    .sort((a, b) => b[1].amount - a[1].amount)
    .map(([symbol, v]) => ({
      symbol, positions: v.positions, amount: v.amount, pct: total > 0 ? (v.amount / total) * 100 : 0, pctOf: "invested",
      unrealised: null, risk: null, strategies: [...v.strategies].join(", "), concentrated: false,
    }));
}

export interface PortfolioTiles {
  invested: number | null;
  investedPct: number | null;
  openPnl: number | null;
  booked: number | null;
  riskAtStops: number | null;
  riskPct: number | null;
}

/** The four tiles: money in open positions, open P&L, booked P&L (every closed trade, paper and live together - the
 * tile says so), risk at the stops. */
export function portfolioTiles(positions: TradeRecord[] | null, trades: TradeRecord[] | null, exposure: PortfolioExposure | null): PortfolioTiles {
  const fallback = positions ? positions.reduce((a, p) => a + Math.abs(p.entry_price * p.quantity), 0) : null;
  const booked = trades ? trades.filter((t) => t.exit_time !== null).reduce((a, t) => a + (t.pnl ?? 0), 0) : null;
  const pnl = todayPnl(exposure);
  return {
    invested: exposure ? exposure.gross_notional : fallback,
    investedPct: exposure ? exposure.gross_pct_of_capital : null,
    openPnl: exposure && exposure.open_positions === 0 ? 0 : pnl.open,
    booked,
    riskAtStops: exposure ? exposure.risk_at_stops : null,
    riskPct: exposure ? exposure.risk_pct_of_capital : null,
  };
}

/** The running total of booked P&L, one point per closed trade in the order they closed. */
export function bookedCurve(trades: TradeRecord[]): number[] {
  let total = 0;
  return trades
    .filter((t) => t.exit_time !== null)
    .sort((a, b) => String(a.exit_time).localeCompare(String(b.exit_time)))
    .map((t) => (total += t.pnl ?? 0));
}

/** "Long ₹1.20 L · Short ₹40,000 · Net ₹80,000" under the allocation list. */
export function longShortLine(exposure: PortfolioExposure | null): string | null {
  if (!exposure || exposure.open_positions === 0) return null;
  return `Long ${formatINR(exposure.long_notional, { compact: true })} · Short ${formatINR(exposure.short_notional, { compact: true })} · Net ${formatINR(exposure.net_notional, { compact: true })}`;
}

// --- Risk limits ------------------------------------------------------------------------------------------------------

/** The rupee amount of a percentage of the capital (null when either is unknown). */
export function ofCapital(config: Pick<RiskConfig, "capital"> | null, pct: number | null | undefined): number | null {
  if (!config || typeof pct !== "number" || !Number.isFinite(pct) || config.capital == null || !Number.isFinite(config.capital)) return null;
  return (config.capital * pct) / 100;
}

export interface TodayRisk {
  /** The most this account can lose in a day under the daily loss limit on this page (₹). */
  limit: number | null;
  /** That limit as a share of capital, for the caption. */
  limitPct: number | null;
  /** Risk per trade (₹) at the current settings. */
  perTrade: number | null;
  /** Plan before trade's paper loss limit for today (₹) and how much of it is in use (0-100+); null while that feature
   * is off. The rupees are the server's share of the limit, never worked out from anything else. */
  paperLimit: number | null;
  paperUsed: number | null;
  paperUsedPct: number | null;
  lockout: string | null;
}

export function todayRisk(config: RiskConfig | null, paper: PaperRiskStatus | null): TodayRisk {
  const today = paper?.today;
  const share = today && typeof today.used === "number" && Number.isFinite(today.used) ? today.used : null;
  const paperLimit = today && typeof today.limit === "number" && today.limit > 0 ? today.limit : null;
  return {
    limit: ofCapital(config, config?.max_daily_loss_pct), limitPct: config?.max_daily_loss_pct ?? null,
    perTrade: ofCapital(config, config?.risk_per_trade_pct),
    paperLimit,
    paperUsed: paperLimit !== null && share !== null ? paperLimit * share : null,
    paperUsedPct: share === null ? null : share * 100,
    lockout: paper?.lockout ? `New paper trades are paused for today: ${paper.lockout.reason.trim().replace(/\.$/, "")}. Exits keep working.` : null,
  };
}

/** CP6-15: the two choices for "After the losses-in-a-row limit", in plain words. */
export const LOSS_STREAK_CHOICES: { value: LossStreakPause; label: string; help: string }[] = [
  { value: "next_session", label: "Pause until the next session",
    help: "New entries start again at the next trading session." },
  { value: "until_resume", label: "Pause until I resume",
    help: "New entries stay paused until you resume them on this page. A backtest cannot resume, so there the pause lasts to the end of the test." },
];

const inARow = (n: number) => `${n} ${n === 1 ? "loss" : "losses"} in a row`;

/** CP6-15: the pause, when it is active, in one plain sentence (null when new entries are not paused by it). */
export function lossStreakNote(s: LossStreakStatus | null): string | null {
  if (!s || !s.paused) return null;
  const ends = s.mode === "until_resume" ? "They stay paused until you resume them." : "They start again at the next session.";
  return `New entries are paused after ${inARow(s.losses_in_a_row)} (your limit is ${s.limit}). ${ends} Exits keep working.`;
}

/** CP6-15: when the trader last resumed, for the paused note and the setting (null when never). */
export function lastResumeText(s: LossStreakStatus | null): string | null {
  return s?.resumed_at ? `Last resumed ${formatDateTimeIST(s.resumed_at)}.` : null;
}

export interface GuardianLine { mode: "Paper" | "Live"; state: string; tone: Tone; detail: string[] }

/** The risk guardian's state for one mode, in words: normal, size cut by the drawdown ladder, or entries paused. */
export function guardianLine(mode: "PAPER" | "LIVE", m: GuardianModeStatus): GuardianLine {
  const dd = formatNumber(m.drawdown_pct, 1);
  const state = m.state === "paused" ? "New entries paused"
    : m.state === "reduced" ? `Size cut to ${formatNumber(m.size_multiplier * 100, 0)}%`
      : "Normal size";
  const detail = [
    `Drawdown ${dd}% from the peak of ${formatINR(m.peak, { compact: true })}, over ${m.closed_trades} closed ${m.closed_trades === 1 ? "trade" : "trades"}`,
    `Risk at the stops ${formatINR(m.open_risk_total, { compact: true })} (${formatNumber(m.open_risk_pct, 1)}% of capital; the cap is ${formatINR(m.portfolio_cap, { compact: true })})`,
  ];
  const buckets = Object.entries(m.open_risk_by_bucket ?? {});
  if (buckets.length) detail.push(`By group: ${buckets.map(([b, v]) => `${b.toUpperCase() === "INDEX" ? "Indices" : b} ${formatINR(v, { compact: true })}`).join(" · ")}`);
  if (m.cooldowns.length) detail.push(`Waiting after a stop-out: ${m.cooldowns.map((c) => `${c.underlying} (${c.minutes_left} min left)`).join(", ")}`);
  return { mode: mode === "LIVE" ? "Live" : "Paper", state, tone: m.state === "normal" ? "neutral" : "warn", detail };
}

export const EVENT_KINDS: { value: string; label: string }[] = [
  { value: "BUDGET", label: "Budget" },
  { value: "RBI_POLICY", label: "RBI policy" },
  { value: "EXPIRY", label: "Expiry" },
  { value: "RESULTS", label: "Results" },
  { value: "FED", label: "US Fed" },
  { value: "ELECTION", label: "Election" },
  { value: "OTHER", label: "Other" },
];

export function eventKindLabel(kind: string): string {
  return EVENT_KINDS.find((k) => k.value === kind)?.label ?? humanise(kind.toLowerCase());
}

/** Who an event applies to: every symbol, the three indices together, or one symbol. */
export function eventScope(underlying: string | null | undefined): string {
  const u = (underlying ?? "").trim();
  if (!u || u === "*") return "All symbols";
  if (u.toUpperCase() === "INDEX") return "NIFTY, BANK NIFTY and SENSEX";
  return u.toUpperCase();
}

export function eventWindow(start: string | null, end: string | null): string {
  if (!start && !end) return "All day";
  return `${start ?? "00:00"} to ${end ?? "23:59"}`;
}

export function eventAction(ev: Pick<MarketEvent, "action" | "size_cut_pct">, defaultCut: number | null): string {
  if (ev.action === "BLOCK") return "No new entries";
  const cut = ev.size_cut_pct ?? defaultCut;
  return cut === null || cut === undefined ? "Smaller size" : `Size cut by ${formatNumber(cut, 0)}%`;
}

/** What removing an event changes - the confirm dialog names it (UI-11). */
export function eventRemoveConsequence(ev: MarketEvent): string {
  const who = eventScope(ev.underlying);
  const day = formatDateIST(ev.event_date);
  const where = who === "All symbols" ? "any symbol" : who;
  return ev.action === "BLOCK"
    ? `New entries in ${where} will no longer be blocked on ${day}.`
    : `Trades in ${where} will use their full size on ${day}.`;
}

export const LIMIT_SCOPES: { value: RiskScope; label: string; needsId: string | null }[] = [
  { value: "TENANT", label: "Organisation", needsId: null },
  { value: "USER", label: "One member", needsId: "Member number" },
  { value: "ACCOUNT", label: "One broker account", needsId: "Account number" },
  { value: "PORTFOLIO", label: "All open positions", needsId: null },
  { value: "STRATEGY", label: "One strategy", needsId: "Strategy name" },
  { value: "DEPLOYMENT", label: "One trading bot", needsId: "Trading bot number" },
  { value: "INSTRUMENT", label: "One symbol", needsId: "Symbol" },
];

type Unit = "inr" | "pct" | "count" | "units";
export const LIMIT_TYPES: { value: RiskLimitType; label: string; unit: Unit; help: string }[] = [
  { value: "MAX_DAILY_LOSS", label: "Daily loss", unit: "inr", help: "Booked loss today. Reaching it stops new orders for the organisation." },
  { value: "MAX_STRATEGY_LOSS", label: "Daily loss per strategy", unit: "inr", help: "Booked loss today in one strategy. Reaching it stops that strategy." },
  { value: "MAX_LOSS_PER_TRADE", label: "Loss per trade", unit: "inr", help: "The loss at the stop (for a spread, its worst case)." },
  { value: "MAX_ORDER_VALUE", label: "Order value", unit: "inr", help: "Entry price times quantity." },
  { value: "MAX_POSITION_QUANTITY", label: "Quantity per order", unit: "units", help: "Shares or contract units in one order." },
  { value: "MAX_GROSS_EXPOSURE", label: "Total exposure", unit: "inr", help: "Value of all open positions after the order (use All open positions)." },
  { value: "MAX_SYMBOL_CONCENTRATION_PCT", label: "Share of capital in one symbol", unit: "pct", help: "After the order." },
  { value: "MAX_OPEN_POSITIONS", label: "Open positions", unit: "count", help: "Including the new one." },
  { value: "MAX_TRADES_PER_DAY", label: "Trades per day", unit: "count", help: "New entries in one day." },
  { value: "MAX_CAPITAL_ALLOCATION_PCT", label: "Share of capital per order", unit: "pct", help: "Order value as a share of capital." },
];

export function limitTypeLabel(t: RiskLimitType): string {
  return LIMIT_TYPES.find((x) => x.value === t)?.label ?? humanise(t.toLowerCase());
}

export function limitScopeLabel(scope: RiskScope, id: string | null | undefined): string {
  if (scope === "GLOBAL") return "Platform";
  const label = LIMIT_SCOPES.find((s) => s.value === scope)?.label ?? humanise(scope.toLowerCase());
  return id ? `${label}: ${id}` : label;
}

export function limitValueText(t: RiskLimitType, v: number): string {
  const unit = LIMIT_TYPES.find((x) => x.value === t)?.unit ?? "count";
  if (unit === "inr") return formatINR(v);
  if (unit === "pct") return `${formatNumber(v, Number.isInteger(v) ? 0 : 2)}%`;
  return formatNumber(v, Number.isInteger(v) ? 0 : 2);
}

/** The status of one risk check: Blocked / Warning / Passed (amber only for the first two). */
export function checkStatus(status: "PASS" | "WARN" | "BLOCK"): { label: string; tone: Tone } {
  return status === "BLOCK" ? { label: "Blocked", tone: "warn" } : status === "WARN" ? { label: "Warning", tone: "warn" } : { label: "Passed", tone: "neutral" };
}

/** A limit field's problem in words, or null: not a number, below zero, below the platform's minimum, or above its
 * ceiling. The server checks the same again; this only says it next to the field before anything is sent.
 * REV-H5 (DEF-0): a blank field of a limit that may be "Not set" (`optional`) is no problem - it saves as not set - unless
 * the platform asks for a minimum; only typed text is checked. A field that always holds a number still needs one. */
export function fieldProblem(raw: string, ceiling?: number | null, minimum?: number | null, optional = false): string | null {
  const text = raw.trim();
  const shown = (n: number) => formatNumber(n, Number.isInteger(n) ? 0 : 2);
  const hasMinimum = typeof minimum === "number" && Number.isFinite(minimum) && minimum > 0;
  if (text === "" && optional) return hasMinimum ? `The least allowed is ${shown(minimum as number)}.` : null;
  if (text === "") return "Enter a number.";
  const v = Number(text);
  if (!Number.isFinite(v)) return "Enter a number.";
  if (v < 0) return "Enter 0 or more.";
  if (typeof minimum === "number" && Number.isFinite(minimum) && minimum > 0 && v < minimum) return `The least allowed is ${shown(minimum)}.`;
  if (typeof ceiling === "number" && Number.isFinite(ceiling) && v > ceiling) return `The most allowed is ${shown(ceiling)}.`;
  return null;
}

/** REV-H5: what a limit field shows for a saved value - blank for a limit that is not set (null), never the text "null". */
export function limitDraft(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isFinite(value) ? "" : String(value);
}

/** REV-H5: what a limit field saves - blank is "Not set" (null) for a limit that may be not set (`optional`), else the
 * number typed. Only called once `fieldProblem` found nothing to fix. */
export function limitValue(raw: string, optional: boolean): number | null {
  const text = raw.trim();
  if (text === "") return optional ? null : Number.NaN;
  return Number(text);
}

/** REV-H5 (Risk Medium): the guardian's drawdown sentence, for the levels the trader set only (null or 0 = off, as the
 * guardian reads them); null when neither is set - no rule is stated that is not set. */
export function drawdownSentence(level1: number | null | undefined, level2: number | null | undefined): string | null {
  const on = (v: number | null | undefined): v is number => typeof v === "number" && Number.isFinite(v) && v > 0;
  const pct = (v: number) => `${formatNumber(v, 1)}%`;
  const tail = " A winning streak never raises the size above normal.";
  if (on(level1) && on(level2)) return `The size is halved at ${pct(level1)} below your equity peak and new entries pause at ${pct(level2)}.${tail}`;
  if (on(level1)) return `The size is halved at ${pct(level1)} below your equity peak.${tail}`;
  if (on(level2)) return `New entries pause at ${pct(level2)} below your equity peak.${tail}`;
  return null;
}

/** The drawdown ladder halves the size before it pauses entries: the first level may not be above the second while the
 * pause is on. The server refuses exactly `dd_level_1 > dd_level_2 > 0`; a pause level of 0 turns the pause off, so any
 * halving level is accepted then. Never stricter than the server, so no other change is ever held back by it. */
export function drawdownOrderProblem(level1: string, level2: string): string | null {
  const a = Number(level1.trim());
  const b = Number(level2.trim());
  if (level1.trim() === "" || level2.trim() === "" || !Number.isFinite(a) || !Number.isFinite(b) || !(a > b && b > 0)) return null;
  return `Halve the size at a drawdown no larger than the one that pauses new entries (${formatNumber(b, Number.isInteger(b) ? 0 : 2)}%).`;
}

// --- Orders (UX-10, My trades wave) -----------------------------------------------------------------------------------

/** One fill on the Orders list: a trade's entry, and its exit once closed. Display only - built from /trades. */
export interface OrderFillRow {
  key: string;
  at: string;
  kind: "Entry" | "Exit";
  symbol: string;
  side: "Long" | "Short";
  mode: "Paper" | "Live";
  size: string;
  price: string;
  when: string;
  /** Why it closed (exits only), in plain words. */
  reason: string | null;
  strategy: string;
}

/** Every entry and exit fill, newest first (UI audit H5 / H4: a list that fits a phone, times in IST). */
export function orderFillRows(trades: readonly TradeRecord[]): OrderFillRow[] {
  const rows: OrderFillRow[] = [];
  for (const t of trades) {
    const base = {
      symbol: t.symbol, side: t.direction === "SHORT" ? "Short" as const : "Long" as const,
      mode: t.mode === "LIVE" ? "Live" as const : "Paper" as const,
      size: formatLots(t.quantity, t.lot_size), strategy: strategyDisplayName(t.strategy_id),
    };
    rows.push({ ...base, key: `${t.id}-entry`, at: t.entry_time, kind: "Entry", price: formatPrice(t.entry_price),
      when: formatDateTimeIST(t.entry_time), reason: null });
    if (t.exit_time && t.exit_price !== null) {
      rows.push({ ...base, key: `${t.id}-exit`, at: t.exit_time, kind: "Exit", price: formatPrice(t.exit_price),
        when: formatDateTimeIST(t.exit_time), reason: t.exit_reason ? humanise(t.exit_reason.toLowerCase()) : null });
    }
  }
  return rows.sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
}

/** The Orders page's tiles: all fills, entries, exits and trades still open. */
export function orderTiles(rows: readonly OrderFillRow[]): { fills: number; entries: number; exits: number; open: number } {
  const entries = rows.filter((r) => r.kind === "Entry").length;
  const exits = rows.length - entries;
  return { fills: rows.length, entries, exits, open: entries - exits };
}
