import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi, type Mock } from "vitest";
import type { WatchlistV2 } from "../chart/watchlistsV2";
import { liveChain, leg } from "../optionChain/chain.fixture";
import ChainTableV2 from "../optionChain/ChainTableV2";
import tableSource from "../optionChain/ChainTableV2.tsx?raw";
import { DEFAULT_COLUMNS, formatOiChangePct } from "../optionChain/model";
import pageSource from "../optionChain/OptionChainPageV2.tsx?raw";
import ladderSource from "../optionChain/ChainLadderV2.tsx?raw";
import type { FnoContract, HeldLeg, OrderPreview, OrderResult } from "./api";
import apiSource from "./api.ts?raw";
import { BasketStore, MAX_LEGS } from "./basket";
import {
  ActionSheetBody, alertCondition, alertSave, basketAdd, chartLink, watchlistAdd, type ActionTarget, type BodyHandlers, type Panel,
} from "./ChainActionSheetV2";
import sheetSource from "./ChainActionSheetV2.tsx?raw";
import { ChainBottomStripV2 } from "./ChainTradingV2";
import tradingSource from "./ChainTradingV2.tsx?raw";
import { PracticeFrame, Review, TicketBody, type Step } from "./OrderTicketV2";
import ticketSource from "./OrderTicketV2.tsx?raw";
import {
  askOf, clampLots, draftProblems, heldPnl, needs, newDraft, resultText, shortcutSide, unitsOf, unitsText, type Draft, type TicketTarget,
} from "./ticketModel";

/**
 * FNO-1 (owner order sections 3, 6 and 9): the chain's action sheet (each action), the ONE order ticket (lots and units,
 * the order types, the confirm step with the margin and max loss before placing, the risk messages, B / S), the "P"
 * markers, the bottom strip and the PAPER frame. No DOM here: the sheet's and the ticket's bodies have no state, so they
 * are rendered directly and their buttons' handlers called (`elements`); clicks in a browser are in e2e/chain-ticket-v2.
 */
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, "\"").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const noop = () => undefined;
// Advice words (the Buy / Sell buttons of the user's own manual order are not advice and are allowed).
const ADVICE = /\b(should|recommend\w*|advice|advise|best|target\w*|guarantee\w*|sure[- ]?shot)\b/i;

/** Every element of a tree, expanding this module's own state-free components (no DOM needed). */
const EXPAND = new Set(["Segmented", "Line", "Inline", "Review", "TicketBody", "ActionSheetBody", "PracticeFrame"]);
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
const by = (els: ReactElement[], attr: string, value: string) => {
  const el = els.find((e) => (e.props as Record<string, unknown>)[attr] === value);
  expect(el, `${attr}=${value}`).toBeTruthy();
  return el!.props as Record<string, unknown> & { onClick?: () => void; disabled?: boolean };
};
const has = (els: ReactElement[], attr: string, value: string) => els.some((e) => (e.props as Record<string, unknown>)[attr] === value);

const CONTRACT: FnoContract = {
  underlying: "NIFTY", tradingsymbol: "NIFTY26NOV24500CE", exchange: "NFO", instrument_key: "NSE_FO|1", expiry: "2026-11-03", strike: 24500,
  right: "CE", lot_size: 75, tick_size: 0.05, label: "NIFTY 03 NOV 24500 CE", freeze_quantity: null,
};
const TARGET: ActionTarget = {
  underlying: "NIFTY", expiry: "2026-11-03", strike: 24500, right: "CE", spotSymbol: "NIFTY 50",
  leg: leg({ ltp: 250, bid: 249.5, ask: 250.5, oi: 400_000 }),
};
const HELD: HeldLeg = {
  trade_id: 7, tradingsymbol: "NIFTY26NOV24500CE", strike: 24500, right: "CE", side: "BUY", quantity: 75, lots: 1, lot_size: 75,
  entry_price: 240, mark_price: 245, pnl: 375, stop_loss: 0.05,
};

