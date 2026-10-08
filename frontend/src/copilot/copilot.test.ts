import { describe, expect, it } from "vitest";
import { ApiError } from "../api/errors";
import type { AiProviderConfig, CopilotReply, NewsFeedItem } from "../types";
import { friendlyError } from "./aiTask";
import { toneFor } from "./components/aiCoreTypes";
import { formatInr, formatTokens } from "./components/CostChip";
import { dialAngle } from "./components/RegimeDial";
import { sentimentAngle } from "./components/SentimentGauge3D";
import { MAX_TILT_DEG, tiltFor } from "./components/TiltCard";
import { estimateFor } from "./cost";
import { countNewHigh } from "./data";
import { copilotI18n } from "./i18n";
import { loadCopilotLanguage } from "./languagePrefs";
import { COPILOT_TABS, copilotPath, isCopilotTab, legacyCopilotPath, tabForAction } from "./tabs";
import { sourcesOf } from "./tabs/AskCopilotTab";
import { resultOf } from "./tabs/CoachTab";
import { isBacktestLine } from "./tabs/IdeaBuilderTab";
import { conditionText } from "./tabs/lab/DraftsPanel";
import { cumulative } from "./tabs/lab/EquityCurve";
import { sessionize } from "./tabs/StrategyLabTab";
import { severityTone } from "./tabs/NewsRadarTab";

const t = copilotI18n.getFixedT("en", "copilot") as unknown as (key: string, o?: Record<string, unknown>) => string;

describe("tabs and addresses", () => {
  it("has the seven tabs, each with its own /copilot/<tab> address", () => {
    expect(COPILOT_TABS).toEqual(["market-pulse", "strategy-lab", "idea-builder", "ask", "watchtower", "news-radar", "coach"]);
    expect(copilotPath("news-radar")).toBe("/copilot/news-radar");
    expect(isCopilotTab("ask")).toBe(true);
    expect(isCopilotTab("study")).toBe(false);
  });

  it("forwards every old /ai-copilot address to the tab holding its content", () => {
    expect(legacyCopilotPath("study")).toBe("/copilot/strategy-lab");
    expect(legacyCopilotPath("today")).toBe("/copilot/market-pulse");
    expect(legacyCopilotPath("interview")).toBe("/copilot/idea-builder");
    expect(legacyCopilotPath("drafts")).toBe("/copilot/strategy-lab");
    expect(legacyCopilotPath("advanced")).toBe("/copilot/watchtower");
    expect(legacyCopilotPath(undefined)).toBe("/copilot/market-pulse");
    expect(legacyCopilotPath("coach")).toBe("/copilot/coach");
  });

  it("opens the tab the server's router names in an answer", () => {
    expect(tabForAction("strategy")).toBe("idea-builder");
    expect(tabForAction("today")).toBe("market-pulse");
    expect(tabForAction("coach")).toBe("coach");
    expect(tabForAction("guide")).toBe("ask");
    expect(tabForAction("anything-else")).toBe("market-pulse");
  });
});

describe("AI call states", () => {
  it("turns failures into sentences, never a raw error", () => {
    expect(friendlyError(new ApiError(500, "Internal", "", "<html>"), t)).toBe(t("states.server"));
    expect(friendlyError(new ApiError(402, "Payment"), t)).toBe(t("states.plan"));
    expect(friendlyError(new ApiError(409, "This draft was already approved."), t)).toBe("This draft was already approved.");
    expect(friendlyError(new DOMException("Aborted", "AbortError"), t)).toBe(t("states.cancelled"));
    expect(friendlyError(new TypeError("undefined is not a function"), t)).toBe(t("states.generic"));
    expect(friendlyError({ weird: true }, t)).toBe(t("states.generic"));
  });
});

describe("3D and motion pieces", () => {
  it("colours the AI Core by the regime", () => {
    expect(toneFor("TREND_UP")).toBe("bullish");
    expect(toneFor("BEARISH")).toBe("bearish");
    expect(toneFor("RANGE")).toBe("neutral");
    expect(toneFor(null)).toBe("neutral");
  });

  it("keeps the tilt at or under 6 degrees", () => {
    expect(MAX_TILT_DEG).toBeLessThanOrEqual(6);
    expect(tiltFor(300)).toBe(MAX_TILT_DEG);
    expect(tiltFor(1080)).toBeCloseTo(2);
  });

  it("maps the sentiment score onto the gauge and clamps it", () => {
    expect(sentimentAngle(0)).toBe(0);
    expect(sentimentAngle(100)).toBe(90);
    expect(sentimentAngle(-50)).toBe(-45);
    expect(sentimentAngle(400)).toBe(90);
  });

  it("points the regime dial at the day type and nowhere when it is unknown", () => {
    expect(dialAngle("TREND_DOWN")).toBe(-67.5);
    expect(dialAngle("TREND_UP")).toBe(67.5);
    expect(dialAngle("UNKNOWN")).toBeNull();
  });
});

