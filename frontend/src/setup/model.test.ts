import stepsSource from "./StepsV2.tsx?raw";
import ts from "typescript";
import { describe, expect, it } from "vitest";
import { flagOffFallback, shellKeyFromPath, shellTitle } from "../shell/shellRoutes";
import {
  RISK_DEFAULTS, SETUP_PATH, SUGGESTIONS, cleanStored, cleanView, dismissChip, isComplete, markOpened, nextStep, openingStep, paperDeployment, START_BOT_PATH,
  limitText, limitsBody, perTradeText, riskErrors, riskNumbers, riskSentence, setupChip, skipStep, stepAfter, testSummary, withStored,
  type SetupStepKey, type SetupView,
} from "./model";

/** UI2-7: the setup wizard's rules - step state, skip and resume, the chip's words, the risk sentence, PAPER only. */

const T = "2026-10-21T09:30:00Z";
const EMPTY = cleanStored(null);

/** A server answer: `done` keys are done, `skipped` keys carry a skip mark. */
function view(done: SetupStepKey[] = [], skipped: SetupStepKey[] = [], extra: Record<string, unknown> = {}): SetupView {
  const stored = { ...EMPTY, ...extra, steps: { ...EMPTY.steps, ...Object.fromEntries(skipped.map((k) => [k, { skipped_at: T }])) } };
  const v = cleanView({ steps: ["broker", "risk", "strategy", "alerts"].map((key) => ({ key, done: done.includes(key as SetupStepKey) })), stored, version: 3 });
  if (!v) throw new Error("not a view");
  return v;
}

describe("UI2-7 step state", () => {
  it("reads the server's answer; anything malformed is unset, and a non-answer is no view", () => {
    expect(EMPTY).toEqual({ steps: { broker: { skipped_at: null }, risk: { skipped_at: null }, strategy: { skipped_at: null }, alerts: { skipped_at: null } },
      opened_at: null, dismissed_at: null, get_started_hidden_at: null, get_started_shown_at: null });
    expect(cleanStored({ steps: { broker: { skipped_at: "soon" }, live: { skipped_at: T } }, opened_at: 5 })).toEqual(EMPTY);
    expect(cleanView(null)).toBeNull();
    expect(cleanView({ steps: "x", version: 1 })).toBeNull();
    expect(cleanView({ steps: [], stored: {}, version: 0 })?.steps.map((s) => s.done)).toEqual([false, false, false, false]);
  });

  it("a done step is never shown as skipped; a skip only changes the order (the next step prefers one not skipped)", () => {
    const v = view(["broker"], ["broker", "risk"]);
    expect(v.steps.map((s) => [s.key, s.done, s.skipped])).toEqual([
      ["broker", true, false], ["risk", false, true], ["strategy", false, false], ["alerts", false, false]]);
    expect(nextStep(v)).toBe("strategy");
    expect(nextStep(view(["broker", "risk"], ["strategy", "alerts"]))).toBe("strategy");   // skipped, still not done
    expect(nextStep(view([], ["broker", "risk", "strategy", "alerts"]))).toBe("risk");      // a skipped broker counts as done
    expect(isComplete(view([], ["broker", "risk", "strategy", "alerts"]))).toBe(false);
    expect(isComplete(view(["risk", "strategy", "alerts"], ["broker"]))).toBe(true);        // "I'll trade on paper first"
    expect(isComplete(view(["broker", "risk", "strategy"], ["alerts"]))).toBe(false);
  });

  it("skip for now, then resume where the trader left off (another device reads the same marks)", () => {
    const start = view();
    expect(openingStep(start)).toBe("broker");
    const stored = skipStep(start.stored, "broker", T);
    expect(stored.steps.broker.skipped_at).toBe(T);
    const after = withStored(start, stored);
    expect(nextStep(after)).toBe("risk");
    // resumed elsewhere: the saved marks give the same next step
    expect(openingStep(cleanView(JSON.parse(JSON.stringify({ ...after, steps: after.steps })))!)).toBe("risk");
    expect(stepAfter(after, "broker")).toBe("risk");
    expect(stepAfter(view(["risk", "strategy", "alerts"]), "alerts")).toBe("broker");     // wraps to an earlier open step
    expect(stepAfter(view(["broker", "risk", "strategy"]), "alerts")).toBeNull();
    expect(stepAfter(view(["broker"], ["strategy"]), "risk")).toBe("alerts");               // past a skipped step
    expect(openingStep(view(["broker"], ["risk"]))).toBe("strategy");                        // opens past skipped steps
    expect(openingStep(view(["broker"], ["risk", "strategy", "alerts"]))).toBe("risk");     // ...or on the first of them
    expect(openingStep(view(["broker", "risk", "strategy", "alerts"]))).toBeNull();          // complete: the summary
  });

  it("records the wizard's first opening once (P3: nothing opens it by itself)", () => {
    expect(markOpened(EMPTY, T).opened_at).toBe(T);
    expect(markOpened({ ...EMPTY, opened_at: "2026-01-01T00:00:00Z" }, T).opened_at).toBe("2026-01-01T00:00:00Z");
    expect(withStored(view(), markOpened(EMPTY, T)).stored.opened_at).toBe(T);
  });
});

