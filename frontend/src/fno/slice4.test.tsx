import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { leg, liveChain } from "../optionChain/chain.fixture";
import type { ChainRow } from "../optionChain/types";
import positionsSource from "../trading/PositionsV2.tsx?raw";
import type { OHLCVBar } from "../types";
import basketSource from "./BasketPanelV2.tsx?raw";
import type { CockpitUnit } from "./cockpit";
import CockpitDeskV2, { ChainRows } from "./CockpitDeskV2";
import deskSource from "./CockpitDeskV2.tsx?raw";
import { PANELS, basketOn, chainAround, deskUnderlyings, heldOn } from "./desk";
import type { RiskBudget } from "./insights";
import type { JournalDraft } from "./journal";
import { JournalDraftsView, SnapshotV2 } from "./JournalDraftsV2";
import journalSource from "./JournalDraftsV2.tsx?raw";
import ticketSource from "./OrderTicketV2.tsx?raw";
import { Review } from "./OrderTicketV2";
import type { PosLeg } from "./positionInsights";
import {
  CHECKS_NOTE, EVENTS_UNREAD, MIN_TO_COMPARE, NO_CHAIN, eventLine, gapPct, legLiquidity, liquidityLine, marginLine, type EventRisk,
} from "./riskParts";
import { RiskChecksV2 } from "./RiskPartsV2";
import riskPartsSource from "./RiskPartsV2.tsx?raw";
import { MAX_BARS, cropBars, snapshotGeometry, snapshotWindow } from "./snapshot";

/**
 * F&O workspace 8b, slice 4 (owner order sections 8b d, i, k and 9), from fixtures: the risk line's events / liquidity /
 * margin parts say only what ATP's own data says (and say so when it is missing), the desk layout of the cockpit reads
 * and never trades, and the journal's chart snapshot is drawn from candles on the page (nothing stored). Made-up numbers.
 */
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, "\"").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const CALL_WORDS = /\b(buy|sell|target|stop[- ]?loss|should|recommend\w*|advice)\b/i;

// --- 8b-d: events ------------------------------------------------------------------------------------------------------

const EV = (over: Partial<EventRisk> = {}): EventRisk => ({
  underlying: "NIFTY", from: "2026-10-27", until: "2026-11-03", clipped: false, events: [], more: 0, calendar_empty: false, message: null,
  source: "Your events calendar", blocks: false, ...over,
});

describe("8b-d the events part", () => {
  it("lists the calendar's events before the expiry with his own event rule, as a warning", () => {
    const line = eventLine(EV({ events: [
      { date: "2026-10-30", kind: "RBI_POLICY", name: "RBI policy", scope: "all indices", rule: "Your event rule that day: no new entries.", source: "market" },
      { date: "2026-11-02", kind: "RESULTS", name: "Results", scope: "RELIANCE", rule: null, source: "company" },
    ], more: 2 }), "2026-11-03");
    expect(line?.tone).toBe("warn");
    expect(line?.text).toBe("Before the 3 Nov 2026 expiry: RBI policy on 30 Oct 2026 (all indices). Your event rule that day: no new entries; "
      + "Results on 2 Nov 2026 (RELIANCE). And 2 more.");
  });
  it("says plainly when nothing is in the calendar, when the calendar is empty and when it could not be read", () => {
    expect(eventLine(EV(), "2026-11-03")).toMatchObject({ tone: "info", text: "No event in your events calendar between today and the 3 Nov 2026 expiry." });
    expect(eventLine(EV({ clipped: true, until: "2027-02-24" }), "2027-06-24")?.text).toContain("(read up to 24 Feb 2027)");
    expect(eventLine(EV({ calendar_empty: true, message: "The events calendar has no entries." }), "2026-11-03")?.text).toBe("The events calendar has no entries.");
    expect(eventLine("error", "2026-11-03")?.text).toBe(EVENTS_UNREAD);
    expect(eventLine(null, "2026-11-03")).toBeNull();                                        // still reading: nothing yet
  });
});

// --- 8b-d: liquidity ---------------------------------------------------------------------------------------------------

/** Ten strikes: tight quotes and deep OI near the middle, the 25,400 call thin. */
function ladder(): ChainRow[] {
  return Array.from({ length: 10 }, (_, i) => {
    const strike = 24500 + i * 100;
    const thin = strike === 25400;
    return {
      strike,
      call: leg({ ltp: 100 - i * 9, bid: thin ? 2 : 99.5 - i * 9, ask: thin ? 8 : 100.5 - i * 9, oi: thin ? 900 : 200_000 + i * 1000, volume: thin ? 0 : 50_000 }),
      put: leg({ ltp: 20 + i * 9, bid: 19.5 + i * 9, ask: 20.5 + i * 9, oi: 150_000, volume: 40_000 }),
    };
  });
}

