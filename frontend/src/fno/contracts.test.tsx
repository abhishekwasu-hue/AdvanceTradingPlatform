import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { ExpiredRows, RowName } from "../chart/WatchlistPanel";
import panelSource from "../chart/WatchlistPanel.tsx?raw";
import { applyEdit, type WatchlistV2 } from "../chart/watchlistsV2";
import { heldRows } from "../trading/model";
import positionsSource from "../trading/PositionsV2.tsx?raw";
import { PositionRowV2, type RowActions } from "../trading/PositionsV2";
import { exposure, trade } from "../trading/trading.fixture";
import apiSource from "./api.ts?raw";
import {
  addSide, adjustHref, combinedPnl, contractItems, contractSubline, contractsKey, countdown, formatOi, groupRows, groupTitle, holdsTarget,
  isContractItem, positionItem, positionTarget, rollEdit, splitExpired, type ContractFacts, type KnownContract, type PositionGroup,
} from "./contracts";
import contractsSource from "./contracts.ts?raw";
import { askOf, newDraft } from "./ticketModel";

/**
 * FNO-4 (owner order sections 2, 8 and 9): F&O contracts in watchlists (name, LTP / OI / change, days left, "Expired"
 * with one-tap "Roll to next expiry" that only edits the list) and the positions page (Add / Adjust on an F&O PAPER
 * position, Exit never blocked, legs placed together with their combined P&L).
 */
const noop = () => undefined;
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/[^\n]*/g, "$1");
const ADVICE = /\b(should|recommend\w*|advice|advise|best|target price|guarantee\w*|sure[- ]?shot)\b/i;

function facts(extra: Partial<KnownContract> = {}): KnownContract {
  return {
    symbol: "NIFTY 22800 CE 28 OCT 26", exchange: "NFO", known: true, listed: true, underlying: "NIFTY", right: "CE", strike: 22800,
    expiry: "2026-10-28", lot_size: 75, label: "NIFTY 28 OCT 22800 CE", expired: false, days_left: 6, spot_symbol: "NIFTY 50",
    spot_exchange: "NSE", roll: null, roll_note: null, ...extra,
  };
}
const ROLL = { symbol: "NIFTY 22800 CE 04 NOV 26", exchange: "NFO", underlying: "NIFTY", right: "CE" as const, strike: 22800, expiry: "2026-11-04",
  lot_size: 75, label: "NIFTY 04 NOV 22800 CE" };
const expired = (extra: Partial<KnownContract> = {}) => facts({ expired: true, days_left: null, roll: ROLL, ...extra });
const LIST: WatchlistV2 = { id: 3, name: "F&O", version: 4, items: [
  { symbol: "RELIANCE", exchange: "NSE" }, { symbol: "NIFTY 22800 CE 28 OCT 26", exchange: "NFO" }, { symbol: "TCS", exchange: "NSE" },
] };

