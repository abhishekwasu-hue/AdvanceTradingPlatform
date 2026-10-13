import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import WhatsAppSection, { META_TEMPLATE, parseHeaders } from "../alerts/WhatsAppSection";
import { etfQuery, inrShort, isEtfResponse, pct, type EtfResponse } from "./etf";
import { EtfPanel } from "./EtfPanel";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").replace(/&#x27;/g, "'");

const DATA: EtfResponse = {
  sessions: 20, total: 3, shown: 2, source: "lake:nse_bhav (closed sessions)",
  note: "Facts from the shared end-of-day history: closes, traded value and how each ETF moved against the index it tracks over the window. Not a ranking of which ETF to hold and not advice; an ETF's expense ratio and cash make a small negative tracking difference normal.",
  rows: [
    { symbol: "NIFTYBEES", name: "Nippon India ETF Nifty 50 BeES", underlying: "NIFTY 50 Index", session: "2026-10-09", close: 271.3, traded_value: 3.4e9,
      change_pct: 2.15, index_change_pct: 2.21, tracking_diff_pct: -0.06, index_key: "NSE_INDEX:NIFTY50", notes: [] },
    { symbol: "GOLDBEES", name: "Nippon India ETF Gold BeES", underlying: "Gold", session: "2026-10-09", close: 61.2, traded_value: 9.1e8,
      change_pct: 4.5, index_change_pct: null, tracking_diff_pct: null, index_key: null, notes: ["the tracked index is not in the shared history"] },
  ],
};

describe("ETF screen helpers (S10 J)", () => {
  it("formats traded value and changes, and builds the query", () => {
    expect(inrShort(3.4e9)).toBe("340.0 Cr");
    expect(inrShort(4.5e5)).toBe("4.5 L");
    expect(inrShort(null)).toBe("–");
    expect(pct(2.15)).toBe("+2.15%");
    expect(pct(-0.06)).toBe("-0.06%");
    expect(pct(null)).toBe("–");
    expect(etfQuery({ sessions: 20, q: " gold ", minCr: 1, maxTracking: null, sort: "traded_value" })).toBe("sessions=20&sort=traded_value&min_traded_value=10000000&q=gold");
    expect(etfQuery({ sessions: 5, q: "", minCr: 0, maxTracking: 0.5, sort: "symbol" })).toContain("max_abs_tracking=0.5");
    expect(isEtfResponse(DATA) && !isEtfResponse({ detail: "off" })).toBe(true);
  });

  it("renders the table with each ETF's facts and the reason a column is empty", () => {
    const html = renderToStaticMarkup(<EtfPanel initial={DATA} load={async () => DATA} />);
    const t = text(html);
    expect(t).toContain("2 of 3 ETFs");
    expect(html.indexOf('data-symbol="NIFTYBEES"')).toBeLessThan(html.indexOf('data-symbol="GOLDBEES"'));
    expect(t).toContain("340.0 Cr");
    expect(t).toContain("-0.06%");
    expect(t).toContain("the tracked index is not in the shared history");
    expect(t).not.toMatch(/\b(buy|sell|recommended|best|top pick|target)\b/i);
  });
});

describe("WhatsApp section (S10 seam)", () => {
  it("asks for the approved template and the opt-in, and parses headers", () => {
    const html = renderToStaticMarkup(<WhatsAppSection channel={undefined} busy={false} run={() => undefined} test={() => undefined} status={<span>not configured</span>} />);
    expect(text(html)).toContain("Every recipient opted in to WhatsApp messages from my organisation");
    expect(html).toContain('placeholder="Approved template name"');
    expect(META_TEMPLATE).toContain("{template}");
    expect(parseHeaders("Authorization: Bearer x\nbad line")).toEqual({ Authorization: "Bearer x" });
  });
});
