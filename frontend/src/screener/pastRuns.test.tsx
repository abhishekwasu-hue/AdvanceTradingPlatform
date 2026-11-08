import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../components/Toast";
import type { RunResult } from "./api";
import PastRunsPanel from "./PastRunsPanel";
import { SaveMatchesButton } from "./SaveMatchesButton";
import { defaultListName, isPastRuns, matchedSymbols, nameProblem, runSummary, runTitle, watchItems, type PastRun } from "./pastRuns";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const r = (symbol: string, matched: boolean, reason: string | null = null): RunResult => ({ symbol, matched, reason });
const RUN: PastRun = { run_id: 7, screen_id: null, screen_name: null, base_tf: "1d", scanned: 50, matched: 7,
  first_matches: ["A", "B", "C", "D", "E"], created_at: "2026-01-05T04:00:00+00:00" };

describe("FN2-g save matches as a watchlist", () => {
  it("takes only the matched symbols, each once, upper-case, in the board's order", () => {
    expect(matchedSymbols([r("tcs", true), r("INFY", false), r("SBIN", false, "The bars could not be read."), r("TCS", true), r("NIFTY 50", true)]))
      .toEqual(["TCS", "NIFTY 50"]);
    expect(matchedSymbols(null)).toEqual([]);
    expect(matchedSymbols(Array.from({ length: 600 }, (_, i) => r(`S${i}`, true)))).toHaveLength(500);
    expect(watchItems(["TCS", "NIFTY 50"])).toEqual([{ symbol: "TCS", exchange: "NSE" }, { symbol: "NIFTY 50", exchange: "NSE" }]);
  });

  it("starts the name from the scan's name, within the server's rules", () => {
    expect(defaultListName("Trend up")).toBe("Trend up matches");
    expect(defaultListName("  ")).toBe("Scan matches");
    expect(defaultListName("<b>x</b>")).toBe("bx/b matches");
    expect(defaultListName("x".repeat(80))).toHaveLength(60);
    expect(nameProblem(defaultListName("x".repeat(80)))).toBeNull();
    expect(nameProblem(" ")).toBe("Give the list a name.");
    expect(nameProblem("a<b")).toBe("Leave out < and >.");
    expect(nameProblem("y".repeat(61))).toBe("Use at most 60 characters.");
  });

  it("shows the button only when something matched, with no developer text", () => {
    const create = vi.fn();
    const none = renderToStaticMarkup(<ToastProvider><SaveMatchesButton results={[r("INFY", false)]} scanName="x" create={create} /></ToastProvider>);
    expect(none).not.toContain("save-matches-open");
    const some = renderToStaticMarkup(<ToastProvider><SaveMatchesButton results={[r("TCS", true)]} scanName="x" create={create} /></ToastProvider>);
    expect(text(some)).toContain("Save matches as a watchlist");
    expect(some).toContain("h-11");                                      // a finger-sized target on a phone
    expect(create).not.toHaveBeenCalled();
  });
});

describe("FN2-i past runs", () => {
  it("checks the server's shape", () => {
    expect(isPastRuns({ runs: [RUN], disclaimer: "d" })).toBe(true);
    expect(isPastRuns({ runs: [] })).toBe(true);
    expect(isPastRuns({ detail: "Not found" })).toBe(false);
    expect(isPastRuns({ runs: [{ run_id: "7" }] })).toBe(false);
  });

  it("says each run in words: its scan, timeframe and the first matches", () => {
    expect(runTitle(RUN, (tf) => (tf === "1d" ? "Daily" : tf))).toBe("Scan · Daily");
    expect(runTitle({ ...RUN, screen_name: "Trend up" })).toBe("Trend up · 1d");
    expect(runSummary(RUN)).toBe("7 of 50 matched: A, B, C, D, E and 2 more");
    expect(runSummary({ ...RUN, matched: 2, first_matches: ["A", "B"] })).toBe("2 of 50 matched: A, B");
    expect(runSummary({ ...RUN, matched: 0, first_matches: [] })).toBe("0 of 50 matched");
  });

  it("loads before it lists (no request text, ids or status codes on screen)", () => {
    const api = { list: vi.fn(() => new Promise<never>(() => undefined)), get: vi.fn() };
    const html = renderToStaticMarkup(<PastRunsPanel api={api} />);
    expect(text(html)).not.toMatch(/run_id|screener\/runs|404/);
    expect(api.get).not.toHaveBeenCalled();
  });
});
