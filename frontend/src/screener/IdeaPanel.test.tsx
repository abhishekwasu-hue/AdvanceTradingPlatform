import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import IdeaPanel, { EXAMPLE_IDEA, ideaSummary } from "./IdeaPanel";
import type { IdeaResponse } from "./api";
import contract from "./builderTexts.json";
import { stagesFromServer } from "./importStages";
import { screenText, type Registry } from "./model";
import { check } from "../signals/language";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, '"');

/** Made-up answer of POST /api/screener/copilot/idea (handover 11j). */
const RESULT: IdeaResponse = {
  ok: true, text: 'IsFnO() AND Trend() == "UPTREND"', base_tf: "1d",
  stages: [
    { text: "IsFnO()", wrapped: "IsFnO()", ast: { k: "Call", name: "IsFnO", args: [], kwargs: {}, offset: 0, tf: null } },
    { text: 'Trend() == "UPTREND"', wrapped: 'Trend() == "UPTREND"', ast: { k: "Compare", op: "==",
      left: { k: "Call", name: "Trend", args: [], kwargs: {}, offset: 0, tf: null }, right: { k: "Str", v: "UPTREND" } } },
  ],
  parts: [
    { rule: "fno", phrase: "F&O", condition: "IsFnO()", reads: "the symbol is in the F&O segment", assumptions: [], ok: true, problems: [] },
    { rule: "trend_up", phrase: "uptrend", condition: 'Trend() == "UPTREND"', reads: "the market-structure trend is up", assumptions: [], ok: true, problems: [] },
    { rule: "demand_zone", phrase: "demand zone", condition: 'NearZone("demand")', reads: "the close is at a buyer area (a demand zone)",
      assumptions: ["at the area = within 0.5% of the nearest live zone (the function's default)"], ok: false,
      problems: [{ kind: "flag_off", message: "NearZone(): not enabled for this organisation", pos: null }] },
  ],
  not_supported: [{ phrase: "pulling back", why: "not supported yet" }],
  disclaimer: "A draft scan from your idea: check each block, edit it, then run it. Matches are filters passed, not advice.",
};

const API = { idea: async () => RESULT };

describe("A scan from your idea (handover 11j)", () => {
  it("shows each part with the user's words, what it checks and what is not supported yet", () => {
    const html = renderToStaticMarkup(<IdeaPanel baseTf="1d" onOpen={() => undefined} api={API} initialText={EXAMPLE_IDEA} initialResult={RESULT} />);
    const t = text(html);
    expect(t).toContain("You wrote: “F&amp;O”");
    expect(t).toContain("Checks: the market-structure trend is up.");
    expect(t).toContain("Not available on your account yet: left out of the scan.");
    expect(t).toContain("Not supported yet");
    expect(t).toContain("“pulling back”");
    expect(t).not.toContain("- not supported yet");
    expect(t).toContain("2 parts of your idea became blocks.");
    expect(html).toContain('data-testid="idea-open"');
    expect(t).toContain(RESULT.disclaimer);
    // the plain view carries no condition code: it sits under "Advanced"
    const plain = html.replace(/<details[\s\S]*?<\/details>/g, "");
    expect(plain).not.toContain("IsFnO()");
    expect(plain).not.toContain("NearZone(): not enabled");
  });

  it("an idea with nothing usable says so and offers no blocks", () => {
    const none: IdeaResponse = { ...RESULT, ok: false, text: null, stages: [], parts: [] };
    expect(ideaSummary(none)).toMatch(/^No part of this idea could become a condition yet/);
    const html = renderToStaticMarkup(<IdeaPanel baseTf="1d" onOpen={() => undefined} api={API} initialResult={none} />);
    expect(html).not.toContain('data-testid="idea-open"');
  });

  it("the blocks open in the builder from the server's stages", () => {
    const reg = contract.registry as unknown as Registry;
    const stages = stagesFromServer(reg, RESULT.stages);
    expect(stages).toHaveLength(2);
    expect(screenText(reg, stages)).toBe(RESULT.text);
  });

  it("the panel's own words are in the signal language (no trade wording, no numbers)", () => {
    for (const line of ["Your idea, in plain words", "Make the scan", "Use the example", "Open as blocks", "Not supported yet",
      "Not available on your account yet: left out of the scan.", ideaSummary(RESULT), RESULT.disclaimer]) {
      expect(check(line.replace(/\d+ parts/, "parts"), "signal_public")).toEqual([]);
    }
  });
});
