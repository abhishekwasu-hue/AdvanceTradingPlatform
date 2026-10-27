import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import type { MarketGroup, MarketsOverview } from "../markets/model";
import { WeekTilesView, weekTiles } from "./WeekTilesV2";

const g = (kind: "index" | "sector", name: string, week: number | null, symbol: string | null = null): MarketGroup => ({
  kind, name, symbol, list_key: null, exchange: "NSE", family: null, family_label: null, close: null, method: "", count: 0,
  change: { day: null, week, month: null }, vs_benchmark: { day: null, week: null, month: null }, breadth: null,
  strongest: { day: [], week: [], month: [] }, weakest: { day: [], week: [], month: [] }, known: true,
});
const overview = (indices: MarketGroup[], sectors: MarketGroup[], state: MarketsOverview["state"] = "ok"): MarketsOverview => ({
  state, message: null, as_of: null, stale: false, periods: { day: null, week: null, month: null },
  benchmark: { name: "NIFTY 50", change: { day: null, week: null, month: null } }, families: [], indices, sectors, source: "", note: "",
});

describe("TD-1: Today's week tiles", () => {
  it("NIFTY 50, BANK NIFTY, the best and the weakest sector of the week", () => {
    const t = weekTiles(overview([g("index", "NIFTY 50", 1.234, "NIFTY 50"), g("index", "NIFTY BANK", -0.5, "NIFTY BANK")],
      [g("sector", "IT", 3.1), g("sector", "Metal", -2.4), g("sector", "Auto", 0.4), g("sector", "No data", null)]));
    expect(t.map((x) => [x.key, x.value, x.caption])).toEqual([
      ["nifty", 1.234, undefined], ["bank", -0.5, undefined], ["best", 3.1, "IT"], ["worst", -2.4, "Metal"]]);
  });

  it("nothing known: no card", () => {
    expect(weekTiles(null)).toEqual([]);
    expect(weekTiles(overview([], [], "no_data"))).toEqual([]);
    expect(weekTiles(overview([g("index", "NIFTY 50", null)], []))).toEqual([]);
    expect(renderToStaticMarkup(<MemoryRouter><WeekTilesView tiles={[]} /></MemoryRouter>)).toBe("");
  });

  it("one number per tile with its sign; up / down by meaning; each tile opens the Week view", () => {
    const html = renderToStaticMarkup(<MemoryRouter><WeekTilesView tiles={[{ key: "nifty", label: "NIFTY 50", value: 1.234 }, { key: "worst", label: "Weakest sector", value: -2.4, caption: "Metal" }]} /></MemoryRouter>);
    expect(html).toContain("This week");
    // UX-11 U2: the shared Signed - colour, ▲ / ▼ and the sign.
    expect(html).toMatch(/text-up"[^>]*data-signed="up"[^>]*><span aria-hidden="true"[^>]*>▲<\/span><span>\+1\.23%</);
    expect(html).toMatch(/text-down"[^>]*data-signed="down"[^>]*><span aria-hidden="true"[^>]*>▼<\/span><span>−2\.40%</);
    expect(html).toContain('href="/markets?period=week"');
    expect(html).toContain(">Metal<");
  });
});
