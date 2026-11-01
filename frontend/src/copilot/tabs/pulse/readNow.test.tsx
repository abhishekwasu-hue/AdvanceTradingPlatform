import { renderToStaticMarkup } from "react-dom/server";
import { I18nextProvider } from "react-i18next";
import { describe, expect, it, vi } from "vitest";
import type { BriefRead, DailyBrief, MarketMemory } from "../../../types";
import { CopilotDataContext, plainReasons, readEverything, type CopilotData, type ReadOutcome } from "../../data";
import { copilotI18n } from "../../i18n";
import pulseSource from "../MarketPulseTab.tsx?raw";
import { ReadNowBar, ReadState } from "../MarketPulseTab";
import MemoryPanel from "./MemoryPanel";
import memorySource from "./MemoryPanel.tsx?raw";
import thesisSource from "./ThesisPanel.tsx?raw";

/** M82 (the Today's read card: the true state) and M83 (one "Read now" for the whole Market Pulse tab). Made-up data. */
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();

const READ_NOW_OK = { enabled: true, broker: "ok", reason: null } as const;
function read(over: Partial<BriefRead> = {}): BriefRead {
  return { state: "none", label: null, read_date: null, session_date: "2026-10-30", session_label: "Fri 30 Oct", reason: "none_ever",
           reason_text: "No market read stored yet. Press Read now.", read_now: READ_NOW_OK, ...over };
}
function brief(r: BriefRead): DailyBrief {
  return { session: { open: false, text: "Market closed - opens Mon 02 Nov 09:15 IST", next_open: null, holidays_next_7_days: [] }, read: r } as unknown as DailyBrief;
}
function memory(symbols: number, report?: MarketMemory["report"]): MarketMemory {
  const rows = ["NIFTY 50", "NIFTY BANK"].slice(0, symbols).map((symbol) => ({
    symbol, exchange: "NSE", kind: "SYMBOL", source: "broker", last_price: 25000, change_pct: 0.4, bias: "BULLISH", regime: "TRENDING_UP",
    higher_regime: "TRENDING_UP", structure: "UPTREND", captured_at: new Date().toISOString(), payload: {} }));
  return { symbols: rows, cues: [], history: {}, updated_at: null, globals: [], watchlist: ["NIFTY 50", "NIFTY BANK"], interval_minutes: 15, report } as unknown as MarketMemory;
}
function store(over: Partial<CopilotData> = {}): CopilotData {
  return { brief: brief(read()), briefError: null, briefLoading: false, reloadBrief: () => undefined, pending: [], reloadActions: () => undefined,
           newHighNews: 0, markNewsSeen: () => undefined, badgesLoading: false, memory: memory(0), memoryError: null,
           loadMemory: async () => undefined, readNow: async () => undefined, reading: false, readReport: null, readVersion: 0, ...over };
}
function render(node: React.ReactNode, value: CopilotData): string {
  return renderToStaticMarkup(
    <I18nextProvider i18n={copilotI18n}><CopilotDataContext.Provider value={value}>{node}</CopilotDataContext.Provider></I18nextProvider>);
}

describe("M82: the Today's read card shows the true state", () => {
  it("market closed with a stored read: the session date, never 'no read'", () => {
    const html = render(<ReadState read={read({ state: "last_session", label: "Last session, Fri 30 Oct", reason: null, reason_text: null })} />, store());
    expect(text(html)).toBe("Last session, Fri 30 Oct");
    expect(html).not.toMatch(/no read|needs a broker session/i);
  });

  it("token expired: Reconnect right on the card (the broker's renew panel opens in place, no link away)", () => {
    const r = read({ reason: "broker_expired", read_now: { enabled: false, broker: "expired", reason: "Your broker session has expired - reconnect it to read the market.",
                                                            reconnect: { broker: "upstox", account_label: "primary" } } });
    const html = render(<ReadState read={r} />, store());
    expect(html).toContain('data-reason="broker_expired"');
    expect(text(html)).toBe("Reconnect broker");
    expect(html).not.toContain("href=");
    expect(pulseSource).toMatch(/<ConnectPanel broker=\{again\.broker\} mode="renew"/);
  });

  it("no read for another reason: no reconnect button (worker missed / none ever)", () => {
    for (const reason of ["worker_missed", "none_ever"] as const) {
      expect(text(render(<ReadState read={read({ reason })} />, store()))).toBe("");
    }
  });

  it("the card's headline is the brief's reason line; the per-card Refresh is gone", () => {
    expect(pulseSource).toContain("<p className=\"mt-2 text-lg font-semibold leading-snug text-fg\">{brief.plan.headline}</p>");
    expect(pulseSource).not.toContain('t("states.refresh")');
  });
});

