import { renderToStaticMarkup } from "react-dom/server";
import { I18nextProvider } from "react-i18next";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import type { MarketStrip, StripIndex } from "../../dashboard/types";
import type { TradeRecord } from "../../types";
import type { HeldRow } from "../../trading/model";
import { copilotI18n } from "../i18n";
import { AnswerV2, sectionTone } from "./AnswerV2";
import { ChatView } from "./CopilotChatV2";
import chatSource from "./CopilotChatV2.tsx?raw";
import { focusKind, positionFocus, symbolFocus } from "./focus";
import { asOfText, marketPills } from "./MarketLineV2";
import type { ChatAnswer, ChatHome } from "./model";
import { newlySettled, shouldJump, showJump } from "./useChatScroll";

/**
 * Owner 13 Nov ("chat window is not attractive and not looking real"; "Ask AI opened the Copilot's old clutter - a
 * specific chat should open"): the chat's layout (input at the foot), its scrolling, the missing-value rule, the
 * price label of Check my trade, and the focused chat of one item.
 */
const noop = () => undefined;
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const render = (node: React.ReactNode) => renderToStaticMarkup(<I18nextProvider i18n={copilotI18n}><MemoryRouter>{node}</MemoryRouter></I18nextProvider>);

const HOME = {
  top_line: "Ask me anything about the market or your trades.", disclaimer: "Information and education only, not advice.",
  ai: { ready: true, title: null, line: null, settings: "/settings/ai", own_key: false },
  market_line: { line: "NIFTY 50 last closed at 25,299.35 (12 Nov 2026); by its swing highs and lows the daily trend is down (weak), the weekly sideways.", reason: null, as_of: null, fii_net_crore: null },
  jobs: ["brief", "chart", "trade_check", "positions"].map((id, n) => ({ id, number: n + 1, title: id, line: "", starter: `starter ${id}`, built: true, page: null })),
  starters: [],
} as unknown as ChatHome;

const index = (key: string, ltp: number | null, change_pct: number | null, extra: Partial<StripIndex> = {}): StripIndex => ({
  key, label: key, symbol: key, exchange: "NSE", ltp, prev_close: null, change: change_pct, change_pct, as_of: "2026-11-13T06:40:00Z",
  stale: false, source: "live", missing: ltp === null, ...extra,
});
const STRIP = {
  indices: [index("NIFTY 50", 25118.6, -0.71), index("NIFTY BANK", 60948.65, null), index("SENSEX", 81462.28, -0.55), index("INDIA VIX", 16.14, 2.1)],
  source: "live", broker: null, market: { exchange: "NSE", phase: "open", is_open: true, opens_at: "09:15", closes_at: "15:30", next_open: null, holiday: null, reason: null },
  refresh_seconds: 15, fetched_at: "2026-11-13T06:40:00Z",
} as unknown as MarketStrip;

describe("owner 13 Nov: the chat's layout", () => {
  it("the input is at the bottom: the conversation first, the chips just above the box, the disclaimer under it", () => {
    const turns = [{ id: 1, question: "Check my trade idea", answer: null, error: null, sentAt: "2026-11-13T06:40:00Z" }];
    const html = render(<ChatView home={HOME} homeError={null} turns={turns} draft="" busy placement="inline" onDraft={noop} onAsk={noop} strip={{ status: "ok", data: STRIP }} />);
    const at = (s: string) => html.indexOf(s);
    expect(at("data-conversation")).toBeLessThan(at("data-composer-dock"));
    expect(at("data-composer-dock")).toBeLessThan(at("data-quick-chips"));
    expect(at("data-quick-chips")).toBeLessThan(at('data-composer="inline"'));
    expect(html.slice(at('data-composer="inline"'))).toContain("data-disclaimer");
    expect(html).toContain("data-typing");                                          // the answer is on its way
    expect(text(html)).toContain("12:10");                                          // his message's time (IST)
  });

  it("the summary line wraps in full - never cut mid-sentence", () => {
    const html = render(<ChatView home={HOME} homeError={null} turns={[]} draft="" busy={false} placement="inline" onDraft={noop} onAsk={noop} />);
    const line = /<p[^>]*data-market-line="ok"[^>]*>[\s\S]*?<\/p>/.exec(html)?.[0] ?? "";
    expect(line).not.toContain("truncate");
    expect(line).toContain("break-words");
    expect(text(line)).toContain("the weekly sideways.");
  });

  it("Enter sends, Shift+Enter makes a new line (the box grows with it)", () => {
    expect(chatSource).toContain('if (e.key === "Enter" && !e.shiftKey) { e.preventDefault();');
    expect(chatSource).toContain("el.style.height = `${Math.min(el.scrollHeight, 160)}px`");
  });
});

