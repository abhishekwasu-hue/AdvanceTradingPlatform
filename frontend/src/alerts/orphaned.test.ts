import { describe, expect, it } from "vitest";
import { isOrphaned } from "./rules";
import panelSource from "../components/AlertRulesPanel.tsx?raw";

describe("UI-28 U7.3: every alert with its symbol, an orphan said plainly", () => {
  it("a screen alert whose screen was deleted (screen_id set to null) is orphaned; others are not", () => {
    expect(isOrphaned({ kind: "screen", screen_id: null })).toBe(true);
    expect(isOrphaned({ kind: "screen", screen_id: 7 })).toBe(false);
    expect(isOrphaned({ kind: "instrument", screen_id: null })).toBe(false);
  });
  it("the rule row shows the symbol chip and the orphan badge", () => {
    expect(panelSource).toContain("data-rule-symbol");
    expect(panelSource).toContain("isOrphaned(rule) &&");
  });
});
