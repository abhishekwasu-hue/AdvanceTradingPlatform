import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import consoleSource from "./AdminConsoleV2.tsx?raw";
import { GrowthFunnelView, ValueFunnelView, spanText } from "./AdminSectionsV2";
import { learnBody } from "./growthSettingsApi";
import apiSource from "./growthSettingsApi.ts?raw";
import sectionsSource from "./AdminSectionsV2.tsx?raw";

/** Readiness C13: the growth funnel in Admin › Users & business and the operator's growth settings. */

describe("the funnel", () => {
  const funnel = {
    steps: [
      { key: "signed_up" as const, label: "Signed up", count: 40 },
      { key: "broker" as const, label: "Connected a broker", count: 20 },
      { key: "paper_bot" as const, label: "Started a trading bot", count: 10 },
      { key: "active_week1" as const, label: "Active in week 1", count: 5 },
    ],
    week1_open: 3,
  };

  it("shows each step in order with its share of the step before", () => {
    const html = renderToStaticMarkup(<GrowthFunnelView funnel={funnel} />);
    const order = funnel.steps.map((s) => html.indexOf(`data-funnel-step="${s.key}"`));
    expect(order).toEqual([...order].sort((a, b) => a - b));
    expect(html).toContain("50% of the step before");
    expect(html).toContain("3 more people are still in their first week.");
    expect(html).toContain("width:25%");
  });

  it("an empty period divides by nothing", () => {
    const html = renderToStaticMarkup(<GrowthFunnelView funnel={{ steps: funnel.steps.map((s) => ({ ...s, count: 0 })), week1_open: 0 }} />);
    expect(html).toContain("0% of the step before");
    expect(html).not.toContain("still in their first week");
  });
});

/** AUD-G2.4: time to first value - the five activation steps and the first-session share. */
describe("time to first value", () => {
  const funnel = {
    steps: [
      { key: "signup" as const, label: "Signed up", count: 20, median_hours: 0 },
      { key: "broker_connect" as const, label: "Connected a broker", count: 10, median_hours: 0.5 },
      { key: "first_chart" as const, label: "Opened a first chart", count: 8, median_hours: 1.5 },
      { key: "first_paper_trade" as const, label: "First paper trade", count: 5, median_hours: 72 },
      { key: "first_journal_review" as const, label: "First journal review", count: 0, median_hours: null },
    ],
    first_session: { hours: 3, decided: 8, within: 2, open: 4, share_pct: 25 },
  };

  it("shows the steps in order with share of sign-ups, the median time and the first-session share", () => {
    const html = renderToStaticMarkup(<ValueFunnelView funnel={funnel} />);
    const order = funnel.steps.map((s) => html.indexOf(`data-value-step="${s.key}"`));
    expect(order.every((i) => i >= 0)).toBe(true);
    expect(order).toEqual([...order].sort((a, b) => a - b));
    expect(html).toContain("50% of sign-ups · median 30 minutes");
    expect(html).toContain("40% of sign-ups · median 1.5 hours");
    expect(html).toContain("25% of sign-ups · median 3 days");
    expect(html).toContain("0% of sign-ups · median -");
    expect(html).toContain("First paper trade within the first session (3 hours after signing up)");
    expect(html).toContain("<strong>25%</strong>");
    expect(html).toContain("(2 of 8; 4 still in their first session)");
    expect(html).toContain("width:50%");
  });

  it("an empty period shows no share and divides by nothing", () => {
    const empty = { steps: funnel.steps.map((s) => ({ ...s, count: 0, median_hours: null })),
      first_session: { hours: 1, decided: 0, within: 0, open: 0, share_pct: null } };
    const html = renderToStaticMarkup(<ValueFunnelView funnel={empty} />);
    expect(html).toContain("(1 hour after signing up)");
    expect(html).toContain("<strong>-</strong>");
    expect(html).not.toContain("still in their first session");
    expect(html).not.toMatch(/NaN|Infinity/);
  });

  it("spells a span the way a reader says it", () => {
    expect(spanText(null)).toBe("-");
    expect(spanText(0.25)).toBe("15 minutes");
    expect(spanText(12)).toBe("12 hours");
    expect(spanText(96)).toBe("4 days");
  });

  it("the activation panel shows it when the server sends it, and reads only", () => {
    expect(sectionsSource).toContain("{data.value_funnel && <ValueFunnelView funnel={data.value_funnel} />}");
    expect(sectionsSource).not.toMatch(/value_funnel[^\n]*method:/);
  });
});

describe("the settings", () => {
  it("only filled links are sent", () => {
    expect(learnBody({ a: " https://x.example ", b: "", c: "  " }, " https://course.example ")).toEqual({
      videos: { a: "https://x.example" }, course_url: "https://course.example",
    });
  });

  it("the writes live in their own module; the console stays free of write calls", () => {
    expect(apiSource).toContain('method: "PUT"');
    expect(consoleSource).not.toMatch(/method:/);
    expect(consoleSource).toContain("<GrowthSettingsPanelV2 />");
  });
});