describe("8b-d the liquidity part", () => {
  it("states a liquid strike's facts", () => {
    const l = legLiquidity(ladder(), 24600, "CE", "24600 CE");
    expect(l).toMatchObject({ onScreen: true, wide: false, lowOi: false });
    expect(liquidityLine([l])).toMatchObject({ tone: "info", text: "Liquidity: bid-ask gap 1.00 (1.1% of the price), open interest 2,01,000, volume 50,000 today." });
  });
  it("warns on a wide gap, low open interest and no trades today, compared with the chain on screen", () => {
    const l = legLiquidity(ladder(), 25400, "CE", "25400 CE");
    expect(l).toMatchObject({ wide: true, lowOi: true, volume: 0 });
    const line = liquidityLine([l]);
    expect(line?.tone).toBe("warn");
    expect(line?.text).toContain("wider than most strikes on screen");
    expect(line?.text).toContain("open interest 900, among the lowest on this side of the chain");
    expect(line?.text).toContain("no trades in it today");
  });
  it("says what is missing: no quote, a strike not on screen, no chain at all; never compares a short chain", () => {
    const rows = ladder();
    rows[0].call = leg({ ltp: 5, bid: null, ask: 6, oi: 0 });
    expect(liquidityLine([legLiquidity(rows, 24500, "CE", "24500 CE")])?.text).toBe("Liquidity: no bid quoted right now; no open interest yet.");
    expect(liquidityLine([legLiquidity(rows, 26000, "PE", "26000 PE")])?.text).toContain("not on the chain on screen");
    expect(liquidityLine([], false)).toMatchObject({ tone: "info", text: NO_CHAIN });
    const short = ladder().slice(0, MIN_TO_COMPARE / 2 - 1);
    expect(legLiquidity(short, 24500, "CE", "x")).toMatchObject({ wide: null, lowOi: null });
    expect(gapPct({ bid: 10, ask: 9 })).toBeNull();
  });
  it("a basket names only the thin legs, or says every leg is quoted", () => {
    const rows = ladder();
    expect(liquidityLine([legLiquidity(rows, 24600, "CE", "A"), legLiquidity(rows, 24700, "PE", "B")])?.tone).toBe("info");
    const line = liquidityLine([legLiquidity(rows, 24600, "CE", "A"), legLiquidity(rows, 25400, "CE", "B")]);
    expect(line?.tone).toBe("warn");
    expect(line?.text.startsWith("Liquidity: B:")).toBe(true);
    expect(line?.text).not.toContain("A:");
  });
});

// --- 8b-d: margin ------------------------------------------------------------------------------------------------------

const BUDGET: RiskBudget = { capital: 500_000, per_trade: { amount: 5_000, pct: 1 }, daily: { amount: 15_000, pct: 3 }, source: "Your risk settings",
  paper_balance: { amount: 200_000, source: "x", open_margin_counted: false }, blocks: false };

describe("8b-d the margin part", () => {
  it("sets the margin estimate beside his PAPER balance, a warning when it is more", () => {
    expect(marginLine(50_000, BUDGET)).toMatchObject({ tone: "info", text: "Margin estimate ₹50,000 is 25% of your PAPER balance of ₹2,00,000." });
    expect(marginLine(250_000, BUDGET)).toMatchObject({ tone: "warn", text: "Margin estimate ₹2,50,000 is more than your PAPER balance of ₹2,00,000." });
    expect(marginLine(null, BUDGET)?.text).toBe("The margin is not estimated here, so it is not set beside your PAPER balance of ₹2,00,000.");
    expect(marginLine(50_000, { ...BUDGET, paper_balance: undefined })).toBeNull();
    expect(marginLine(50_000, BUDGET)?.note).toContain("margin your open positions use is not taken off");
  });
});