describe("owner 13 Nov: auto-scroll", () => {
  it("a new question or another conversation jumps to the newest message; the same last turn does not", () => {
    expect(shouldJump(null, 1)).toBe(true);
    expect(shouldJump(3, 4)).toBe(true);
    expect(shouldJump(4, 4)).toBe(false);
    expect(shouldJump(4, null)).toBe(false);
  });

  it("an answer landing is brought into view once; a restored turn never pulls the screen", () => {
    const seen = new Set<number>();
    const turns = [{ id: 1, answer: {}, error: null, at: "2026-11-13T05:00:00Z" }, { id: 2, answer: null, error: null }];
    expect(newlySettled(turns, seen)).toEqual([]);
    expect(newlySettled([turns[0], { id: 2, answer: {}, error: null }], seen)).toEqual([2]);
    expect(newlySettled([turns[0], { id: 2, answer: {}, error: null }], seen)).toEqual([]);
  });

  it("'Jump to latest' shows only when he has scrolled up in a conversation", () => {
    expect(showJump(false, 3)).toBe(true);
    expect(showJump(true, 3)).toBe(false);
    expect(showJump(false, 0)).toBe(false);
  });
});

describe("owner 13 Nov: a missing value is hidden, a stale one says when", () => {
  it("BANK NIFTY without a change shows its price only; an unpriced index is left out", () => {
    const pills = marketPills({ ...STRIP, indices: [...STRIP.indices.slice(0, 3), index("INDIA VIX", null, null)] }, Date.parse("2026-11-13T07:00:00Z"));
    expect(pills.map((p) => p.key)).toEqual(["NIFTY 50", "NIFTY BANK", "SENSEX"]);
    expect(pills[1]).toMatchObject({ value: "60,948.65", pct: null });
    const html = render(<ChatView home={HOME} homeError={null} turns={[]} draft="" busy={false} placement="inline" onDraft={noop} onAsk={noop} strip={{ status: "ok", data: STRIP }} />);
    const strip = html.slice(html.indexOf("data-numbers-strip"), html.indexOf("data-details"));
    expect(text(strip)).not.toContain("–");
    expect(strip).not.toContain("data-fii-net");                                   // FII net not known: left out
  });

  it("a live price has no 'as of'; a remembered level its time; the last close its date", () => {
    const now = Date.parse("2026-11-13T07:00:00Z");
    expect(asOfText(index("NIFTY 50", 1, 1), now)).toBeNull();
    expect(asOfText(index("NIFTY 50", 1, 1, { source: "memory" }), now)).toBe("as of 12:10");
    expect(asOfText(index("NIFTY 50", 1, 1, { source: "live", stale: true, as_of: "2026-11-12T09:59:00Z" }), now)).toBe("as of 12 Nov");
    expect(asOfText(index("NIFTY 50", 1, 1, { source: "eod", as_of: "2026-11-12" }), now)).toBe("Last close · 12 Nov");
  });
});

describe("owner 13 Nov: Check my trade's price label", () => {
  it("the Price row reads the live price with its time, never yesterday's close as 'the price'", () => {
    const answer = {
      job: "trade_check", job_number: 3, title: "Check my trade", summary: ["Here is what ATP's data shows."], source: "rules",
      sections: [{ title: "Price", lines: ["Price: 25,118.60 at 12:10"] },
        { title: "Area", lines: ["The price 25,118.60 at 12:10 is not inside any of ATP's stored areas."] }],
      blocks: [], unknowns: [], worked: [], notes: [], starters: [], tool_calls: [], disclaimer: "", refused: null,
    } as unknown as ChatAnswer;
    const html = render(<AnswerV2 answer={answer} disclaimer={false} />);
    const row = /<li[^>]*data-answer-row[^>]*>[\s\S]*?<\/li>/.exec(html)?.[0] ?? "";
    expect(text(row)).toBe("Price: 25,118.60 at 12:10");
    expect(text(html)).not.toContain("25,299.35");
  });
});

