import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { MarketStrip } from "../dashboard/types";
import { summaryCards } from "./SummaryStripV2";
import { prevCloseOf, rowsAround, spotChange } from "./spotChange";
import type { ChainSummary } from "./types";

const strip = { indices: [{ key: "NIFTY 50", prev_close: 24694 }, { key: "NIFTY BANK", prev_close: 54158.65 }] } as unknown as MarketStrip;

describe("UI-28 U7.1: the chain's spot change and the strike range", () => {
  it("the previous close comes from the Today strip for NIFTY and BANK NIFTY only", () => {
    expect(prevCloseOf(strip, "nifty")).toBe(24694);
    expect(prevCloseOf(strip, "BANKNIFTY")).toBe(54158.65);
    expect(prevCloseOf(strip, "FINNIFTY")).toBeNull();
    expect(prevCloseOf(null, "NIFTY")).toBeNull();
  });
  it("the change is the spot on screen minus that close, with its %", () => {
    const c = spotChange(24812.35, 24694)!;
    expect(c.change).toBeCloseTo(118.35, 2);
    expect(c.pct).toBeCloseTo(0.479, 3);
    expect(spotChange(null, 24694)).toBeNull();
    expect(spotChange(24812, null)).toBeNull();
  });
  it("the spot card shows the signed change and %; without a close it shows none", () => {
    const s = { spot: 24812.35 } as ChainSummary;
    const html = renderToStaticMarkup(<>{summaryCards(s, null, null, 24694)[0].extra}</>);
    expect(html).toContain("data-spot-change");
    expect(html).toContain("118.35");
    expect(summaryCards(s, null, null, null)[0].extra).toBeUndefined();
  });
  it("ATM ± N keeps N listed strikes either side of the strike nearest ATM; 0 keeps every strike", () => {
    const rows = [24600, 24650, 24700, 24750, 24800, 24850, 24900].map((strike) => ({ strike }));
    expect(rowsAround(rows, 24760, 1).map((r) => r.strike)).toEqual([24700, 24750, 24800]);
    expect(rowsAround(rows, 24600, 2).map((r) => r.strike)).toEqual([24600, 24650, 24700]);
    expect(rowsAround(rows, 24760, 0)).toHaveLength(7);
    expect(rowsAround(rows, null, 5)).toHaveLength(7);
  });
});
