import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { HeadlineRow } from "./AnswerV2";
import type { AnswerHeadline } from "./model";

const base: AnswerHeadline = {
  symbol: "NIFTY", price: 23053.1, price_label: "Last close", as_of: "2026-10-30", trend: "down", timeframe: "Daily",
  area: { side: "supply", from: 23020.95, to: 23162.7, where: "inside", distance: 0, distance_pct: 0 }, chart: null,
};

describe("DATA-FIX D1: the chart card shows the live price while the market is open", () => {
  it("market open: last close and live price with its time and change; the area line is the server's (from live)", () => {
    const html = renderToStaticMarkup(<HeadlineRow h={{ ...base, live_price: 23339.05, live_at: "2026-11-02T06:02:00+00:00",
      area: { ...base.area!, where: "below", distance: 176.35, distance_pct: 0.76 } }} />);
    expect(html).toContain("data-headline-live");
    expect(html).toContain("23,339.05");
    expect(html).toContain("23,053.10");
    expect(html).toMatch(/Live · 11:32/);
    expect(html).toMatch(/data-headline-live-change[^>]*class="[^"]*text-up|class="[^"]*text-up[^"]*"[^>]*data-headline-live-change/);
    expect(html).toContain('data-headline-area="below"');
  });
  it("market closed: no live cell, as before", () => {
    const html = renderToStaticMarkup(<HeadlineRow h={base} />);
    expect(html).not.toContain("data-headline-live");
  });
  it("the last close says its date, so an older stored close is never read as today's (owner, 7 Nov)", () => {
    const html = renderToStaticMarkup(<HeadlineRow h={base} />);
    expect(html).toMatch(/NIFTY · Last close · 30 Oct/);
  });
});
