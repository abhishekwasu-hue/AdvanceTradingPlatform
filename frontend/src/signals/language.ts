/**
 * The signal language (handover 11l): the frontend mirror of backend app/signals/language.py - ONE formatter and ONE
 * checker for every place that shows a signal.
 *
 * A signal is described in structure words only. Public / education surfaces: no security names and no prices. The
 * user's OWN signals: his symbol, and the area as a price range ("Seller area: 23,000–23,100"); no other number.
 * Everywhere a signal is described: no buy / sell / target / stop-loss wording. What his automation did (orders, lots,
 * fills) belongs to the Orders / Positions pages, never to the signal text.
 *
 * Both sides run the shared cases in ./__fixtures__/signal_language.json (a byte-identical copy of
 * backend/tests/fixtures/signal_language.json, checked by the backend tests), so the two cannot drift.
 */

export const FLAG = "signal_language_v2";

// --- trade-call words (Analyze a chart runs the same list: chartAnalyze/signalLanguage.ts) ---------------------------------
// Whole words only, any case, except "SL", matched only in capitals. "buyer" / "seller" are structure words: not matched.
const CALL_WORDS_SOURCE =
  "\\b(?:buy|buys|buying|bought|sell|sells|selling|sold|targets?|targeted|targeting|stops?|stop[\\s-]?loss(?:es)?|stoploss(?:es)?)\\b";
export const CALL_WORDS = new RegExp(CALL_WORDS_SOURCE, "i");
export const SL_WORD = /\bSL\b/;

/** The trade-call words in `text`, lower-cased, in order (empty when the text is clean). */
export function callWords(text: string): string[] {
  if (!text) return [];
  const found = Array.from(text.matchAll(new RegExp(CALL_WORDS_SOURCE, "gi")), (m) => m[0].toLowerCase());
  for (const _ of text.matchAll(/\bSL\b/g)) found.push("sl");
  return found;
}

export function isClean(text: string): boolean {
  return callWords(text).length === 0;
}

// --- a single price in free text (the Signals page's own levels check; frontend only) ----------------------------------------
/** A price-like figure: "24,790", "24790", "1,35,000.50", "99.5". Small whole numbers ("EMA 20", "15m") are not prices. */
export const PRICE_LIKE = /(?:\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\b\d{3,}(?:\.\d+)?\b|\b\d+\.\d+\b)/;

/** True when the text carries a single price. */
export function hasPrice(text: string): boolean {
  return PRICE_LIKE.test(text);
}

// --- the formatter ---------------------------------------------------------------------------------------------------------
export type Surface = "signal_own" | "signal_public" | "education";
export const SURFACES: readonly Surface[] = ["signal_own", "signal_public", "education"];

export const TIMEFRAME_WORDS = ["1-minute", "3-minute", "5-minute", "15-minute", "30-minute", "1-hour", "4-hour"] as const;
export const TIMEFRAMES: Record<string, string> = {
  "1m": "1-minute", "3m": "3-minute", "5m": "5-minute", "15m": "15-minute", "30m": "30-minute", "1h": "1-hour", "4h": "4-hour",
  "1d": "Daily", "1w": "Weekly", "1M": "Monthly",
};
export const TRENDS: Record<string, string> = {
  up: "the market is in an uptrend",
  down: "the market is in a downtrend",
  range: "the market is moving sideways in a range",
};
export const SIDES: Record<string, string> = { buyer: "buyer", seller: "seller" };
export const STEPS: Record<string, string> = {
  pullback_reached_area: "the pullback reached {the} {side} area",
  rally_reached_area: "the move reached {the} {side} area",
  handover_forming: "a handover is forming inside the area",
  handover_complete: "the handover inside the area is complete",
  swept_and_reclaimed: "the move swept beyond the area and came back inside",
  range_edge: "the move reached the edge of the range",
  channel_edge: "the move reached the edge of the channel",
  breakout_retest: "the breakout is being retested from the other side",
  structure_shift: "the structure shifted against the last swing",
};
export const SETUPS: Record<string, string> = {
  trend_continuation: "a trend-continuation setup has formed",
  reversal: "a reversal setup has formed",
  range_reversion: "a range setup has formed at the edge",
  breakout_retest: "a breakout-and-retest setup has formed",
  sweep_reclaim: "a sweep-and-reclaim setup has formed",
};
export const EVIDENCE: Record<string, string> = {
  ltf_choch: "lower-timeframe change of character",
  bos: "break of structure",
  bullish_divergence: "bullish RSI divergence",
  bearish_divergence: "bearish RSI divergence",
  liquidity_sweep: "liquidity sweep",
  rejection_candle: "a rejection candle",
  volume_dry_up: "volume drying up in the pullback",
  engulfing_candle: "an engulfing candle",
};
export const RANGE_DASH = "–";