function handlers(): BodyHandlers & Record<keyof BodyHandlers, Mock> {
  return { onTrade: vi.fn(), onExit: vi.fn(), onPanel: vi.fn(), onBasket: vi.fn(), onWatchlist: vi.fn(), onLevel: vi.fn(), onAlert: vi.fn(), onChart: vi.fn() };
}

function body(opts: { panel?: Panel; held?: HeldLeg[]; contract?: FnoContract | null; watchlists?: WatchlistV2[] | null;
  watchlistsOn?: boolean; alertsOn?: boolean } = {}) {
  const on = handlers();
  const node = (
    <ActionSheetBody target={TARGET} contract={opts.contract === undefined ? CONTRACT : opts.contract} contractProblem={null} held={opts.held ?? []}
      panel={opts.panel ?? null} note={null} watchlists={opts.watchlists ?? null} level="260" watchlistsOn={opts.watchlistsOn ?? true}
      alertsOn={opts.alertsOn ?? true} {...on} />
  );
  return { on, els: elements(node), html: renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>) };
}

describe("FNO-1 chain action sheet: each action", () => {
  it("Buy and Sell open the ticket with that side", () => {
    const { on, els } = body();
    by(els, "data-action", "buy").onClick!();
    by(els, "data-action", "sell").onClick!();
    expect(on.onTrade.mock.calls).toEqual([["BUY"], ["SELL"]]);
  });

  it("Add to basket: a Buy or a Sell leg of one lot, the same contract adds its lots, a full basket says so", () => {
    const closed = body();
    by(closed.els, "data-action", "basket").onClick!();
    expect(closed.on.onPanel).toHaveBeenCalledWith("basket");
    const open = body({ panel: "basket" });
    by(open.els, "data-action", "basket-buy").onClick!();
    by(open.els, "data-action", "basket-sell").onClick!();
    expect(open.on.onBasket.mock.calls).toEqual([["BUY"], ["SELL"]]);

    const store = new BasketStore([]);
    expect(basketAdd(store, TARGET, CONTRACT, "BUY")).toEqual({ tone: "done", text: "Added to the basket: Buy 1 lot of NIFTY 03 NOV 24500 CE." });
    basketAdd(store, TARGET, CONTRACT, "BUY");
    basketAdd(store, TARGET, CONTRACT, "SELL");
    expect(store.snapshot().map((l) => [l.side, l.lots, l.price, l.label])).toEqual([["BUY", 2, 250, CONTRACT.label], ["SELL", 1, 250, CONTRACT.label]]);
    const full = new BasketStore(Array.from({ length: MAX_LEGS }, (_, i) => ({ ...store.snapshot()[0], id: `x${i}`, strike: 20000 + i })));
    expect(basketAdd(full, TARGET, CONTRACT, "BUY")?.tone).toBe("warn");
  });

  it("Chart of this strike: the contract on its exchange, else the underlying", () => {
    expect(chartLink(TARGET, CONTRACT)).toBe("/charts/NIFTY26NOV24500CE?exchange=NFO");
    expect(chartLink(TARGET, null)).toBe("/charts/NIFTY%2050");
    const { els, on } = body();
    expect(by(els, "data-action", "chart").to).toBe("/charts/NIFTY26NOV24500CE?exchange=NFO");
    by(els, "data-action", "chart").onClick!();
    expect(on.onChart).toHaveBeenCalled();
  });

  it("Add to watchlist: the contract on NFO into a chosen list (or a new F&O list); hidden while watchlists are off", async () => {
    const list: WatchlistV2 = { id: 3, name: "Mine", items: [{ symbol: "RELIANCE", exchange: "NSE" }], version: 4 };
    const { els, on } = body({ panel: "watchlist", watchlists: [list] });
    by(els, "data-watchlist", 3 as unknown as string);
    const lists = { list: vi.fn(), create: vi.fn(async () => list), save: vi.fn(async (l: WatchlistV2) => l) };
    expect(await watchlistAdd(lists, CONTRACT, list)).toEqual({ tone: "done", text: "Added NIFTY 03 NOV 24500 CE to Mine." });
    expect(lists.save).toHaveBeenCalledWith({ ...list, items: [...list.items, { symbol: "NIFTY26NOV24500CE", exchange: "NFO" }] });
    const holding = { ...list, items: [{ symbol: "NIFTY26NOV24500CE", exchange: "NFO" }] };
    expect((await watchlistAdd(lists, CONTRACT, holding))?.text).toBe("NIFTY 03 NOV 24500 CE is already in Mine.");
    expect(lists.save).toHaveBeenCalledTimes(1);
    await watchlistAdd(lists, CONTRACT, null);
    expect(lists.create).toHaveBeenCalledWith("F&O", [{ symbol: "NIFTY26NOV24500CE", exchange: "NFO" }]);
    const none = body({ panel: "watchlist", watchlists: [] });
    by(none.els, "data-watchlist", "new").onClick!();
    expect(none.on.onWatchlist).toHaveBeenCalledWith(null);
    expect(on.onWatchlist).not.toHaveBeenCalled();
    expect(has(body({ watchlistsOn: false }).els, "data-action", "watchlist")).toBe(false);
    expect(by(body({ contract: null }).els, "data-action", "watchlist").disabled).toBe(true);    // waits for the contract
  });

  it("Price alert: an instrument rule on the contract's price that only notifies; hidden while alerts are off", async () => {
    const { els, on } = body({ panel: "alert" });
    by(els, "data-action", "alert-save").onClick!();
    expect(on.onAlert).toHaveBeenCalled();
    const create = vi.fn(async () => ({}));
    expect(await alertSave(create, CONTRACT, "260", 250)).toEqual({ tone: "done", text: "Alert saved: you will be told when NIFTY 03 NOV 24500 CE is at or above 260.00." });
    expect(create).toHaveBeenCalledWith(expect.objectContaining({ kind: "instrument", symbol: "NIFTY26NOV24500CE", exchange: "NFO", condition: "close >= 260" }));
    expect(alertCondition(240, 250)).toEqual({ condition: "close <= 240", words: "at or below", sign: "≤" });
    expect(create).toHaveBeenCalledWith(expect.objectContaining({ name: "Alert when NIFTY 03 NOV 24500 CE ≥ 260" }));   // UI-28 U1.4: plain words
    expect((await alertSave(create, CONTRACT, "", 250))?.tone).toBe("warn");
    expect(create).toHaveBeenCalledTimes(1);
    expect(has(body({ alertsOn: false }).els, "data-action", "alert")).toBe(false);
    expect(sheetSource).not.toMatch(/placeOrder|\/paper\/fno\/order/);           // the sheet itself places nothing
  });

  it("a held contract shows its P&L with Add and Exit; Exit is never disabled", () => {
    const { els, on, html } = body({ held: [HELD, { ...HELD, trade_id: 8, right: "PE" }] });
    expect(text(html)).toContain("Bought 1 lot at 240.00");
    expect(text(html)).toContain("P&L +₹750");                             // at the chain's LTP 250: (250 - 240) x 75
    expect(els.filter((e) => (e.props as Record<string, unknown>)["data-held-trade"] !== undefined)).toHaveLength(1);   // the call only
    const exit = by(els, "data-action", "held-exit");
    expect(exit.disabled).toBeUndefined();
    exit.onClick!();
    by(els, "data-action", "held-add").onClick!();
    expect(on.onExit).toHaveBeenCalledWith(HELD);
    expect(on.onTrade).toHaveBeenCalledWith("BUY");
    expect(heldPnl({ ...HELD, side: "SELL" }, 250)).toBe(-750);
    expect(heldPnl(HELD, null)).toBe(375);                                      // the server's last mark without a chain price
  });

  it("the practice frame is there and no word advises a trade", () => {
    const { html } = body({ held: [HELD], panel: "basket" });
    expect(text(html)).toContain("PAPER");                                   // UI-28 U1.1: one chip
    expect(text(html)).not.toMatch(ADVICE);
  });
});