describe("UI2-7 the Setup chip", () => {
  it("says which step the trader is on and what is next (§11's example)", () => {
    expect(setupChip(view(["broker"]))).toEqual({ label: "Setup 2 of 4", next: "Next: set your risk limits", to: SETUP_PATH });
    expect(setupChip(view())).toEqual({ label: "Setup 1 of 4", next: "Next: connect your broker", to: "/settings/setup" });
    expect(setupChip(view([], ["broker"]))?.label).toBe("Setup 2 of 4");                  // a skipped broker counts as done
    expect(setupChip(view(["broker"], ["risk"]))).toMatchObject({ label: "Setup 2 of 4", next: "Next: pick your first strategy" });
    expect(setupChip(view(["broker", "risk", "strategy"]))).toMatchObject({ label: "Setup 4 of 4", next: "Next: choose your alerts" });
    expect(setupChip(view(["risk"], ["broker"]))?.next).toBe("Next: pick your first strategy");
  });

  it("stays until every step is done (§11) or it is closed; Settings still shows it until setup is complete", () => {
    expect(setupChip(view(["broker", "risk"], ["strategy", "alerts"]))).toMatchObject({ label: "Setup 3 of 4", next: "Next: pick your first strategy" });
    expect(setupChip(view([], ["broker", "risk", "strategy", "alerts"]))).toMatchObject({ label: "Setup 2 of 4", next: "Next: set your risk limits" });
    expect(setupChip(view(["risk", "strategy", "alerts"], ["broker"]))).toBeNull();
    expect(setupChip(view(["broker", "risk", "strategy", "alerts"]))).toBeNull();
    expect(setupChip(null)).toBeNull();
    const closed = withStored(view(["broker"]), dismissChip(EMPTY, T));
    expect(setupChip(closed)).toBeNull();
    expect(setupChip(closed, { ignoreDismissal: true })?.label).toBe("Setup 2 of 4");
  });
});