describe("8b-d the parts are information only", () => {
  it("render as one box of text with no control, and no part uses call words", () => {
    const lines = [eventLine(EV(), "2026-11-03"), liquidityLine([legLiquidity(ladder(), 25400, "CE", "x")]), marginLine(250_000, BUDGET)];
    const html = renderToStaticMarkup(<RiskChecksV2 testId="t" lines={lines} />);
    expect(html).not.toMatch(/<button|<a |<input/);
    expect(text(html)).toContain(CHECKS_NOTE);
    for (const l of lines) expect(l?.text ?? "").not.toMatch(CALL_WORDS);
    expect(renderToStaticMarkup(<RiskChecksV2 testId="t" lines={[null, null]} />)).toBe("");
  });
  it("the ticket's confirm step shows them under the max-loss line; its buttons still read only can_place", () => {
    const preview = {
      mode: "PAPER", practice: "", contract: { underlying: "NIFTY", tradingsymbol: "X", exchange: "NFO", instrument_key: "k", expiry: "2026-11-03", strike: 24500,
        right: "CE", lot_size: 75, tick_size: 0.05, label: "NIFTY 03 NOV 24500 CE", freeze_quantity: null },
      label: "NIFTY 03 NOV 24500 CE", side: "BUY", lots: 1, lot_size: 75, units: 75, order_type: "MARKET", product: "MIS", price: 150, price_source: "live",
      fill_price: 150, fill_note: null, stop_loss: null, margin: { value: 11_250, basis: "", estimated: true }, max_loss: { value: 11_250, basis: "" },
      charges: null, risk: {}, errors: [], refusals: [], warnings: [], can_place: true, confirm_label: null, as_of: "",
    } as unknown as Parameters<typeof Review>[0]["preview"];
    const html = text(renderToStaticMarkup(<MemoryRouter><Review preview={preview} budget={BUDGET} events={EV()} liquidity={liquidityLine([], false)} /></MemoryRouter>));
    expect(html).toContain("Before you place (information only)");
    expect(html).toContain("No event in your events calendar between today and the 3 Nov 2026 expiry.");
    expect(html).toContain(NO_CHAIN);
    expect(html).toContain("Margin estimate ₹11,250 is 5.6% of your PAPER balance");
    expect(ticketSource).toContain("disabled={busy || !step.preview.can_place}");          // unchanged: the parts are not read by it
    expect(riskPartsSource).not.toMatch(/disabled|onClick/);
    expect(basketSource).toContain("<RiskChecksV2 testId=\"basket-confirm\"");
  });
});

// --- 8b-i: the desk ----------------------------------------------------------------------------------------------------

const pos = (id: number, over: Partial<PosLeg>): PosLeg => ({
  id, label: `NIFTY ${id}`, symbol: `NIFTY${id}`, exchange: "NFO", underlying: "NIFTY", spotSymbol: "NIFTY 50", spotExchange: "NSE", expiry: "2026-11-03",
  strike: 24600, right: "CE", side: "SELL", units: 75, lotSize: 75, entry: 80, ltp: 60, exitLevel: null, profitLevel: null, ...over,
});
const UNITS: CockpitUnit[] = [
  { key: "basket:1", title: "Basket", pnl: 100, legs: [pos(1, { strike: 24600 }), pos(2, { strike: 24700, side: "BUY" })] },
  { key: "trade:3", title: "BANKNIFTY", pnl: 10, legs: [pos(3, { underlying: "BANKNIFTY", spotSymbol: "NIFTY BANK", strike: 52000, right: "PE" })] },
  { key: "trade:4", title: "NIFTY far", pnl: 5, legs: [pos(4, { strike: 24300, right: "PE", expiry: "2026-10-28" })] },
];

