import type { Page } from "@playwright/test";

/**
 * UI2-4b2: the v2 Charts page's API replies for e2e/charts-v2.e2e.ts - made-up candles, last price, chart settings,
 * candle sources and market sessions (no real account, price or id). UI2-4c: made-up watchlists (versioned), batch
 * quotes and stored drawings. UI2-4d: made-up saved chart layouts (versioned, as `/charts/layouts` does).
 * Registered after mockApi() and before page.goto(), so they answer first. The candles are a function of the request only (never of the clock), so a
 * screenshot is the same on every run.
 */
export const api = (path: string) => new RegExp(`/api(?:/v1)?${path}`);

export interface ChartsMocks {
  /** The timeframes `/market-data/sources` lists (default: all nine, week and month included). */
  timeframes?: string[];
  /** The stored chart settings `GET /me/prefs` starts from. */
  prefs?: Record<string, unknown>;
  /** NSE open (default) or closed until the next morning. */
  market?: "open" | "closed";
  /** UI2-4c: the stored watchlists `GET /watchlists` starts from (version 1 each). */
  watchlists?: { id: number; name: string; items: { symbol: string; exchange: string }[] }[];
  /** UI2-4c: stored horizontal lines per symbol (`{ "NIFTY 50": [24100, 24300] }`); `locked` prices stay on Delete all. */
  drawings?: Record<string, number[]>;
  locked?: number[];
  /** UI2-4d: the saved multi-chart layouts `GET /charts/layouts` starts from (`layout/1`, version 1 each). */
  layouts?: { id: number; name: string; layout: Record<string, unknown> }[];
}

export interface LayoutWrite { method: string; id: number | null; body: Record<string, unknown> | null }

export interface WatchlistWrite { method: string; id: number | null; body: Record<string, unknown> | null }

export interface CandleCall { symbol?: string; timeframe: string; lookback_days: number; before: string | null; forming?: boolean; volume_from_future?: boolean }

export interface ChartsCalls {
  candles: CandleCall[];
  prefsPuts: { version: string | null; body: Record<string, unknown> }[];
  ltp: number;
  /** UI2-4c */
  watchlistReads: number;
  watchlistWrites: WatchlistWrite[];
  quotes: { symbol: string; exchange: string }[][];
  drawingPosts: Record<string, unknown>[];
  deleteAll: string[];
  /** UI2-4d: each last-price read's symbol, and the saved layouts' writes. */
  ltpSymbols: string[];
  layoutWrites: LayoutWrite[];
}

const ALL = ["1min", "3min", "5min", "15min", "30min", "60min", "day", "week", "month"];
const STEP: Record<string, number> = { "1min": 60, "3min": 180, "5min": 300, "15min": 900, "30min": 1_800, "60min": 3_600 };
const BARS = 160;
const IST = 19_800;                                       // seconds east of UTC
const DAY = 86_400;
// "Now" in the specs: Monday 19 Oct 2026 11:00 IST. Sessions run 09:15-15:30 IST on weekdays.
const TODAY = Date.parse("2026-10-19T00:00:00+05:30") / 1000;
const weekday = (t: number) => new Date((t + IST) * 1000).getUTCDay();

/** The newest `BARS` bar starts of a timeframe, oldest first: intraday inside sessions (today up to 10:55), days on
 * weekdays (today's the forming one), weeks on Mondays, months on the 1st. */
function barTimes(timeframe: string): number[] {
  const out: number[] = [];
  if (timeframe === "month") for (let i = 0; out.length < BARS; i += 1) out.push(Date.UTC(2026, 9 - i, 1) / 1000 - IST);
  else if (timeframe === "week") for (let i = 0; out.length < BARS; i += 1) out.push(TODAY - i * 7 * DAY);
  else if (timeframe === "day") { for (let d = TODAY; out.length < BARS; d -= DAY) if (weekday(d) % 6 !== 0) out.push(d); }
  else {
    const step = STEP[timeframe];
    for (let d = TODAY; out.length < BARS; d -= DAY) {
      if (weekday(d) % 6 === 0) continue;
      const open = d + 9 * 3_600 + 15 * 60;
      const last = d === TODAY ? d + 10 * 3_600 + 55 * 60 : d + 15 * 3_600 + 30 * 60 - step;
      for (let t = last - ((last - open) % step); t >= open && out.length < BARS; t -= step) out.push(t);
    }
  }
  return out.reverse();
}

