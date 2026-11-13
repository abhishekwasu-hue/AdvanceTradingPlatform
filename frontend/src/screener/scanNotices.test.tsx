import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { RunResult } from "./api";
import { KillerNote, killerLine } from "./KillerNote";
import { BROKER_LIMIT, brokerLimitLine, heldBackLine, reasonLine, tally, unreadReason } from "./readCounts";
import { HELD_TEXT } from "./universe";
import { UnreadNote } from "./UnreadNote";

/**
 * Owner screenshot (Find setups, a 5m Ready scan on NIFTY 50 - 0 matched, 45 did not match, 5 could not be read):
 * the broker's limit was said twice, "7 held back" sat beside "5 could not be read", and the condition "removes every
 * symbol" although 5 were never checked. One notice, counts that add up, and a sentence that claims only what was checked.
 */
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").replace(/&#x27;|&apos;/g, "'").trim();
const funnel = (universe: number, withData: number) => ({ universe, with_data: withData, stages: [{ text: "x", survivors: 0 }] });
const rows: RunResult[] = [
  ...Array.from({ length: 45 }, (_, i) => ({ symbol: `S${i}`, matched: false, reason: null })),
  ...["TCS", "TITAN", "WIPRO", "UPL", "TRENT"].map((symbol) => ({ symbol, matched: false, reason: HELD_TEXT, code: BROKER_LIMIT, action: "retry" as const })),
];

describe("the broker's limit is said once, in one plain sentence, with Retry", () => {
  it("the grouped reason of the screenshot's answer is the broker's limit, for 5 stocks", () => {
    expect(unreadReason({ results: rows })).toMatchObject({ code: BROKER_LIMIT, count: 5, action: "retry" });
  });

  it("the note says it as one sentence - never '5 stocks: waited for the broker's limit - run again shortly'", () => {
    const html = renderToStaticMarkup(<UnreadNote tally={tally(funnel(50, 45), 0)} reason={unreadReason({ results: rows })} onAction={() => {}} hideCount />);
    const words = text(html);
    expect(words).toContain("5 stocks waited for your broker's limit. Run again in a minute to include them.");
    expect(words).not.toContain("stocks:");
    expect(words).not.toContain(" - run again shortly");
    expect(words).toContain("Retry");
    expect(words.split("broker's limit").length - 1).toBe(1);
  });

  it("one stock reads in the singular; other reasons keep 'N stocks: reason'", () => {
    expect(brokerLimitLine(1)).toBe("1 stock waited for your broker's limit. Run again in a minute to include it.");
    expect(reasonLine({ text: "Log in again.", count: 3, code: "login_ended" }, true)).toBe("3 stocks: Log in again.");
    expect(reasonLine({ text: "Could not read any of the 5 stocks.", code: BROKER_LIMIT }, false)).toBe("Could not read any of the 5 stocks.");
  });

  it("the page shows its own 'waited' line only when the note below does not already say it (both layouts)", () => {
    const source = readFileSync("src/pages/ScreenerPage.tsx", "utf8");
    const held = source.split("held.length > 0 && !running");
    expect(held.length - 1).toBe(2);
    for (const part of held.slice(1)) expect(part.startsWith(" && !heldInNote")).toBe(true);
    expect(source).toContain("const heldInNote = !readProblem && shown?.reason?.code === BROKER_LIMIT;");
  });
});

describe("the counts say what each number means", () => {
  it("symbols read from history past the live limit are said apart from those that could not be read", () => {
    expect(heldBackLine(2)).toBe("2 read from history (your live session limit was full)");
    const source = readFileSync("src/pages/ScreenerPage.tsx", "utf8");
    expect(source).not.toContain("held back by the session budget");
  });
});

describe("a condition that left nothing never claims more than the run checked", () => {
  it("with 5 not read, it removes all 45 it could check", () => {
    expect(killerLine(45, 5)).toBe("This condition removes all 45 symbols it could check. Loosen it or disable it to see candidates.");
    const html = renderToStaticMarkup(<KillerNote removed={45} unread={5} onShow={() => {}} onDisable={() => {}} />);
    expect(text(html)).toContain("removes all 45 symbols it could check");
    expect(text(html)).not.toContain("every symbol");
    expect(text(html)).toContain("Show the stage");
    expect(text(html)).toContain("Disable it");
  });

  it("with every symbol read, it still says every symbol", () => {
    expect(killerLine(50, 0)).toBe("This condition removes every symbol. Loosen it or disable it to see candidates.");
    expect(killerLine(1, 2)).toContain("removes the one symbol it could check");
  });

  it("both layouts use the one note", () => {
    const source = readFileSync("src/pages/ScreenerPage.tsx", "utf8");
    expect(source.split("<KillerNote ").length - 1).toBe(2);
    expect(source).not.toContain("This condition removes every symbol");
  });
});
