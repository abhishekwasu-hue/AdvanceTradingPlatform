import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AGENT_LIMIT_FIELDS, CopilotAgentLimitsView, parseLimit, type AgentLimits } from "./CopilotAgentLimitsCard";

/** M72 PR-2 (F20): the Copilot planner's limits card in Admin › Controls › Limits. Made-up values. */
const LIMITS: AgentLimits = {
  limits: { max_steps: 8, max_tool_calls: 12, max_seconds: 60, max_tokens_per_step: 4000 },
  defaults: { max_steps: 6, max_tool_calls: 12, max_seconds: 60, max_tokens_per_step: 4000 },
  bounds: { max_steps: { low: 1, high: 12 }, max_tool_calls: { low: 1, high: 30 }, max_seconds: { low: 5, high: 180 }, max_tokens_per_step: { low: 500, high: 16000 } },
};

describe("M72 PR-2 planner limits", () => {
  it("shows the four limits with the value in force and the default as the hint", () => {
    const html = renderToStaticMarkup(<CopilotAgentLimitsView limits={LIMITS} onSave={() => undefined} />);
    for (const f of AGENT_LIMIT_FIELDS) expect(html).toContain(`data-agent-limit="${f.key}"`);
    expect(html).toContain('value="8"');
    expect(html).toContain('placeholder="6"');
    expect(html).toContain("Save planner limits");
  });

  it("an empty field goes back to the default; a value outside its bounds is refused", () => {
    expect(parseLimit("", { low: 1, high: 12 })).toBeNull();
    expect(parseLimit("7", { low: 1, high: 12 })).toBe(7);
    expect(parseLimit("13", { low: 1, high: 12 })).toBeUndefined();
    expect(parseLimit("abc", { low: 1, high: 12 })).toBeUndefined();
  });
});