// --- the ticket ------------------------------------------------------------------------------------------------------

const TT: TicketTarget = { underlying: "NIFTY", expiry: "2026-11-03", strike: 24500, right: "CE", label: CONTRACT.label, lotSize: 75, ltp: 250, bid: 249.5, ask: 250.5 };

function preview(over: Partial<OrderPreview> = {}): OrderPreview {
  return {
    mode: "PAPER", practice: "Practice mode (PAPER): no real order is sent and no money moves.", contract: CONTRACT, label: CONTRACT.label,
    side: "BUY", lots: 2, lot_size: 75, units: 150, order_type: "MARKET", product: "MIS", price: 250, price_source: "live", fill_price: 250,
    fill_note: "Fills at the live price 250.00", stop_loss: null,
    margin: { value: 37500, basis: "Long options only: the premium paid.", estimated: true },
    max_loss: { value: 37500, basis: "The premium you pay, if the option expires worthless." }, charges: { total: 61.2, estimated: true },
    risk: { risk_per_trade: 50000, allowed_lots: 5 }, errors: [], refusals: [], warnings: [], can_place: true,
    confirm_label: "Place PAPER order: Buy 2 lots (150 qty)", as_of: "2026-10-27T05:00:00+00:00", ...over,
  };
}

function ticket(draft: Draft, step: Step = { kind: "edit" }) {
  const on = { onDraft: vi.fn(), onReview: vi.fn(), onPlace: vi.fn(), onBack: vi.fn(), onClose: vi.fn() };
  const node = <TicketBody target={TT} draft={draft} step={step} busy={false} problem={null} idPrefix="t" {...on} />;
  return { on, els: elements(node), html: renderToStaticMarkup(node) };
}

