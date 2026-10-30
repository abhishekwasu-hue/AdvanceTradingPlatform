import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { api as appApi } from "../api/client";
import type { MyDayPosition } from "../dashboard/myDay";
import ChainTableV2 from "../optionChain/ChainTableV2";
import { liveChain } from "../optionChain/chain.fixture";
import { DEFAULT_COLUMNS } from "../optionChain/model";
import type { Evaluation } from "../optionsBuilder/model";
import { PositionList, PositionRowV2, type RowActions } from "../trading/PositionsV2";
import { heldRows } from "../trading/model";
import { exposure, trade } from "../trading/trading.fixture";
import type { BasketNumbers, MarginPair, WhatIf } from "./api";
import { BasketStore, type BasketLeg } from "./basket";
import { BasketBody, whatIfLegs, type BodyHandlers } from "./BasketPanelV2";
import basketSource from "./BasketPanelV2.tsx?raw";
import { groupExitApi } from "./groupExits";
import {
  NOT_ATOMIC, existingExits, exitWords, netWords, pendingStore, pendingText, rollLegs, runRoll, writePending, type RollExits, type RollPreview,
} from "./roll";
import rollSheetSource from "./RollV2.tsx?raw";
import { areaShort, areaWords, areasByTrade, chainAreaMarks, shortStrikeAreas } from "./strikeArea";
import pageSource from "../terminal/TerminalPageV2.tsx?raw";

/**
 * TERM-2 step 4 (owner order ATP_ORDER_TERM2.md, "Beyond the best"), each behind its own flag with ui_v2 + terminal:
 * R1 Roll (the preview, then the EXISTING exit - never disabled - then the new legs in the one basket, two confirmed
 * steps), R2 the what-if payoff, R3 a short strike's distance to the nearest area, R4 the margin pair.
 */
const noop = () => undefined;
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const ADVICE = /\b(buy now|sell now|should|recommend\w*|advice|advise|target\w*|stop[- ]?loss|guarantee\w*)\b/i;

function preview(over: Partial<RollPreview> = {}): RollPreview {
  return {
    mode: "PAPER", ok: true, errors: [], expiry: "2026-11-10", underlying: "NIFTY", spot: 24480,
    exit: [{ trade_id: 7, label: "NIFTY 03 NOV 24400 PE", side: "SELL", lots: 1, units: 75, price: 60, price_source: "live" }],
    legs: [{ underlying: "NIFTY", expiry: "2026-11-10", strike: 24400, right: "PE", side: "SELL", lots: 1, price: 110, stop_loss: null, label: "NIFTY 10 NOV 24400 PE" }],
    numbers: { net_premium: 8250, extremes: { max_profit: 8250, max_loss: null, unbounded_profit: false, unbounded_loss: true }, breakevens: [24290], lot_sizes: [75] },
    numbers_errors: [], exit_value: -4500, net: 3750, atomic: false,
    steps: "Step 1 closes these legs through Exit now. Step 2 puts the new legs in your basket: you review and place them.", as_of: "", ...over,
  };
}

function fakeExits(over: Partial<RollExits> = {}) {
  const exitNow = vi.fn(async () => ({ closed: [{ exit_price: 60, pnl: 1500, exit_reason: "MANUAL" }], warnings: [] }));
  const exitGroup = vi.fn(async () => ({ status: "CLOSED" as const, legs: [{ trade_id: 7, symbol: "NIFTY 24400 PE", closed: true, exit_price: 60, pnl: 1500, warnings: [] }] }));
  return { exitNow, exitGroup, ...over } as RollExits & { exitNow: typeof exitNow; exitGroup: typeof exitGroup };
}

