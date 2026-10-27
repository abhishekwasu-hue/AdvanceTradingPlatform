import { createRef } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import ticketSource from "../fno/OrderTicketV2.tsx?raw";
import { TicketBody } from "../fno/OrderTicketV2";
import { draftFromDefaults, newDraft, type TicketTarget } from "../fno/ticketModel";
import oldPageSource from "../pages/DeploymentsPage.tsx?raw";
import { StartBotV2 } from "../paperbot/PaperBotV2";
import paperBotSource from "../paperbot/PaperBotV2.tsx?raw";
import { paperBotRequest, paperBotRequestWithDefaults, startSummary } from "../paperbot/model";
import { flagOffFallback } from "../shell/aliases";
import { navItemFor } from "../shell/navModel";
import { PAGE_COMPONENTS_V2 } from "../shell/pagesV2";
import { shellKeyFromPath } from "../shell/shellRoutes";
import type { StrategyInfo } from "../types";
import pageSource from "./TradingDefaultsV2.tsx?raw";
import { DefaultsFormV2 } from "./TradingDefaultsV2";
import modelSource from "./tradingDefaults.ts?raw";
import {
  DEFAULTS_PATH, EMPTY_DEFAULTS, botWithDefaults, cleanForSave, defaultsOf, formOf, formPrefill, hasAnyDefault, readDefaults,
  stillFromDefaults, ticketPrefill, type TradingDefaults,
} from "./tradingDefaults";

/** Settings redesign part C: "My trading defaults" - a pre-fill, never a hidden rule. */

const noop = () => undefined;
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, "\"").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/[^\n]*/g, "$1");

const intraday: StrategyInfo = { id: "orb_15m_5m", name: "Opening range breakout", description: "Trades the break.", category: "breakout" as StrategyInfo["category"], timeframes: ["5m", "15m"], default_params: {} };
const daily: StrategyInfo = { id: "swing_ema_pullback_d", name: "Swing pullback", description: "Daily.", category: "trend" as StrategyInfo["category"], timeframes: ["day"], default_params: {} };
const d = (over: Partial<TradingDefaults>): TradingDefaults => ({ ...EMPTY_DEFAULTS, ...over });
const SOME = d({ expiry_rule: "NEXT", max_lots: 2, strike_rule: "OTM", strike_offset: 2, exit_rules: { trailing_stop_pct: 1.5, break_even_at_r: null, time_exit_minutes: null, time_exit_at: null } });
const NEVER_KEYS = ["mode", "broker_name", "broker_account_id", "routing_policy", "route_across_brokers", "order_style", "market_protection_pct", "holding", "timeframe", "symbol", "strategy_id", "exchange"];

