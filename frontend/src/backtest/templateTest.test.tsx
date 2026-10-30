import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { paperBotPathFor, templateLinkFromSearch, testPathFor } from "../strategyTemplates/link";
import { TEMPLATE_PICK, templateTimeframeFor } from "./templateTest";

const A5 = {
  timeframes: [{ value: "5m", label: "5-minute" }, { value: "15m", label: "15-minute" }, { value: "1d", label: "Daily" }],
  default_timeframe: "5m",
};
const INTRADAY_ONLY = { timeframes: [{ value: "5m", label: "5-minute" }], default_timeframe: "5m" };
const BANNED = /\b(buy|sell|target|stop-loss|backend|lake|bhavcopy|1d|15m)\b/i;

describe("day-one fix 1: a strategy template is tested on Strategies › Test", () => {
  it("the idea's link to the test page carries the same template, timeframe and settings as the Trading bot link", () => {
    const link = { template: "A5", timeframe: "15m", params: { retest_max_bars: 8 } };
    expect(testPathFor(link)).toBe("/strategies/test?template=A5&timeframe=15m&set.retest_max_bars=8");
    expect(testPathFor(link).split("?")[1]).toBe(paperBotPathFor(link).split("?")[1]);
    expect(templateLinkFromSearch(testPathFor(link).split("?")[1])).toEqual({ template: "A5", timeframe: "15m", params: { retest_max_bars: "8" } });
    expect(TEMPLATE_PICK).toBe("template-pick:");
  });

  it("with broker prices the idea's own timeframe is used and nothing is said", () => {
    expect(templateTimeframeFor(A5, { linked: "15m", storedOnly: false })).toEqual({ value: "15m", note: null });
    expect(templateTimeframeFor(A5, { storedOnly: false })).toEqual({ value: "5m", note: null });
  });

  it("without broker prices an intraday idea is tested on Daily stored history, in plain words", () => {
    const r = templateTimeframeFor(A5, { linked: "15m", storedOnly: true });
    expect(r.value).toBe("1d");
    expect(r.note).toBe("The idea is set to 15-minute bars. Without your broker's prices today only Daily history is stored, so this test runs "
      + "on Daily. Log in on the Broker page to test it on 15-minute bars.");
    expect(r.note).not.toMatch(BANNED);
    expect(templateTimeframeFor(A5, { storedOnly: true }).note).toContain("This template usually reads 5-minute bars.");
  });

  it("the trader's own pick wins; an intraday pick without broker prices says what it needs", () => {
    expect(templateTimeframeFor(A5, { chosen: "1d", linked: "15m", storedOnly: true })).toEqual({ value: "1d", note: null });
    const r = templateTimeframeFor(A5, { chosen: "5m", storedOnly: true });
    expect(r.value).toBe("5m");
    expect(r.note).toBe("5-minute bars need prices from your broker: log in on the Broker page, or pick Daily to test on ATP's stored history.");
    expect(templateTimeframeFor(A5, { chosen: "4h", storedOnly: false }).value).toBe("5m");
  });

  it("an intraday-only template without broker prices says so instead of failing later", () => {
    const r = templateTimeframeFor(INTRADAY_ONLY, { storedOnly: true });
    expect(r.value).toBe("5m");
    expect(r.note).toContain("need prices from your broker");
    expect(templateTimeframeFor({ timeframes: [], default_timeframe: null }, { storedOnly: true }).value).toBe("");
  });

  it("the page, the idea card and the Trading bot form are wired (v2 + strategy_templates only)", () => {
    const page = readFileSync("src/pages/BacktestPage.tsx", "utf8");
    expect(page).toContain('uiV2 && features.on("strategy_templates")');
    expect(page).toContain("templatesApi.adopt(");
    expect(page).toContain("data-template-test-note");
    expect(readFileSync("src/ideas/IdeaLibraryV2.tsx", "utf8")).toContain("Test it on past prices");
    expect(readFileSync("src/paperbot/PaperBotV2.tsx", "utf8")).toContain("Test it first on past prices");
  });

  it("REV-S3: a template run's history note shows with the result, in the warning tokens", () => {
    const page = readFileSync("src/pages/BacktestPage.tsx", "utf8");
    const at = page.indexOf('data-testid="history-note"');
    expect(at).toBeGreaterThan(page.indexOf('<StatTile label="Total Trades"'));
    expect(page.slice(at - 200, at + 200)).toContain("result.history?.note");
    expect(page.slice(at, at + 200)).toContain("border-warn/40 bg-warn/10");
    expect(readFileSync("src/types.ts", "utf8")).toContain("history?: { note: string | null; needed_days: number; warmup_days: number } | null;");
  });
});