describe("UI2-7 risk limits (TT-12: nothing filled in for him)", () => {
  it("no limit has a default; the 'most you can lose today' sentence follows his own numbers", () => {
    expect(RISK_DEFAULTS).toEqual({ capital: 100000 });
    expect(riskSentence(100000, 3)).toBe("With these limits the most you can lose today is ₹3,000.");
    expect(riskSentence(1000000, 2.5)).toBe("With these limits the most you can lose today is ₹25,000.");
    expect(perTradeText(100000, 1)).toBe("At most ₹1,000 on one trade.");
    expect(riskSentence(Number.NaN, 3)).toBeNull();
    expect(riskSentence(100000, 0)).toBeNull();
    expect(riskSentence(100000, null)).toBeNull();                   // not set: no sentence
    expect(perTradeText(100000, null)).toBeNull();
  });

  it("checks each field against the server's bounds, in plain words; an empty limit is fine (not set)", () => {
    const ok = { capital: "1,00,000", riskPerTradePct: "1", dailyLossPct: "3", maxTradesPerDay: "10", minRr: "1.5" };
    expect(riskErrors(ok)).toEqual({});
    expect(riskErrors({ capital: "1,00,000", riskPerTradePct: "", dailyLossPct: "", maxTradesPerDay: "", minRr: "" })).toEqual({});
    const bad = riskErrors({ capital: "5000", riskPerTradePct: "101", dailyLossPct: "x", maxTradesPerDay: "2.5", minRr: "-1" });
    expect(bad.capital).toBe("Starting capital must be between ₹10,000 and ₹1,00,00,00,000.");
    expect(riskErrors({ ...ok, capital: "" }, { capital: false })).toEqual({});                // no field, nothing to check
    expect(riskErrors({ ...ok, capital: "" }).capital).toBe("Enter a number for starting capital.");
    expect(bad.riskPerTradePct).toBe("Risk per trade must be more than 0 and at most 100.");   // M68 PR-5: no ATP cap (was 5)
    expect(riskErrors({ ...ok, riskPerTradePct: "9", minRr: "11" })).toEqual({});
    expect(bad.dailyLossPct).toBe("Enter a number for daily loss limit.");
    expect(bad.maxTradesPerDay).toBe("Enter a whole number for trades a day.");
    expect(bad.minRr).toBe("Lowest reward to risk must be at least 0.");
  });

  it("an empty box is sent as null (not set); Settings says 'Off' unless he set it (UI-28 U1.10)", () => {
    const n = riskNumbers({ capital: "1,00,000", riskPerTradePct: "", dailyLossPct: "2", maxTradesPerDay: "", minRr: "" });
    expect(n).toEqual({ capital: 100000, riskPerTradePct: null, dailyLossPct: 2, maxTradesPerDay: null, minRr: null });
    expect(limitsBody(n)).toEqual({ risk_per_trade_pct: null, daily_loss_pct: 2, max_trades_per_day: null, min_rr: null });
    expect(limitText("minRr", [], 0)).toBe("Off");
    expect(limitText("minRr", undefined, 1.5)).toBe("Off");               // an older server: nothing is known to be his
    expect(limitText("minRr", ["min_rr"], 1.5)).toBe("1.5 : 1");
    expect(limitText("dailyLossPct", ["daily_loss_pct"], 2)).toBe("2% of capital");
    expect(limitText("maxTradesPerDay", ["max_trades_per_day"], 0)).toBe("No limit");
    expect(limitText("maxTradesPerDay", ["max_trades_per_day"], 5)).toBe("5 a day");
  });
});

