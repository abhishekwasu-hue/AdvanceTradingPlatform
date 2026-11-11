import { renderToStaticMarkup } from "react-dom/server";
import { I18nextProvider } from "react-i18next";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { copilotI18n } from "../i18n";
import { AnswerV2 } from "./AnswerV2";
import chatSource from "./CopilotChatV2.tsx?raw";
import { ChatView } from "./CopilotChatV2";
import { watchIstDate } from "./istDate";
import {
  cardTrendText, chartPickable, chartQuestionFor, CHART_SUGGESTIONS, keptIsCurrent, keptIsStale, msToNextIstMidnight,
  type AnswerHeadline, type ChatAnswer, type ChatHistory, type ChatHome, type StoredTurn,
} from "./model";

/**
 * COP-2 (owner 11 Nov, "Default yes"; "when the calendar date changes, the old information must go away automatically"):
 * A. the Copilot opens fresh when the kept conversation is from an earlier India date or its chart answer has a newer
 *    stored close (the server's `stale`), and clears the screen when the India date changes while it stays open;
 * B. "Read chart" opens the symbol picker; C. the headline says the structure card's trend word (weak included).
 */
const render = (node: React.ReactNode) => renderToStaticMarkup(
  <MemoryRouter><I18nextProvider i18n={copilotI18n}>{node}</I18nextProvider></MemoryRouter>);
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
const noop = () => undefined;

const HEAD: AnswerHeadline = { symbol: "NIFTY", price: 100, price_label: "Last close", as_of: "2026-11-10", trend: "up", timeframe: "Daily",
  area: null, chart: null };
const CHART: ChatAnswer = { job: "chart", summary: ["NIFTY closed."], sections: [], blocks: [], unknowns: [], worked: [], notes: [], refused: null,
  starters: [], tool_calls: [], disclaimer: "", headline: HEAD } as unknown as ChatAnswer;
const NOW = new Date("2026-11-11T04:30:00Z");                                   // 10:00 IST, 11 Nov
const turn = (at: string, answer: ChatAnswer | null, stale?: boolean): StoredTurn => ({ id: 1, question: "Read the NIFTY chart", answer, at, stale });
const kept = (...turns: StoredTurn[]): ChatHistory => ({ turns, keep: 20 });

describe("COP-2 A: an out-of-date conversation is not shown on open", () => {
  it("same day and no newer close: shown", () => {
    expect(keptIsCurrent(kept(turn("2026-11-11T03:50:00Z", CHART, false)), NOW)).toBe(true);
    expect(keptIsCurrent(kept(turn("2026-11-11T03:50:00Z", CHART)), NOW)).toBe(true);           // an older server: no field
  });
  it("same day but a newer close is stored: not shown", () => {
    expect(keptIsStale(kept(turn("2026-11-11T03:50:00Z", CHART, true)))).toBe(true);
    expect(keptIsCurrent(kept(turn("2026-11-11T03:50:00Z", CHART, true)), NOW)).toBe(false);
  });
  it("only the newest chart answer counts; a turn without a chart is never stale", () => {
    const plain = { ...CHART, job: "teacher", headline: null } as ChatAnswer;
    expect(keptIsCurrent(kept(turn("2026-11-11T03:00:00Z", CHART, true), turn("2026-11-11T03:50:00Z", CHART, false)), NOW)).toBe(true);
    expect(keptIsCurrent(kept(turn("2026-11-11T03:00:00Z", CHART, true), turn("2026-11-11T03:50:00Z", plain, false)), NOW)).toBe(false);
    expect(keptIsCurrent(kept(turn("2026-11-11T03:50:00Z", plain, false)), NOW)).toBe(true);
  });
  it("a new India date: not shown", () => {
    expect(keptIsCurrent(kept(turn("2026-11-10T17:00:00Z", CHART, false)), NOW)).toBe(false);   // 22:30 IST 10 Nov
  });
  it("on open the page uses the combined rule; opening from the list shows the conversation as it is", () => {
    expect(chatSource).toContain("!keptIsCurrent(h)");
    const open = chatSource.slice(chatSource.indexOf("async function openConversation"), chatSource.indexOf("return (", chatSource.indexOf("async function openConversation")));
    expect(open).toContain("setTurns(restored)");
    expect(open).not.toMatch(/keptIs(Current|Today|Stale)/);
  });
});

