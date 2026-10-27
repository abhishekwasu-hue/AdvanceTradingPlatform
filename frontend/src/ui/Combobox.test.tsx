import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Combobox, RECENT_GROUP, comboRows, stepRow, typeAhead, type ComboOption } from "./Combobox";

const OPTS: ComboOption[] = [
  { value: "NIFTY", label: "NIFTY", group: "Indices" }, { value: "BANKNIFTY", label: "BANKNIFTY", group: "Indices" },
  { value: "RELIANCE", label: "RELIANCE", group: "Stocks" }, { value: "INFY", label: "INFY", group: "Stocks", disabled: true },
  { value: "TCS", label: "TCS", group: "Stocks" },
];

describe("UX-5: the shared combobox", () => {
  it("filters by label or value, matches that start with the text first; recent values first when not filtering", () => {
    expect(comboRows(OPTS, "nifty").map((o) => o.value)).toEqual(["NIFTY", "BANKNIFTY"]);
    expect(comboRows(OPTS, "  ").map((o) => o.value)).toEqual(OPTS.map((o) => o.value));
    const withRecent = comboRows(OPTS, "", ["TCS", "GONE"]);                        // a recent value no longer offered is dropped
    expect(withRecent[0]).toMatchObject({ value: "TCS", group: RECENT_GROUP });
    expect(withRecent).toHaveLength(OPTS.length + 1);
  });

  it("arrows skip disabled rows and wrap; type-ahead finds the next match", () => {
    expect(stepRow(OPTS, 2, 1)).toBe(4);                                              // INFY is disabled
    expect(stepRow(OPTS, 4, 1)).toBe(0);
    expect(stepRow(OPTS, 0, -1)).toBe(4);
    expect(stepRow([{ value: "a", label: "a", disabled: true }], -1, 1)).toBe(-1);
    expect(typeAhead(OPTS, "b", 0)).toBe(1);
    expect(typeAhead(OPTS, "i", 0)).toBe(-1);                                         // only a disabled row starts with "i"
    expect(typeAhead(OPTS, "n", 0)).toBe(0);                                          // wraps to the top
  });

  it("closed: a button with the combobox role, the current label, no list; a placeholder without a value", () => {
    const html = renderToStaticMarkup(<Combobox label="Symbol" value="RELIANCE" options={OPTS} onChange={() => {}} dataAttrs={{ "data-terminal": "underlying" }} />);
    expect(html).toMatch(/<button[^>]*role="combobox"[^>]*aria-haspopup="listbox"[^>]*aria-expanded="false"/);
    expect(html).toContain('aria-label="Symbol"');
    expect(html).toContain('data-combobox="RELIANCE"');
    expect(html).toContain('data-terminal="underlying"');
    expect(html).toContain(">RELIANCE<");
    expect(html).not.toContain("data-combobox-list");
    expect(renderToStaticMarkup(<Combobox label="Expiry" value="" options={[]} onChange={() => {}} placeholder="Nearest" disabled />)).toMatch(/disabled=""[^>]*>.*Nearest/);
  });

  it("the v2 pages of the order use the combobox, not the browser's <select>", () => {
    for (const f of ["src/chart/ChartsPageV2.tsx", "src/chart/MultiChartV2.tsx", "src/optionChain/OptionChainPageV2.tsx", "src/optionChain/ChartsViewV2.tsx",
      "src/terminal/TerminalPageV2.tsx"]) {
      expect(readFileSync(f, "utf8"), f).not.toMatch(/<select[\s>]/);
    }
    for (const f of ["src/pages/AnalyticsPage.tsx", "src/pages/NewsEventsPage.tsx"]) {                // shared with today's UI
      expect(readFileSync(f, "utf8"), f).not.toMatch(/<select[\s>]/);
      expect(readFileSync(f, "utf8"), f).toContain("<SelectOrCombobox");
    }
  });
});