describe("UI2-7 strategy step: PAPER only", () => {
  it("three suggestions, one per style, each deployed in PAPER and never with a broker or LIVE", () => {
    expect(SUGGESTIONS.map((s) => s.style)).toEqual(["Intraday", "Swing", "Options"]);
    for (const s of SUGGESTIONS) {
      const body = paperDeployment(s);
      expect(body.mode).toBe("PAPER");
      expect(body).not.toHaveProperty("broker_name");
      expect(JSON.stringify(body)).not.toMatch(/LIVE/);
    }
    expect(paperDeployment(SUGGESTIONS[1])).toMatchObject({ timeframe: "day", holding: "SWING", instrument_kind: "UNDERLYING" });
    expect(paperDeployment(SUGGESTIONS[2])).toEqual(expect.objectContaining({ instrument_kind: "OPTION" }));
    // M68 PR-4 (TS1-040): no pre-chosen side, expiry or strike
    expect(paperDeployment(SUGGESTIONS[2])).not.toHaveProperty("option_position");
    expect(paperDeployment(SUGGESTIONS[2])).not.toHaveProperty("expiry_rule");
    expect(paperDeployment(SUGGESTIONS[2])).not.toHaveProperty("strike_rule");
  });

  it("REV-1 M2 (D3): Start a trading bot opens the form with nothing pre-chosen; no Buys wording", () => {
    expect(START_BOT_PATH).toBe("/deployments");
    for (const s of SUGGESTIONS) expect(s.line).not.toMatch(/\bbuys?\b|\bsells?\b/i);
    expect(stepsSource).toContain("to={START_BOT_PATH}");
    expect(stepsSource).not.toMatch(/createDeployment\(|paperDeployment\(/);
  });

  it("a quick test in one line", () => {
    expect(testSummary({ total_trades: 12, win_rate: 58.33, net_pnl: 4250 }, 30, "NIFTY 50")).toBe("12 trades · 58% won · +₹4,250 on the last 30 days of NIFTY 50 prices.");
    expect(testSummary({ total_trades: 1, win_rate: 0, net_pnl: -310.4 }, 30, "RELIANCE")).toBe("1 trade · 0% won · −₹310 on the last 30 days of RELIANCE prices.");
    expect(testSummary({ total_trades: 0, win_rate: 0, net_pnl: 0 }, 365, "RELIANCE")).toBe("No trades on the last 365 days of RELIANCE prices.");
  });
});

describe("UI2-7 Settings → Setup address", () => {
  it("is a v2 page with its own title; with the flag off it goes back to Settings", () => {
    expect(shellKeyFromPath("/settings/setup")).toBe("setup");
    expect(shellKeyFromPath("/settings/setup/")).toBe("setup");
    expect(shellTitle("setup")).toBe("Setup");
    expect(flagOffFallback("/settings/setup")).toBe("/settings");
    expect(flagOffFallback("/settings")).toBeNull();
  });
});

// --- Copy: plain English, no advice words (the Copilot's compliance list) ---------------------------------------------

const SOURCES = import.meta.glob<string>(["./*.ts", "./*.tsx", "!./*.test.ts", "!./*.test.tsx"], { query: "?raw", import: "default", eager: true });
const BANNED = [/\brecommend(ed|s|ing|ations?)?\b/i, /\bbest\b/i, /\bfor you\b/i, /\bguarantee(d|s)?\b/i, /\brisk[- ]free\b/i, /\bsure[- ]?shot\b/i,
  /\bsuggested\b/i, /\bideal\b/i];

function literals(file: string, text: string): string[] {
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const out: string[] = [];
  const visit = (node: ts.Node) => {
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node) || ts.isTemplateHead(node) || ts.isTemplateMiddle(node) || ts.isTemplateTail(node)) out.push(node.text);
    else if (ts.isJsxText(node) && node.text.trim()) out.push(node.text.trim());
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return out;
}

describe("UI2-7 copy", () => {
  it("has no advice words and no developer wording on screen", () => {
    expect(Object.keys(SOURCES).length).toBeGreaterThanOrEqual(5);
    const found = Object.entries(SOURCES).flatMap(([f, text]) => literals(f, text)
      .filter((s) => BANNED.some((re) => re.test(s)) || /\b(?:4\d\d|5\d\d|endpoint|traceback|flag|registry|adapter)\b/i.test(s) && !s.startsWith("/"))
      .map((s) => `${f}: ${s.slice(0, 60)}`));
    expect(found).toEqual([]);
  });

  it("never offers LIVE in the wizard", () => {
    const steps = SOURCES["./StepsV2.tsx"] + SOURCES["./SetupWizardV2.tsx"];
    const text = literals("x.tsx", steps).join(" ");
    expect(text).not.toMatch(/\bLIVE\b|\bLive\b|real money/);
    expect(steps).not.toMatch(/mode:\s*"LIVE"/);
  });
});
