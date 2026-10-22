import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/errors";
import { AuthProvider } from "../auth/AuthContext";
import OptionChainPage, { BROKER_PAGE_PATH, chainProblem } from "../pages/OptionChainPage";
import flagOffSource from "../pages/OptionChainPage.tsx?raw";
import { PAGE_COMPONENTS_V2 } from "../shell/pagesV2";
import { isDataPath } from "../ui/dataPages";
import { EXPIRIES, liveChain, rows } from "./chain.fixture";
import ChainTableV2 from "./ChainTableV2";
import tableSource from "./ChainTableV2.tsx?raw";
import columnsSource from "./ColumnsMenuV2.tsx?raw";
import modelSource from "./model.ts?raw";
import {
  CALL_COLUMNS, DEFAULT_COLUMNS, POLL_CLOSED_MS, POLL_OPEN_MS, TEXT, atmIndex, barPercent, centreScrollTop, chainNotes, chooseExpiry,
  columnShown, daysText, formatCount, formatCountChange, formatStrike, initialSymbol, isItm, pollEvery, type ColumnPrefs,
} from "./model";
import { OptionChainView } from "./OptionChainPageV2";
import pageSource from "./OptionChainPageV2.tsx?raw";
import { RowDetailsBody } from "./RowDetailsV2";
import detailsSource from "./RowDetailsV2.tsx?raw";
import SummaryStripV2 from "./SummaryStripV2";
import stripSource from "./SummaryStripV2.tsx?raw";
import type { LiveChain } from "./types";
import { loadExpiryChoice, problemOf, startChainPolling, type OptionChainData } from "./useOptionChain";
import hookSource from "./useOptionChain.ts?raw";

/**
 * OC1: the automatic Option chain page (v2) - the automatic symbol and expiry (with the expiry-day rule), the ATM row
 * in the middle, the summary, each plain-word state, the phone layout, and no sample data or developer controls on
 * this page or on today's (flag-off) Option chain page. The data is hand-made (chain.fixture.ts).
 */
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const noop = () => undefined;
const flush = async () => { for (let i = 0; i < 6; i += 1) await Promise.resolve(); };

function data(over: Partial<OptionChainData> = {}): OptionChainData {
  return { expiries: EXPIRIES, expiry: "2026-11-03", setExpiry: noop, live: liveChain(), loading: false, problem: null, ...over };
}

function view(over: Partial<OptionChainData> = {}, opts: { marketOpen?: boolean | null; prefs?: ColumnPrefs; symbol?: string } = {}) {
  return renderToStaticMarkup(
    <MemoryRouter>
      <AuthProvider>
        <OptionChainView symbol={opts.symbol ?? "NIFTY"} onSymbol={noop} chain={data(over)} marketOpen={opts.marketOpen ?? true}
          prefs={opts.prefs ?? DEFAULT_COLUMNS} onPrefs={noop} />
      </AuthProvider>
    </MemoryRouter>,
  );
}

const table = (live: LiveChain = liveChain(), prefs: ColumnPrefs = DEFAULT_COLUMNS) =>
  renderToStaticMarkup(<ChainTableV2 live={live} prefs={prefs} centreKey="NIFTY|2026-11-03" onPick={noop} />);

/** The cells of one row, in order, as markup. */
function rowCells(html: string, strike: number): string[] {
  const row = new RegExp(`<tr[^>]*data-strike="${strike}"[^>]*>([\\s\\S]*?)</tr>`).exec(html);
  expect(row, `row ${strike}`).not.toBeNull();
  return row![1].match(/<td[\s\S]*?<\/td>/g) ?? [];
}

