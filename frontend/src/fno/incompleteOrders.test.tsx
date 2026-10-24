import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { IncompleteItem } from "./incompleteApi";
import { IncompleteCardV2, countdownText } from "./IncompleteOrdersV2";
import source from "./IncompleteOrdersV2.tsx?raw";
import { SMART_DEFAULTS, readSmart, smartFormOf, smartOf } from "../settings/smartExecutionSettings";

/**
 * SE-7 (flag smart_execution): the incomplete multi-leg order on Positions - what filled, what did not, his three taps,
 * the countdown to his own rule - and the two settings behind it (close the filled legs / keep them, after N minutes).
 */
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const ADVICE = /\b(should|recommend\w*|advice|advise|best|target\w*|guarantee\w*|sure[- ]?shot|stop[- ]?loss)\b/i;
function elements(node: ReactNode, out: ReactElement[] = []): ReactElement[] {
  if (Array.isArray(node)) { node.forEach((n) => elements(n, out)); return out; }
  if (!isValidElement(node)) return out;
  out.push(node);
  elements((node.props as { children?: ReactNode }).children, out);
  return out;
}

const ITEM: IncompleteItem = {
  group_id: "g1", underlying: "NIFTY 50", strategy: "bull put spread", mode: "PAPER", state: "INCOMPLETE",
  message: "Incomplete: a leg did not fill.", failure: "SELL NIFTY 24500 PE rejected: no practice price",
  deadline: "2026-10-23T10:05:00Z", seconds_left: 245, on_no_answer: "close_filled", rule: null,
  filled: [{ trade_id: 7, symbol: "NIFTY 24400 PE", side: "BUY", role: "hedge", quantity: 150, entry_price: 80.4, open: true }],
  pending: [{ symbol: "NIFTY 24500 PE", side: "SELL", role: "sold", quantity: 150 }],
  actions: ["complete_rest", "close_filled", "keep"],
};

describe("the countdown", () => {
  it("reads minutes and seconds and never goes below zero", () => {
    expect(countdownText(245)).toBe("4:05");
    expect(countdownText(59)).toBe("0:59");
    expect(countdownText(-3)).toBe("0:00");
    expect(countdownText(null)).toBe("0:00");
  });
});

describe("the incomplete order card", () => {
  it("shows what filled, what did not, the time left and what his rule does", () => {
    const html = text(renderToStaticMarkup(<IncompleteCardV2 item={ITEM} secondsLeft={245} busy={false} onAnswer={() => undefined} />));
    expect(html).toContain("Incomplete order: NIFTY 50 (practice)");
    expect(html).toContain("Filled (1)");
    expect(html).toContain("NIFTY 24400 PE");
    expect(html).toContain("Not placed (1)");
    expect(html).toContain("4:05 left to choose");
    expect(html).toContain("the filled legs are closed, sold legs first (your setting)");
    expect(html).toContain("nothing was undone");
    expect(html).not.toMatch(ADVICE);
    const keep = text(renderToStaticMarkup(<IncompleteCardV2 item={{ ...ITEM, on_no_answer: "keep" }} secondsLeft={10} busy={false} onAnswer={() => undefined} />));
    expect(keep).toContain("the filled legs stay open (your setting)");
  });

  it("answers only on his tap, one action per button", () => {
    const onAnswer = vi.fn();
    const tree = elements(IncompleteCardV2({ item: ITEM, secondsLeft: 100, busy: false, onAnswer }));
    const buttons = tree.filter((e) => e.type === "button");
    expect(buttons.map((b) => (b.props as Record<string, string>)["data-incomplete-action"])).toEqual(["complete_rest", "close_filled", "keep"]);
    expect(onAnswer).not.toHaveBeenCalled();
    buttons.forEach((b) => (b.props as { onClick: () => void }).onClick());
    expect(onAnswer.mock.calls.map((c) => c[0])).toEqual(["complete_rest", "close_filled", "keep"]);
    // Nothing in the component answers by itself: the only answer call is the tap handler's.
    expect(source.match(/api\.answer\(/g)?.length).toBe(1);
  });

  it("once answered shows the progress instead of the buttons", () => {
    const html = renderToStaticMarkup(<IncompleteCardV2 item={{ ...ITEM, state: "CLOSING", actions: [] }} secondsLeft={null} busy={false} onAnswer={() => undefined} />);
    expect(html).not.toContain("data-incomplete-action");
    expect(text(html)).toContain("Closing the filled legs, sold legs first.");
  });
});

describe("his answer settings", () => {
  it("defaults to closing the filled legs after 5 minutes, with bounds on the minutes", () => {
    expect(SMART_DEFAULTS.on_no_answer).toBe("close_filled");
    expect(readSmart({ on_no_answer: "unwind", answer_minutes: "x" })).toMatchObject({ on_no_answer: "close_filled", answer_minutes: 5 });
    expect(readSmart({ on_no_answer: "keep", answer_minutes: 30 })).toMatchObject({ on_no_answer: "keep", answer_minutes: 30 });
    const form = smartFormOf(SMART_DEFAULTS);
    expect(smartOf({ ...form, answer_minutes: "0" }).problems[0]).toContain("from 1 to 120");
    expect(smartOf({ ...form, answer_minutes: "121" }).problems[0]).toContain("from 1 to 120");
    expect(smartOf({ ...form, answer_minutes: "2.5" }).problems[0]).toContain("whole number");
    expect(smartOf({ ...form, answer_minutes: "15", on_no_answer: "keep" })).toEqual({ value: { ...SMART_DEFAULTS, answer_minutes: 15, on_no_answer: "keep" }, problems: [] });
  });
});
