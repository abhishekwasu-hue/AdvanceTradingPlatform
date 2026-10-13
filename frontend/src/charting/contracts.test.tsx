import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ContractPanel } from "../pages/ContractChartWindow";
import { chainLinks, contractChartUrl, contractKey, ivText, legName, strikeText, toCandles, type ContractSeries, type SeriesLeg } from "./contracts";

const leg = (over: Partial<SeriesLeg> = {}): SeriesLeg => ({
  key: "NFO:NIFTY:CE:2026-10-27:25000", qty: 1, underlying: "NIFTY", kind: "CE", expiry: "2026-10-27", strike: 25000, bars: 2,
  oi: [1000, 1200], close: [200, 210], sources: ["nse_bhav"], ...over,
});
const series = (over: Partial<ContractSeries> = {}): ContractSeries => ({
  schema: "contract/1", at: "2026-09-03T10:00:00+00:00", start: "2026-08-01T00:00:00+00:00", days: ["2026-09-01", "2026-09-02"],
  times: [Date.parse("2026-09-01T03:45:00Z") / 1000, Date.parse("2026-09-02T03:45:00Z") / 1000], legs: [leg()],
  note: "Daily contract bars from the shared market-data history - reference only.", spread: [200, 210], ...over,
});

describe("contract charts (N4 CH4b)", () => {
  it("builds the shared lake's keys exactly as the backend does", () => {
    expect([strikeText(25000), strikeText(112.5), strikeText(1050.25)]).toEqual(["25000", "112.5", "1050.25"]);
    expect(contractKey(" nifty ", "CE", "2026-10-27", 25000)).toBe("NFO:NIFTY:CE:2026-10-27:25000");
    expect(contractKey("NIFTY", "FUT", "2026-10-27")).toBe("NFO:NIFTY:FUT:2026-10-27");
    expect(contractChartUrl(["NFO:NIFTY:CE:2026-10-27:25000", "-2*NFO:NIFTY:CE:2026-10-27:25500"]))
      .toBe("/?contract=NFO%3ANIFTY%3ACE%3A2026-10-27%3A25000&contract=-2*NFO%3ANIFTY%3ACE%3A2026-10-27%3A25500");
  });

  it("links a live chain's strikes to their charts, and never a sample chain's", () => {
    expect(chainLinks({ underlying: "NIFTY", expiry: "2026-10-27" }, 25000)?.pe).toBe(contractChartUrl(["NFO:NIFTY:PE:2026-10-27:25000"]));
    expect(chainLinks({ underlying: "NIFTY", expiry: "sample" }, 25000)).toBeNull();
    expect(chainLinks(null, 25000)).toBeNull();
  });

  it("draws one contract's own OHLC, and a spread's close only", () => {
    const one = toCandles(series({ ohlc: { o: [198, 205], h: [204, 214], l: [196, 203], c: [200, 210], v: [5, 7] } }));
    expect(one[1]).toEqual({ timestamp: "2026-09-02T03:45:00.000Z", open: 205, high: 214, low: 203, close: 210, volume: 7 });
    const spread = toCandles(series({ legs: [leg(), leg({ key: "x", qty: -2, kind: "PE", strike: 24500 })], spread: [-100, -86] }));
    expect(spread[0]).toEqual({ timestamp: "2026-09-01T03:45:00.000Z", open: -100, high: -100, low: -100, close: -100, volume: 0 });
    expect(legName(leg({ qty: -2 }))).toBe("-2 x NIFTY 27 Oct 25000 CE");
    expect(legName(leg({ kind: "FUT", strike: null }))).toBe("NIFTY 27 Oct FUT");
  });

  it("labels the IV as computed and shows each leg's OI from the series", () => {
    const html = renderToStaticMarkup(<ContractPanel series={series({ legs: [leg({ iv: [0.18, 0.1834] })],
      iv_note: "Computed IV: Black-76 from the day's close and the same-expiry future's close (risk-free rate 7.00%). Not a quoted IV." })} />);
    expect(html).toContain("IV (computed) 18.3 %");
    expect(html).toContain("Not a quoted IV.");
    expect(html).toContain('aria-label="Open interest of NIFTY 27 Oct 25000 CE over 2 days, last 1200"');
    expect(html).not.toMatch(/\b(buy|sell|order)\b/i);
    expect(ivText(null)).toBe("n/a");
  });
});