describe("owner 13 Nov: answer sections as clean cards", () => {
  it("one meaning chip per section from its own words; every line stays on screen (lists such as warnings never fold)", () => {
    expect(sectionTone(["Daily: the structure reads down (weak)."])).toEqual({ tone: "down", label: "Down" });
    expect(sectionTone(["Daily: the structure reads up; your bullish idea is with it."])).toEqual({ tone: "up", label: "With your side" });
    expect(sectionTone(["Daily: the structure reads up; your bearish idea is against it."])).toEqual({ tone: "warn", label: "Against your side" });
    expect(sectionTone(["2 lots from 24,600 to 24,480 puts 18,000 at risk: above your risk per trade."])?.tone).toBe("warn");
    expect(sectionTone(["Weekly: the structure reads range."])).toEqual({ tone: "neutral", label: "Sideways" });
    expect(sectionTone(["NIFTY: nothing else stated yet."])).toBeNull();
    expect(sectionTone(["The volume against its average."])).toBeNull();          // "against its" is not "against it"
  });
});

// --- the focused chat -----------------------------------------------------------------------------------------------------

const POSITION = {
  id: 42, mode: "PAPER", symbol: "NIFTY 25150 CE 17 NOV 26", strategy_id: "manual", direction: "SHORT", entry_time: "2026-11-10T05:00:00Z",
  entry_price: 174.6, quantity: 130, stop_loss: 0, target1: null, target2: null, exit_time: null, exit_price: null, exit_reason: null, pnl: null,
  charges: 0, instrument_kind: "OPTION", option_position: "WRITE", lot_size: 65, expiry: "2026-11-17", underlying_symbol: "NIFTY",
} as unknown as TradeRecord;
const ROW = {
  instrument: "NIFTY 25150 CE", expiryLabel: "17 Nov", ltp: 123.75, pnl: 6597.5, chartSymbol: "NIFTY",
  levels: [{ label: "Stop", value: "no stop", tone: "down" }, { label: "Premium ceiling", value: "0.00", tone: "neutral" }],
} as unknown as HeldRow;
const AREA = { low: 24989.35, high: 25141.3, state: "fresh", where: "below" as const, distance_pct: 0.03, range_text: "24,989.35–25,141.30", from: "strike" as const, change: null };

