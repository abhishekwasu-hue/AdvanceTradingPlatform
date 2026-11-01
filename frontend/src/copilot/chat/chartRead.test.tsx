import { renderToStaticMarkup } from "react-dom/server";
import { I18nextProvider } from "react-i18next";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { fixtureHybrid, fixtureItem } from "../../chartAnalyze/fixtures";
import { copilotI18n } from "../i18n";
import { AnswerV2 } from "./AnswerV2";
import { ChartTurnV2 } from "./ChartTurnV2";
import chatSource from "./CopilotChatV2.tsx?raw";
import { hybridPick, type ChatAnswer, type StructureCard } from "./model";

/**
 * M72 PR-5 (CP-4): the chart reading in the Copilot - an attached chart is read in "Advanced analysis" only on the symbol
 * and timeframe the page's own context names (never guessed from the picture), the thread shows ATP's annotated reading
 * beside the picture without the page-only buttons, and a planner answer that read a chart keeps its checked headline
 * line above the code-drawn card. Made-up data.
 */
const render = (node: React.ReactNode) => renderToStaticMarkup(<I18nextProvider i18n={copilotI18n}><MemoryRouter>{node}</MemoryRouter></I18nextProvider>);

describe("M72 PR-5: the hybrid pick comes from the page's context only", () => {
  it("maps the context's timeframe words; anything else is AI only", () => {
    expect(hybridPick({ symbol: "nifty", timeframe: "15min" })).toEqual({ symbol: "NIFTY", timeframe: "15m" });
    expect(hybridPick({ symbol: "RELIANCE", timeframe: "day" })).toEqual({ symbol: "RELIANCE", timeframe: "1d" });
    expect(hybridPick({ symbol: "RELIANCE", timeframe: "60min" })).toEqual({ symbol: "RELIANCE", timeframe: "1h" });
    expect(hybridPick({ symbol: "RELIANCE" })).toBeNull();
    expect(hybridPick({ timeframe: "15m" })).toBeNull();
    expect(hybridPick({ symbol: "RELIANCE", timeframe: "week" })).toBeNull();
    expect(hybridPick(null)).toBeNull();
  });

  it("the chat sends hybrid only when it is on and the pick exists", () => {
    expect(chatSource).toContain('status.modes.hybrid ? hybridPick(about) : null');
    expect(chatSource).toContain('chartAnalyzeApi.add(added, "hybrid", pick)');
    expect(chatSource).toContain("chartAnalyzeApi.readingPng(done.id)");
  });
});

describe("M72 PR-5: a hybrid chart in the thread", () => {
  it("shows ATP's annotated reading beside the picture, without Download / Delete", () => {
    const item = { ...fixtureItem(7, null), mode: "hybrid" as const, result: null, hybrid: fixtureHybrid(false) };
    const html = render(<ChartTurnV2 chart={{ status: "done", item, imageSrc: "blob:mine", readingSrc: "blob:atp" }} />);
    expect(html).toContain('data-testid="hybrid-result"');
    expect(html).toContain('src="blob:atp"');
    expect(html).toContain('src="blob:mine"');
    expect(html).not.toContain("Download image");
    expect(html).not.toContain(">Delete<");
  });
});

describe("M72 PR-5: a planner chart answer", () => {
  const card: StructureCard = {
    symbol: "EEESTOCK", price: 101.5, timeframe: "Daily",
    timeframes: [{ tf: "1d", label: "Daily", ready: true, trend: "up", weak: false, protected: 98, last_break: null }],
    areas_above: [], areas_below: [{ side: "demand", from: 99, to: 100, where: "below", distance: 1.5 }], swing_levels: [], watch: null,
  };
  const answer = {
    job: "agent", job_number: null, title: "Answer", summary: ["EEESTOCK daily: up, buyer area 99 - 100", "The structure is up."], answer: "",
    sections: [], blocks: [], unknowns: [], worked: [], action: null, refused: null, starters: [], concepts: [], notes: [],
    disclaimer: "Information and education only, not advice.", source: "ai", tool_calls: [], structure_card: card,
    headline_line: "EEESTOCK daily: up, buyer area 99 - 100",
  } as unknown as ChatAnswer;
  it("keeps the checked headline line above the code-drawn card", () => {
    const html = render(<AnswerV2 answer={answer} />);
    const line = html.indexOf("data-headline-line");
    expect(line).toBeGreaterThan(-1);
    expect(html.indexOf("99")).toBeGreaterThan(line);
    expect(html).not.toMatch(/\b(buy|sell|target|stop)\b/i);
  });
});