describe("M83: one Read now for the whole tab", () => {
  it("valid session: the sticky header's Read now is enabled", () => {
    const html = render(<ReadNowBar />, store());
    expect(html).toMatch(/class="sticky top-0[^"]*"/);
    expect(html).toMatch(/<button[^>]*data-testid="pulse-read-now"[^>]*>/);
    expect(html).not.toMatch(/<button[^>]*disabled=""[^>]*data-testid="pulse-read-now"/);
  });

  it("no valid broker session: disabled, with the reason shown", () => {
    const value = store({ brief: brief(read({ read_now: { enabled: false, broker: "none", reason: "No broker is connected - connect your broker on the Broker page to read the market." } })) });
    const html = render(<ReadNowBar />, value);
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*data-testid="pulse-read-now"/);
    expect(text(html)).toContain("No broker is connected - connect your broker on the Broker page to read the market.");
  });

  it("the panels have no Read now of their own - one action in the header", () => {
    expect(memorySource).not.toContain("aiMarketMemoryRefresh");
    expect(memorySource).not.toContain('t("memory.readNow")');
    expect(pulseSource.match(/readNow\(\)/g)).toHaveLength(1);
  });

  it("a read that stored no symbol shows the exact reasons on the Market memory panel, never a silent empty", () => {
    const outcome: ReadOutcome = { at: "2026-11-01T06:30:00Z", symbols: 0, cues: 0, globals: 9, error: null,
                                   reasons: plainReasons(["broker: The broker linked in your organisation was linked by a teammate.", "NIFTY 50: BrokerAPIError: UDAPI100050 invalid token"]) };
    const html = render(<MemoryPanel />, store({ readReport: outcome, memory: memory(0, { symbols: 0, cues: 0, globals: 9, errors: [] }) }));
    const words = text(html);
    expect(words).toContain("The last read stored no symbol:");
    expect(words).toContain("The broker linked in your organisation was linked by a teammate.");
    expect(words).toContain("NIFTY 50: UDAPI100050 invalid token");
    expect(words).not.toContain("The memory is empty");
    expect(words).not.toContain("BrokerAPIError");
    const bar = text(render(<ReadNowBar />, store({ readReport: outcome })));
    expect(bar).toContain("0 symbols, 0 India cues, 9 global cues stored.");
  });
});

describe("M83 A3: after one press every card re-renders from the new data, no page reload", () => {
  it("one press: one read, then the briefing reloads; the memory comes from the read itself", async () => {
    const fresh = memory(2, { symbols: 2, cues: 4, globals: 9, errors: [] });
    const steps = { refresh: vi.fn(async () => fresh), reloadBrief: vi.fn(async () => undefined), reloadMemory: vi.fn(async () => undefined),
                    now: () => "2026-11-01T06:30:00Z" };
    const { memory: got, outcome } = await readEverything(steps);
    expect(steps.refresh).toHaveBeenCalledTimes(1);
    expect(steps.reloadBrief).toHaveBeenCalledTimes(1);
    expect(steps.reloadMemory).not.toHaveBeenCalled();
    expect(got).toBe(fresh);
    expect(outcome).toEqual({ at: "2026-11-01T06:30:00Z", symbols: 2, cues: 4, globals: 9, reasons: [], error: null });
    // The same store, before and after: the Market memory panel renders the new rows straight away.
    expect(text(render(<MemoryPanel />, store()))).toContain("The memory is empty");
    const after = text(render(<MemoryPanel />, store({ memory: got, readReport: outcome })));
    expect(after).toContain("NIFTY 50");
    expect(after).toContain("NIFTY BANK");
    expect(after).not.toContain("The memory is empty");
  });

  it("a failed press still reloads what is stored, and the failure is kept for the cards", async () => {
    const boom = new Error("409");
    const steps = { refresh: vi.fn(async () => { throw boom; }), reloadBrief: vi.fn(async () => undefined), reloadMemory: vi.fn(async () => undefined) };
    const { memory: got, outcome } = await readEverything(steps);
    expect(got).toBeNull();
    expect(outcome.error).toBe(boom);
    expect(steps.reloadMemory).toHaveBeenCalledTimes(1);
    expect(steps.reloadBrief).toHaveBeenCalledTimes(1);
  });

  it("the thesis reloads on the store's read version (no own fetch of the memory)", () => {
    expect(thesisSource).toMatch(/useEffect\(\(\) => \{ void load\(\); \}, \[symbol, readVersion\]\)/);
    expect(thesisSource).not.toContain("api.aiMarketMemory()");
  });
});
