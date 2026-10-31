import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import journalSource from "../pages/JournalPage.tsx?raw";
import signalSource from "../signals/SignalPartsV2.tsx?raw";
import SimilarSetupsV2, { RecallBody } from "./SimilarSetupsV2";
import { contextWords, journalLink, journalTarget, rWords, recallSummary, type Recall, type Reflection } from "./recall";

/** AUD-T6 (owner audit 28 Oct; BR-M3 / BR-R1): similar-setup recall and what the context said vs what happened. */
const recall: Recall = {
  context: { setup: "s1", side: "LONG", holding: "INTRADAY", timeframe: "15min" }, fields: ["setup", "side", "holding", "timeframe"],
  as_of: "2026-10-01T04:00:00Z", count: 2, min_sample: 10, enough: false, line: "Not enough history yet: 2 of 10 similar closed trades.",
  wins: null, losses: null, flat: null, avg_r: null, r_trades: 0, common_loss_exit: null,
  matches: [
    { trade_id: 12, entered_at: "2026-09-29T04:00:00Z", closed_at: "2026-09-29T06:00:00Z", journal_week: "2026-W40", mode: "PAPER", r: 1.2,
      result: "gain", outcome: "+1.20 R, a gain", how_closed: "Closed at your exit-at-profit level" },
    { trade_id: 9, entered_at: "2026-09-22T04:00:00Z", closed_at: "2026-09-22T06:00:00Z", journal_week: "2026-W39", mode: "PAPER", r: -1,
      result: "loss", outcome: "-1.00 R, a loss", how_closed: "Closed at your exit-if-wrong level" },
  ],
};
const reflection: Reflection = {
  closed: true,
  said: { setup: "s1", side: "LONG", holding: "INTRADAY", timeframe: "15min", planned_rr: 2, source: "PROPOSAL", captured_at: "2026-10-01T04:00:00Z",
          reasons: ["Pullback into the buyer area", "Buy above 2,450"] },
  happened: { how_closed: "Closed at your exit-at-profit level", r: 1.8, result: "gain", outcome: "+1.80 R, a gain", mfe_r: 2.1, mae_r: 0.4 },
  line: "Entered on the long side of the 15min chart, planned reward to risk 2; closed at your exit-at-profit level, +1.80 R, a gain.",
};

describe("AUD-T6 similar setups", () => {
  it("the summary shows the aggregate only from the minimum sample", () => {
    expect(recallSummary(recall)).toBe("2 similar past trades · not enough history yet (2 of 10)");
    expect(recallSummary({ ...recall, count: 0, matches: [] })).toBe("No similar closed trades of yours yet");
    expect(recallSummary({ ...recall, count: 12, enough: true, wins: 7, losses: 5, avg_r: 0.35 }))
      .toBe("12 similar past trades: 7 with a gain, 5 with a loss · average +0.35 R");
    expect(rWords(-0.5)).toBe("−0.50 R");
    expect(contextWords(recall.context)).toBe("Long · Intraday · 15min chart");
    expect(contextWords({ ...recall.context, timeframe: null })).toBe("Long · Intraday");
  });

  it("links each match to its journal row, and the journal reads the address back", () => {
    expect(journalLink(recall.matches[0])).toBe("/positions/results/journal?week=2026-W40#trade-12");
    expect(journalTarget("?week=2026-W40", "#trade-12")).toEqual({ week: "2026-W40", tradeId: 12 });
    expect(journalTarget("?week=bad", "#x")).toEqual({ week: null, tradeId: null });
  });

  it("the body lists date, outcome and link, and what the context said vs what happened; no call words", () => {
    const html = renderToStaticMarkup(<MemoryRouter><RecallBody recall={recall} reflection={reflection} /></MemoryRouter>);
    expect(html).toContain('data-recall-match="12"');
    expect(html).toContain("29 Sep 2026");
    expect(html).toContain("+1.20 R, a gain");
    expect(html).toContain('href="/positions/results/journal?week=2026-W40#trade-12"');
    expect(html).toContain("What the context said vs what happened");
    expect(html).toContain("Pullback into the buyer area");
    expect(html).not.toContain("Buy above");                             // a signal reason with a call word is left out
    expect(html).toContain("most in your favour 2.1 R, most against you 0.4 R");
    expect(html).not.toMatch(/\b(buy|sell|target|stop-loss|stop loss)\b/i);
    const inJournal = renderToStaticMarkup(<RecallBody recall={recall} reflection={null} onOpenTrade={() => undefined} />);
    expect(inJournal).not.toContain("href=");
    expect(inJournal).not.toContain("data-recall-reflection");
  });

  it("shows nothing until the server answers", () => {
    expect(renderToStaticMarkup(<MemoryRouter><SimilarSetupsV2 kind="proposal" id={3} /></MemoryRouter>)).toBe("");
  });

  it("sits on the journal row and the approval card behind ui_v2 + setup_recall", () => {
    expect(journalSource).toMatch(/const recallOn = features\.on\("ui_v2"\) && features\.on\("setup_recall"\)/);
    expect(journalSource).toContain('<SimilarSetupsV2 kind="trade" id={e.trade_id}');
    expect(signalSource).toMatch(/item\.action_id !== null && features\.on\("ui_v2"\) && features\.on\("setup_recall"\)/);
    expect(signalSource).toContain('<SimilarSetupsV2 kind="proposal" id={item.action_id} />');
  });
});
