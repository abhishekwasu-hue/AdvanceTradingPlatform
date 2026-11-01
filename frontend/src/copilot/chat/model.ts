import { request } from "../../api/client";
import { formatDateTimeIST, formatNumber } from "../../ui/format";
import type { AnalysisChart, AnalysisTable } from "../../optionChain/analyses";
import type { StrategyDraft } from "../strategyDraft";
import type { MarketStrip, StripIndex } from "../../dashboard/types";
import type { AnalysisItem, AnalyzeStatus } from "../../chartAnalyze/types";
import type { LayerBand } from "../../charting/engine";
import { ZONE_COLORS } from "../../charting/marketZones";

/**
 * Item 5 (owner's handover, the AI Copilot as a trader's brain), first slice: the chat-first Copilot page's data and its
 * pure rules. The server (backend app/ai/copilot_chat.py, app/ai/chat_routes.py) routes each question to one of eight
 * jobs, reads ATP's own tools and builds the answer; this module only fetches and arranges it. Nothing here can place,
 * change or cancel an order (ADR-0006).
 */

export type JobId = "brief" | "chart" | "trade_check" | "positions" | "review" | "strategy" | "option_chain" | "teacher" | "screen" | "views";

export interface ChatJob {
  id: JobId; number: number; title: string; line: string; starter: string; built: boolean;
  page: { label: string; to: string } | null;
}
export interface AiState {
  ready: boolean; title: string | null; line: string | null; settings: string; reason: string | null;
  /** Settings redesign A (ai_one_key on only): the refusal code when nothing is connected, and whether the key is the user's own. */
  code?: string | null; own_key?: boolean;
}
export interface MarketLine {
  line: string | null; reason: string | null; as_of: string | null;
  /** CU2: the numbers strip's FII net in crore (null: not known) and its date. */
  fii_net_crore?: number | null; fii_as_of?: string | null;
}
/** CU3: the chart answer's headline numbers (all from the tools): the last close, the trend word on the asked timeframe,
 * the nearest stored area with its distance, and the chart the card draws (the Charts page's symbol and timeframe). */
export interface AnswerHeadline {
  symbol: string; price: number | null; price_label: string; as_of: string | null; trend: "up" | "down" | "sideways" | null; timeframe: string;
  area: { side: "demand" | "supply" | null; from: number; to: number; where: "above" | "below" | "inside"; distance: number; distance_pct: number | null } | null;
  chart: { symbol: string; exchange: string; timeframe: string } | null;
}
/** M59 CR-1: one stored area beside the price on the structure card (distance in points to its near edge, 0 inside). */
export interface CardArea { side: "demand" | "supply" | null; from: number; to: number; where: "above" | "below" | "inside"; distance: number }
/** M59 CR-1: one timeframe row of the structure card; `ready` false = that timeframe's history is not stored yet. */
export interface CardTimeframe {
  tf: string; label: string; ready: boolean; trend: "up" | "down" | "sideways" | null; weak: boolean; protected: number | null;
  last_break: { label: string; price: number } | null;
}
/** M59 CR-1: the chart reader's structure answer as data (backend copilot_chat.structure_card) - the card draws it. */
export interface StructureCard {
  symbol: string; price: number | null; timeframe: string; timeframes: CardTimeframe[];
  areas_above: CardArea[]; areas_below: CardArea[]; swing_levels: { side: "high" | "low" | null; price: number }[]; watch: string | null;
}
export interface ChatHome {
  top_line: string; ai: AiState; market_line: MarketLine; disclaimer: string; jobs: ChatJob[]; starters: string[];
  /** CU6: how many turns the server keeps (config COPILOT_CHAT_KEEP); the page keeps the same number. */
  keep?: number;
}
/** CU6: one stored turn of the conversation (oldest first). `answer` null: the stored card could not be read. */
export interface StoredTurn { id: number; question: string; answer: ChatAnswer | null; at: string | null }
export interface ChatHistory { turns: StoredTurn[]; keep: number }
export interface WorkedStep {
  tool: string; label: string; ok: boolean; as_of: string | null; source: string | null;
  numbers: { label: string; value: number }[]; note: string | null; args: Record<string, unknown>;
  /** False: that part of ATP is not set up for this account, so the tool was not read (owner order 23 Oct). */
  ran?: boolean;
}
/** A part of ATP the answer needed that is switched off: what, and (admin only) the flag. */
export interface NotSetUp { what: string; flag: string }
export type AnswerBlock =
  | { type: "table"; table: AnalysisTable }
  | { type: "chart"; title: string; chart: AnalysisChart };
