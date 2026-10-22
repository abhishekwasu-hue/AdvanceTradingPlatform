import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import CopilotDraftV2 from "../paperbot/CopilotDraftV2";
import cardSource from "../paperbot/CopilotDraftV2.tsx?raw";
import { withIdeaLots } from "../paperbot/model";
import pageSource from "../paperbot/PaperBotV2.tsx?raw";
import answerSource from "./chat/AnswerV2.tsx?raw";
import { AnswerV2 } from "./chat/AnswerV2";
import { actionState, type ChatAnswer } from "./chat/model";
import {
  answerProblem, askKind, draftConfig, draftFromState, formSymbol, openAsks, saveLine, targetPair, type StrategyDraft,
} from "./strategyDraft";

/**
 * Item 5, job 6: the Copilot's draft paper strategy - its rules on the page (nothing saves with a number the trader did not
 * give), the hand-over from the chat to the Paper bot page and the review card. Made-up data.
 */
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, "\"").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const noop = () => undefined;
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/[^\n]*/g, "$1");

const ema = (period: number | null) => ({ type: "indicator" as const, indicator: "EMA" as const, period, multiplier: null, timeframe: null });
const COMPLETE: StrategyDraft = {
  idea: "on the 15-minute chart, go long when the 20 EMA crosses above the 50 EMA. Stop 1.5 x ATR 14, objective 2R.",
  name: "My EMA idea", symbol: "NIFTY", timeframe: "15min", sides: ["long"],
  rules: [{ side: "long", condition: { left: ema(20), operator: "CROSSES_ABOVE", right: ema(50) }, text: "The 20 EMA crosses above the 50 EMA.", words: "x" }],
  settings: { stop_loss_atr_mult: 1.5, atr_period: 14, target_rr: [2, 2], min_rr: 1.5 }, min_rr_source: "your risk limits", lots: null,
  asks: [], unsupported: [], unread: [], read_as: [], ready_made: [], complete: true, mode: "PAPER",
};
const OPEN: StrategyDraft = {
  ...COMPLETE, idea: "when the fast EMA crosses the slow EMA", name: "My EMA idea", symbol: null, timeframe: null, sides: [],
  rules: [{ side: null, condition: { left: ema(null), operator: null, right: ema(null) }, text: "The EMA (length to decide) crosses (above or below: yours to decide) the EMA (length to decide).", words: "x" }],
  settings: { stop_loss_atr_mult: null, atr_period: null, target_rr: null, min_rr: null }, min_rr_source: null, lots: 2,
  asks: [
    { key: "side", label: "The side: long, short or both" }, { key: "timeframe", label: "The timeframe: the candles your rules read" },
    { key: "rules.0.left.period", label: "The EMA length (rule 1)" }, { key: "rules.0.right.period", label: "The EMA length (rule 1, second)" },
    { key: "rules.0.operator", label: "The direction of the cross: above or below" },
    { key: "stop_loss_atr_mult", label: "Where the idea is wrong: the stop distance, in ATRs from the entry" },
    { key: "atr_period", label: "The ATR length the stop distance uses" }, { key: "target_rr", label: "Your profit objective, in R (a multiple of the risk)" },
    { key: "min_rr", label: "Your minimum reward to risk for a trade to count" },
  ],
  unsupported: ["RSI divergence. The Screener can look for it today"], unread: ["the moon is full"], complete: false,
};
const ANSWERS = {
  side: "both", timeframe: "5min", "rules.0.left.period": "9", "rules.0.right.period": "21", "rules.0.operator": "CROSSES_ABOVE",
  stop_loss_atr_mult: "1", atr_period: "14", target_rr: "1.5, 3", min_rr: "1.2",
};

