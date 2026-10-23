import { BROKER_PATH } from "../broker/paths";
import type { BrokerGateState } from "../ui/brokerSession";
import { brokerDisplayName } from "../ui/brokerSession";
import type { SetupHealthItem } from "../setup/model";
import { paperOnlyFrom } from "../setup/paperOnly";
import { reasonWords, setupWords } from "../signals/signalText";
import { EMPTY, formatDateIST, formatLots, formatPrice, formatTimeIST } from "../ui/format";
import { botDisplayName, strategyDisplayName } from "../ui/strategyName";
import type { OiBannerResponse } from "../oi/types";
import type {
  AiAction, AnalyticsSummary, Deployment, NotificationEntry, PaperRiskStatus, PortfolioExposure, SystemStatus, TradeRecord,
} from "../types";
import type { StripIndex, StripMarket } from "./types";

/**
 * UI2-2 (UI v2 proposal §5): what each dashboard block shows, worked out from the existing API answers. Pure functions
 * only - the blocks render these, the unit tests check them, and nothing here fetches.
 *
 * Wording rules (§3, §9): plain English for a trader; no developer terms, no raw server text in an error; numbers go
 * through src/ui/format.ts; colour is a meaning (up, down, warn) chosen here and drawn with tokens by the blocks.
 */

export const MAX_ATTENTION = 5;
export const MAX_POSITIONS = 5;
export const MAX_SIGNALS = 3;
/** The Today block's win rate covers the trades closed in this many days (§5: "Win rate (30 days)"). */
export const WIN_RATE_DAYS = 30;

const IST_OFFSET_MS = (5 * 60 + 30) * 60 * 1000;
const WEEKDAYS_LONG = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS_LONG = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** The IST calendar day of an instant, as "2026-10-19". */
export function istDay(ms: number): string {
  return new Date(ms + IST_OFFSET_MS).toISOString().slice(0, 10);
}

/** A backend timestamp (UTC, maybe without a zone) as epoch ms; NaN when unreadable. */
export function toMs(v: string | null | undefined): number {
  if (!v) return Number.NaN;
  const text = v.trim();
  const zoned = /([zZ]|[+-]\d{2}:?\d{2})$/.test(text) ? text : `${text.replace(" ", "T")}Z`;
  return Date.parse(zoned);
}

// --- Greeting ---------------------------------------------------------------------------------------------------------

/** The name to greet: the e-mail's local part up to the first dot, dash, underscore or digit, capitalised. */
export function greetingName(email: string | null | undefined): string {
  const local = (email ?? "").split("@")[0] ?? "";
  const first = local.split(/[._\-+\d]/).find(Boolean) ?? "";
  return first ? first[0].toUpperCase() + first.slice(1).toLowerCase() : "";
}

/** "Good morning, Abhi" and "Monday, 19 October" / "09:05", in IST whatever the device's zone. */
export function greeting(nowMs: number, email: string | null | undefined): { hello: string; date: string; time: string } {
  const d = new Date(nowMs + IST_OFFSET_MS);
  const hour = d.getUTCHours();
  const part = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
  const name = greetingName(email);
  return {
    hello: name ? `${part}, ${name}` : part,
    date: `${WEEKDAYS_LONG[d.getUTCDay()]}, ${d.getUTCDate()} ${MONTHS_LONG[d.getUTCMonth()]}`,
    time: formatTimeIST(nowMs, { suffix: false }),
  };
}

// --- Market strip -----------------------------------------------------------------------------------------------------

/** "Market open · closes 15:30", "Pre-open · opens 09:15", "Closed · holiday (Diwali)", "Closed · opens Tue 09:15". */
export function marketLabel(market: StripMarket | null | undefined, nowMs: number): string {
  if (!market) return "Market hours unknown";
  if (market.phase === "open") return `Market open · closes ${market.closes_at}`;
  if (market.phase === "pre-open") return `Pre-open · opens ${market.opens_at}`;
  if (market.reason === "holiday") return market.holiday ? `Closed · holiday (${market.holiday})` : "Closed · holiday";
  const next = toMs(market.next_open);
  if (!Number.isFinite(next)) return "Market closed";
  const time = formatTimeIST(next, { suffix: false });
  if (istDay(next) === istDay(nowMs)) return `Closed · opens ${time}`;
  const weekday = formatDateIST(next, { weekday: true }).split(" ")[0];
  return `Closed · opens ${weekday} ${time}`;
}

