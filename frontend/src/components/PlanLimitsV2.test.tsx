import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { PlanLimitsV2, planLimitRows, planTileLine } from "./PlanLimitsV2";

const FREE = {
  watchlists: 5, screen_symbols: 50, reports_per_day: 10, report_retention_days: 7, report_storage_mb: 50, report_schedules: 1,
  min_schedule_interval_min: 60, lab_strategies: 3, lab_cpu_seconds_per_day: 300,
};
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

describe("PlanLimitsV2 (AMW-1)", () => {
  it("lists every limit, with the caller's usage where a feature counts it", () => {
    const rows = planLimitRows(FREE, { watchlists: 5, reports_per_day: null, report_storage_mb: 12.5 });
    expect(rows.map((r) => r.key)).toEqual([
      "watchlists", "screen_symbols", "reports_per_day", "report_storage_mb", "report_retention_days", "report_schedules",
      "min_schedule_interval_min", "lab_strategies", "lab_cpu_seconds_per_day",
    ]);
    const by = Object.fromEntries(rows.map((r) => [r.key, r]));
    expect(by.watchlists).toMatchObject({ used: "5", limit: "5", full: true });
    expect(by.reports_per_day).toMatchObject({ used: null, full: false });
    expect(by.report_storage_mb).toMatchObject({ used: "12.5 MB", limit: "50 MB", full: false });
    expect(by.min_schedule_interval_min.limit).toBe("at least 60 min");
    expect(by.report_retention_days.used).toBeNull();
  });

  it("skips limits the server did not send and renders nothing without any", () => {
    expect(planLimitRows({ watchlists: 25 }).map((r) => r.key)).toEqual(["watchlists"]);
    expect(renderToStaticMarkup(<PlanLimitsV2 limits={{}} />)).toBe("");
  });

  it("renders usage against the limit and the plan tile summary", () => {
    const words = text(renderToStaticMarkup(<PlanLimitsV2 limits={FREE} usage={{ watchlists: 2 }} />));
    expect(words).toContain("Watchlists 2 of 5");
    expect(words).toContain("Symbols per screener run 50");
    expect(words).toContain("Shortest schedule interval at least 60 min");
    expect(planTileLine(FREE)).toBe("5 watchlists · 50 symbols/scan · 10 reports/day · 3 Lab strategies");
  });

  it("uses no Devanagari", () => {
    expect(/[ऀ-ॿ]/.test(text(renderToStaticMarkup(<PlanLimitsV2 limits={FREE} />)))).toBe(false);
  });
});
