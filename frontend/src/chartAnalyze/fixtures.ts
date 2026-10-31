import type { AnalysisItem, AnalysisResult, AnalyzeStatus, HybridResult, SetupStatus, StrikesAnswer } from "./types";

/**
 * Analyze a chart: made-up results for the unit and browser tests (public repository: no real chart, no real data, no
 * method text). The shapes are the backend contract's (app/chart_analyze/contract.py) for a 640 x 360 drawn image.
 */

export const FIXTURE_WIDTH = 640;
export const FIXTURE_HEIGHT = 360;
export const IMAGE_ONLY_LABEL = "Image-only analysis: prices and volume are read from the picture and may be approximate";
export const DISCLAIMER = "Educational only. This describes the situation on the chart and the possibilities, as the method reads them. "
  + "It is not investment advice or a recommendation to buy or sell, and ATP places no order from it.";

export function fixtureResult(status: SetupStatus): AnalysisResult {
  const forming = status === "forming";
  return {
    contract_version: 1, mode: "image_only", label: IMAGE_ONLY_LABEL, disclaimer: DISCLAIMER, approximate_prices: true,
    coordinate_space: "image_pixels", image: { width: FIXTURE_WIDTH, height: FIXTURE_HEIGHT },
    recognised: { symbol: "DEMO", exchange: "NSE", timeframe: "15m", first_bar: "made-up start", last_bar: "made-up end" },
    story: ["Made-up story: the drawn candles rise, pause and rise again.", "Made-up story, part two."],
    trend: { side: "up", text: "made-up higher highs and higher lows" },
    current_state: { phase: status === "none" ? "range" : "pullback", text: "made-up current state" },
    rsi: { divergence: "none", text: "made-up: no divergence drawn" },
    setup: {
      side: status === "none" ? "none" : "bull_put", label: status === "none" ? null : "A", status,
      waiting_for: forming ? "a close back above the made-up area" : null,
      trigger: { text: "a close above the made-up trigger line", price: 120.5 },
      invalidation: { text: "a close below the made-up area", price: 101 },
      next_areas: [{ text: "the made-up upper area", price: 140 }],
    },
    evidence: [
      { name: "Trend side", present: true, reason: "made-up reason one" },
      { name: "Pullback", present: status !== "none", reason: "made-up reason two" },
      { name: "Confluence", present: false, reason: "made-up reason three" },
    ],
    scenarios: { primary: "Made-up primary scenario.", alternative: "Made-up alternative scenario.",
      primary_invalidated_at: { text: "below the made-up area", price: 101 } },
    what_to_wait_for: status === "formed" ? null : status === "forming"
      ? { condition: "a made-up close above the trigger line", alert: { kind: "above", price: 120.5 } }
      : { condition: "a made-up lower-timeframe change of character", alert: null },
    htf_context: [{ timeframe: "1h", trend: "up", nearest_areas: [{ side: "buyer", state: "fresh", price_low: 98, price_high: 101 }] }],
    key_levels: [
      { kind: "area", name: "Made-up buyer area", side: "buyer", state: "tested", price_low: 98, price_high: 101 },
      { kind: "line", name: "Made-up line", side: null, state: null, price_low: 120.5, price_high: 120.5 },
    ],
    annotations: {
      swings: [{ label: "HL", x: 120, y: 250, price: 100 }, { label: "HH", x: 400, y: 80, price: 130 }, { label: "LH", x: 520, y: 120, price: 126 }],
      areas: [{ id: "a1", side: "buyer", state: "tested", x0: 100, y0: 240, x1: 600, y1: 270, price_low: 98, price_high: 101, note: "made-up area" },
        { id: "a2", side: "seller", state: "fresh", x0: 300, y0: 60, x1: 600, y1: 85, price_low: 128, price_high: 131, note: "made-up area" }],
      lines: [{ id: "l1", name: "Made-up line", x0: 50, y0: 150, x1: 600, y1: 150, touches: [{ x: 200, y: 150 }, { x: 450, y: 150 }], note: "made-up" }],
      rsi_divergence: status === "formed" ? { kind: "bullish", price: { x0: 120, y0: 250, x1: 330, y1: 262 }, rsi: null, note: "made-up" } : null,
      commitment_candle: status === "none" ? null : { x: 300, y: 200, note: "made-up" },
      ltf_choch: forming ? { x: 560, y: 180, note: "made-up" } : null,
      setup_lines: status === "none" ? [] : [{ kind: "trigger", y: 150, price: 120.5 }, { kind: "invalidation", y: 270, price: 101 }, { kind: "next_area", y: 40, price: 140 }],
    },
  };
}

