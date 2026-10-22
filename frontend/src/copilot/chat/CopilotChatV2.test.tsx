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
  cleanQuestion, composerPlacement, jobCards, marketLineText, needsKey, settled, stampText, withTurn, workedLine,
  type ChatAnswer, type ChatHome, type Turn,
} from "./model";

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
  ai: { ready: false, title: "Add a free AI key to start", line: "Until then I answer from ATP's own data in short, fixed sentences.", settings: "/settings#ai", reason: null },
  market_line: { line: "NIFTY 50 last closed at 24,850.5 (2026-10-20); Dow structure daily uptrend, weekly range.", reason: null, as_of: "2026-10-21T03:30:00+00:00" },
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

  it("shows the top line, the question box, the eight starters and the eight job cards", () => {
    const html = view();
    const t = text(html);
    expect(t).toContain("Ask me anything about the market or your trades.");
    expect(html).toContain('id="copilot-question"');
    expect(html).toContain('data-composer="inline"');
    for (const s of STARTERS) expect(t).toContain(s);
    expect(html.match(/data-job="/g)).toHaveLength(8);
    expect(html.match(/data-built="true"/g)).toHaveLength(4);
    expect(t).toContain("Coming later");
    expect(html).toContain('href="/page-5"');                 // a job not built yet links today's page
    expect(t).toContain("Information and education only, not advice.");
  });

  it("shows the no-key card linking the AI key settings, and hides it once a key is set", () => {
    const html = view();
    expect(html).toContain("data-no-key");
    expect(text(html)).toContain("Add a free AI key to start");
    expect(html).toContain('href="/settings#ai"');
    expect(view({ home: { ...HOME, ai: { ...HOME.ai, ready: true, title: null, line: null } } })).not.toContain("data-no-key");
    expect(needsKey(HOME)).toBe(true);
    expect(needsKey(null)).toBe(false);
  });

  it("shows today's market line, or the reason it is empty", () => {
    expect(text(view())).toContain("NIFTY 50 last closed at 24,850.5");
    const empty = view({ home: { ...HOME, market_line: { line: null, reason: "Today's market line is not ready: this data is not available on your account yet.", as_of: null } } });
    expect(empty).toContain('data-market-line="empty"');
    expect(text(empty)).toContain("this data is not available on your account yet");
    expect(marketLineText(null)).toEqual({ text: "", empty: true });
  });

  it("renders an answer with its tables, chart, unknowns and how it was worked out", () => {
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

  it("uses tokens only, never a fixed pixel text size, and no order action", () => {
    for (const src of [chatSource, answerSource, pageSource]) {
      expect(src).not.toMatch(/text-\[\d+px\]/);
      expect(src).not.toMatch(/placeOrder|api\.(exitNow|placeOrder|cancelOrder)/);
    }
    const strings = JSON.stringify(COPILOT_EN.chat);
    expect(strings).not.toMatch(/\brecommend|\bbest\b|guarantee|sure[- ]?shot/i);
  });
});