/** What the engine read, as token ids. `symbol` and `area_range` show on the user's OWN signals only. */
export interface SignalStructure {
  trend?: string | null;
  side?: string | null;
  steps?: string[];
  setup?: string | null;
  evidence?: string[];
  timeframe?: string | null;
  symbol?: string | null;
  area_range?: [number, number] | null;
}

function unknown(s: SignalStructure): string[] {
  const bad: string[] = [];
  const one: [string, string | null | undefined, Record<string, string>][] = [
    ["trend", s.trend, TRENDS], ["side", s.side, SIDES], ["setup", s.setup, SETUPS], ["timeframe", s.timeframe, TIMEFRAMES]];
  for (const [kind, value, table] of one) if (value != null && !(value in table)) bad.push(`${kind} ${value}`);
  for (const step of s.steps ?? []) if (!(step in STEPS)) bad.push(`step ${step}`);
  for (const e of s.evidence ?? []) if (!(e in EVIDENCE)) bad.push(`evidence ${e}`);
  return bad;
}

/** A number in Indian digit grouping (23,000; 1,23,456), at most two decimals, no trailing zeros. */
export function groupIn(value: number): string {
  const total = Math.floor(Math.abs(value) * 100 + 0.5);
  const whole = Math.floor(total / 100);
  const cents = total % 100;
  let digits = String(whole);
  if (digits.length > 3) {
    let head = digits.slice(0, -3);
    const tail = digits.slice(-3);
    const pairs: string[] = [];
    while (head.length > 2) {
      pairs.unshift(head.slice(-2));
      head = head.slice(0, -2);
    }
    digits = [head, ...pairs, tail].join(",");
  }
  const frac = cents ? String(cents).padStart(2, "0").replace(/0+$/, "") : "";
  return (value < 0 && total ? "-" : "") + digits + (frac ? `.${frac}` : "");
}

/** "Seller area: 23,000–23,100" (the edges in order). */
export function areaLabel(side: string, low: number, high: number): string {
  const [lo, hi] = low <= high ? [low, high] : [high, low];
  const word = SIDES[side];
  return `${word.charAt(0).toUpperCase()}${word.slice(1)} area: ${groupIn(lo)}${RANGE_DASH}${groupIn(hi)}`;
}

function sentence(parts: string[]): string {
  const text = parts.filter(Boolean).join("; ");
  return text ? `${text.charAt(0).toUpperCase()}${text.slice(1)}.` : "";
}

/** The text of a signal. Throws for a token without a phrase. Public surfaces never show the symbol or the area range. */
export function formatSignal(s: SignalStructure, surface: Surface): string {
  const bad = unknown(s);
  if (bad.length) throw new Error(`no phrase for: ${bad.join(", ")}`);
  if (surface !== "signal_own" && surface !== "signal_public") throw new Error(`not a signal surface: ${surface}`);
  const own = surface === "signal_own";
  const showArea = own && s.area_range != null && !!s.side;
  const the = showArea ? "this" : own ? "the" : "a";
  const side = s.side ? SIDES[s.side] : "";
  const parts: string[] = [];
  if (s.trend) parts.push(TRENDS[s.trend]);
  for (const step of s.steps ?? []) {
    const phrase = STEPS[step];
    if (phrase.includes("{side}") && !s.side) throw new Error(`step ${step} needs the area side`);
    parts.push(phrase.replace("{the}", the).replace("{side}", side));
  }
  if (s.setup) parts.push(SETUPS[s.setup]);
  let body = sentence(parts);
  if (s.timeframe && body) body = `On the ${TIMEFRAMES[s.timeframe]} chart, ${body.charAt(0).toLowerCase()}${body.slice(1)}`;
  if (s.evidence?.length) body = `${body ? `${body} ` : ""}Evidence: ${s.evidence.map((e) => EVIDENCE[e]).join(", ")}.`;
  const head: string[] = [];
  if (own && s.symbol) head.push(s.symbol.trim().toUpperCase());
  if (showArea && s.area_range && s.side) head.push(`${areaLabel(s.side, s.area_range[0], s.area_range[1])}.`);
  if (head.length) {
    const lead = head.join(" · ");
    if (!body) return lead;
    return showArea ? `${lead} ${body}` : `${lead} · ${body}`;
  }
  return body;
}

