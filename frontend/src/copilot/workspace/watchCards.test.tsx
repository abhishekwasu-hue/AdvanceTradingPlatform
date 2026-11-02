import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Annotations } from "./annotations";
import source from "./WatchCardsV2.tsx?raw";
import rulesSource from "./watchCards.ts?raw";
import notificationsSource from "../../pages/NotificationsPage.tsx?raw";
import workspaceSource from "./ChartWorkspaceV2.tsx?raw";
import { addArea, areaText, areaToWatch, blockedReason, KIND_ROWS, MAX_WATCHED, numberOf, type WatchApi, type WatchPrefs } from "./watchCards";
import { WatchCardsV2 } from "./WatchCardsV2";

/** M76 MP-11 (D-1): watch cards - opt-in, his own numbers, information only, "Open here" in place. */
const OFF: WatchPrefs = {
  telegram: false, area_entry: false, area_rejection: false, strike_points: false, strike_points_n: null, iv_spike: false, iv_spike_points: null,
  margin_use: false, margin_use_pct: null, cut_off_reminder: false, cut_off_minutes: null, areas: [],
};
const AREA: Annotations["areas"][number] = {
  role: "demand", side: "buyer", low: 24480, high: 24560.5, low_text: "24,480", high_text: "24,560.50", timeframe: "1h", state: "fresh",
  state_words: "untouched since it formed", tests: 0, words: "Buyer area.",
};
const row = (k: string) => KIND_ROWS.find((r) => r.kind === k)!;

describe("watch card rules", () => {
  it("every type is off and every number Not set by default; a number is his, in range", () => {
    expect(KIND_ROWS.map((r) => r.kind)).toEqual(["area_entry", "area_rejection", "strike_points", "iv_spike", "margin_use", "cut_off_reminder"]);
    expect(numberOf(row("strike_points"), "")).toBeNull();
    expect(numberOf(row("strike_points"), "75")).toBe(75);
    expect(numberOf(row("margin_use"), "120")).toBeNull();                  // above 100 %
    expect(numberOf(row("margin_use"), "-5")).toBeNull();
    expect(numberOf(row("cut_off_reminder"), "7.5")).toBeNull();            // whole minutes
    expect(numberOf(row("cut_off_reminder"), "15")).toBe(15);
  });

  it("a type with a number cannot be on without his number; the reminder needs his own square-off time", () => {
    expect(blockedReason(OFF, row("area_entry"), null)).toBeNull();
    expect(blockedReason(OFF, row("iv_spike"), null)).toBe("Set your number first.");
    expect(blockedReason({ ...OFF, cut_off_minutes: 15 }, row("cut_off_reminder"), null)).toContain("square-off time");
    expect(blockedReason({ ...OFF, cut_off_minutes: 15 }, row("cut_off_reminder"), "15:10")).toBeNull();
  });

  it("an area of the open chart becomes a watched area with the chart's own timeframe; once each, at most the limit", () => {
    const a = areaToWatch(AREA, "NSE:NIFTY", "15m")!;
    expect(a).toEqual({ id: "nifty-15m-buyer-2448000-2456050", exchange: "NSE", symbol: "NIFTY", tf: "15m", side: "buyer", low: 24480, high: 24560.5 });
    expect(areaToWatch(AREA, "NSE:NIFTY", "1w")).toBeNull();
    expect(areaToWatch(AREA, "bad", "15m")).toBeNull();
    expect(addArea([a], a)).toBeNull();
    expect(addArea(Array.from({ length: MAX_WATCHED }, (_x, i) => ({ ...a, id: `x${i}` })), a)).toBeNull();
    expect(addArea([], a)).toEqual([a]);
    expect(areaText(a, (n) => String(n))).toBe("Buyer area 24480 - 24560.5 · 15m · NIFTY");
  });
});

describe("the watch panel and Open here", () => {
  const api: WatchApi = { read: () => new Promise(() => undefined), save: () => Promise.reject(new Error("not in this test")) };
  it("renders nothing until his choices are read; no order path, no call words", () => {
    expect(renderToStaticMarkup(<WatchCardsV2 instrument="NSE:NIFTY" tf="15m" annotations={null} api={api} />)).toBe("");
    for (const s of [source, rulesSource]) {
      expect(s).not.toMatch(/\/order|placeOrder|exit-now|basket\/execute|fno\/order|resting/);
      expect(s).not.toMatch(/(?<!\.)\b(buy|sell|target|stop-loss)\b/i);       // (an input event's `.target` is not a word)
    }
  });
  it("a card in Notifications opens the workspace in place, and the workspace shows the panel on an underlying's chart", () => {
    expect(notificationsSource).toContain('<InPlaceLink to={copilotToolPath("read", n.open_here.symbol)}');
    expect(workspaceSource).toContain("<WatchCardsV2 instrument={key?.instrument ?? null} tf={key?.tf ?? null} annotations={annotations} />");
  });
});
