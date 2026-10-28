import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi, type Mock } from "vitest";
import { ApiError } from "../api/errors";
import chartsPageSource from "../chart/ChartsPageV2.tsx?raw";
import type { WatchlistV2 } from "../chart/watchlistsV2";
import chainPageSource from "../optionChain/OptionChainPageV2.tsx?raw";
import { loadExpiryChoice } from "../optionChain/useOptionChain";
import { BasketStore } from "./basket";
import { basketAsk, basketProblems, builderHandoff, contractLabel, legLabel, legsOf, savedOf } from "./basketModel";
import { watchlistAdd } from "./ChainActionSheetV2";
import { FUTURES_TEXT, FuturesChainView, addFutureToBasket, futuresProblemOf, type FuturesViewProps, type RowHandlers } from "./FuturesChainV2";
import futuresSource from "./FuturesChainV2.tsx?raw";
import {
  basketHref, dayRange, expiryText, futureContract, futureLeg, futureTarget, hubHref, hubTabs, hubViewOf, isHubInfo, readCarry, yearRange,
  type Bar, type FutureRow, type FuturesChain, type HubInfo,
} from "./hubModel";
import { OverviewView } from "./OverviewV2";
import { HubTabsView } from "./SymbolHubTabsV2";
import { askOf, draftProblems, newDraft, stopHint } from "./ticketModel";

/**
 * FNO-3 (owner order sections 1, 4 and 9): the symbol hub's tabs show / hide by F&O availability, the symbol, expiry and
 * timeframe carry over in every tab's address, the futures rows (figures, missing prices, the rollover caption) and
 * each row's actions (Chart, Buy / Sell -> the PAPER ticket, Add to basket, Add to watchlist), and the Overview (only
 * the figures that have data). No DOM: the views have no state, so they are rendered directly and their handlers called.
 */
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, "\"").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const ADVICE = /\b(should|recommend\w*|advice|advise|best|target\w*|guarantee\w*|sure[- ]?shot|bullish|bearish)\b/i;

const EXPAND = new Set(["RowActions", "RowPanelView", "Signed", "Problem", "FuturesChainView"]);
function elements(node: ReactNode, out: ReactElement[] = []): ReactElement[] {
  if (Array.isArray(node)) { node.forEach((n) => elements(n, out)); return out; }
  if (!isValidElement(node)) return out;
  const type = node.type as unknown;
  if (typeof type === "function" && EXPAND.has((type as { name: string }).name)) {
    return elements((type as (p: unknown) => ReactNode)(node.props), out);
  }
  out.push(node);
  elements((node.props as { children?: ReactNode }).children, out);
  return out;
}
const all = (els: ReactElement[], attr: string, value: string) => els.filter((e) => (e.props as Record<string, unknown>)[attr] === value)
  .map((e) => e.props as Record<string, unknown> & { onClick?: () => void; to?: string });

const INFO: HubInfo = {
  symbol: "NIFTY 50", underlying: "NIFTY", spot_symbol: "NIFTY 50", spot_exchange: "NSE", has_fno: true, has_options: true, has_futures: true,
  lot_size: 75, option_expiries: [{ date: "2026-10-27", kind: "weekly" }, { date: "2026-11-03", kind: "weekly" }],
  default_option_expiry: "2026-10-27", future_expiries: ["2026-10-27", "2026-11-24", "2026-12-29"], today: "2026-10-21",
};
const PLAIN: HubInfo = { ...INFO, symbol: "ZOMATOX", underlying: "ZOMATOX", spot_symbol: "ZOMATOX", has_fno: false, has_options: false,
  has_futures: false, lot_size: null, option_expiries: [], default_option_expiry: null, future_expiries: [] };

