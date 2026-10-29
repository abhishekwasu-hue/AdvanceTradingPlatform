import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import pageSource from "./ChartsPageV2.tsx?raw";
import { ReadChartBody, ReadChartPanel } from "./ReadChartV2";
import { READ_LAYER_ID, readLayer, readSummary, readTimeframe, type ReadChart } from "./readChart";

/** AUD-T5 (owner audit 28 Oct): Read this chart - the server's reading as layers, one open line and a closed Details. */
const read: ReadChart = {
  instrument: "NSE:NIFTY BANK", tf: "15m", bars: 400, as_of: "2026-10-28T08:00:00+00:00", last_bar: 1793260800, note: "A reading of past prices in structure words - reference, not advice.",
  answer_key: null,
  structure: { direction: "up", words: "Structure on the 15-minute chart: up; the last event was a break of structure up.",
               last_event: { type: "BOS", direction: "up", time: 1793250000, words: "a break of structure" },
               protected: { price: 51000, side: "low", words: "protected swing low of this structure" } },
  areas: [
    { key: "z1", role: "demand", low: 51100, high: 51200, low_text: "51,100.00", high_text: "51,200.00", timeframe: "1h", state: "fresh",
      state_words: "untouched since it formed", touches: 0, words: "Demand area 51,100.00 - 51,200.00 from the 1-hour chart: untouched since it formed." },
    { key: "z2", role: "supply", low: 51600, high: 51700, low_text: "51,600.00", high_text: "51,700.00", timeframe: "15m", state: "tested",
      state_words: "price has come back to it", touches: 2, words: "Supply area 51,600.00 - 51,700.00 from the 15-minute chart: price has come back to it (2 touches)." },
  ],
  pullback: { available: true, trend: "UP", phase: "impulse", counter: "pullback", as_of: "2026-10-27", words: "Daily trend: up; a pullback inside the running leg." },
  commitment: { time: 1793259900, bar_end: "2026-10-28T07:45:00+00:00", role: "demand", form: "bullish engulfing", area: { low: 51100, high: 51200, key: "z1" },
                words: "Commitment candle (bullish engulfing) at the demand area 51,100.00 - 51,200.00." },
  lines: [
    "Structure on the 15-minute chart: up; the last event was a break of structure up.", "Daily trend: up; a pullback inside the running leg.",
    "Demand area 51,100.00 - 51,200.00 from the 1-hour chart: untouched since it formed.",
    "Supply area 51,600.00 - 51,700.00 from the 15-minute chart: price has come back to it (2 touches).",
    "Commitment candle (bullish engulfing) at the demand area 51,100.00 - 51,200.00.",
  ],
};
const CALL_WORDS = /\b(buy|sell|target|stop|stop-?loss|SL|entry)\b/i;

describe("AUD-T5 Read this chart", () => {
  it("maps the chart timeframes the server reads; week and month are not offered", () => {
    expect(readTimeframe("15min")).toBe("15m");
    expect(readTimeframe("60min")).toBe("1h");
    expect(readTimeframe("day")).toBe("1d");
    expect(readTimeframe("week")).toBeNull();
    expect(readTimeframe("month")).toBeNull();
  });

  it("draws each area as its own band (never merged), the protected swing and the commitment candle", () => {
    const layer = readLayer(read);
    expect(READ_LAYER_ID).toBe("read-chart");
    expect(layer.bands?.map((b) => [b.low, b.high])).toEqual([[51100, 51200], [51600, 51700]]);
    expect(layer.bands?.[0].label).toBe("demand area · 1h · fresh");
    expect(layer.lines).toEqual([{ price: 51000, style: "dashed", title: "protected swing low" }]);
    expect(layer.markers?.[0]).toMatchObject({ time: 1793259900, position: "belowBar", text: "commitment" });
    expect(readLayer({ ...read, commitment: null, structure: null }).markers).toEqual([]);
  });

  it("keeps one line open and the rest in a closed Details, in structure words only", () => {
    const html = renderToStaticMarkup(<ReadChartBody read={read} />);
    expect(html).toContain(readSummary(read));
    expect(html).toContain('data-details="charts.read"');
    expect(html).not.toMatch(/<details[^>]*open/);
    expect(html).toContain("Commitment candle (bullish engulfing)");
    expect(html).not.toMatch(CALL_WORDS);
    expect(html).not.toContain("verified on");                     // no answer key stored: no line
  });

  it("shows nothing while off or idle, and a plain line while loading or failed", () => {
    expect(renderToStaticMarkup(<ReadChartPanel state={{ status: "off" }} />)).toBe("");
    expect(renderToStaticMarkup(<ReadChartPanel state={{ status: "idle" }} />)).toBe("");
    expect(renderToStaticMarkup(<ReadChartPanel state={{ status: "loading" }} />)).toContain("Reading this chart");
    expect(renderToStaticMarkup(<ReadChartPanel state={{ status: "failed" }} />)).toContain("could not be read");
  });

  it("the toolbar button shows only with the read_chart flag", () => {
    expect(pageSource).toContain('features.on("read_chart")');
    expect(pageSource).toContain('aria-label="Read this chart"');
  });
});
