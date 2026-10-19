import { describe, expect, it } from "vitest";
import type { MultiView, MultiViewAnalysis, MultiViewSummary } from "../../../types";
import { cleanSymbol, factLabel, factRows, headlines, sections, totalUsage } from "./multiview";

const view = (over: Partial<MultiView>): MultiView => ({
  id: "technical", label: "Technical", ok: true, facts: {}, as_of: null, sources: [], untrusted: false, error: null, report: "", report_source: "rules", ...over,
});

describe("H-C4 multi-view helpers", () => {
  it("lists the non-empty summary sections in reading order", () => {
    const summary: MultiViewSummary = { situation: "s", leans_up: ["a"], leans_down: [], risks: ["r", " "], possibilities: ["p"], claims: [], source: "rules" };
    expect(sections(summary)).toEqual([{ key: "leans_up", items: ["a"] }, { key: "risks", items: ["r"] }, { key: "possibilities", items: ["p"] }]);
  });

  it("shows scalar facts with readable labels and skips lists, objects and blanks", () => {
    const rows = factRows(view({ facts: { pcr: 1.25, regime: "TRENDING_UP", top_call_oi: [{ strike: 1 }], sentiment: { a: 1 }, low: null, change_pct: 2.5 } }));
    expect(rows).toEqual([["PCR", "1.25"], ["Regime", "TRENDING_UP"], ["Change %", "2.5"]]);
    expect(factLabel("india_vix")).toBe("India VIX");
  });

  it("reads headlines only from the news view, as plain fields", () => {
    const news = view({ id: "news", untrusted: true, facts: { headlines: [{ headline: "<b>X</b> results", source: "feed", severity: 4, verified: false }, { nope: 1 }] } });
    expect(headlines(news)).toEqual([{ headline: "<b>X</b> results", source: "feed", published_at: null, severity: 4, verified: false }]);
    expect(headlines(view({ facts: { headlines: [{ headline: "x" }] } }))).toEqual([]);
  });

  it("adds both tiers' usage, or none when no model ran", () => {
    const base = { usage: { cheap: { calls: 4, tokens_input: 100, tokens_output: 50, cost_inr: 0.1 }, strong: { calls: 1, tokens_input: 300, tokens_output: 100, cost_inr: 0.5 } } };
    expect(totalUsage(base as unknown as MultiViewAnalysis)).toEqual({ tokens: 550, inr: 0.6 });
    expect(totalUsage({ usage: { cheap: null, strong: null } } as unknown as MultiViewAnalysis)).toBeNull();
  });

  it("accepts only symbols the route accepts", () => {
    expect(cleanSymbol(" nifty 50 ")).toBe("NIFTY 50");
    expect(cleanSymbol("M&M")).toBe("M&M");
    expect(cleanSymbol("<script>")).toBeNull();
    expect(cleanSymbol("")).toBeNull();
  });
});
