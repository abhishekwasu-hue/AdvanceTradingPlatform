import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { lastRunText, rrgScale, type RrgResponse } from "./boardViews";
import { RrgView } from "./ResultViews";
import { sheetGroups, TemplateCard, type SheetTemplate } from "./TemplateSheet";
import taxonomySample from "./taxonomySample.json";
import type { Taxonomy } from "./taxonomy";

const rrg: RrgResponse = {
  as_of: "2026-10-12T10:00:00Z", benchmark: "Nifty 50", benchmark_key: "NSE_INDEX:NIFTY50", weeks: 10, reason: null, note: "approximation",
  symbols: [
    { symbol: "LEAD", quadrant: "Leading", tail: [{ week: "2026-W40", rs_ratio: 101, rs_momentum: 100.5 }, { week: "2026-W41", rs_ratio: 102, rs_momentum: 101 }] },
    { symbol: "LAG", quadrant: "Lagging", tail: [{ week: "2026-W41", rs_ratio: 97, rs_momentum: 98 }] },
    { symbol: "NEW", quadrant: null, tail: [] },
  ],
};

describe("RRG and the template sheet (U5 D3b)", () => {
  it("scales both axes symmetrically around 100 so the quadrants meet in the middle", () => {
    const sc = rrgScale(rrg.symbols, 300, 200);
    expect(sc.x(100)).toBeCloseTo(150, 6);
    expect(sc.y(100)).toBeCloseTo(100, 6);
    expect(sc.span).toBeCloseTo(3 * 1.1, 6);
    expect(sc.x(102)).toBeGreaterThan(sc.x(100));
    expect(sc.y(101)).toBeLessThan(sc.y(100));                                // up is higher momentum
  });

  it("draws each symbol's tail and names its quadrant, in words too", () => {
    const html = renderToStaticMarkup(<RrgView rrg={rrg} />);
    expect(html).toContain('data-quadrant="Leading"');
    expect(html).toContain("Relative rotation against Nifty 50: LEAD Leading, LAG Lagging");
    expect(html).toContain("not enough history");
    expect(html).not.toMatch(/\b(buy|sell|order)\b/i);
  });

  it("groups templates by setup and shows the last run and a live thumbnail or says it never ran", () => {
    const tax = taxonomySample as unknown as Taxonomy;
    const t: SheetTemplate = { id: "starter.momentum", name: "Momentum", definition: "RSI above 60", setup: tax.setups[0].id, source: "RSI(14) > 60", canOpen: true };
    expect(sheetGroups(tax, [t])).toEqual([{ label: tax.setups[0].label, items: [t] }]);
    const never = renderToStaticMarkup(<TemplateCard t={t} run={null} onOpen={() => undefined} />);
    expect(never).toContain("Never run in your organisation");
    expect(never).toContain("Not run yet - run it to see a preview from live data");
    const ran = renderToStaticMarkup(<TemplateCard t={{ ...t, canOpen: false }} run={{ run_id: 1, at: "2026-10-12T10:00:00Z", base_tf: "1d", scanned: 10, matched: 3, evidence_id: null }}
                                                    onOpen={() => undefined} />);
    expect(ran).toContain("Last run: 3 of 10 matched · 12 Oct, 15:30 IST");
    expect(ran).toContain("No chart stored for this run");
    expect(ran).toContain('disabled=""');
    expect(lastRunText(undefined)).toBeNull();
  });
});
