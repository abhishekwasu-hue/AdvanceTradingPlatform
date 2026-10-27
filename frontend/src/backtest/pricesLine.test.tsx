import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { heldRange } from "../admin/AdminSectionsV2";
import { NEEDS_BROKER_FOR_DATA, PlainDataSourceBar, type CandleSourceState } from "../components/DataSource";
import { OWN_BROKER_STATES } from "../shell/pagesV2";
import { shellGates } from "../shell/shellRoutes";
import { STORED_HISTORY_PATHS } from "../ui/dataPages";
import { dayWords, pricesLine, STORED_HISTORY_CHOICES, STORED_HISTORY_NOTE, storedHistoryLoginNote } from "./pricesLine";

// The bar's login action reads the broker session (AuthProvider); here it is a plain stand-in.
vi.mock("../ui/BrokerGate", () => ({ StoredLoginAction: () => <a href="/broker" data-stored-login="stub">Open Broker</a> }));

const noop = () => undefined;
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/\s+/g, " ");
const bar = (timestamp: string) => ({ timestamp, open: 1, high: 2, low: 0.5, close: 1.5, volume: 1 });

describe("owner order 23 Oct: where a backtest's prices came from", () => {
  it("names the broker and the range in India dates", () => {
    const candles = [bar("2026-01-01T18:45:00Z"), bar("2026-10-22T00:00:00+05:30")];
    expect(pricesLine("broker:upstox_analytics", candles)).toBe("Prices from your broker (Upstox Analytics), 2 Jan 2026 to 22 Oct 2026.");
    expect(pricesLine("broker:auto", candles)).toBe("Prices from your broker, 2 Jan 2026 to 22 Oct 2026.");
    expect(dayWords("2026-10-22T00:00:00+05:30")).toBe("22 Oct 2026");
  });

  it("uses the server's own line for stored history and says nothing for sample data", () => {
    const line = "Prices from ATP's stored end-of-day history (NSE's daily files), 2 Jan 2025 to 22 Oct 2026.";
    expect(pricesLine("lake:daily", [bar("2025-01-02T00:00:00+05:30")], line)).toBe(line);
    expect(pricesLine("sample", [bar("2025-01-02T00:00:00+05:30")])).toBeNull();
    expect(pricesLine("broker:upstox", [])).toBeNull();
  });

  const source = (over: Partial<CandleSourceState>): CandleSourceState => ({
    mode: "sample", setMode: noop, broker: "", setBroker: noop, lookbackDays: 30, setLookbackDays: noop, sources: [], usable: false, loading: false,
    label: "lake:daily", fetch: () => Promise.reject(new Error("unused")), fetchChains: () => Promise.reject(new Error("unused")), sampleHidden: true, ...over,
  });

  it("with no broker and stored history: Daily runs on it, intraday needs the broker - in one plain sentence", () => {
    const html = renderToStaticMarkup(<MemoryRouter><PlainDataSourceBar source={source({ stored: true })} /></MemoryRouter>);
    expect(text(html)).toContain(STORED_HISTORY_NOTE);
    expect(html).toContain('data-plain-data-source="stored"');
    expect(html).toContain('href="/broker"');
    expect(html).not.toMatch(/sample|bhavcopy|lake|backend|lookback/i);
  });

  it("day-one fix 3: a broker that is only not logged in today is named, never 'No broker connected'", () => {
    const sources = [{ broker: "upstox", account_label: "primary", token_status: "EXPIRED", usable: false }] as CandleSourceState["sources"];
    const html = renderToStaticMarkup(<MemoryRouter><PlainDataSourceBar source={source({ stored: true, sources })} /></MemoryRouter>);
    expect(text(html)).toContain(storedHistoryLoginNote("Upstox"));
    expect(storedHistoryLoginNote("Upstox")).toBe("Not logged in to Upstox today: Daily backtests use ATP's stored end-of-day history. "
      + "Intraday backtests need today's login.");
    expect(text(html)).not.toContain("No broker connected");
    expect(html).toContain('data-stored-login="stub"');
  });

  it("day-one fix 3: History is a plain choice of about a year (the server's Daily minimum) or two, never '30 days'", () => {
    const html = renderToStaticMarkup(<MemoryRouter><PlainDataSourceBar source={source({ stored: true, storedDays: 400 })} /></MemoryRouter>);
    expect(STORED_HISTORY_CHOICES.map((c) => c.days)).toEqual([400, 730]);
    expect(text(html)).toContain("History About 1 year 2 years");
    expect(html).toContain('aria-pressed="true"');
    expect(html).not.toContain("<select");      // the strategy select stays the page's first combobox
    expect(html).not.toMatch(/type="number"/);
    // the sentence has its own line on the phone (it wrapped one word per line beside the controls)
    expect(html).toContain("flex-col");
  });

  it("without stored history the bar is as before", () => {
    const html = renderToStaticMarkup(<MemoryRouter><PlainDataSourceBar source={source({ stored: false })} /></MemoryRouter>);
    expect(text(html)).toContain(NEEDS_BROKER_FOR_DATA);
    expect(html).not.toContain(STORED_HISTORY_NOTE);
  });

  it("the admin console shows the days the shared history holds", () => {
    expect(heldRange({ cm: { first_done: "2016-01-01", last_done: "2026-10-22" }, index: { first_done: "2016-01-01", last_done: "2026-10-21" }, fo: {} }))
      .toBe("Holds: stocks 2016-01-01 to 2026-10-22 · indices 2016-01-01 to 2026-10-21");
    expect(heldRange(undefined)).toBe("Holds: no daily files yet");
  });

  it("with stored history on, the backtest pages show their own broker state instead of the gate (and nothing else changes)", () => {
    const own = [...OWN_BROKER_STATES, ...STORED_HISTORY_PATHS];
    expect(shellGates("/strategies/test", OWN_BROKER_STATES)).toBe(true);
    expect(shellGates("/strategies/test", own)).toBe(false);
    expect(shellGates("/backtest", own)).toBe(false);
    expect(shellGates("/charts", own)).toBe(true);
    expect(shellGates("/option-chain", own)).toBe(true);
  });
});