export interface ChatAnswer {
  job: JobId | "refuse" | "help"; job_number: number | null; title: string;
  summary: string[]; answer: string; sections: { title: string; lines: string[] }[]; blocks: AnswerBlock[]; unknowns: string[];
  worked: WorkedStep[]; action: { label: string; to: string } | null; refused: RefusalKind | null; starters: string[];
  concepts: { id: string; title: string }[]; notes: string[]; disclaimer: string; source: "rules" | "ai"; tool_calls: string[];
  ai?: AiState; note?: string;
  /** "How I worked this out": the plain names of the tools read for this answer (owner order 23 Oct). */
  tools_used?: string[];
  /** SUPER_ADMIN only (the server sends it to nobody else): the parts that are not set up, with their flags. */
  diagnostics?: { not_set_up?: NotSetUp[] } & Record<string, unknown>;
  /** Job 6: the draft paper strategy the Trading bot page's review card shows (never saved by the Copilot). */
  draft?: StrategyDraft | null;
  /** Job 6 follow-up (CP6-2): a next question the page offers as one button, e.g. "Test it on past data" for a complete draft. */
  followup?: { label: string; question: string } | null;
  /** UI-28 U7.7: what this answer's AI call cost (tokens and rupees; only when the AI worded it - the rules cost nothing). */
  usage?: { calls: number; tokens_input: number; tokens_output: number; cost_inr: number } | null;
  /** CU3: the headline number row (the chart reader today; null or absent elsewhere). */
  headline?: AnswerHeadline | null;
  /** M59 CR-1: the chart reader's structure card (absent on older stored turns and on other jobs). */
  structure_card?: StructureCard | null;
}
/** Job 6 added: the trader's own settings, a promise of returns, someone else's strategy. */
export type RefusalKind = "order" | "advice" | "tip" | "settings" | "promise" | "copy";

/** One turn of the conversation on the page (CU6: kept by the server across reloads, the newest `keep`). */
export interface Turn {
  id: number; question: string; answer: ChatAnswer | null; error: string | null;
  /** M59 CR-2: a chart the user attached (upload or TradingView snapshot link) and its analysis, shown in this turn. */
  chart?: ChartTurnState | null;
}

/** M59 CR-2: what the user attached in the Ask box - a chart image (picked or pasted) or a snapshot link. */
export type ChartSource = { kind: "file"; file: Blob; name: string } | { kind: "link"; link: string };
/** M59 CR-2: the attached chart's state in its thread turn (the same chart_analyze pipeline as the Analyze a chart page). */
export type ChartTurnState =
  | { status: "working"; step: "adding" | "analysing" }
  | { status: "done"; item: AnalysisItem; imageSrc: string | null }
  | { status: "failed"; message: string }
  | { status: "unavailable"; engine: AnalyzeStatus["engine"] };

/** M59 CR-2: the turn's question line for an attached chart. */
export function chartQuestion(source: ChartSource): string {
  if (source.kind === "file") return "Analyze my chart";
  return /tradingview\.com\//i.test(source.link) ? "Analyze my TradingView snapshot" : "Analyze the chart at this link";
}

/** M59 CR-2: the Copilot question for ATP's own structure reading (the CR-1 card) of the symbol the picture shows -
 * "Read the NIFTY chart" - or null when the picture's symbol was not read (or is not a plain ticker). The exchange
 * prefix ("NSE:") is dropped; the Copilot reads its own stored data, never prices from the picture. */
export function readingQuestion(item: Pick<AnalysisItem, "result">): string | null {
  const raw = (item.result?.recognised.symbol ?? "").trim().toUpperCase().replace(/^[A-Z]+:/, "");
  if (!raw || raw.length > 30 || !/^[A-Z0-9][A-Z0-9 &.-]*$/.test(raw)) return null;
  return `Read the ${raw} chart`;
}

/** M59 CR-2: a pasted text that is a TradingView snapshot link (the Ask box offers it as a chart, not a question). */
export function isSnapshotLink(text: string): boolean {
  return /^https:\/\/(www\.)?tradingview\.com\/x\/[A-Za-z0-9]+\/?$/i.test(text.trim());
}

/** M59 CR-2: puts the attached chart's new state on its turn. */
export function withChart(turns: readonly Turn[], id: number, chart: ChartTurnState): Turn[] {
  return turns.map((t) => (t.id === id ? { ...t, chart } : t));
}