// --- the checker -------------------------------------------------------------------------------------------------------------
export interface Problem {
  kind: string;
  detail: string;
}

const NUMBER = "\\d[\\d,]*(?:\\.\\d+)?";
export const AREA_RANGE = new RegExp(`\\b(?:Buyer|Seller) area: ${NUMBER}\\s?[${RANGE_DASH}-]\\s?${NUMBER}`, "g");
const TF_WORDS = new RegExp([...TIMEFRAME_WORDS].sort((a, b) => b.length - a.length).map(escape).join("|"), "g");
const CURRENCY = /₹|\bRs\.?(?=\s?\d)|\bINR\b|\$(?=\s?\d)/g;

function escape(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * The policy problems of a shown text (empty = clean): signal_own - no call word, no currency, no digit outside an area
 * range or a timeframe name (his own symbols may be named); signal_public - the same without an area range, and none of
 * `names`; education - none of `names` and no currency amount.
 */
export function check(text: string, surface: Surface, opts: { names?: readonly string[]; ownSymbols?: readonly string[] } = {}): Problem[] {
  if (!SURFACES.includes(surface)) throw new Error(`unknown surface: ${surface}`);
  const value = text ?? "";
  const problems: Problem[] = [];
  if (surface !== "education") {
    for (const w of callWords(value)) problems.push({ kind: "call_word", detail: w });
    let rest = surface === "signal_own" ? value.replace(AREA_RANGE, " ") : value;
    if (surface === "signal_own") {
      const own = Array.from(new Set((opts.ownSymbols ?? []).map((o) => o.trim().toUpperCase()).filter(Boolean)))
        .sort((a, b) => b.length - a.length);
      for (const o of own) rest = rest.replace(new RegExp(`(?<![A-Za-z0-9&-])${escape(o)}(?![A-Za-z0-9&-])`, "g"), " ");
    }
    rest = rest.replace(TF_WORDS, " ");
    for (const m of rest.matchAll(new RegExp(NUMBER, "g"))) problems.push({ kind: "number", detail: m[0] });
  }
  for (const m of value.matchAll(CURRENCY)) problems.push({ kind: "currency", detail: m[0] });
  if (surface !== "signal_own") {
    const names = Array.from(new Set((opts.names ?? []).map((n) => n.trim()).filter(Boolean))).sort();
    for (const name of names) {
      if (new RegExp(`(?<![A-Za-z0-9&-])${escape(name)}(?![A-Za-z0-9&-])`).test(value)) problems.push({ kind: "security_name", detail: name });
    }
  }
  return problems;
}

/** The lines that pass `check` (a line with a problem is left out, never rewritten). */
export function shownLines(lines: readonly string[], surface: Surface, opts: { names?: readonly string[]; ownSymbols?: readonly string[] } = {}): string[] {
  return lines.filter((line) => check(line, surface, opts).length === 0);
}

export const WITHHELD = "(This text was withheld: it read as advice rather than a description.)";

export function withheld(text: string, surface: Surface, opts: { names?: readonly string[]; ownSymbols?: readonly string[] } = {}): string {
  return check(text, surface, opts).length ? WITHHELD : text;
}

const SIDE_OF_DIRECTION: Record<string, string> = { LONG: "an upside", SHORT: "a downside" };

/** (title, message) of a notify-only setup on the user's own symbol (mirror of the backend's setup_notice). */
export function setupNotice(label: string, symbol: string, direction: string, strategyName = "", reasons: readonly string[] = []): [string, string] {
  const sym = (symbol ?? "").trim().toUpperCase();
  const side = SIDE_OF_DIRECTION[(direction ?? "").toUpperCase()] ?? "a";
  const name = (strategyName ?? "").trim();
  const whose = name && check(name, "signal_own", { ownSymbols: [sym] }).length === 0 ? `your strategy ${name}` : "your strategy";
  const why = shownLines(reasons.map(String).filter((r) => r.trim()), "signal_own", { ownSymbols: [sym] }).slice(0, 3);
  let message = `${label}: ${sym} - ${side} setup from ${whose} has formed.`;
  if (why.length) message += ` Why: ${why.map((w) => w.replace(/\.+$/, "")).join("; ")}.`;
  return [`${label}: ${sym}`, `${message} Notify only: nothing is proposed or placed.`];
}
