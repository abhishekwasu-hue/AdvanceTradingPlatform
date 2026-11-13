import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { MyDayArea } from "../../dashboard/myDay";
import type { PortfolioExposure, TradeRecord } from "../../types";
import { formatNumber, formatPrice } from "../../ui/format";
import { heldRows, NO_STOP, type HeldRow } from "../../trading/model";
import type { AskContext } from "../askContext";

/**
 * Owner 13 Nov ("Ask AI opened the Copilot's old clutter - a specific chat should open, with only the points about it"):
 * "Ask AI" on an item opens a FOCUSED chat - a new conversation about that item. Its header names the item in plain
 * words, one fact line (numbers first) follows, and the suggested questions are built from the item's own facts.
 * Information only: no buy / sell / target / stop recommendation in any question. The facts come from the reads the
 * pages already make (positions, portfolio exposure, My day's areas); nothing new is fetched for the AI - the server
 * re-reads the item itself from the request's context (askContext.ts).
 */

export type FocusKind = "position" | "trade" | "order" | "signal" | "scan" | "strike" | "chart" | "journal";

export interface FocusFact {
  key: "avg" | "ltp" | "pnl" | "expiry" | "stop" | "area" | "side" | "tf";
  label: string;
  value: string;
  /** Colour by meaning: up / down (a signed number), warn (something not set), neutral. */
  tone: "up" | "down" | "warn" | "neutral";
}

export interface Focus {
  kind: FocusKind;
  /** "NIFTY 25150 CE · 17 Nov · Short 2 lots" */
  title: string;
  facts: FocusFact[];
  suggestions: string[];
  /** A read behind the facts failed: said in one line under the header ("Some facts are not available right now."). */
  note?: string;
}

/** What the header says when a read behind the facts failed (the facts it did read still show). */
export const FACTS_UNAVAILABLE = "Some facts about this item are not available right now.";

const CHART_PAGES = new Set(["charts", "chart", "terminal-chart"]);
const STRIKE_PAGES = new Set(["option-chain", "chain", "terminal-chain"]);

/** Which kind of item the context names; null: no item (the general chat - opened from the top bar or the Copilot page). */
export function focusKind(ctx: AskContext | null | undefined): FocusKind | null {
  if (!ctx) return null;
  const o = ctx.object?.kind;
  const page = ctx.page ?? "";
  if (o === "signal" || (!o && page === "signals")) return ctx.symbol || o ? "signal" : null;
  if (o === "journal") return "journal";
  if (o === "order" || page === "orders") return o || ctx.symbol ? "order" : null;
  if (o === "position" || o === "trade") return page === "positions" || o === "position" ? "position" : "trade";
  if (!ctx.symbol) return null;
  if (page === "screener") return "scan";
  if (STRIKE_PAGES.has(page)) return "strike";
  if (CHART_PAGES.has(page) || ctx.timeframe) return "chart";
  return null;
}

const KIND_TITLE: Record<FocusKind, string> = {
  position: "Position", trade: "Trade", order: "Order", signal: "Signal", scan: "Scan result", strike: "Option chain", chart: "Chart", journal: "Journal entry",
};

/** A whole-number range for a question ("24,989–25,141"). */
function rangeWords(a: Pick<MyDayArea, "low" | "high">): string {
  return `${formatNumber(Math.round(a.low), 0)}–${formatNumber(Math.round(a.high), 0)}`;
}

function sideLots(p: TradeRecord): string {
  const side = (p.direction ?? "").toUpperCase() === "SHORT" ? "Short" : "Long";
  const lots = p.lot_size && p.lot_size > 1 && Number.isInteger(Math.abs(p.quantity) / p.lot_size) ? Math.abs(p.quantity) / p.lot_size : null;
  return lots ? `${side} ${lots} lot${lots === 1 ? "" : "s"}` : `${side} ${formatNumber(Math.abs(p.quantity), 0)}`;
}

const isOption = (p: TradeRecord) => p.instrument_kind === "OPTION" || / (CE|PE)( |$)/.test(p.symbol);