function row(over: Partial<FutureRow> = {}): FutureRow {
  return {
    underlying: "NIFTY", tradingsymbol: "NIFTY26OCTFUT", exchange: "NFO", instrument_key: "NSE_FO|1", expiry: "2026-10-27", lot_size: 75,
    tick_size: 0.05, label: "NIFTY 27 OCT FUT", days_to_expiry: 6, ltp: 24612.5, prev_close: 24500, change: 112.5, change_pct: 0.46,
    oi: 1_250_000, oi_change: 45_000, volume: 90_000, bid: 24612, ask: 24613, basis: 62.5, basis_pct: 0.255, missing: false, error: null, ...over,
  };
}
const CHAIN: FuturesChain = {
  underlying: "NIFTY", spot: { symbol: "NIFTY 50", exchange: "NSE", ltp: 24550, change: 100, change_pct: 0.41 },
  rows: [row(), row({ tradingsymbol: "NIFTY26NOVFUT", expiry: "2026-11-24", label: "NIFTY 24 NOV FUT", days_to_expiry: 34, ltp: 24750, basis: 200, basis_pct: 0.815, oi: 400_000, oi_change: null }),
    row({ tradingsymbol: "NIFTY26DECFUT", expiry: "2026-12-29", label: "NIFTY 29 DEC FUT", days_to_expiry: 69, ltp: null, change: null, change_pct: null, oi: null, oi_change: null, basis: null, basis_pct: null, missing: true, error: "No price" })],
  rollover: { near_expiry: "2026-10-27", next_expiry: "2026-11-24", near_label: "NIFTY 27 OCT FUT", next_label: "NIFTY 24 NOV FUT", next_share_pct: 24.2,
    spread: 137.5, spread_pct: 0.559, near_oi: 1_250_000, next_oi: 400_000,
    caption: "24.2% of the near and next month futures OI is in the next month; the next month trades 137.50 above the near month." },
  oi_change: { status: "ready", source: "first_reading", since: "2026-10-21T03:50:00+00:00" }, market_open: true, fetched_at: "2026-10-21T05:30:00+00:00",
};

function on(): RowHandlers & Record<keyof RowHandlers, Mock> {
  return { onTrade: vi.fn(), onPanel: vi.fn(), onBasket: vi.fn(), onWatchlist: vi.fn() };
}
function view(over: Partial<FuturesViewProps> = {}) {
  const handlers = on();
  const props: FuturesViewProps = { data: CHAIN, problem: null, loading: false, trading: true, watchlistsOn: true, timeframe: "15min", panel: null,
    watchlists: null, note: null, basketCount: 0, on: handlers, ...over };
  const node = <FuturesChainView {...props} />;
  return { on: handlers, els: elements(node), html: renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>) };
}

describe("FNO-3 hub tabs: show / hide by F&O availability", () => {
  it("a symbol with F&O has all four tabs; one without has Chart and Overview; unknown facts show no tab row", () => {
    expect(hubTabs(INFO)).toEqual(["chart", "option-chain", "futures", "overview"]);
    expect(hubTabs(PLAIN)).toEqual(["chart", "overview"]);
    expect(hubTabs({ ...INFO, has_futures: false })).toEqual(["chart", "option-chain", "overview"]);
    expect(hubTabs({ ...INFO, has_options: false })).toEqual(["chart", "futures", "overview"]);
    expect(hubTabs(null)).toEqual([]);
    const tabs = (info: HubInfo | null) => [...renderToStaticMarkup(<MemoryRouter><HubTabsView info={info} current="chart" carry={{ expiry: null, timeframe: null }} /></MemoryRouter>)
      .matchAll(/data-hub-tab="([^"]+)"/g)].map((m) => m[1]);
    expect(tabs(INFO)).toEqual(["chart", "option-chain", "futures", "overview"]);
    expect(tabs(PLAIN)).toEqual(["chart", "overview"]);
    expect(tabs(null)).toEqual([]);
  });

  it("the current tab is marked and labelled in plain words", () => {
    const html = renderToStaticMarkup(<MemoryRouter><HubTabsView info={INFO} current="futures" carry={{ expiry: null, timeframe: null }} /></MemoryRouter>);
    expect(html).toMatch(/aria-current="page"[^>]*data-hub-tab="futures"|data-hub-tab="futures"[^>]*aria-current="page"/);
    expect(text(html)).toContain("Chart Option chain Futures Overview");
  });

  it("only a well-formed answer is trusted (a mocked or older server gives no tabs)", () => {
    expect(isHubInfo(INFO)).toBe(true);
    expect(isHubInfo({ detail: "Not found" })).toBe(false);
    expect(isHubInfo(null)).toBe(false);
  });
});