describe("Copilot data helpers", () => {
  const item = (severity: number, at: string) => ({ classification: { severity }, published_at: at }) as unknown as NewsFeedItem;

  it("counts only new high-severity news for the badge", () => {
    const seen = new Date("2026-10-08T09:00:00Z").getTime();
    expect(countNewHigh([item(5, "2026-10-08T10:00:00Z"), item(4, "2026-10-08T09:30:00Z"), item(3, "2026-10-08T10:00:00Z"), item(5, "2026-10-08T08:00:00Z")], seen)).toBe(2);
  });

  it("says what each answer was built from", () => {
    const reply = { intent: "guide", used_market_memory: true, concepts: [{ id: "atr", title: "ATR" }] } as unknown as CopilotReply;
    expect(sourcesOf(reply).map((s) => s.kind)).toEqual(["guide", "memory", "concept", "rules"]);
    expect(sourcesOf({ intent: "deployments" } as CopilotReply).map((s) => s.kind)).toEqual(["brief", "deployments", "rules"]);
  });

  it("estimates the cost of a call only when an outside provider answers", () => {
    const cfg = {
      provider: "anthropic", enabled: true, configured: true,
      task_models: { anthropic: [{ task: "strategist", tier: "strong", est_inr_per_call: 4.2 }] },
      typical_call_tokens: { strong: { input: 6000, output: 4000 } },
    } as unknown as AiProviderConfig;
    expect(estimateFor(cfg, "strategist")).toEqual({ tokens: 10000, inr: 4.2 });
    expect(estimateFor({ ...cfg, provider: "rule_based" } as AiProviderConfig, "strategist")).toBeNull();
    expect(estimateFor(cfg, "unknown-task")).toBeNull();
    expect(formatTokens(10_000)).toBe("10k");
    expect(formatTokens(1_250)).toBe("1.3k");
    expect(formatInr(0.004)).toBe("<₹0.01");
    expect(formatInr(4.2)).toBe("₹4.20");
  });

  it("grades a thesis from the server's score", () => {
    expect(resultOf(1)).toBe("hit");
    expect(resultOf(-1)).toBe("miss");
    expect(resultOf(0)).toBe("flat");
  });

  it("blurs only the backtest lines of a template on sample data - the rules stay readable", () => {
    expect([0, 1, 2, 3, 4, 5].map(isBacktestLine)).toEqual([false, false, true, false, true, true]);
  });

  it("writes a rule condition in words", () => {
    const c = { left: { type: "indicator", indicator: "EMA", period: 20 }, operator: "CROSSES_ABOVE", right: { type: "indicator", indicator: "EMA", period: 50 } };
    expect(conditionText(c as never, t)).toBe("EMA(20) crosses above EMA(50)");
  });

  it("draws the equity curve from the trade results", () => {
    expect(cumulative([1, -0.5, 2])).toEqual([0, 1, 0.5, 2.5]);
  });

  it("lays sample minute bars into weekday NSE sessions", () => {
    const bars = Array.from({ length: 375 * 2 }, (_, i) => ({ timestamp: "", open: i, high: i, low: i, close: i, volume: 1 }));
    const out = sessionize(bars, new Date("2026-10-08T12:00:00Z"));
    expect(out[0].timestamp.endsWith("03:45:00.000Z")).toBe(true);      // 09:15 IST
    expect(new Set(out.map((b) => b.timestamp.slice(0, 10))).size).toBe(2);
    expect(out.every((b) => ![0, 6].includes(new Date(b.timestamp).getUTCDay()))).toBe(true);
  });

  it("tones a news item by its severity", () => {
    expect(severityTone(5)).toBe("down");
    expect(severityTone(4)).toBe("warn");
    expect(severityTone(1)).toBe("neutral");
  });

  it("defaults the interview's second language to Marathi without storage", () => {
    expect(loadCopilotLanguage()).toEqual({ ui: "en", secondary: "mr" });
  });
});
