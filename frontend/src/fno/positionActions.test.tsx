import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { PositionRowV2, type RowActions } from "../trading/PositionsV2";
import { heldRows } from "../trading/model";
import { exposure, trade } from "../trading/trading.fixture";
import sheetSource from "./PositionActionsV2.tsx?raw";
import panelSource from "../terminal/PanelsV2.tsx?raw";
import { CARD_LINE_WORDS, wordCount } from "../ui/textBudget";
import { ExitPartResultLine, ModifyFormV2 } from "./PositionActionsV2";
import {
  NO_PRICE_LINE, canExitPart, canReverse, clampLots, exitPartCopy, exitPartPieces, exitPartText, modifyAsk, modifyChanges, modifyFields,
  modifyStart, newRef, openLots, reverseCopy, reverseNeedsStop, reverseStopLabel, runAction, waitingLine, waitingQuantity,
  type ExitPartResult, type PositionActionsApi, type ReverseResult, type WaitingOrder,
} from "./positionActions";

/** Terminal T2: TT-9 "Exit part" / "Reverse" and TT-10 Modify / Cancel of a waiting order. */

const noop = () => undefined;
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const option = (extra = {}) => trade({ symbol: "NIFTY 25000 CE", instrument_kind: "OPTION", quantity: 225, lot_size: 75, ...extra });

function fakeApi(answers: { part?: ExitPartResult[]; reverse?: ReverseResult; fail?: unknown } = {}) {
  const calls: unknown[][] = [];
  const api: PositionActionsApi = {
    exitPart: vi.fn(async (id: number, lots: number, ref: string, confirm: boolean) => {
      calls.push(["part", id, lots, ref, confirm]);
      if (answers.fail) throw answers.fail;
      const done: ExitPartResult = { status: "CLOSED", closed: { trade_id: 9, symbol: "NIFTY 25000 CE", lots, closed: true, exit_price: 120, pnl: 1500 }, remaining_lots: 2 };
      return (answers.part ?? []).shift() ?? done;
    }),
    reverse: vi.fn(async (id: number, body: Parameters<PositionActionsApi["reverse"]>[1]): Promise<ReverseResult> => {
      calls.push(["reverse", id, body]);
      if (answers.fail) throw answers.fail;
      return answers.reverse ?? { status: "REVERSED", text: "NIFTY 25000 CE closed and the opposite position is open." };
    }),
    waiting: vi.fn(async () => []),
    modify: vi.fn(async () => { throw new Error("unused"); }),
    cancel: vi.fn(async () => { throw new Error("unused"); }),
  };
  return { api, calls };
}

