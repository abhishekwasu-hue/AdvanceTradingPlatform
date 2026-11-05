import { describe, expect, it } from "vitest";
import { answerIndex, answerPlainText } from "./AnswerV2";
import { headingId } from "./RichText";

describe("CH-1b an indexed, copyable answer", () => {
  const lines = ["NIFTY 50 is in an up structure.", "## Summary\nUp structure, pullback running.", "## Structure\n- HH 24,260\n- HL 23,980",
    "## Areas\n- **Demand** 23,860 - 23,900", "## What to watch\nA close below 23,980."];
  it("the index lists the section headings in order", () => {
    expect(answerIndex(lines)).toEqual(["Summary", "Structure", "Areas", "What to watch"]);
    expect(answerIndex(["one line only"])).toEqual([]);
    expect(headingId("ans1", "What to watch")).toBe("ans1-what-to-watch");
  });
  it("Copy gives the answer's text without bold marks, then the disclaimer", () => {
    const text = answerPlainText({ summary: lines, disclaimer: "Information and education only, not advice." });
    expect(text).toContain("## Areas\n- Demand 23,860 - 23,900");
    expect(text).not.toContain("**");
    expect(text.endsWith("Information and education only, not advice.")).toBe(true);
  });
});
