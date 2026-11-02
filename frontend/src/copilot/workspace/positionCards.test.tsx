import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ContractFacts, PositionGroup } from "../../fno/contracts";
import type { WaitingOrder } from "../../fno/positionActions";
import sheetSource from "../../fno/PositionActionsV2.tsx?raw";
import positionsSource from "../../trading/PositionsV2.tsx?raw";
import type { HeldRow } from "../../trading/model";
import { trade } from "../../trading/trading.fixture";
import type { TradeRecord } from "../../types";
import cardsSource from "./PositionCardsV2.tsx?raw";
import workspaceSource from "./ChartWorkspaceV2.tsx?raw";
import { CardsGridV2 } from "./PositionCardsV2";
import { buildCards, distanceTo, exitLines, halfOf, positionsHere, rootOf, spotOf, stopToEntry, type FactsOf } from "./positionCards";

/** M76 MP-10 (E-3 / E-4): position cards in the workspace; every exit is the existing one, never held back. */
const fact = (symbol: string, right: "CE" | "PE" | "FUT", strike: number | null, days = 3): ContractFacts => ({
  symbol, exchange: "NFO", known: true, listed: true, underlying: "NIFTY", right, strike, expiry: "2031-03-27", lot_size: 75,
  label: `NIFTY 27 MAR ${strike ?? "FUT"} ${right}`, expired: false, days_left: days, spot_symbol: "NIFTY 50", spot_exchange: "NSE", roll: null, roll_note: null,
});
const SOLD = trade({ id: 11, symbol: "NIFTY31MAR24400PE", exchange: "NFO", direction: "SHORT", instrument_kind: "OPTION", quantity: 150, entry_price: 60, leg_group_id: "b1" });
const HEDGE = trade({ id: 12, symbol: "NIFTY31MAR24300PE", exchange: "NFO", direction: "LONG", instrument_kind: "OPTION", quantity: 150, entry_price: 35, leg_group_id: "b1" });
const SINGLE = trade({ id: 13, symbol: "NIFTY31MAR24600CE", exchange: "NFO", direction: "LONG", instrument_kind: "OPTION", quantity: 300, entry_price: 80 });
const OTHER = trade({ id: 14, symbol: "BANKNIFTY31MAR52000CE", exchange: "NFO", instrument_kind: "OPTION" });
const FACTS = new Map<number, ContractFacts>([[11, fact(SOLD.symbol, "PE", 24400, 3)], [12, fact(HEDGE.symbol, "PE", 24300, 3)],
  [13, fact(SINGLE.symbol, "CE", 24600, 10)], [14, { ...fact(OTHER.symbol, "CE", 52000), underlying: "BANKNIFTY" }]]);
const factsOf: FactsOf = (p) => FACTS.get(p.id);
const GROUPS: PositionGroup[] = [{ key: "b1", kind: "basket", name: "Bull put spread", trade_ids: [11, 12], hedge_ids: [12] }];
const row = (id: number, pnl: number | null) => [id, { id, pnl } as HeldRow] as const;
const ROWS = new Map([row(11, 900), row(12, -300), row(13, null)]);
const GREEKS = { spot: { "NIFTY 50": 24512 }, legs: [{ trade_id: 11, position_delta: 22.5 }, { trade_id: 12, position_delta: -10 }] };
const waiting = (over: Partial<WaitingOrder>): WaitingOrder => ({
  id: 7, purpose: "EXIT", kind: "STOP", trade_id: 13, symbol: SINGLE.symbol, side: "SELL", quantity: 300, lots: 4, order_type: "SL-M", price: null, trigger_price: 50,
  trigger: { source: "PREMIUM", symbol: SINGLE.symbol, level: 50, side: "BELOW", mode: "TOUCH", timeframe_minutes: null },
  status: "RESTING", status_words: "Waiting", note: null, ...over,
});

