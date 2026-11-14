import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

const ctx = vi.hoisted(() => ({ role: "OWNER" as string, flags: ["ui_v2", "paper_account"] as string[] }));
vi.mock("../auth/AuthContext", () => ({ useAuth: () => ({ user: { id: 1, role: ctx.role } }), useOptionalAuth: () => ({ user: { id: 1, role: ctx.role } }) }));
vi.mock("../ui/useFeatures", () => ({
  useFeatures: () => ({ on: (n: string) => ctx.flags.includes(n), loaded: true, answered: true, features: {} }),
  useUiV2: () => ctx.flags.includes("ui_v2"),
}));

import type { ContractFacts } from "../fno/contracts";
import { trade } from "../trading/trading.fixture";
import ChartTradeBar, { ChartTradeButtons } from "./ChartTradeBar";
import { chartTicketTarget, heldOnChart, heldWords, positionItems, tradableOnChart } from "./chartTrade";
import { itemKey } from "./watchlistsV2";

/** CTB-1 (owner 14 Nov): Buy / Sell / Exit on a chart window. Made-up contracts ("DEMO ..."), not market data. */
function contract(symbol: string, right: "CE" | "PE" | "FUT", extra: Partial<Extract<ContractFacts, { known: true }>> = {}): ContractFacts {
  return { symbol, exchange: "NFO", known: true, listed: true, underlying: "DEMO", right, strike: right === "FUT" ? null : 25250, expiry: "2026-11-17",
    lot_size: 65, label: `DEMO 17 NOV ${right === "FUT" ? "FUT" : `25250 ${right}`}`, expired: false, days_left: 3, spot_symbol: "DEMO IDX",
    spot_exchange: "NSE", roll: null, roll_note: null, ...extra };
}
const SPACED_PE = "DEMO 25250 PE 17 NOV 26";
const COMPACT_PE = "DEMO26N1725250PE";
const PE = contract(SPACED_PE, "PE");
const facts = (...list: ContractFacts[]) => new Map(list.map((f) => [itemKey(f), f]));
const HELD_PE = trade({ id: 11, symbol: COMPACT_PE, exchange: "NFO", instrument_kind: "OPTION", direction: "LONG", entry_price: 120.5, quantity: 65 });

describe("which charts trade", () => {
  it("only an F&O contract (NFO / BFO); never an index or a stock", () => {
    expect(tradableOnChart("NFO")).toBe(true);
    expect(tradableOnChart("bfo")).toBe(true);
    expect(tradableOnChart("NSE")).toBe(false);
    expect(tradableOnChart(null)).toBe(false);
  });

  it("the ticket's target is the contract and its last price only - no lots, order type, product or stop", () => {
    const t = chartTicketTarget(PE, 118.4);
    expect(t).toEqual({ underlying: "DEMO", expiry: "2026-11-17", strike: 25250, right: "PE", label: "DEMO 17 NOV 25250 PE", lotSize: 65, ltp: 118.4, bid: null, ask: null });
    expect(Object.keys(t ?? {})).not.toEqual(expect.arrayContaining(["lots", "product", "orderType", "stopLoss"]));
    expect(chartTicketTarget(contract("DEMO 25 NOV FUT", "FUT"), null)?.strike).toBeNull();
    expect(chartTicketTarget(PE, 0)?.ltp).toBeNull();
  });

  it("no ticket for a contract not known, expired or without an expiry", () => {
    expect(chartTicketTarget(undefined, 1)).toBeNull();
    expect(chartTicketTarget({ symbol: "X", exchange: "NFO", known: false }, 1)).toBeNull();
    expect(chartTicketTarget(contract(SPACED_PE, "PE", { expired: true }), 1)).toBeNull();
    expect(chartTicketTarget(contract(SPACED_PE, "PE", { expiry: null }), 1)).toBeNull();
  });
});