describe("owner 13 Nov: Ask AI on an item opens a focused chat", () => {
  it("which item: a position, an order, a signal, a scan hit, a strike, a chart - the top bar (no item) is the general chat", () => {
    expect(focusKind({ symbol: "NIFTY 25150 CE 17 NOV 26", object: { kind: "trade", id: 42 }, page: "positions" })).toBe("position");
    expect(focusKind({ symbol: "SBIN", object: { kind: "trade", id: 7 }, page: "orders" })).toBe("order");
    expect(focusKind({ symbol: "SBIN", object: { kind: "signal", id: 3 }, page: "signals" })).toBe("signal");
    expect(focusKind({ symbol: "SBIN", page: "screener" })).toBe("scan");
    expect(focusKind({ symbol: "NIFTY", page: "option-chain" })).toBe("strike");
    expect(focusKind({ symbol: "NIFTY", timeframe: "15min", page: "charts" })).toBe("chart");
    expect(focusKind(null)).toBeNull();
    expect(focusKind({ symbol: "NIFTY 50" })).toBeNull();                         // the top bar's Ask AI: the general chat
  });

  it("the position's header, facts (numbers first) and questions come from its own data; nothing advises", () => {
    const f = positionFocus(POSITION, ROW, AREA, 4);
    expect(f.title).toBe("NIFTY 25150 CE · 17 Nov · Short 2 lots");
    expect(f.facts.map((x) => `${x.label} ${x.value}`)).toEqual(["Avg 174.60", "LTP 123.75", "P&L +₹6,598", "Expiry 4 days", "Stop not set", "Area 0.03% below · 24,989.35–25,141.30"]);
    expect(f.facts.find((x) => x.key === "pnl")?.tone).toBe("up");
    expect(f.facts.find((x) => x.key === "stop")?.tone).toBe("warn");
    expect(f.suggestions).toEqual([
      "What happens to this position if NIFTY reaches the area 24,989–25,141?",
      "My stop is not set; show me the stop options in my settings.",
      "Explain this position's P&L and its time decay to expiry (4 days).",
      "Show this option's chart.",
    ]);
    for (const q of f.suggestions) expect(q).not.toMatch(/\b(buy|sell|target|take profit|you should)\b/i);
  });

  it("the questions differ by item type, and a missing fact leaves its question and chip out", () => {
    const kinds = ["order", "signal", "scan", "strike", "chart"] as const;
    const sets = kinds.map((k) => symbolFocus(k, { symbol: "SBIN", timeframe: k === "chart" ? "15min" : undefined }).suggestions.join("|"));
    expect(new Set(sets).size).toBe(kinds.length);
    expect(symbolFocus("scan", { symbol: "SBIN" }).suggestions[0]).toBe("Why did SBIN match this scan?");
    expect(symbolFocus("order", {}).suggestions.every((q) => !q.includes("\u0000"))).toBe(true);
    const bare = positionFocus({ ...POSITION, instrument_kind: "UNDERLYING", symbol: "SBIN", underlying_symbol: null, lot_size: null, quantity: 10, direction: "LONG" } as unknown as TradeRecord,
      { ...ROW, instrument: "SBIN", expiryLabel: null, ltp: null, pnl: null, chartSymbol: "SBIN", levels: [{ label: "Stop", value: "780.00", tone: "down" }] } as unknown as HeldRow, null, null);
    expect(bare.title).toBe("SBIN · Long 10");
    expect(bare.facts.map((x) => x.key)).toEqual(["avg", "stop"]);
    expect(bare.suggestions).toEqual(["Explain this position's P&L.", "Read the SBIN chart."]);
  });

  it("the focused chat shows the item, its facts and its questions; the market is folded and the general chips and Advanced are gone", () => {
    const focus = positionFocus(POSITION, ROW, AREA, 4);
    const html = render(<ChatView home={HOME} homeError={null} turns={[]} draft="" busy={false} placement="inline" onDraft={noop} onAsk={noop}
      strip={{ status: "ok", data: STRIP }} focus={focus} onLeaveFocus={noop} about={<span data-about-stub />} />);
    expect(html).toContain('data-focus="position"');
    expect(text(html)).toContain("NIFTY 25150 CE · 17 Nov · Short 2 lots");
    expect(html.match(/data-suggestion/g)).toHaveLength(4);
    expect(html).toMatch(/data-numbers-strip[^>]*data-folded="true"/);
    expect(html).not.toContain("data-market-pills");                               // the general market card: one tap away
    expect(html).not.toContain("data-quick-chips");
    expect(html).not.toContain('data-details="copilot.chat.advanced"');
    expect(html).not.toContain("data-about-stub");                                  // the header names the item instead
    expect(html).not.toContain("data-market-line=");
    expect(html).toContain("data-focus-leave");
  });

  it("Ask AI from a position starts a NEW conversation (no old messages); the top bar's general chat is unchanged", () => {
    expect(chatSource).toContain("const focused = !compact && focusKind(context) !== null;");
    expect(chatSource).toMatch(/if \(focused \|\| \(h\.turns\.length > 0 && !keptIsCurrent\(h\)\)\) \{[^\n]*\n\s*setKeep\(h\.keep\);\n\s*if \(!convoRef\.current\) fresh\.current = true;\n\s*return;/);
    expect(chatSource).toContain("chatApi.ask(question, about, target())");            // the item's context rides on the request
    const general = render(<ChatView home={HOME} homeError={null} turns={[]} draft="" busy={false} placement="inline" onDraft={noop} onAsk={noop} strip={{ status: "ok", data: STRIP }} />);
    expect(general).not.toContain("data-focus=");
    expect(general).toContain("data-quick-chips");
    expect(general).toContain('data-details="copilot.chat.advanced"');
    expect(general).toContain("data-market-pills");
  });
});