export function fixtureItem(id: number, status: SetupStatus | null, created = "2026-10-20T09:30:00+00:00"): AnalysisItem {
  const result = status ? fixtureResult(status) : null;
  return {
    id, mode: "image_only", source: "upload", source_host: null, status: result ? "done" : "ready",
    image: { width: FIXTURE_WIDTH, height: FIXTURE_HEIGHT, url: `/chart-analyze/items/${id}/image` }, error: null, provider: "anthropic",
    model: "made-up-model", engine_version: "fake-engine 0.0 / test", billed_to_user_key: false, cost_usd: 0.01, created_at: created,
    analysed_at: result ? created : null, summary: result ? { setup_status: result.setup.status, trend: "up", symbol: "DEMO" } : null,
    result, label: IMAGE_ONLY_LABEL, disclaimer: DISCLAIMER,
  };
}

export function fixtureStatus(over: Partial<AnalyzeStatus> = {}): AnalyzeStatus {
  return {
    enabled: true, engine: { installed: true, name: "fake-engine", version: "0.0" }, modes: { image_only: true, hybrid: false },
    hybrid_note: "Advanced analysis is not switched on for your organisation yet. Use AI only for now.",
    limits: { tier: "pro", daily_limit: 10, monthly_limit: 150, daily_used: 1, monthly_used: 3, cost_cap_usd: 10, cost_used_usd: 0.03,
      resets: "daily at 00:00 UTC, monthly on the 1st (UTC)" },
    upload: { types: ["image/png", "image/jpeg"], max_bytes: 5 * 1024 * 1024, max_width: 4096, max_height: 4096 },
    retention_days: 30, label: IMAGE_ONLY_LABEL, disclaimer: DISCLAIMER, link_message: "Please upload a screenshot instead",
    ...over,
  };
}

/** A made-up "Option strikes · from your settings" answer: one made-up bull put spread setting on the made-up symbol,
 * worked out at the fixture's trigger (120.5) on a made-up ladder (steps of 5). */
export function fixtureStrikes(): StrikesAnswer {
  return {
    source: "your_settings", side: "bull_put", symbol: "DEMO",
    reference: { text: "the trigger level", price: 120.5, approximate: true },
    approximate_note: "Worked out from a price read from the picture, so they are approximate. Check them on the option chain.",
    items: [{
      id: 7, mode: "Paper", symbol: "DEMO", settings: "Bull put spread · main strike 1 strike out of the money · wing 2 strikes away · nearest expiry",
      expiry: "2026-10-27", legs: [{ label: "Main strike", right: "PE", strike: 115, ratio: 1 }, { label: "Wing", right: "PE", strike: 105, ratio: 1 }], note: null,
    }],
    message: null,
  };
}

/** The structure-only copy a share link opens (backend share.py, PR-A4): the made-up forming result without the name, the
 * prices, the dates, the timeframes and the alert. */
export function fixtureSharedResult(): AnalysisResult {
  const PRICE_KEYS = new Set(["price", "price_low", "price_high", "low", "high"]);
  const strip = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(strip);
    if (value && typeof value === "object") {
      return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, PRICE_KEYS.has(k) || k === "alert" ? null : strip(v)]));
    }
    return value;
  };
  const base = strip(fixtureResult("forming")) as AnalysisResult;
  return {
    ...base, approximate_prices: false, shared: true,
    shared_note: "Shared by a member of your organisation. The chart's name, its prices, its dates and the picture are left out: this copy shows the structure only.",
    recognised: { symbol: null, exchange: null, timeframe: null, first_bar: null, last_bar: null },
    htf_context: base.htf_context.map((h) => ({ ...h, timeframe: null })),
  };
}

