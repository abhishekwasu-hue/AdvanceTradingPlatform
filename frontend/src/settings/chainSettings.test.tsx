import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { formatDateTimeIST } from "../ui/format";
import ChainSettingsV2, { PCR_DEFAULTS, pcrFormOf, pcrOf, readPcrZones } from "./chainSettings";

/** B4 (UI-H bug lane): his own PCR zone edges, defaults = the values used before. */
describe("Option chain settings: PCR zones (B4)", () => {
  it("defaults equal the values the page used before (0.70 / 0.90 / 1.00 / 1.30)", () => {
    expect(PCR_DEFAULTS).toEqual({ very_low_below: 0.7, low_below: 0.9, balanced_max: 1.0, high_max: 1.3 });
    expect(readPcrZones(undefined)).toEqual(PCR_DEFAULTS);
    expect(readPcrZones({ pcr_zones: { very_low_below: 0.5, low_below: "x" } })).toEqual({ ...PCR_DEFAULTS, very_low_below: 0.5 });
    expect(pcrFormOf(PCR_DEFAULTS)).toEqual({ very_low_below: "0.70", low_below: "0.90", balanced_max: "1.00", high_max: "1.30" });
  });

  it("saves ascending edges above 0 and says why otherwise", () => {
    expect(pcrOf({ very_low_below: "0.5", low_below: "0.8", balanced_max: "1.2", high_max: "1.5" }))
      .toEqual({ value: { very_low_below: 0.5, low_below: 0.8, balanced_max: 1.2, high_max: 1.5 }, problem: null });
    expect(pcrOf({ very_low_below: "0.9", low_below: "0.8", balanced_max: "1.2", high_max: "1.5" }).problem).toBe("The PCR edges must go up from very low to high.");
    expect(pcrOf({ very_low_below: "0", low_below: "0.8", balanced_max: "1.2", high_max: "1.5" }).problem).toMatch(/above 0/);
    expect(pcrOf({ very_low_below: "", low_below: "0.8", balanced_max: "1.2", high_max: "1.5" }).problem).toMatch(/above 0/);
  });

  it("renders nothing until his settings are read (no flash of wrong values)", () => {
    expect(renderToStaticMarkup(<ChainSettingsV2 fetcher={() => new Promise(() => undefined)} />)).toBe("");
  });
});

/** Orders time (UI-H bug lane): one en-IN / IST formatter (src/ui/format.ts) for every date on the user pages. */
describe("dates and times: one IST formatter", () => {
  const SOURCES = import.meta.glob<string>(["../**/*.ts", "../**/*.tsx", "!../**/*.test.ts", "!../**/*.test.tsx", "!../stories/**"],
    { query: "?raw", import: "default", eager: true });

  it("the Orders page shows the time in IST, never in the device's zone and locale", () => {
    const orders = SOURCES["../pages/OrdersPage.tsx"];
    expect(orders).toContain("formatDateTimeIST(o.time)");
    expect(formatDateTimeIST("2026-10-23T04:05:00Z")).toBe("23 Oct 2026, 09:35 IST");
  });

  it("no page formats a date with the browser's own locale and zone", () => {
    expect(Object.keys(SOURCES).length).toBeGreaterThan(100);
    for (const [file, src] of Object.entries(SOURCES)) {
      expect(src, `${file}: use formatDateTimeIST / formatDateIST / formatTimeIST (src/ui/format.ts)`)
        .not.toMatch(/new Date\([^;]*?\)\.toLocale(?:Date|Time)?String\(\)/);
    }
  });
});