describe("the namespace as the frontend reads it", () => {
  it("empty by default; unknown keys and out-of-range values are dropped, never a LIVE or routing field", () => {
    expect(readDefaults(undefined)).toEqual(EMPTY_DEFAULTS);
    expect(readDefaults({})).toEqual(EMPTY_DEFAULTS);
    expect(hasAnyDefault(EMPTY_DEFAULTS)).toBe(false);
    const read = readDefaults({ mode: "LIVE", order_style: "PROTECTED_LIMIT", broker_name: "upstox", routing_policy: "MOST_MARGIN", holding: "SWING",
      max_lots: 501, strike_offset: 1.5, expiry_rule: "WEEKLY", option_strategy: "CUSTOM", exit_rules: { trailing_stop_pct: 0, time_exit_at: "25:00" },
      ticket: { lots: 3, product: "CNC", side: "SELL" } });
    expect(read).toEqual({ ...EMPTY_DEFAULTS, ticket: { lots: 3, product: null, order_type: null } });
    expect(Object.keys(read)).not.toEqual(expect.arrayContaining(["mode"]));
    for (const k of NEVER_KEYS) expect(Object.keys(EMPTY_DEFAULTS)).not.toContain(k);
    expect(Object.keys(EMPTY_DEFAULTS).sort()).toEqual(["entry_mode", "exit_rules", "expiry_rule", "instrument_kind", "max_lots", "option_position",
      "option_strategy", "premium_stop_pct", "spread_width", "stop_credit_pct", "strike_offset", "strike_rule", "target_credit_pct", "ticket"]);
  });

  it("a save keeps only what applies to the choice, so nothing hidden is stored", () => {
    expect(cleanForSave(d({ instrument_kind: "UNDERLYING", expiry_rule: "NEXT", max_lots: 3, strike_rule: "OTM", premium_stop_pct: 30 })))
      .toEqual(d({ instrument_kind: "UNDERLYING" }));
    expect(cleanForSave(d({ instrument_kind: "FUTURE", expiry_rule: "MONTHLY", max_lots: 2, option_position: "WRITE", strike_rule: "ITM" })))
      .toEqual(d({ instrument_kind: "FUTURE", expiry_rule: "MONTHLY", max_lots: 2 }));
    expect(cleanForSave(d({ option_strategy: "IRON_CONDOR", premium_stop_pct: 30, option_position: "BUY", spread_width: 3, target_credit_pct: 50 })))
      .toEqual(d({ option_strategy: "IRON_CONDOR", spread_width: 3, target_credit_pct: 50 }));
    expect(cleanForSave(d({ strike_rule: "ATM", strike_offset: 3 }))).toEqual(d({ strike_rule: "ATM" }));
  });

  it("the page's form round-trips and says what cannot be saved in plain words", () => {
    expect(defaultsOf(formOf(SOME))).toEqual({ value: SOME, problems: [] });
    expect(defaultsOf(formOf(EMPTY_DEFAULTS))).toEqual({ value: EMPTY_DEFAULTS, problems: [] });
    expect(defaultsOf({ ...formOf(EMPTY_DEFAULTS), max_lots: "0" }).problems).toEqual(["Lots for a new bot: a whole number from 1 to 500."]);
    expect(defaultsOf({ ...formOf(EMPTY_DEFAULTS), trailing_stop_pct: "0" }).problems).toEqual(["Trailing stop: a number above 0 up to 50."]);
    expect(defaultsOf({ ...formOf(EMPTY_DEFAULTS), time_exit_at: "3 pm" }).problems).toEqual(["Flat at: a time like 15:10."]);
    expect(defaultsOf({ ...formOf(EMPTY_DEFAULTS), option_strategy: "LONG_STRADDLE", stop_credit_pct: "150" }).problems[0]).toMatch(/at most 100/);
  });
});

describe("identity: empty defaults change nothing (byte-identical bodies)", () => {
  const cases: [string, StrategyInfo][] = [["NIFTY 50", intraday], ["NIFTY BANK", intraday], ["RELIANCE", intraday], ["RELIANCE", daily], ["NIFTY 50", daily]];
  for (const [symbol, info] of cases) {
    it(`Start on paper: ${symbol} (${info.timeframes.join("/")})`, () => {
      const today = paperBotRequest(info.id, symbol, info);
      for (const none of [null, undefined, EMPTY_DEFAULTS, readDefaults({}), readDefaults({ mode: "LIVE" })]) {
        for (const approvalAllowed of [false, true]) {
          const { request, applied } = paperBotRequestWithDefaults(info.id, symbol, info, none, approvalAllowed);
          expect(request).toEqual(today);
          expect(JSON.stringify(request)).toBe(JSON.stringify(today));
          expect(applied).toEqual([]);
        }
      }
      // "Use my defaults" off: today's request even with defaults
      expect(JSON.stringify(paperBotRequestWithDefaults(info.id, symbol, info, SOME, true, false).request)).toBe(JSON.stringify(today));
      expect(startSummary(symbol, info, today)).toBe(startSummary(symbol, info));
    });
  }

  it("the full form and the ticket", () => {
    expect(formPrefill(null, true)).toEqual({});
    expect(formPrefill(EMPTY_DEFAULTS, true)).toEqual({});
    expect(newDraft("BUY", undefined)).toEqual({ side: "BUY", lots: 1, orderType: "MARKET", product: null, price: "", trigger: "", stopLoss: "" });   // owner F1: no product pre-selected
    expect(JSON.stringify(newDraft("SELL", Object.keys(ticketPrefill(EMPTY_DEFAULTS)).length ? ticketPrefill(EMPTY_DEFAULTS) : undefined)))
      .toBe(JSON.stringify(newDraft("SELL")));
    expect(ticketPrefill(EMPTY_DEFAULTS)).toEqual({});
  });
});

