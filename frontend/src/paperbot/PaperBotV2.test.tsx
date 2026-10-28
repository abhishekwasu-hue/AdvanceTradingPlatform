import { createRef } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import oldPageSource from "../pages/DeploymentsPage.tsx?raw";
import routesSource from "../routes.ts?raw";
import { NAV_SECTIONS_V2 } from "../shell/navModel";
import { PAGE_COMPONENTS_V2 } from "../shell/pagesV2";
import type { Deployment, StrategyInfo } from "../types";
import modelSource from "./model.ts?raw";
import pageSource from "./PaperBotV2.tsx?raw";
import { BotRow, StartBotV2 } from "./PaperBotV2";
import {
  READY, botName, botState, botStatusText, botWhat, cleanSymbol, isSwing, lastChecked, paperBotRequest, sortBots, startSummary, startSummaryParts, strategyLine,
  symbolProblem,
} from "./model";

/** UX2 fix 4: the v2 Paper bot page - three taps, PAPER only, plain words. */

const noop = () => undefined;
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/[^\n]*/g, "$1");

const intraday: StrategyInfo = { id: "orb_15m_5m", name: "Opening range breakout", description: "Trades the break. More text.", category: "breakout" as StrategyInfo["category"], timeframes: ["5m", "15m"], default_params: {} };
const daily: StrategyInfo = { id: "swing_ema_pullback_d", name: "Swing pullback", description: "Daily.", category: "trend" as StrategyInfo["category"], timeframes: ["day"], default_params: {} };
const other: StrategyInfo = { id: "rsi_div", name: "RSI divergence", description: "Looks for a divergence between price and RSI at a swing. It then waits.", category: "reversal" as StrategyInfo["category"], timeframes: ["15m"], default_params: {} };

function bot(over: Partial<Deployment> = {}): Deployment {
  return {
    id: 7, strategy_id: "orb_15m_5m", symbol: "NIFTY 50", exchange: "NSE", timeframe: "1min", mode: "PAPER", broker_name: "upstox",
    status: "ACTIVE", pause_reason: null, last_evaluated_at: new Date(Date.now() - 120_000).toISOString(), last_signal_at: null, last_error: null,
    consecutive_failures: 0, open_positions: 0, created_by: 1, created_at: "2026-10-01T04:00:00Z", updated_at: "2026-10-01T04:00:00Z",
    instrument_kind: "OPTION", option_position: "BUY", expiry_rule: "NEAREST", strike_rule: "ATM", strike_offset: 0, premium_stop_pct: null,
    max_lots: 1, contract_rules: "OPTION BUY NEAREST ATM", holding: "INTRADAY", ...over,
  } as Deployment;
}