describe("FNO-1 order ticket", () => {
  it("quantity is in lots; the units are lots x the lot size from the instrument master", () => {
    expect(unitsOf(2, 75)).toBe(150);
    expect(unitsText(1, 75)).toBe("1 lot = 75 qty (lot size 75)");
    expect(unitsText(3, 30)).toBe("3 lots = 90 qty (lot size 30)");
    expect(unitsText(2, null)).toBe("2 lots");
    expect([clampLots(0), clampLots(2.6), clampLots(9999)]).toEqual([1, 3, 500]);
    const { els, html, on } = ticket({ ...newDraft("BUY"), lots: 2 });
    expect(text(html)).toContain("2 lots = 150 qty (lot size 75)");
    by(els, "data-ticket", "lots-more").onClick!();
    by(els, "data-ticket", "lots-less").onClick!();
    expect(on.onDraft.mock.calls).toEqual([[{ lots: 3 }], [{ lots: 1 }]]);
  });

  it("Market / Limit / SL / SL-M ask for the right prices, and the price helper fills them", () => {
    expect([needs("MARKET"), needs("LIMIT"), needs("SL"), needs("SL-M")]).toEqual([
      { price: false, trigger: false }, { price: true, trigger: false }, { price: true, trigger: true }, { price: false, trigger: true }]);
    const market = ticket(newDraft("BUY"));
    expect(has(market.els, "data-ticket", "price")).toBe(false);
    expect(has(market.els, "data-ticket", "trigger")).toBe(false);
    by(market.els, "data-option", "SL").onClick!();
    by(market.els, "data-option", "NRML").onClick!();
    expect(market.on.onDraft.mock.calls).toEqual([[{ orderType: "SL" }], [{ product: "NRML" }]]);
    const limit = ticket({ ...newDraft("BUY"), orderType: "LIMIT" });
    expect(has(limit.els, "data-ticket", "price") && !has(limit.els, "data-ticket", "trigger")).toBe(true);
    by(limit.els, "data-helper", "Bid").onClick!();
    expect(limit.on.onDraft).toHaveBeenCalledWith({ price: "249.50" });
    const slm = ticket({ ...newDraft("SELL"), orderType: "SL-M" });
    expect(!has(slm.els, "data-ticket", "price") && has(slm.els, "data-ticket", "trigger")).toBe(true);
    by(slm.els, "data-helper", "Ask").onClick!();
    expect(slm.on.onDraft).toHaveBeenCalledWith({ trigger: "250.50" });
    const mis = { product: "MIS" as const };                                   // owner F1: the user picked a product
    expect(draftProblems({ ...newDraft("BUY", mis), orderType: "SL" })).toEqual(["Enter the limit price", "Enter the trigger price"]);
    expect(draftProblems(newDraft("SELL", mis))).toEqual([]);              // DEF-0 D7: only his own setting asks for a stop
    expect(draftProblems(newDraft("BUY", mis))).toEqual([]);
  });

  it("the request carries lots, the order type and the prices it needs - and no mode (PAPER only)", () => {
    const ask = askOf(TT, { ...newDraft("SELL", { product: "MIS" }), lots: 2, orderType: "LIMIT", price: "251", trigger: "249", stopLoss: "300" }, "ref-12345678");
    expect(ask).toEqual({ underlying: "NIFTY", expiry: "2026-11-03", strike: 24500, right: "CE", side: "SELL", lots: 2, order_type: "LIMIT",
      product: "MIS", price: 251, trigger_price: null, stop_loss: 300, chain_ltp: 250, client_ref: "ref-12345678" });
    expect(ask).not.toHaveProperty("mode");
    expect(apiSource).not.toMatch(/LIVE/);
    expect(apiSource.slice(apiSource.indexOf("export interface OrderAsk"), apiSource.indexOf("export interface MoneyLine"))).not.toContain("mode");
  });

  it("the confirm step shows the margin required and the max loss BEFORE placing; the first step cannot place", () => {
    const edit = ticket(newDraft("BUY"));
    expect(has(edit.els, "data-ticket", "place")).toBe(false);
    by(edit.els, "data-ticket", "review").onClick!();
    expect(edit.on.onReview).toHaveBeenCalled();
    const review = ticket(newDraft("BUY"), { kind: "review", preview: preview() });
    const words = text(review.html);
    for (const part of ["Margin required ₹37,500.00", "Long options only: the premium paid.", "Max loss ₹37,500.00",
      "The premium you pay, if the option expires worthless.", "Quantity 2 lots = 150 qty (lot size 75)", "Charges (estimate) ₹61.20",
      "Buy"]) {
      expect(words).toContain(part);
    }
    by(review.els, "data-ticket", "place").onClick!();
    by(review.els, "data-ticket", "back").onClick!();
    expect(review.on.onPlace).toHaveBeenCalled();
    expect(review.on.onBack).toHaveBeenCalled();
    // A sold option's margin is never made up.
    const sold = text(renderToStaticMarkup(<Review preview={preview({ side: "SELL", margin: { value: null, basis: "A sold option needs your broker's SPAN and exposure margin, which practice mode does not have." } })} />));
    expect(sold).toContain("Margin required – A sold option needs your broker's SPAN");
  });

  it("the risk checks speak plain words and a refused ticket cannot be placed", () => {
    const refusals = ["Your risk per trade (₹10,000) allows 1 lot(s) at this stop-loss. Lower the lots or set a closer stop-loss.",
      "Today's loss limit is reached: no new entries today. Exits still work."];
    const review = ticket(newDraft("BUY"), { kind: "review", preview: preview({ refusals, can_place: false, confirm_label: null }) });
    const words = text(review.html);
    for (const r of refusals) expect(words).toContain(r);
    expect(words).not.toContain("Cannot place");                                 // UI-28 U1.1: only the blocking line says why
    expect(by(review.els, "data-ticket", "place").disabled).toBe(true);
    expect(words).not.toMatch(/Traceback|Exception|RISK_|max_trades_per_day/);
  });

  it("the result in plain words", () => {
    const placed: OrderResult = { ...preview(), status: "PLACED", reasons: [], order_id: 1, trade_id: 2, filled: { price: 250.05, quantity: 150, lots: 2 } };
    expect(resultText(placed)).toBe("Placed in practice mode: Bought 2 lots (150 qty) of NIFTY 03 NOV 24500 CE at 250.05.");
    expect(resultText({ ...placed, status: "REFUSED", filled: null, reasons: ["You have reached your limit of 3 trades for today."] }))
      .toBe("Not placed: You have reached your limit of 3 trades for today.");
  });

  it("keys: B = Buy and S = Sell on the desktop, never while typing or with a modifier", () => {
    const k = (key: string, over: Partial<KeyboardEvent> = {}) => ({ key, ctrlKey: false, metaKey: false, altKey: false, ...over });
    expect(shortcutSide(k("b"), null)).toBe("BUY");
    expect(shortcutSide(k("S"), { tagName: "DIV" } as unknown as EventTarget)).toBe("SELL");
    expect(shortcutSide(k("b"), { tagName: "INPUT" } as unknown as EventTarget)).toBeNull();
    expect(shortcutSide(k("s", { ctrlKey: true }), null)).toBeNull();
    expect(shortcutSide(k("x"), null)).toBeNull();
    expect(ticketSource).toContain('window.addEventListener("keydown", onKey)');
    expect(ticketSource).toContain("setDraft((d) => ({ ...d, side: picked }))");
    expect(text(ticket(newDraft("BUY")).html)).toContain("Keys: B for Buy, S for Sell.");
  });

  it("the PAPER chip is on every ticket (UI-28 U1.1: one chip, the sentence its tooltip), and a phone gets a bottom sheet", () => {
    const chip = renderToStaticMarkup(<PracticeFrame />);
    expect(text(chip).trim()).toBe("PAPER");
    expect(chip).toContain('title="Practice mode: no real order is sent and no money moves."');
    expect(ticketSource).toContain("<PracticeFrame />");
    expect(ticketSource).not.toContain("Place PAPER order");
    expect(ticketSource).toContain("fixed inset-x-0 bottom-0");                 // a bottom sheet on a phone
    expect(ticketSource).toContain("md:right-0");                                // a side panel from md
    expect(ticketSource).toMatch(/min-h-11/);                                    // 44 px targets
    expect(text(ticket(newDraft("BUY")).html)).not.toMatch(ADVICE);
  });
});