describe("pre-fill per form", () => {
  it("Start on paper on an index: expiry, lots, strike and exits, each named", () => {
    const { request, applied } = paperBotRequestWithDefaults("orb_15m_5m", "NIFTY 50", intraday, SOME, false);
    expect(request).toEqual({ ...paperBotRequest("orb_15m_5m", "NIFTY 50", intraday), expiry_rule: "NEXT", max_lots: 2, strike_rule: "OTM", strike_offset: 2,
      exit_rules: { trailing_stop_pct: 1.5 } });
    expect(applied).toEqual(["strike 2 steps out of the money", "next expiry", "2 lots", "trailing stop 1.5%"]);
    expect(startSummary("NIFTY 50", intraday, request)).toBe("Trades a NIFTY 50 option at the next expiry, 2 lots, closed before the market closes. Paper money only; your risk limits apply.");
  });

  it("futures as the default: the option rules go, the expiry and lots stay", () => {
    const { request, applied } = paperBotRequestWithDefaults("orb_15m_5m", "NIFTY 50", intraday, d({ instrument_kind: "FUTURE", expiry_rule: "MONTHLY", max_lots: 3 }), false);
    expect(request).toMatchObject({ instrument_kind: "FUTURE", expiry_rule: "MONTHLY", max_lots: 3, mode: "PAPER" });
    for (const k of ["option_position", "strike_rule", "strike_offset", "premium_stop_pct"]) expect(request).not.toHaveProperty(k);
    expect(applied).toEqual(["futures", "monthly expiry", "3 lots"]);
    expect(startSummary("NIFTY 50", intraday, request)).toContain("a NIFTY 50 future at the monthly expiry, 3 lots");
  });

  it("a structure: its width and credit %, no option side and no premium stop", () => {
    const { request, applied } = paperBotRequestWithDefaults("orb_15m_5m", "NIFTY 50", intraday,
      d({ option_strategy: "BULL_PUT_SPREAD", spread_width: 3, target_credit_pct: 50, stop_credit_pct: 150 }), false);
    expect(request).toMatchObject({ option_strategy: "BULL_PUT_SPREAD", spread_width: 3, target_credit_pct: 50, stop_credit_pct: 150 });
    expect(request).not.toHaveProperty("option_position");
    expect(applied).toEqual(["Bull put spread", "wing width 3 steps", "target 50% of the credit", "stop 150% of the credit"]);
  });

  it("a daily strategy keeps the swing rules: no structure, no writing (not claimed either)", () => {
    const { request, applied } = paperBotRequestWithDefaults("swing_ema_pullback_d", "NIFTY 50", daily,
      d({ option_strategy: "IRON_CONDOR", option_position: "WRITE", max_lots: 2 }), false);
    expect(request).toMatchObject({ holding: "SWING", option_position: "BUY", max_lots: 2 });
    expect(request).not.toHaveProperty("option_strategy");
    expect(applied).toEqual(["2 lots"]);
  });

  it("a stock: the quick bot trades the stock itself, so only the exits apply", () => {
    const { request, applied } = paperBotRequestWithDefaults("orb_15m_5m", "RELIANCE", intraday, d({ ...SOME, instrument_kind: "OPTION" }), false);
    expect(request).toEqual({ ...paperBotRequest("orb_15m_5m", "RELIANCE", intraday), exit_rules: { trailing_stop_pct: 1.5 } });
    expect(applied).toEqual(["trailing stop 1.5%"]);
  });

  it("the full form: every value it takes, as the form holds it, and the mark goes when the user changes it", () => {
    const p = formPrefill(d({ instrument_kind: "OPTION", option_strategy: "SINGLE", option_position: "WRITE", expiry_rule: "NEXT", strike_rule: "ITM",
      strike_offset: 2, premium_stop_pct: 40, max_lots: 4, exit_rules: { trailing_stop_pct: 2, break_even_at_r: 1, time_exit_minutes: 30, time_exit_at: "15:00" } }), false);
    expect(p).toEqual({ kind: "OPTION", structure: "SINGLE", position: "WRITE", expiryRule: "NEXT", strikeRule: "ITM", strikeOffset: 2, premiumStop: "40",
      maxLots: "4", exitTrail: "2", exitBreakEven: "1", exitMinutes: "30", exitAt: "15:00" });
    expect(stillFromDefaults(p, "maxLots", "4")).toBe(true);
    expect(stillFromDefaults(p, "maxLots", "5")).toBe(false);
    expect(stillFromDefaults(p, "spreadWidth", 2)).toBe(false);
  });

  it("the chain ticket: lots, product and order type; never the side", () => {
    const t = ticketPrefill(d({ ticket: { lots: 3, product: "NRML", order_type: "LIMIT" } }));
    expect(t).toEqual({ lots: 3, product: "NRML", orderType: "LIMIT" });
    expect(newDraft("SELL", t)).toEqual({ side: "SELL", lots: 3, orderType: "LIMIT", product: "NRML", price: "", trigger: "", stopLoss: "" });
    expect(draftFromDefaults(newDraft("BUY", t), t)).toEqual({ lots: true, orderType: true, product: true });
    expect(draftFromDefaults({ ...newDraft("BUY", t), lots: 1 }, t)).toEqual({ lots: false, orderType: true, product: true });
    expect(draftFromDefaults(newDraft("BUY"), undefined)).toEqual({ lots: false, orderType: false, product: false });
  });
});

