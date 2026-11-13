import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { formatContracts, formatCount, formatLakhs } from "../ui/format";
import { leg, liveChain, rows } from "./chain.fixture";
import { DEFAULT_COLUMNS, tradingDaysText, formatOi, oiUnitCaption, readColumns, COLUMNS_KEY } from "./model";
import {
  PCR_ZONE_LABEL, bannerTone, calendarDte, classText, dominanceOf, expiryLine, inTradeWindow, netDiffRows, netDiffStatus, netDiffTotal,
  pcrZone, signalTone, todayIst,
} from "./oiDiff";
import OiDiffTableV2, { ChgCell, NetDiffTableBody } from "./OiDiffTableV2";
import { INTERVALS, IntervalPicker, TradeOiBannerView, bannerLines } from "./TradeOiBannerV2";
import bannerSource from "./TradeOiBannerV2.tsx?raw";
import type { OiBannerResponse, OiBannerRow, OiHistoryResponse } from "../oi/types";
import ChainTableV2 from "./ChainTableV2";
import pageSource from "./OptionChainPageV2.tsx?raw";
import diffSource from "./OiDiffTableV2.tsx?raw";
import terminalSource from "../terminal/TerminalPageV2.tsx?raw";
import { ticketTargetOf } from "../fno/ChainTradingV2";
import { TicketBody } from "../fno/OrderTicketV2";
import { LOTS_UNSET_TEXT, firstMissing, newDraft } from "../fno/ticketModel";
import type { ChainRow, NetDiff, NetDiffRow } from "./types";

/** M96 L1 / O1 / Q3 (owner; L1 "taken from the Trade repo AS IS"): Trade's Net Diff table and OI banner, the ticket opened
 * from the table, and OI in lakhs. */

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const noop = () => undefined;

function elements(node: ReactNode, out: ReactElement[] = []): ReactElement[] {
  if (Array.isArray(node)) { node.forEach((n) => elements(n, out)); return out; }
  if (!isValidElement(node)) return out;
  out.push(node);
  elements((node.props as { children?: ReactNode }).children, out);
  return out;
}

const STRIKES = Array.from({ length: 13 }, (_, i) => 24200 + 50 * i);

/** A made-up Trade "Net Diff" answer: ATM 24,500, step 50; CE change 1,000 x (i - 6), PE change 2,000 x (6 - i). */
function netDiff(status: NetDiff["status"] = "ready"): NetDiff {
  const rows: NetDiffRow[] = STRIKES.map((strike, i) => {
    const ce = 1000 * (i - 6);
    const pe = 2000 * (6 - i);
    return { strike, ce_chg: ce, ce_oi: 100_000 + 10_000 * i, ce_ltp: 300 - 20 * i, pe_ltp: 20 * i + 10, pe_oi: 220_000 - 10_000 * i, pe_chg: pe,
      diff: pe - ce, dominance: pe - ce > 0 ? "Bulls" : pe - ce < 0 ? "Bears" : "Neutral" };
  });
  return { status, since: "2026-11-11T03:50:00+00:00", step: 50, atm: 24500, window: 6, pcr: 1.43, rows };
}

function chainRowsFor(strikes: readonly number[]): ChainRow[] {
  return strikes.map((strike) => ({ strike, call: leg({}), put: leg({}) }));
}

