import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MarketZonesLayer, MarketZonesLegend } from "./MarketZonesLayer";
import { selectBands, type ZoneCandidate } from "./marketZones";

const cand = (key: string, low: number, high: number, extra: Partial<ZoneCandidate> = {}): ZoneCandidate => ({
  zone_key: key, zone_type: "DEMAND_ZONE", family: "DEMAND_ZONE", timeframe: "1h", kind: "band", low, high,
  low_text: low.toFixed(2), high_text: high.toFixed(2), state: "fresh", touches: 0, strength: 50, ...extra,
});

describe("MarketZonesLegend (Z7a-3)", () => {
  it("lists each band's label and touch count, top to bottom, with the snapshot time", () => {
    const bands = selectBands([cand("s", 1004, 1008, { state: "tested", touches: 2 }), cand("d", 990, 995)], 1000);
    const html = renderToStaticMarkup(<MarketZonesLegend bands={bands} asOf="2026-10-09T10:00:00Z" note="reference levels" />);
    expect(html.indexOf("supply 1004.00 (tested)")).toBeLessThan(html.indexOf("demand 995.00 (fresh)"));
    expect(html).toContain("· 2 touches");
    expect(html).toContain("· no touches");
    expect(html).toContain('data-role="supply"');
    expect(html).toContain("as of 9 Oct, 15:30 IST");
    expect(html).toContain('title="reference levels"');
    expect(html).not.toContain("market-zones-fallback");
    const fb = renderToStaticMarkup(<MarketZonesLegend bands={bands} asOf={null} settingsNote="platform default settings" />);
    expect(fb).toContain("from platform default settings");
  });

  it("renders nothing without bands (no zones stored, or the flag is off)", () => {
    expect(renderToStaticMarkup(<MarketZonesLegend bands={[]} asOf={null} />)).toBe("");
    expect(renderToStaticMarkup(<MarketZonesLayer engine={null} instrument="NSE:X" price={100} />)).toBe("");
  });
});
