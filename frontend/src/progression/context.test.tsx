import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ContextPanel } from "../types";
import { ContextPanelView } from "./ContextPanelCard";
import { tileLines } from "./context";
import { tProgression as t } from "./i18n";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&lt;/g, "<").replace(/&amp;/g, "&").replace(/\s+/g, " ");

// Shaped as tests/test_n2_context_panel.py's answer.
const PANEL: ContextPanel = {
  order: ["regime", "breadth", "vix", "fii_dii", "global", "events"], as_of: "2026-10-12T05:00:00+00:00",
  note: "Context only: what the data shows now, from the sources named on each tile.",
  tiles: {
    regime: { status: "ok", source: "NSE index closes (daily lake)", as_of: "2026-10-09", market: "NIFTY 50", close: 25010.5,
              daily: { trend: "UPTREND", swings: [{ kind: "LOW", label: "HL", price: 24800, date: "2026-10-03" }, { kind: "HIGH", label: "HH", price: 25100, date: "2026-10-08" }] },
              weekly: { trend: "RANGE", swings: [] } },
    breadth: { status: "stale", source: "NSE bhavcopy, cash market (daily lake)", as_of: "2026-10-06", advances: 1200, declines: 800, unchanged: 50, counted: 2050, ratio: 1.5 },
    vix: { status: "ok", source: "India VIX daily closes (daily lake)", as_of: "2026-10-09", close: 13.2, change_pct: -2.5, rank: 22.4, percentile: 30.1, sessions: 252,
           live: { last: 13.6, change_pct: 1.1 } },
    fii_dii: { status: "na", source: "FII / DII provider seam (FII_DII_SOURCE)", as_of: null, reason: "No FII / DII source with clear terms of use is configured yet" },
    global: { status: "ok", source: "free public data", as_of: "2026-10-12T04:00:00+00:00", missing: 2,
              markets: [{ name: "S&P 500 futures", last: 5800.25, change_pct: 0.4 }, { name: "Brent crude", last: 82.1, change_pct: -1.2 }] },
    events: { status: "ok", source: "Market events calendar", as_of: "2026-10-12",
              today: [{ date: "2026-10-12", start_time: "10:00", end_time: null, kind: "RBI_POLICY", scope: "*", action: "SIZE_CUT", description: "Policy", today: true, own: false }],
              this_week: [{ date: "2026-10-16", start_time: null, end_time: null, kind: "RESULTS", scope: "RELIANCE", action: "BLOCK", description: "Results", today: false, own: true }] },
  },
};

describe("context helpers (N2-5)", () => {
  it("writes each tile's lines from the server's values", () => {
    expect(tileLines("regime", PANEL.tiles.regime, t)).toEqual(["NIFTY 50 25,010.5: daily uptrend, weekly range", "Last swings: HL 24,800 (2026-10-03), HH 25,100 (2026-10-08)"]);
    expect(tileLines("breadth", PANEL.tiles.breadth, t)).toEqual(["1200 advanced · 800 declined · 50 unchanged of 2050 shares", "Advance / decline ratio 1.50"]);
    expect(tileLines("vix", PANEL.tiles.vix, t)).toEqual(["13.20 (−2.50% on the day)", "1-year rank 22.4 · percentile 30.1 (252 sessions)", "Live reading 13.60 (+1.10%)"]);
    expect(tileLines("fii_dii", PANEL.tiles.fii_dii, t)).toEqual([]);
    expect(tileLines("global", PANEL.tiles.global, t)).toEqual(["S&P 500 futures: 5,800.25 (+0.40%)", "Brent crude: 82.1 (−1.20%)", "2 markets did not answer"]);
    expect(tileLines("events", PANEL.tiles.events, t)).toEqual(["Today:", "2026-10-12 10:00 RBI_POLICY (all): Policy", "Rest of this week:",
                                                              "2026-10-16  RESULTS (RELIANCE): Results · your organisation"]);
    expect(tileLines("events", { ...PANEL.tiles.events, today: [], this_week: [] }, t)).toEqual(["No market event in the calendar for the rest of this week."]);
  });
});

describe("ContextPanelView (N2-5)", () => {
  it("shows every tile with its source and time, the stale mark and the NA reason", () => {
    const html = renderToStaticMarkup(<ContextPanelView panel={PANEL} busy={false} onRefresh={() => undefined} />);
    const words = text(html);
    for (const title of ["Regime (Dow structure)", "Breadth", "India VIX", "FII / DII flows", "Global cues", "Events"]) expect(words).toContain(title);
    expect(html).toContain('data-testid="context-breadth" data-status="stale"');
    expect(words).toContain("older than expected");
    expect(words).toContain("Not available: No FII / DII source with clear terms of use is configured yet");
    expect(words).toContain("Source: NSE index closes (daily lake)");
    expect(words).toContain("as of 2026-10-12 04:00");
    expect(html.match(/<button/g)?.length).toBe(1);                 // only Refresh
  });
});