/** A made-up walk: smooth waves around 24,000, the same for the same timeframe every time. */
export function madeUpBars(timeframe: string): { timestamp: string; open: number; high: number; low: number; close: number; volume: number; forming?: boolean }[] {
  const seed = ALL.indexOf(timeframe) + 1;
  return barTimes(timeframe).map((t, k) => {
    const base = 24_000 + 400 * Math.sin(k / (9 + seed)) + 150 * Math.sin(k / 3.1) + k * 2;
    const open = base + 30 * Math.sin(k * 1.7);
    const close = base + 30 * Math.cos(k * 1.3);
    return { timestamp: new Date(t * 1000).toISOString(), open, close, high: Math.max(open, close) + 25 + 10 * Math.abs(Math.sin(k)),
             low: Math.min(open, close) - 25 - 10 * Math.abs(Math.cos(k)), volume: 50_000 + 30_000 * Math.abs(Math.sin(k / 2.3)) };
  });
}

/** A made-up price for a symbol: the same every time, between 100 and 50,000. */
function madeUpPrice(symbol: string): { ltp: number; change: number } {
  let h = 7;
  for (const c of symbol) h = (h * 31 + c.charCodeAt(0)) % 100_003;
  const ltp = Math.round((100 + (h % 49_900)) * 20) / 20;
  const change = Math.round(((h % 7) - 3) * ltp * 0.004 * 20) / 20;
  return { ltp, change };
}