describe("job 6: the draft's rules", () => {
  it("a complete draft saves exactly the values the trader's words gave, PAPER only", () => {
    const cfg = draftConfig(COMPLETE, {})!;
    expect(cfg).toEqual({
      name: "My EMA idea", timeframe: "15min", short_conditions: [], stop_loss_atr_mult: 1.5, atr_period: 14, target_rr: [2, 2], min_rr: 1.5,
      long_conditions: [{ left: { type: "indicator", indicator: "EMA", period: 20, multiplier: 3, timeframe: null, value: 0 }, operator: "CROSSES_ABOVE",
        right: { type: "indicator", indicator: "EMA", period: 50, multiplier: 3, timeframe: null, value: 0 } }],
    });
    expect(saveLine(COMPLETE, {})).toContain("nothing starts until you press Start on paper");
  });

  it("nothing saves while any value is open: no number is ever filled in for the trader", () => {
    expect(draftConfig(OPEN, {})).toBeNull();
    expect(openAsks(OPEN, {})).toHaveLength(OPEN.asks.length);
    for (const key of Object.keys(ANSWERS)) {
      const partial = { ...ANSWERS, [key]: "" };
      expect(draftConfig(OPEN, partial), key).toBeNull();
    }
    expect(saveLine(OPEN, {})).toBe("9 values are still yours to decide.");
    expect(saveLine(OPEN, { ...ANSWERS, atr_period: "" })).toBe("One value is still yours to decide.");
  });

  it("with every value answered, the trader's own numbers make the strategy (both sides from one rule set)", () => {
    const cfg = draftConfig(OPEN, ANSWERS, "  My cross  ")!;
    expect(cfg.name).toBe("My cross");
    expect(cfg.timeframe).toBe("5min");
    expect(cfg.long_conditions).toEqual(cfg.short_conditions);
    expect(cfg.long_conditions[0]).toMatchObject({ operator: "CROSSES_ABOVE", left: { period: 9 }, right: { period: 21 } });
    expect([cfg.stop_loss_atr_mult, cfg.atr_period, cfg.target_rr, cfg.min_rr]).toEqual([1, 14, [1.5, 3], 1.2]);
  });

  it("his own minimum reward to risk can be changed; an invalid one blocks the save", () => {
    expect(draftConfig(COMPLETE, { min_rr: "2" })!.min_rr).toBe(2);
    expect(draftConfig(COMPLETE, { min_rr: "abc" })).toBeNull();
  });

  it("each answer is checked in plain words", () => {
    expect(askKind("side")).toBe("side");
    expect(askKind("rules.0.operator")).toBe("direction");
    expect(askKind("rules.1.left.period")).toBe("whole");
    expect(askKind("target_rr")).toBe("pair");
    expect(askKind("rules.0.right.value")).toBe("number");
    expect(answerProblem("atr_period", "")).toBe("Still yours to decide.");
    expect(answerProblem("atr_period", "14.5")).toBe("Write a whole number up to 500.");
    expect(answerProblem("stop_loss_atr_mult", "-1")).toBe("Write a number above zero.");
    expect(answerProblem("stop_loss_atr_mult", "1.5")).toBeNull();
    expect(answerProblem("side", "up")).toBe("Pick long, short or both.");
    expect(answerProblem("timeframe", "1d")).toBe("Pick a timeframe.");
    expect(targetPair("2")).toEqual([2, 2]);
    expect(targetPair("1.5, 3")).toEqual([1.5, 3]);
    expect(targetPair("3, 1")).toBeNull();
    expect(targetPair("1 2 3")).toBeNull();
  });

  it("the hand-over: only a PAPER draft with rules is taken from the navigation state", () => {
    expect(draftFromState({ copilotDraft: COMPLETE })).toBe(COMPLETE);
    expect(draftFromState({ copilotDraft: { ...COMPLETE, mode: "LIVE" } })).toBeNull();
    expect(draftFromState({ copilotDraft: { ...COMPLETE, rules: [] } })).toBeNull();
    expect(draftFromState(null)).toBeNull();
    expect(draftFromState("x")).toBeNull();
    expect(formSymbol("NIFTY")).toBe("NIFTY 50");
    expect(formSymbol("BANKNIFTY")).toBe("NIFTY BANK");
    expect(formSymbol("reliance")).toBe("RELIANCE");
    expect(formSymbol(null)).toBeNull();
  });

  it("the size his idea named goes on a contract that takes lots, never on a cash symbol, never the mode", () => {
    const option = { strategy_id: "custom:1", symbol: "NIFTY 50", exchange: "NSE", timeframe: "1min", mode: "PAPER" as const, instrument_kind: "OPTION" as const, max_lots: 1 };
    expect(withIdeaLots(option, 2)).toEqual({ ...option, max_lots: 2 });
    expect(withIdeaLots(option, null)).toBe(option);
    const cash = { ...option, instrument_kind: "UNDERLYING" as const };
    expect(withIdeaLots(cash, 2)).toBe(cash);
    expect(withIdeaLots(option, 2).mode).toBe("PAPER");
  });
});