describe("8b-i the desk: chart + chain + basket beside the cockpit, read only", () => {
  it("follows the underlying with the most legs, on its nearest expiry held", () => {
    expect(deskUnderlyings(UNITS).map((u) => [u.underlying, u.legs, u.expiry])).toEqual([["NIFTY", 3, "2026-10-28"], ["BANKNIFTY", 1, "2026-11-03"]]);
    expect(heldOn(UNITS, "NIFTY", "2026-11-03").map((h) => h.label)).toEqual(["24,600 CE short", "24,700 CE long"]);
  });
  it("shows the chain from two strikes below his lowest to two above his highest (none held: around ATM)", () => {
    const rows = liveChain().rows;                                                           // 24,200 .. 24,800
    expect(chainAround(rows, [24600, 24700], 24500).map((r) => r.strike)).toEqual([24400, 24500, 24600, 24700, 24800]);
    expect(chainAround(rows, [], 24500).map((r) => r.strike)).toEqual([24300, 24400, 24500, 24600, 24700]);
    expect(chainAround([], [24600], 24500)).toEqual([]);
    const html = renderToStaticMarkup(<ChainRows chain={liveChain()} held={heldOn(UNITS, "NIFTY", "2026-11-03")} />);
    expect(html).not.toMatch(/<button|<input/);
    expect(text(html)).toContain("Highlighted: the contracts you hold.");
  });
  it("his basket's legs on the underlying only", () => {
    const b = [{ id: "a", underlying: "NIFTY", expiry: "2026-11-03", strike: 1, right: "CE" as const, side: "BUY" as const, lots: 1, price: null, label: null, iv: null },
      { id: "b", underlying: "BANKNIFTY", expiry: "2026-11-03", strike: 1, right: "CE" as const, side: "BUY" as const, lots: 1, price: null, label: null, iv: null }];
    expect(basketOn(b, "NIFTY").map((l) => l.id)).toEqual(["a"]);
  });
  it("renders the four panels with a phone quick switch; links open the pages, nothing places an order", () => {
    const html = renderToStaticMarkup(<MemoryRouter><CockpitDeskV2 units={UNITS} alertsOn={false}
      deskApi={{ candles: () => new Promise(() => undefined), chain: () => new Promise(() => undefined) }}
      cockpitApi={{ greeks: () => new Promise(() => undefined), alertRules: () => new Promise(() => undefined) }} /></MemoryRouter>);
    for (const p of PANELS) expect(html).toContain(`data-desk-panel="${p.key}"`);
    expect(html).toContain("data-desk=\"switch\"");
    expect(html).toContain("lg:hidden");
    expect(html).toContain("href=\"/option-chain?underlying=NIFTY&amp;basket=open\"");
    expect(text(html)).toContain("Panels follow");
    expect(deskSource).not.toMatch(/\.place\(|\/order|execute|exitNow/);
    expect(positionsSource).toContain("<CockpitDeskV2 units={cockpit}");
  });
});

// --- 8b-k: the chart snapshot ------------------------------------------------------------------------------------------

const bar = (iso: string, c: number): OHLCVBar => ({ timestamp: iso, open: c - 1, high: c + 2, low: c - 3, close: c, volume: 1 });

describe("8b-k the journal's chart snapshot, drawn on the page", () => {
  it("reads a finer timeframe for a shorter trade, the history up to its closing day only", () => {
    const now = Date.parse("2026-10-19T06:00:00Z");
    expect(snapshotWindow(Date.parse("2026-10-16T04:45:00Z"), Date.parse("2026-10-16T08:50:00Z"), now)).toEqual({ timeframe: "5min", lookbackDays: 4, before: "2026-10-17" });
    expect(snapshotWindow(Date.parse("2026-10-14T04:45:00Z"), Date.parse("2026-10-16T08:50:00Z"), now).timeframe).toBe("15min");
    expect(snapshotWindow(Date.parse("2026-10-01T04:45:00Z"), Date.parse("2026-10-16T08:50:00Z"), now).timeframe).toBe("60min");
    expect(snapshotWindow(Date.parse("2026-08-01T04:45:00Z"), Date.parse("2026-10-16T08:50:00Z"), now)).toMatchObject({ timeframe: "day" });
    expect(snapshotWindow(Date.parse("2026-10-19T04:00:00Z"), Date.parse("2026-10-19T05:00:00Z"), now).before).toBeNull();   // closed today
  });
  it("crops to the trade with a margin either side and marks the bars it opened and closed on", () => {
    const start = Date.parse("2026-10-16T03:45:00Z");
    const bars = Array.from({ length: 400 }, (_, i) => bar(new Date(start + i * 300_000).toISOString(), 100 + i));
    const opened = Date.parse("2026-10-16T05:00:00Z");
    const closed = Date.parse("2026-10-16T06:00:00Z");
    const cut = cropBars(bars, opened, closed, "5min");
    expect(cut.length).toBeLessThanOrEqual(MAX_BARS);
    expect(Date.parse(cut[0].timestamp)).toBeLessThan(opened);
    expect(Date.parse(cut[cut.length - 1].timestamp)).toBeGreaterThan(closed);
    const g = snapshotGeometry(cut, [{ at: opened, label: "Opened" }, { at: closed, label: "Closed" }], 600, 200, [{ price: 1, label: "far" }])!;
    expect(g.markers[0].x).toBeLessThan(g.markers[1].x);
    expect(g.outside).toEqual(["far"]);
    expect(snapshotGeometry([], [], 600, 200)).toBeNull();
  });
  it("draws only on his tap (one candle read), says so plainly when the history is missing, and stores nothing", () => {
    const draft = { key: "trade:1", kind: "trade", title: "NIFTY 24500 CE", underlying: "NIFTY", spot_symbol: "NIFTY 50", spot_exchange: "NSE", trade_ids: [1],
      opened_at: "2026-10-16T04:45:00+00:00", closed_at: "2026-10-16T08:50:00+00:00", legs: [], pnl: 10, charges: 0, placed_by: "you", strategy: null,
      signal: null, notes: null, status: "draft" } as JournalDraft;
    let reads = 0;
    const html = renderToStaticMarkup(<SnapshotV2 draft={draft} candles={async () => { reads += 1; return []; }} />);
    expect(text(html)).toContain("Show the chart");
    expect(reads).toBe(0);
    const view = renderToStaticMarkup(<JournalDraftsView state={{ status: "ok", data: { mode: "PAPER", days: 14, items: [draft] } }} onSave={async () => undefined}
      candles={async () => []} />);
    expect(view).toContain("data-draft-chart=\"show\"");
    expect(renderToStaticMarkup(<SnapshotV2 draft={{ ...draft, closed_at: null }} candles={async () => []} />)).toBe("");
    expect(journalSource).not.toMatch(/method: "POST"|upload/);
  });
});
