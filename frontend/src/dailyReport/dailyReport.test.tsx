import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { parseSymbols, RunsTable } from "./DailyReportAdminCard";
import adminSource from "./DailyReportAdminCard.tsx?raw";
import cardSource from "./DailyReportCardV2.tsx?raw";
import { openedFromLink, ReportCardView } from "./DailyReportCardV2";
import sheetSource from "./DailyReportSheetV2.tsx?raw";
import { ReportBody } from "./DailyReportSheetV2";
import foldedSource from "../copilot/chat/CopilotFoldedV2.tsx?raw";
import { REPORT } from "./fixture";
import { sortRows } from "./model";
import type { ReportRun } from "./types";

/** M88-R: the Daily market report card, its in-place sheet and the admin card. */
describe("daily market report", () => {
  it("the card shows the NIFTY close, breadth, the conclusion and the information line", () => {
    const html = renderToStaticMarkup(<ReportCardView report={REPORT} onOpen={() => undefined} />);
    expect(html).toContain("NIFTY 50");
    expect(html).toContain("24,180.40");
    expect(html).toContain("A firm close with broad participation.");
    expect(html).toContain("Last close 6 Nov 2026 · AI-generated · information only, not advice");
    expect(renderToStaticMarkup(<ReportCardView report={null} onOpen={() => undefined} />)).toContain("first report is stored after the next market close");
  });

  it("every part of the full report carries the information line", () => {
    const html = renderToStaticMarkup(<ReportBody report={REPORT} onOpen={() => undefined} />);
    const parts = html.match(/data-report-part=/g) ?? [];
    const lines = html.match(/data-report-info/g) ?? [];
    expect(parts.length).toBe(7);
    expect(lines.length).toBe(parts.length);
    for (const s of REPORT.stocks) { expect(html).toContain(`data-report-row="${s.symbol}"`); expect(html).toContain(`data-report-card-row="${s.symbol}"`); }
    expect(html).toContain("No close stored for this day.");
  });

  it("sorts the stock table, a missing value last", () => {
    expect(sortRows(REPORT.stocks, "change_pct", true).map((r) => r.symbol)).toEqual(["TESTAAA", "TESTCCC", "TESTBBB", "TESTDDD"]);
    expect(sortRows(REPORT.stocks, "change_pct", false).map((r) => r.symbol)).toEqual(["TESTBBB", "TESTCCC", "TESTAAA", "TESTDDD"]);
    expect(sortRows(REPORT.stocks, "pcr", true)[0].symbol).toBe("TESTCCC");
    expect(sortRows(REPORT.stocks, "symbol", false).map((r) => r.symbol)).toEqual(["TESTAAA", "TESTBBB", "TESTCCC", "TESTDDD"]);
  });

  it("opens charts in place, never navigates away, and reads only the stored report", () => {
    expect(sheetSource).toContain("inPlace.open(chartTabPath(symbol))");
    for (const src of [cardSource, sheetSource, adminSource]) {
      expect(src).not.toMatch(/\bnavigate\(|<Link\b|window\.open|location\.href/);
      expect(src).not.toMatch(/text-(xs|sm|base|lg|xl)\b/);           // text sizes from the tokens only
    }
    expect(cardSource).toContain("reportApi.latest()");
    expect(foldedSource).toContain('import("../../dailyReport/DailyReportCardV2")');
    expect(openedFromLink("?report=open")).toBe(true);
    expect(openedFromLink("?tab=read")).toBe(false);
  });

  it("the admin card cleans the symbols and shows tokens, cost and why a run stored nothing", () => {
    expect(parseSymbols("nifty 50\nNIFTY  50 constituents, nifty 50\n\n")).toEqual(["NIFTY 50", "NIFTY 50 CONSTITUENTS"]);
    const run: ReportRun = { id: 1, trade_date: "2026-11-06", status: "failed", error: "The AI answer was refused twice: it used the words buy. Nothing was stored.",
      attempts: 1, ai_requests: 2, input_tokens: 6000, output_tokens: 1800, tokens: 7800, cost_usd: 0.05, cost_inr: 4.2, symbols: 51,
      trigger: "run_now", stored: false, next_try_at: null, updated_at: null };
    const html = renderToStaticMarkup(<RunsTable runs={[run]} />);
    expect(html).toContain("Not stored");
    expect(html).toContain("refused twice");
    expect(html).toContain("7,800");
    expect(html).toContain("Run now");
  });
});
