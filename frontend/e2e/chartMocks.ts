import type { Page, Route } from "@playwright/test";
import { readFileSync } from "node:fs";

/**
 * N4 render checks: synthetic bars and the chart API answers (candles, features, layouts, the price-action layer,
 * studies) for the chart pages, on top of mockApi's login. Everything is generated here - no backend, no market data.
 */
export interface Bar { timestamp: string; open: number; high: number; low: number; close: number; volume: number }

/** `n` 5-minute bars (a seeded walk), oldest first, ending before `end`; session-shaped enough for the chart. */
export function bars(n: number, stepMinutes = 5, end = Date.parse("2026-10-09T10:00:00Z")): Bar[] {
  let seed = 7;
  const rnd = () => { seed = (seed * 1103515245 + 12345) % 2 ** 31; return seed / 2 ** 31; };
  const out: Bar[] = [];
  let price = 24_000;
  const t0 = end - n * stepMinutes * 60_000;
  for (let i = 0; i < n; i++) {
    const open = price;
    const close = Math.max(100, open + (rnd() - 0.5) * 40);
    const high = Math.max(open, close) + rnd() * 15;
    const low = Math.min(open, close) - rnd() * 15;
    out.push({ timestamp: new Date(t0 + i * stepMinutes * 60_000).toISOString(), open, high, low, close, volume: Math.round(1000 + rnd() * 5000) });
    price = close;
  }
  return out;
}

export function candlesResponse(symbol: string, b: Bar[]) {
  return { source: { broker: "e2e", account_label: "e2e" }, exchange: "NSE", timeframe: "5min", base_interval: "5min", lookback_days: 30, fetched_at: "2026-10-09T10:00:00Z",
           symbols: { [symbol]: { bars: b, count: b.length, first: b[0]?.timestamp ?? null, last: b[b.length - 1]?.timestamp ?? null, error: null } },
           warnings: [], note: "" };
}

const fixture = (path: string): unknown => JSON.parse(readFileSync(new URL(path, import.meta.url), "utf8"));
/** The CH3 price-action golden (the backend's own answer on the frozen fixture), as the layer response. */
export const paGolden = (fixture("../src/charting/__fixtures__/pa_layer_golden.json") as { default: Record<string, unknown> }).default;

export interface ChartRoutes {
  candles?: (symbol: string) => Bar[];
  features?: Record<string, boolean>;
  /** The price-action layer: an answer per request (null = flag off, 503), optionally after a delay (ms). */
  priceAction?: (url: URL) => { body: unknown; delayMs?: number } | null;
  studies?: (url: URL) => unknown | null;
  /** Requests seen, for the assertions. */
  seen?: string[];
}

export async function chartRoutes(page: Page, r: ChartRoutes): Promise<void> {
  await page.route(/\/api(\/v1)?\/(market-data|system\/features|charts)\b/, async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^\/api(\/v1)?/, "");
    r.seen?.push(`${path}${url.search}`);
    if (path === "/market-data/candles") {
      const body = route.request().postDataJSON() as { symbols: string[]; before?: string | null };
      const sym = body.symbols[0];
      return route.fulfill({ json: candlesResponse(sym, body.before ? [] : (r.candles ? r.candles(sym) : bars(500))) });
    }
    if (path.startsWith("/market-data/")) return route.fulfill({ status: 404, json: { detail: "no live price in the render check" } });
    if (path === "/system/features") return route.fulfill({ json: r.features ?? {} });
    if (path === "/charts/layouts") return route.fulfill({ json: [] });
    if (path === "/charts/layers/price-action") {
      const a = r.priceAction?.(url);
      if (!a) return route.fulfill({ status: 503, json: { detail: "disabled" } });
      if (a.delayMs) await new Promise((res) => setTimeout(res, a.delayMs));
      return route.fulfill({ json: a.body });
    }
    if (path === "/charts/studies") {
      const s = r.studies?.(url);
      return s ? route.fulfill({ json: s }) : route.fulfill({ status: 503, json: { detail: "disabled" } });
    }
    return route.fulfill({ status: 503, json: { detail: "disabled in the render check" } });
  });
}

/** Two animation frames: whatever React committed has been painted. */
export async function painted(page: Page): Promise<void> {
  await page.evaluate(() => new Promise<void>((res) => requestAnimationFrame(() => requestAnimationFrame(() => res()))));
}