describe("UX2 fix 4 wiring", () => {
  it("the v2 shell has the Paper bot page at /deployments; today's router keeps the old page", () => {
    expect(PAGE_COMPONENTS_V2.deployments).toBeDefined();
    expect(routesSource).toContain('deployments: () => import("./pages/DeploymentsPage")');
    expect(routesSource).not.toContain("paperbot");
  });

  it("one name: the navigation says Paper bot and still finds it by its old name", () => {
    const item = NAV_SECTIONS_V2.flatMap((s) => s.items).find((i) => i.key === "deployments")!;
    expect([item.label, item.title]).toEqual(["Paper bot", "Paper bot"]);
    expect(item.keywords).toContain("autopilot");
  });

  it("More ways to trade is the old form in paper-only mode, loaded only when opened", () => {
    expect(pageSource).toContain('lazy(() => import("../pages/DeploymentsPage"))');
    expect(pageSource).toMatch(/\{moreOpen && \([\s\S]*<MoreWaysToTrade paperOnly/);
    const old = code(oldPageSource);
    expect(old).toContain("{!paperOnly && <div className=\"flex items-center gap-2 text-sm\">");     // the PAPER / LIVE switch
    expect(old).toContain("{!paperOnly && <Card title={`Deployments");
    expect(old).toContain('const [mode, setMode] = useState<ExecutionMode>("PAPER")');
  });

  it("never offers LIVE: the request is always PAPER and the page has no mode switch", () => {
    expect(code(pageSource)).not.toMatch(/"LIVE"\s*\)|setMode|mode:\s*"LIVE"/);
    expect(code(modelSource)).toContain('mode: "PAPER"');
    expect(code(modelSource)).not.toContain('"LIVE"');
  });
});

describe("UX2 fix 4 model", () => {
  it("an index trades its option at the nearest expiry, one lot; a stock trades its shares", () => {
    expect(paperBotRequest("orb_15m_5m", " nifty 50 ", intraday)).toEqual({
      strategy_id: "orb_15m_5m", symbol: "NIFTY 50", exchange: "NSE", timeframe: "1min", mode: "PAPER", holding: "INTRADAY",
      instrument_kind: "OPTION", option_position: "BUY", expiry_rule: "NEAREST", strike_rule: "ATM", strike_offset: 0, max_lots: 1,
    });
    expect(paperBotRequest("swing_ema_pullback_d", "RELIANCE", daily)).toEqual({
      strategy_id: "swing_ema_pullback_d", symbol: "RELIANCE", exchange: "NSE", timeframe: "day", mode: "PAPER", holding: "SWING",
      instrument_kind: "UNDERLYING",
    });
    expect(isSwing(daily)).toBe(true);
    expect(isSwing(intraday)).toBe(false);
  });

  it("symbols: cleaned, and a plain problem for a bad one", () => {
    expect(cleanSymbol("  hdfc   bank ")).toBe("HDFC BANK");
    expect(symbolProblem("")).toBe("Pick or type a symbol.");
    expect(symbolProblem("<script>")).toMatch(/as the exchange writes it/);
    expect(symbolProblem("m&m")).toBeNull();
  });

  it("the strategy line: the ready-made sentence, else the Library's first sentence", () => {
    expect(strategyLine("orb_15m_5m", intraday)).toBe(READY[0].line);
    expect(strategyLine("rsi_div", other)).toBe("Looks for a divergence between price and RSI at a swing.");
    expect(strategyLine("x", null)).toBe("");
  });

  it("ready-made lines use structure words only: no prices, no order or advice words", () => {
    for (const r of READY) {
      expect(r.line).not.toMatch(/\d{3,}|₹|\b(buy|sell|target|stop[- ]?loss|you should|recommend)/i);
    }
  });

  it("the start summary says what is traded, how long it is held, and paper only", () => {
    expect(startSummary("NIFTY 50", intraday)).toBe("Trades a NIFTY 50 option at the nearest expiry, 1 lot, closed before the market closes. Paper money only; your risk limits apply.");
    expect(startSummary("RELIANCE", daily)).toBe("Trades RELIANCE shares, held for days. Paper money only; your risk limits apply.");
    // UX-11 U1: one visible line, the rest on demand - the same words, none dropped.
    expect(startSummaryParts("NIFTY 50", intraday)).toEqual({ line: "Trades a NIFTY 50 option at the nearest expiry, 1 lot.",
      more: "Closed before the market closes. Paper money only; your risk limits apply." });
    expect(startSummaryParts("RELIANCE", daily).line).toBe("Trades RELIANCE shares.");
  });

  it("status in words: running, paused by you, a readable reason, never an internal code", () => {
    expect(botStatusText(bot())).toBe("Running");
    expect(botStatusText(bot({ status: "PAUSED", pause_reason: "paused from Autopilot tab" }))).toBe("Paused by you");
    expect(botStatusText(bot({ status: "PAUSED", pause_reason: "paused from the Paper bot page" }))).toBe("Paused by you");
    expect(botStatusText(bot({ status: "PAUSED", pause_reason: "3 losses in a row." }))).toBe("Paused: 3 losses in a row");
    expect(botStatusText(bot({ status: "PAUSED", pause_reason: "auto_paused: consecutive_failures>=5" }))).toBe("Paused");
    expect(botStatusText(bot({ status: "STOPPED" }))).toBe("Stopped");
    expect(botState(bot({ status: "PAUSED" }))).toBe("paused");
  });

  it("names, what it trades, the last check", () => {
    const names = new Map([["orb_15m_5m", "Opening range breakout"]]);
    expect(botName(bot(), names)).toBe("Opening range breakout");
    expect(botName(bot({ strategy_id: "custom_my_breakout" }), names)).toBe("My breakout");
    expect(botWhat(bot())).toBe("NIFTY 50 options · during the day");
    expect(botWhat(bot({ symbol: "RELIANCE", instrument_kind: "UNDERLYING", holding: "SWING" }))).toBe("RELIANCE · held for days");
    const now = Date.parse("2026-10-22T06:00:00Z");
    expect(lastChecked(null, now)).toBe("Not checked yet");
    expect(lastChecked("2026-10-22T05:59:30Z", now)).toBe("Checked just now");
    expect(lastChecked("2026-10-22T05:50:00Z", now)).toBe("Checked 10 min ago");
    expect(lastChecked("2026-10-22T01:00:00Z", now)).toBe("Checked 5 h ago");
  });

  it("running first, then paused; stopped only when asked for", () => {
    const list = [bot({ id: 1, status: "STOPPED" }), bot({ id: 2, status: "PAUSED" }), bot({ id: 3 }), bot({ id: 4 })];
    expect(sortBots(list).map((b) => b.id)).toEqual([4, 3, 2]);
    expect(sortBots(list, true).map((b) => b.id)).toEqual([4, 3, 2, 1]);
  });
});

describe("UX2 fix 4 view", () => {
  const row = (b: Deployment, over: Partial<Parameters<typeof BotRow>[0]> = {}) => renderToStaticMarkup(
    <ul><BotRow bot={b} name="Opening range breakout" busy={false} disabled={false} confirming={false} onAsk={noop} onCancel={noop} onAct={noop} {...over} /></ul>);

  it("a running bot: name, Paper, what it trades, Running, Pause and Stop", () => {
    const t = text(row(bot({ open_positions: 1 })));
    expect(t).toContain("Opening range breakout Paper NIFTY 50 options During the day");   // UX-11b C5: two chips
    expect(t).toMatch(/Running Checked 2 min ago (No signal yet|Last signal [^ ]+ [a-z]+ ago) 1 open trade Pause Stop/);
    expect(t).not.toContain("Resume");
  });

  it("a paused bot offers Resume; a stopped one no buttons; Stop asks first", () => {
    expect(text(row(bot({ status: "PAUSED", pause_reason: "3 losses in a row" })))).toMatch(/Paused: 3 losses in a row .* Resume Stop/);
    expect(row(bot({ status: "STOPPED" }))).not.toContain("<button");
    const asking = text(row(bot(), { confirming: true }));
    expect(asking).toContain("Stop Opening range breakout? It takes no new trades. An open paper trade is still watched until it closes.");
    expect(asking).toContain("Yes, stop it Keep it running");
    expect(asking).not.toMatch(/ Pause /);
  });

  it("the start form: 1 pick a strategy, 2 pick a symbol, Start on paper; the broker note when none is connected", () => {
    const html = renderToStaticMarkup(<MemoryRouter><StartBotV2 library={[intraday, daily, other]} custom={[]} hasBroker={false}
      firstField={createRef<HTMLSelectElement>()} onStarted={noop} /></MemoryRouter>);
    const t = text(html);
    expect(t).toMatch(/1 Pick a strategy .* 2 Pick a symbol .* Start on paper/);
    expect(t).toContain("Connect your broker");
    expect(html.match(/<optgroup label="Ready-made">[\s\S]*?<\/optgroup>/)?.[0]).toContain("Opening range breakout");
    expect(html.match(/<optgroup label="More from the Library">[\s\S]*?<\/optgroup>/)?.[0]).toContain("RSI divergence");
    expect(t).toContain("Another symbol…");
  });

  it("no developer words on the page", () => {
    const own = code(pageSource) + code(modelSource);
    const strings = [...own.matchAll(/"([^"\n]{12,})"|`([^`\n]{12,})`/g)].map((m) => m[1] ?? m[2]).filter((s) => /\s/.test(s) && !/[{}]|\bclass|^[\w-]+:|^\.\.\//.test(s));
    for (const s of strings) expect(s, s).not.toMatch(/deployment|worker|instrument master|base candles|regime|OI gate|endpoint|\bAPI\b|LIVE/);
  });
});
