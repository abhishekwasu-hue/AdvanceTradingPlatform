import { renderToStaticMarkup } from "react-dom/server";
import { I18nextProvider } from "react-i18next";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { copilotI18n } from "../i18n";
import { AnswerV2, INDEX_MIN_CHARS } from "./AnswerV2";
import { chatReply } from "./CopilotChatV2";
import { answerParts, ideaPrefill, type ChatAnswer, type ChatJob } from "./model";

/** CP-CHAT (owner 20 Nov): an AI reply reads as a chat message, and a starter that needs his idea fills the box. */
const render = (node: React.ReactNode) =>
  renderToStaticMarkup(<I18nextProvider i18n={copilotI18n}><MemoryRouter initialEntries={["/copilot"]}>{node}</MemoryRouter></I18nextProvider>);

const JOBS: Pick<ChatJob, "starter" | "prefill">[] = [
  { starter: "Today's market brief", prefill: null },
  { starter: "Check my trade idea", prefill: "Check my trade idea: " },
  { starter: "Turn my idea into a strategy and test it", prefill: "Turn my idea into a strategy and test it: " },
];

const REPLY: ChatAnswer = {
  job: "agent", job_number: null, title: "Answer", source: "ai", tool_calls: [], worked: [], unknowns: [], starters: [], concepts: [],
  notes: [], refused: null, action: null, blocks: [], disclaimer: "Information and education only, not advice.",
  summary: ["## Summary\nNIFTY reads up.", "## What I need from you\nYour timeframe.", "## Next step\nTell me the timeframe."],
  answer: "", sections: [{ title: "Structure", lines: ["Daily: reads up"] }],
};

describe("CP-CHAT starters that need his idea", () => {
  it("fill the box when only the starter or the bare prefill would be sent", () => {
    expect(ideaPrefill("Check my trade idea", JOBS)).toBe("Check my trade idea: ");
    expect(ideaPrefill("  check my trade idea:  ", JOBS)).toBe("Check my trade idea: ");
    expect(ideaPrefill("Turn my idea into a strategy and test it", JOBS)).toBe("Turn my idea into a strategy and test it: ");
  });

  it("send a question that carries his own words, and every other starter, as before", () => {
    expect(ideaPrefill("Check my trade idea: NIFTY 24500 CE, 2 lots", JOBS)).toBeNull();
    expect(ideaPrefill("Today's market brief", JOBS)).toBeNull();
    expect(ideaPrefill("", JOBS)).toBeNull();
    expect(ideaPrefill("Check my trade idea", [])).toBeNull();
  });
});

describe("CP-CHAT an AI reply", () => {
  it("shows no row of section chips on a short reply", () => {
    const html = render(<AnswerV2 answer={REPLY} onAsk={() => undefined} />);
    expect(html).not.toContain("data-answer-index");
  });

  it("keeps the jump index for a long answer written in sections", () => {
    const long = { ...REPLY, summary: REPLY.summary.map((s) => `${s} ${"x".repeat(INDEX_MIN_CHARS / 2)}`) };
    expect(render(<AnswerV2 answer={long} onAsk={() => undefined} />)).toContain("data-answer-index");
  });

  it("folds the rule sections under its own words; a rule answer keeps them", () => {
    expect(answerParts(REPLY).shown).toEqual([]);
    expect(answerParts(REPLY).folded).toHaveLength(1);
    expect(answerParts({ ...REPLY, source: "rules" }).shown).toHaveLength(1);
  });

  it("is a chat bubble; a chart reading, a number row or a refusal keeps its card", () => {
    expect(chatReply(REPLY)).toBe(true);
    expect(chatReply({ ...REPLY, source: "rules" })).toBe(false);
    expect(chatReply({ ...REPLY, refused: "advice" })).toBe(false);
    expect(chatReply({ ...REPLY, structure_card: {} as never })).toBe(false);
  });
});