describe("FNO-3 hub tabs: the symbol, expiry and timeframe carry over", () => {
  const carry = { expiry: "2026-11-03", timeframe: "15min" };
  it("every tab's address keeps them", () => {
    expect(hubHref("chart", INFO, carry)).toBe("/charts/NIFTY%2050?expiry=2026-11-03&tf=15min");
    expect(hubHref("option-chain", INFO, carry)).toBe("/option-chain?underlying=NIFTY&expiry=2026-11-03&tf=15min");
    expect(hubHref("futures", INFO, carry)).toBe("/charts/NIFTY%2050?hub=futures&expiry=2026-11-03&tf=15min");
    expect(hubHref("overview", INFO, { expiry: null, timeframe: null })).toBe("/charts/NIFTY%2050?hub=overview");
    // A BSE index keeps its exchange on the Charts page.
    expect(hubHref("chart", { underlying: "SENSEX", spot_symbol: "SENSEX", spot_exchange: "BSE" }, carry)).toBe("/charts/SENSEX?exchange=BSE&expiry=2026-11-03&tf=15min");
  });

  it("the address is read back; anything malformed is dropped", () => {
    expect(readCarry("?expiry=2026-11-03&tf=15min&underlying=NIFTY")).toEqual(carry);
    expect(readCarry("?expiry=tomorrow&tf=<x>")).toEqual({ expiry: null, timeframe: null });
    expect(hubViewOf("?hub=futures")).toBe("futures");
    expect(hubViewOf("?hub=overview")).toBe("overview");
    expect(hubViewOf("?hub=other")).toBeNull();
  });

  it("the option chain opens on the carried expiry while its list holds it, else on the default", async () => {
    const list = { underlying: "NIFTY", today: "2026-10-21", expiries: [{ date: "2026-10-27", kind: "weekly" as const }, { date: "2026-11-03", kind: "weekly" as const }],
      default: "2026-10-27", source: "master" };
    const read = async () => list;
    expect((await loadExpiryChoice("NIFTY", read, "2026-11-03")).expiry).toBe("2026-11-03");
    expect((await loadExpiryChoice("NIFTY", read, "2027-01-01")).expiry).toBe("2026-10-27");
    expect((await loadExpiryChoice("NIFTY", read)).expiry).toBe("2026-10-27");
  });

  it("both pages mount the tab row; the Charts page shows Futures and Overview in place of the chart", () => {
    expect(chainPageSource).toContain('<SymbolHubTabsV2 symbol={symbol} current="option-chain"');
    expect(chartsPageSource).toContain('<HubTabsView info={hubInfo} current={hubView ?? "chart"}');
    expect(chartsPageSource).toContain('lazy(() => import("../fno/FuturesChainV2"))');
    expect(chartsPageSource).toContain('{tab === "chart" && !hubView && (');
    expect(chartsPageSource).toContain('paused: tab !== "chart" || hubView !== null');
  });
});