describe("FNO-4 watchlist contract rows", () => {
  it("contract rows are the NFO / BFO items; the lookup's key ignores order", () => {
    expect(isContractItem({ exchange: "nfo" })).toBe(true);
    expect(isContractItem({ exchange: "BFO" })).toBe(true);
    expect(isContractItem({ exchange: "NSE" })).toBe(false);
    expect(contractItems(LIST.items)).toEqual([LIST.items[1]]);
    const reordered = [LIST.items[2], LIST.items[1], LIST.items[0]];
    expect(contractsKey(reordered)).toBe(contractsKey(LIST.items));
    expect(contractsKey([{ symbol: "TCS", exchange: "NSE" }])).toBe("");
  });

  it("the second line: days left and OI, or Expired", () => {
    expect(countdown(6)).toBe("6 days left");
    expect(countdown(1)).toBe("Expires tomorrow");
    expect(countdown(0)).toBe("Expires today");
    expect(countdown(null)).toBeNull();
    expect(formatOi(12_500_000)).toBe("1.25 Cr");
    expect(formatOi(1_250_000)).toBe("12.5 L");
    expect(formatOi(85_000)).toBe("85,000");
    expect(formatOi(0)).toBeNull();
    expect(formatOi(null)).toBeNull();
    expect(contractSubline(facts(), 1_250_000)).toBe("6 days left · OI 12.5 L");
    expect(contractSubline(facts({ days_left: null }), null)).toBe("NFO");
    expect(contractSubline(expired(), 999)).toBe("Expired");
  });

  it("a contract row shows its name as traders write it, with the trading symbol on hover; others as before", () => {
    const html = renderToStaticMarkup(<RowName item={LIST.items[1]} facts={facts()} q={{ symbol: "X", exchange: "NFO", ltp: 101, change: 1, change_pct: 1, missing: false, oi: 1_250_000 }} />);
    expect(text(html)).toContain("NIFTY 28 OCT 22800 CE");
    expect(text(html)).toContain("6 days left · OI 12.5 L");
    expect(html).toContain('title="NIFTY 22800 CE 28 OCT 26"');
    const plain = text(renderToStaticMarkup(<RowName item={LIST.items[0]} facts={undefined} q={undefined} />));
    expect(plain).toContain("RELIANCE");
    expect(plain).toContain("NSE");
  });

  it("expired contracts move below the others, in list order", () => {
    const map = new Map<string, ContractFacts>([["NFO:NIFTY 22800 CE 28 OCT 26", expired()]]);
    const split = splitExpired(LIST.items, map);
    expect(split.live.map((i) => i.symbol)).toEqual(["RELIANCE", "TCS"]);
    expect(split.expired.map((i) => i.symbol)).toEqual(["NIFTY 22800 CE 28 OCT 26"]);
    expect(splitExpired(LIST.items, new Map()).expired).toEqual([]);                       // nothing known: as before
  });

  it("Roll to next expiry replaces the row in place - a watchlist edit only", () => {
    const edit = rollEdit(LIST.items[1], expired());
    expect(edit).toEqual({ kind: "replace", item: LIST.items[1], with: { symbol: ROLL.symbol, exchange: "NFO" } });
    const rolled = applyEdit(LIST, edit!);
    expect(rolled.items.map((i) => i.symbol)).toEqual(["RELIANCE", ROLL.symbol, "TCS"]);
    expect(rolled.id).toBe(LIST.id);
    // The list already holds the next contract: the expired row only goes (never twice).
    const holding = { ...LIST, items: [...LIST.items, { symbol: ROLL.symbol, exchange: "NFO" }] };
    expect(holdsTarget(holding, expired())).toBe(true);
    expect(applyEdit(holding, edit!).items.map((i) => i.symbol)).toEqual(["RELIANCE", "TCS", ROLL.symbol]);
    // No roll for a live contract, an unknown one or one with no next expiry listed.
    expect(rollEdit(LIST.items[1], facts())).toBeNull();
    expect(rollEdit(LIST.items[1], expired({ roll: null, roll_note: "not listed" }))).toBeNull();
    expect(rollEdit(LIST.items[0], { symbol: "RELIANCE", exchange: "NSE", known: false })).toBeNull();
    // A replace of a row the list no longer holds changes nothing.
    expect(applyEdit(LIST, { kind: "replace", item: { symbol: "GONE", exchange: "NFO" }, with: { symbol: "X", exchange: "NFO" } })).toBe(LIST);
  });

  it("the Expired block: the next contract named, one tap to roll, remove; no price, no order", () => {
    const onRoll = vi.fn();
    const map = new Map<string, ContractFacts>([["NFO:NIFTY 22800 CE 28 OCT 26", expired()]]);
    const html = renderToStaticMarkup(<ExpiredRows rows={[LIST.items[1]]} facts={map} listName="F&O" inList={() => false} onRoll={onRoll} onRemove={noop}
      iconBtn="" touch={false} />);
    expect(text(html)).toContain("Expired (1)");
    expect(text(html)).toContain("Next: NIFTY 04 NOV 22800 CE");
    expect(html).toContain('data-action="roll"');
    expect(text(html)).toContain("Roll to next expiry");
    expect(text(html)).not.toMatch(/Buy|Sell|order/i);
    const none = new Map<string, ContractFacts>([["NFO:NIFTY 22800 CE 28 OCT 26", expired({ roll: null })]]);
    const noRoll = renderToStaticMarkup(<ExpiredRows rows={[LIST.items[1]]} facts={none} listName="F&O" inList={() => false} onRoll={onRoll} onRemove={noop} iconBtn="" touch />);
    expect(text(noRoll)).toContain("The next expiry is not listed yet");
    expect(noRoll).not.toContain('data-action="roll"');
    expect(renderToStaticMarkup(<ExpiredRows rows={[]} facts={map} listName="F&O" inList={() => false} onRoll={onRoll} onRemove={noop} iconBtn="" touch />)).toBe("");
  });

  it("the panel rolls through the list store only (no order, no ticket)", () => {
    const panel = code(panelSource);
    expect(panel).toMatch(/store\.edit\(list\.id, edit\)/);
    expect(panel).not.toMatch(/fnoApi|basketApi|\/paper\/fno\/order|OrderTicket/);
    expect(code(contractsSource)).not.toMatch(/\/paper\/fno\/order|basket\/execute|exit-now|mode:/);
    expect(contractsSource).not.toMatch(ADVICE);
  });
});