describe("TT-9 pure parts", () => {
  it("counts whole lots and keeps the stepper between 1 and what is held", () => {
    expect(openLots(option())).toBe(3);
    expect(openLots(trade({ quantity: 40, lot_size: null }))).toBe(40);
    expect(clampLots(0, 3)).toBe(1);
    expect(clampLots(7, 3)).toBe(3);
    expect(clampLots(Number.NaN, 3)).toBe(1);
  });

  it("Exit part needs more than one lot; Reverse an F&O position; never on a strategy structure or LIVE", () => {
    expect(canExitPart(option())).toBe(true);
    expect(canExitPart(option({ quantity: 75 }))).toBe(false);
    expect(canExitPart(option({ leg_group_id: "g1" }))).toBe(false);
    expect(canExitPart(option({ mode: "LIVE" }))).toBe(false);
    expect(canReverse(option({ quantity: 75 }))).toBe(true);
    expect(canReverse(trade())).toBe(false);                           // a stock position: no ticket to reverse into
    expect(reverseNeedsStop(option())).toBe(true);                     // a bought option reverses into a sold one
    expect(reverseNeedsStop(option({ direction: "SHORT" }))).toBe(false);
    expect(reverseNeedsStop(option({ direction: "SHORT", instrument_kind: "FUTURE" }))).toBe(true);
  });

  it("the confirm words are one short line; the rest (the no-price line once) is in Details", () => {
    const part = exitPartCopy("NIFTY 25000 CE", 1, 3);
    expect(part.body).toBe("1 lot of NIFTY 25000 CE closes now; 2 lots stay open.");
    expect(part.details).toContain("The lots that stay open keep the same stop and target.");
    expect(part.confirm).toBe("Exit 1 lot");
    expect(exitPartCopy("X", 3, 3).body).toBe("All 3 lots of X close now.");
    const rev = reverseCopy("NIFTY 25000 CE", 3, true);
    expect(rev.body).toBe("All 3 lots of NIFTY 25000 CE close, then a short of 3 lots opens.");
    expect(rev.details.join(" ")).toContain("your position stays closed and you see why");
    for (const c of [part, rev, exitPartCopy("X", 3, 3)]) {
      expect(wordCount(c.body)).toBeLessThanOrEqual(CARD_LINE_WORDS);
      expect(c.body.split(". ")).toHaveLength(1);
      expect(c.details.filter((l) => l === NO_PRICE_LINE)).toHaveLength(1);
    }
    for (const words of [part.title, part.body, rev.title, rev.body]) expect(words).not.toMatch(/target price|stop-loss at|client_ref|idempotency/i);
  });

  it("the Reverse stop box states its unit and basis (the contract's own price per unit, as the ticket takes it)", () => {
    expect(reverseStopLabel(option()).label).toBe("Stop-loss · premium, ₹ per unit");
    expect(reverseStopLabel(option({ instrument_kind: "FUTURE" })).label).toBe("Stop-loss · future price, ₹ per unit");
    expect(reverseStopLabel(option()).detail).toMatch(/not the underlying, not a combined ₹ amount/);
  });

  it("the result line names the lots, the price and what is left", () => {
    const r: ExitPartResult = { status: "CLOSED", closed: { trade_id: 2, symbol: "X", lots: 2, closed: true, exit_price: 120, pnl: 1500 }, remaining_lots: 1 };
    expect(exitPartText(r)).toMatch(/^2 lots of X closed at .*120.*: \+₹1,500\. 1 lot still open\.$/);
    expect(exitPartText({ ...r, closed: { ...r.closed!, pnl: -400 } })).toContain(": −₹400.");
    expect(exitPartPieces(r)).toEqual({ before: expect.stringMatching(/closed at .*: $/), pnl: 1500, after: ". 1 lot still open." });
  });

  it("a new reference each time", () => {
    expect(newRef()).not.toBe(newRef());
    expect(newRef()).toMatch(/^[A-Za-z0-9]{8,64}$/);
  });
});

describe("TT-9 the confirm tap", () => {
  it("Exit part sends the lots with the sheet's own reference; a retry reuses it", async () => {
    const { api, calls } = fakeApi();
    const ask = { kind: "part" as const, tradeId: 4, symbol: "NIFTY 25000 CE", lots: 2, ref: "ref-1234567" };
    const step = await runAction(ask, false, api);
    await runAction(ask, false, api);
    expect(calls).toEqual([["part", 4, 2, "ref-1234567", false], ["part", 4, 2, "ref-1234567", false]]);
    expect(step).toMatchObject({ kind: "done", ok: true, text: expect.stringContaining("2 lots of NIFTY 25000 CE closed") });
  });

  it("a hedge warning keeps the sheet open; the next tap confirms", async () => {
    const { api, calls } = fakeApi({ part: [{ status: "CONFIRM", warning: "It protects your sold leg", closed: null, remaining_lots: 3 }] });
    const ask = { kind: "part" as const, tradeId: 4, symbol: "X", lots: 1, ref: "ref-1234567" };
    expect(await runAction(ask, false, api)).toEqual({ kind: "warn", warning: "It protects your sold leg" });
    await runAction(ask, true, api);
    expect(calls[1]).toEqual(["part", 4, 1, "ref-1234567", true]);
  });

  it("Reverse with the new side refused: the position is closed and the line says why", async () => {
    const { api, calls } = fakeApi({ reverse: { status: "EXITED", text: "X closed. The opposite position was not opened: trading is stopped" } });
    const step = await runAction({ kind: "reverse", tradeId: 5, symbol: "X", ref: "ref-abcdefgh", stopLoss: 130 }, false, api);
    expect(calls[0]).toEqual(["reverse", 5, { client_ref: "ref-abcdefgh", stop_loss: 130, confirm: false }]);
    expect(step).toEqual({ kind: "done", ok: false, text: "X closed. The opposite position was not opened: trading is stopped" });
  });

  it("a refusal from the server is a plain line, never a crash", async () => {
    const { api } = fakeApi({ fail: new ApiError(422, "You hold 3 lots of X. Choose between 1 and 3 lots to exit - nothing was closed.") });
    const step = await runAction({ kind: "part", tradeId: 1, symbol: "X", lots: 4, ref: "ref-1234567" }, false, api);
    expect(step.kind).toBe("done");
    expect(step.kind === "done" && step.ok).toBe(false);
    expect(step.kind === "done" && step.text).toContain("X: nothing was closed.");
  });
});