/** Where an answer's action goes: the draft rides along in the navigation state for the Trading bot page's review card. */
export function actionState(answer: Pick<ChatAnswer, "draft">): { copilotDraft: StrategyDraft } | undefined {
  return answer.draft ? { copilotDraft: answer.draft } : undefined;
}

export const chatApi = {
  home: () => request<ChatHome>("/ai/chat/home"),
  ask: (message: string) => request<ChatAnswer>("/ai/chat", { method: "POST", body: JSON.stringify({ message }) }),
  /** M59 CR-2: the rule answer only (its structure card), never the AI wording - no metered AI call the user did not ask for. */
  askRules: (message: string) => request<ChatAnswer>("/ai/chat", { method: "POST", body: JSON.stringify({ message, wording: false }) }),
  /** CU6: the person's own conversation, oldest turn first. */
  history: () => request<ChatHistory>("/ai/chat/history"),
  /** CU6: Settings → My data, "Delete my Copilot conversation". */
  forget: () => request<{ deleted: number }>("/ai/chat/history", { method: "DELETE" }),
};

/** The default number of turns the page keeps before the server has said its own (config COPILOT_CHAT_KEEP). */
export const DEFAULT_KEEP = 20;

/** CU6: the stored turns as the page's turns (ids 1..n; a turn whose card could not be read says so plainly). */
export function restoredTurns(history: ChatHistory | null, unreadable: string): Turn[] {
  if (!history) return [];
  return history.turns.slice(-Math.max(1, history.keep)).map((s, i) => ({
    id: i + 1, question: s.question, answer: s.answer, error: s.answer ? null : unreadable,
  }));
}

export const MAX_QUESTION = 1000;
export const PHONE_MAX_WIDTH = 767;

/** The question as it is sent: trimmed, at most MAX_QUESTION characters; null when too short to ask. */
export function cleanQuestion(text: string): string | null {
  const q = text.replace(/\s+/g, " ").trim().slice(0, MAX_QUESTION);
  return q.length >= 2 ? q : null;
}

/** Where the question box sits: fixed at the bottom on a phone (as in a chat app), under the starters on a wider screen. */
export function composerPlacement(width: number): "bottom" | "inline" {
  return width <= PHONE_MAX_WIDTH ? "bottom" : "inline";
}

/** "Today's market in one line": the line, or the plain reason it is empty. */
export function marketLineText(home: ChatHome | null): { text: string; empty: boolean } {
  if (!home) return { text: "", empty: true };
  const m = home.market_line;
  return m.line ? { text: m.line, empty: false } : { text: m.reason ?? "Today's market line is not ready yet.", empty: true };
}

/** The no-key card is shown while no AI provider is set; the rules still answer. */
export function needsKey(home: ChatHome | null): boolean {
  return !!home && !home.ai.ready;
}

/** The "What I can do" cards: every job, a built one asks its starter, one not built yet says so and links today's page. */
export function jobCards(jobs: readonly ChatJob[]): { job: ChatJob; status: string; link: { label: string; to: string } | null }[] {
  return jobs.map((job) => ({
    job,
    status: job.built ? "Ready" : "Coming later",
    link: job.built ? null : job.page,
  }));
}

/** Appends a turn (the newest last) and keeps the last `keep` turns. */
export function withTurn(turns: readonly Turn[], turn: Turn, keep = DEFAULT_KEEP): Turn[] {
  return [...turns, turn].slice(-keep);
}

/** Puts the answer (or the error) on its turn. */
export function settled(turns: readonly Turn[], id: number, answer: ChatAnswer | null, error: string | null): Turn[] {
  return turns.map((t) => (t.id === id ? { ...t, answer, error } : t));
}

/** "How I worked this out": one line per tool, its time stamp and the numbers it returned. */
export function workedLine(step: WorkedStep): string {
  if (!step.ok) return `${step.label}: ${step.ran === false ? "not set up yet" : `no answer (${step.note ?? "not available"})`}`;
  const numbers = step.numbers.map((n) => `${n.label.replace(/_/g, " ")} ${n.value}`).join(", ");
  return `${step.label}${numbers ? `: ${numbers}` : ""}`;
}

/** "Tools used: Chart structure, News feed" - the plain names, or null when no tool was read. */
export function toolsUsedLine(answer: Pick<ChatAnswer, "tools_used">): string | null {
  const names = answer.tools_used ?? [];
  return names.length ? names.join(", ") : null;
}

