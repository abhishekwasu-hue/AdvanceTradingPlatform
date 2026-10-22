import type { AnalysisItem, AnalysisResult, AnalyzeStatus, SetupStatus } from "./types";

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
    hybrid_note: "Hybrid analysis is coming soon. Use Image only for now.",
    limits: { tier: "pro", daily_limit: 10, monthly_limit: 150, daily_used: 1, monthly_used: 3, cost_cap_usd: 10, cost_used_usd: 0.03,
      resets: "daily at 00:00 UTC, monthly on the 1st (UTC)" },
    upload: { types: ["image/png", "image/jpeg"], max_bytes: 5 * 1024 * 1024, max_width: 4096, max_height: 4096 },
    retention_days: 30, label: IMAGE_ONLY_LABEL, disclaimer: DISCLAIMER, link_message: "Please upload a screenshot instead",
    ...over,
  };
}
