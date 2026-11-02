import { renderToStaticMarkup } from "react-dom/server";
import { I18nextProvider } from "react-i18next";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { copilotI18n } from "../i18n";
import { AnswerV2 } from "./AnswerV2";
import { chipPath, hasRawMarkdown, type ChatAnswer } from "./model";

/** M72 PR-2 (CP-2): the AI planner's answer - the headline line first, the cards as tables, the chips. Made-up data. */
const render = (node: React.ReactNode) =>
  renderToStaticMarkup(<I18nextProvider i18n={copilotI18n}><MemoryRouter initialEntries={["/copilot"]}>{node}</MemoryRouter></I18nextProvider>);
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/\s+/g, " ");

const PLANNED: ChatAnswer = {
  job: "agent", job_number: null, title: "Answer",
  summary: ["NIFTY: highest put OI at 24,600, highest call OI at 25,100", "Put OI is larger below the price."],
  answer: "Put OI is larger below the price.", headline_line: "NIFTY: highest put OI at 24,600, highest call OI at 25,100",
  sections: [], unknowns: [], starters: [], concepts: [], notes: [], refused: null, action: null, source: "ai", tool_calls: ["get_option_chain_analyses"],
  disclaimer: "Information and education only, not advice.",
  blocks: [{ type: "table", table: { title: "OI walls", columns: [{ key: "strike", label: "Strike", kind: "number" }, { key: "put_oi", label: "Put OI", kind: "number" }],
    rows: [{ strike: 24600, put_oi: 152000 }] } }],
  worked: [{ tool: "get_option_chain_analyses", label: "Option chain analyses", ok: true, as_of: "2026-10-21T03:30:00+00:00", source: null, numbers: [], note: null,
    args: { symbol: "NIFTY" } }],
  chips: [{ kind: "chart", label: "Show on chart", symbol: "NIFTY" }, { kind: "terminal", label: "Open in Terminal", symbol: "NIFTY" },
    { kind: "ask", label: "Make this an alert", question: "Make an alert for NIFTY" }],
};

describe("M72 PR-2 planner answer format", () => {
  it("shows the headline line first, then the short lines and the code-drawn table", () => {
    const html = render(<AnswerV2 answer={PLANNED} onAsk={() => undefined} />);
    const plain = text(html);
    expect(html).toContain("data-headline-line");
    expect(plain.indexOf("highest put OI at 24,600")).toBeLessThan(plain.indexOf("Put OI is larger"));
    expect(html).toContain('data-block="table"');
    expect(plain).toContain("OI walls");
    expect(hasRawMarkdown(plain)).toBe(false);
  });

  it("renders the chips in place: chart and Terminal open the symbol, the alert asks only when tapped", () => {
    const html = render(<AnswerV2 answer={PLANNED} onAsk={() => undefined} />);
    expect(html).toContain('data-chip="chart"');
    expect(html).toContain('href="/charts/NIFTY"');
    expect(html).toContain('href="/terminal?symbol=NIFTY"');
    expect(html).toContain('data-chip="ask"');
    expect(html).not.toContain('target="_blank"');
  });

  it("drops the ask chips when the card cannot ask (a stand-alone card)", () => {
    const html = render(<AnswerV2 answer={PLANNED} />);
    expect(html).not.toContain('data-chip="ask"');
    expect(html).toContain('data-chip="terminal"');
  });

  it("an answer without the planner fields renders as before", () => {
    const older: ChatAnswer = { ...PLANNED, headline_line: undefined, chips: undefined };
    const html = render(<AnswerV2 answer={older} />);
    expect(html).not.toContain("data-headline-line");
    expect(html).not.toContain("data-chips");
  });

  it("chip paths stay inside the app", () => {
    expect(chipPath({ kind: "chart", label: "Show on chart", symbol: "nifty bank" })).toBe("/charts/NIFTY%20BANK");
    expect(chipPath({ kind: "terminal", label: "Open in Terminal", symbol: "M&M" })).toBe("/terminal?symbol=M%26M");
    expect(chipPath({ kind: "ask", label: "Backtest this", question: "Backtest a strategy on NIFTY" })).toBeNull();
  });
});
