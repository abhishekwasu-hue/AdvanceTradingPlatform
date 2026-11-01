import { renderToStaticMarkup } from "react-dom/server";
import { I18nextProvider } from "react-i18next";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { PAGE_COMPONENTS_V2 } from "../../shell/pagesV2";
import { copilotI18n } from "../i18n";
import { COPILOT_EN } from "../locales/en";
import answerSource from "./AnswerV2.tsx?raw";
import { AnswerV2 } from "./AnswerV2";
import chatSource from "./CopilotChatV2.tsx?raw";
import { ChatView, type ChatViewProps } from "./CopilotChatV2";
import pageSource from "./CopilotPageV2.tsx?raw";
import {
  answerParts, areaText, cardAreaText, cardTrendText, cleanQuestion, hasRawMarkdown, notReadyText, swingText, composerPlacement, distanceText, headlineAreas, jobCards, marketLineText, needsKey, notSetUpLine,
  numbersCells, quickChips, settled, stampText, toolsUsedLine, withTurn, workedLine,
  type ChatAnswer, type ChatHome, type Turn,
} from "./model";
import type { MarketStrip } from "../../dashboard/types";

/**
 * Item 5 (owner's handover): the chat-first Copilot page - the starters, the no-key and empty states, the answer with
 * its tables, chart, "I don't know" and "How I worked this out", a refusal, and the phone layout rule. Made-up data.
 */
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, "\"").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const noop = () => undefined;

const STARTERS = ["Today's market brief", "Read the NIFTY chart", "Check my trade idea", "How are my open positions?", "Review my last trade",
  "Turn my idea into a strategy and test it", "What does the option chain show?", "Teach me: what is a demand zone?"];
