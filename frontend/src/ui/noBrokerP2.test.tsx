import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { indexCaption } from "../dashboard/model";
import type { StripIndex } from "../dashboard/types";
import { eodRowsAround } from "../optionChain/EodChainV2";
import { ConnectLiveButton, NoBrokerLine } from "./ConnectLive";
import { CONNECT_LIVE } from "./lastClose";

/** M86 D2 / D3: the strip's last close and the one in-place connect button. */
const index = (over: Partial<StripIndex>): StripIndex => ({ key: "NIFTY 50", label: "NIFTY 50", symbol: "NIFTY 50", exchange: "NSE", ltp: 24310.5,
  prev_close: 24255.2, change: 55.3, change_pct: 0.23, as_of: "2026-11-06", stale: false, source: "eod", missing: false, ...over });

describe("M86 strip and chain", () => {
  it("an index from NSE's end-of-day file says 'Last close · <date>'", () => {
    expect(indexCaption(index({}), Date.parse("2026-11-08T05:00:00Z"))).toBe("Last close · 6 Nov");
    expect(indexCaption(index({ as_of: null }), Date.now())).toBe("Last close");
    expect(indexCaption(index({ source: "live", as_of: "2026-11-08T05:00:00Z" }), Date.now())).toBeNull();
    expect(indexCaption(index({ ltp: null, missing: true, source: null }), Date.now())).toBe("No price yet");
  });

  it("the end-of-day chain shows the strikes around the spot, all of them without one", () => {
    const rows = Array.from({ length: 41 }, (_, i) => ({ strike: 24000 + i * 50, call: null, put: null }));
    const around = eodRowsAround(rows, 25012, 2);
    expect(around.map((r) => r.strike)).toEqual([24900, 24950, 25000, 25050, 25100]);
    expect(eodRowsAround(rows, null, 2)).toHaveLength(41);
    expect(eodRowsAround(rows.slice(0, 3), 24000, 2)).toHaveLength(3);
  });
});

describe("M86 D3: the one connect button", () => {
  it("says the one sentence and is a button (it opens the broker sheet in place, no navigation)", () => {
    const html = renderToStaticMarkup(<MemoryRouter><ConnectLiveButton /></MemoryRouter>);
    expect(html).toContain(CONNECT_LIVE);
    expect(html).toContain("<button");
    expect(html).not.toContain("href=");
  });

  it("a line carries it once, or not at all where the page already has it", () => {
    const one = renderToStaticMarkup(<MemoryRouter><NoBrokerLine>Last close · 6 Nov</NoBrokerLine></MemoryRouter>);
    expect(one.split(CONNECT_LIVE)).toHaveLength(2);
    const none = renderToStaticMarkup(<MemoryRouter><NoBrokerLine button={false}>Last close · 6 Nov</NoBrokerLine></MemoryRouter>);
    expect(none).not.toContain(CONNECT_LIVE);
  });
});