describe("OC1 automatic symbol and expiry", () => {
  it("opens on NIFTY, or the symbol a deep link names", () => {
    expect(initialSymbol("")).toBe("NIFTY");
    expect(initialSymbol("?underlying=banknifty")).toBe("BANKNIFTY");
    expect(initialSymbol("?underlying=M%26M")).toBe("M&M");
    expect(initialSymbol("?underlying=%3Cscript%3E")).toBe("NIFTY");
  });

  it("selects the server's default: the nearest expiry, and on an expiry day the next one", async () => {
    // 27 Oct is an expiry day (EXPIRIES.today): the list still holds it, the default is 3 Nov.
    const read = vi.fn(async () => EXPIRIES);
    expect(await loadExpiryChoice("NIFTY", read)).toEqual({ list: EXPIRIES, expiry: "2026-11-03" });
    expect(read).toHaveBeenCalledWith("NIFTY");
    expect(await loadExpiryChoice("NIFTY", async () => ({ ...EXPIRIES, today: "2026-10-21", default: "2026-10-27" })))
      .toMatchObject({ expiry: "2026-10-27" });
    // A failed list selects nothing: the chain is still read, for the broker's nearest expiry.
    expect(await loadExpiryChoice("NIFTY", async () => { throw new Error("x"); })).toEqual({ list: null, expiry: null });
  });

  it("keeps the expiry on screen while it is listed; else the default; else the first", () => {
    expect(chooseExpiry(EXPIRIES, "2026-11-10")).toBe("2026-11-10");
    expect(chooseExpiry(EXPIRIES, "2026-12-29")).toBe("2026-11-03");
    expect(chooseExpiry({ ...EXPIRIES, default: null }, null)).toBe("2026-10-27");
    expect(chooseExpiry({ ...EXPIRIES, expiries: [] }, null)).toBeNull();
    expect(chooseExpiry(null, "2026-11-03")).toBeNull();
  });

  it("reads the list and then the chain on its own, with no button to press", () => {
    expect(hookSource).toContain("loadExpiryChoice(symbol, api.optionChainExpiries, carried)");      // FNO-3: the hub carries the expiry
    expect(hookSource).toContain("api.optionChainLive(symbol, expiry)");
    expect(hookSource).toContain("usePageVisible()");
    // A passed expiry (the page left open overnight): the server's 422 reads the list again for today's default.
    expect(hookSource).toContain("e.status === 422 && expiry && retriedFor.current !== `${symbol}|${expiry}`");
    expect(hookSource).toContain("}, [symbol, listRound]);");
    expect(pageSource).not.toMatch(/Analy[sz]e|Refresh|Load chain|Show chain/);
    const html = view();
    expect(html).toMatch(/<option value="NIFTY" selected="">NIFTY<\/option>/);
    expect(html).toMatch(/<option value="2026-11-03" selected="">3 Nov 2026<\/option>/);
    expect(text(html)).toContain("27 Oct 2026 · Monthly");
    // The order of the symbols: NIFTY, BANKNIFTY, FINNIFTY, SENSEX, then the F&O stocks.
    const order = [...html.matchAll(/<option value="([^"]+)"/g)].map((m) => m[1]);
    expect(order.slice(0, 4)).toEqual(["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]);
    expect(order).toContain("RELIANCE");
  });

  it("a deep-linked symbol outside the list is still selected", () => {
    expect(view({}, { symbol: "NEWLISTED" })).toMatch(/<option value="NEWLISTED" selected="">/);
  });

  describe("live updates", () => {
    beforeEach(() => { vi.useFakeTimers(); });
    afterEach(() => { vi.useRealTimers(); });

    it("reads at once, then every 15 s in market hours (5 min when closed), and stops", async () => {
      const seen: string[] = [];
      let n = 0;
      let open = true;
      const stop = startChainPolling({ read: async () => liveChain({ fetched_at: String(++n) }), firstDelay: 0, delay: () => pollEvery(open),
        onData: (c) => seen.push(c.fetched_at), onError: noop });
      await vi.advanceTimersByTimeAsync(0);
      await flush();
      expect(seen).toEqual(["1"]);
      await vi.advanceTimersByTimeAsync(POLL_OPEN_MS);
      expect(seen).toEqual(["1", "2"]);
      open = false;
      await vi.advanceTimersByTimeAsync(POLL_OPEN_MS);
      expect(seen).toEqual(["1", "2", "3"]);
      await vi.advanceTimersByTimeAsync(POLL_OPEN_MS * 2);
      expect(seen).toHaveLength(3);
      await vi.advanceTimersByTimeAsync(POLL_CLOSED_MS);
      expect(seen).toHaveLength(4);
      stop();
      await vi.advanceTimersByTimeAsync(POLL_CLOSED_MS * 3);
      expect(seen).toHaveLength(4);
    });

    it("a hidden tab reads nothing (the effect waits for the page to be shown)", () => {
      expect(hookSource).toMatch(/if \(!listReady \|\| !visible\) return undefined;/);
    });
  });
});

describe("OC1 ATM row in the middle", () => {
  it("the scroll position puts the ATM row's middle in the middle of the table", () => {
    expect(centreScrollTop(600, 40, 400)).toBe(420);       // 600 + 20 - 200
    expect(centreScrollTop(100, 40, 400)).toBe(0);         // near the top: never above it
    expect(atmIndex(rows(), 24500)).toBe(3);
    expect(atmIndex(rows(), null)).toBe(-1);
    expect(tableSource).toContain("box.current.scrollTop = centreScrollTop(");
    expect(tableSource).toContain("centred.current === centreKey");        // once per symbol and expiry, not each refresh
  });

  it("the ATM row is highlighted; in-the-money cells are shaded (calls below the spot, puts above)", () => {
    const html = table();
    expect(html).toMatch(/<tr[^>]*data-strike="24500"[^>]*data-atm="true"[^>]*class="[^"]*bg-brand\/10/);
    expect(html.match(/data-atm="true"/g)).toHaveLength(1);
    expect(text(html)).toContain("24,500 ATM");
    for (const k of [24200, 24300, 24400]) {
      const cells = rowCells(html, k);
      expect(cells.slice(0, 5).every((c) => c.includes("data-itm"))).toBe(true);   // the call side
      expect(cells.slice(5).some((c) => c.includes("data-itm"))).toBe(false);       // the put side
    }
    for (const k of [24600, 24700, 24800]) {
      const cells = rowCells(html, k);
      expect(cells.slice(0, 5).some((c) => c.includes("data-itm"))).toBe(false);
      expect(cells.slice(5).every((c) => c.includes("data-itm"))).toBe(true);
    }
    expect(rowCells(html, 24500).some((c) => c.includes("data-itm"))).toBe(false);
    expect(isItm("call", 24400, 24480)).toBe(true);
    expect(isItm("put", 24400, 24480)).toBe(false);
    expect(isItm("call", 24400, null)).toBe(false);
  });

  it("OI change is neutral text with its sign only (up / down colours are for prices and P&L)", () => {
    const html = table();
    for (const k of [24200, 24800]) {
      const change = rowCells(html, k)[1];                                    // the call side's OI change
      expect(change).toMatch(/[+−]\d/);
      expect(change).not.toMatch(/text-up|text-down/);
    }
    expect(tableSource).not.toMatch(/text-up|text-down/);
  });

  it("OI bars in the cells; the highest call OI and put OI strikes are marked", () => {
    const html = table();
    expect(rowCells(html, 24800)[0]).toContain('data-max-oi="call"');
    const low = rowCells(html, 24200);
    expect(low[low.length - 1]).toContain('data-max-oi="put"');
    expect(html.match(/data-max-oi=/g)).toHaveLength(2);
    expect(rowCells(html, 24800)[0]).toContain("width:100%");
    expect(rowCells(html, 24200)[0]).toContain("width:14.3%");                  // 1 of 7 lakh
    expect(barPercent(350_000, 700_000)).toBe(50);
    expect(barPercent(null, 700_000)).toBe(0);
    expect(barPercent(5, 0)).toBe(0);
  });
});

describe("OC1 summary strip", () => {
  it("spot, futures, ATM strike, PCR (OI), max pain, ATM IV and days to expiry, with the glossary hints", () => {
    const html = renderToStaticMarkup(<SummaryStripV2 summary={liveChain().summary} expiry="2026-11-03" loading={false} />);
    const words = text(html);
    for (const part of ["Spot 24,480.00", "Futures 24,530.50 Expires 24 Nov", "ATM strike 24,500", "PCR (OI) 0.88",
      "Max pain 24,500", "ATM IV 12.5%", "Days to expiry 7 days 3 Nov 2026"]) expect(words).toContain(part);
    for (const term of ["PCR", "Max pain", "IV", "ATM", "Expiry"]) expect(html).toContain(`aria-label="What is ${term}?"`);
  });

  it("a value the broker did not give is a dash, never 0", () => {
    const s = { ...liveChain().summary, spot: null, futures: null, pcr_oi: null, atm_iv: null, days_to_expiry: null };
    const words = text(renderToStaticMarkup(<SummaryStripV2 summary={s} expiry={null} loading={false} />));
    expect(words).toContain("Spot –");
    expect(words).toContain("Futures –");
    expect(words).toContain("PCR (OI) –");
    expect(words).toContain("ATM IV –");
    expect(daysText(0)).toBe("Today");
    expect(daysText(1)).toBe("1 day");
  });

  it("numbers in the Indian way", () => {
    expect(formatCount(85_000)).toBe("85,000");
    expect(formatCount(1_235_000)).toBe("12.35 L");
    expect(formatCount(12_400_000)).toBe("1.24 Cr");
    expect(formatCountChange(-4_500)).toBe("−4,500");
    expect(formatCountChange(120_000)).toBe("+1.2 L");
    expect(formatStrike(22.5)).toBe("22.50");
    expect(formatStrike(24500)).toBe("24,500");
  });
});

describe("OC1 plain-word states", () => {
  it("OI history still building", () => {
    const html = view({ live: liveChain({ oi_change: { status: "building", source: "first_reading", since: "2026-10-27T03:50:00+00:00" } }) });
    expect(text(html)).toContain("OI change will show after the first few minutes");
  });

  it("market closed: the last session's chain, and no 'building' note", () => {
    const building = liveChain({ market_open: false, oi_change: { status: "building", source: "first_reading", since: null } });
    const words = text(view({ live: building }, { marketOpen: false }));
    expect(words).toContain("Market closed — showing the last session's chain");
    expect(words).not.toContain(TEXT.building);
    // The chain's own answer (its exchange's session) wins over the system status, which serves until it answers.
    expect(chainNotes(liveChain({ market_open: false }), null, String).map((n) => n.key)).toEqual(["closed"]);
    expect(chainNotes(liveChain({ market_open: false }), true, String).map((n) => n.key)).toEqual(["closed"]);
    expect(chainNotes(liveChain({ market_open: true }), false, String)).toEqual([]);
    expect(chainNotes(null, false, String).map((n) => n.key)).toEqual(["closed"]);
    expect(hookSource).toContain("openRef.current = live?.market_open ?? marketOpen;");
  });

  it("OI change counted from the first reading says since when", () => {
    const live = liveChain({ oi_change: { status: "ready", source: "first_reading", since: "2026-10-27T04:12:00+00:00" } });
    expect(text(view({ live }))).toContain("OI change since 09:42 IST");
  });

  it("no broker session, a failed read, an empty chain, and loading", () => {
    expect(text(view({ live: null, problem: "no-session" }))).toContain(TEXT.noSession);
    expect(text(view({ live: null, problem: "failed" }))).toContain("We could not load the option chain. We will try again in a moment.");
    expect(text(view({ live: liveChain({ rows: [] }) }))).toContain("Your broker sent no strikes for this expiry.");
    const loading = view({ live: null, loading: true });
    expect(loading).toContain('aria-busy="true"');
    expect(text(loading)).not.toMatch(/\d{2},\d{3}/);                       // no figure before the broker's answer
    expect(problemOf(new ApiError(409, "x", "x", "x"))).toBe("no-session");
    expect(problemOf(new ApiError(409, "x", "x", "x", null, "C"))).toBe("plan");               // BYOB plan: X-Data-Tier
    expect(problemOf(new Error("x"))).toBe("failed");
    expect(text(view({ live: null, problem: "plan" }))).toContain("This symbol is not available live on your plan.");
  });

  it("the broker gate stands in front of the page (no broker: only 'Connect your broker')", () => {
    expect(isDataPath("/option-chain")).toBe(true);
    expect(PAGE_COMPONENTS_V2["option-chain"]).toBeDefined();
  });

  it("an Analysis mount point below the chain, for OC2", () => {
    const html = view();
    expect(html).toContain('data-analysis-mount="option-chain"');
    expect(html.indexOf('data-block="chain"')).toBeLessThan(html.indexOf("data-analysis-mount"));
    expect(html).toMatch(/data-analysis-mount="option-chain" data-underlying="NIFTY" data-expiry="2026-11-03" data-refresh-key="2026-10-27T05:00:00\+00:00"/);
    expect(pageSource).toContain("<AnalysisSlotV2 symbol={symbol}");
  });

  it("OC4: below the chain, the Analysis tabs, then Ask AI, then My analyses", () => {
    const html = view();
    const at = (label: string) => html.indexOf(`aria-label="${label}"`);
    expect(at("Option chain analyses")).toBeGreaterThan(html.indexOf('data-block="chain"'));
    expect(at("Ask AI about this option chain")).toBeGreaterThan(at("Option chain analyses"));
    expect(at("My analyses")).toBeGreaterThan(at("Ask AI about this option chain"));
    expect(text(html)).not.toMatch(/sample|lookback|manual ltp/i);
  });
});

describe("OC1 no sample data and no developer controls", () => {
  const DEV = /sample|lookback|cached|cache |Underlying LTP|OI tilt|Broker candles|Insufficient data|Stale\b|backend|endpoint/i;

  it("no page file of the v2 chain imports the sample engine or the data-source switch", () => {
    for (const [name, src] of Object.entries({ pageSource, tableSource, stripSource, modelSource, hookSource, detailsSource, columnsSource })) {
      expect(src, name).not.toMatch(/sampleData|generateSample|DataSource|useCandleSource/);
    }
  });

  it("today's (flag-off) page has no sample chain, no switch, no lookback, no typed price and no tilt", () => {
    expect(flagOffSource).not.toMatch(/sampleData|generateSampleOptionChain|DataSourceBar|useCandleSource|Underlying LTP|tilt|lookback/i);
    expect(flagOffSource).toContain("api.marketDataOptionChains([name])");
    vi.stubGlobal("window", { location: { search: "" } });                   // the page reads ?underlying= on open
    let words = "";
    try {
      words = text(renderToStaticMarkup(<MemoryRouter><AuthProvider><OptionChainPage /></AuthProvider></MemoryRouter>));
    } finally {
      vi.unstubAllGlobals();
    }
    expect(words).toContain("Show chain");
    expect(words).not.toMatch(DEV);
    // A 409: plain words and the Broker page; a plan refusal says so; the error is muted text, never red.
    expect(chainProblem(new ApiError(409, "x"))).toEqual({ text: "Connect your broker on the", connect: true });
    expect(chainProblem(new ApiError(409, "x", "", "", null, "C")).text).toBe("This symbol is not available live on your plan.");
    expect(chainProblem(new Error("boom")).text).toBe("We could not load the option chain. Try again in a moment.");
    expect(BROKER_PAGE_PATH).toBe("/broker");
    expect(flagOffSource).toContain('<p className="text-sm text-fg-muted" role="status">');
    expect(flagOffSource).not.toContain("text-down\">{error");
  });

  it("the v2 page's words: no developer text, no advice and no buy / sell words", () => {
    for (const html of [view(), view({}, { prefs: { greeks: true, quotes: true, more: true } }), view({ live: null, problem: "failed" }),
      renderToStaticMarkup(<RowDetailsBody row={rows()[3]} />)]) {
      const words = text(html);
      expect(words).not.toMatch(DEV);
      expect(words).not.toMatch(/\b(buy|sell|buyers?|sellers?|should|recommend\w*|advice|target)\b/i);
    }
  });
});

describe("OC1 phone layout", () => {
  it("a phone shows OI and LTP on each side by default; the desktop adds OI change, IV and volume", () => {
    const phone = CALL_COLUMNS.filter((c) => columnShown(c, DEFAULT_COLUMNS) === "all").map((c) => c.key);
    expect(phone).toEqual(["oi", "ltp"]);
    const desktop = CALL_COLUMNS.filter((c) => columnShown(c, DEFAULT_COLUMNS) !== "none").map((c) => c.key);
    expect(desktop).toEqual(["oi", "oi_change", "volume", "iv", "ltp"]);
    const html = table();
    for (const label of ["OI change", "Volume", "IV"]) {
      expect(html).toMatch(new RegExp(`<th[^>]*class="[^"]*hidden md:table-cell[^"]*"[^>]*><span class="sr-only">Call </span>${label}</th>`));
    }
    expect(html).not.toMatch(/Delta|Bid/);                                   // Greeks and bid / ask are off by default
    const all = table(liveChain(), { greeks: true, quotes: true, more: true });
    expect(all).toContain("Delta");
    expect(all).toContain("Bid");
    expect(all).not.toContain("hidden md:table-cell");
  });

  it("the strike stays in the centre (sticky) and every row opens its details", () => {
    const html = table();
    // Sticky in the centre once the table is wider than its box (measured in the browser; e2e checks it at 360 px).
    expect(tableSource).toContain('wide && "sticky left-[calc(50%-2.75rem)] right-[calc(50%-2.75rem)] z-[1]"');
    expect(tableSource).toContain("setWide(el.scrollWidth > el.clientWidth + 1)");
    expect(html).toMatch(/<tr[^>]*data-strike="24500"[^>]*tabindex="0"/);
    expect(html).toMatch(/<th scope="row"[^>]*>24,500<span[^>]*>ATM<span class="sr-only">, at the money<\/span><\/span><\/th>/);
    expect(tableSource).toContain("onClick={() => onPick(row)}");
    expect(tableSource).toContain('if (e.key === "Enter" || e.key === " ")');
    const details = text(renderToStaticMarkup(<RowDetailsBody row={rows()[3]} />));
    for (const part of ["Call Put", "LTP 250.00 200.00", "OI 4 L 4 L", "IV 12.3% 12.7%", "Delta 0.50 −0.50", "Bid 249.50 199.50"]) {
      expect(details).toContain(part);
    }
  });

  it("UX3: the summary is two rows of two on a phone (the rest under More), never sideways; one row from md", () => {
    const html = renderToStaticMarkup(<SummaryStripV2 summary={liveChain().summary} expiry="2026-11-03" loading={false} />);
    expect(html).toMatch(/aria-label="Summary"[^>]*class="grid grid-cols-2[^"]*md:grid-cols-4[^"]*xl:grid-cols-7"/);
    expect(html).not.toMatch(/overflow-x-auto|snap-x/);
    const order = [...html.matchAll(/data-summary="([a-z-]+)"/g)].map((m) => m[1]);
    expect(order.slice(0, 4)).toEqual(["spot", "atm", "pcr", "max-pain"]);
    for (const key of ["futures", "atm-iv", "dte"]) expect(html).toMatch(new RegExp(`data-summary="${key}"[^>]*class="[^"]*hidden md:block`));
    expect(html).toMatch(/data-summary="futures"[^>]*md:order-2/);                       // the server's order from md
    expect(html).toContain("More: futures, IV, days to expiry");
    expect(stripSource).toContain("<TermHint term={c.term} floating");      // a hint inside the strip is not cut off
  });
});
