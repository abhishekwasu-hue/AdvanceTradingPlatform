import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { BasketResult, SettlementView, SmartView } from "./api";
import { ResultView } from "./BasketPanelV2";
import { IncompleteV2, SettlementListV2, SmartConfirmV2 } from "./SmartExecutionV2";
import source from "./SmartExecutionV2.tsx?raw";

/**
 * TM Part 7 smart execution (flag smart_execution): the confirm block (order of the legs, slices, margin and its label,
 * free balance after), the incomplete state with its two choices, and the delivery warning on Positions. No DOM: the
 * views are rendered to markup and their handlers read from the element tree.
 */
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, "\"").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const ADVICE = /\b(should|recommend\w*|advice|advise|best|target\w*|guarantee\w*|sure[- ]?shot)\b/i;
function elements(node: ReactNode, out: ReactElement[] = []): ReactElement[] {
  if (Array.isArray(node)) { node.forEach((n) => elements(n, out)); return out; }
  if (!isValidElement(node)) return out;
  const type = node.type as unknown;
  if (typeof type === "function" && (type as { name: string }).name === "ResultView") return elements((type as (p: unknown) => ReactNode)(node.props), out);
  out.push(node);
  elements((node.props as { children?: ReactNode }).children, out);
  return out;
}

const SMART: SmartView = {
  sequence: [
    { seq: 1, leg: 2, label: "NIFTY 28 OCT 24300 PE", side: "BUY", role: "hedge", lots: 1, slices: [1], note: null },
    { seq: 2, leg: 0, label: "NIFTY 28 OCT 24500 PE", side: "SELL", role: "short", lots: 60, slices: [53, 7],
      note: "Above the exchange's freeze limit (3,510 qty for NIFTY): sent as 2 orders of at most 53 lots each." },
  ],
  margin: { source: "estimate", label: "ATP's estimate, not your broker's number", required: 52000, without_hedge: 180000, benefit: 128000,
    free_before: 500000, free_after: 448000, free_source: "practice", notes: ["Free balance: your practice balance; the margin of positions you already hold is not counted."] },
  entry_rule: "Hedge legs (Buy) go first, then the sold legs, so the protection is in place before the short.",
  exit_rule: "On exit the sold legs close first, then the hedges: a short is never left without its hedge.",
  incomplete_rule: "If a leg does not fill, nothing more is sent: you choose Complete the rest or Close the filled legs.",
};

