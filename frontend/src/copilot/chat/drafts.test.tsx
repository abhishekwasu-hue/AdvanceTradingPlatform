import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import draftSource from "./DraftCardV2.tsx?raw";
import { DraftCardsV2 } from "./DraftCardV2";
import { draftParams, draftSymbols, paramLabel, type AnswerDraft } from "./model";

/**
 * M72 PR-6 (CP-5): the planner's editable drafts. A screen draft shows its text, the values the user decides (empty
 * unless he stated them) and an empty symbols field; Run waits for both; the Screener is called only from the buttons
 * (validate / run), never on render, and nothing is saved. A proposed alert links to the Watchtower for approval.
 * Made-up data.
 */
const SCREEN: AnswerDraft = { kind: "screen", text: "RSI(14) > 60 AND close > SMA(close, $period)", base_tf: "1d",
  params: [{ name: "period", value: null }], stages: ["Momentum"], problems: [], ok: false };
const render = (drafts: AnswerDraft[]) => renderToStaticMarkup(<MemoryRouter><DraftCardsV2 drafts={drafts} /></MemoryRouter>);

describe("M72 PR-6: the drafts", () => {
  it("a screen draft: its text, an empty value the user decides, no symbols, Run waiting", () => {
    const html = render([SCREEN]);
    expect(html).toContain("Screen draft");
    expect(html).toContain("RSI(14) &gt; 60 AND close &gt; SMA(close, $period)");
    expect(html).toContain("Values you decide");
    expect(html).toMatch(/data-draft-param="period"[^>]*value=""|value=""[^>]*data-draft-param="period"/);
    expect(html).toMatch(/data-draft-symbols/);
    expect(html).toContain("Fill every value you decide");
    expect(html).toMatch(/disabled=""[^>]*data-draft-run|data-draft-run[^>]*disabled/);
    expect(html).toContain("Not saved. Nothing runs until you tap Run.");
  });

  it("a stated value is filled; the Screener is called only from the buttons; no order call, no save", () => {
    const html = render([{ ...SCREEN, params: [{ name: "period", value: 20 }] }]);
    expect(html).toContain('value="20"');
    expect(draftSource).toMatch(/async function check\(\)[\s\S]*screenerApi\.validate/);
    expect(draftSource).toMatch(/async function run\(\)[\s\S]*screenerApi\.run/);
    expect(draftSource).not.toMatch(/useEffect|placeOrder|saveScreen|screenerApi\.save|createScreen/);
  });

  it("a proposed alert waits for approval in the Watchtower", () => {
    const html = render([{ kind: "alert", status: "proposed", proposal_id: 41 }]);
    expect(html).toContain("Alert proposed");
    expect(html).toContain('href="/copilot/watchtower"');
    expect(html).not.toMatch(/\b(buy|sell|target|stop)\b/i);
  });

  it("helpers: labels, symbols, values", () => {
    expect(paramLabel("rsi_level")).toBe("Rsi level");
    expect(draftSymbols("reliance, tcs  infy,TCS")).toEqual(["RELIANCE", "TCS", "INFY"]);
    expect(draftParams({ period: "20" }, ["period"])).toEqual({ period: 20 });
    expect(draftParams({ period: "" }, ["period"])).toBeNull();
    expect(draftParams({ period: "abc" }, ["period"])).toBeNull();
    expect(draftParams({}, [])).toEqual({});
  });
});
