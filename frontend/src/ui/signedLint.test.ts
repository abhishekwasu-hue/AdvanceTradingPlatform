import { describe, expect, it } from "vitest";
import marketsPage from "../markets/MarketsPageV2.tsx?raw";
import pageDish from "../growth/PageDishV2.tsx?raw";
import briefRows from "../rhythm/SectionRowsV2.tsx?raw";
import briefCard from "../rhythm/DailyRhythmCardV2.tsx?raw";

/**
 * UX-11b C7 "a signed number rendered without Signed" lint, where it can be checked from the source: on the pages
 * converted to numbers first, a signed figure goes through `Signed` (▲ / ▼, the sign, the up / down colour) - never a
 * hand-made signed string (`formatSigned*`), never a bare colour class on a move (`toneText`), and an up / down colour
 * class only on text that carries its own ▲ / ▼ mark.
 */
const FILES: Record<string, string> = {
  "markets/MarketsPageV2.tsx": marketsPage, "growth/PageDishV2.tsx": pageDish,
  "rhythm/SectionRowsV2.tsx": briefRows, "rhythm/DailyRhythmCardV2.tsx": briefCard,
};

describe("UX-11b: signed numbers go through Signed on the numbers-first pages", () => {
  it.each(Object.entries(FILES))("%s", (_, src) => {
    expect(src).not.toMatch(/formatSigned(INR|CompactIN)\(/);
    expect(src).not.toMatch(/\btoneText\(/);
    for (const line of src.split("\n").filter((l) => /"[^"]*\btext-(up|down)\b/.test(l))) {
      expect(line, "an up / down colour without its ▲ / ▼ mark").toMatch(/[▲▼]/);
    }
  });
});
