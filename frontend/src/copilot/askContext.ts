/**
 * M72 PR-3 (CP-3, finding F8): "Ask AI about this" - the context the Copilot opens with. The address carries WHERE the
 * question comes from (`/copilot?symbol=NIFTY&tf=15min&ctx=position:42&from=positions`); the server re-reads the object
 * for this user (another person's id: not found) and never takes a number about it from the page.
 */
export type AskObjectKind = "position" | "trade" | "order" | "journal" | "signal";
export const ASK_OBJECT_KINDS: readonly AskObjectKind[] = ["position", "trade", "order", "journal", "signal"];
export interface AskContext {
  page?: string;
  symbol?: string;
  timeframe?: string;
  object?: { kind: AskObjectKind; id: number };
}

const SYMBOL = /^[A-Za-z0-9][A-Za-z0-9 &._:-]{0,29}$/;
const TIMEFRAME = /^[A-Za-z0-9][A-Za-z0-9 -]{0,19}$/;
const PAGE = /^[a-z0-9][a-z0-9_-]{0,39}$/;

/** The Copilot's address for this context (the in-place panel opens it beside the page). */
export function askAiPath(ctx: AskContext): string {
  const q = new URLSearchParams();
  if (ctx.symbol) q.set("symbol", ctx.symbol.trim().toUpperCase());
  if (ctx.timeframe) q.set("tf", ctx.timeframe);
  if (ctx.object) q.set("ctx", `${ctx.object.kind}:${ctx.object.id}`);
  if (ctx.page) q.set("from", ctx.page);
  const query = q.toString();
  return query ? `/copilot?${query}` : "/copilot";
}

/** The context an address carries (null: none); anything malformed is left out, never guessed. */
export function askContextFrom(get: (key: string) => string | null | undefined): AskContext | null {
  const out: AskContext = {};
  const symbol = get("symbol")?.trim();
  if (symbol && SYMBOL.test(symbol)) out.symbol = symbol.toUpperCase();
  const tf = get("tf")?.trim();
  if (tf && TIMEFRAME.test(tf)) out.timeframe = tf;
  const page = get("from")?.trim();
  if (page && PAGE.test(page)) out.page = page;
  const m = /^([a-z]+):([1-9][0-9]{0,15})$/.exec(get("ctx")?.trim() ?? "");
  if (m && (ASK_OBJECT_KINDS as readonly string[]).includes(m[1])) out.object = { kind: m[1] as AskObjectKind, id: Number(m[2]) };
  return Object.keys(out).length ? out : null;
}

const KIND_WORDS: Record<AskObjectKind, string> = { position: "Your position", trade: "Your trade", order: "Your order", journal: "Your journal entry", signal: "Signal" };

/** The chip's words: "Your position · NIFTY", "NIFTY · 15min". */
export function askContextLabel(ctx: AskContext): string {
  const parts = [ctx.object ? KIND_WORDS[ctx.object.kind] : null, ctx.symbol ?? null, ctx.object ? null : ctx.timeframe ?? null].filter(Boolean);
  return parts.join(" · ");
}

/** A Signals inbox item's object: "history:12" is a signal, "trade:7" a trade; other sources carry the symbol only. */
export function signalObject(id: string): AskContext["object"] {
  const m = /^(history|trade):([1-9][0-9]{0,15})$/.exec(id);
  if (!m) return undefined;
  return { kind: m[1] === "history" ? "signal" : "trade", id: Number(m[2]) };
}