describe("never pre-filled, and the approval gate", () => {
  it("the mode, broker routing, the LIVE order style and the holding never come from defaults", () => {
    const crafted = { ...SOME, entry_mode: "APPROVAL", mode: "LIVE", order_style: "PROTECTED_LIMIT", broker_name: "upstox", routing_policy: "MOST_MARGIN",
      holding: "SWING", timeframe: "day" } as unknown as TradingDefaults;
    for (const [symbol, info] of [["NIFTY 50", intraday], ["RELIANCE", daily]] as const) {
      const today = paperBotRequest(info.id, symbol, info);
      const { request } = paperBotRequestWithDefaults(info.id, symbol, info, crafted, true);
      expect(request.mode).toBe("PAPER");
      for (const k of NEVER_KEYS) expect((request as unknown as Record<string, unknown>)[k]).toEqual((today as unknown as Record<string, unknown>)[k]);
    }
    // the time exits are empty by default and only come from the user's own value (SR-10)
    expect(EMPTY_DEFAULTS.exit_rules).toBeNull();
    expect(code(modelSource)).not.toMatch(/time_exit_(minutes|at):\s*\d/);
    expect(code(modelSource)).not.toMatch(/order_style|routing_policy|broker_name|"LIVE"/);
  });

  it("APPROVAL only while the approval-only flag allows it", () => {
    const approval = d({ entry_mode: "APPROVAL" });
    const base = paperBotRequest("orb_15m_5m", "NIFTY 50", intraday);
    expect(botWithDefaults(base, approval, { index: true, swing: false, approvalAllowed: false })).toEqual({ request: base, applied: [] });
    const on = botWithDefaults(base, approval, { index: true, swing: false, approvalAllowed: true });
    expect(on.request).toEqual({ ...base, entry_mode: "APPROVAL" });
    expect(on.applied).toEqual(["each entry waits for your Approve"]);
    expect(formPrefill(approval, false)).toEqual({});
    expect(formPrefill(approval, true)).toEqual({ approval: true });
    expect(formPrefill(d({ entry_mode: "AUTO" }), true)).toEqual({});
    // the full form sends it only on the paper-only form, with the flag on and the box ticked
    expect(code(oldPageSource)).toContain('...(paperOnly && approvalAllowed && approval ? { entry_mode: "APPROVAL" as const } : {})');
  });
});

