import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { settingRows } from "../strategyTemplates/link";
import { IDEA_SYMBOL, SYMBOLS, startSummary } from "./model";

const A5 = { params: [
  { key: "retest_of", label: "What broke", help: "", type: "enum" as const, default: "swing", choices: [{ value: "swing", label: "A swing level" }] },
  { key: "vol_mult", label: "Volume expansion", help: "", type: "float" as const, default: 0 },
  { key: "confirm", label: "Wait for a close", help: "", type: "bool" as const, default: false },
] };

describe("day-one fix 6: the adopt form shows the idea's own settings and never quietly picks an option", () => {
  it("settings read as their labels and choice words", () => {
    expect(settingRows(A5, { retest_of: "swing", vol_mult: 1.5, confirm: true, unknown_key: 3 })).toEqual([
      { key: "retest_of", label: "What broke", value: "A swing level" },
      { key: "vol_mult", label: "Volume expansion", value: "1.5" },
      { key: "confirm", label: "Wait for a close", value: "Yes" },
    ]);
  });

  it("an idea never starts on a pre-picked symbol; a stock's summary says it trades the shares", () => {
    expect(SYMBOLS).toContain(IDEA_SYMBOL);
    expect(startSummary(IDEA_SYMBOL, { timeframes: ["day"] })).toBe("Trades RELIANCE shares, held for days. Paper money only; your risk limits apply.");
    const form = readFileSync("src/paperbot/PaperBotV2.tsx", "utf8");
    // M68 PR-8 (TS1-098): no symbol is pre-selected at all (no index, and no stock either) - he picks it.
    expect(form).toContain('const [symbolChoice, setSymbolChoice] = useState("");');
    expect(form).not.toContain("? IDEA_SYMBOL :");
    expect(form).toContain("data-template-settings");
  });
});