describe("R1 roll: two confirmed steps through the existing exit", () => {
  it("step 1 is the existing Exit now of the position; then the new legs go into the basket and the roll is marked half done", async () => {
    writePending(null);
    const exits = fakeExits();
    const store = new BasketStore([]);
    const out = await runRoll({ kind: "single", tradeId: 7, title: "NIFTY 03 NOV 24400 PE" }, preview(), exits, store);
    expect(exits.exitNow).toHaveBeenCalledWith(7);
    expect(exits.exitGroup).not.toHaveBeenCalled();
    expect(out).toMatchObject({ ok: true, toBasket: true });
    expect(out.text).toContain("Step 2: review and place the new legs in your basket.");
    expect(store.snapshot().map((l) => [l.side, l.strike, l.right, l.expiry, l.lots, l.stopLoss])).toEqual([["SELL", 24400, "PE", "2026-11-10", 1, null]]);
    const pending = pendingStore.snapshot();
    expect(pending && pendingText(pending)).toBe("Roll half done: NIFTY 03 NOV 24400 PE is closed. The 1 new leg is in your basket (2026-11-10), not placed yet.");
    writePending(null);
  });

  it("a basket's legs close with the existing group exit (sold legs first); a structure with its first leg's Exit now", async () => {
    const exits = fakeExits();
    await runRoll({ kind: "group", tradeIds: [7, 8], title: "Basket", basket: true }, preview(), exits, new BasketStore([]));
    expect(exits.exitGroup).toHaveBeenCalledWith(7);
    const s = fakeExits();
    await runRoll({ kind: "group", tradeIds: [9, 10], title: "Iron Condor", basket: false }, preview(), s, new BasketStore([]));
    expect(s.exitNow).toHaveBeenCalledWith(9);
    writePending(null);
  });

  it("the default exits ARE the existing calls - Exit now and the group exit, never a buy leg in a basket", async () => {
    const now = vi.spyOn(appApi, "exitNow").mockResolvedValue({ closed: [], warnings: [] });
    const group = vi.spyOn(groupExitApi, "exitGroup").mockResolvedValue({ status: "CLOSED", legs: [] });
    await existingExits.exitNow(5);
    await existingExits.exitGroup(6);
    expect(now).toHaveBeenCalledWith(5);
    expect(group).toHaveBeenCalledWith(6);
    now.mockRestore();
    group.mockRestore();
    expect(rollSheetSource).not.toMatch(/basketApi|\.execute\(/);          // the sheet never places anything itself
  });

  it("an exit that did not close stops the roll: nothing goes into the basket, said plainly", async () => {
    writePending(null);
    const store = new BasketStore([]);
    const failed = await runRoll({ kind: "single", tradeId: 7, title: "NIFTY 24400 PE" }, preview(),
      fakeExits({ exitNow: vi.fn(async () => ({ closed: [], warnings: ["No live price"] })) }), store);
    expect(failed).toMatchObject({ ok: false, toBasket: false });
    expect(failed.text).toContain("The roll stopped after step 1: nothing went into your basket.");
    const thrown = await runRoll({ kind: "single", tradeId: 7, title: "NIFTY 24400 PE" }, preview(),
      fakeExits({ exitNow: vi.fn(async () => { throw new Error("Network down"); }) }), store);
    expect(thrown.ok).toBe(false);
    const partial = await runRoll({ kind: "group", tradeIds: [7, 8], title: "Basket", basket: true }, preview(),
      fakeExits({ exitGroup: vi.fn(async () => ({ status: "PARTIAL" as const, legs: [] })) }), store);
    expect(partial.toBasket).toBe(false);
    expect(store.snapshot()).toEqual([]);
    expect(pendingStore.snapshot()).toBeNull();
  });

  it("the exit runs even when the preview could not be read (exits are never blocked); the legs are then left to the chain", async () => {
    const exits = fakeExits();
    const out = await runRoll({ kind: "single", tradeId: 7, title: "NIFTY 24400 PE" }, null, exits, new BasketStore([]));
    expect(exits.exitNow).toHaveBeenCalledOnce();
    expect(out).toMatchObject({ ok: true, toBasket: false });
    expect(out.text).toContain("The new legs could not be prepared");
  });

  it("the sheet's confirm button is never disabled, and says the roll is two steps", () => {
    const confirm = rollSheetSource.slice(rollSheetSource.indexOf('data-testid="roll-confirm"') - 200, rollSheetSource.indexOf('data-testid="roll-confirm"') + 200);
    expect(confirm).not.toMatch(/disabled=/);
    expect(NOT_ATOMIC).toBe("Two steps, not one: you hold neither set of legs between them.");
    expect(rollSheetSource).toContain("STEP_ONE");
    expect(rollSheetSource).toContain("STEP_TWO");
  });

  it("an existing basket keeps its legs (the roll adds); words are structure words with Signed money", () => {
    const store = new BasketStore([{ id: "x", underlying: "NIFTY", expiry: "2026-11-03", strike: 24600, right: "CE", side: "BUY", lots: 1, price: 30, label: null }]);
    rollLegs(preview()).forEach((l) => store.add(l));
    expect(store.snapshot()).toHaveLength(2);
    expect(netWords(3750)).toBe("Net credit ₹3,750");
    expect(netWords(-1200)).toBe("Net debit ₹1,200");
    expect(netWords(null)).toBeNull();
    expect(exitWords(preview().exit[0])).toBe("Close sold NIFTY 03 NOV 24400 PE · 1 lot");
    expect(`${NOT_ATOMIC} ${preview().steps}`).not.toMatch(ADVICE);
  });

  it("Roll sits beside Exit now on an F&O PAPER row only with the flag's actions; Exit now stays, never disabled", () => {
    const p = trade({ id: 7, instrument_kind: "OPTION", symbol: "NIFTY 24400 PE 03 NOV 26", direction: "SHORT", exchange: "NFO" });
    const [r] = heldRows([p], exposure());
    const base: RowActions = { exitNowOn: true, canTrade: true, onExit: noop, onCheck: noop };
    const onRoll = vi.fn();
    const html = (a: RowActions) => renderToStaticMarkup(<MemoryRouter><ul><PositionRowV2 row={r} position={p} actions={a} /></ul></MemoryRouter>);
    expect(html(base)).not.toContain('data-testid="roll"');
    const withRoll = html({ ...base, roll: { onRoll } });
    expect(withRoll).toContain('data-testid="roll"');
    const exitTag = withRoll.match(/<button[^>]*data-testid="exit-now"[^>]*>/)?.[0] ?? "";
    expect(exitTag).not.toBe("");
    expect(exitTag).not.toContain('disabled=""');
    // An equity row or a LIVE row has no Roll.
    const eq = trade({ id: 8 });
    const [er] = heldRows([eq], exposure());
    expect(renderToStaticMarkup(<MemoryRouter><ul><PositionRowV2 row={er} position={eq} actions={{ ...base, roll: { onRoll } }} /></ul></MemoryRouter>)).not.toContain('data-testid="roll"');
    // A group of legs gets one Roll for the group.
    const legs = [p, trade({ id: 9, instrument_kind: "OPTION", symbol: "NIFTY 24300 PE 03 NOV 26", exchange: "NFO" })];
    const rows = heldRows(legs, exposure());
    const list = renderToStaticMarkup(<MemoryRouter><PositionList rows={rows} byId={new Map(legs.map((x) => [x.id, x]))} actions={{ ...base, roll: { onRoll } }}
      groups={[{ key: "basket:a", kind: "basket", name: "Basket", trade_ids: [7, 9], hedge_ids: [9] }]} /></MemoryRouter>);
    expect(list).toContain('data-testid="roll-group"');
    expect(list.match(/data-testid="roll"/g)).toBeNull();
  });

  it("the Terminal gates Roll on its flag with ui_v2 + terminal + the PAPER account, and shows the half-done notice", () => {
    expect(pageSource).toContain('termOn("term_roll") && features.on("paper_account") && canTrade');
    expect(pageSource).toContain('features.on("ui_v2") && features.on("terminal") && features.on(flag)');
    expect(pageSource).toContain("<RollPendingV2 onBasket={openBasket} />");
    expect(pageSource).toContain("writePending(null)");                                // a placed basket ends the roll
  });
});

// --- R3 ---------------------------------------------------------------------------------------------------------------

function myDay(over: Partial<MyDayPosition> = {}): MyDayPosition {
  return {
    id: 7, symbol: "NIFTY 24400 PE 03 NOV 26", mode: "PAPER", side: "SHORT", kind: "OPTION", underlying: "NIFTY", deployment_id: null,
    chip: null, chip_label: null, room_left_pct: null, chip_text: null, expiry: "2026-11-03", expiry_sessions: 5, expiry_text: null,
    area: { low: 24250, high: 24320, state: "fresh", where: "below", distance_pct: 0.33, range_text: "24,250.00-24,320.00", from: "strike", change: null },
    area_text: "", strike: 24400, right: "PE", ...over,
  };
}

describe("R3 a short strike's distance to the nearest area", () => {
  it("only sold options measured from their strike; words and numbers only", () => {
    const rows = [myDay(), myDay({ id: 8, side: "LONG" }), myDay({ id: 9, kind: "UNDERLYING" }), myDay({ id: 10, area: null })];
    expect(shortStrikeAreas(rows).map((r) => r.id)).toEqual([7]);
    expect(areaShort(myDay().area!)).toBe("Area 0.33% below");
    expect(areaShort({ ...myDay().area!, where: "inside" })).toBe("Inside an area");
    expect(areaWords(myDay().area!)).toBe("Area 0.33% below · 24,250.00-24,320.00");
    expect(areaWords(myDay().area!)).not.toMatch(ADVICE);
    expect([...areasByTrade(rows).keys()]).toEqual([7]);
  });

  it("the chain marks the strike of the shown underlying + expiry, beside the held marker", () => {
    const marks = chainAreaMarks([myDay(), myDay({ id: 8, expiry: "2026-11-10", strike: 24500 })], "NIFTY", "2026-11-03");
    expect([...marks]).toEqual([["24400|PE", "Area 0.33% below"]]);
    const html = renderToStaticMarkup(<ChainTableV2 live={liveChain()} prefs={DEFAULT_COLUMNS} centreKey="k" onPick={noop} held={new Set(["24400|PE"])} areaMarks={marks} />);
    expect(text(html)).toContain("Area 0.33% below");
    expect(html).toContain('data-area-side="put"');
    // Without marks the chain is exactly as before.
    expect(renderToStaticMarkup(<ChainTableV2 live={liveChain()} prefs={DEFAULT_COLUMNS} centreKey="k" onPick={noop} />)).not.toContain("data-area-mark");
  });

  it("the position row carries the mark as a chip", () => {
    const p = trade({ id: 7, instrument_kind: "OPTION", symbol: "NIFTY 24400 PE 03 NOV 26", direction: "SHORT", exchange: "NFO" });
    const [r] = heldRows([p], exposure());
    const html = renderToStaticMarkup(<MemoryRouter><ul><PositionRowV2 row={r} position={p}
      actions={{ exitNowOn: true, canTrade: true, onExit: noop, onCheck: noop, areas: areasByTrade([myDay()]) }} /></ul></MemoryRouter>);
    expect(text(html)).toContain("Area 0.33% below · 24,250.00-24,320.00");
  });
});

// --- R2 and R4 in the one basket ---------------------------------------------------------------------------------------

const EXPIRY = "2026-11-03";
const bleg = (over: Partial<BasketLeg> = {}): BasketLeg => ({
  id: "l1", underlying: "NIFTY", expiry: EXPIRY, strike: 24500, right: "PE", side: "SELL", lots: 1, price: 200, label: null, stopLoss: 300, iv: 13, ...over,
});
function evaluation(maxLoss: number): Evaluation {
  const prices = Array.from({ length: 241 }, (_, i) => 23500 + i * 8.33333);
  return {
    prices, at_expiry: prices.map(() => 0), today: prices.map(() => 0), on_date: prices.map(() => 0), days_forward: 0, iv_shift: 0, single_expiry: true,
    extremes: { max_profit: 7500, max_loss: maxLoss, unbounded_profit: false, unbounded_loss: false }, profitable: null, breakevens: [24400],
    summary: { expiry: EXPIRY, t_years: 0.02, sigma: 0.13, pop: 0.62, expected_move: 400, expected_pnl: 100,
      greeks: { delta: 1, gamma: 0, theta: 1, vega: 1, rho: 1 }, method: "", model: "bs" },
    legs: [], disclaimer: "Model estimates.", net_premium: 7500, reward_to_risk: 1,
    margin: { value: 7500, estimated: true, basis: "Defined risk.", note: "" },
    costs: { to_open: { stt: 1, exch: 1, sebi: 0, stamp: 0, brokerage: 40, tax: 7, total: 49, other: 8 }, round_trip: 98, brokerage_per_order: 20, orders: 2, as_of: "", note: "" },
  };
}
const PAIR: MarginPair = { source: "estimate", estimate: true, label: "ATP's estimate, not your broker's number", required: 22500, without_hedge: null,
  benefit: null, available: 977500, available_note: "Your practice balance minus the margin estimate.", free_after: 955000, notes: [] };
function numbers(over: { whatif?: WhatIf; margin_pair?: MarginPair } = {}): BasketNumbers {
  return { ...evaluation(-7500), ok: true, errors: [], lot_sizes: [75], labels: [], order: [0],
    margin: { ...evaluation(-7500).margin, hedge: { with_hedge: { value: 7500, estimated: true, basis: "Defined risk." }, without_hedge: null, benefit: null,
      hedged: false, source: "estimate", note: "ATP's estimate." } }, ...over };
}
const WHATIF: WhatIf = { ...evaluation(-21000), ok: true, errors: [], open: [{ trade_id: 7, label: "NIFTY 03 NOV 24400 PE", side: "SELL", lots: 1, price: 80 }],
  open_legs: [{ id: "open-7", direction: "SELL", option_type: "PE", strike: 24400, premium: 80, lots: 1, lot_size: 75, expiry: EXPIRY, iv: null }] };

const EXPAND = new Set(["BasketBody", "LegRow", "Figure"]);
function elements(node: ReactNode, out: ReactElement[] = []): ReactElement[] {
  if (Array.isArray(node)) { node.forEach((n) => elements(n, out)); return out; }
  if (!isValidElement(node)) return out;
  const type = node.type as unknown;
  if (typeof type === "function" && EXPAND.has((type as { name: string }).name)) return elements((type as (p: unknown) => ReactNode)(node.props), out);
  out.push(node);
  elements((node.props as { children?: ReactNode }).children, out);
  return out;
}
function handlers(): BodyHandlers {
  return Object.fromEntries(["onLeg", "onRemove", "onReady", "onAround", "onProduct", "onStrike", "onReview", "onFlip", "onClear", "onTool",
    "onSaveName", "onSave", "onLoad", "onDelete", "onBuilder", "onBrokerApp"].map((k) => [k, vi.fn()])) as unknown as BodyHandlers;
}
function body(n: BasketNumbers, whatif?: Parameters<typeof BasketBody>[0]["whatif"]) {
  const on = handlers();
  const node = <BasketBody legs={[bleg()]} numbers={n} problems={[]} settings={null} rows={liveChain().rows} pick={24500} atm={24500} spot={24480}
    product="MIS" note={null} tool={null} saved={null} saveName="" idPrefix="b" on={on} whatif={whatif} />;
  return { on, els: elements(node), html: renderToStaticMarkup(node) };
}

describe("R2 what-if: the open positions + the new legs, one server calculation", () => {
  it("the combined payoff is drawn by default with its own figures; a toggle shows the basket alone", () => {
    const onView = vi.fn();
    const { html, els } = body(numbers({ whatif: WHATIF }), { view: "combined", onView });
    expect(html).toContain('data-payoff="combined"');
    const words = text(html);
    expect(words).toContain("With my positions (1)");
    expect(words).toContain("Max loss, together");
    expect(words).toContain("NIFTY 03 NOV 24400 PE");
    const basketOnly = els.find((e) => (e.props as Record<string, unknown>)["data-whatif-view"] === "basket");
    (basketOnly!.props as { onClick: () => void }).onClick();
    expect(onView).toHaveBeenCalledWith("basket");
    expect(body(numbers({ whatif: WHATIF }), { view: "basket", onView }).html).toContain('data-payoff="basket"');
  });

  it("the open legs join the chart but cannot be dragged; without the flag the basket is exactly as before", () => {
    expect(whatIfLegs([bleg()], numbers({ whatif: WHATIF })).map((l) => l.id)).toEqual(["open-7", "l1"]);
    const off = body(numbers({ whatif: WHATIF }));
    expect(off.html).toContain('data-payoff="basket"');
    expect(off.html).not.toContain("whatif-toggle");
    expect(basketSource).toContain('if (!id.startsWith("open-")) on.onStrike(id, strike);');
    expect(basketSource).toContain("...(whatifOn ? { with_open: true } : {})");
  });
});

describe("R4 margin pair: required and available on every lot change", () => {
  it("required is labelled an estimate unless the broker answered; available is the status funds", () => {
    const words = text(body(numbers({ margin_pair: PAIR })).html);
    expect(words).toContain("Margin required (estimate)");
    expect(words).toContain("ATP's estimate, not your broker's number");
    expect(words).toContain("Available");
    expect(words).toContain("Your practice balance minus the margin estimate.");
    expect(words).toContain("Left after");
    const broker = text(body(numbers({ margin_pair: { ...PAIR, source: "broker", estimate: false, label: "Your broker's margin (upstox), with the hedge benefit" } })).html);
    expect(broker).toContain("Margin required ");
    expect(broker).not.toContain("Margin required (estimate)");
  });

  it("the evaluate call asks for the pair only with the flag, and re-reads on every legs (lots) change", () => {
    expect(basketSource).toContain("...(pairOn ? { margin_pair: true } : {})");
    expect(basketSource).toContain("[open, legs, spot, product, api, chain.symbol, whatifOn, pairOn]");
    expect(text(body(numbers()).html)).not.toContain("Margin required");
  });
});
