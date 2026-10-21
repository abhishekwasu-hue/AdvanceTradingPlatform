/**
 * UI2-4d (Charts page plan §3): the Multi-chart tab's rules without React - where a layout starts, how a cell's
 * timeframe and chart type show on a v2 chart, the old `?workspace=1` address under v2, and the shared candle reads.
 * The layout itself is the CH5 model (`charting/workspace.ts`, `layout/1`), saved through the same `layoutsApi`.
 */
import { ApiError } from "../api/client";
import type { CandlesResponse } from "../types";
import {
  LayoutConflict, TIMEFRAMES as CELL_TIMEFRAMES, defaultLayoutV2, layoutProblem, type CellChartType, type LayoutV1, type Timeframe,
} from "../charting/workspace";
import type { ChartTypeV2 } from "./chartCore";
import { DEFAULT_TIMEFRAME, type TimeframeId } from "./timeframes";
import type { FetchCandles } from "./useChartCandles";

type UserId = number | string | null | undefined;

/** The layout on screen (per user, on this browser), so a reload or a tab switch keeps it. */
export const MULTI_DRAFT_KEY = "atp_charts_multi_draft";
/** The saved layout used last: the CH5 page's key, as both pages show the same saved layouts. */
export const LAST_LAYOUT_KEY = "atp_workspace_last";

export function readMultiDraft(user: UserId): LayoutV1 | null {
  if (user == null) return null;
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(`${MULTI_DRAFT_KEY}:${user}`) ?? "null");
    return parsed && layoutProblem(parsed) === null ? (parsed as LayoutV1) : null;
  } catch {
    return null;
  }
}

export function saveMultiDraft(user: UserId, layout: LayoutV1): void {
  if (user == null) return;
  try { localStorage.setItem(`${MULTI_DRAFT_KEY}:${user}`, JSON.stringify(layout)); } catch { /* storage unavailable */ }
}

export function readLastLayout(): string | null {
  try { return localStorage.getItem(LAST_LAYOUT_KEY); } catch { return null; }
}

export function saveLastLayout(id: number | null): void {
  try {
    if (id === null) localStorage.removeItem(LAST_LAYOUT_KEY);
    else localStorage.setItem(LAST_LAYOUT_KEY, String(id));
  } catch { /* storage unavailable */ }
}

/** A symbol the address asked for (an old workspace link, redirected): `/charts?tab=multi&symbol=&tf=&exchange=`. */
export interface StartChart { symbol: string; exchange: string; timeframe: Timeframe }
/** The address parameters the tab starts from (then removed from the address). Not `chart`: at the root of the app
 * that parameter opens the single chart window (App.tsx). */
export const START_PARAMS = ["symbol", "tf", "exchange"] as const;

export function startChartFrom(params: URLSearchParams): StartChart | null {
  const symbol = (params.get("symbol") ?? "").trim();
  if (!symbol) return null;
  const tf = params.get("tf") ?? "";
  return { symbol, exchange: (params.get("exchange") ?? "NSE").trim().toUpperCase() || "NSE",
           timeframe: (CELL_TIMEFRAMES as readonly string[]).includes(tf) ? (tf as Timeframe) : "5min" };
}

/** Where the tab starts: a symbol from the address (two charts of it), else this browser's draft, else two charts of
 * the page's symbol (a new trader: grid 2). A saved layout used last replaces it once the list is read. */
export function startLayoutV2(start: StartChart | null, draft: LayoutV1 | null, symbol: string, exchange: string): LayoutV1 {
  if (start) return defaultLayoutV2(start.symbol, start.exchange, start.timeframe);
  return draft ?? defaultLayoutV2(symbol, exchange);
}

/** UI2-4d: an old `/?workspace=1[&chart=&tf=&exchange=]` link under `ui_v2` opens the Multi-chart tab. */
export function workspaceRedirect(params: URLSearchParams): string {
  const next = new URLSearchParams({ tab: "multi" });
  for (const [from, to] of [["chart", "symbol"], ["tf", "tf"], ["exchange", "exchange"]] as const) {
    const v = params.get(from)?.trim();
    if (v) next.set(to, v);
  }
  return `/charts?${next.toString()}`;
}

