import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { BotsView } from "../signals/DayOneV2";
import type { DayOneOffer } from "../signals/dayOne";
import type { Deployment } from "../types";
import { BOTS_NOT_CHECKED, botWaitText } from "./model";
import { BotRow } from "./PaperBotV2";
import pageSource from "./PaperBotV2.tsx?raw";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/\s+/g, " ").trim();
const A5: DayOneOffer = { id: "A5", name: "Breakout and retest", idea: "After a level breaks...", difficulty: "Beginner", family: "A", timeframe: "Daily",
  rules: [], settings: [] };
const bot = (over: Partial<Deployment>): Deployment => ({ id: 70, strategy_id: "template:A5:1:1d:both:swing,,,,,10,", symbol: "RELIANCE", mode: "PAPER",
  status: "ACTIVE", pause_reason: null, last_evaluated_at: "2026-10-30T09:00:00Z", consecutive_failures: 0, open_positions: 0,
  instrument_kind: "UNDERLYING", holding: "INTRADAY", ...over } as Deployment);

describe("M55 S7: bots say why they wait; a silent engine is said in one line", () => {
  it("a running bot's wait reason shows in plain words; a paused one, internal text or none shows nothing", () => {
    expect(botWaitText(bot({ wait_reason: "Waiting: market closed" }))).toBe("Waiting: market closed");
    expect(botWaitText(bot({ wait_reason: "Waiting: broker login needed" }))).toBe("Waiting: broker login needed");
    expect(botWaitText(bot({ status: "PAUSED", wait_reason: "Waiting: market closed" }))).toBeNull();
    expect(botWaitText(bot({ wait_reason: "worker_lock_lost" }))).toBeNull();
    expect(botWaitText(bot({ wait_reason: null }))).toBeNull();
  });

  it("the bot card shows the reason", () => {
    const html = renderToStaticMarkup(<MemoryRouter><ul><BotRow bot={bot({ wait_reason: "Waiting: no new entries after the cut-off" })} name="My bot"
      busy={false} disabled={false} confirming={false} onAsk={() => undefined} onCancel={() => undefined} onAct={() => undefined} /></ul></MemoryRouter>);
    expect(text(html)).toContain("Waiting: no new entries after the cut-off");
  });

  it("Today shows 'Bots are not being checked right now' only while the engine is silent", () => {
    const silent = renderToStaticMarkup(<MemoryRouter><BotsView bots={[bot({})]} offers={[A5]} checked={false} /></MemoryRouter>);
    expect(text(silent)).toContain(BOTS_NOT_CHECKED);
    const fine = renderToStaticMarkup(<MemoryRouter><BotsView bots={[bot({})]} offers={[A5]} checked /></MemoryRouter>);
    expect(text(fine)).not.toContain(BOTS_NOT_CHECKED);
    const unknown = renderToStaticMarkup(<MemoryRouter><BotsView bots={[bot({ wait_reason: "Waiting: market closed" })]} offers={[A5]} /></MemoryRouter>);
    expect(text(unknown)).not.toContain(BOTS_NOT_CHECKED);
    expect(text(unknown)).toContain("Waiting: market closed");
  });

  it("the bot page reads the engine's status with the bots and shows the same line", () => {
    expect(pageSource).toContain("api.workerStatus()");
    expect(pageSource).toContain("checked === false && <p role=\"status\"");
    expect(BOTS_NOT_CHECKED).toBe("Bots are not being checked right now");
  });
});