describe("TT-9 the position row", () => {
  function row(p = option(), actions: Partial<RowActions> = {}) {
    const [r] = heldRows([p], exposure());
    const a: RowActions = { exitNowOn: true, canTrade: true, onExit: noop, onCheck: noop, ...actions };
    return text(renderToStaticMarkup(<MemoryRouter><ul><PositionRowV2 row={r} position={p} actions={a} /></ul></MemoryRouter>));
  }
  const part = { onPart: noop, onReverse: noop };

  it("shows Exit part and Reverse next to Exit now only when the panel passes them", () => {
    const html = row(option(), { part });
    expect(html).toContain("Exit now");
    expect(html).toContain("Exit part");
    expect(html).toContain("Reverse");
    expect(row(option())).not.toContain("Exit part");
    expect(row(option({ quantity: 75 }), { part })).not.toContain("Exit part");   // one lot: Exit now is the exit
    expect(row(option(), { part, canTrade: false })).not.toContain("Reverse");
  });

  it("the buttons and the stepper are never disabled; every action has a confirm step", () => {
    const [r] = heldRows([option()], exposure());
    const html = renderToStaticMarkup(<MemoryRouter><ul><PositionRowV2 row={r} position={option()} actions={{ exitNowOn: true, canTrade: true, onExit: noop, onCheck: noop, part }} /></ul></MemoryRouter>);
    expect(html).not.toMatch(/data-testid="(exit-part|reverse)"[^>]*\sdisabled=""/);
    expect(sheetSource).toContain("Keep it open");
    expect(sheetSource).toMatch(/window\.confirm\(`Cancel this waiting order\?/);
    expect(sheetSource).toMatch(/window\.confirm\(`Change this waiting order\?/);
    expect(panelSource).toContain("<PositionActionSheetV2");
    expect(panelSource).toMatch(/part: fnoOn && canTrade \?/);               // behind the PAPER account + the trader role
  });

  it("text sizes come from the tokens only", () => {
    expect(sheetSource).not.toMatch(/text-(xs|sm|base|lg|xl)\b/);
    expect(sheetSource).not.toMatch(/#[0-9a-fA-F]{3,6}\b/);
  });
});

describe("TT-10 waiting orders", () => {
  const order = (extra: Partial<WaitingOrder> = {}): WaitingOrder => ({
    id: 1, purpose: "ENTRY", kind: "LIMIT", trade_id: null, symbol: "NIFTY 25000 CE", side: "BUY", quantity: 75, order_type: "LIMIT",
    price: 95, trigger_price: null, trigger: { source: "PREMIUM", symbol: "NIFTY 25000 CE", level: 95, side: "BELOW", mode: "TOUCH", timeframe_minutes: null },
    status: "RESTING", status_words: "Waiting for your price", note: null, ...extra,
  });

  it("one plain line per order, in lots; the quantity is in Details", () => {
    expect(waitingLine(order({ lots: 1, side: "SELL", price: 62 }))).toBe("1 lot · NIFTY 25000 CE · sell at 62.00");
    expect(waitingLine(order({ lots: 2, quantity: 150 }))).toMatch(/^2 lots · NIFTY 25000 CE · buy at .*95/);
    expect(waitingLine(order({ lots: 2 }))).not.toMatch(/\b75\b/);
    expect(waitingLine(order({ order_type: "SL-M", price: null, trigger_price: 96 }))).toMatch(/· buy once .*96.* trades$/);
    expect(waitingQuantity(order({ lots: 2, quantity: 150 }))).toBe("Quantity: 150 units (2 lots of 75).");
    expect(waitingLine(order({ purpose: "EXIT", kind: "STOP", order_type: "SL-M" }))).toMatch(/^Stop on NIFTY 25000 CE at .*95/);
    expect(waitingLine(order({ purpose: "EXIT", kind: "TARGET", trigger: { ...order().trigger, source: "SPOT", side: "ABOVE", level: 25100 } })))
      .toMatch(/^Target on NIFTY 25000 CE underlying at or above /);
  });

  it("Modify asks an entry for its price / trigger / lots and an exit for its level only", () => {
    expect(modifyFields(order())).toEqual(["price", "lots"]);
    expect(modifyFields(order({ order_type: "SL" }))).toEqual(["price", "trigger_price", "lots"]);
    expect(modifyFields(order({ order_type: "SL-M" }))).toEqual(["trigger_price", "lots"]);
    expect(modifyFields(order({ purpose: "EXIT", kind: "STOP" }))).toEqual(["level"]);
    expect(modifyFields(order({ purpose: "EXIT", trigger: { ...order().trigger, source: "SPOT" } }))).toEqual(["spot_level"]);
  });

  it("Modify opens with the current values; only a changed value is sent; nothing changed means Save stays off", () => {
    const o = order({ lots: 1, order_type: "SL", trigger_price: 96 });
    const start = modifyStart(o);
    expect(start).toEqual({ price: "95", trigger_price: "96", lots: "1" });
    expect(modifyChanges(start, start)).toBe("same");
    expect(modifyChanges(start, { ...start, price: "95.00" })).toBe("same");
    expect(modifyChanges(start, { ...start, lots: "2" })).toEqual({ lots: 2 });
    expect(modifyChanges(start, { ...start, price: "" })).toBeNull();
    expect(modifyStart(order({ purpose: "EXIT", kind: "STOP" }))).toEqual({ level: "95" });
  });

  it("the form shows the current values and Save is disabled until one changes", () => {
    const api = fakeApi().api;
    const html = renderToStaticMarkup(<ModifyFormV2 order={order({ lots: 1 })} api={api} onDone={noop} />);
    expect(html).toContain(`data-field="price" value="95"`);
    expect(html).toContain(`data-field="lots" value="1"`);
    expect(html).toMatch(/disabled=""[^>]*data-testid="waiting-save"/);
    expect(sheetSource).toContain('disabled={busy || changes === "same"}');
  });

  it("only the typed values are sent, as numbers; a bad value sends nothing", () => {
    expect(modifyAsk({ price: "92", lots: "2" })).toEqual({ price: 92, lots: 2 });
    expect(modifyAsk({ price: "", lots: "" })).toBeNull();
    expect(modifyAsk({ price: "abc" })).toBeNull();
    expect(modifyAsk({ level: "-5" })).toBeNull();
  });
});

describe("TT-9 the sheet (UX-11)", () => {
  it("one visible line; the longer words in ONE Details per dialog; the stop box says its unit", () => {
    expect(sheetSource).not.toContain(NO_PRICE_LINE);                          // said once, from the copy, inside Details
    expect(sheetSource.match(/<Details id=\{`terminal\.\$\{ask\.kind/g)).toHaveLength(1);
    expect(sheetSource).toContain("copy.details.map");
    expect(sheetSource).toContain("{stopLabel.label}");
    expect(sheetSource).toMatch(/<Details id="terminal\.waiting-order"/);
  });

  it("the closed lots' profit / loss is the shared signed value", async () => {
    const { api } = fakeApi();
    const step = await runAction({ kind: "part", tradeId: 4, symbol: "NIFTY 25000 CE", lots: 1, ref: "ref-1234567" }, false, api);
    expect(step.kind === "done" && step.pieces).toBeTruthy();
    if (step.kind !== "done" || !step.pieces) return;
    const html = renderToStaticMarkup(<ExitPartResultLine pieces={step.pieces} />);
    expect(html).toContain('data-signed="up"');
    expect(text(html)).toMatch(/1 lot of NIFTY 25000 CE closed at .*: ▲ \+₹1,500 \. 2 lots still open\./);
    const loss = renderToStaticMarkup(<ExitPartResultLine pieces={{ before: "X closed: ", pnl: -400, after: "." }} />);
    expect(loss).toContain('data-signed="down"');
    expect(text(loss)).toContain("▼ −₹400");
  });
});