/** The admin's line: each part that is not set up and the flag it needs (only present in a SUPER_ADMIN's answer). */
export function notSetUpLine(answer: Pick<ChatAnswer, "diagnostics">): string | null {
  const rows = answer.diagnostics?.not_set_up ?? [];
  return rows.length ? rows.map((r) => `${r.what}: flag ${r.flag}`).join("; ") : null;
}

/** A tool's time stamp as a short IST time ("21 Oct, 09:00 IST"), or null. */
export function stampText(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" });
  return formatDateTimeIST(d, { year: false });
}

/** UI-28 U2.4 (owner, 28 Oct): a "Label: value" line of an answer as a row - the label left, the value right, a signed
 * value ("+₹1,200", "−0.8%", "-3") coloured and arrowed by its own sign (the server's words and numbers as sent).
 * Null for a line that is a sentence, so it stays text. */
export interface AnswerRow { label: string; value: string; dir: "up" | "down" | null }

export function answerRow(line: string): AnswerRow | null {
  const m = /^([^:]{1,40}):\s+(.{1,40})$/.exec(line.trim());
  // A row is a short label (up to four words) and a number-led value ("+₹1,200", "0.84", "₹5,000"); anything else is a sentence.
  if (!m || m[1].trim().split(/\s+/).length > 4 || !/^[+−-]?\s?₹?\s?\d/.test(m[2].trim())) return null;
  const value = m[2].trim().replace(/\.$/, "");
  const signed = /^([+−-])\s?₹?\s?\d/.exec(value);
  return { label: m[1].trim(), value, dir: signed ? (signed[1] === "+" ? "up" : "down") : null };
}

/** CU2: the four chips under the numbers strip - each asks its job's own starter question (from the server). A job the
 * server does not list is left out. */
export const QUICK_JOBS: readonly { id: JobId; label: string }[] = [
  { id: "brief", label: "Brief" }, { id: "chart", label: "Read chart" }, { id: "trade_check", label: "Check my trade" }, { id: "positions", label: "My positions" },
];

export function quickChips(jobs: readonly ChatJob[]): { id: JobId; label: string; question: string }[] {
  return QUICK_JOBS.flatMap((q) => {
    const job = jobs.find((j) => j.id === q.id);
    return job ? [{ ...q, question: job.starter }] : [];
  });
}

/** CU3: the nearest area as "Demand 24,520 – 24,610" (the side word only when the server knows it). */
export function areaText(area: NonNullable<AnswerHeadline["area"]>, price: (v: number) => string): string {
  const side = area.side ? `${area.side[0].toUpperCase()}${area.side.slice(1)} ` : "";
  return `${side}${price(area.from)} – ${price(area.to)}`;
}

/** CU3: how far the price is from that area: "inside", or "229.5 (0.92%) above" / "below" (where the area sits). */
export function distanceText(area: NonNullable<AnswerHeadline["area"]>, price: (v: number) => string): string {
  if (area.where === "inside") return "Price is inside";
  const pct = area.distance_pct === null ? "" : ` (${formatNumber(area.distance_pct, 2)}%)`;
  return `${price(area.distance)}${pct} ${area.where}`;
}

/** CU3 + M75 W-1 (W2): the chart answer's nearest area for the mini chart - ONE filled band (buyer / seller colour from
 * the chart tokens via its role), labelled "Supply area" / "Demand area" inside or beside it; never two dashed price
 * lines with "Area top" / "Area bottom" titles over the price axis. */
export function headlineAreas(h: AnswerHeadline | null | undefined): LayerBand[] {
  const a = h?.area;
  if (!a || !Number.isFinite(a.from) || !Number.isFinite(a.to)) return [];
  const role = a.side ?? "neutral";
  const label = a.side ? `${a.side[0].toUpperCase()}${a.side.slice(1)} area` : "Area";
  return [{ low: Math.min(a.from, a.to), high: Math.max(a.from, a.to), role, color: a.side ? ZONE_COLORS[a.side] : undefined, opacity: 0.18,
            style: "solid", label }];
}

/** M59 CR-1: a timeframe whose history is not stored yet - "1-hour: data not ready", never a bar count. */
export function notReadyText(row: Pick<CardTimeframe, "label">): string {
  return `${row.label}: data not ready`;
}

/** M59 CR-1: the trend chip's word - "▲ Up", "▼ Down · weak", "Sideways", "No clear trend". */
export function cardTrendText(row: Pick<CardTimeframe, "trend" | "weak">): string {
  const word = row.trend === "up" ? "▲ Up" : row.trend === "down" ? "▼ Down" : row.trend === "sideways" ? "Sideways" : "No clear trend";
  return row.weak && row.trend ? `${word} · weak` : word;
}