export type Tone = "neutral" | "up" | "down" | "warn";

/** The direction colour of a change: up, down, or neutral at zero or unknown. */
export function signTone(v: number | null | undefined): Tone {
  if (typeof v !== "number" || !Number.isFinite(v) || v === 0) return "neutral";
  return v > 0 ? "up" : "down";
}

/** The broker chip in the strip: "Upstox connected" (neutral, check) / "Upstox · log in" (amber) /
 * "2 brokers · 1 needs login". Null while it is not known. */
export function brokerChip(state: BrokerGateState, rows: { needs_login: boolean }[] | null = null): { text: string; tone: "neutral" | "warn"; connected: boolean } | null {
  const partly = state.kind === "connected" ? partlyConnected(rows) : null;
  if (partly) return { text: partly, tone: "warn", connected: true };
  if (state.kind === "connected") {
    const names = [...new Set(state.brokers.map((b) => brokerDisplayName(b.broker_name)))];
    return { text: names.length === 1 ? `${names[0]} connected` : `${names.length} brokers connected`, tone: "neutral", connected: true };
  }
  if (state.kind === "login-needed") {
    const all = [state.broker, ...state.others];
    if (all.length === 1) return { text: `${brokerDisplayName(state.broker.broker_name)} · log in`, tone: "warn", connected: false };
    return { text: `${all.length} brokers · ${all.length} need login`, tone: "warn", connected: false };
  }
  return null;
}

/** "2 brokers · 1 needs login" when some sessions are usable and others are not (rows straight from token-status). */
export function partlyConnected(rows: { needs_login: boolean }[] | null): string | null {
  if (!rows || rows.length < 2) return null;
  const waiting = rows.filter((r) => r.needs_login).length;
  return waiting > 0 && waiting < rows.length ? `${rows.length} brokers · ${waiting} ${waiting === 1 ? "needs" : "need"} login` : null;
}

/** OI underlyings as the option chain names them, mapped to the strip's indices. */
const OI_UNDERLYING: Record<string, string> = { NIFTY: "NIFTY 50", "NIFTY 50": "NIFTY 50", BANKNIFTY: "NIFTY BANK", "NIFTY BANK": "NIFTY BANK" };

/** The first clause of the OI banner's message: "Put writing rising → avoid shorting calls; ..." -> "Put writing rising". */
function firstClause(message: string): string {
  const head = message.split(/\s*(?:→|—|;|\.\s|\s-\s)\s*/)[0] ?? "";
  return head.trim().replace(/\.$/, "");
}

/** One OI line per index: "PCR 1.12 · Put writing rising" (§5 M10: the first sentence plus PCR only). */
export function oiLines(banners: OiBannerResponse[] | null | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  for (const b of banners ?? []) {
    const key = OI_UNDERLYING[(b.underlying ?? "").toUpperCase()];
    const row = b.banner;
    if (!key || !row) continue;
    const parts: string[] = [];
    if (typeof row.pcr === "number" && Number.isFinite(row.pcr)) parts.push(`PCR ${row.pcr.toFixed(2)}`);
    const clause = row.direction === "NEUTRAL" ? "" : firstClause(row.message ?? "");
    if (clause && !/insufficient/i.test(clause)) parts.push(clause[0].toUpperCase() + clause.slice(1));
    if (parts.length) out[key] = parts.join(" · ");
  }
  return out;
}

/** India VIX in words (the market-memory bands: 12 / 16 / 20). */
export function vixNote(v: number | null | undefined): string | null {
  if (typeof v !== "number" || !Number.isFinite(v) || v <= 0) return null;
  if (v < 12) return "Very calm: below 12";
  if (v < 16) return "Calm: 12 to 16";
  if (v < 20) return "Elevated: 16 to 20";
  return "Nervous: above 20";
}

/** The small line under an index: where a remembered price is from, or that there is none. */
export function indexCaption(index: StripIndex, nowMs: number): string | null {
  if (index.missing || index.ltp === null) return "No price yet";
  if (index.source !== "memory") return null;
  const at = toMs(index.as_of);
  if (!Number.isFinite(at)) return "Last known level";
  return istDay(at) === istDay(nowMs) ? `As of ${formatTimeIST(at, { suffix: false })}` : `As of ${formatDateIST(at).replace(/ \d{4}$/, "")}`;
}

