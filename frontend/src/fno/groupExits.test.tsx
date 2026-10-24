import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { PositionRowV2, type RowActions } from "../trading/PositionsV2";
import positionsSource from "../trading/PositionsV2.tsx?raw";
import sheetSource from "./GroupExitsV2.tsx?raw";
import { heldRows } from "../trading/model";
import { exposure, trade } from "../trading/trading.fixture";
import { HEDGE_NOTE, confirmCopy, resultText, runExit, type GroupExitApi, type GroupExitResult } from "./groupExits";

/** TT-8: a basket's legs as one group - "Exit this leg" / "Exit the whole group", the hedge warning, never a block. */

const noop = () => undefined;
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");

function row(groupLeg?: { hedge: boolean }, actions: Partial<RowActions> = {}) {
  const p = trade();
  const [r] = heldRows([p], exposure());
  const a: RowActions = { exitNowOn: true, canTrade: true, onExit: noop, onCheck: noop, ...actions };
  return renderToStaticMarkup(<MemoryRouter><ul><PositionRowV2 row={r} position={p} actions={a} groupLeg={groupLeg} /></ul></MemoryRouter>);
}

const leg = (over: Partial<GroupExitResult["legs"][number]> = {}) =>
  ({ trade_id: 1, symbol: "NIFTY 25000 CE", closed: true, exit_price: 80, pnl: 250, warnings: [], ...over });

function fakeApi(answers: { leg?: GroupExitResult[]; group?: GroupExitResult; fail?: unknown } = {}) {
  const calls: Array<[string, number, boolean?]> = [];
  const api: GroupExitApi = {
    exitLeg: vi.fn(async (id: number, confirm: boolean) => {
      calls.push(["leg", id, confirm]);
      if (answers.fail) throw answers.fail;
      return (answers.leg ?? []).shift() ?? ({ status: "CLOSED", legs: [leg({ trade_id: id })] } as GroupExitResult);
    }),
    exitGroup: vi.fn(async (id: number) => {
      calls.push(["group", id]);
      if (answers.fail) throw answers.fail;
      return answers.group ?? ({ status: "CLOSED", legs: [leg(), leg({ trade_id: 2, symbol: "NIFTY 25200 CE", pnl: -50 })] } as GroupExitResult);
    }),
  };
  return { api, calls };
}

describe("TT-8 the confirm sheet's words", () => {
  it("a leg says only that leg closes; a hedge carries the warning and 'anyway'", () => {
    const plain = confirmCopy({ kind: "leg", tradeId: 1, symbol: "NIFTY 25000 CE", hedge: false, warning: null });
    expect(plain.title).toBe("Exit this leg?");
    expect(plain.body).toContain("The other legs of the group stay open");
    expect(plain.warning).toBeNull();
    expect(plain.confirm).toBe("Exit this leg");
    const hedge = confirmCopy({ kind: "leg", tradeId: 1, symbol: "NIFTY 25200 CE", hedge: true, warning: null });
    expect(hedge.warning).toBe(HEDGE_NOTE);
    expect(hedge.confirm).toBe("Exit this leg anyway");
    expect(HEDGE_NOTE).toMatch(/You can still exit it/);
  });

  it("the whole group says the sold legs close first", () => {
    const copy = confirmCopy({ kind: "group", tradeId: 1, title: "Basket · 4 legs", legs: 4 });
    expect(copy.title).toBe("Exit the whole group?");
    expect(copy.body).toBe("All 4 legs close at the current price: the sold legs first, then the legs that protect them.");
    expect(copy.confirm).toBe("Exit the whole group");
  });

  it("no buy / sell advice words, no internal terms", () => {
    const all = [HEDGE_NOTE, ...["leg", "group"].flatMap((k) => Object.values(confirmCopy(k === "leg"
      ? { kind: "leg", tradeId: 1, symbol: "X", hedge: true, warning: null } : { kind: "group", tradeId: 1, title: "Basket", legs: 2 })))].join(" ");
    expect(all).not.toMatch(/\b(target|stop-loss|trade_id|leg_role|group_meta|basket_group|API|null)\b/i);
  });
});

