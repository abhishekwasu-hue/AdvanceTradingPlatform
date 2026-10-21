import type { Page } from "@playwright/test";

/**
 * UI2-4b2: the v2 Charts page's API replies for e2e/charts-v2.e2e.ts - made-up candles, last price, chart settings,
 * candle sources and market sessions (no real account, price or id). Registered after mockApi() and before
 * page.goto(), so they answer first. The candles are a function of the request only (never of the clock), so a
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
}

export interface CandleCall { timeframe: string; lookback_days: number; before: string | null; forming?: boolean; volume_from_future?: boolean }

export interface ChartsCalls {
  candles: CandleCall[];
  prefsPuts: { version: string | null; body: Record<string, unknown> }[];
  ltp: number;
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

export async function mockCharts(page: Page, { timeframes = ALL, prefs = {}, market = "open" }: ChartsMocks = {}): Promise<ChartsCalls> {
  const calls: ChartsCalls = { candles: [], prefsPuts: [], ltp: 0 };
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
    calls.candles.push({ timeframe: body.timeframe, lookback_days: body.lookback_days, before: body.before, forming: body.forming, volume_from_future: body.volume_from_future });
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
  await page.route(api("/charts/drawings"), (r) => r.fulfill({ json: [] }));
  await page.route(api("/instrument-master/search"), (r) => {
    const type = new URL(r.request().url()).searchParams.get("type");
    return r.fulfill({ json: type === "INDEX"
      ? [{ tradingsymbol: "NIFTY BANK", exchange: "NSE", name: "Nifty Bank", instrument_type: "INDEX" }]
      : [{ tradingsymbol: "BANKBEES", exchange: "NSE", name: "Made-up bank fund", instrument_type: "EQ" }] });
  });
  return calls;
}