describe("position card rules", () => {
  it("the chart's underlying, by the server's aliases and the contract facts", () => {
    expect(rootOf("NIFTY 50")).toBe("NIFTY");
    expect(rootOf(" nifty  bank ")).toBe("BANKNIFTY");
    expect(positionsHere([SOLD, HEDGE, SINGLE, OTHER], "NIFTY 50", factsOf).map((p) => p.id)).toEqual([11, 12, 13]);
    expect(positionsHere([{ ...SOLD, exit_time: "2031-03-20T05:00:00" }], "NIFTY", factsOf)).toEqual([]);
    expect(spotOf(GREEKS, "NIFTY")).toBe(24512);
    expect(spotOf(null, "NIFTY")).toBeNull();
  });

  it("a structure card: summed P&L, spot to each sold strike in points and %, summed delta, days to expiry", () => {
    const [basket, single] = buildCards([SOLD, HEDGE, SINGLE], ROWS, GROUPS, factsOf, GREEKS, 24512);
    expect(basket).toMatchObject({ kind: "basket", title: "Bull put spread", pnl: 600, delta: 12.5, daysLeft: 3, paper: true });
    expect(basket.distances).toEqual([{ strike: 24400, right: "PE", points: 112, pct: 0.46 }]);
    expect(basket.legs.map((l) => [l.trade.id, l.hedge, l.lots])).toEqual([[11, false, 2], [12, true, 2]]);
    expect(single).toMatchObject({ kind: "single", pnl: null, delta: null, distances: [] });      // no live price / Greek: Not known
    expect(distanceTo(null, 24400, "PE")).toBeNull();
    expect(distanceTo(24300, 24400, "PE")?.points).toBe(-100);
  });

  it("close half = Exit part's own rule (PAPER, not a leg, 2+ lots), half rounded down", () => {
    expect(halfOf(SINGLE)).toBe(2);
    expect(halfOf({ ...SINGLE, quantity: 375 })).toBe(2);
    expect(halfOf({ ...SINGLE, quantity: 75 })).toBeNull();
    expect(halfOf(SOLD)).toBeNull();
    expect(halfOf({ ...SINGLE, mode: "LIVE" })).toBeNull();
  });

  it("stop to entry: his own waiting stop on the contract's price, moved to the entry; nothing else", () => {
    expect(stopToEntry([waiting({})], SINGLE)).toMatchObject({ level: 80 });
    expect(stopToEntry([waiting({ trigger: { ...waiting({}).trigger, source: "SPOT" } })], SINGLE)).toBeNull();
    expect(stopToEntry([waiting({ trigger: { ...waiting({}).trigger, level: 80 } })], SINGLE)).toBeNull();
    expect(stopToEntry([waiting({ kind: "TARGET" })], SINGLE)).toBeNull();
    expect(stopToEntry([waiting({ trade_id: 99 })], SINGLE)).toBeNull();
  });

  it("chart lines: only his stops / targets on the underlying's level, for these positions", () => {
    const spotStop = waiting({ id: 8, trigger: { ...waiting({}).trigger, source: "SPOT", level: 24350 } });
    const lines = exitLines([waiting({}), spotStop, { ...spotStop, id: 9, trade_id: 99 }], new Set([13]), { stop: "r", target: "g" });
    expect(lines).toEqual([{ price: 24350, color: "r", style: "dashed", title: `Stop · ${SINGLE.symbol}` }]);
  });
});

describe("the cards: exits are never blocked and go through the existing paths", () => {
  const noop = () => undefined;
  const cards = buildCards([SOLD, HEDGE, SINGLE], ROWS, GROUPS, factsOf, GREEKS, 24512);
  const grid = (list = cards, exitsOn = true) => renderToStaticMarkup(
    <CardsGridV2 cards={list} waiting={[waiting({})]} exitsOn={exitsOn} onCloseAll={noop} onHalf={noop} onLeg={noop} onStopToEntry={noop} />);

  it("every exit button is shown and none is disabled", () => {
    const html = grid();
    for (const a of ["close-all", "close-half", "close-leg", "stop-to-entry"]) expect(html).toContain(`data-card-action="${a}"`);
    expect(html).not.toContain("disabled");
    expect(html).toContain("Close all legs");
    expect(html).toContain("Spot to sold 24,400 PE: 112.00 points above (0.46 %)");
  });

  it("a LIVE position says where it closes, exactly as Positions does (one rule, PAPER and LIVE alike)", () => {
    const live = buildCards([{ ...SINGLE, mode: "LIVE" } as TradeRecord], ROWS, [], factsOf, null, null);
    expect(grid(live)).toContain("Close a live position in your broker&#x27;s app.");
    expect(positionsSource).toContain("position.mode === \"PAPER\"");
    expect(positionsSource).toContain("Close a live position in your broker&apos;s app.");
  });

  it("only the existing exit calls; no new route, no pre-check before an exit; Close half pre-fills only his tap", () => {
    expect(cardsSource).toContain("api.exitNow(p.id)");
    expect(cardsSource).toContain("<GroupExitSheetV2 ");
    expect(cardsSource).toContain("<PositionActionSheetV2 ");
    expect(cardsSource).toContain("api.actions.modify(ask.order.id, { level: ask.level })");
    expect(cardsSource).not.toMatch(/request<[^>]*>\("\/paper\/fno\/positions|exit-leg|exit-group|exit-part|basket\/execute|\/order"/);
    const exitNow = cardsSource.slice(cardsSource.indexOf("async function exitNow"), cardsSource.indexOf("async function stopAtEntry"));
    expect(exitNow.indexOf("window.confirm")).toBeLessThan(exitNow.indexOf("api.exitNow"));
    expect(exitNow).not.toMatch(/check|preview|warning|risk/i);
    expect(sheetSource).toContain("ask.lots ? clampLots(ask.lots, openLots(ask.position)) : null");
    expect(workspaceSource).toContain("<PositionCardsV2 symbol={pick.symbol} engine={engine} />");
  });
});