// --- Today's P&L ------------------------------------------------------------------------------------------------------

export interface TodayPnl {
  /** Booked + open; null while the open part is not known (never the booked figure passed off as the day's P&L). */
  total: number | null;
  booked: number | null;
  /** 0 with no open positions; null while any open symbol has no live price (P&L at the entry price would be a made-up
   * zero) or a symbol was converted from another currency, or has no rate to convert it. */
  open: number | null;
}

/** Any currency conversion used or missing: some symbol is not simply in rupees (a missing rate leaves that symbol in
 * its own currency), so neither its price nor the open P&L can be read in rupees. */
export function convertedOrUnconverted(exposure: PortfolioExposure): boolean {
  return Object.keys(exposure.fx_rates_used ?? {}).length > 0 || (exposure.fx_missing ?? []).length > 0;
}

/** True when every open symbol is valued at a live price in rupees: the exposure's open P&L is real. */
export function fullyPriced(exposure: PortfolioExposure): boolean {
  if (exposure.price_source !== "ltp" || convertedOrUnconverted(exposure)) return false;
  const priced = new Set(exposure.priced_symbols ?? []);
  return (exposure.by_symbol ?? []).every((row) => priced.has(row.symbol));
}

export function todayPnl(exposure: PortfolioExposure | null | undefined): TodayPnl {
  if (!exposure) return { total: null, booked: null, open: null };
  const booked = exposure.realised_today;
  const open = exposure.open_positions === 0 ? 0 : fullyPriced(exposure) ? exposure.unrealised_pnl : null;
  return { total: open === null ? null : booked + open, booked, open };
}

// --- Open positions ---------------------------------------------------------------------------------------------------

export interface PositionRow {
  id: number;
  symbol: string;
  mode: "Paper" | "Live";
  side: "Buy" | "Sell";
  detail: string;
  /** Null when the symbol has no live price yet. */
  ltp: number | null;
  pnl: number | null;
  pnlPct: number | null;
}

/** The live price of each priced symbol, from the exposure's per-symbol notional: notional = Σ|qty| × price. */
export function symbolPrices(positions: TradeRecord[], exposure: PortfolioExposure | null | undefined): Record<string, number> {
  const out: Record<string, number> = {};
  // A symbol converted from another currency is valued in rupees against an entry in its own currency: no P&L from it.
  if (!exposure || exposure.price_source !== "ltp" || convertedOrUnconverted(exposure)) return out;
  const priced = new Set(exposure.priced_symbols ?? []);
  for (const row of exposure.by_symbol ?? []) {
    if (!priced.has(row.symbol)) continue;
    const qty = positions.filter((p) => p.symbol === row.symbol).reduce((a, p) => a + Math.abs(p.quantity), 0);
    if (qty > 0 && row.notional > 0) out[row.symbol] = row.notional / qty;
  }
  return out;
}

/** Up to MAX_POSITIONS rows, newest first (the order /positions gives). */
export function positionRows(positions: TradeRecord[], exposure: PortfolioExposure | null | undefined, max = MAX_POSITIONS): PositionRow[] {
  const prices = symbolPrices(positions, exposure);
  return positions.slice(0, max).map((p) => {
    const long = p.direction !== "SHORT";
    const ltp = prices[p.symbol] ?? null;
    const pnl = ltp === null ? null : (long ? 1 : -1) * (ltp - p.entry_price) * p.quantity;
    const cost = Math.abs(p.entry_price * p.quantity);
    return {
      id: p.id, symbol: p.symbol, mode: p.mode === "LIVE" ? "Live" : "Paper", side: long ? "Buy" : "Sell",
      detail: `${long ? "Buy" : "Sell"} · ${formatLots(p.quantity, p.lot_size)} · avg ${formatPrice(p.entry_price)}`,
      ltp, pnl, pnlPct: pnl === null || cost === 0 ? null : (pnl / cost) * 100,
    };
  });
}

// --- Active signals (today's entry proposals until UI2-3's signals feed) --------------------------------------------

