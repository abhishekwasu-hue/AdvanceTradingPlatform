import { renderToStaticMarkup } from "react-dom/server";
import { I18nextProvider } from "react-i18next";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { fixtureItem } from "../../chartAnalyze/fixtures";
import { copilotI18n } from "../i18n";
import { COPILOT_EN } from "../locales/en";
import { AttachMenu, imageFrom } from "./ChartAttachV2";
import { ChartTurnV2 } from "./ChartTurnV2";
import chatSource from "./CopilotChatV2.tsx?raw";
import { ChatView } from "./CopilotChatV2";
import { chartQuestion, isSnapshotLink, readingQuestion, withChart, type ChatHome, type Turn } from "./model";

/**
 * M59 CR-2: a chart in the Copilot's Ask box - the attach menu (Upload a chart / Paste a TradingView snapshot link),
 * the thread turn (the annotated chart and the reading, ResultViewV2) and the engine-missing states (CR-3, user vs
 * admin). The upload / link happy paths through the fake engine are the e2e (e2e/copilot-chat-v2.e2e.ts). Made-up data.
 */
const noop = () => undefined;
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, "\"").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const render = (node: React.ReactNode) => renderToStaticMarkup(<I18nextProvider i18n={copilotI18n}><MemoryRouter>{node}</MemoryRouter></I18nextProvider>);
const HOME = {
  top_line: "Ask me anything about the market or your trades.", ai: { ready: false, title: null, line: null, settings: "/settings/ai", reason: null },
  market_line: { line: "NIFTY 50 last closed at 24,850.5.", reason: null, as_of: null }, disclaimer: "Information and education only, not advice.",
  jobs: [], starters: [],
} as ChatHome;
const view = (turns: Turn[], onAttach?: () => void) =>
  render(<ChatView home={HOME} homeError={null} turns={turns} draft="" busy={false} placement="inline" onDraft={noop} onAsk={noop} onAttach={onAttach} />);