export async function mockCharts(page: Page, { timeframes = ALL, prefs = {}, market = "open", watchlists = [], drawings = {}, locked = [], layouts = [] }: ChartsMocks = {}): Promise<ChartsCalls> {
  const calls: ChartsCalls = { candles: [], prefsPuts: [], ltp: 0, watchlistReads: 0, watchlistWrites: [], quotes: [], drawingPosts: [], deleteAll: [], ltpSymbols: [], layoutWrites: [] };
  let stored: Record<string, unknown> = prefs;
  let version = 1;
  const sessions = market === "open"
    ? { NSE: { is_open: true, reason: "NSE regular session open", next_open: null } }
    : { NSE: { is_open: false, reason: "NSE closed for the day", next_open: "2026-10-20T09:15:00+05:30" } };

  await page.route(api("/system/status$"), (r) => r.fulfill({ json: { maintenance_mode: false, maintenance_message: null, disabled_brokers: [], sessions } }));
  await page.route(api("/market-data/sources$"), (r) => r.fulfill({ json: {
    sources: [{ broker: "upstox", account_label: "primary", token_status: "VALID", usable: true }], usable: true, timeframes,
    max_symbols: 50, intraday_max_lookback_days: 30, daily_max_lookback_days: 730,
  } }));
  await page.route(api("/market-data/candles$"), (r) => {
    const body = r.request().postDataJSON() as CandleCall & { symbols: string[] };
    calls.candles.push({ symbol: body.symbols[0], timeframe: body.timeframe, lookback_days: body.lookback_days, before: body.before, forming: body.forming, volume_from_future: body.volume_from_future });
    const symbol = body.symbols[0];
    // Older pages: none (the broker has no older history), so the chart must ask only once.
    const bars = body.before ? [] : madeUpBars(body.timeframe);
    if (!body.before && body.forming && bars.length) bars[bars.length - 1].forming = true;
    const index = symbol === "NIFTY 50";
    return r.fulfill({ json: {
      source: { broker: "upstox", account_label: "primary" }, exchange: "NSE", timeframe: body.timeframe, base_interval: "1min",
      lookback_days: body.lookback_days, before: body.before, fetched_at: "2026-10-19T05:30:00Z", warnings: [], note: "",
      symbols: { [symbol]: { bars, count: bars.length, first: bars[0]?.timestamp ?? null, last: bars.at(-1)?.timestamp ?? null, error: null,
                             volume_source: index && body.volume_from_future ? { symbol: "NIFTY26OCTFUT", expiry: "2026-10-27", exchange: "NFO", underlying: "NIFTY", from: null, label: "" } : null } },
    } });
  });
  await page.route(api("/market-data/ltp"), (r) => {
    calls.ltp += 1;
    calls.ltpSymbols.push(new URL(r.request().url()).searchParams.get("symbol") ?? "");
    return r.fulfill({ json: { symbol: "NIFTY 50", exchange: "NSE", source_broker: "upstox", fetched_at: "2026-10-19T05:30:00Z", ltp: 24_420.5,
                                timestamp: "2026-10-19T05:29:58Z", age_seconds: 2, stale: false, stale_reason: null, source: "tick", bid: null, ask: null, volume: null } });
  });
  await page.route(api("/me/prefs(/chart)?(\\?.*)?$"), (r) => {
    const req = r.request();
    if (req.method() === "PUT") {
      stored = req.postDataJSON() as Record<string, unknown>;
      calls.prefsPuts.push({ version: new URL(req.url()).searchParams.get("version"), body: stored });
      version += 1;
      return r.fulfill({ json: { namespace: "chart", value: stored, version } });
    }
    return r.fulfill({ json: { prefs: { chart: stored }, versions: { chart: version } } });
  });
  // Drawings: stored per symbol, with the backend's Delete all rule (unlocked only, confirm=true).
  type Row = { id: number; symbol: string; exchange: string; kind: string; drawing: unknown; version: number; locked: boolean; updated_at: null; timeframe: string | null };
  let nextDrawing = 1;
  const saved: Row[] = Object.entries(drawings).flatMap(([symbol, prices]) => prices.map((p) => ({
    id: nextDrawing++, symbol, exchange: "NSE", kind: "hline", drawing: { schema: "drawing/1", kind: "hline", anchors: [{ p }] }, version: 1,
    locked: locked.includes(p), updated_at: null, timeframe: null,
  })));
  await page.route(api("/charts/drawings(/\\d+)?(\\?.*)?$"), (r) => {
    const req = r.request();
    const url = new URL(req.url());
    const symbol = url.searchParams.get("symbol");
    if (req.method() === "POST") {
      const body = req.postDataJSON() as { symbol: string; exchange: string; drawing: { kind: string }; timeframe?: string };
      calls.drawingPosts.push(body);
      const row: Row = { id: nextDrawing++, symbol: body.symbol, exchange: body.exchange, kind: body.drawing.kind, drawing: body.drawing, version: 1,
                         locked: false, updated_at: null, timeframe: body.timeframe ?? null };
      saved.push(row);
      return r.fulfill({ status: 201, json: row });
    }
    if (req.method() === "DELETE" && !/\/\d+$/.test(url.pathname)) {
      calls.deleteAll.push(url.search);
      if (url.searchParams.get("confirm") !== "true") return r.fulfill({ status: 400, json: { detail: "needs confirm=true" } });
      const gone = saved.filter((d) => d.symbol === symbol && !d.locked);
      for (const d of gone) saved.splice(saved.indexOf(d), 1);
      return r.fulfill({ json: { deleted: gone.length, kept_locked: saved.filter((d) => d.symbol === symbol).length, ids: gone.map((d) => d.id) } });
    }
    return r.fulfill({ json: saved.filter((d) => d.symbol === symbol) });
  });

  // Watchlists: versioned as the backend does (a PUT naming a stale version gets 409 with the current list).
  const lists = watchlists.map((w) => ({ ...w, items: [...w.items], version: 1 }));
  let nextList = 100;
  await page.route(api("/watchlists(/\\d+)?$"), (r) => {
    const req = r.request();
    const id = Number(new URL(req.url()).pathname.match(/\/(\d+)$/)?.[1] ?? NaN);
    const body = req.method() === "GET" || req.method() === "DELETE" ? null : req.postDataJSON() as Record<string, unknown>;
    if (req.method() === "GET") { calls.watchlistReads += 1; return r.fulfill({ json: lists }); }
    calls.watchlistWrites.push({ method: req.method(), id: Number.isNaN(id) ? null : id, body });
    if (req.method() === "POST") {
      const made = { id: nextList++, name: String(body?.name), items: (body?.items as typeof lists[0]["items"]) ?? [], version: 1 };
      lists.push(made);
      return r.fulfill({ status: 201, json: made });
    }
    const at = lists.findIndex((l) => l.id === id);
    if (at < 0) return r.fulfill({ status: 404, json: { detail: "Watchlist not found" } });
    if (req.method() === "DELETE") { lists.splice(at, 1); return r.fulfill({ json: { deleted: id } }); }
    if (body?.version !== undefined && body.version !== lists[at].version) {
      return r.fulfill({ status: 409, json: { detail: { message: "the watchlist changed since this edit was made", current: lists[at] } } });
    }
    lists[at] = { ...lists[at], name: String(body?.name), items: body?.items as typeof lists[0]["items"], version: lists[at].version + 1 };
    return r.fulfill({ json: lists[at] });
  });
  await page.route(api("/market-data/quotes$"), (r) => {
    const items = (r.request().postDataJSON() as { items: { symbol: string; exchange: string }[] }).items;
    calls.quotes.push(items);
    return r.fulfill({ json: {
      source: { broker: "upstox", account_label: "primary" }, fetched_at: "2026-10-19T05:30:00Z", broker_calls: 1, missing: 0, warnings: [],
      items: items.map(({ symbol, exchange }) => {
        const { ltp, change } = madeUpPrice(symbol);
        const prev = ltp - change;
        return { symbol, exchange, ltp, prev_close: prev, change, change_pct: Math.round((change / prev) * 10_000) / 100, timestamp: "2026-10-19T05:29:58Z",
                 age_seconds: 2, stale: false, stale_reason: null, source: "tick", missing: false, error: null };
      }),
    } });
  });
  // Saved multi-chart layouts (UI2-4d): versioned as the backend does (a PUT naming a stale version gets 409).
  const rows = layouts.map((l) => ({ ...l, version: 1, updated_at: null as string | null }));
  let nextLayout = 200;
  await page.route(api("/charts/layouts(/\\d+)?(\\?.*)?$"), (r) => {
    const req = r.request();
    const id = Number(new URL(req.url()).pathname.match(/\/(\d+)$/)?.[1] ?? NaN);
    const body = req.method() === "GET" || req.method() === "DELETE" ? null : req.postDataJSON() as Record<string, unknown>;
    if (req.method() === "GET") return r.fulfill({ json: rows });
    calls.layoutWrites.push({ method: req.method(), id: Number.isNaN(id) ? null : id, body });
    if (req.method() === "POST") {
      const made = { id: nextLayout++, name: String(body?.name), layout: body?.layout as Record<string, unknown>, version: 1, updated_at: null };
      rows.unshift(made);
      return r.fulfill({ status: 201, json: made });
    }
    const at = rows.findIndex((l) => l.id === id);
    if (at < 0) return r.fulfill({ status: 404, json: { detail: "Layout not found" } });
    if (req.method() === "DELETE") { rows.splice(at, 1); return r.fulfill({ json: { deleted: id } }); }
    if (body?.version !== rows[at].version) return r.fulfill({ status: 409, json: { detail: { message: "the layout changed since this edit was made", current: rows[at] } } });
    rows[at] = { ...rows[at], layout: body?.layout as Record<string, unknown>, version: rows[at].version + 1 };
    return r.fulfill({ json: rows[at] });
  });
  await page.route(api("/instrument-master/search"), (r) => {
    const type = new URL(r.request().url()).searchParams.get("type");
    return r.fulfill({ json: type === "INDEX"
      ? [{ tradingsymbol: "NIFTY BANK", exchange: "NSE", name: "Nifty Bank", instrument_type: "INDEX" }]
      : [{ tradingsymbol: "BANKBEES", exchange: "NSE", name: "Made-up bank fund", instrument_type: "EQ" }] });
  });
  return calls;
}