const JOBS = STARTERS.map((starter, i) => ({
  id: (["brief", "chart", "trade_check", "positions", "review", "strategy", "option_chain", "teacher"] as const)[i], number: i + 1,
  title: ["Pre-market brief", "Chart reader", "Pre-trade check", "Position watch", "Post-trade coach", "Strategy builder", "Option chain and risk", "Teacher"][i],
  line: "A made-up line.", starter, built: [0, 1, 6, 7].includes(i),
  page: [0, 1, 6, 7].includes(i) ? null : { label: `Open page ${i + 1}`, to: `/page-${i + 1}` },
}));
const HOME: ChatHome = {
  top_line: "Ask me anything about the market or your trades.",
  ai: { ready: false, title: "AI is off — answers are from rules", line: "Connect your own AI key to have the answers worded by AI.", settings: "/settings/ai", reason: null },
  market_line: { line: "NIFTY 50 last closed at 24,850.5 (20 Oct 2026); by its swing highs and lows the daily trend is up, the weekly sideways.", reason: null, as_of: "2026-10-21T03:30:00+00:00",
    fii_net_crore: -1234.56, fii_as_of: "2026-10-20" },
  disclaimer: "Information and education only, not advice. Every trading decision is yours.", jobs: JOBS, starters: STARTERS,
};
const ANSWER: ChatAnswer = {
  job: "chart", job_number: 2, title: "Chart reader", summary: ["NIFTY last closed at 24,850.5."], answer: "NIFTY last closed at 24,850.5.",
  sections: [{ title: "Structure per timeframe", lines: ["Daily: up, protected level 24,310"] }],
  blocks: [
    { type: "table", table: { title: "NIFTY key areas", columns: [{ key: "role", label: "Side", kind: "text" }, { key: "low", label: "From", kind: "price" }],
      rows: [{ role: "demand", low: 24520 }] } },
    { type: "chart", title: "Support / Resistance from OI", chart: { type: "bar", x_kind: "strike", x_label: "Strike", y_label: "Open interest", marker: null,
      series: [{ key: "put_oi", label: "Put OI", tone: "up", points: [{ x: 24600, y: 152000 }, { x: 24650, y: 80000 }] }] } },
  ],
  unknowns: ["I don't know the NIFTY key areas: no zones for this instrument."],
  worked: [{ tool: "get_chart_structure", label: "NIFTY structure per timeframe", ok: true, as_of: "2026-10-21T03:30:00+00:00", source: "price_action_layer",
             numbers: [{ label: "last_close", value: 24850.5 }], note: null, args: { symbol: "NIFTY" } },
           { tool: "get_market_zones", label: "NIFTY market zones", ok: false, as_of: null, source: "market_zones", numbers: [], note: "no zones for this instrument", args: {} }],
  action: null, refused: null, starters: [], concepts: [], notes: ["Structure reading from ATP's zone layers (the full Price Action reader is not in ATP yet)."],
  disclaimer: HOME.disclaimer, source: "rules", tool_calls: ["get_chart_structure", "get_market_zones"],
};
const HEADLINED: ChatAnswer = {
  ...ANSWER, summary: ["SENSEX last closed at 81,261.1. On the daily chart the structure reads up, protected level 79,493.7."],
  headline: { symbol: "SENSEX", price: 81261.1, price_label: "Last close", as_of: "2026-10-21T03:30:00+00:00", trend: "up", timeframe: "Daily",
    area: { side: "supply", from: 82011.6, to: 82273.2, where: "above", distance: 750.5, distance_pct: 0.92 },
    chart: { symbol: "SENSEX", exchange: "BSE", timeframe: "day" } },
};
// M59 CR-1: the owner's 30 Oct screenshot - the AI wording with raw Markdown and a missing 1-hour history.
const AI_PROSE = "**Weekly:** sideways.\n**Daily:** up, protected level 24,310.\n- **1-hour:** I don't know. Only 0 of the 30 bars needed are stored.";
const CARDED: ChatAnswer = {
  ...HEADLINED, source: "ai", summary: [AI_PROSE], answer: AI_PROSE,
  structure_card: {
    symbol: "NIFTY", price: 24850.5, timeframe: "Daily",
    timeframes: [
      { tf: "1w", label: "Weekly", ready: true, trend: "sideways", weak: false, protected: null, last_break: null },
      { tf: "1d", label: "Daily", ready: true, trend: "up", weak: false, protected: 2431000, last_break: { label: "Swing break", price: 24790 } },
      { tf: "1h", label: "1-hour", ready: false, trend: null, weak: false, protected: null, last_break: null },
      { tf: "15m", label: "15-minute", ready: true, trend: "down", weak: true, protected: 24730.5, last_break: null },
    ],
    areas_above: [{ side: "supply", from: 25080, to: 25160, where: "above", distance: 229.5 }],
    areas_below: [{ side: "demand", from: 24520, to: 24610, where: "below", distance: 240.5 }],
    swing_levels: [{ side: "high", price: 25010.5 }, { side: "low", price: 24600.5 }],
    watch: "Whether the daily protected level holds or breaks on a closed bar, and how the price behaves when it reaches the nearest area.",
  },
};
const RULE_TEXTS = ["The 20 EMA crosses above the 50 EMA.", "RSI (14) is above 50."];
const STRATEGY: ChatAnswer = {
  ...ANSWER, job: "strategy", job_number: 6, title: "Strategy builder", blocks: [], unknowns: [], notes: [],
  summary: ["Your idea as a draft paper strategy, built only from ATP's own building blocks."],
  sections: [{ title: "Long side: all of these hold on the 15-minute chart", lines: RULE_TEXTS },
    { title: "Your settings", lines: ["Where the idea is wrong: 1.5 x ATR (14) from the entry.", "Profit objective: 2R."] },
    { title: "Values you decide", lines: ["The size: the Trading bot page starts with one lot or your own saved default; change it there."] },
    { title: "How I build it", lines: ["I use only ATP's own building blocks."] }],
  action: { label: "Review it on the Trading bot page", to: "/deployments" }, followup: { label: "Test it on past data", question: "x. Test it on past data." },
  draft: { rules: RULE_TEXTS.map((text) => ({ side: "long", text })) } as unknown as ChatAnswer["draft"],
};
const STRIP: MarketStrip = {
  indices: [
    { key: "NIFTY 50", label: "NIFTY 50", symbol: "NIFTY 50", exchange: "NSE", ltp: 24812.35, prev_close: 24693.95, change: 118.4, change_pct: 0.48, as_of: null, stale: false, source: "live", missing: false },
    { key: "NIFTY BANK", label: "BANK NIFTY", symbol: "NIFTY BANK", exchange: "NSE", ltp: 53946.1, prev_close: 54158.65, change: -212.55, change_pct: -0.39, as_of: null, stale: false, source: "live", missing: false },
    { key: "SENSEX", label: "SENSEX", symbol: "SENSEX", exchange: "BSE", ltp: 81261.1, prev_close: 81020.4, change: 240.7, change_pct: 0.3, as_of: null, stale: false, source: "live", missing: false },
    { key: "INDIA VIX", label: "INDIA VIX", symbol: "INDIA VIX", exchange: "NSE", ltp: 13.42, prev_close: 13.63, change: -0.21, change_pct: -1.54, as_of: null, stale: false, source: "live", missing: false },
  ],
  source: "live", broker: null, market: { exchange: "NSE", phase: "open", is_open: true, opens_at: "09:15", closes_at: "15:30", next_open: null, holiday: null, reason: null },
  refresh_seconds: 15, fetched_at: "2026-10-21T03:30:00Z",
} as MarketStrip;
const REFUSAL: ChatAnswer = {
  ...ANSWER, job: "refuse", job_number: null, title: "I can't do that", summary: ["I can't tell you to buy or sell anything. That decision is always yours."],
  sections: [], blocks: [], unknowns: [], worked: [], notes: [], refused: "advice", starters: ["Read the NIFTY chart"], tool_calls: [],
};