/** The focused chat of one held position: its title, fact line and questions, from the position's own data. Pure. */
export function positionFocus(p: TradeRecord, row: HeldRow | null, area: MyDayArea | null, sessions: number | null): Focus {
  const title = [row?.instrument ?? p.symbol, row?.expiryLabel, sideLots(p)].filter(Boolean).join(" · ");
  const facts: FocusFact[] = [{ key: "avg", label: "Avg", value: formatPrice(p.entry_price), tone: "neutral" }];
  if (row?.ltp != null) facts.push({ key: "ltp", label: "LTP", value: formatPrice(row.ltp), tone: "neutral" });
  if (row?.pnl != null) {
    const sign = row.pnl > 0 ? "+" : row.pnl < 0 ? "−" : "";
    facts.push({ key: "pnl", label: "P&L", value: `${sign}₹${formatNumber(Math.abs(row.pnl), 0)}`, tone: row.pnl > 0 ? "up" : row.pnl < 0 ? "down" : "neutral" });
  }
  if (sessions != null) facts.push({ key: "expiry", label: "Expiry", value: `${sessions} day${sessions === 1 ? "" : "s"}`, tone: "neutral" });
  const stop = row?.levels.find((l) => l.label === "Stop");
  const stopSet = stop ? stop.value !== NO_STOP : null;
  if (stopSet !== null) facts.push({ key: "stop", label: "Stop", value: stopSet ? stop!.value : "not set", tone: stopSet ? "neutral" : "warn" });
  if (area) facts.push({ key: "area", label: "Area", value: `${formatNumber(area.distance_pct, 2)}% ${area.where === "inside" ? "inside" : area.where} · ${area.range_text}`, tone: "neutral" });
  const underlying = p.underlying_symbol || (row?.chartSymbol ?? p.symbol);
  const suggestions: string[] = [];
  if (area) suggestions.push(`What happens to this position if ${underlying} reaches the area ${rangeWords(area)}?`);
  if (stopSet === false) suggestions.push("My stop is not set; show me the stop options in my settings.");
  suggestions.push(isOption(p) && sessions != null ? `Explain this position's P&L and its time decay to expiry (${sessions} day${sessions === 1 ? "" : "s"}).`
    : "Explain this position's P&L.");
  suggestions.push(isOption(p) ? "Show this option's chart." : `Read the ${underlying} chart.`);
  return { kind: "position", title, facts, suggestions };
}

/** The focused chat of an item the page knows only by its symbol (and timeframe): its questions by kind. Pure. */
export function symbolFocus(kind: FocusKind, ctx: AskContext): Focus {
  const s = ctx.symbol ?? "\u0000";
  const tf = ctx.timeframe ? ` ${ctx.timeframe}` : "";
  const facts: FocusFact[] = ctx.timeframe ? [{ key: "tf", label: "Timeframe", value: ctx.timeframe, tone: "neutral" }] : [];
  const byKind: Record<FocusKind, string[]> = {
    position: [], // built from the position's data (positionFocus)
    trade: ["Review this trade.", "Explain this trade's P&L.", `Read the ${s} chart.`],
    order: ["What is the status of this order?", "Explain this order's quantity and lots.", "How does this order compare with my risk settings?", `Read the ${s} chart.`],
    signal: ["Explain the structure behind this signal.", "Which timeframes does this signal read?", `Where are the nearest areas on ${s}?`, `Read the ${s} chart.`],
    scan: [`Why did ${s} match this scan?`, `What is the ${s} trend on the daily and weekly?`, `Where are the nearest areas on ${s}?`, `Read the ${s} chart.`],
    strike: [`Read the ${s} option chain.`, `Where is the open interest building on ${s}?`, `Where are the nearest areas on ${s}?`, `Read the ${s} chart.`],
    chart: [`Read the ${s}${tf} chart.`, `What is the ${s} structure on${tf || " the daily"}?`, `Where are the nearest areas on ${s}?`, `What changed on ${s} since the last close?`],
    journal: ["Review this trade.", "What did I note and tag on this trade?", `Read the ${s} chart.`],
  };
  // A question that names the symbol is left out when the page gave none.
  const named = (q: string) => !q.includes("\u0000");
  return { kind, title: [KIND_TITLE[kind], ctx.symbol].filter(Boolean).join(" · "), facts, suggestions: byKind[kind].filter(named) };
}

/** The focused chat for the context, read from the pages' own data while it loads: the symbol-only focus first, the
 * position's own facts once its rows are back. null: the general chat. */
export function useFocus(ctx: AskContext | null): Focus | null {
  const kind = focusKind(ctx);
  const key = JSON.stringify(ctx);
  const [focus, setFocus] = useState<Focus | null>(() => (kind && ctx ? symbolFocus(kind, ctx) : null));
  useEffect(() => {
    const c = JSON.parse(key) as AskContext | null;
    const k = focusKind(c);
    if (!k || !c) { setFocus(null); return undefined; }
    setFocus(symbolFocus(k, c));
    if (k !== "position" || !c.object) return undefined;
    let live = true;
    const id = c.object.id;
    // Each read that fails is logged and said under the header (FACTS_UNAVAILABLE); the facts that were read still show.
    let failed = false;
    const miss = (what: string) => (e: unknown) => {
      failed = true;
      console.warn(`Copilot focus: ${what} could not be read`, e);
      return null;
    };
    void (async () => {
      const list = await api.listPositions().catch(miss("positions"));
      const p = list?.find((x) => x.id === id) ?? null;
      if (!live) return;
      if (!p) {
        if (list) setFocus(symbolFocus("trade", c));                     // no longer open: a trade
        else setFocus({ ...symbolFocus(k, c), note: FACTS_UNAVAILABLE });
        return;
      }
      const [exposure, day] = await Promise.all([
        api.portfolioExposure(true).catch(miss("prices")) as Promise<PortfolioExposure | null>,
        api.myDay().catch(miss("areas and expiry")),
      ]);
      if (!live) return;
      const row = heldRows([p], exposure)[0] ?? null;
      const mine = day?.positions.find((x) => x.id === id) ?? null;
      const focus = positionFocus(p, row, mine?.area ?? null, mine?.expiry_sessions ?? null);
      setFocus(failed ? { ...focus, note: FACTS_UNAVAILABLE } : focus);
    })();
    return () => { live = false; };
  }, [key]);
  return focus;
}
