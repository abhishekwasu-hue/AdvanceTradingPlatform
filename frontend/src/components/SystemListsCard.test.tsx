import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SystemListsView, when, type SystemListsStatus } from "./SystemListsCard";

const status = (over: Partial<SystemListsStatus> = {}): SystemListsStatus => ({
  last_refresh: {
    id: 3, started_at: "2026-10-21T13:00:00+00:00", finished_at: "2026-10-21T13:00:05+00:00", lists_total: 4, lists_changed: 2,
    changes: [
      { key: "nifty-50", name: "NIFTY 50 stocks (50)", kind: "changed", added: ["AAA"], removed: ["BBB"] },
      { key: "sector-it", name: "IT", kind: "held", added: [], removed: [], reason: "kept as it was" },
      { key: "big", name: "Big", kind: "created", added: Array.from({ length: 20 }, (_, i) => `S${i}`), removed: [] },
    ],
    unmapped_count: 1, note: null, unmapped: [{ list: "Indices", symbol: "NIFTY XYZ", reason: "no index row in the instrument master" }],
  },
  history: [], lists: 4, groups: { INDICES: 1, INDEX_STOCKS: 2, SECTORS: 1, FNO: 0 }, universe_sync_enabled: false, refresh_hour_ist: 18,
  ...over,
});

describe("SystemListsView (admin)", () => {
  it("shows the last refresh, the changes with added / removed symbols, and the unmapped symbols", () => {
    const html = renderToStaticMarkup(<SystemListsView status={status()} onRefresh={() => {}} />);
    expect(html).toContain("Last refresh:");
    expect(html).toContain("Index stocks 2");
    expect(html).toContain("Added: AAA");
    expect(html).toContain("Removed: BBB");
    expect(html).toContain("kept as it was");
    expect(html).toContain("+8 more");
    expect(html).toContain("NIFTY XYZ");
    expect(html).toContain("Unmapped symbols (1)");
    expect(html).toContain("Refresh now");
    expect(html).toContain("universe sync is off");
  });

  it("handles no refresh yet", () => {
    const html = renderToStaticMarkup(<SystemListsView status={status({ last_refresh: null, lists: 0, groups: {} })} />);
    expect(html).toContain("never");
    expect(html).toContain("No list changed.");
    expect(html).toContain("Every symbol mapped");
    expect(html).not.toContain("Refresh now");
  });

  it("formats times and passes bad input through", () => {
    expect(when(null)).toBe("never");
    expect(when("nope")).toBe("nope");
  });
});