describe("M59 CR-2: the chart attach in the Ask box", () => {
  it("the attach button shows only when chart analysis is on for the account", () => {
    expect(view([], noop)).toContain('aria-label="Add a chart"');
    expect(view([])).not.toContain("data-attach-button");
  });

  it("the menu: Upload a chart, Paste a TradingView snapshot link, the Alt+S line; no order or advice word", () => {
    const html = render(<AttachMenu onAttach={noop} startOpen />);
    expect(html).toContain('role="menu"');
    expect(html).toContain('data-attach="upload"');
    expect(html).toContain('data-attach="link"');
    const t = text(html);
    expect(t).toContain("Upload a chart");
    expect(t).toContain("Paste a TradingView snapshot link");
    expect(t).toContain("Alt+S");
    expect(t).toContain("A screenshot pasted in the box works too");
    expect(html).toContain('accept="image/png,image/jpeg"');
    const mine = Object.entries(COPILOT_EN.chat).filter(([k]) => /^(attach|chart[A-Z])/.test(k)).map(([, v]) => v).join(" ");
    expect(mine).toContain("Upload a chart");
    expect(mine).not.toMatch(/\b(buy|sell|target|stop[- ]?loss)\b/i);
  });

  it("the turn's words and the follow-up reading come from the picture's symbol only when it is a plain ticker", () => {
    expect(chartQuestion({ kind: "file", file: new Blob(), name: "c.png" })).toBe("Analyze my chart");
    expect(chartQuestion({ kind: "link", link: "https://www.tradingview.com/x/Zx9Abc12/" })).toBe("Analyze my TradingView snapshot");
    expect(chartQuestion({ kind: "link", link: "https://img.example.com/c.png" })).toBe("Analyze the chart at this link");
    const item = fixtureItem(7, "formed");
    expect(readingQuestion({ result: { ...item.result!, recognised: { ...item.result!.recognised, symbol: "NSE:NIFTY" } } })).toBe("Read the NIFTY chart");
    expect(readingQuestion({ result: { ...item.result!, recognised: { ...item.result!.recognised, symbol: null } } })).toBeNull();
    expect(readingQuestion({ result: { ...item.result!, recognised: { ...item.result!.recognised, symbol: "<b>x</b>" } } })).toBeNull();
    expect(readingQuestion({ result: null })).toBeNull();
    expect(isSnapshotLink("https://www.tradingview.com/x/Zx9Abc12/")).toBe(true);
    expect(isSnapshotLink("https://www.tradingview.com/chart/AbCd/")).toBe(false);
    expect(withChart([{ id: 1, question: "q", answer: null, error: null }], 1, { status: "failed", message: "m" })[0].chart).toEqual({ status: "failed", message: "m" });
    expect(imageFrom([{ type: "image/gif" } as File])).toBeNull();
  });

  it("a done chart: the annotated chart and the reading in the thread, no page-only buttons, a link to the page", () => {
    const html = render(<ChartTurnV2 chart={{ status: "done", item: fixtureItem(7, "formed"), imageSrc: "blob:fixture" }} />);
    expect(html).toContain('data-testid="analysis-result"');
    expect(html).toContain('data-block="ca-annotated"');
    expect(html).toContain('href="/chart-analyze"');
    expect(html).not.toContain("Download / share the result image");
    expect(html).not.toMatch(/<button[^>]*>Delete<\/button>/);
    expect(html).toContain("Set an alert");                                    // the per-level alerts stay (a price alert, never an order)
    for (const label of [...html.matchAll(/<button[^>]*>(.*?)<\/button>/g)].map((m) => m[1].replace(/<[^>]+>/g, ""))) {
      expect(label).not.toMatch(/\b(buy|sell|order|place)\b/i);
    }
  });

  it("the thread: the chart card, then the Copilot's own reading under it", () => {
    const turn: Turn = { id: 3, question: "Analyze my chart", answer: null, error: null, chart: { status: "working", step: "analysing" } };
    const html = view([turn], noop);
    expect(html).toContain("data-chart-card");
    expect(text(html)).toContain("Analyze my chart");
    expect(html).not.toContain("data-chart-reading");
    const done: Turn = { ...turn, chart: { status: "done", item: { ...fixtureItem(7, "formed"), result: { ...fixtureItem(7, "formed").result!,
      recognised: { ...fixtureItem(7, "formed").result!.recognised, symbol: "NIFTY" } } }, imageSrc: null },
      answer: { job: "chart", summary: [], sections: [], blocks: [], unknowns: [], worked: [], notes: [], starters: [], source: "rules" } as never };
    const withReading = view([done], noop);
    expect(withReading).toContain("data-chart-reading");
    expect(withReading).toMatch(/<button[^>]*data-explain-words[^>]*>Explain in words<\/button>/);
  });

  it("CR-3 in the thread: a user gets the plain line, the admin the owner action and the steps", () => {
    const user = render(<ChartTurnV2 chart={{ status: "unavailable", engine: { installed: false, message: "Analysis engine not installed" } }} />);
    expect(user).toContain('data-audience="user"');
    expect(text(user)).toContain("Chart upload analysis is being switched on");
    expect(user).not.toMatch(/owner action|private package|ATP_PRIVATE_REF|deploy key|\.ssh|\.env/i);
    const admin = render(<ChartTurnV2 chart={{ status: "unavailable", engine: { installed: false, reason: "the private package is not installed on this server",
      owner_steps: "The owner adds the read-only deploy key and ATP_PRIVATE_REF, then runs the deploy." } }} />);
    expect(admin).toContain('data-audience="admin"');
    expect(text(admin)).toContain("Chart analysis engine not installed — owner action");
    expect(text(admin)).toContain("ATP_PRIVATE_REF");
    const failed = render(<ChartTurnV2 chart={{ status: "failed", message: "Please upload a screenshot instead" }} />);
    expect(failed).toContain('role="alert"');
    expect(text(failed)).toContain("Please upload a screenshot instead");
  });

  it("uses the same chart_analyze pipeline as the page, Image only, and never an order call", () => {
    expect(chatSource).toContain('chartAnalyzeApi.add(source.kind === "file" ? { file: source.file, fileName: source.name } : { link: source.link }, "image_only")');
    expect(chatSource).toContain("chartAnalyzeApi.analyze(item.id)");
    expect(chatSource).not.toMatch(/placeOrder|exitNow|cancelOrder/);
    // M59 CR-2: after the analysis only the rule answer is asked (no AI wording); the AI words only on the user's tap
    const flow = chatSource.slice(chatSource.indexOf("async function attachChart"), chatSource.indexOf("return (\n    <>\n      <PageIntro"));
    expect(flow).toContain("chatApi.askRules(question)");
    expect(flow).not.toMatch(/chatApi\.ask\(/);
  });
});