describe("M96 L1: Trade's Net Diff table", () => {
  it("Net Diff = PE Chg OI - CE Chg OI, Dominance by its sign", () => {
    const d = netDiffRows(netDiff().rows);
    expect(d.map((r) => r.diff)).toEqual(STRIKES.map((_, i) => 2000 * (6 - i) - 1000 * (i - 6)));
    expect(d[0]).toMatchObject({ strike: 24200, ceChg: -6000, peChg: 12_000, diff: 18_000, dominance: "Bulls" });
    expect(d[6]).toMatchObject({ strike: 24500, diff: 0, dominance: "Neutral" });
    expect(d[12]).toMatchObject({ strike: 24800, diff: -18_000, dominance: "Bears" });
    expect([dominanceOf(1), dominanceOf(-1), dominanceOf(0)]).toEqual(["Bulls", "Bears", "Neutral"]);
    // the server's own diff / dominance agree with the ones read here
    for (const [i, r] of netDiff().rows.entries()) expect([d[i].diff, d[i].dominance]).toEqual([r.diff, r.dominance]);
  });

  it("the TOTAL row is the column sums (LTP and Dominance blank)", () => {
    const t = netDiffTotal(netDiffRows(netDiff().rows));
    expect(t).toEqual({ ceChg: 0, ceOi: 13 * 100_000 + 10_000 * 78, peOi: 13 * 220_000 - 10_000 * 78, peChg: 0, diff: 0 });
    const one = netDiffTotal(netDiffRows([{ ...netDiff().rows[0] }, { ...netDiff().rows[1] }]));
    expect(one).toEqual({ ceChg: -11_000, ceOi: 210_000, peOi: 430_000, peChg: 22_000, diff: 33_000 });
  });

  it("the window is ATM +- 6 steps, inclusive (Trade: NIFTY 50, BANKNIFTY / SENSEX 100)", () => {
    expect(STRIKES.every((k) => inTradeWindow(k, 24500, 50))).toBe(true);
    expect([inTradeWindow(24150, 24500, 50), inTradeWindow(24850, 24500, 50)]).toEqual([false, false]);
    expect([inTradeWindow(51900, 52500, 100), inTradeWindow(53100, 52500, 100), inTradeWindow(53200, 52500, 100)]).toEqual([true, true, false]);
  });

  it("the table: 13 strikes and a TOTAL row, lakhs by default, coloured by sign, the ATM row marked, Trade's columns", () => {
    const html = renderToStaticMarkup(<NetDiffTableBody rows={netDiffRows(netDiff().rows)} atm={24500} chainRows={chainRowsFor(STRIKES)} />);
    expect(html.match(/data-oi-diff-row="/g)).toHaveLength(13);
    expect(text(html)).toContain("CE Chg OI CE Total OI CE LTP Strike PE LTP PE Total OI PE Chg OI Net Diff Dominance");
    expect(html).toMatch(/data-oi-diff-row="24200"[\s\S]*?text-down[^>]*>−0.06<[\s\S]*?24,200[\s\S]*?text-up[^>]*>\+0.12<[\s\S]*?\+0.18<[\s\S]*?Bulls/);
    // owner 13 Nov: no "L" in any cell (the TOTAL row too); the unit is said once (aria-label here, the heading above)
    expect(text(html)).not.toMatch(/\d L\b/);
    expect(html).toContain('aria-label="OI difference by strike, OI in lakhs"');
    // every cell one line: no fixed layout, nowrap; Strike sticky for the sideways scroll; numbers right-aligned
    expect(html).toMatch(/<table class="[^"]*whitespace-nowrap/);
    expect(html).not.toContain("table-fixed");
    expect(html).toMatch(/<th scope="col" class="[^"]*sticky left-0 right-0[^"]*">Strike<\/th>/);
    expect(html).toMatch(/<th scope="row" class="[^"]*sticky left-0 right-0[^"]*">TOTAL<\/th>/);
    expect(html).not.toMatch(/<td[^>]*text-left[^>]*>[^<]*\d/);
    expect(html).toMatch(/data-oi-diff-row="24500" data-oi-diff-atm="true"/);
    expect(html).toMatch(/data-oi-diff-total[\s\S]*?TOTAL/);
    const contracts = renderToStaticMarkup(<NetDiffTableBody rows={netDiffRows(netDiff().rows)} atm={24500} chainRows={[]} units="contracts" />);
    expect(contracts).toContain("+18,000");
    expect(contracts).toContain('aria-label="OI difference by strike, OI in contracts"');
    expect(text(html)).not.toMatch(/\b(buy|sell|should|recommend\w*|advice|target)\b/i);
    expect(text(html)).not.toMatch(/added more|cut more|buildup|unwinding/i);           // no ATP-invented words or tags
    expect(diffSource).toContain('<details className="text-t12 text-fg-muted" data-oi-diff-help>');
    expect(diffSource).toContain("not the exchange's");
  });

  it("building on the day's first reading; nothing without a window", () => {
    expect(netDiffStatus(netDiff("building"))).toBe("building");
    expect(netDiffStatus(netDiff())).toBe("ready");
    expect(netDiffStatus(undefined)).toBe("none");
    expect(netDiffStatus({ status: "ready", since: null, pcr: null, rows: [] })).toBe("none");
    const live = { ...liveChain(), net_diff: netDiff("building") };
    const html = renderToStaticMarkup(<OiDiffTableV2 live={live} centreKey="k" />);
    expect(text(html)).toContain("Building: the day's first reading was taken at 09:20 IST");
    const none = renderToStaticMarkup(<OiDiffTableV2 live={liveChain()} centreKey="k" />);
    expect(none).toContain('data-oi-diff-state="none"');
  });
});

describe("M96 L1: Trade's banner", () => {
  it("the five PCR zones with Trade's edges, labels in English", () => {
    const cases: [number, string][] = [[0.69, "OVERSOLD"], [0.7, "MILD_BEARISH"], [0.89, "MILD_BEARISH"], [0.9, "SIDEWAYS"], [1.0, "SIDEWAYS"],
      [1.01, "MILD_BULLISH"], [1.3, "MILD_BULLISH"], [1.31, "OVERBOUGHT"]];
    for (const [pcr, zone] of cases) expect(pcrZone(pcr)).toBe(zone);
    expect(pcrZone(null)).toBeNull();
    expect(PCR_ZONE_LABEL.MILD_BULLISH).toBe("Mild bullish trend (not yet overbought)");
    expect(Object.values(PCR_ZONE_LABEL).join(" ")).not.toMatch(/[\u0900-\u097f]|\p{Extended_Pictographic}/u);
  });

  it("days to expiry in calendar days (Trade), 'Expiry today' at 0", () => {
    expect(calendarDte("2026-11-13", "2026-11-11")).toBe(2);
    expect(calendarDte("2026-11-16", "2026-11-13")).toBe(3);                         // Friday to Monday: 3, not 1 trading day
    expect(calendarDte("2026-11-11", "2026-11-11")).toBe(0);
    expect(calendarDte(null, "2026-11-11")).toBeNull();
    expect(expiryLine("2026-11-13", 2)).toBe("Expiry: 13 Nov 2026 (2 days left)");
    expect(expiryLine("2026-11-11", 0)).toBe("Expiry: 11 Nov 2026 (Expiry today)");
    expect(expiryLine("2026-11-12", 1)).toBe("Expiry: 12 Nov 2026 (1 day left)");
    expect(todayIst(Date.parse("2026-11-10T20:00:00Z"))).toBe("2026-11-11");          // 01:30 IST is already the 11th
  });

  it("classes, direction colours and the History signal colours as Trade", () => {
    expect(["Writing", "Buying", "Short covering", "Long unwinding", "Flat/unclear", "Insufficient data"].map(classText))
      .toEqual(["Writing", "Buying", "Short covering", "Long unwinding", "Flat or unclear", "Not enough data"]);
    expect(["BULLISH", "BEARISH", "MIXED", "NEUTRAL"].map(bannerTone)).toEqual(["up", "down", "warn", "neutral"]);
    expect(["BULLISH (Strong)", "BEARISH (Strong)", "BULLISH (Weakening)", "BEARISH (Weakening)", "NEUTRAL"].map(signalTone))
      .toEqual(["up", "down", "down", "up", "muted"]);
  });

  const row = (over: Partial<OiBannerRow> = {}): OiBannerRow => ({
    slot: "2026-11-11T10:25:00+05:30", data_as_of: "2026-11-11T04:56:00+00:00", direction: "BULLISH", message: "",
    fact_message: "Put writing rising → NIFTY OI readings lean up", put_class: "Writing", call_class: "Flat/unclear",
    signal: "BULLISH (Strong)", diff: 1_250_000, delta_diff: -40_000, total_call_oi: 9_000_000, total_put_oi: 10_250_000, pcr: 1.14,
    pcr_band: "MILD_BULLISH", pcr_label: "", underlying_price: 24510, atm_strike: 24500, strike_step: 50, max_pain: 24500,
    expiry: "2026-11-13", dte: 2, first_of_day: false, ...over });
  const banner = (over: Partial<OiBannerRow> = {}, open = true): OiBannerResponse => ({ underlying: "NIFTY", date: "2026-11-11", banner: row(over),
    market_open: open, stale: false, age_minutes: 1, stale_after_minutes: 15 });
  const live = () => ({ ...liveChain(), expiry: "2026-11-13", summary: { ...liveChain().summary, pcr_oi_atm: 1.14 }, net_diff: netDiff() });
  const NOW = Date.parse("2026-11-11T05:00:00Z");

  it("the banner lines: message, classes, PCR ATM +- 6 with its zone, calendar expiry, the reading time", () => {
    const l = bannerLines("NIFTY", banner(), live(), "2026-11-11");
    expect(l).toMatchObject({ tone: "up", message: "Put writing rising → NIFTY OI readings lean up", classes: "Put: Writing · Call: Flat or unclear",
      pcr: "PCR (ATM ± 6): 1.14", pcrZone: "Mild bullish trend (not yet overbought)", pcrTone: "up",
      expiry: "Expiry: 13 Nov 2026 (2 days left)", expiryNear: false, readingAt: "10:26 IST" });
    expect(bannerLines("NIFTY", banner({ direction: "MIXED" }), live(), "2026-11-12")).toMatchObject({ tone: "warn", expiryNear: true });
    // closed market: Trade shows "Market closed", grey, no classes
    expect(bannerLines("NIFTY", banner({}, false), live(), "2026-11-11")).toMatchObject({ tone: "neutral", message: "Market closed",
      classes: "Put: Not enough data · Call: Not enough data" });
    expect(bannerLines("SENSEX", null, null, "2026-11-11")).toMatchObject({ message: "No OI reading of SENSEX yet today", pcr: "PCR (ATM ± 6): not available" });
  });

  it("owner bug 13 Nov: the banner keeps Trade's calendar days (M96 AS IS); ATP's own labels name their unit, trading days", () => {
    const fri = { ...live(), expiry: "2026-11-17", summary: { ...live().summary, days_to_expiry: 2 } };
    expect(bannerLines("NIFTY", banner(), fri, "2026-11-13").expiry).toBe("Expiry: 17 Nov 2026 (4 days left)");   // Trade's sentence, unchanged
    expect(tradingDaysText(fri.summary.days_to_expiry)).toBe("2 trading days");        // the context bar's "Expiry in" and the card
    expect(tradingDaysText(1)).toBe("1 trading day");
    expect(tradingDaysText(0)).toBe("Today");
    expect(tradingDaysText(null)).toBe("–");
  });

  it("the banner and its History table (Trade's columns, lakhs, the stable Signal), no advice words", () => {
    const history: OiHistoryResponse = { underlying: "NIFTY", date: "2026-11-11", interval: 5, market_open: true, stale: false, age_minutes: 1,
      stale_after_minutes: 15, rows: [row(), row({ slot: "2026-11-11T10:20:00+05:30", signal: "BEARISH (Weakening)", diff: -300_000, delta_diff: 0 })] };
    const html = renderToStaticMarkup(<TradeOiBannerView symbol="NIFTY" live={live()} state={{ banner: banner(), history, loaded: true }} interval={5}
      onInterval={noop} now={NOW} />);
    const t = text(html);
    expect(html).toContain('data-banner-tone="up"');
    expect(t).toContain("PCR (ATM ± 6): 1.14 - Mild bullish trend (not yet overbought)");
    expect(t).toContain("Expiry: 13 Nov 2026 (2 days left)");
    expect(html).not.toContain("data-banner-caption");                                   // a later reading: no caption
    const first = text(renderToStaticMarkup(<TradeOiBannerView symbol="NIFTY" live={live()} state={{ banner: banner({ first_of_day: true,
      put_class: "Insufficient data", call_class: "Insufficient data", fact_message: "No clear movement" }), history: null, loaded: true }}
      interval={5} onInterval={noop} now={NOW} />));
    expect(first).toContain("Put: Not enough data · Call: Not enough data");
    expect(first).toContain("The first reading of the day shows &quot;Not enough data&quot;: the reading needs history");
    expect(first).toContain("No OI reading is stored for today yet.");
    expect(t).toContain("Time Total Call OI Total Put OI Diff Δ Diff Signal");
    expect(t).toContain("(OI in lakhs)");                                               // owner 13 Nov: the unit once above
    expect(t).toContain("10:25 90.00 102.50 +12.50 −0.40 BULLISH (Strong)");            // no "L" per cell
    expect(html).toMatch(/class="[^"]*text-up" data-history-signal="true">BULLISH \(Strong\)/);
    expect(html).toMatch(/class="[^"]*text-up" data-history-signal="true">BEARISH \(Weakening\)/);   // weakening: the other colour
    expect(t).not.toMatch(/short call|short put|don't|cautious|\bbuy\b|\bsell\b/i);
    expect(bannerSource).not.toMatch(/[\u0900-\u097f]|\p{Extended_Pictographic}/u);
  });

  it("owner 13 Nov: the banner shows Trade's stable Signal of the latest reading, with Trade's five labels and colours", () => {
    const cases: [string, "up" | "down" | "muted"][] = [["BULLISH (Strong)", "up"], ["BULLISH (Weakening)", "down"], ["BEARISH (Strong)", "down"],
      ["BEARISH (Weakening)", "up"], ["NEUTRAL", "muted"]];
    for (const [signal, tone] of cases) {
      expect(bannerLines("NIFTY", banner({ signal }), live(), "2026-11-11")).toMatchObject({ signal, signalTone: tone });
      const html = renderToStaticMarkup(<TradeOiBannerView symbol="NIFTY" live={live()} state={{ banner: banner({ signal }), history: null, loaded: true }}
        interval={5} onInterval={noop} now={NOW} />);
      const cls = { up: "text-up", down: "text-down", muted: "text-fg-muted" }[tone];
      expect(html).toContain(`<span class="${cls}" data-banner-signal-label="true">${signal}</span>`);
      expect(text(html)).toContain(`Signal: ${signal}`);
    }
    // before the day's first reading: no Signal line (never a made-up one)
    expect(bannerLines("NIFTY", null, live(), "2026-11-11").signal).toBeNull();
    expect(renderToStaticMarkup(<TradeOiBannerView symbol="NIFTY" live={live()} state={{ banner: null, history: null, loaded: true }}
      interval={5} onInterval={noop} now={NOW} />)).not.toContain("data-banner-signal");
    // no ATP-invented words for the signal (the per-strike Dominance words stay in the Net Diff table only)
    expect(bannerSource).not.toMatch(/"Bulls"|"Bears"|Bullish weakening|Bearish weakening/);
  });

  it("owner 13 Nov: Trade's View interval 5 / 10 / 15 minutes in sight above the History, 5 first; a choice opens the table", () => {
    expect([...INTERVALS]).toEqual([5, 10, 15]);
    const html = renderToStaticMarkup(<TradeOiBannerView symbol="NIFTY" live={live()} state={{ banner: banner(), history: null, loaded: true }}
      interval={5} onInterval={noop} now={NOW} />);
    const pickerAt = html.indexOf("data-oi-interval=");
    expect(pickerAt).toBeGreaterThan(-1);
    expect(pickerAt).toBeLessThan(html.indexOf("<details"));                      // outside the collapsed History
    expect(text(html)).toContain("View interval 5 min 10 min 15 min");
    expect(html).toMatch(/aria-checked="true"[^>]*data-oi-interval-option="5"/);
    expect(html).toMatch(/aria-checked="false"[^>]*data-oi-interval-option="10"/);
    expect(html).not.toMatch(/<details[^>]*open=""/);                             // collapsed until asked, as Trade's expander
    const picked: number[] = [];
    const tree = IntervalPicker({ id: "i", interval: 10, onPick: (n) => picked.push(n) });
    const buttons = elements(tree).filter((e) => e.type === "button");
    expect(buttons.map((b) => (b.props as Record<string, unknown>)["aria-checked"])).toEqual([false, true, false]);
    for (const b of buttons) {
      expect(String((b.props as { className: string }).className)).toContain("min-h-11");          // 44 px on a phone
      (b.props as { onClick: () => void }).onClick();
    }
    expect(picked).toEqual([5, 10, 15]);
    // the view passes the choice to the History read (the server buckets Trade's way) and opens the table
    expect(bannerSource).toMatch(/onPick=\{\(n\) => \{ onInterval\(n\); setOpen\(true\); \}\}/);
    expect(bannerSource).toMatch(/api\.oiHistory\(underlying, interval\)/);
    expect(bannerSource).toMatch(/useState<Interval>\(5\)/);
    // a 10-minute row: Trade's History "Time" is the bucket's start (10:20), not its last reading's slot (10:25)
    const ten: OiHistoryResponse = { underlying: "NIFTY", date: "2026-11-11", interval: 10, market_open: true, stale: false, age_minutes: 1,
      stale_after_minutes: 15, rows: [{ ...row(), bucket: "2026-11-11T10:20:00+05:30" }] };
    const tenHtml = text(renderToStaticMarkup(<TradeOiBannerView symbol="NIFTY" live={live()} state={{ banner: banner(), history: ten, loaded: true }}
      interval={10} onInterval={noop} now={NOW} />));
    expect(tenHtml).toContain("10:20 90.00 ");
    expect(tenHtml).not.toContain("10:25 90.00 ");
  });

  it("owner 13 Nov: the Net Diff table keeps Trade's own column words (Dominance: Bulls / Bears / Neutral, TOTAL)", () => {
    const html = renderToStaticMarkup(<OiDiffTableV2 live={live()} centreKey="k" />);
    const t = text(html);
    for (const h of ["CE Chg OI", "CE Total OI", "CE LTP", "Strike", "PE LTP", "PE Total OI", "PE Chg OI", "Net Diff", "Dominance"]) expect(t).toContain(h);
    expect(t).toContain("TOTAL");
    // owner 13 Nov: the unit once in the heading, by the Lakhs / Contracts choice (M96 Q3)
    expect(html).toMatch(/\(ATM ± 6 · <span data-oi-unit="true">OI in lakhs<\/span>\)/);
    const inContracts = renderToStaticMarkup(<OiDiffTableV2 live={live()} units="contracts" centreKey="k" />);
    expect(inContracts).toContain('<span data-oi-unit="true">OI in contracts</span>');
    expect(text(inContracts)).not.toMatch(/\d L\b/);
  });
});

describe("M96 O1: a tap in the OI-difference table opens that contract's ticket - contract only", () => {
  it("a CE or PE change calls the chain's own leg handler with that row and side (off: plain cells)", () => {
    const pick = vi.fn();
    const r = rows()[3];
    const d = netDiffRows(netDiff().rows).find((x) => x.strike === 24500)!;
    const row: ChainRow = { ...r, strike: 24500 };
    for (const side of ["call", "put"] as const) {
      const btn = elements(ChgCell({ side, d, row, onPickLeg: pick })).find((e) => e.type === "button")!;
      expect((btn.props as Record<string, unknown>)["data-oi-diff-cell"]).toBe(`24500|${side === "call" ? "CE" : "PE"}`);
      expect(String((btn.props as { className: string }).className)).toContain("min-h-11");          // 44 px target
      (btn.props as { onClick: () => void }).onClick();
    }
    expect(pick.mock.calls).toEqual([[row, "call"], [row, "put"]]);
    expect(elements(ChgCell({ side: "call", d, row })).some((e) => e.type === "button")).toBe(false);
    expect(elements(ChgCell({ side: "call", d, row: undefined, onPickLeg: pick })).some((e) => e.type === "button")).toBe(false);
  });

  it("the page wires the table to the SAME path as the chain table (action sheet -> ticket / Terminal order panel)", () => {
    expect(pageSource).toMatch(/<OiDiffTableV2 [^>]*onPickLeg=\{trading\.onPickLeg\}/);
    expect(pageSource).toMatch(/<ChainLadderV2 [\s\S]*?onPickLeg=\{trading\.onPickLeg\}/);
    expect(pageSource).toContain("const trading = useChainTrading({ symbol, live, prefs, onPrefs, expiries: expiryOptions, signal, onTicket });");
    expect(pageSource).toContain("<TradeOiBannerV2 symbol={symbol} live={live} />");
    expect(terminalSource).toContain("onTicket={orderPanelShown ? (t) => setTicket(t) : undefined}");
  });

  it("the prefill is the contract and its quotes only; the ticket starts with no lots, order type or product (M68)", () => {
    const r = rows()[3];
    const t = ticketTargetOf({ underlying: "NIFTY", expiry: "2026-11-03", strike: r.strike, right: "PE", leg: r.put, spotSymbol: "NIFTY 50" },
      { underlying: "NIFTY", tradingsymbol: "X", exchange: "NFO", instrument_key: "k", expiry: "2026-11-03", strike: r.strike, right: "PE",
        lot_size: 75, tick_size: 0.05, label: "NIFTY 03 NOV 24500 PE", freeze_quantity: null });
    expect(Object.keys(t).sort()).toEqual(["ask", "bid", "expiry", "label", "lotSize", "ltp", "right", "strike", "underlying"]);
    const draft = newDraft("BUY");
    expect([draft.lots, draft.orderType, draft.product]).toEqual([null, null, null]);
    expect(firstMissing(draft)).toBe("lots");
    const html = renderToStaticMarkup(<TicketBody target={t} draft={draft} step={{ kind: "edit" }} busy={false} problem={null} idPrefix="t" saveDefaults
      onDraft={noop} onReview={noop} onPlace={noop} onBack={noop} onClose={noop} />);
    expect(html).toMatch(/value=""[^>]*data-ticket="lots"|data-ticket="lots"[^>]*value=""/);
    expect(html).toContain(LOTS_UNSET_TEXT);
    expect(html).toMatch(/disabled=""[^>]*data-ticket="review"|data-ticket="review"[^>]*disabled=""/);   // he presses Place himself
  });
});

describe("M96 Q3: OI, OI change and volume in lakhs (two decimals), with a Contracts choice", () => {
  it("formatLakhs: always lakhs, two decimals, Indian grouping, signed for a change", () => {
    expect(formatLakhs(50_000)).toBe("0.50 L");
    expect(formatLakhs(400_000)).toBe("4.00 L");
    expect(formatLakhs(1_234_567)).toBe("12.35 L");
    expect(formatLakhs(25_000_000)).toBe("250.00 L");
    expect(formatLakhs(1_234_500_000)).toBe("12,345.00 L");
    expect(formatLakhs(-4_500)).toBe("−0.05 L");
    expect(formatLakhs(4_500, { signed: true })).toBe("+0.05 L");
    expect(formatLakhs(-4_500, { signed: true })).toBe("−0.05 L");
    expect(formatLakhs(400, { signed: true })).toBe("0.00 L");
    expect(formatLakhs(0)).toBe("0.00 L");
    expect(formatLakhs(-4_500, { signed: true, unit: false })).toBe("−0.05");        // owner 13 Nov: the bare cell figure
    expect(formatLakhs(0, { unit: false })).toBe("0.00");
    expect(formatLakhs(null)).toBe("–");
    expect(formatLakhs(Number.NaN)).toBe("–");
    expect(formatContracts(50_000)).toBe("50,000");
    expect(formatContracts(15_000, { signed: true })).toBe("+15,000");
    expect(formatContracts(-4_500, { signed: true })).toBe("−4,500");
    expect(formatContracts(0, { signed: true })).toBe("0");
    expect(formatOi(50_000, undefined)).toBe("0.50 L");
    expect(formatOi(50_000, "contracts")).toBe("50,000");
    expect(formatOi(50_000, undefined, true, false)).toBe("+0.50");
    expect([oiUnitCaption(undefined), oiUnitCaption("lakhs"), oiUnitCaption("contracts")]).toEqual(["OI in lakhs", "OI in lakhs", "OI in contracts"]);
    expect(formatCount(50_000)).toBe("50,000");                         // other callers keep formatCount as it was
  });

  it("the chain table shows OI, OI change and volume in the chosen unit", () => {
    const html = renderToStaticMarkup(<ChainTableV2 live={liveChain()} prefs={DEFAULT_COLUMNS} centreKey="k" onPick={noop} />);
    expect(html).toMatch(/data-col="call-oi"[\s\S]*?>1.00</);
    expect(html).toContain(">+0.15<");
    expect(html).toContain(">0.20<");                                   // volume 20,000
    expect(html).toContain("(OI and volume in lakhs)");                 // owner 13 Nov: the unit once, above the table
    expect(text(html)).not.toMatch(/\d L\b/);
    const contracts = renderToStaticMarkup(<ChainTableV2 live={liveChain()} prefs={{ ...DEFAULT_COLUMNS, units: "contracts" }} centreKey="k" onPick={noop} />);
    expect(contracts).toContain("1,00,000");
    expect(contracts).toContain("+15,000");
    expect(contracts).toContain("(OI and volume in contracts)");
  });

  it("the unit is kept in the columns prefs (lakhs unless Contracts was picked)", () => {
    const store = new Map<string, string>();
    vi.stubGlobal("localStorage", { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => store.set(k, v) });
    try {
      expect(readColumns().units).toBe("lakhs");
      store.set(COLUMNS_KEY, JSON.stringify({ units: "contracts" }));
      expect(readColumns().units).toBe("contracts");
      store.set(COLUMNS_KEY, JSON.stringify({ units: "shares" }));
      expect(readColumns().units).toBe("lakhs");
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