describe("FNO-3 futures chain: the rows", () => {
  it("each future with LTP, change, OI, OI change and basis; the rollover caption; no made-up price", () => {
    const { html } = view();
    const t = text(html);
    for (const s of ["NIFTY 27 OCT FUT", "NIFTY 24 NOV FUT", "NIFTY 29 DEC FUT", "24,612.50", "+112.50", "+0.46%", "12.5 L", "+45,000", "+62.50", "+0.26%"]) {
      expect(t).toContain(s);
    }
    expect(t).toContain("Near month · Expires 27 Oct 2026 · 6 days");
    expect(t).toContain("Rollover NIFTY 27 OCT FUT → NIFTY 24 NOV FUT");
    expect(t).toContain(CHAIN.rollover!.caption!);
    expect(t).toContain("Spot NIFTY 50 24,550.00");
    // The far month has no price from the broker: dashes, never 0.
    expect(html).toMatch(/data-future-row="NIFTY26DECFUT"[\s\S]*?data-cell="ltp">–</);
    expect(t).toContain("Practice mode (PAPER)");
    expect(t).not.toMatch(ADVICE);
    expect(futuresSource).not.toMatch(/sampleData|generateSample|Math\.random/);
  });

  it("the states in plain words", () => {
    expect(text(view({ data: null, problem: "no-session" }).html)).toContain(FUTURES_TEXT["no-session"]);
    expect(text(view({ data: { ...CHAIN, rows: [], rollover: null } }).html)).toContain(FUTURES_TEXT.empty);
    expect(text(view({ data: { ...CHAIN, market_open: false } }).html)).toContain("Market closed — showing the last prices.");
    expect(text(view({ data: { ...CHAIN, oi_change: { status: "building", source: "first_reading", since: null } } }).html))
      .toContain("OI change will show after the first few minutes.");
    expect(futuresProblemOf(new ApiError(409, "x"))).toBe("no-session");
    expect(futuresProblemOf(new ApiError(409, "x", "", "", null, "C"))).toBe("plan");
    expect(futuresProblemOf(new ApiError(429, "x"))).toBe("budget");
    expect(futuresProblemOf(new Error("x"))).toBe("failed");
  });

  it("expiry words", () => {
    expect(expiryText({ expiry: "2026-10-27", days_to_expiry: 0 })).toBe("Expires today");
    expect(expiryText({ expiry: "2026-10-27", days_to_expiry: 1 })).toBe("Expires 27 Oct 2026 · 1 day");
  });
});

