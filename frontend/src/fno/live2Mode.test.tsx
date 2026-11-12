import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { BasketPreview, BasketResult } from "./api";
import { ConfirmView } from "./BasketPanelV2";
import { resultHeadline } from "./basketModel";
import { ConfirmModeLine, OrderModeChoice, liveIsOff } from "./OrderModeV2";
import { canReverse, positionActionsApi, reverseCopy, runAction, type PositionActionsApi, type ReverseResult } from "./positionActions";
import { LIVE_OFF_TEXT } from "./ticketModel";
import type { TradeRecord } from "../types";

/**
 * LIVE-2 (owner LIVE-1, binding): the basket's and Reverse's PAPER / LIVE choice is the ticket's control - PAPER unless
 * he picks LIVE; while LIVE is off the one sentence shows and the action cannot go; the confirm step names the mode.
 */
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const noop = () => undefined;

function choice(mode: "PAPER" | "LIVE", live: { open: boolean; text: string | null } | null) {
  return renderToStaticMarkup(<OrderModeChoice mode={mode} onMode={noop} live={live} testId="basket-mode" />);
}

function preview(over: Partial<BasketPreview> = {}): BasketPreview {
  return { mode: "PAPER", practice: "", legs: [], refusals: [], totals: null, totals_errors: [], can_execute: true, product: "MIS", smart: null,
    confirm_label: "Place 2 PAPER orders, hedge legs first", as_of: "2026-11-12T05:00:00+00:00", ...over } as BasketPreview;
}

describe("LIVE-2 mode control", () => {
  it("is the ticket's Mode choice, PAPER selected unless he picks LIVE", () => {
    const html = choice("PAPER", null);
    expect(html).toContain('role="radiogroup" aria-label="Mode" data-ticket="basket-mode"');
    expect(html).toContain('aria-checked="true" data-option="PAPER"');
    expect(html).toContain('aria-checked="false" data-option="LIVE"');
    expect(html).not.toContain("data-live-off");
  });

  it("LIVE while the switch is off: one plain sentence; unknown is never 'off'", () => {
    expect(text(choice("LIVE", { open: false, text: LIVE_OFF_TEXT }))).toContain(LIVE_OFF_TEXT);
    expect(choice("LIVE", { open: true, text: null })).not.toContain("data-live-off");
    expect(choice("PAPER", { open: false, text: LIVE_OFF_TEXT })).not.toContain("data-live-off");     // PAPER is untouched
    expect(liveIsOff("LIVE", { open: false, text: null })).toBe(true);
    expect(liveIsOff("LIVE", null)).toBe(false);
    expect(liveIsOff("PAPER", { open: false, text: null })).toBe(false);
  });

  it("the confirm step names the mode in large text, from the server's answer", () => {
    expect(renderToStaticMarkup(<ConfirmModeLine mode="LIVE" what="orders" testId="basket" />))
      .toMatch(/class="text-t18 font-semibold text-warn" data-confirm-mode="basket">LIVE orders · real money</);
    const live = renderToStaticMarkup(<ConfirmView preview={preview({ mode: "LIVE", confirm_label: "Place 2 LIVE orders, hedge legs first" })} busy={false}
      onBack={noop} onPlace={noop} showMode />);
    expect(live).toContain('data-confirm-mode="basket">LIVE orders · real money');
    expect(text(live)).toContain("Place 2 LIVE orders, hedge legs first");
    const paper = renderToStaticMarkup(<ConfirmView preview={preview()} busy={false} onBack={noop} onPlace={noop} showMode />);
    expect(paper).toContain('data-confirm-mode="basket">PAPER orders · practice');
    expect(renderToStaticMarkup(<ConfirmView preview={preview()} busy={false} onBack={noop} onPlace={noop} />)).not.toContain("data-confirm-mode");
  });

  it("the basket's result says where the legs went", () => {
    const r = { mode: "LIVE", status: "PLACED", reasons: [], legs: [{}, {}], placed: 2, next_steps: [] } as unknown as BasketResult;
    expect(resultHeadline(r)).toBe("All 2 legs sent to your broker (LIVE), hedge legs first.");
    expect(resultHeadline({ ...r, mode: "PAPER" })).toBe("All 2 legs placed in practice mode, hedge legs first.");
  });

  it("Reverse: the new side's mode is named and sent only when he picked LIVE", async () => {
    expect(reverseCopy("X", 1, true, "LIVE").confirm).toBe("Reverse · LIVE");
    expect(reverseCopy("X", 1, true, "LIVE").body).toMatch(/opens as a LIVE order at your broker\.$/);
    expect(reverseCopy("X", 1, true).confirm).toBe("Reverse");
    const calls: unknown[] = [];
    const api = { ...positionActionsApi, reverse: vi.fn(async (_id: number, body: unknown): Promise<ReverseResult> => {
      calls.push(body);
      return { status: "REVERSED", text: "X closed and the opposite position is open." };
    }) } as PositionActionsApi;
    await runAction({ kind: "reverse", tradeId: 5, symbol: "X", ref: "ref-abcdefgh", mode: "LIVE" }, false, api);
    await runAction({ kind: "reverse", tradeId: 5, symbol: "X", ref: "ref-abcdefgh", mode: "PAPER" }, false, api);
    expect(calls).toEqual([{ client_ref: "ref-abcdefgh", stop_loss: undefined, confirm: false, mode: "LIVE" },
      { client_ref: "ref-abcdefgh", stop_loss: undefined, confirm: false }]);
  });

  it("Reverse is offered on a LIVE F&O position too (its exit goes to its own broker)", () => {
    const base = { quantity: 75, lot_size: 75, instrument_kind: "OPTION", leg_group_id: null, symbol: "NIFTY25000CE" } as unknown as TradeRecord;
    expect(canReverse({ ...base, mode: "LIVE" } as TradeRecord)).toBe(true);
    expect(canReverse({ ...base, mode: "PAPER" } as TradeRecord)).toBe(true);
    expect(canReverse({ ...base, mode: "LIVE", leg_group_id: "g1" } as TradeRecord)).toBe(false);
  });
});
