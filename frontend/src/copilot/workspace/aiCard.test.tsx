import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AiCardV2, cardTimeframe, type WorkspaceCard } from "./AiCardV2";
import workspaceSource from "./ChartWorkspaceV2.tsx?raw";

/**
 * M75 W-4 (owner order M75 CW-1 / CW-2; M75Q-5): the workspace's AI card - started by itself after the code reading,
 * a short card + table, one plain line when it cannot run; every text guarded; no order button.
 */

const base: WorkspaceCard = { status: "ready", message: null, notes: [], as_of: "2026-10-20T10:00:00+00:00", cached: false,
  disclaimer: "Educational only.", card: {
    story: ["Price is in a pullback inside the up leg.", "It sits above the buyer area."],
    trend: { side: "up", text: "Up on this timeframe." }, current_state: { phase: "pullback", text: "A pullback is running." },
    rsi: { divergence: "none", text: "No divergence." }, what_to_wait_for: { condition: "A commitment candle at the buyer area." },
    scenarios: { primary: "The leg resumes.", alternative: "The pullback deepens." }, evidence: [{ name: "Higher high", present: true }],
  } };

describe("W-4: the AI card", () => {
  it("is offered on the hybrid reading's timeframes only", () => {
    expect(["5m", "15m", "1h", "1d", "1m", "30m", null].map(cardTimeframe)).toEqual(["5m", "15m", "1h", "1d", null, null, null]);
  });
  it("a short card + table, toned by meaning, with the disclaimer and no order button", () => {
    const html = renderToStaticMarkup(<AiCardV2 state={{ status: "done", card: base }} offered />);
    expect(html).toContain('data-ai-card="ready"');
    expect(html).toContain('data-ai-row="trend"');
    expect(html).toContain("text-up");
    expect(html).toContain("A commitment candle at the buyer area.");
    expect(html).toContain("Educational only.");
    expect(html).not.toMatch(/<button[^>]*>(Place|Buy|Sell)/);
  });
  it("a cap or a missing part: one plain line", () => {
    const html = renderToStaticMarkup(<AiCardV2 state={{ status: "done", card: { ...base, status: "limit", card: null,
      message: "Limit reached. The code reading above is complete on its own." } }} offered />);
    expect(html).toContain('data-ai-card="limit"');
    expect(html).toContain("The code reading above is complete on its own.");
    expect(html).not.toContain("data-ai-row");
  });
  it("call words from the model are withheld here as well", () => {
    const html = renderToStaticMarkup(<AiCardV2 state={{ status: "done", card: { ...base, card: { ...base.card, story: ["Buy now at 24000 with a target."] } } }} offered />);
    expect(html).not.toContain("Buy now");
  });
  it("starts by itself after the code reading, once per symbol and timeframe", () => {
    expect(workspaceSource).toMatch(/useWorkspaceCard\(pick\?\.symbol \?\? null, cardTf, read\.status === "ready"\)/);
  });
  it("a timeframe without the AI part says so", () => {
    expect(renderToStaticMarkup(<AiCardV2 state={{ status: "idle" }} offered={false} />)).toContain("5-minute, 15-minute, 1-hour and Daily");
  });
});
