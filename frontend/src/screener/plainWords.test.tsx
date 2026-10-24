import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { TIMEFRAMES, TIMEFRAME_WORDS, timeframeWords } from "./model";
import { PlainTimeframes, TimeframeChip } from "./TimeframeChip";

describe("day-one fix 4: Find setups in a trader's words (ui_v2)", () => {
  it("every timeframe has words; codes only off v2", () => {
    for (const tf of TIMEFRAMES) expect(TIMEFRAME_WORDS[tf]).toBeTruthy();
    expect(timeframeWords("1d", true)).toBe("Daily");
    expect(timeframeWords("15m", true)).toBe("15 min");
    expect(timeframeWords("1d", false)).toBe("1d");
  });

  it("the stage chip reads 'Daily' on v2 and '1d' before", () => {
    const v2 = renderToStaticMarkup(<PlainTimeframes.Provider value><TimeframeChip value={null} scanTf="1d" onChange={() => undefined} /></PlainTimeframes.Provider>);
    expect(v2).toContain(">Daily</span>");
    expect(v2).not.toMatch(/>1d</);
    const old = renderToStaticMarkup(<TimeframeChip value={null} scanTf="1d" onChange={() => undefined} />);
    expect(old).toContain(">1d</span>");
  });

  it("the page hides the developer tools behind Advanced and leads with ready scans and matches", () => {
    const page = readFileSync("src/pages/ScreenerPage.tsx", "utf8");
    expect(page).toContain("{advanced && pineOn &&");
    expect(page).toContain("formula={builderU2 && advanced}");
    expect(page).toContain('data-testid="ready-scans"');
    expect(page).toContain("const longAnswer = universeV2 && (run?.results.length ?? 0) > 0;");
    expect(page).toContain('placeholder={universeV2 ? "Untitled scan" : undefined}');
  });
});