describe("smart execution confirm block", () => {
  it("shows the legs in the order they go, the slices, the labelled margin and the free balance after", () => {
    const html = renderToStaticMarkup(<SmartConfirmV2 smart={SMART} />);
    const t = text(html);
    expect(t.indexOf("Buy NIFTY 28 OCT 24300 PE")).toBeLessThan(t.indexOf("Sell NIFTY 28 OCT 24500 PE"));
    expect(t).toContain("hedge, goes first");
    expect(t).toContain("sent as 2 orders of at most 53 lots");
    expect(html).toContain('data-source="estimate"');
    expect(t).toContain("ATP's estimate, not your broker's number");
    expect(t).toContain("Saved by the hedge");
    expect(t).toContain("Free after this order");
    expect(t).toContain("sold legs close first");
    expect(ADVICE.test(t)).toBe(false);
  });

  it("names the broker when the margin is the broker's, and marks a balance that goes below zero", () => {
    const broker = { ...SMART, margin: { ...SMART.margin, source: "broker" as const, label: "Your broker's margin (upstox), with the hedge benefit", free_after: -100 } };
    const html = renderToStaticMarkup(<SmartConfirmV2 smart={broker} />);
    expect(html).toContain('data-source="broker"');
    expect(text(html)).toContain("Your broker's margin (upstox)");
    expect(html).toMatch(/data-smart="free-after"[^>]*>|text-warn[^"]*"[^>]*data-smart="free-after"/);
    expect(html).toContain("text-warn");
  });
});

const INCOMPLETE: BasketResult = {
  mode: "PAPER", status: "PARTIAL", state: "INCOMPLETE", actions: ["complete_rest", "close_filled"], reasons: [], placed: 3,
  message: "Incomplete: 3 of 4 legs filled. Complete the rest, or close the filled legs. Nothing happens until you choose.",
  next_steps: ["x"], filled_trade_ids: [11, 12, 13],
  legs: [
    { seq: 1, leg: 2, label: "A", side: "BUY", lots: 1, status: "PLACED", reasons: [], filled: { price: 40, quantity: 75, lots: 1 }, order_id: 1, trade_id: 11 },
    { seq: 2, leg: 3, label: "B", side: "BUY", lots: 1, status: "PLACED", reasons: [], filled: { price: 40, quantity: 75, lots: 1 }, order_id: 2, trade_id: 12 },
    { seq: 3, leg: 0, label: "C", side: "SELL", lots: 1, status: "PLACED", reasons: [], filled: { price: 80, quantity: 75, lots: 1 }, order_id: 3, trade_id: 13 },
    { seq: 4, leg: 1, label: "D", side: "SELL", lots: 1, status: "REFUSED", reasons: ["Broker refused"], filled: null, order_id: null, trade_id: null },
  ],
};

describe("the incomplete state", () => {
  it("offers Complete the leg(s) and Exit the filled leg(s) (owner's words, T1-4), only on the user's tap", () => {
    const html = renderToStaticMarkup(<IncompleteV2 message={INCOMPLETE.message!} filledTradeIds={[11, 12, 13]} onCompleteRest={() => undefined} />);
    const t = text(html);
    expect(t).toMatch(/Complete the legs?/);
    expect(t).toMatch(/Exit the filled legs?/);
    expect(t).toContain("Nothing happens until you choose");
    expect(t).toContain("sold legs first, then the hedges");
  });

  it("replaces 'Place the remaining legs' in the basket result and passes the filled legs", () => {
    const onRemaining = vi.fn();
    const els = elements(<ResultView result={INCOMPLETE} onRemaining={onRemaining} onEdit={() => undefined} onClear={() => undefined} onClose={() => undefined} />);
    const inc = els.find((e) => e.type === IncompleteV2);
    expect(inc).toBeTruthy();
    const props = inc!.props as { filledTradeIds: number[]; onCompleteRest: () => void };
    expect(props.filledTradeIds).toEqual([11, 12, 13]);
    props.onCompleteRest();
    expect(onRemaining).toHaveBeenCalledTimes(1);
    expect(els.some((e) => (e.props as Record<string, unknown>)["data-basket"] === "remaining")).toBe(false);
    // Without the flag (no state), the old result is unchanged.
    const old = elements(<ResultView result={{ ...INCOMPLETE, state: undefined }} onRemaining={onRemaining} onEdit={() => undefined} onClear={() => undefined} onClose={() => undefined} />);
    expect(old.some((e) => e.type === IncompleteV2)).toBe(false);
    expect(old.some((e) => (e.props as Record<string, unknown>)["data-basket"] === "remaining")).toBe(true);
  });

  it("never closes or places anything by itself", () => {
    expect(source).not.toMatch(/useEffect\([^)]*closeFilled/);
    expect(source).not.toMatch(/place\(/);
  });
});

describe("the delivery warning", () => {
  const view: SettlementView = {
    warnings: [{ trade_id: 7, label: "RELIANCE 30 OCT 2900 CE", underlying: "RELIANCE", expiry: "2026-10-30", days_to_expiry: 2, state: "ITM",
      delivery_value: 1450000, quantity: 500, obligation: "deliver",
      message: "RELIANCE 30 OCT 2900 CE is in the money and expires in 2 days. Held to expiry it settles by delivery: you would deliver 500 shares of RELIANCE (about ₹1,450,000)." }],
    unpriced: [], settings: { days_before: 4, band_pct: 2, telegram: true }, note: "Information only: nothing is closed for you.", as_of: "",
  };
  it("lists each position with its delivery value", () => {
    const t = text(renderToStaticMarkup(<SettlementListV2 view={view} />));
    expect(t).toContain("Delivery ahead: 1 position");
    expect(t).toContain("settles by delivery");
    expect(t).toContain("Delivery value");
    expect(ADVICE.test(t)).toBe(false);
  });
  it("shows nothing when nothing is flagged", () => {
    expect(renderToStaticMarkup(<SettlementListV2 view={{ ...view, warnings: [] }} />)).toBe("");
  });
});

describe("his own smart-execution settings", () => {
  it("reads defensively and refuses values out of bounds in plain words", async () => {
    const { readSmart, smartFormOf, smartOf, SMART_DEFAULTS } = await import("../settings/smartExecutionSettings");
    expect(readSmart({ settlement_days_before: "x" })).toEqual(SMART_DEFAULTS);
    const ok = smartOf({ ...smartFormOf(SMART_DEFAULTS), settlement_days_before: "6", protection_band_pct: "1.5" });
    expect(ok.problems).toEqual([]);
    expect(ok.value.settlement_days_before).toBe(6);
    expect(ok.value.protection_band_pct).toBe(1.5);
    expect(smartOf({ ...smartFormOf(SMART_DEFAULTS), settlement_days_before: "2.5" }).problems[0]).toContain("whole number");
    expect(smartOf({ ...smartFormOf(SMART_DEFAULTS), settlement_band_pct: "50" }).problems[0]).toContain("from 0 to 20");
  });
});