describe("wiring: behind ui_v2, PAPER only, a page Part B can link", () => {
  it("the page is v2 only at /settings/trading-defaults; with the flag off it goes back to Settings", () => {
    expect(DEFAULTS_PATH).toBe("/settings/trading-defaults");
    expect(PAGE_COMPONENTS_V2["trading-defaults"]).toBeDefined();
    expect(shellKeyFromPath("/settings/trading-defaults")).toBe("trading-defaults");
    expect(shellKeyFromPath("/settings/trading-defaults/")).toBe("trading-defaults");
    expect(flagOffFallback("/settings/trading-defaults")).toBe("/settings");
    expect(navItemFor("trading-defaults")?.title).toBe("My trading defaults");
  });

  it("each form reads the defaults only with ui_v2 on (the full form only as the Paper bot's paper-only form)", () => {
    expect(code(paperBotSource)).toContain('useTradingDefaults(!!user && features.on("ui_v2"))');
    expect(code(oldPageSource)).toContain('useTradingDefaults(paperOnly && !!user && features.on("ui_v2"))');
    expect(code(ticketSource)).toContain('useTradingDefaults(features.on("ui_v2"))');
    expect(code(oldPageSource)).toContain("if (!Object.keys(p).length) return;");      // no defaults: the form's state is untouched
  });

  it("Start on paper says what came from the defaults, with a switch and a link; nothing extra without defaults", () => {
    const render = (defaults: TradingDefaults | null) => text(renderToStaticMarkup(
      <MemoryRouter><StartBotV2 library={[intraday]} custom={[]} hasBroker firstField={createRef()} onStarted={noop} defaults={defaults} /></MemoryRouter>));
    const none = render(null);
    expect(none).not.toContain("Use my defaults");
    expect(render(EMPTY_DEFAULTS)).toBe(none);
    // the first render has no strategy picked yet; the summary still names the defaults' expiry and lots
    const some = render(SOME);
    expect(some).toContain("Use my defaults for this bot");
    expect(some).toContain("From your defaults: strike 2 steps out of the money, next expiry, 2 lots, trailing stop 1.5%.");
    expect(some).toContain("Change my defaults");
    expect(some).toContain("at the next expiry, 2 lots");
  });

  it("the ticket marks the fields that still show a default", () => {
    const TT: TicketTarget = { underlying: "NIFTY", expiry: "2026-11-03", strike: 24500, right: "CE", label: "NIFTY 03 NOV 24500 CE", lotSize: 75, ltp: 150, bid: 149.5, ask: 150.5 };
    const t = { lots: 2, product: "NRML" as const };
    const body = (defaults?: typeof t) => text(renderToStaticMarkup(<TicketBody target={TT} draft={newDraft("BUY", defaults)} step={{ kind: "edit" }} busy={false}
      problem={null} idPrefix="t" onDraft={noop} onReview={noop} onPlace={noop} onBack={noop} onClose={noop} defaults={defaults} />));
    expect(body()).not.toContain("from your defaults");
    expect(body(t)).toContain("Lots from your defaults");
    expect(body(t)).toContain("Product from your defaults. Tap to change.");
  });

  it("the page: plain words, the time exits empty, the entries card only with the flag, risk limits a link", () => {
    const render = (approvalAllowed: boolean) => text(renderToStaticMarkup(<MemoryRouter>
      <DefaultsFormV2 form={formOf(EMPTY_DEFAULTS)} onChange={noop} approvalAllowed={approvalAllowed} busy={false} note={null} onSave={noop} onClear={noop} />
    </MemoryRouter>));
    const off = render(false);
    expect(off).toContain("What a new bot trades");
    expect(off).toContain("Chain order ticket");
    expect(off).toContain("Open Risk limits");
    expect(off).not.toContain("keeps asking for my Approve");
    expect(render(true)).toContain("A new bot keeps asking for my Approve before each entry");
    expect(off.match(/Empty unless you set one\./g)?.length).toBe(2);
    for (const word of ["LIVE", "Mode", "Broker", "Routing", "order style", "deployment", "namespace", "prefs", "null", "undefined"]) expect(off).not.toContain(word);
    expect(code(pageSource)).not.toMatch(/"LIVE"|order_style|routing_policy/);
  });
});
