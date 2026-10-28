import { describe, expect, it } from "vitest";
import journalSource from "./JournalPage.tsx?raw";
import { evidenceText } from "./JournalPage";

/** UI-28 U1.5 (owner, 28 Oct): a tag's evidence reads as words, never raw JSON; the scoring note is on demand. */
describe("UI-28 U1.5 Journal evidence and scoring", () => {
  it("evidence as 'name value' pairs, nested values and empties left out", () => {
    expect(evidenceText({ stop_moved: 2, risk_pct: 1.5, reason: "wider", raw: { a: 1 }, none: null })).toBe("stop moved 2 · risk pct 1.5 · reason wider");
    expect(evidenceText(null)).toBe("");
    expect(evidenceText("late entry")).toBe("late entry");
    expect(evidenceText({ amount: 125000 })).toBe("amount 1,25,000");
  });

  it("the page never prints JSON; 'How scored' holds the explanation", () => {
    expect(journalSource).not.toContain("JSON.stringify(t.evidence)");
    expect(journalSource).toMatch(/<Details id="journal\.how-scored" label="How scored">[\s\S]*Each component is the share/);
  });
});
