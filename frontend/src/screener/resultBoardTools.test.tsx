import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ResultBoard, resultsCsv, type WhyChip } from "./ResultBoard";
import type { RunResult } from "./api";

const ROWS: RunResult[] = [
  { symbol: "RELIANCE", matched: true, reason: null },
  { symbol: "TCS", matched: false, reason: null },
  { symbol: "INFY", matched: false, reason: "No candles, \"stale\"" },
];
const why = (symbol: string): WhyChip[] => [{ id: "a", summary: "Close > EMA", passed: symbol !== "TCS" }, { id: "b", summary: "RSI", passed: symbol === "RELIANCE" }];

describe("UI-28 U5.3: the results board tools", () => {
  it("CSV: one row per symbol in the board's order, one column per stage, quoted where needed", () => {
    expect(resultsCsv(ROWS, why).split("\n")).toEqual([
      "symbol,result,reason,stage_1,stage_2",
      "RELIANCE,matched,,passed,passed",
      "TCS,not matched,,not passed,not passed",
      "INFY,not read,\"No candles, \"\"stale\"\"\",passed,not passed",
      "",
    ]);
    expect(resultsCsv(ROWS).split("\n")[0]).toBe("symbol,result,reason");
  });

  it("the header carries Auto-refresh 1m, the Stages column switch and Export (CSV) once there are results", () => {
    const html = renderToStaticMarkup(<ResultBoard results={ROWS} why={why} autoRefresh={{ on: false, onChange: () => {} }} />);
    expect(html).toContain('data-testid="results-auto-refresh"');
    expect(html).toContain("Auto-refresh 1m");
    expect(html).toContain('data-testid="results-stages-column"');
    expect(html).toContain('data-testid="results-csv"');
    const empty = renderToStaticMarkup(<ResultBoard results={null} />);
    expect(empty).not.toContain('data-testid="results-csv"');
    expect(empty).not.toContain("Auto-refresh");
  });
});
