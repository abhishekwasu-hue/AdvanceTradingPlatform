import { renderToStaticMarkup } from "react-dom/server";
import { I18nextProvider } from "react-i18next";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import myDataSource from "../../settings/MyDataCardV2.tsx?raw";
import { CopilotConversationRow } from "../../settings/MyDataCardV2";
import { copilotI18n } from "../i18n";
import { BUILD_PATH, COPILOT_TABS, COPILOT_V2_TABS, FOLDED_TABS, isCopilotTab, isFoldedTab } from "../tabs";
import chatSource from "./CopilotChatV2.tsx?raw";
import foldedSource from "./CopilotFoldedV2.tsx?raw";
import pageSource from "./CopilotPageV2.tsx?raw";
import CopilotTabsV2 from "./CopilotTabsV2";
import { DEFAULT_KEEP, restoredTurns, withTurn, type ChatAnswer, type ChatHistory } from "./model";

/**
 * CP-UX CU4 (one Copilot: the tab bar and the folded older tabs), CU6 (the conversation kept across reloads, deleted
 * in Settings → My data). Made-up data.
 */
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
function render(node: React.ReactNode, at = "/copilot"): string {
  return renderToStaticMarkup(<I18nextProvider i18n={copilotI18n}><MemoryRouter initialEntries={[at]}>{node}</MemoryRouter></I18nextProvider>);
}

describe("CU4: one Copilot", () => {
  it("has five tabs - Ask, Brief, Build, Review, Signals - each a link with its own address", () => {
    const html = render(<CopilotTabsV2 active="ask" />);
    expect(text(html)).toBe("Ask Brief Build Review Signals");
    expect([...html.matchAll(/href="([^"]+)"/g)].map((m) => m[1])).toEqual(["/copilot", "/copilot/brief", "/strategies/build", "/copilot/review", "/copilot/signals"]);
    expect(html.match(/aria-current="page"/g)).toHaveLength(1);
    expect(html).toMatch(/<a[^>]*aria-current="page"[^>]*data-copilot-tab="ask"/);
    expect(html).toContain('aria-label="Copilot"');
  });

  it("every tab target is at least 44 px", () => {
    const html = render(<CopilotTabsV2 active="brief" />);
    const links = [...html.matchAll(/<a [^>]*class="([^"]+)"/g)].map((m) => m[1]);
    expect(links).toHaveLength(5);
    for (const cls of links) {
      expect(cls).toContain("min-h-11");
      expect(cls).not.toMatch(/md:min-h-(?:[0-9]|10)\b/);
    }
  });

  it("Build is a link to the one builder (Strategies › Build), never a second builder", () => {
    expect(BUILD_PATH).toBe("/strategies/build");
    expect(COPILOT_V2_TABS.find((t) => t.key === "build")?.to).toBe(BUILD_PATH);
    expect(isFoldedTab("build")).toBe(false);
    expect(foldedSource).not.toMatch(/StrategyLab|IdeaBuilder|StrategyBuilder/);
  });

  it("folds Market Pulse + News Radar into Brief, Coach into Review, Watchtower into Signals - the same components", () => {
    expect(FOLDED_TABS).toEqual(["brief", "review", "signals"]);
    for (const tab of ["MarketPulseTab", "NewsRadarTab", "CoachTab", "WatchtowerTab"]) expect(foldedSource).toContain(`import("../tabs/${tab}")`);
    expect(foldedSource).toMatch(/tab === "brief"[\s\S]*<MarketPulse \/>[\s\S]*<NewsRadar \/>/);
    expect(foldedSource).toContain('tab === "review" ? <Coach /> : <Watchtower />');
    expect(foldedSource.match(/data-disclaimer/g)).toHaveLength(1);
    expect(foldedSource).toContain("<FirstUseModal");
  });

  it("keeps the seven older tabs at their addresses (under Advanced) until the owner's ✅", () => {
    expect(COPILOT_TABS).toHaveLength(7);
    for (const tab of COPILOT_TABS) { expect(isCopilotTab(tab)).toBe(true); expect(isFoldedTab(tab)).toBe(false); }
    expect(pageSource).toContain("if (isCopilotTab(tab)) return <Suspense fallback={null}><LegacyCopilot /></Suspense>;");
    expect(chatSource).toContain("COPILOT_TABS.map((tab) =>");
    expect(pageSource).toContain("<CopilotTabsV2 active={folded ?? \"ask\"} />");
  });
});

describe("CU6: the conversation kept across reloads", () => {
  const answer = { job: "teacher", title: "Teacher", summary: ["RSI is a momentum indicator."] } as unknown as ChatAnswer;
  const history: ChatHistory = { keep: 3, turns: [
    { id: 41, question: "q1", answer, at: null }, { id: 42, question: "q2", answer: null, at: null },
    { id: 43, question: "q3", answer, at: null }, { id: 44, question: "q4", answer, at: null },
  ] };

  it("restores the stored turns oldest first, at most `keep`, and says plainly when one cannot be shown", () => {
    const turns = restoredTurns(history, "could not be shown");
    expect(turns.map((t) => t.question)).toEqual(["q2", "q3", "q4"]);
    expect(turns.map((t) => t.id)).toEqual([1, 2, 3]);
    expect(turns[0]).toMatchObject({ answer: null, error: "could not be shown" });
    expect(turns[1].answer).toBe(answer);
    expect(restoredTurns(null, "x")).toEqual([]);
  });

  it("keeps the server's number of turns on the page", () => {
    let turns = restoredTurns(history, "x");
    turns = withTurn(turns, { id: 4, question: "q5", answer: null, error: null }, history.keep);
    expect(turns.map((t) => t.question)).toEqual(["q3", "q4", "q5"]);
    expect(DEFAULT_KEEP).toBeGreaterThan(0);
    expect(chatSource).toContain("chatApi.history()");
    expect(chatSource).toContain("home?.keep ?? keep");
  });

  it("Settings → My data has 'Delete my Copilot conversation', asked once more before it deletes", () => {
    const html = render(<CopilotConversationRow busy={false} setBusy={() => undefined} />);
    expect(text(html)).toContain("Delete my Copilot conversation");
    expect(html).toContain("data-copilot-forget");
    expect(html).not.toContain("data-copilot-forget-confirm");
    expect(myDataSource).toContain('request<{ deleted: number }>("/ai/chat/history", { method: "DELETE" })');
    expect(myDataSource).toContain("{copilot && <CopilotConversationRow");
    expect(myDataSource).toContain("min-h-11");
  });
});
