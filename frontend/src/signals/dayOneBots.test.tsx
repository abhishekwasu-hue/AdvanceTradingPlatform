import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { BotsView, botCheckWords } from "./DayOneV2";
import viewSource from "./DayOneV2.tsx?raw";
import type { Deployment } from "../types";
import type { DayOneOffer } from "./dayOne";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/\s+/g, " ").trim();
const A5: DayOneOffer = { id: "A5", name: "Breakout and retest", idea: "After a level breaks...", difficulty: "Beginner", family: "A", timeframe: "Daily",
  rules: [], settings: [] };
const bot = (over: Partial<Deployment>): Deployment => ({ id: 70, strategy_id: "template:A5:1:1d:both:swing,,,,,10,", symbol: "RELIANCE", mode: "PAPER",
  status: "ACTIVE", pause_reason: null, last_evaluated_at: null, ...over } as Deployment);

describe("day-one fix 7: Signals knows the bot he just started", () => {
  it("a just-started bot is named, with its symbol and 'just started', and no new adopt is asked", () => {
    const html = renderToStaticMarkup(<MemoryRouter><BotsView bots={[bot({})]} offers={[A5]} onAlsoList={() => undefined} /></MemoryRouter>);
    expect(html).toContain('data-day-one="bots"');
    expect(text(html)).toContain("Your paper bot is on");
    expect(text(html)).toContain("Breakout and retest · RELIANCE");
    expect(text(html)).toContain("Running · Just started: it reads its chart on its next round");
    expect(text(html)).not.toContain("Pick a ready strategy");
    expect(text(html)).toContain("Also watch a whole list");
    expect(html).not.toMatch(/template:|strategy_id/);
  });

  it("checked bots say when; several bots are counted; no 'Also watch' without an offer", () => {
    const now = Date.parse("2026-10-23T05:00:00Z");
    expect(botCheckWords({ status: "ACTIVE", last_evaluated_at: "2026-10-23T04:58:00Z" }, now)).toBe("Checked 2 min ago");
    expect(botCheckWords({ status: "PAUSED", last_evaluated_at: null }, now)).toBe("Not checked yet");
    const html = renderToStaticMarkup(<MemoryRouter><BotsView bots={[bot({}), bot({ id: 71, symbol: "TCS" })]} offers={[A5]} /></MemoryRouter>);
    expect(text(html)).toContain("Your 2 paper bots are on");
    expect(text(html)).not.toContain("Also watch a whole list");
  });

  it("the card shows his bots only while no watch is adopted (PAPER bots, running or paused)", () => {
    expect(viewSource).toContain('b.mode === "PAPER" && (b.status === "ACTIVE" || b.status === "PAUSED")');
    expect(viewSource).toContain("if (!data.adopted && running.length > 0 && !alsoList)");
  });
});