function render(node: React.ReactNode): string {
  return renderToStaticMarkup(<I18nextProvider i18n={copilotI18n}><MemoryRouter>{node}</MemoryRouter></I18nextProvider>);
}
function view(props: Partial<ChatViewProps> = {}): string {
  return render(<ChatView home={HOME} homeError={null} turns={[]} draft="" busy={false} placement="inline" onDraft={noop} onAsk={noop} {...props} />);
}

describe("the chat-first Copilot page", () => {
  it("is the v2 Copilot, lazy, and keeps today's tabs at /copilot/<tab>", () => {
    expect(PAGE_COMPONENTS_V2.copilot).toBeDefined();
    expect(pageSource).toContain("isCopilotTab(tab)");
    expect(pageSource).toContain('lazy(() => import("../../pages/AiCopilotPage"))');
  });

  it("CU2: screen 1 reads numbers strip, then the four chips, then the question box - in that order", () => {
    const html = view({ strip: { status: "ok", data: STRIP } });
    const at = (needle: string) => { const i = html.indexOf(needle); expect(i, needle).toBeGreaterThan(-1); return i; };
    expect(at("data-numbers-strip")).toBeLessThan(at("data-quick-chips"));
    expect(at("data-quick-chips")).toBeLessThan(at('data-composer="inline"'));
    expect(at('data-composer="inline"')).toBeLessThan(at('data-details="copilot.chat.advanced"'));
    const t = text(html);
    for (const label of ["NIFTY 50", "BANK NIFTY", "SENSEX"]) expect(html).toContain(`data-index="${label === "BANK NIFTY" ? "NIFTY BANK" : label}"`);
    expect(t).toContain("81,261.10");
    expect(html).toContain('data-vix-level="');
    expect(t).toContain("FII net");
    expect(html).toContain('data-fii-net="ok"');
    expect(t).toMatch(/−1,235\s*cr/);
    expect([...html.matchAll(/data-quick="([a-z_]+)"/g)].map((m) => m[1])).toEqual(["brief", "chart", "trade_check", "positions"]);
    expect(t).toContain("Brief Read chart Check my trade My positions");
  });

  it("CU2: the question box is the ONLY primary action on screen 1; the jobs sit under Advanced, with no Ready pills", () => {
    for (const placement of ["inline", "bottom"] as const) {
      const html = view({ placement, connect: <div data-panel-stub /> });
      expect(html.match(/bg-brand /g) ?? []).toHaveLength(1);
      expect(html.match(/data-primary/g)).toHaveLength(1);
    }
    const html = view();
    const advanced = html.slice(html.indexOf('data-details="copilot.chat.advanced"'));
    expect(advanced.match(/data-job="/g)).toHaveLength(8);
    expect(html.slice(0, html.indexOf('data-details="copilot.chat.advanced"'))).not.toContain("data-job=");
    expect(html).not.toMatch(/<details[^>]*copilot\.chat\.advanced[^>]*open/);
    expect(advanced).toContain('href="/page-5"');                 // a job not built yet links today's page
    expect(text(html)).not.toContain("Ready");
    expect(html).not.toContain("data-starters");
    expect(html.match(/data-no-key/g)).toHaveLength(1);                 // CU5: one line, said once
    expect(html.match(/data-disclaimer/g)).toHaveLength(1);
    expect(text(html)).toContain("Information and education only, not advice.");
  });

  it("CU5: no key - one line 'AI is off — answers are from rules' with a Connect button; never fixed sentences first", () => {
    const html = view();
    const t = text(html);
    expect(t.match(/AI is off — answers are from rules/g)).toHaveLength(1);
    expect(t).not.toContain("fixed sentences");
    expect(t).not.toContain("Add a free AI key");
    expect(html.match(/data-ai-state="off"/g)).toHaveLength(1);
    expect(html).toMatch(/<a[^>]*data-connect[^>]*href="\/settings\/ai"[^>]*>Connect<\/a>/);
    expect(html.indexOf("data-no-key")).toBeLessThan(html.indexOf("data-numbers-strip"));       // one line, then the numbers
    expect(view({ home: { ...HOME, ai: { ...HOME.ai, title: null } } })).toContain(COPILOT_EN.chat.aiOffLine);
    const connect = view({ connect: <div data-panel-stub /> });
    expect(connect).toMatch(/<button[^>]*aria-expanded="false"[^>]*data-connect[^>]*>Connect<\/button>/);
  });

  it("CU2 / CU5: the Connect button opens the key setup; AI on once a key answers", () => {
    const html = view();
    expect(html).toContain('data-ai-state="off"');
    expect(html).toContain('href="/settings/ai"');
    const withPanel = view({ connect: <div data-panel-stub /> });
    expect(withPanel).toContain('aria-expanded="false"');
    expect(withPanel).not.toContain("data-panel-stub");
    const open = view({ connect: <div data-panel-stub />, keyOpen: true });
    expect(open).toContain("data-connect-slot");
    expect(open).toContain("data-panel-stub");
    expect(needsKey(HOME)).toBe(true);
    expect(needsKey(null)).toBe(false);
    const ready = view({ home: { ...HOME, ai: { ...HOME.ai, ready: true, title: null, line: null, own_key: true } }, connect: <div data-panel-stub />,
      aiChip: <span data-chip-stub />, keyOpen: true });
    expect(ready).toContain("data-chip-stub");
    expect(ready).not.toContain("data-panel-stub");
    expect(view({ home: { ...HOME, ai: { ...HOME.ai, ready: true, title: null, line: null } } })).toContain('data-ai-state="on"');
  });

  it("CU2: the numbers strip says a dash for what is not known, never 0", () => {
    const html = view({ strip: { status: "error" }, home: { ...HOME, market_line: { ...HOME.market_line, fii_net_crore: null } } });
    expect(html).toContain('data-fii-net="unknown"');
    expect(html).toContain('data-vix-level="unknown"');
    expect(numbersCells(null).map((c) => c.index)).toEqual([null, null, null]);
    expect(quickChips(JOBS.slice(2)).map((c) => c.id)).toEqual(["trade_check", "positions"]);
  });

  it("shows today's market line, or the reason it is empty", () => {
    expect(text(view())).toContain("NIFTY 50 last closed at 24,850.5");
    const empty = view({ home: { ...HOME, market_line: { line: null, reason: "Today's market line is not ready: this data is not available on your account yet.", as_of: null } } });
    expect(empty).toContain('data-market-line="empty"');
    expect(text(empty)).toContain("this data is not available on your account yet");
    expect(marketLineText(null)).toEqual({ text: "", empty: true });
  });

  it("renders an answer with its tables, chart, unknowns and how it was worked out (behind Details)", () => {
    const html = render(<AnswerV2 answer={ANSWER} />);
    const t = text(html);
    expect(t).toContain("NIFTY last closed at 24,850.5.");
    expect(t).toContain("Structure reading from ATP's zone layers");
    expect(html).toContain('data-block="table"');
    expect(html).toContain('data-chart="bar"');
    expect(t).toContain("I don't know the NIFTY key areas");
    expect(t).toContain("How I worked this out");
    expect(t).toContain("NIFTY structure per timeframe: last close 24850.5");
    expect(t).toContain("NIFTY market zones: no answer (no zones for this instrument)");
    expect(t).toContain("ATP's own sentences");
    expect(html).toContain('data-disclaimer');
    const details = html.slice(html.indexOf('data-details="copilot.answer"'));
    for (const part of ["data-unknowns", "data-worked", "data-note="]) expect(details).toContain(part);
  });

  it("CU3: the headline number row comes before any sentence, then the mini chart and ONE action; Details is folded", () => {
    const html = render(<AnswerV2 answer={HEADLINED} onAsk={noop} />);
    const at = (needle: string) => { const i = html.indexOf(needle); expect(i, needle).toBeGreaterThan(-1); return i; };
    expect(at('data-headline="SENSEX"')).toBeLessThan(at("data-summary"));
    expect(at("data-summary")).toBeLessThan(at('data-mini-chart="SENSEX"'));
    expect(at('data-mini-chart="SENSEX"')).toBeLessThan(at("data-actions"));
    expect(at("data-actions")).toBeLessThan(at('data-details="copilot.answer"'));
    const head = text(html.slice(at('data-headline="SENSEX"'), at("data-summary")));
    expect(head).toContain("81,261.10");
    expect(head).toContain("▲ Up");
    expect(head).toContain("Supply 82,011.60 – 82,273.20");
    expect(head).toContain("750.50 (0.92%) above");
    expect(html.match(/data-action="/g)).toHaveLength(1);
    expect(html).toContain('href="/charts/SENSEX?tf=day&amp;exchange=BSE"');
    expect(html).not.toMatch(/<details[^>]*copilot\.answer[^>]*open/);
    const before = html.slice(0, at('data-details="copilot.answer"'));
    for (const part of ["data-unknowns", "data-worked", "data-tools-used", "data-section=", "data-block="]) expect(before).not.toContain(part);
    expect(distanceText({ side: null, from: 1, to: 2, where: "inside", distance: 0, distance_pct: 0 }, String)).toBe("Price is inside");
    expect(areaText({ side: null, from: 1, to: 2, where: "below", distance: 3, distance_pct: null }, String)).toBe("1 – 2");
    // M75 W-1: the nearest area is ONE filled band in the seller colour, labelled on the chart - no "Area top" / "Area bottom" price lines
    expect(headlineAreas(HEADLINED.headline)).toEqual([
      { low: 82011.6, high: 82273.2, role: "supply", color: "#dc2626", opacity: 0.18, style: "solid", label: "Seller area" }]);
    expect(headlineAreas({ ...HEADLINED.headline!, area: { side: null, from: 5, to: 2, where: "below", distance: 1, distance_pct: null } }))
      .toMatchObject([{ low: 2, high: 5, role: "neutral", label: "Area" }]);
    expect(headlineAreas(null)).toEqual([]);
  });

  it("M59 CR-1: the chart reading is a card - table, areas nearby, swing levels, one line; never raw Markdown or a bar count", () => {
    const html = render(<AnswerV2 answer={CARDED} onAsk={noop} />);
    const at = (needle: string) => { const i = html.indexOf(needle); expect(i, needle).toBeGreaterThan(-1); return i; };
    const details = at('data-details="copilot.answer"');
    const card = html.slice(0, details);
    // the order: headline row, the table, the areas, the swing levels, the one line; the prose is behind Details
    expect(at('data-headline="SENSEX"')).toBeLessThan(at('data-card-block="table"'));
    expect(at('data-card-block="table"')).toBeLessThan(at('data-card-block="areas"'));
    expect(at('data-card-block="areas"')).toBeLessThan(at('data-card-block="swings"'));
    expect(at('data-card-block="swings"')).toBeLessThan(at('data-card-block="watch"'));
    expect(card).not.toContain("data-summary");
    expect(html.slice(details)).toContain('data-summary="folded"');
    const t = text(card);
    for (const head of ["Timeframe", "Trend", "Protected level", "Last break"]) expect(t).toContain(head);
    expect(t).toContain("24,31,000.00");                       // 2 decimals, Indian grouping
    expect(t).toContain("Swing break 24,790.00");
    expect(t).toContain("▼ Down · weak");
    expect(card).toContain('data-not-ready="1h"');
    expect(t).toContain("1-hour: data not ready");
    expect(t).toContain("25,080.00 – 25,160.00 Supply · 229.50 pts");
    expect(t).toContain("24,520.00 – 24,610.00 Demand · 240.50 pts");
    expect(card).toContain('aria-label="Supply 25,080.00 – 25,160.00 · 229.50 pts"');
    expect(t).toContain("Last break: Swing break 24,790.00");                 // the phone line under its row
    expect(t).toContain("Swing high 25,010.50");
    expect(t).toContain("What to watch:");
    expect(t).not.toMatch(/of the 30|bars needed|\b\d+ of \d+ bars/);
    expect(t).not.toMatch(/\b(buy|sell|target|stop[- ]?loss)\b/i);
    // the AI wording behind Details is rendered, never printed raw
    expect(html).not.toContain("**");
    expect(html).toContain('<strong class="font-semibold text-fg">Weekly:</strong>');
    // phone: at most 4 blocks on the first screen (headline + table + areas + line; the swing chips sit behind a (?))
    expect(card).toMatch(/<div class="hidden md:block" data-card-block="swings">/);
    expect(card).toContain('aria-label="Help: Swing levels"');
    for (const b of ["data-headline=", 'data-card-block="table"', 'data-card-block="areas"', 'data-card-block="watch"']) expect(card).toContain(b);
    expect(card).not.toContain('data-card-block="swings-phone"');
  });

  it("M59 CR-1: every timeframe missing - one 'data not ready' chip each, no table numbers, no counts", () => {
    const base = CARDED.structure_card!;
    const empty = { ...base, timeframes: base.timeframes.map((r) => ({ ...r, ready: false, trend: null, protected: null, last_break: null })),
      areas_above: [], areas_below: [], swing_levels: [], watch: null };
    const html = render(<AnswerV2 answer={{ ...CARDED, headline: null, structure_card: empty,
      summary: ["For the NIFTY chart, only 0 of the 30 daily bars needed are stored; history is loading."] }} />);
    const card = html.slice(0, html.indexOf('data-details="copilot.answer"'));
    expect(card.match(/data-not-ready="/g)).toHaveLength(4);
    for (const label of ["Weekly", "Daily", "1-hour", "15-minute"]) expect(text(card)).toContain(`${label}: data not ready`);
    expect(text(card)).not.toMatch(/of the 30|bars/);
    expect(card).not.toContain('data-card-block="areas"');
    expect(card).not.toContain('data-card-block="watch"');
    expect(card).not.toContain("data-section=");
  });

  it("M59 CR-1: a free-text answer without a card renders its Markdown safely (bold, lists), never the raw marks", () => {
    const html = render(<AnswerV2 answer={{ ...ANSWER, source: "ai", summary: ["**Daily:** up.\n* one\n* two\nA *soft* word and a stray ** mark. <b>x</b>"] }} />);
    expect(html).not.toContain("**");
    expect(html).toContain('<strong class="font-semibold text-fg">Daily:</strong>');
    expect(html.match(/<li>/g)?.length).toBeGreaterThanOrEqual(2);
    expect(html).not.toContain("<b>x</b>");                     // text, never markup
    expect(text(html)).toContain("A soft word and a stray mark.");
    expect(hasRawMarkdown("**Weekly:** up")).toBe(true);
    expect(hasRawMarkdown("Weekly: up")).toBe(false);
    expect(notReadyText({ label: "1-hour" })).toBe("1-hour: data not ready");
    expect(cardTrendText({ trend: null, weak: true })).toBe("No clear trend");
    expect(cardTrendText({ trend: "up", weak: true })).toBe("▲ Up · weak");
    expect(cardAreaText({ side: null, from: 1, to: 2, where: "inside", distance: 0 }, String)).toBe("1 – 2 · price inside");
    expect(swingText({ side: null, price: 5 }, String)).toBe("Swing level 5");
  });

  it("CU3: the strategy answer is a result card - rules and settings as chips, the theory behind Details", () => {
    const html = render(<AnswerV2 answer={STRATEGY} onAsk={noop} />);
    const details = html.indexOf('data-details="copilot.answer"');
    const card = html.slice(0, details);
    expect(card.match(/data-rule-chip/g)).toHaveLength(2);
    expect(card.match(/data-setting-chip/g)).toHaveLength(2);
    expect(card).toContain('data-section="Values you decide"');
    expect(card).toContain('data-action="draft"');
    expect(card).toContain("data-followup");
    expect(card).not.toContain('data-section="How I build it"');
    expect(card).not.toContain('data-section="Long side: all of these hold on the 15-minute chart"');
    expect(html.slice(details)).toContain('data-section="How I build it"');
    const parts = answerParts(STRATEGY);
    expect(parts.rules).toEqual(["The 20 EMA crosses above the 50 EMA.", "RSI (14) is above 50."]);
    expect(parts.folded.map((s) => s.title)).toEqual(["How I build it"]);
    expect(answerParts(ANSWER).shown).toHaveLength(1);
  });

  it("CU3: in the conversation the answer carries no disclaimer of its own (the page footer has the one line)", () => {
    const html = view({ turns: [{ id: 1, question: "Read the SENSEX chart", answer: HEADLINED, error: null }] });
    expect(html.match(/data-disclaimer/g)).toHaveLength(1);
  });

  it("renders a refusal in plain words with what to ask instead, and no worked-out block", () => {
    const html = render(<AnswerV2 answer={REFUSAL} onAsk={noop} />);
    expect(text(html)).toContain("I can't tell you to buy or sell anything.");
    expect(text(html)).toContain("Read the NIFTY chart");
    expect(html).not.toContain("data-worked");
  });

  it("shows the conversation: the question, then the answer, a wait or an error", () => {
    const turns: Turn[] = [{ id: 1, question: "Read the NIFTY chart", answer: ANSWER, error: null },
      { id: 2, question: "Today's market brief", answer: null, error: null }, { id: 3, question: "x?", answer: null, error: "I couldn't answer just now." }];
    const html = view({ turns });
    expect(html.match(/data-turn="/g)).toHaveLength(3);
    expect(html).toContain('aria-busy="true"');
    expect(html).toContain('role="alert"');
  });

  it("puts the question box at the bottom on a phone", () => {
    expect(composerPlacement(390)).toBe("bottom");
    expect(composerPlacement(767)).toBe("bottom");
    expect(composerPlacement(768)).toBe("inline");
    const html = view({ placement: "bottom" });
    expect(html).toContain('data-composer="bottom"');
    expect(html).not.toContain('data-composer="inline"');
    expect(chatSource).toContain("fixed inset-x-0 bottom-[calc(4rem+env(safe-area-inset-bottom))]");
  });

  it("cleans the question and keeps the turns in order", () => {
    expect(cleanQuestion("  read   the chart ")).toBe("read the chart");
    expect(cleanQuestion(" a ")).toBeNull();
    expect(cleanQuestion("x".repeat(2000))).toHaveLength(1000);
    let turns: Turn[] = [];
    for (let i = 1; i <= 25; i++) turns = withTurn(turns, { id: i, question: `q${i}`, answer: null, error: null });
    expect(turns).toHaveLength(20);
    expect(turns[0].id).toBe(6);
    expect(settled(turns, 25, ANSWER, null)[19].answer).toBe(ANSWER);
    expect(jobCards(JOBS).filter((c) => c.link)).toHaveLength(4);
    expect(workedLine(ANSWER.worked[1])).toContain("no answer");
    expect(stampText(null)).toBeNull();
    expect(stampText("2026-10-21T03:30:00+00:00")).toContain("IST");
  });

  it("lists the tools used under How I worked this out, says not set up plainly, and names the flag only to the admin", () => {
    const tooled: ChatAnswer = {
      ...ANSWER, job: "views", tools_used: ["Chart structure", "News feed"],
      worked: [...ANSWER.worked, { tool: "get_market_snapshot", label: "Market memory", ok: false, as_of: null, source: "get_market_snapshot",
                                   numbers: [], note: "market memory in the Copilot is not set up yet", args: {}, ran: false }],
    };
    const member = text(render(<AnswerV2 answer={tooled} />));
    expect(member).toContain("Tools used: Chart structure, News feed");
    expect(member).toContain("Market memory: not set up yet");
    expect(member).not.toContain("flag");
    expect(toolsUsedLine({ tools_used: [] })).toBeNull();
    const admin = text(render(<AnswerV2 answer={{ ...tooled, diagnostics: { not_set_up: [{ what: "Market memory", flag: "ai_agent" }] } }} />));
    expect(admin).toContain("Admin only - not set up here: Market memory: flag ai_agent");
    expect(notSetUpLine({})).toBeNull();
  });

  it("uses tokens only, never a fixed pixel text size, and no order action", () => {
    for (const src of [chatSource, answerSource, pageSource]) {
      expect(src).not.toMatch(/text-\[\d+px\]/);
      expect(src).not.toMatch(/placeOrder|api\.(exitNow|placeOrder|cancelOrder)/);
    }
    const strings = JSON.stringify(COPILOT_EN.chat);
    expect(strings).not.toMatch(/\brecommend|\bbest\b|guarantee|sure[- ]?shot/i);
  });
});
