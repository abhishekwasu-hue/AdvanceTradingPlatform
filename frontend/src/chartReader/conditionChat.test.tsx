import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import ConditionChatV2, { chatBands, NO_ITEM } from "./ConditionChatV2";
import { OWN_CONDITIONS, OWN_CONDITIONS_LABEL } from "./ChartReaderSectionV2";
import sectionSource from "./ChartReaderSectionV2.tsx?raw";
import { withExtra } from "../chartAnalyze/HybridResultV2";
import type { ChatTurn } from "./types";

/** CA-CHAT (owner 18 / 19 Nov). Made-up turns only. */
const turn = (over: Partial<ChatTurn> = {}): ChatTurn => ({
  question: "Example: price above the last swing low", at: "2026-11-19T04:00:00Z", cost_usd: 0.012,
  answers: [
    { condition: "Example swing low held", status: "met", evidence: "Example evidence.", price_low: 101.5, price_high: 102.25 },
    { condition: "Example volume rising", status: "unclear", evidence: "Not drawn.", price_low: null, price_high: null },
    { condition: "Example strong candle", status: "not_met", evidence: "Small bodies.", price_low: 99, price_high: 99.5 },
  ],
  ...over,
});

describe("the condition chat", () => {
  it("each answer with a price area becomes a chart band coloured by its status, tagged Q<turn>.<answer>", () => {
    const bands = chatBands([turn()]);
    expect(bands.map((b) => [b.label, b.role, b.low, b.high])).toEqual([["Q1.1", "demand", 101.5, 102.25], ["Q1.3", "supply", 99, 99.5]]);
    expect(chatBands([])).toEqual([]);
    expect(chatBands(Array.from({ length: 20 }, () => turn()))).toHaveLength(12);       // the latest only, never a cluttered chart
  });

  it("the chat's bands draw on top of ATP's own reading, which stays as it was", () => {
    const base = { levels: [], areas: [{ low: 1, high: 2 }] };
    expect(withExtra(base, [])).toBe(base);
    expect(withExtra(base, chatBands([turn()])).areas).toHaveLength(3);
  });

  it("without an analysis it asks for one first and calls nothing", () => {
    expect(renderToStaticMarkup(<ConditionChatV2 itemId={null} />)).toContain(NO_ITEM);
  });

  it("the input, the send button and no order words in the static text", () => {
    const html = renderToStaticMarkup(<ConditionChatV2 itemId={7} />);
    expect(html).toContain('data-testid="chat-input"');
    expect(html).toContain("Check on this chart");
    expect(html).not.toMatch(/\b(buy|sell|target|stop[- ]?loss)\b/i);
  });

  it("the Setup list offers My own conditions, never sent to the reader as a strategy", () => {
    expect(OWN_CONDITIONS_LABEL).toBe("My own conditions");
    expect(sectionSource).toContain("pick === OWN_CONDITIONS) return");
    expect(OWN_CONDITIONS.startsWith("__")).toBe(true);
  });
});