/** M59 CR-4: a made-up "Advanced analysis" result - ATP's reading of made-up stored candles for the made-up DEMO 1-hour. */
export function fixtureHybrid(withAi = true): HybridResult {
  const bars = Array.from({ length: 40 }, (_, i) => {
    const o = 100 + Math.sin(i / 4) * 6 + i * 0.2;
    const c = o + (i % 3 === 0 ? -1.2 : 1.1);
    return { t: 1_700_000_000 + i * 3600, o, h: Math.max(o, c) + 0.8, l: Math.min(o, c) - 0.7, c };
  });
  const brk = { label: "Swing break", price: 104.5, direction: "up" as const, time: bars[34].t };
  return {
    contract_version: 1, mode: "hybrid", method: withAi ? "code_ai" : "code",
    label: withAi ? "Advanced analysis (code + AI)" : "Advanced analysis (code)", disclaimer: DISCLAIMER,
    pick: { symbol: "DEMO", timeframe: "1h" },
    side_by_side_note: "ATP does not know your picture's price scale, so nothing is drawn on it. ATP's reading is drawn on its own chart of the stored candles for the symbol and timeframe you picked, shown beside your chart.",
    reading: {
      symbol: "DEMO", timeframe: "1h", timeframe_label: "1-hour", as_of: "2026-01-01T10:00:00+00:00", last_close: 106.2, bars,
      table: [
        { tf: "1w", label: "Weekly", ready: false, trend: null, weak: false, protected: null, last_break: null },
        { tf: "1d", label: "Daily", ready: true, trend: "sideways", weak: false, protected: null, last_break: null },
        { tf: "1h", label: "1-hour", ready: true, trend: "up", weak: true, protected: 101.25, last_break: brk },
        { tf: "15m", label: "15-minute", ready: true, trend: "down", weak: false, protected: 107.4, last_break: { ...brk, label: "Break against the trend", price: 106.9 } },
      ],
      areas: [
        { where: "above", low: 109.1, high: 110.4, distance: 2.9, state: "fresh", timeframe: "1h" },
        { where: "below", low: 102.3, high: 103.6, distance: 2.6, state: "tested", timeframe: "1d" },
      ],
      levels: [{ kind: "swing high", price: 111.8 }, { kind: "swing low", price: 99.4 }], protected: 101.25, last_break: brk,
      trigger_candle: { index: 34, ...bars[34] }, zones_note: null,
      checklist: [
        { name: "Trend on the 1-hour chart", present: true, reason: "Up (weak)" },
        { name: "Protected level", present: true, reason: "101.25" },
        { name: "Last break of structure", present: true, reason: "Swing break at 104.50" },
        { name: "Trigger candle marked", present: true, reason: "The candle of the last break is marked on ATP's chart" },
        { name: "Area of interest nearby", present: true, reason: "1 above, 1 below" },
        { name: "Price inside an area of interest", present: false, reason: "Between areas" },
        { name: "Higher and lower timeframes read", present: false, reason: "Data not ready: Weekly" },
      ],
    },
    chart: { width: 960, height: 540 },
    ai: withAi ? {
      story: ["Made-up story: the made-up candles rise in steps and pause above the made-up protected level."],
      trend: { side: "up", text: "made-up higher highs" }, current_state: { phase: "pullback", text: "made-up pullback" },
      setup: fixtureResult("forming").setup, evidence: [],
      scenarios: { primary: "Made-up primary scenario.", alternative: "Made-up alternative scenario.", primary_invalidated_at: { text: "", price: null } },
      what_to_wait_for: { condition: "a made-up close back above the made-up area", alert: null },
    } : null,
    notes: [],
  };
}