/** A cell's chart type on the v2 chart: the CH5-only types show as their nearest v2 type (the layout keeps them). */
export function cellChartType(t: CellChartType): ChartTypeV2 {
  if (t === "bars" || t === "heikin_ashi" || t === "line") return t;
  return t === "area" ? "line" : "candles";
}

/** A cell's timeframe as shown: week and month only while the server lists them (as on the Chart tab); a stored week
 * or month waits (null) while the server's list is not known yet; otherwise the default timeframe. */
export function cellTimeframe(tf: Timeframe, offered: readonly TimeframeId[], known: boolean): TimeframeId | null {
  if (offered.includes(tf)) return tf;
  if (!known && (tf === "week" || tf === "month")) return null;
  return DEFAULT_TIMEFRAME;
}

/** How many of the layout's charts are shown: a phone shows one or two, stacked (CHP-9: the grid itself is kept). */
export function shownCount(layout: LayoutV1, phone: boolean): number {
  return phone ? Math.min(layout.grid, 2) : layout.grid;
}

/** A failed save or delete of a saved layout in plain words: what happened, then what to do. */
export function layoutErrorText(error: unknown, action: "saved" | "deleted"): string {
  if (error instanceof LayoutConflict) return error.message;
  if (error instanceof ApiError && error.status === 409 && /named/.test(error.detail)) return "You already have a layout with that name. Pick another name.";
  if (error instanceof ApiError && error.status === 409 && /at most/.test(error.detail)) return "You have the most saved layouts allowed. Delete one first.";
  return `The layout could not be ${action}. Check your connection, then try again.`;
}

// --- Shared candle reads -----------------------------------------------------------------------------------------------

export interface PoolOptions {
  /** Requests on the wire at once; the rest wait their turn (the first load of eight charts is staggered). */
  maxInFlight: number;
  /** An identical request answered this recently is answered again from memory (charts of one symbol and timeframe). */
  freshMs: number;
}

/**
 * UI2-4d: the Multi-chart tab's candle reads, shared by its cells. Identical requests (symbol, timeframe, days,
 * exchange, before, options) are sent once: one in flight is joined, and one answered within `freshMs` is answered
 * again. At most `maxInFlight` go out at once. A failed read is never kept. The server's per-user limit (120 candle
 * reads a minute) and its shared candle copies (LD-10) stay the backstop; this keeps eight charts well under both.
 * Pure (the fetch and the clock are given).
 */
export class CandlePool {
  private inFlight = new Map<string, Promise<CandlesResponse>>();
  private recent = new Map<string, { at: number; value: CandlesResponse }>();
  private waiting: (() => void)[] = [];
  private running = 0;
  /** Requests actually sent (tests, diagnostics). */
  sent = 0;

  constructor(private readonly fetch: FetchCandles, private readonly options: PoolOptions = { maxInFlight: 2, freshMs: 30_000 },
              private readonly now: () => number = () => Date.now()) {}

  /** The pool as a drop-in for `api.marketDataCandles`. */
  readonly fetchCandles: FetchCandles = (...args) => this.get(args);

  private get(args: Parameters<FetchCandles>): Promise<CandlesResponse> {
    const key = JSON.stringify(args.map((a) => a ?? null));
    const t = this.now();
    for (const [k, r] of this.recent) if (t - r.at >= this.options.freshMs) this.recent.delete(k);
    const hit = this.recent.get(key);
    if (hit) return Promise.resolve(hit.value);
    const flying = this.inFlight.get(key);
    if (flying) return flying;
    const p = this.slot()
      .then(() => { this.sent += 1; return this.fetch(...args); })
      .then((value) => { this.recent.set(key, { at: this.now(), value }); return value; })
      .finally(() => { this.inFlight.delete(key); this.release(); });
    this.inFlight.set(key, p);
    return p;
  }

  private slot(): Promise<void> {
    if (this.running < this.options.maxInFlight) { this.running += 1; return Promise.resolve(); }
    return new Promise((resolve) => this.waiting.push(() => { this.running += 1; resolve(); }));
  }

  private release(): void {
    this.running -= 1;
    this.waiting.shift()?.();
  }
}