describe("job 6: the chat answer and the review card", () => {
  const answer: ChatAnswer = {
    job: "strategy", job_number: 6, title: "Strategy builder", summary: ["Your idea as a draft paper strategy, built only from ATP's own building blocks."],
    answer: "", sections: [{ title: "Long side: all of these hold on the 15-minute chart", lines: ["The 20 EMA crosses above the 50 EMA."] }],
    blocks: [], unknowns: ["I don't know how this idea did in the past: a test on past data is not part of this draft yet."], worked: [],
    action: { label: "Review it on the Paper bot page", to: "/deployments" }, refused: null, starters: [], concepts: [], notes: [],
    disclaimer: "Information and education only, not advice. Every trading decision is yours.", source: "rules", tool_calls: ["get_risk_limits"], draft: COMPLETE,
  };

  it("the answer's one primary action carries the draft to the Paper bot page (state, not the address)", () => {
    const html = renderToStaticMarkup(<MemoryRouter><AnswerV2 answer={answer} /></MemoryRouter>);
    expect(html).toContain('data-action="draft"');
    expect(html).toContain('href="/deployments"');
    expect(text(html)).toContain("Review it on the Paper bot page");
    expect(actionState(answer)).toEqual({ copilotDraft: COMPLETE });
    expect(actionState({ draft: null })).toBeUndefined();
    expect(answerSource).toContain("state={actionState(answer)}");
  });

  it("the card shows the rules, an EMPTY field per open value, what was left out, and Save off until all are set", () => {
    const html = renderToStaticMarkup(<CopilotDraftV2 draft={OPEN} onSaved={noop} onDiscard={noop} />);
    const t = text(html);
    expect(t).toContain("Your draft from the Copilot");
    expect(t).toContain("Nothing is saved yet");
    expect(t).toContain("Side to decide");
    expect(t).toContain("Values you decide");
    for (const a of OPEN.asks) expect(html).toContain(`data-ask="${a.key}"`);
    expect(html).not.toMatch(/data-ask="[^"]+"[^>]*value="[^"]+"/);                     // every field starts empty
    expect(t).toContain("Not supported yet: RSI divergence");
    expect(t).toContain("Not read: “the moon is full”");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Save as my strategy/);
    expect(t).toContain("Discard the draft");
  });

  it("a complete draft shows his stated values and can be saved; his own minimum reward to risk is editable", () => {
    const html = renderToStaticMarkup(<CopilotDraftV2 draft={COMPLETE} onSaved={noop} onDiscard={noop} />);
    const t = text(html);
    expect(t).toContain("From your words");
    expect(t).toContain("Where the idea is wrong: 1.5 x ATR from the entry");
    expect(t).toContain("Profit objective: 2R");
    expect(t).toContain("Minimum reward to risk (from your own risk limits)");
    expect(html).not.toMatch(/<button[^>]*disabled=""[^>]*>Save as my strategy/);
  });

  it("the Copilot never saves or starts: the card saves only on the trader's click, the page starts nothing", () => {
    const card = code(cardSource);
    expect(card).toContain("onClick={() => void onSave()}");
    expect(card).not.toMatch(/createDeployment|startDeployment|placeOrder|mode: "LIVE"/);
    expect(card.match(/save\(config\)/g)).toHaveLength(1);
    const page = code(pageSource);
    expect(page).toContain('features.on("ai_copilot") ? draftFromState(location.state) : null');
    expect(page).toMatch(/onSaved=\{\(saved\) => \{[\s\S]*setPreselect[\s\S]*\}\}/);
    expect(page.match(/api\.createDeployment/g)).toHaveLength(1);                       // only "Start on paper" starts a bot
  });
});
