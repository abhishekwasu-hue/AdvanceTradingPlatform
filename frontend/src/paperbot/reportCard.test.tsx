import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { cardSvgOf } from "../growth/model";
import pageSource from "./PaperBotV2.tsx?raw";
import { ReportCardBodyV2, ReportRowV2 } from "./ReportCardV2";
import { capText, periodText, rBasis, rText, rowName, shareCard, shareText, winText, type PaperReportCard, type ReportRow } from "./reportCard";

/** AUD-T4 (owner audit 28 Oct): the paper report card - his own closed paper trades per bot and per strategy. */
const row: ReportRow = {
  key: "41", kind: "bot", name: "Supertrend flip", strategy_id: "supertrend_flip", symbol: "RELIANCE",
  closed: 4, wins: 2, win_pct: 50, avg_r: 1.1667, r_trades: 3, no_stop_trades: 1, net_pnl: 12345.5, max_drawdown: 2450,
  capture_pct: 19.5, credit_trades: 2, plan_pct: 66.7, plan_trades: 3, plan_flagged: 1,
  first_close: "2026-10-01T04:30:00Z", last_close: "2026-10-28T09:00:00Z",
};
const data: PaperReportCard = {
  bots: [row], strategies: [{ ...row, key: "s1", kind: "strategy", symbol: null }],
  total: row, plan_note: null,
  calibration: { shown: false, brier: null, trades: 0, min_trades: 20, note: "No confidence is stored with your trades, so there is nothing to check its calibration against." },
};

describe("AUD-T4 paper report card", () => {
  it("formats the figures plainly", () => {
    expect(winText(62.5)).toBe("63%");
    expect(rText(1.1667)).toBe("+1.17R");
    expect(rText(-0.5)).toBe("-0.50R");
    expect(rText(null)).toBe("–");
    expect(capText(-12)).toContain("12%");
    expect(rBasis(row)).toBe("3 of 4 trades");
    expect(rBasis({ ...row, no_stop_trades: 0, r_trades: 4 })).toBeNull();
    expect(periodText(row)).toBe("1 Oct 2026 - 28 Oct 2026");
  });

  it("names a row by the shared rule, never by an id", () => {
    expect(rowName({ ...row, strategy_id: "custom:12", name: "My range plan" })).toBe("My range plan");
    expect(rowName({ ...row, strategy_id: "custom:12", name: "custom:12" })).toBe("My strategy");
    expect(rowName({ ...row, strategy_id: "template:D2", name: "template:D2" }, new Map([["template:D2", "Range breakout"]]))).toBe("Range breakout");
    expect(rowName({ ...row, strategy_id: null, name: "Trades without a bot" })).toBe("Trades without a bot");
  });

  it("a row shows the numbers; credit kept and plan followed only when they apply", () => {
    const html = renderToStaticMarkup(<ReportRowV2 row={row} onShare={() => undefined} />);
    for (const k of ["closed", "win", "r", "drawdown", "capture", "plan"]) expect(html).toContain(`data-stat="${k}"`);
    expect(html).toContain("3 of 4 trades");
    expect(html).toContain("data-report-share");
    const plain = renderToStaticMarkup(<ReportRowV2 row={{ ...row, credit_trades: 0, capture_pct: null, plan_pct: null }} />);
    expect(plain).not.toContain('data-stat="capture"');
    expect(plain).not.toContain('data-stat="plan"');
    expect(plain).not.toContain("data-report-share");
  });

  it("explanations live in the one Details, with the calibration sentence; no call words", () => {
    const html = renderToStaticMarkup(<ReportCardBodyV2 data={data} view="bot" onView={() => undefined} />);
    expect(html).toContain('data-details="paperbot.report-card"');
    expect(html).toContain("nothing to check its calibration");
    expect(html).toContain("not a backtest");
    expect(html).toContain('aria-pressed="true"');
    expect(html).not.toMatch(/\b(buy|sell|target|stop-loss|stop loss)\b/i);
    const empty = renderToStaticMarkup(<ReportCardBodyV2 data={{ ...data, bots: [], strategies: [], total: { ...row, closed: 0 } }} view="bot" onView={() => undefined} />);
    expect(empty).toContain("No closed paper trades yet");
  });

  it("the share image carries no name, symbol, id or amount", () => {
    const card = shareCard(row);
    const svg = cardSvgOf(card);
    const text = shareText(card);
    for (const out of [svg, text, JSON.stringify(card)]) {
      expect(out).not.toContain("RELIANCE");
      expect(out).not.toContain("Supertrend");
      expect(out).not.toMatch(/₹|12345|2450|\bRs\b/);
    }
    expect(JSON.stringify(card)).not.toContain("41");     // the bot's id
    expect(text).not.toContain("41");
    expect(card.facts.map((f) => f.key)).toEqual(["closed", "win", "r", "capture"]);
    expect(text).toContain("Plan followed on 67% of journaled trades.");
    expect(text).toContain("not a forecast");
  });

  it("mounted under Your bots only behind the paper_report_card flag", () => {
    expect(pageSource).toContain('{features.on("paper_report_card") && <ReportCardV2 names={names} />}');
  });
});