describe("COP-2 A: the screen clears when the India date changes while the page stays open", () => {
  afterEach(() => { vi.useRealTimers(); });

  const tab = () => {
    const doc = new EventTarget() as EventTarget & { visibilityState: string };
    doc.visibilityState = "visible";
    return { doc, target: new EventTarget() };
  };

  it("says how long until the next 00:00 IST", () => {
    expect(msToNextIstMidnight(new Date("2026-11-11T18:29:00Z"))).toBe(60_000);                   // 23:59 IST
    expect(msToNextIstMidnight(new Date("2026-11-11T18:30:00Z"))).toBe(86_400_000);               // 00:00 IST
  });

  it("the timer clears the screen at the IST midnight boundary, and again the next night", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-11T18:29:30Z"));                                          // 23:59:30 IST
    const cleared = vi.fn();
    const stop = watchIstDate(cleared, tab());
    vi.advanceTimersByTime(29_000);
    expect(cleared).not.toHaveBeenCalled();                                                      // still 11 Nov
    vi.advanceTimersByTime(2_000);
    expect(cleared).toHaveBeenCalledTimes(1);                                                    // 12 Nov
    vi.advanceTimersByTime(86_400_000);
    expect(cleared).toHaveBeenCalledTimes(2);                                                    // 13 Nov
    stop();
    vi.advanceTimersByTime(86_400_000);
    expect(cleared).toHaveBeenCalledTimes(2);
  });

  it("a tab coming back after midnight clears the screen; on the same day it keeps it", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-11T10:00:00Z"));                                          // 15:30 IST
    const cleared = vi.fn();
    const { doc, target } = tab();
    const stop = watchIstDate(cleared, { doc, target });
    vi.setSystemTime(new Date("2026-11-11T17:00:00Z"));                                          // 22:30 IST, same day
    doc.dispatchEvent(new Event("visibilitychange"));
    target.dispatchEvent(new Event("focus"));
    expect(cleared).not.toHaveBeenCalled();
    vi.setSystemTime(new Date("2026-11-12T02:00:00Z"));                                          // 07:30 IST next day (timer asleep)
    doc.visibilityState = "hidden";
    doc.dispatchEvent(new Event("visibilitychange"));
    expect(cleared).not.toHaveBeenCalled();                                                      // still hidden
    doc.visibilityState = "visible";
    doc.dispatchEvent(new Event("visibilitychange"));
    expect(cleared).toHaveBeenCalledTimes(1);
    target.dispatchEvent(new Event("focus"));
    expect(cleared).toHaveBeenCalledTimes(1);                                                    // once per date change
    stop();
  });

  it("the page starts a new chat at the date change (the conversation stays in the list)", () => {
    const hook = chatSource.slice(chatSource.indexOf("useEffect(() => watchIstDate("), chatSource.indexOf("}), []);", chatSource.indexOf("useEffect(() => watchIstDate(")));
    expect(hook).toContain("fresh.current = true");
    expect(hook).toContain("setTurns([])");
    expect(hook).not.toMatch(/drop|delete|forget/i);
  });
});

describe("COP-2 B: Read chart for any symbol", () => {
  it("asks for the picked symbol by the name the chart reader reads", () => {
    expect(chartQuestionFor("RELIANCE")).toBe("Read the RELIANCE chart");
    expect(chartQuestionFor("NIFTY 50")).toBe("Read the NIFTY chart");
    expect(chartQuestionFor("nifty bank")).toBe("Read the BANKNIFTY chart");
    expect(chartQuestionFor("NIFTY FIN SERVICE")).toBe("Read the FINNIFTY chart");
    expect(chartQuestionFor("SENSEX")).toBe("Read the SENSEX chart");
  });
  it("offers shares and the indices it reads, never another index", () => {
    expect(["RELIANCE", "M&M", "BAJAJ-AUTO", "NIFTY 50", "NIFTY BANK", "SENSEX"].every((s) => chartPickable({ symbol: s }))).toBe(true);
    expect(["NIFTY IT", "INDIA VIX", ""].some((s) => chartPickable({ symbol: s }))).toBe(false);
    expect(CHART_SUGGESTIONS.map((s) => s.symbol)).toEqual(["NIFTY"]);
  });
  it("the Read chart chip opens the picker instead of asking at once", () => {
    const home = { jobs: [{ id: "chart", number: 2, title: "Chart reader", line: "", starter: "Read the NIFTY chart", built: true }], keep: 20, ai: { ready: true },
      top_line: "Ask me anything", market_line: { line: null, reason: null, as_of: null } } as unknown as ChatHome;
    const html = render(<ChatView home={home} homeError={null} turns={[]} draft="" busy={false} placement="inline" onDraft={noop} onAsk={noop} />);
    expect(html).toContain('data-quick="chart"');
    expect(html).toMatch(/data-quick="chart"[^>]*aria-expanded="false"|aria-expanded="false"[^>]*data-quick="chart"/);
    expect(chatSource).toContain("onAsk(chartQuestionFor(p.symbol))");
    expect(chatSource).toContain("<SymbolPicker id={pickerId}");
    expect(chatSource).toContain("rowFilter={chartPickable}");
  });
});

describe("COP-2 C: the headline says the card's trend word", () => {
  const head = (h: Partial<AnswerHeadline>) => text(render(<AnswerV2 answer={{ ...CHART, headline: { ...HEAD, ...h } }} onAsk={noop} />));
  it("up weak shows as the card shows it", () => {
    expect(head({ trend: "up", weak: true })).toContain(cardTrendText({ trend: "up", weak: true }));
    expect(head({ trend: "up", weak: true })).toContain("▲ Up · weak");
    expect(head({ trend: "down", weak: true })).toContain("▼ Down · weak");
    expect(head({ trend: "down" })).toContain("▼ Down");
    expect(head({ trend: "sideways" })).toContain("Sideways");
  });
  it("Not known yet only when there is no direction", () => {
    expect(head({ trend: null })).toContain("Not known yet");
    expect(head({ trend: "up" })).not.toContain("Not known yet");
  });
});
