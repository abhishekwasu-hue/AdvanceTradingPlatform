import { botWithDefaults, structureLabel, type TradingDefaults } from "../settings/tradingDefaults";
import type { ContractRules, Deployment, DeploymentCreateRequest, StrategyInfo } from "../types";

/**
 * UX2 fix 4 ("Paper bot in three taps", docs/design/UX_AUDIT_FULL.md problem 3 and P18): the rules of the v2 Paper bot
 * page. Pick a strategy, pick a symbol, Start on paper. Always PAPER, one lot; the trader's own risk limits apply on the
 * server. Pure functions only, so every rule here is unit-tested.
 */

export interface ReadyStrategy {
  id: string;
  /** One plain sentence: what the strategy waits for. Structure words only, no prices and no order wording. */
  line: string;
  /** The symbol it is shown with first. */
  symbol: string;
}

/** The Library's built-in strategies a beginner starts with (the Setup wizard's three). One missing from the Library is
 * not offered. */
export const READY: readonly ReadyStrategy[] = [
  { id: "orb_15m_5m", symbol: "NIFTY 50", line: "Waits for the first 15 minutes' range to break, then follows the move. Closes before the market does." },
  { id: "vwap_supertrend_5m", symbol: "NIFTY 50", line: "Follows the day's trend while it holds, and closes before the market does." },
  { id: "swing_ema_pullback_d", symbol: "RELIANCE", line: "Waits for a pullback inside an uptrend and holds for days." },
];

/** Index symbols have no cash leg: a paper bot on one trades its option (nearest expiry, at the money, one lot). */
export const INDEX_SYMBOLS: ReadonlySet<string> = new Set([
  "NIFTY 50", "NIFTY", "NIFTY BANK", "BANKNIFTY", "NIFTY FIN SERVICE", "FINNIFTY", "NIFTY MID SELECT", "MIDCPNIFTY", "NIFTY NEXT 50",
  "SENSEX", "BANKEX",
]);

/** The symbols offered in the list; "Another symbol" lets the trader type one. */
export const SYMBOLS: readonly string[] = ["NIFTY 50", "NIFTY BANK", "FINNIFTY", "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN"];
/** Day-one fix 6: an idea opened from the Ideas page starts on a stock, so the form never quietly picks an index option
 * for a price-action idea (the trader changes it in one tap). */
export const IDEA_SYMBOL = "RELIANCE";

const SYMBOL = /^[A-Z0-9&][A-Z0-9&\- ]{0,29}$/;

export function cleanSymbol(raw: string): string {
  return raw.trim().toUpperCase().replace(/\s+/g, " ");
}

export function symbolProblem(raw: string): string | null {
  const s = cleanSymbol(raw);
  if (!s) return "Pick or type a symbol.";
  if (!SYMBOL.test(s)) return "Use the symbol as the exchange writes it, for example RELIANCE.";
  return null;
}

export const isIndex = (symbol: string) => INDEX_SYMBOLS.has(cleanSymbol(symbol));

/** A daily strategy holds for days (daily candles, kept overnight). */
export function isSwing(info: Pick<StrategyInfo, "timeframes"> | null | undefined): boolean {
  return !!info && info.timeframes.length === 1 && info.timeframes[0] === "day";
}

/** The sentence under the strategy list: the ready-made line, else the Library's own description (its first sentence). */
export function strategyLine(id: string, info: Pick<StrategyInfo, "description"> | null | undefined): string {
  const ready = READY.find((r) => r.id === id);
  if (ready) return ready.line;
  const text = (info?.description ?? "").trim();
  if (!text) return "";
  const first = text.split(/(?<=[.!?])\s/)[0];
  return first.length > 160 ? `${first.slice(0, 157).trimEnd()}…` : first;
}

/** The one deployment "Start on paper" creates. Never LIVE: this page offers no other mode. */
export function paperBotRequest(strategyId: string, symbol: string, info: Pick<StrategyInfo, "timeframes"> | null | undefined):
  DeploymentCreateRequest & { mode: "PAPER" } {
  const s = cleanSymbol(symbol);
  const swing = isSwing(info);
  const contract: ContractRules = isIndex(s)
    ? { instrument_kind: "OPTION", option_position: "BUY", expiry_rule: "NEAREST", strike_rule: "ATM", strike_offset: 0, max_lots: 1 }
    : { instrument_kind: "UNDERLYING" };
  return {
    strategy_id: strategyId, symbol: s, exchange: "NSE", timeframe: swing ? "day" : "1min", mode: "PAPER",
    holding: swing ? "SWING" : "INTRADAY", ...contract,
  };
}

const EXPIRY_WORDS: Record<string, string> = { NEAREST: "nearest", NEXT: "next", MONTHLY: "monthly" };

/** Settings part C: the same request with the user's own defaults (a pre-fill: only the values that apply, each named in
 * `applied` so the page can say "From your defaults: ..."). No defaults, or `use` false: today's request itself. Never
 * the mode: it stays PAPER. */
export function paperBotRequestWithDefaults(strategyId: string, symbol: string, info: Pick<StrategyInfo, "timeframes"> | null | undefined,
  defaults: TradingDefaults | null | undefined, approvalAllowed: boolean, use = true):
  { request: DeploymentCreateRequest & { mode: "PAPER" }; applied: string[] } {
  const base = paperBotRequest(strategyId, symbol, info);
  if (!use) return { request: base, applied: [] };
  return botWithDefaults(base, defaults, { index: isIndex(symbol), swing: isSwing(info), approvalAllowed });
}

/** What "Start on paper" says under the button. Settings part C: given the request (with the user's defaults), it says
 * what that request trades; without one (or with today's request) the words are today's. */
