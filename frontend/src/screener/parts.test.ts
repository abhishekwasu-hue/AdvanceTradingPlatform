import { describe, expect, it } from "vitest";
import { freshness, formatIst } from "./FreshnessPill";
import { parseInline } from "./ParamInline";
import { sortResults } from "./ResultBoard";
import { trailFraction } from "./SurvivorTrail";

describe("inline number editing", () => {
  it("accepts a clean number and drops anything half-typed or out of range (never half-applied)", () => {
    expect(parseInline(" 14 ", true)).toBe(14);
    expect(parseInline("2.5", false)).toBe(2.5);
    expect(parseInline("-3", false)).toBe(-3);
    for (const bad of ["", "-", "1e3", "abc", "1.5.2", "2.5"]) expect(parseInline(bad, true)).toBeNull();
    expect(parseInline("0", true, 1)).toBeNull();
  });
});

describe("result table order", () => {
  const rows = [
    { symbol: "TCS", matched: false, reason: null },
    { symbol: "INFY", matched: true, reason: null },
    { symbol: "ABB", matched: false, reason: "no bars from the broker" },
    { symbol: "HDFC", matched: true, reason: null },
  ];
  it("puts matched first, then not matched, then symbols that could not be read - each by symbol", () => {
    expect(sortResults(rows, { key: "result", dir: "asc" }).map((r) => r.symbol)).toEqual(["HDFC", "INFY", "TCS", "ABB"]);
    expect(sortResults(rows, { key: "symbol", dir: "desc" }).map((r) => r.symbol)).toEqual(["TCS", "INFY", "HDFC", "ABB"]);
  });
});

describe("freshness", () => {
  const ran = new Date("2026-03-02T04:00:00Z");
  it("is never stale before a run and turns stale after two of the scan's bars", () => {
    expect(freshness(null, ran, "5m")).toEqual({ behindMin: null, stale: false });
    expect(freshness(ran, new Date(ran.getTime() + 9 * 60e3), "5m")).toEqual({ behindMin: 9, stale: false });
    expect(freshness(ran, new Date(ran.getTime() + 14 * 60e3), "5m")).toEqual({ behindMin: 14, stale: true });
    expect(freshness(ran, new Date(ran.getTime() + 14 * 60e3), "1d").stale).toBe(false);
  });
  it("shows the time in IST", () => {
    expect(formatIst(ran)).toBe("09:30");
  });
});

describe("the trail bar", () => {
  it("is the share of the universe still in, clamped, and empty without a count", () => {
    expect(trailFraction(5, 20)).toBe(0.25);
    expect(trailFraction(null, 20)).toBe(0);
    expect(trailFraction(3, 0)).toBe(0);
    expect(trailFraction(30, 20)).toBe(1);
  });
});
