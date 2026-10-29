import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { CostMeter, CostPeriod } from "../types";
import { EMPTY } from "../ui/format";
import { cardTextProblems } from "../ui/textBudget";
import { CostMeterView, sharePct, sourceLabel } from "./CostMeterV2";
import positionsSource from "./PositionsV2.tsx?raw";

/** AUD-T7: the cost-of-trading card - paper and live apart, numbers first, the method in Details. */

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const outside = (html: string) => html.replace(/<details[\s\S]*<\/details>/g, "");

function period(over: Partial<CostPeriod> = {}): CostPeriod {
  return { trades: 4, gross: 1000, charges: 250, net: 750, charges_share_of_gross: 0.25, charges_per_trade: 62.5, median_trade_share: 0.3,
    trades_with_gross_profit: 3, charges_took_whole_profit: 1, from_contract_notes: 0, estimated: 4, ...over };
}
const EMPTY_MONTH = period({ trades: 0, gross: 0, charges: 0, net: 0, charges_share_of_gross: null, charges_per_trade: null,
  median_trade_share: null, trades_with_gross_profit: 0, charges_took_whole_profit: 0, estimated: 0 });

const DATA: CostMeter = {
  year_start: "2026-04-01", month_start: "2026-10-01",
  modes: [
    { mode: "PAPER", year: period(), month: EMPTY_MONTH },
    { mode: "LIVE", year: period({ gross: -200, net: -260, charges: 60, charges_share_of_gross: null, from_contract_notes: 4, estimated: 0 }), month: period({ trades: 1 }) },
  ],
};

describe("Cost of trading", () => {
  it("shows paper and live as two separate blocks, never one total", () => {
    const html = renderToStaticMarkup(<CostMeterView data={DATA} />);
    expect(html).toContain('data-cost-mode="PAPER"');
    expect(html).toContain('data-cost-mode="LIVE"');
    expect(text(html)).toContain("Paper trades");
    expect(text(html)).toContain("Live trades");
    expect(text(outside(html))).toContain("25.0%");
  });

  it("a losing gross shows no share, and an empty month shows dashes", () => {
    const html = renderToStaticMarkup(<CostMeterView data={DATA} />);
    const live = html.split('data-cost-mode="LIVE"')[1];
    const liveShare = text(live.split('data-cost-row="share"')[1].split("</tr>")[0]).trim().split(" ");
    expect(liveShare.slice(-2)).toEqual([EMPTY, "25.0%"]);         // this year a gross loss: no share; this month a profit
    const paperMonthCharges = html.split('data-cost-mode="PAPER"')[1].split('data-cost-row="charges"')[1].split("</tr>")[0];
    expect(text(paperMonthCharges).trim().endsWith(EMPTY)).toBe(true);
  });

  it("labels where the charges come from", () => {
    expect(sourceLabel(period())).toBe("Estimated");
    expect(sourceLabel(period({ from_contract_notes: 4 }))).toBe("From contract notes");
    expect(sourceLabel(period({ from_contract_notes: 1 }))).toBe("1 of 4 from contract notes");
    expect(sharePct(null)).toBe(EMPTY);
    expect(sharePct(0.374)).toBe("37.4%");
  });

  it("no closed trade: one affirmative line, no figures", () => {
    const html = renderToStaticMarkup(<CostMeterView data={{ ...DATA, modes: [] }} />);
    expect(text(html)).toContain("Your charges show here once a trade closes this year.");
    expect(html).not.toContain("<table");
  });

  it("one short visible line; the method in Details", () => {
    const html = renderToStaticMarkup(<CostMeterView data={DATA} />);
    const lines = [...outside(html).matchAll(/<p[^>]*>([\s\S]*?)<\/p>/g)].map((m) => text(m[1]));
    expect(cardTextProblems([{ card: "Cost of trading", lines }])).toEqual([]);
    expect(text(html)).toContain("Gross P&L is before charges");
    expect(html).toContain('data-cost-whole="PAPER"');
    expect(html).not.toMatch(/\b(buy|sell|target|stop-loss)\b/i);
  });

  it("My trades shows the card only behind the cost_meter flag", () => {
    expect(positionsSource).toContain('features.on("cost_meter") && signedIn && <CostMeterV2 />');
  });
});