export function startSummary(symbol: string, info: Pick<StrategyInfo, "timeframes"> | null | undefined,
  req?: Pick<DeploymentCreateRequest, "instrument_kind" | "expiry_rule" | "max_lots" | "option_strategy">): string {
  const s = cleanSymbol(symbol) || "the symbol";
  const lots = req?.max_lots ?? 1;
  const expiry = EXPIRY_WORDS[req?.expiry_rule ?? "NEAREST"] ?? "nearest";
  const size = `${lots} lot${lots === 1 ? "" : "s"}`;
  const structure = req?.option_strategy && req.option_strategy !== "SINGLE" ? req.option_strategy : null;
  // Day-one fix 6: a stock is traded as its shares (plain words; "RELIANCE itself" read oddly).
  const what = !isIndex(s) ? `${s} shares`
    : req?.instrument_kind === "FUTURE" ? `a ${s} future at the ${expiry} expiry, ${size}`
    : structure ? `${s} options as a ${structureLabel(structure).toLowerCase()} at the ${expiry} expiry, ${size}`
    : `a ${s} option at the ${expiry} expiry, ${size}`;
  const when = isSwing(info) ? "held for days" : "closed before the market closes";
  return `Trades ${what}, ${when}. Paper money only; your risk limits apply.`;
}

export type BotState = "running" | "paused" | "stopped";

export function botState(d: Pick<Deployment, "status">): BotState {
  return d.status === "ACTIVE" ? "running" : d.status === "PAUSED" ? "paused" : "stopped";
}

const STATE_TEXT: Record<BotState, string> = { running: "Running", paused: "Paused", stopped: "Stopped" };

/** "Running", "Paused: 3 losses in a row" - a reason only when it reads as words (never an internal code). */
export function botStatusText(d: Pick<Deployment, "status" | "pause_reason">): string {
  const state = botState(d);
  const reason = (d.pause_reason ?? "").trim();
  if (state === "paused" && reason && /^paused from (the )?(autopilot|paper bot)/i.test(reason)) return "Paused by you";
  if (state === "paused" && reason && reason.length <= 80 && !/[_{}[\]<>=]|#\d/.test(reason)) {
    return `Paused: ${reason.charAt(0).toLowerCase()}${reason.slice(1).replace(/\.$/, "")}`;
  }
  return STATE_TEXT[state];
}

/** The bot's name: the strategy's Library name, else its id in words. */
/** Strategy templates step 2: a template strategy id ("template:<id>:<version>:..." - the settings travel in it). */
export const TEMPLATE_ID_PREFIX = "template:";

export function isTemplateId(id: string): boolean {
  return id.startsWith(TEMPLATE_ID_PREFIX);
}

/** The template a template strategy id names ("D2"), or null. */
export function templateOf(id: string): string | null {
  if (!isTemplateId(id)) return null;
  const part = id.slice(TEMPLATE_ID_PREFIX.length).split(":")[0];
  return part || null;
}

export function botName(d: Pick<Deployment, "strategy_id">, names: ReadonlyMap<string, string>): string {
  const name = names.get(d.strategy_id);
  if (name) return name;
  const template = templateOf(d.strategy_id);
  if (template) return names.get(TEMPLATE_ID_PREFIX + template) ?? "Strategy template";   // never the id itself
  const words = d.strategy_id.replace(/^custom[:_-]?/i, "").replace(/[_-]+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : "My strategy";
}

/** "Option of NIFTY 50" / "RELIANCE" and how long it holds. */
export function botWhat(d: Pick<Deployment, "symbol" | "instrument_kind" | "holding">): string {
  const what = d.instrument_kind === "OPTION" ? `${d.symbol} options` : d.instrument_kind === "FUTURE" ? `${d.symbol} futures` : d.symbol;
  return d.holding === "SWING" ? `${what} · held for days` : `${what} · during the day`;
}

/** "Checked 2 min ago" / "Not checked yet". */
export function lastChecked(iso: string | null, now: number = Date.now()): string {
  if (!iso) return "Not checked yet";
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (!Number.isFinite(seconds)) return "Not checked yet";
  if (seconds < 90) return "Checked just now";
  if (seconds < 90 * 60) return `Checked ${Math.round(seconds / 60)} min ago`;
  if (seconds < 36 * 3600) return `Checked ${Math.round(seconds / 3600)} h ago`;
  return `Checked ${Math.round(seconds / 86400)} days ago`;
}

/** Running first, then paused, then stopped; newest first inside each. Stopped bots only when asked for. */
export function sortBots<T extends Pick<Deployment, "status" | "id">>(bots: readonly T[], showStopped = false): T[] {
  const rank: Record<BotState, number> = { running: 0, paused: 1, stopped: 2 };
  return bots.filter((b) => showStopped || botState(b) !== "stopped")
    .slice().sort((a, b) => rank[botState(a)] - rank[botState(b)] || b.id - a.id);
}

/** Job 6 (the Copilot's strategy builder): the size the trader's own idea named ("2 lots"), on a contract that takes lots
 * (an option or a future); a cash symbol, or no size named, keeps the request as it is. Never the mode: PAPER stays. */
export function withIdeaLots<T extends DeploymentCreateRequest>(req: T, lots: number | null | undefined): T {
  if (!lots || !Number.isInteger(lots) || lots < 1 || req.instrument_kind === "UNDERLYING" || !req.instrument_kind) return req;
  return { ...req, max_lots: lots };
}

/** The strategy and symbol the page picks after the trader saved the Copilot's draft (his own click). */
export interface Preselect { strategyId: string; symbol: string | null; lots: number | null }