describe("TT-8 one tap on confirm", () => {
  it("a plain leg goes without confirm and closes", async () => {
    const { api, calls } = fakeApi();
    const step = await runExit({ kind: "leg", tradeId: 7, symbol: "NIFTY 25000 CE", hedge: false, warning: null }, false, api);
    expect(calls).toEqual([["leg", 7, false]]);
    expect(step).toEqual({ kind: "done", ok: true, text: expect.stringContaining("NIFTY 25000 CE closed") });
  });

  it("a hedge the page knows goes with confirm", async () => {
    const { api, calls } = fakeApi();
    await runExit({ kind: "leg", tradeId: 3, symbol: "NIFTY 25200 CE", hedge: true, warning: null }, false, api);
    expect(calls).toEqual([["leg", 3, true]]);
  });

  it("the server's warning keeps the sheet open; the second tap confirms", async () => {
    const { api, calls } = fakeApi({ leg: [{ status: "CONFIRM", warning: "NIFTY 25200 CE protects your sold leg", legs: [] }] });
    const ask = { kind: "leg" as const, tradeId: 3, symbol: "NIFTY 25200 CE", hedge: false, warning: null };
    expect(await runExit(ask, false, api)).toEqual({ kind: "warn", warning: "NIFTY 25200 CE protects your sold leg" });
    const done = await runExit(ask, true, api);
    expect(calls).toEqual([["leg", 3, false], ["leg", 3, true]]);
    expect(done.kind).toBe("done");
  });

  it("the whole group: one call, the result in rupees, a leg left open is named", async () => {
    const { api, calls } = fakeApi({ group: { status: "PARTIAL", legs: [leg({ pnl: 300 }), leg({ trade_id: 2, symbol: "NIFTY 25200 CE", closed: false, pnl: null })] } });
    const step = await runExit({ kind: "group", tradeId: 1, title: "Basket · 2 legs", legs: 2 }, false, api);
    expect(calls).toEqual([["group", 1]]);
    expect(step.kind === "done" && step.text).toMatch(/NIFTY 25000 CE closed: \+₹300\. 1 leg is still open: NIFTY 25200 CE\./);
  });

  it("a refusal from the server is a plain line", async () => {
    const { api } = fakeApi({ fail: new ApiError(409, "No recent price for NIFTY 25000 CE. Nothing was closed") });
    const step = await runExit({ kind: "group", tradeId: 1, title: "Basket", legs: 2 }, false, api);
    expect(step).toEqual({ kind: "done", ok: false, text: expect.stringContaining("The group was not closed.") });
  });

  it("the result line adds the legs' P&L", () => {
    expect(resultText({ status: "CLOSED", legs: [leg({ pnl: 250 }), leg({ trade_id: 2, pnl: -50 })] })).toMatch(/^2 legs closed: \+₹200\.$/);
  });
});

describe("TT-8 on My positions", () => {
  it("a basket leg shows 'Exit this leg' in place of 'Exit now'; other rows are as before", () => {
    const onLeg = vi.fn();
    const html = row({ hedge: true }, { group: { onLeg, onGroup: noop } });
    expect(text(html)).toContain("Exit this leg");
    expect(html).toContain('data-testid="exit-leg"');
    expect(html).not.toContain('data-testid="exit-now"');
    const plain = row();
    expect(plain).toContain('data-testid="exit-now"');
    expect(plain).not.toContain("exit-leg");
  });

  it("the buttons are never disabled, and only a basket gets them (a strategy keeps Exit now)", () => {
    const code = positionsSource.replace(/\/\*[\s\S]*?\*\//g, "");
    expect(code).toMatch(/group\.kind === "basket" && !!actions\.group/);
    expect(code).toMatch(/data-testid="exit-group"/);
    for (const src of [code, sheetSource]) {
      for (const button of src.match(/<button[^>]*data-testid="exit-(leg|group)"[^>]*>/g) ?? []) expect(button).not.toMatch(/disabled/);
    }
    expect(code).toMatch(/<GroupExitSheetV2/);
  });
});