/** M59 CR-1: one area line - "Supply 25,080.00 – 25,160.00 · 229.50 pts" ("price inside" at 0). */
export function cardAreaText(area: CardArea, num: (v: number) => string): string {
  const side = area.side ? `${area.side[0].toUpperCase()}${area.side.slice(1)} ` : "";
  const gap = area.where === "inside" ? "price inside" : `${num(area.distance)} pts`;
  return `${side}${num(area.from)} – ${num(area.to)} · ${gap}`;
}

/** M59 CR-1: an area's second line on the card - "Supply · 229.50 pts" / "Demand · price inside". */
export function areaNote(area: CardArea, num: (v: number) => string = String): string {
  const side = area.side ? `${area.side[0].toUpperCase()}${area.side.slice(1)} · ` : "";
  return `${side}${area.where === "inside" ? "price inside" : `${num(area.distance)} pts`}`;
}

/** M59 CR-1: a swing level chip - "Swing high 25,010.50" / "Swing low 24,600.50". */
export function swingText(level: StructureCard["swing_levels"][number], num: (v: number) => string): string {
  return `${level.side === "high" ? "Swing high" : level.side === "low" ? "Swing low" : "Swing level"} ${num(level.price)}`;
}

/** M59 CR-1: true when a text still carries Markdown marks the page would otherwise print raw (`**`, `__`, "# ", "- "). */
export function hasRawMarkdown(text: string): boolean {
  return /\*\*|__|^#{1,3}\s|^\s*[-*]\s/m.test(text);
}

/** CU3 (strategy result card): the sections that stay on the card; every other one is theory and folds into Details. */
export const STRATEGY_SHOWN: ReadonlySet<string> = new Set(["Values you decide", "Your draft on past data", "Is it enough to judge?"]);

export interface AnswerParts {
  /** The strategy's own rules, as chips. */
  rules: string[];
  /** The strategy's settings (timeframe, side, stop...), as chips. */
  settings: string[];
  shown: ChatAnswer["sections"];
  folded: ChatAnswer["sections"];
  blocksShown: AnswerBlock[];
  blocksFolded: AnswerBlock[];
}

/**
 * CU3: what the answer card shows and what goes behind its one "Details" tap.
 * - A chart answer (with a headline row): the row, the mini chart and the action are the answer; its sections and
 *   tables repeat them in words, so they fold.
 * - A strategy answer (with a draft): the rules and settings become chips, "Values you decide" and the test results
 *   stay; the theory (how I read your words, not supported yet, how I build it...) folds.
 * - Any other answer: its sections and tables are the answer and stay.
 */
export function answerParts(answer: Pick<ChatAnswer, "job" | "sections" | "blocks" | "draft" | "headline" | "structure_card">): AnswerParts {
  const all = answer.sections;
  if (answer.headline || answer.structure_card) return { rules: [], settings: [], shown: [], folded: [...all], blocksShown: [], blocksFolded: [...answer.blocks] };
  if (answer.job === "strategy" && answer.draft) {
    const ruleTexts = new Set((answer.draft.rules ?? []).map((r) => r.text));
    const ruleSections = all.filter((s) => s.lines.length > 0 && s.lines.every((l) => ruleTexts.has(l)));
    const settings = all.find((s) => s.title === "Your settings");
    return {
      rules: ruleSections.flatMap((s) => s.lines), settings: settings ? [...settings.lines] : [],
      shown: all.filter((s) => STRATEGY_SHOWN.has(s.title)),
      folded: all.filter((s) => !ruleSections.includes(s) && s !== settings && !STRATEGY_SHOWN.has(s.title)),
      blocksShown: [...answer.blocks], blocksFolded: [],
    };
  }
  return { rules: [], settings: [], shown: [...all], folded: [], blocksShown: [...answer.blocks], blocksFolded: [] };
}

/** CU2: the numbers strip's three index cells, in this order; an index the strip does not carry is its label alone. */
export const NUMBERS_INDICES: readonly { key: string; label: string }[] = [
  { key: "NIFTY 50", label: "NIFTY 50" }, { key: "NIFTY BANK", label: "BANK NIFTY" }, { key: "SENSEX", label: "SENSEX" },
];

export function numbersCells(strip: MarketStrip | null): { key: string; label: string; index: StripIndex | null }[] {
  return NUMBERS_INDICES.map(({ key, label }) => ({ key, label, index: strip?.indices.find((i) => i.key === key) ?? null }));
}
