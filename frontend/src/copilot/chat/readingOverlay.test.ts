import { describe, expect, it } from "vitest";
import { asAtpReading, readingIsEmpty } from "../../chart/atpReading";
import { noAreaText, type AnswerHeadline, type StructureCard } from "./model";
import { readingOverlay } from "./readingOverlay";

/** Owner 15 Nov ("Copilot must annotate its analysis on the chart"): the answer's reading -> chart items. */
const card = (over: Partial<StructureCard> = {}): StructureCard => ({
  symbol: "RELCHEMQ", price: 124.15, timeframe: "Daily", swing_levels: [], watch: null, areas_above: [], areas_below: [],
  timeframes: [
    { tf: "1w", label: "Weekly", ready: true, trend: "down", weak: true, protected: 130,
      last_break: { label: "Break against the trend", price: 130, time: 1762128000, direction: "down" } },
    { tf: "1d", label: "Daily", ready: true, trend: "sideways", weak: false, protected: null, last_break: null },
    { tf: "1h", label: "1-hour", ready: false, trend: null, weak: false, protected: null, last_break: null },
    { tf: "15m", label: "15-minute", ready: true, trend: "up", weak: false, protected: 121.5,
      last_break: { label: "Swing break", price: 123, direction: "up" } },
  ],
  ...over,
});

describe("readingOverlay", () => {
  it("draws each protected level, the last break at its bar and the trend per timeframe - from the answer only", () => {
    const r = readingOverlay(card());
    expect(r.levels).toEqual([
      { price: 130, title: "Weekly protected", tone: "down" },
      { price: 121.5, title: "15-minute protected", tone: "up" },
    ]);
    // the weekly break carries its bar; the 15-minute one has none (an older answer): no marker, never a guessed bar
    expect(r.markers).toEqual([{ timestamp: new Date(1762128000 * 1000).toISOString(), position: "aboveBar", color: "down", shape: "arrowDown",
                                 text: "Weekly · Break against the trend" }]);
    expect(r.trends.map((t) => `${t.label} ${t.text}`)).toEqual(["Weekly ▼ Down · weak", "Daily Sideways", "15-minute ▲ Up"]);
    expect(r.areas).toEqual([]);
  });

  it("merges one price shared by two timeframes into one line, neutral when their trends differ", () => {
    const c = card();
    c.timeframes[3] = { ...c.timeframes[3], protected: 130 };
    expect(readingOverlay(c).levels).toEqual([{ price: 130, title: "Weekly · 15-minute protected", tone: "neutral" }]);
  });

  it("draws the areas as bands with their role, timeframe and state; context labelled; a line keeps one price", () => {
    const r = readingOverlay(card({
      areas_above: [{ side: "supply", from: 128, to: 126, where: "above", distance: 1.85, kind: "band", timeframe: "1d", state: "fresh" }],
      areas_below: [{ side: "demand", from: 118, to: 118, where: "below", distance: 6.15, kind: "line", timeframe: "1w", state: "tested", context: true }],
    }));
    expect(r.areas).toEqual([
      expect.objectContaining({ low: 126, high: 128, role: "supply", style: "solid", label: "Seller area · 1d · fresh" }),
      expect.objectContaining({ low: 118, high: 118, role: "demand", style: "dashed", label: "Buyer area · 1w context · tested" }),
    ]);
  });

  it("an answer without a card draws its one nearest area; nothing at all is an empty reading", () => {
    const h = { symbol: "X", price: 100, price_label: "Last close", as_of: null, trend: null, timeframe: "Daily", chart: null,
                area: { side: "demand", from: 95, to: 97, where: "below", distance: 3, distance_pct: 3 } } as AnswerHeadline;
    expect(readingOverlay(null, h).areas).toEqual([expect.objectContaining({ low: 95, high: 97, role: "demand" })]);
    const none = readingOverlay(null, { ...h, area: null });
    expect(none).toEqual({ levels: [], areas: [], markers: [], trends: [] });
    expect(readingIsEmpty(none)).toBe(true);
    expect(readingIsEmpty(readingOverlay(card()))).toBe(false);
  });

  it("never says buy / sell / target / stop", () => {
    const text = JSON.stringify(readingOverlay(card({ areas_above: [{ side: "supply", from: 126, to: 128, where: "above", distance: 1 }] })));
    expect(text).not.toMatch(/\b(buy|sell|target|stop)\b/i);
  });

  it("accepts a handed-over reading only in its own shape", () => {
    const r = readingOverlay(card());
    expect(asAtpReading(JSON.parse(JSON.stringify(r)))).toEqual(r);
    expect(asAtpReading({ levels: [] })).toBeNull();
    expect(asAtpReading(null)).toBeNull();
  });

  it("says plainly when there is no area", () => {
    expect(noAreaText("Daily")).toBe("No area found on the daily chart yet");
    expect(noAreaText("")).toBe("No area found on this chart yet");
  });
});