// --- the chain: "P" markers, cells, % change, the bottom strip -------------------------------------------------------

describe("FNO-1 on the chain", () => {
  it("a held call or put shows P next to its strike; a call or put cell opens its actions", () => {
    const html = renderToStaticMarkup(<ChainTableV2 live={liveChain()} prefs={DEFAULT_COLUMNS} centreKey="k" onPick={noop} onPickLeg={noop}
      held={new Set(["24500|CE", "24300|PE"])} />);
    expect(html).toMatch(/data-strike="24500"[\s\S]*?data-held="call"[\s\S]*?24,500/);
    expect(html).toMatch(/data-strike="24300"[\s\S]*?24,300[\s\S]*?data-held="put"/);
    expect(html.match(/data-held=/g)).toHaveLength(2);
    expect(text(html)).toContain("(you hold the call)");
    expect(tableSource).toContain("onClick={pick}");
    expect(tableSource).toContain("e.stopPropagation(); onPickLeg(row, side);");
    // Off (no onPickLeg, no held): the OC1 chain, unchanged.
    const plain = renderToStaticMarkup(<ChainTableV2 live={liveChain()} prefs={DEFAULT_COLUMNS} centreKey="k" onPick={noop} />);
    expect(plain).not.toContain("data-held");
    expect(plain).not.toContain("aria-keyshortcuts");
  });

  it("OI change as a count or in % of the OI before it", () => {
    expect(formatOiChangePct(110, 10)).toBe("+10.0%");
    expect(formatOiChangePct(90, -10)).toBe("−10.0%");
    expect(formatOiChangePct(100, 0)).toBe("0.0%");
    expect(formatOiChangePct(10, 10)).toBe("–");
    expect(formatOiChangePct(null, 5)).toBe("–");
    const html = renderToStaticMarkup(<ChainTableV2 live={liveChain()} prefs={{ ...DEFAULT_COLUMNS, pct: true }} centreKey="k" onPick={noop} />);
    expect(html).toContain("+2.2%");                       // 24,800 call: OI 7 L, change +15,000 -> 15,000 / 685,000
  });

  it("the bottom strip: tap ATM to recentre, the change toggle, the basket count - no figure repeated from the summary (CT-1 C4)", () => {
    const onRecentre = vi.fn();
    const onPct = vi.fn();
    const html = renderToStaticMarkup(<ChainBottomStripV2 summary={liveChain().summary} pct={false} onPct={onPct} onRecentre={onRecentre} basketCount={2} />);
    const words = text(html);
    for (const part of ["ATM 24,500", "Count %"]) expect(words).toContain(part);
    for (const part of ["PCR", "Max pain", "ATM IV", "Expiry in"]) expect(words).not.toContain(part);   // in the summary strip only
    expect(html).toContain('aria-label="Basket: 2 legs"');
    expect(words).not.toMatch(/percentile/i);                // not available yet: left out, never made up
    expect(tradingSource).toContain("onRecentre={() => setRecentre((n) => n + 1)}");
    // TERM-2: the chain lives in ChainLadderV2 (the chain page and the Terminal place it); the page passes the recentre.
    expect(pageSource).toContain("centreSuffix={trading.centreSuffix}");
    expect(ladderSource).toContain("centreKey={`${symbol}|${live.expiry ?? \"\"}${centreSuffix}`}");
    expect(tradingSource).toContain('features.on("paper_account")');      // chain trading only with the PAPER account
    expect(text(html)).not.toMatch(ADVICE);
  });

  it("the Greeks toggle is in Columns (verified)", async () => {
    const columns = (await import("../optionChain/ColumnsMenuV2.tsx?raw")).default;
    expect(columns).toContain('{ key: "greeks", label: "Greeks (delta, gamma, theta, vega)" }');
    expect(columns).toContain('{ key: "pct", label: "OI change in %" }');
  });
});

describe("UI-28 U7.5: Required beside Available on the confirm step", () => {
  const budget = { capital: 500_000, per_trade: { amount: 5_000, pct: 1 }, daily: { amount: 15_000, pct: 3 }, source: "Your risk settings",
    paper_balance: { amount: 200_000, source: "x", open_margin_counted: false }, blocks: false } as const;
  it("shows his PAPER balance as Available next to the margin required", () => {
    const html = renderToStaticMarkup(<Review preview={preview({ margin: { value: 50_000, basis: "x" } })} budget={budget} />);
    expect(html).toContain('data-line="available"');
    expect(text(html)).toContain("Available (PAPER)");
    expect(text(html)).not.toContain("Less than the margin required");
  });
  it("says so when the margin is more than the balance; no Available line without a balance", () => {
    const short = text(renderToStaticMarkup(<Review preview={preview({ margin: { value: 250_000, basis: "x" } })} budget={budget} />));
    expect(short).toContain("Less than the margin required");
    expect(renderToStaticMarkup(<Review preview={preview()} />)).not.toContain('data-line="available"');
  });
});