describe("FNO-4 positions: Add / Adjust / Exit and legs placed together", () => {
  const option = (extra = {}) => trade({ id: 41, symbol: "NIFTY 22800 CE 28 OCT 26", exchange: "NFO", instrument_kind: "OPTION", lot_size: 75,
    underlying_symbol: "NIFTY 50", direction: "LONG", ...extra });
  const fnoMap = (f: ContractFacts) => new Map<string, ContractFacts>([["NFO:NIFTY 22800 CE 28 OCT 26", f]]);
  function render(p = option(), f: ContractFacts | null = facts(), extra: Partial<RowActions> = {}) {
    const [r] = heldRows([p], exposure());
    const onAdd = vi.fn();
    const a: RowActions = { exitNowOn: true, canTrade: true, onExit: noop, onCheck: noop,
      fno: { facts: f ? fnoMap(f) : new Map(), onAdd }, ...extra };
    return { html: renderToStaticMarkup(<MemoryRouter><ul><PositionRowV2 row={r} position={p} actions={a} /></ul></MemoryRouter>), onAdd };
  }

  it("an F&O PAPER position has Add and Adjust next to Exit now", () => {
    const { html } = render();
    expect(html).toContain('data-testid="exit-now"');
    expect(html).toContain('data-testid="fno-add"');
    expect(html).toContain('data-testid="fno-adjust"');
    expect(html).toContain('href="/option-chain?underlying=NIFTY&amp;expiry=2026-10-28"');
  });

  it("Exit is never removed: unknown or expired contract, LIVE, or no F&O actions at all", () => {
    for (const f of [null, expired(), { symbol: "X", exchange: "NFO", known: false } as ContractFacts]) {
      const { html } = render(option(), f);
      expect(html).toContain('data-testid="exit-now"');
      expect(html).not.toContain('data-testid="fno-add"');
    }
    expect(render(option(), facts(), { fno: undefined }).html).toContain('data-testid="exit-now"');
    const live = render(option({ mode: "LIVE" })).html;
    expect(live).not.toContain('data-testid="fno-add"');
    expect(live).not.toContain('data-testid="fno-adjust"');
    // A stock position: no F&O actions.
    expect(render(trade(), facts()).html).not.toContain('data-testid="fno-add"');
  });

  it("Add opens the one ticket on the same contract and side; its order carries no mode (PAPER at the server)", () => {
    const p = option({ direction: "SHORT" });
    expect(addSide(p)).toBe("SELL");
    expect(addSide(option())).toBe("BUY");
    const target = positionTarget(p, facts(), 120.5);
    expect(target).toEqual({ underlying: "NIFTY", expiry: "2026-10-28", strike: 22800, right: "CE", label: "NIFTY 28 OCT 22800 CE", lotSize: 75,
      ltp: 120.5, bid: null, ask: null });
    const ask = askOf(target!, newDraft(addSide(p)), "ref-12345678");
    expect(ask).not.toHaveProperty("mode");
    expect(ask.side).toBe("SELL");
    expect(apiSource).toContain('place: (ask: OrderAsk) => request<OrderResult>("/paper/fno/order"');
    // A future: no strike; Adjust opens the futures chain.
    const fut = facts({ right: "FUT", strike: null, label: "NIFTY 28 OCT FUT" });
    expect(positionTarget(p, fut, null)?.strike).toBeNull();
    expect(adjustHref(fut)).toBe("/charts/NIFTY%2050?hub=futures&expiry=2026-10-28");
    expect(positionTarget(p, expired(), 1)).toBeNull();
    expect(adjustHref(expired())).toBeNull();
    expect(positionItem({ symbol: "nifty 22800 ce 28 oct 26", exchange: null, instrument_kind: "OPTION" })).toEqual({ symbol: "NIFTY 22800 CE 28 OCT 26", exchange: "NFO" });
  });

  it("legs placed together group with the combined P&L; a leg without a live P&L leaves the sum unknown", () => {
    const rows = [{ id: 1, pnl: 1200 }, { id: 2, pnl: -450.5 }, { id: 3, pnl: 300 }, { id: 4, pnl: -100 }, { id: 5, pnl: 50 }];
    const groups: PositionGroup[] = [
      { key: "basket:a", kind: "basket", name: "Basket", trade_ids: [1, 2, 4] },
      { key: "structure:b", kind: "structure", name: "Iron Condor", trade_ids: [4, 9] },        // 4 is taken; 9 not on screen
    ];
    const { grouped, single } = groupRows(rows, groups);
    expect(grouped).toHaveLength(1);
    expect(grouped[0].legs.map((l) => l.id)).toEqual([1, 2, 4]);
    expect(grouped[0].pnl).toBe(649.5);
    expect(groupTitle(grouped[0])).toBe("Basket · 3 legs");
    expect(single.map((r) => r.id)).toEqual([3, 5]);
    expect(combinedPnl([{ pnl: 10 }, { pnl: null }])).toBeNull();
    expect(combinedPnl([])).toBeNull();
    expect(combinedPnl([{ pnl: 0.1 }, { pnl: 0.2 }])).toBe(0.3);
    expect(groupRows(rows, []).single).toHaveLength(5);
  });

  it("the page wires it under the PAPER account only; the ticket is its own lazy chunk; exits keep today's handler", () => {
    const src = code(positionsSource);
    expect(src).toMatch(/const fnoOn = features\.on\("paper_account"\) && canTrade;/);
    expect(src).toMatch(/lazy\(\(\) => import\("\.\.\/fno\/OrderTicketV2"\)\)/);
    expect(src).toMatch(/const result = await api\.exitNow\(p\.id\);/);
    expect(src).not.toMatch(/disabled=\{[^}]*\}[^>]*data-testid="exit-now"/);
    expect(positionsSource).not.toMatch(ADVICE);
  });
});
