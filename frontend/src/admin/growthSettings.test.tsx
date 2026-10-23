import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import consoleSource from "./AdminConsoleV2.tsx?raw";
import { GrowthFunnelView } from "./AdminSectionsV2";
import { learnBody } from "./growthSettingsApi";
import apiSource from "./growthSettingsApi.ts?raw";

/** Readiness C13: the growth funnel in Admin › Users & business and the operator's growth settings. */

describe("the funnel", () => {
  const funnel = {
    steps: [
      { key: "signed_up" as const, label: "Signed up", count: 40 },
      { key: "broker" as const, label: "Connected a broker", count: 20 },
      { key: "paper_bot" as const, label: "Started a paper bot", count: 10 },
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