export type SignalStatus = "Waiting for you" | "Taken" | "Skipped" | "Expired" | "Did not go through";

export interface SignalRow {
  id: number;
  time: string;
  symbol: string;
  side: "Long" | "Short";
  /** Structure words only (UX3 fix 10, src/signals/signalText.ts): no single price, no call word. */
  setup: string;
  source: string;
  status: SignalStatus;
  waiting: boolean;
}

const SOURCES: Record<string, string> = {
  deployment: "Paper bot", scan: "Scan", tradingview: "TradingView alert", public_api: "Your API", manual: "Your setup",
  ticket: "Paper ticket", requote: "Re-quoted", console: "Copilot",
};
const STATUS: Record<string, SignalStatus> = {
  PROPOSED: "Waiting for you", APPROVED: "Taken", EXECUTED: "Taken", REJECTED: "Skipped", EXPIRED: "Expired", FAILED: "Did not go through",
};

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const str = (v: unknown): string => (typeof v === "string" ? v : "");

/** An internal code in words: "target_1_hit" -> "Target 1 hit". Not for strategy ids: those go through
 * `strategyDisplayName` (src/ui/strategyName.ts), which never shows a slug. */
export function humanise(id: string): string {
  const words = id.replace(/[_-]+/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : "";
}

function signalRow(a: AiAction): SignalRow {
  const e = a.evidence ?? {};
  const signal = (typeof e.signal === "object" && e.signal !== null ? e.signal : {}) as Record<string, unknown>;
  const reasons = Array.isArray(signal.reasons) ? signal.reasons.filter((r): r is string => typeof r === "string" && r.trim() !== "") : [];
  // UX3 fix 10 (signal language, CLAUDE.md): no "Entry · Stop · Target" prices on the row; the Signals page holds the
  // user's own levels one tap away. The setup is the first reason in structure words, else the strategy's name.
  const strategy = strategyDisplayName(str(e.strategy_id), null, { index: false });
  return {
    id: a.id,
    time: formatTimeIST(a.created_at, { suffix: false }),
    symbol: str(e.symbol) || EMPTY,
    side: str(e.direction).toUpperCase() === "SHORT" ? "Short" : "Long",
    setup: setupWords(reasonWords(reasons)[0] ?? strategy),
    source: [SOURCES[str(e.source)] ?? "Signal", strategy].filter(Boolean).join(" · "),
    status: STATUS[a.status] ?? "Expired",
    waiting: a.status === "PROPOSED",
  };
}

export interface SignalsToday { rows: SignalRow[]; count: number; waiting: number; taken: number }

/** Today's (IST) entry proposals: waiting ones first, then newest; counts for the Today tile. */
export function signalsToday(actions: AiAction[] | null | undefined, nowMs: number, max = MAX_SIGNALS): SignalsToday {
  const today = istDay(nowMs);
  const mine = (actions ?? []).filter((a) => a.action === "ENTRY" && Number.isFinite(toMs(a.created_at)) && istDay(toMs(a.created_at)) === today);
  const sorted = [...mine].sort((a, b) => Number(b.status === "PROPOSED") - Number(a.status === "PROPOSED") || toMs(b.created_at) - toMs(a.created_at));
  return {
    rows: sorted.slice(0, max).map(signalRow),
    count: mine.length,
    waiting: mine.filter((a) => a.status === "PROPOSED").length,
    taken: mine.filter((a) => a.status === "APPROVED" || a.status === "EXECUTED").length,
  };
}

// --- Needs attention --------------------------------------------------------------------------------------------------

export type AttentionIcon = "approval" | "login" | "paused" | "lockout" | "alert" | "warning" | "account";

export interface AttentionRow {
  id: string;
  icon: AttentionIcon;
  tone: "neutral" | "warn";
  title: string;
  detail: string;
  /** A link, or the broker login (run by the block itself). None for a notice with nothing to do here. */
  action?: { label: string; to: string } | { label: string; login: true };
  /** Settings redesign D: the trader may hide this row until tomorrow (IST), on this device. */
  dismissible?: boolean;
}

/** Settings redesign D: where each setup-health row's one button leads. */
export const AI_FIX_PATH = "/settings#ai";
/** Settings → Alerts (src/setup/getStarted.ts ALERTS_PATH; not imported, so this pure module does not load the routes). */
export const TELEGRAM_FIX_PATH = "/settings#alerts";

export interface AttentionInput {
  nowMs: number;
  broker: BrokerGateState;
  /** Raw token-status rows: some usable, some waiting for a login. */
  brokerRows: { broker_name: string; needs_login: boolean; account_label?: string }[] | null;
  /** Proposals waiting for a decision (GET /ai/actions?status=PROPOSED: the server leaves snoozed ones out). */
  pending: AiAction[] | null;
  deployments: Deployment[] | null;
  risk: PaperRiskStatus | null;
  exposure: PortfolioExposure | null;
  unread: NotificationEntry[] | null;
  status: SystemStatus | null;
  emailVerified: boolean | undefined;
  /** `phase0_paper_only` is on (or not known yet): nothing about LIVE in any row (user-friendly pass item 2). */
  liveHidden?: boolean;
  /** Where approvals are made today (UI2-3 moves them to Signals). */
  approvalsPath: string;
  /** Settings redesign D: what in the setup needs an action now (`GET /api/me/setup` -> `health`); null = not known. */
  health?: SetupHealthItem[] | null;
  /** The Get started card is on the page (it has its own Telegram step, so the Telegram row waits until it is hidden). */
  getStartedShowing?: boolean;
  /** Row ids the trader hid for today (src/dashboard/dismissToday.ts). */
  dismissed?: ReadonlySet<string>;
}

/** The setup-health rows (Settings redesign D), each with one button that opens the exact fix. */
function healthRows(health: SetupHealthItem[] | null | undefined, getStartedShowing: boolean): Record<SetupHealthItem["key"], AttentionRow | null> {
  const out: Record<SetupHealthItem["key"], AttentionRow | null> = { analytics_token: null, ai_key: null, telegram: null, data_feed: null };
  for (const item of health ?? []) {
    if (item.key === "analytics_token") {
      out.analytics_token = {
        id: "renew-token", icon: "login", tone: "warn", dismissible: true,
        title: item.state === "expired" ? "Your Upstox Analytics Token has expired" : "Your Upstox Analytics Token ends today",
        detail: item.state === "expired" ? "Paste a new token to keep prices and paper trading going."
          : "Paste a new token today so prices and paper trading keep going tomorrow.",
        action: { label: "Renew", to: BROKER_PATH },
      };
    } else if (item.key === "ai_key") {
      out.ai_key = {
        id: "ai-key", icon: "warning", tone: "warn", dismissible: true,
        title: item.state === "no_credit" ? "Your AI key is out of credit" : "Your AI key is not working",
        detail: item.state === "no_credit" ? "Add credit with your AI provider, or connect another key." : (item.message || "Check the key or connect another one."),
        action: { label: "Fix", to: AI_FIX_PATH },
      };
    } else if (item.key === "telegram") {
      if (!getStartedShowing) {
        out.telegram = {
          id: "telegram", icon: "alert", tone: "neutral", dismissible: true, title: "Get your alerts on Telegram",
          detail: "Connect Telegram in one tap, so alerts reach your phone.", action: { label: "Connect", to: TELEGRAM_FIX_PATH },
        };
      }
    } else if (item.key === "data_feed") {
      out.data_feed = {
        id: "data-delayed", icon: "warning", tone: "warn", dismissible: true, title: "Live prices are delayed",
        detail: "Some prices may be from an earlier session for a while. This clears by itself; nothing to do on your side.",
      };
    }
  }
  return out;
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

function minutesLeft(expires: string | null, nowMs: number): number | null {
  const at = toMs(expires);
  if (!Number.isFinite(at)) return null;
  return Math.max(0, Math.round((at - nowMs) / 60000));
}

/** Up to MAX_ATTENTION rows, most urgent first (§5): the broker login (nothing is priced without it, so it leads, as in
 * the phone mockup); approvals waiting; Autopilot paused or failing;
 * the daily-loss lockout; a real exposure warning; unseen alerts; LIVE paused by the operator (never in the paper
 * phase); e-mail not verified (the only place it is asked for, item 1).
 * Settings redesign D adds the setup-health rows (`input.health`): the Upstox Analytics Token ending ("Renew", after the
 * broker login), delayed prices and a failing AI key ("Fix", after the exposure warning), and Telegram not connected
 * ("Connect", last, and only once the Get started card is hidden). A row marked `dismissible` that the trader hid today
 * stays out (`input.dismissed`). */
export function attentionRows(input: AttentionInput, max = MAX_ATTENTION): AttentionRow[] {
  const rows: AttentionRow[] = [];
  const health = healthRows(input.health, !!input.getStartedShowing);
  // Snoozed proposals stay out until they wake (the server's pending list already leaves them out; checked again here).
  const pending = (input.pending ?? []).filter((a) => a.status === "PROPOSED" && !(toMs(a.snoozed_until) > input.nowMs));
  const entries = pending.filter((a) => a.action === "ENTRY");
  if (input.broker.kind === "login-needed") {
    const name = brokerDisplayName(input.broker.broker.broker_name);
    rows.push({
      id: "login", icon: "login", tone: "warn", title: `Log in to ${name}`,
      detail: input.broker.expired ? `Your ${name} session has ended. Log in to see live prices and your positions.`
        : `Log in to ${name} to see live prices and your positions.`,
      action: { label: "Log in", login: true }, dismissible: true,
    });
  } else {
    const partly = partlyConnected(input.brokerRows);
    if (partly) rows.push({ id: "login", icon: "login", tone: "warn", title: partly, detail: "Log in to each broker you trade through today.", action: { label: "Brokers", to: BROKER_PATH }, dismissible: true });
  }
  if (health.analytics_token) rows.push(health.analytics_token);
  if (entries.length) {
    const named = entries.slice(0, 2).map((a) => {
      const e = a.evidence ?? {};
      const side = str(e.direction).toUpperCase() === "SHORT" ? "short" : "long";
      const from = SOURCES[str(e.source)] ?? "a signal";
      return `${str(e.symbol) || "A symbol"} ${side} from ${from}`;
    });
    const soonest = entries.map((a) => minutesLeft(a.expires_at, input.nowMs)).filter((m): m is number => m !== null).sort((a, b) => a - b)[0];
    const expires = soonest === undefined ? "" : soonest === 0 ? " · one expires now" : ` · first one expires in ${soonest} min`;
    rows.push({
      id: "approvals", icon: "approval", tone: "neutral",
      title: entries.length === 1 ? "1 trade is waiting for your approval" : `${entries.length} trades are waiting for your approval`,
      detail: `${named.join(" · ")}${entries.length > 2 ? ` · ${entries.length - 2} more` : ""}${expires}`,
      action: { label: entries.length === 1 ? "Review trade" : `Review ${entries.length} trades`, to: input.approvalsPath },
    });
  }
  const others = pending.filter((a) => a.action !== "ENTRY");
  if (others.length) {
    rows.push({
      id: "suggestions", icon: "approval", tone: "neutral",
      title: others.length === 1 ? "1 paper bot suggestion needs your answer" : `${others.length} paper bot suggestions need your answer`,
      detail: others[0].reason, action: { label: "Review", to: input.approvalsPath },
    });
  }
  const paused = (input.deployments ?? []).filter((d) => d.status === "PAUSED");
  const failing = (input.deployments ?? []).filter((d) => d.status === "ACTIVE" && !!d.last_error);
  if (paused.length) {
    const d = paused[0];
    rows.push({
      id: "paused", icon: "paused", tone: "warn",
      title: paused.length === 1 ? `Paper bot paused “${botDisplayName(d.strategy_id, d.symbol)}”` : `${paused.length} paper bots are paused`,
      detail: paused.length === 1 ? (d.pause_reason ? `${d.pause_reason} Nothing else is affected.` : "It stays paused until you resume it.")
        : "They stay paused until you resume them.",
      action: { label: "Review", to: "/deployments" }, dismissible: true,
    });
  } else if (failing.length) {
    rows.push({
      id: "failing", icon: "paused", tone: "warn",
      title: failing.length === 1 ? `Paper bot “${botDisplayName(failing[0].strategy_id, failing[0].symbol)}” hit a problem` : `${failing.length} paper bots hit a problem`,
      detail: "Open Paper bot to see what happened and what to do.", action: { label: "Review", to: "/deployments" }, dismissible: true,
    });
  }
  const lockout = input.risk?.lockout;
  if (lockout) {
    rows.push({ id: "lockout", icon: "lockout", tone: "warn", title: "New trades are paused for today", detail: lockout.reason, action: { label: "Risk limits", to: "/risk-management" } });
  }
  const warning = input.exposure?.warnings?.[0];
  if (warning) rows.push({ id: "exposure", icon: "warning", tone: "warn", title: "Your exposure is high", detail: warning, action: { label: "Portfolio", to: "/positions/money" } });
  if (health.data_feed) rows.push(health.data_feed);
  if (health.ai_key) rows.push(health.ai_key);
  const unread = input.unread ?? [];
  if (unread.length) {
    const newest = [...unread].sort((a, b) => toMs(b.created_at) - toMs(a.created_at))[0];
    rows.push({
      id: "alerts", icon: "alert", tone: "neutral",
      title: unread.length === 1 ? newest.title : `${unread.length} alerts you have not seen`,
      detail: unread.length === 1 ? formatTimeIST(newest.created_at, { suffix: false }) : `Newest: ${newest.title}`,
      action: { label: "Open", to: "/notifications" },
    });
  }
  // P3: in PAPER (the trader has no LIVE deployment) nothing about LIVE shows on the front page: no "Live trading is
  // paused" row. Only a deployments list that was read can hide it: a failed read (null) keeps it
  // (src/setup/paperOnly.ts). While `phase0_paper_only` is on (`liveHidden`) it never shows.
  const paperOnly = paperOnlyFrom(input.deployments) || !!input.liveHidden;
  if (!paperOnly && input.status && input.status.disabled_brokers.length) {
    rows.push({ id: "live-paused", icon: "warning", tone: "warn", title: `Live trading is paused on ${input.status.disabled_brokers.map(brokerDisplayName).join(", ")}`,
      detail: "Paper trading carries on. Live orders wait until it is resumed." });
  }
  if (input.status?.maintenance_mode) {
    rows.push({ id: "maintenance", icon: "warning", tone: "warn", title: "Maintenance: new trades are paused", detail: input.status.maintenance_message || "Open positions are still watched." });
  }
  // User-friendly pass item 1 (UX audit M13): "Verify your email" lives here, once - never as a chip on every page. In
  // PAPER it is a quiet, dismissible reminder with no LIVE wording; it names live trading only once LIVE is in play.
  if (input.emailVerified === false) {
    rows.push({ id: "email", icon: "account", tone: "neutral", title: "Verify your email", dismissible: true,
      detail: paperOnly ? "Confirm your address, so messages about your account reach you." : "Live trading needs a verified address.",
      action: { label: "Verify", to: "/settings/account" } });
  }
  if (health.telegram) rows.push(health.telegram);
  const dismissed = input.dismissed;
  return (dismissed?.size ? rows.filter((r) => !(r.dismissible && dismissed.has(r.id))) : rows).slice(0, max);
}

// --- Today summary ----------------------------------------------------------------------------------------------------

export interface TodayTiles {
  booked: number | null;
  winRate: number | null;
  closedTrades: number | null;
  /** Percent of the daily loss limit in use, 0-100+; null without a limit. */
  riskUsedPct: number | null;
  riskUsed: number | null;
  riskLimit: number | null;
  signals: SignalsToday;
}

export function todayTiles(exposure: PortfolioExposure | null, summary: AnalyticsSummary | null, risk: PaperRiskStatus | null, signals: SignalsToday): TodayTiles {
  const used = risk?.today?.used;
  const limit = risk?.today?.limit;
  // `used` is the share of the limit (0-1) the server computed; the rupees are |worst| of the day against the limit.
  const pct = typeof used === "number" && Number.isFinite(used) ? used * 100 : null;
  return {
    booked: exposure ? exposure.realised_today : null,
    winRate: summary && summary.closed_trades > 0 ? summary.win_rate : null,
    closedTrades: summary ? summary.closed_trades : null,
    riskUsedPct: pct,
    riskUsed: typeof limit === "number" && limit > 0 && pct !== null ? (limit * pct) / 100 : null,
    riskLimit: typeof limit === "number" && limit > 0 ? limit : null,
    signals,
  };
}