describe("FNO-3 futures chain: each row's actions", () => {
  it("Chart opens the future's own chart with the timeframe", () => {
    const charts = all(view().els, "data-action", "chart");
    expect(charts.map((c) => c.to)).toContain("/charts/NIFTY26OCTFUT?tf=15min&exchange=NFO");
  });

  it("Buy / Sell open the PAPER ticket for the future: lots, no strike, a stop-loss needed on both sides", () => {
    const { on: h, els } = view();
    all(els, "data-action", "buy")[0].onClick!();
    all(els, "data-action", "sell")[0].onClick!();
    expect(h.onTrade.mock.calls.map(([r, s]) => [r.tradingsymbol, s])).toEqual([["NIFTY26OCTFUT", "BUY"], ["NIFTY26OCTFUT", "SELL"]]);
    const target = futureTarget(row());
    expect(target).toMatchObject({ right: "FUT", strike: null, lotSize: 75, label: "NIFTY 27 OCT FUT", ltp: 24612.5 });
    const ask = askOf(target, { ...newDraft("BUY", { product: "NRML" }), stopLoss: "24500" }, "ref-12345678");
    expect(ask).toMatchObject({ underlying: "NIFTY", expiry: "2026-10-27", right: "FUT", strike: null, stop_loss: 24500 });
    // DEF-0 D7: no stop is forced on any side - only the trader's own setting asks for one (the server says so)
    expect(draftProblems(newDraft("BUY", { product: "NRML" }), "FUT")).toEqual([]);
    expect(draftProblems(newDraft("BUY", { product: "NRML" }))).toEqual([]);
    expect(stopHint("BUY", "FUT")).toBe("(optional)");
  });

  it("without the PAPER account there is no Buy / Sell and no basket; without watchlists no watchlist button", () => {
    const off = view({ trading: false, watchlistsOn: false });
    expect(all(off.els, "data-action", "buy")).toEqual([]);
    expect(all(off.els, "data-action", "basket")).toEqual([]);
    expect(all(off.els, "data-action", "watchlist")).toEqual([]);
    expect(all(off.els, "data-action", "chart").length).toBeGreaterThan(0);
    expect(text(off.html)).not.toContain("Practice mode (PAPER)");
  });

  it("Add to basket: a Buy or a Sell leg of one lot in the FNO-1 basket store", () => {
    const closed = view();
    all(closed.els, "data-action", "basket")[0].onClick!();
    expect(closed.on.onPanel).toHaveBeenCalledWith({ key: "NIFTY26OCTFUT", kind: "basket" });
    const open = view({ panel: { key: "NIFTY26OCTFUT", kind: "basket" } });
    all(open.els, "data-action", "basket-buy")[0].onClick!();
    all(open.els, "data-action", "basket-sell")[0].onClick!();
    expect(open.on.onBasket.mock.calls.map(([r, s]) => [r.tradingsymbol, s])).toEqual([["NIFTY26OCTFUT", "BUY"], ["NIFTY26OCTFUT", "SELL"]]);

    const store = new BasketStore([]);
    expect(addFutureToBasket(store, row(), "SELL")).toEqual({ tone: "done", text: "Added to the basket: Sell 1 lot of NIFTY 27 OCT FUT." });
    expect(store.snapshot()[0]).toMatchObject({ underlying: "NIFTY", expiry: "2026-10-27", right: "FUT", strike: 0, side: "SELL", lots: 1, price: 24612.5 });
    expect(futureLeg(row(), "BUY")).toMatchObject({ right: "FUT", strike: 0 });
  });

  it("Add to watchlist: the future on its exchange, in the list picked (a new F&O list when there is none)", async () => {
    const lists = { list: vi.fn(), create: vi.fn().mockResolvedValue({}), save: vi.fn().mockResolvedValue({}) };
    const picker = view({ panel: { key: "NIFTY26OCTFUT", kind: "watchlist" }, watchlists: [{ id: 3, name: "Mine", items: [], version: 1 }] });
    const btn = picker.els.find((e) => (e.props as Record<string, unknown>)["data-watchlist"] === 3)!.props as { onClick: () => void };
    btn.onClick();
    expect(picker.on.onWatchlist.mock.calls[0][1]).toMatchObject({ id: 3 });
    const contract = futureContract(row());
    expect(await watchlistAdd(lists as never, contract, null)).toEqual({ tone: "done", text: "Added NIFTY 27 OCT FUT to a new list, F&O." });
    expect(lists.create).toHaveBeenCalledWith("F&O", [{ symbol: "NIFTY26OCTFUT", exchange: "NFO" }]);
    const list: WatchlistV2 = { id: 3, name: "Mine", items: [], version: 1 };
    await watchlistAdd(lists as never, contract, list);
    expect(lists.save).toHaveBeenCalled();
  });
});

