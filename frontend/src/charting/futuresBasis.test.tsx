import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { BasisPanel } from "../pages/FuturesBasisWindow";
import { basisLinkFor, ContractPanel } from "../pages/ContractChartWindow";
import { basisAvailable, basisUrl, latest, linePaths, pctText, PROBE_DAYS, type BasisSeries } from "./futuresBasis";
import type { ContractSeries, SeriesLeg } from "./contracts";

const month = (expiry: (string | null)[], close: (number | null)[], oi: (number | null)[]) => ({ expiry, close, oi });
const series = (over: Partial<BasisSeries> = {}): BasisSeries => ({
  schema: "basis/1", at: "2026-09-30T18:00:00+00:00", start: "2026-09-01T00:00:00+00:00", underlying: "NIFTY", spot_key: "NSE_INDEX:NIFTY50",
  days: ["2026-09-28", "2026-09-29", "2026-09-30"], times: [1, 2, 3],
  near: month(["2026-09-29", "2026-09-29", "2026-10-27"], [101, 102, 105], [900, 800, 500]),
  next: month(["2026-10-27", "2026-10-27", "2026-11-24"], [102, 103, 106], [300, 400, 50]),
  far: month(["2026-11-24", "2026-11-24", null], [103, 104, null], [50, 50, null]),
  spot: [100, 101, 103], basis: [1, 1, 2], basis_pct: [1, 0.9901, 1.9417], carry_pct: [365, 361.3861, 26.2483], calendar: [1, 1, 1],
  total_oi: [1250, 1250, 550], rollover_pct: [28, 36, 9.0909], dte: [1, 0, 27], rolls: ["2026-09-30"], sources: ["nse_bhav"],
  note: "Futures basis, carry and rollover from the shared daily history - reference only, not signals or recommendations.", ...over,
});

describe("futures basis charts (N4 CH4c)", () => {
  it("opens an underlying's page in its own tab", () => {
    expect(basisUrl(" nifty ")).toBe("/?basis=NIFTY");
  });

  it("reads the last day from the series and formats percentages", () => {
    expect(latest(series())).toEqual({ day: "2026-09-30", near: "2026-10-27", basis: 2, basisPct: 1.9417, carryPct: 26.2483, calendar: 1,
                                        rolloverPct: 9.0909, dte: 27 });
    expect(latest(series({ days: [] }))).toBeNull();
    expect([pctText(1.23456), pctText(null), pctText(9.0909, 1)]).toEqual(["1.23 %", "n/a", "9.1 %"]);
  });

  it("breaks a line at missing values and scales it into the box", () => {
    expect(linePaths([1, 2, null, 4], 30, 10)).toEqual(["0.0,10.0 10.0,6.7", "30.0,0.0"]);
    expect(linePaths([null, null], 30, 10)).toEqual([]);
  });

  it("shows the server's numbers with the roll days and no advice or order wording", () => {
    const html = renderToStaticMarkup(<BasisPanel series={series()} />);
    expect(html).toContain("2026-09-30: near month 2026-10-27 (27 days to expiry)");
    expect(html).toContain("basis 1.94 %");
    expect(html).toContain('aria-label="Rollover (next + far OI, % of all) over 3 days, last 9.09 %"');
    expect(html).toContain("stroke-dasharray=\"2 3\"");
    expect(html).toContain("Spot: NSE_INDEX:NIFTY50");
    expect(html).not.toMatch(/\b(buy|sell|order|recommend)\b/i);
  });

  it("links a single futures contract's chart to the basis page only when given the link", () => {
    const leg = (over: Partial<SeriesLeg>): SeriesLeg => ({ key: "NFO:NIFTY:FUT:2026-10-27", qty: 1, underlying: "NIFTY", kind: "FUT", expiry: "2026-10-27",
                                                           strike: null, bars: 1, oi: [1], close: [1], sources: ["nse_bhav"], ...over });
    const cs = (legs: SeriesLeg[]): ContractSeries => ({ schema: "contract/1", at: "", start: "", days: ["2026-09-30"], times: [1], legs, note: "n", spread: [1] });
    expect(basisLinkFor(cs([leg({})]))).toBe("/?basis=NIFTY");
    expect(basisLinkFor(cs([leg({ kind: "CE", strike: 25000 })]))).toBeNull();
    expect(basisLinkFor(cs([leg({}), leg({ key: "x", qty: -1 })]))).toBeNull();
    expect(renderToStaticMarkup(<ContractPanel series={cs([leg({})])} basisLink="/?basis=NIFTY" />)).toContain('data-testid="basis-link"');
    expect(renderToStaticMarkup(<ContractPanel series={cs([leg({})])} />)).not.toContain("basis-link");
  });

  it("probes availability with a short window, not the page's full year", async () => {
    const now = new Date("2026-10-13T06:00:00.000Z");
    const respond = (status: number) => vi.fn(async () => new Response(status === 200 ? JSON.stringify(series()) : JSON.stringify({ detail: "off" }),
                                                                    { status, headers: { "content-type": "application/json" } }));
    try {
      const ok = respond(200);
      vi.stubGlobal("fetch", ok);
      expect(await basisAvailable(" nifty ", now)).toBe(true);
      const url = decodeURIComponent(String((ok.mock.calls as unknown as [string][])[0][0]));
      const start = new Date(now.getTime() - PROBE_DAYS * 86_400_000).toISOString();
      expect(url).toContain(`/charts/contracts/basis?underlying=NIFTY&start=${start}`);
      expect(PROBE_DAYS).toBeLessThanOrEqual(7);
      vi.stubGlobal("fetch", respond(503));
      expect(await basisAvailable("NIFTY", now)).toBe(false);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
