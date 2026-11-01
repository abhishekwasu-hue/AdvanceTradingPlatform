import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AiPlatformLimitsView } from "../admin/AiPlatformLimitsCard";
import limitsSource from "../admin/AiPlatformLimitsCard.tsx?raw";
import { PlatformAiUsageView } from "./PlatformAiUsageV2";
import usageSource from "./PlatformAiUsageV2.tsx?raw";
import { parseRupees, usedPct, type PlatformLimits, type PlatformUsage } from "./platformAiApi";
import apiSource from "./platformAiApi.ts?raw";

/** M72 PR-1b (M72Q-1): the platform AI key - his own use today in Settings › AI, and the owner's limits in Admin. */

const usage = (over: Partial<PlatformUsage> = {}): PlatformUsage => ({
  in_use: true, available: true, reason: null, message: null, today_inr: 4.5, cap_inr: 10, operator_cap_inr: 10, own_cap_inr: null, ...over,
});
const limits = (over: Partial<PlatformLimits> = {}): PlatformLimits => ({
  plan_budget_inr: { free: null, pro: null, business: null }, user_daily_cap_inr: null, key_configured: true, provider: "anthropic",
  plans: [{ id: "free", name: "Free" }, { id: "pro", name: "Pro" }, { id: "business", name: "Business" }], ...over,
});
const noop = () => undefined;

describe("platform AI helpers", () => {
  it("reads rupees: empty = Not set, a bad value = invalid", () => {
    expect(parseRupees("")).toBeNull();
    expect(parseRupees(" ₹1,500 ")).toBe(1500);
    expect(parseRupees("0")).toBe(0);
    expect(parseRupees("-1")).toBeUndefined();
    expect(parseRupees("abc")).toBeUndefined();
  });
  it("works out the share of today's limit used", () => {
    expect(usedPct(5, 10)).toBe(50);
    expect(usedPct(15, 10)).toBe(100);
    expect(usedPct(5, null)).toBeNull();
    expect(usedPct(5, 0)).toBeNull();
  });
});

describe("Settings › AI: AI use today", () => {
  it("shows today's use against the limit, numbers first", () => {
    const html = renderToStaticMarkup(<PlatformAiUsageView usage={usage()} onSave={noop} />);
    expect(html).toContain("₹4.50");
    expect(html).toContain("of ₹10.00 today");
    expect(html).toContain('aria-valuenow="45"');
    expect(html).toContain('placeholder="Not set"');
    expect(html).not.toContain("data-clear-cap");
  });
  it("says why at the limit and offers to clear his own limit", () => {
    const html = renderToStaticMarkup(<PlatformAiUsageView usage={usage({ today_inr: 10, available: false, reason: "daily_cap",
      message: "You reached your AI limit for today.", own_cap_inr: 5, cap_inr: 5 })} onSave={noop} />);
    expect(html).toContain("You reached your AI limit for today.");
    expect(html).toContain("bg-down");
    expect(html).toContain("data-clear-cap");
  });
  it("with no limit set says so and shows no bar", () => {
    const html = renderToStaticMarkup(<PlatformAiUsageView usage={usage({ cap_inr: null, operator_cap_inr: null })} onSave={noop} />);
    expect(html).toContain("not set yet");
    expect(html).not.toContain("progressbar");
  });
});

describe("Admin › Limits: platform AI key", () => {
  it("every field starts Not set and the key is never shown", () => {
    const html = renderToStaticMarkup(<AiPlatformLimitsView limits={limits()} onSave={noop} />);
    expect(html).toContain("Free: budget a month (₹)");
    expect(html).toContain("Daily cap per user (₹)");
    expect((html.match(/placeholder="Not set"/g) ?? []).length).toBe(4);
    expect(html).toContain("Ready · Anthropic");
    expect(renderToStaticMarkup(<AiPlatformLimitsView limits={limits({ key_configured: false, provider: null })} onSave={noop} />))
      .toContain("Not set");
  });
  it("shows the owner's saved values", () => {
    const html = renderToStaticMarkup(<AiPlatformLimitsView limits={limits({ plan_budget_inr: { free: 0, pro: 1500, business: null },
      user_daily_cap_inr: 25 })} onSave={noop} />);
    expect(html).toContain('value="1500"');
    expect(html).toContain('value="25"');
  });
});

describe("guards", () => {
  it("no pre-chosen number, no order call, no navigation", () => {
    for (const src of [usageSource, limitsSource, apiSource]) {
      expect(src).not.toMatch(/useNavigate|<Link |window\.location|href=/);
      expect(src).not.toMatch(/\/orders|placeOrder|\/execution/);
    }
    // No limit value is chosen in the frontend: defaults come only from the server (null = Not set).
    expect(limitsSource).not.toMatch(/useState\(\s*\d/);
    expect(usageSource).not.toMatch(/useState\(\s*\d/);
  });
});