describe("FNO-3 futures in the basket (FNO-2 panel)", () => {
  const fut = { ...futureLeg(row(), "BUY"), stopLoss: null, iv: null };
  const call = { underlying: "NIFTY", expiry: "2026-10-27", strike: 24800, right: "CE" as const, side: "SELL" as const, lots: 1, price: 90, label: null, stopLoss: 135, iv: 14 };

  it("the badge opens the basket panel on the option chain page, with the leg count", () => {
    expect(basketHref("NIFTY", "15min")).toBe("/option-chain?underlying=NIFTY&basket=open&tf=15min");
    const badge = view({ basketCount: 2 }).els.find((e) => (e.props as Record<string, unknown>)["data-futures-basket"] !== undefined)!.props as Record<string, unknown>;
    expect(badge.to).toBe("/option-chain?underlying=NIFTY&basket=open&tf=15min");
    expect(badge["aria-label"]).toBe("Open the basket: 2 legs");
    expect(chainPageSource).toContain('next.delete("basket")');
  });

  it("a future leg is written as traders write it, sent with no strike, and needs a stop-loss on either side", () => {
    expect(contractLabel("NIFTY", "2026-10-27", 0, "FUT")).toBe("NIFTY 27 OCT FUT");
    expect(legLabel({ ...fut, label: null })).toBe("NIFTY 27 OCT FUT");
    expect(basketProblems([fut])).toEqual([]);                              // DEF-0 D7: a stop is his choice
    expect(basketProblems([{ ...fut, side: "SELL" }])).toEqual([]);
    expect(basketProblems([{ ...fut, stopLoss: 24500 }, call])).toEqual([]);
    const ask = basketAsk([{ ...fut, stopLoss: 24500 }, call], 24550, "NRML");
    expect(ask.legs[0]).toMatchObject({ right: "FUT", strike: null, side: "BUY", stop_loss: 24500, price: 24612.5 });
    expect(ask.legs[1]).toMatchObject({ right: "CE", strike: 24800, stop_loss: 135 });
  });

  it("saved and loaded with no strike; handed to the Options Builder at its entry price", () => {
    const saved = savedOf([{ ...fut, stopLoss: 24500 }], "Long future", "NRML", new Date("2026-10-21T05:30:00Z"));
    expect(saved.legs[0]).toEqual({ expiry: "2026-10-27", strike: null, right: "FUT", side: "BUY", lots: 1, price: 24612.5, stop_loss: 24500 });
    expect(legsOf(saved)[0]).toMatchObject({ right: "FUT", strike: 0, stopLoss: 24500 });
    const h = builderHandoff([fut, call], [75, 75], 24550, 50)!;
    expect(h.legs[0]).toMatchObject({ option_type: "FUT", strike: 24612.5, premium: 24612.5, iv: null });
  });
});

describe("FNO-3 Overview: only the figures that have data", () => {
  const day = (iso: string, low: number, high: number, forming = false): Bar => ({ timestamp: `${iso}T00:00:00+05:30`, open: low, high, low, close: high, forming });
  const yearOfBars = (): Bar[] => {
    const out: Bar[] = [];
    const start = Date.parse("2025-10-20T00:00:00+05:30");
    for (let i = 0; i <= 366; i += 7) out.push(day(new Date(start + i * 86400000).toISOString().slice(0, 10), 100 + i, 110 + i));
    return out;
  };

  it("the day range (today's bar while it forms), the 52-week range only when the bars cover a year", () => {
    const bars = yearOfBars();
    expect(dayRange([...bars, day("2026-10-22", 480, 495, true)])).toEqual({ low: 480, high: 495, which: "today" });
    expect(dayRange(bars)?.which).toBe("last");
    expect(dayRange([])).toBeNull();
    const year = yearRange(bars)!;
    expect(year.high).toBe(110 + 364);
    expect(year.low).toBeGreaterThanOrEqual(100);
    expect(yearRange(bars.slice(-10))).toBeNull();                            // ten weeks of bars: no 52-week range
  });

  it("the snapshot leaves out what has no data; F&O facts from the hub", () => {
    const quote = { symbol: "NIFTY 50", exchange: "NSE", ltp: 24550, change: 100, change_pct: 0.41, missing: false };
    const full = text(renderToStaticMarkup(<OverviewView symbol="NIFTY 50" info={INFO} data={{ quote, bars: yearOfBars() }} />));
    for (const s of ["Last price 24,550.00", "Change +100.00 (+0.41%)", "52-week range", "F&O Yes", "Lot size 75", "Next option expiries 27 Oct 2026 · 3 Nov 2026",
      "Futures expiries 27 Oct 2026 · 24 Nov 2026 · 29 Dec 2026"]) expect(full).toContain(s);
    const bare = renderToStaticMarkup(<OverviewView symbol="ZOMATOX" info={PLAIN} data={{ quote: null, bars: [] }} />);
    expect(bare).not.toMatch(/data-fact="(ltp|change|day-range|year-range|lot|expiries)"/);
    expect(text(bare)).toContain("F&O No");
    expect(text(bare)).toContain("No prices for ZOMATOX from your broker just now.");
    expect(text(full)).not.toMatch(ADVICE);
  });
});