describe("his position in the chart's contract", () => {
  const chart = { symbol: SPACED_PE, exchange: "NFO" };

  it("the same contract under the exchange's compact name counts as held (the server reads both)", () => {
    const f = facts(PE, contract(COMPACT_PE, "PE"));
    expect(heldOnChart(chart, [HELD_PE], f).map((p) => p.id)).toEqual([11]);
  });

  it("the other side, another strike, a closed or a stock position does not", () => {
    const ce = trade({ id: 12, symbol: "DEMO26N1725250CE", exchange: "NFO", instrument_kind: "OPTION" });
    const closed = trade({ ...HELD_PE, id: 13, exit_time: "2026-11-14T05:00:00" });
    const stock = trade({ id: 14, symbol: SPACED_PE, exchange: "NSE" });
    const f = facts(PE, contract("DEMO26N1725250CE", "CE"), contract(COMPACT_PE, "PE", { strike: 25300 }));
    expect(heldOnChart(chart, [ce, closed, stock, HELD_PE], f)).toEqual([]);
  });

  it("the same symbol is held even before the lookup answers; only open F&O positions are looked up", () => {
    const same = trade({ ...HELD_PE, id: 15, symbol: SPACED_PE });
    expect(heldOnChart(chart, [same], new Map()).map((p) => p.id)).toEqual([15]);
    expect(positionItems([HELD_PE, trade({ id: 2 }), trade({ ...HELD_PE, id: 3, exit_time: "x" })])).toEqual([{ symbol: COMPACT_PE, exchange: "NFO" }]);
  });

  it("the chip is his own record: side, qty", () => {
    expect(heldWords(HELD_PE)).toEqual({ side: "Long", qty: "Qty 65" });
    expect(heldWords({ direction: "SHORT", quantity: 130 }).side).toBe("Short");
  });
});

describe("the bar", () => {
  const none = () => undefined;
  const render = (over: Partial<Parameters<typeof ChartTradeButtons>[0]> = {}) => renderToStaticMarkup(
    <ChartTradeButtons chart={SPACED_PE} name="DEMO 17 NOV 25250 PE" entry held={[]} paperHeld={[]} liveHeld={false} note={null} onSide={none} onExit={none} {...over} />);

  it("Buy (green) and Sell (red) from the tokens, 44 px on a phone", () => {
    const html = render();
    expect(html).toMatch(/data-chart-trade-action="buy" aria-label="Buy DEMO 17 NOV 25250 PE"[^>]*class="[^"]*min-h-11[^"]*bg-up text-on-brand/);
    expect(html).toMatch(/data-chart-trade-action="sell"[^>]*class="[^"]*bg-down text-on-brand/);
    expect(html).not.toContain("data-chart-trade-action=\"exit\"");
  });

  it("held: the chip and Exit, never disabled (ADR-0004)", () => {
    const html = render({ held: [HELD_PE], paperHeld: [HELD_PE] });
    expect(html).toContain('data-chart-trade-held="11"');
    expect(html).toContain("Qty 65");
    expect(html).toContain("Avg 120.50");
    expect(html).toMatch(/<button[^>]*data-chart-trade-action="exit" data-position="11"[^>]*>Exit<\/button>/);
    expect(html).not.toContain("disabled");
  });

  it("Exit is shown without Buy / Sell (an expired contract, the PAPER account off); a LIVE position says where to close it", () => {
    const html = render({ entry: false, paperHeld: [HELD_PE], liveHeld: true });
    expect(html).not.toContain("data-chart-trade-action=\"buy\"");
    expect(html).toContain("data-chart-trade-action=\"exit\"");
    expect(html).toContain("Close a live position in your broker&#x27;s app.");
  });

  it("two accounts in one contract: one Exit each, by account", () => {
    const a = { ...HELD_PE, account_label: "Practice A" };
    const b = { ...HELD_PE, id: 16, account_label: "Practice B" };
    const html = render({ held: [a, b], paperHeld: [a, b] });
    expect(html).toContain(">Exit Practice A<");
    expect(html).toContain(">Exit Practice B<");
  });

  it("nothing on an index or a stock chart, for a viewer, or with the PAPER account and Exit now off", () => {
    const bar = (exchange: string) => renderToStaticMarkup(<ChartTradeBar symbol="DEMO IDX" exchange={exchange} />);
    expect(bar("NSE")).toBe("");
    ctx.role = "VIEWER";
    expect(renderToStaticMarkup(<ChartTradeBar symbol={SPACED_PE} exchange="NFO" />)).toBe("");
    ctx.role = "OWNER";
    ctx.flags = ["ui_v2"];
    expect(renderToStaticMarkup(<ChartTradeBar symbol={SPACED_PE} exchange="NFO" />)).toBe("");
    ctx.flags = ["ui_v2", "paper_account"];
  });
});
