import type { Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { madeUpBars } from "./chartsMocks";

/**
 * CP-UX CU2 / CU3: the Copilot screen 1 numbers strip (GET /api/market/strip?sensex=true: the dashboard fixture's three
 * indices plus a made-up SENSEX) and the answer card's mini chart candles (the Charts mocks' made-up bars, scaled to
 * the symbol's level). Made-up data only.
 */
const D = JSON.parse(readFileSync(new URL("./fixtures/dashboard_v2.json", import.meta.url), "utf8")) as { strip: { indices: unknown[] } & Record<string, unknown> };

export const SENSEX_ROW = { key: "SENSEX", label: "SENSEX", symbol: "SENSEX", exchange: "BSE", ltp: 81261.1, prev_close: 81020.4, change: 240.7,
  change_pct: 0.2971, as_of: "2026-10-19T05:29:58Z", stale: false, source: "live", missing: false };

export function copilotStrip(): Record<string, unknown> {
  return { ...D.strip, indices: [...D.strip.indices.slice(0, 2), SENSEX_ROW, D.strip.indices[2]] };
}

const LEVEL: Record<string, number> = { SENSEX: 3.38 };

export async function mockCopilotNumbers(page: Page): Promise<{ strip: string[]; candles: string[] }> {
  const calls = { strip: [] as string[], candles: [] as string[] };
  await page.route(/\/api(?:\/v1)?\/market\/strip(\?.*)?$/, (r) => { calls.strip.push(r.request().url()); return r.fulfill({ json: copilotStrip() }); });
  await page.route(/\/api(?:\/v1)?\/market-data\/candles$/, (r) => {
    const body = r.request().postDataJSON() as { symbols: string[]; timeframe: string; lookback_days: number; before?: string | null };
    const symbol = body.symbols[0];
    calls.candles.push(symbol);
    const k = LEVEL[symbol] ?? 1;
    const bars = body.before ? [] : madeUpBars(body.timeframe).map((b) => ({ ...b, open: b.open * k, high: b.high * k, low: b.low * k, close: b.close * k }));
    return r.fulfill({ json: { source: { broker: "upstox", account_label: "primary" }, exchange: symbol === "SENSEX" ? "BSE" : "NSE", timeframe: body.timeframe,
      base_interval: "1min", lookback_days: body.lookback_days, before: body.before ?? null, fetched_at: "2026-10-19T05:30:00Z", warnings: [], note: "",
      symbols: { [symbol]: { bars, count: bars.length, first: bars[0]?.timestamp ?? null, last: bars.at(-1)?.timestamp ?? null, error: null, volume_source: null } } } });
  });
  return calls;
}
