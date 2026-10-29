import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import AnswerV2 from "./AnswerV2";
import type { ChatAnswer } from "./model";
import chatSource from "./CopilotChatV2.tsx?raw";

const base = { job: "help", job_number: null, title: "t", summary: ["One line."], answer: "One line.", sections: [], blocks: [], unknowns: [],
  worked: [], action: null, refused: null, starters: [], concepts: [], notes: [], disclaimer: "For information.", source: "ai", tool_calls: [] } as unknown as ChatAnswer;

describe("UI-28 U7.7: the Copilot chat's cost chip and the folded tools", () => {
  it("an AI-worded answer shows what its call cost; a rule-based one shows nothing", () => {
    const html = renderToStaticMarkup(<MemoryRouter><AnswerV2 answer={{ ...base, usage: { calls: 1, tokens_input: 900, tokens_output: 300, cost_inr: 0.42 } }} /></MemoryRouter>);
    expect(html).toContain("data-answer-cost");
    expect(html).toContain("₹0.42");
    const rules = renderToStaticMarkup(<MemoryRouter><AnswerV2 answer={{ ...base, source: "rules", usage: null }} /></MemoryRouter>);
    expect(rules).not.toContain("data-answer-cost");
  });
  it("the older Copilot tools sit under one closed Advanced (kept, not removed)", () => {
    expect(chatSource).toContain('<Details id="copilot.chat.advanced" label="Advanced">');
    expect(chatSource).toContain("COPILOT_TABS.map");
  });
});
