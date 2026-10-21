import { describe, expect, it } from "vitest";
import type { BrokerGateState } from "../ui/brokerSession";
import type { AiAction, Deployment, NotificationEntry, PortfolioExposure, TradeRecord } from "../types";
import {
  attentionRows, brokerChip, greeting, greetingName, indexCaption, istDay, marketLabel, oiLines, partlyConnected, positionRows,
  signTone, signalsToday, symbolPrices, todayPnl, todayTiles, vixNote, type AttentionInput,
} from "./model";
import type { StripIndex, StripMarket } from "./types";

/** UI2-2: the v2 dashboard's blocks, worked out from the API answers (made-up figures). */

const NOW = Date.parse("2026-10-19T11:00:00+05:30");        // Monday, market open
const upstox = { broker_name: "upstox", account_label: "primary", token_status: "VALID", token_expires_at: null, last_verified_at: null,
  needs_login: false, oauth_supported: true, oauth_callback_url: null } as const;
const CONNECTED: BrokerGateState = { kind: "connected", brokers: [{ ...upstox }] };
const LOGIN: BrokerGateState = { kind: "login-needed", broker: { ...upstox, needs_login: true, token_status: "EXPIRED" }, others: [], expired: true };

const market = (m: Partial<StripMarket>): StripMarket => ({ exchange: "NSE", phase: "closed", is_open: false, opens_at: "09:15", closes_at: "15:30",
  next_open: null, holiday: null, reason: null, ...m });

function entry(id: number, created: string, status: AiAction["status"], extra: Record<string, unknown> = {}, expires: string | null = null): AiAction {
  return { id, deployment_id: null, trade_id: null, action: "ENTRY", rule: "Z3", reason: "", status, decided_by: null, decided_at: null,
    decision_note: null, executed_at: null, result: null, expires_at: expires, created_at: created,
    evidence: { symbol: "NIFTY 50", direction: "LONG", proposal_price: 24790, stop_loss: 24720, target1: 24930, source: "deployment",
      strategy_id: "orb_nifty", signal: { reasons: ["Opening-range breakout, retest held"] }, ...extra } };
}

const trade = (id: number, symbol: string, direction: string, quantity: number, entry_price: number, mode = "PAPER", lot_size: number | null = null) =>
  ({ id, symbol, direction, quantity, entry_price, mode, lot_size, strategy_id: "x", entry_time: "", stop_loss: 0, target1: null, target2: null,
    exit_time: null, exit_price: null, exit_reason: null, pnl: null, charges: 0 }) as TradeRecord;

const exposure = (e: Partial<PortfolioExposure>): PortfolioExposure => ({ as_of: "", capital: 1, open_positions: 0, gross_notional: 0, net_notional: 0,
  long_notional: 0, short_notional: 0, gross_pct_of_capital: 0, unrealised_pnl: 0, realised_today: 0, risk_at_stops: 0, risk_pct_of_capital: 0,
  largest_symbol_pct: 0, by_symbol: [], by_strategy: {}, by_mode: {}, warnings: [], price_source: "ltp", priced_symbols: [], ...e });

describe("UI2-2 greeting", () => {
  it("greets by the time of day in IST, by name, with the date", () => {
    expect(greeting(NOW, "abhi.w@example.com")).toEqual({ hello: "Good morning, Abhi", date: "Monday, 19 October", time: "11:00" });
    expect(greeting(Date.parse("2026-10-19T14:00:00+05:30"), "x@example.com").hello).toBe("Good afternoon, X");
    expect(greeting(Date.parse("2026-10-19T19:30:00+05:30"), null).hello).toBe("Good evening");
    expect(greetingName("trader_7@example.com")).toBe("Trader");
    expect(greetingName("123@example.com")).toBe("");
  });
});

describe("UI2-2 market strip", () => {
  it("names the market state the way §5 writes it", () => {
    expect(marketLabel(market({ phase: "open", is_open: true }), NOW)).toBe("Market open · closes 15:30");
    expect(marketLabel(market({ phase: "pre-open", next_open: "2026-10-19T09:15:00+05:30" }), NOW)).toBe("Pre-open · opens 09:15");
    expect(marketLabel(market({ reason: "holiday", holiday: "Diwali" }), NOW)).toBe("Closed · holiday (Diwali)");
    expect(marketLabel(market({ reason: "after-close", next_open: "2026-10-20T09:15:00+05:30" }), NOW)).toBe("Closed · opens Tue 09:15");
    expect(marketLabel(market({ reason: "before-open", next_open: "2026-10-19T09:15:00+05:30" }), Date.parse("2026-10-19T07:00:00+05:30")))
      .toBe("Closed · opens 09:15");
    expect(marketLabel(null, NOW)).toBe("Market hours unknown");
  });

  it("the broker chip: neutral when connected, amber when a login is needed", () => {
    expect(brokerChip(CONNECTED)).toEqual({ text: "Upstox connected", tone: "neutral", connected: true });
    expect(brokerChip(LOGIN)).toEqual({ text: "Upstox · log in", tone: "warn", connected: false });
    expect(brokerChip({ kind: "loading" })).toBeNull();
    expect(partlyConnected([{ needs_login: false }, { needs_login: true }])).toBe("2 brokers · 1 needs login");
    expect(brokerChip(CONNECTED, [{ needs_login: false }, { needs_login: true }])).toEqual({ text: "2 brokers · 1 needs login", tone: "warn", connected: true });
    expect(brokerChip(CONNECTED, [{ needs_login: false }])?.text).toBe("Upstox connected");
    expect(partlyConnected([{ needs_login: true }, { needs_login: true }])).toBeNull();      // none usable: the login row says it
    expect(partlyConnected([{ needs_login: false }])).toBeNull();
  });

  it("one OI line per index: PCR and the first clause of the banner, nothing for no history", () => {
    const b = (underlying: string, pcr: number | null, message: string, direction = "BULLISH") =>
      ({ underlying, date: "", market_open: true, stale: false, age_minutes: 1, stale_after_minutes: 15,
        banner: { pcr, message, direction } }) as never;
    expect(oiLines([b("NIFTY", 1.123, "Put writing rising → avoid shorting calls; NIFTY bias bullish"),
      b("BANKNIFTY", 0.86, "Insufficient data — history builds through the session", "NEUTRAL"), b("FINNIFTY", 1, "x")]))
      .toEqual({ "NIFTY 50": "PCR 1.12 · Put writing rising", "NIFTY BANK": "PCR 0.86" });
    expect(oiLines(null)).toEqual({});
  });

  it("India VIX in words; a remembered level says when it was taken; no price is never 0", () => {
    expect(vixNote(13.42)).toBe("Calm: 12 to 16");
    expect(vixNote(11)).toBe("Very calm: below 12");
    expect(vixNote(22)).toBe("Nervous: above 20");
    expect(vixNote(null)).toBeNull();
    const idx = (i: Partial<StripIndex>): StripIndex => ({ key: "NIFTY 50", label: "NIFTY 50", symbol: "NIFTY 50", exchange: "NSE", ltp: 1, prev_close: 1,
      change: 0, change_pct: 0, as_of: null, stale: false, source: "live", missing: false, ...i });
    expect(indexCaption(idx({}), NOW)).toBeNull();
    expect(indexCaption(idx({ source: "memory", as_of: "2026-10-19T04:00:00Z" }), NOW)).toBe("As of 09:30");
    expect(indexCaption(idx({ source: "memory", as_of: "2026-10-16T10:00:00Z" }), NOW)).toBe("As of 16 Oct");
    expect(indexCaption(idx({ ltp: null, missing: true, source: null }), NOW)).toBe("No price yet");
    expect(signTone(1)).toBe("up");
    expect(signTone(-0.5)).toBe("down");
    expect(signTone(0)).toBe("neutral");
    expect(signTone(null)).toBe("neutral");
  });

  it("today's P&L: booked plus open, only when every open symbol has a live price in rupees", () => {
    const sym = (symbol: string) => ({ symbol, positions: 1, quantity: 1, notional: 1, pct_of_capital: 0, unrealised_pnl: 0, risk_at_stop: 0, strategies: [] });
    // Every open symbol priced live: the day's P&L is booked + open.
    expect(todayPnl(exposure({ realised_today: 2540, unrealised_pnl: 4306.5, open_positions: 2, price_source: "ltp",
      by_symbol: [sym("A"), sym("B")], priced_symbols: ["A", "B"] }))).toEqual({ total: 6846.5, booked: 2540, open: 4306.5 });
    // No open positions: open is 0, so the booked figure IS the day's P&L.
    expect(todayPnl(exposure({ realised_today: 1200, unrealised_pnl: 0, open_positions: 0, price_source: "entry" })))
      .toEqual({ total: 1200, booked: 1200, open: 0 });
    // Before the broker login (valued at entry): the headline is unknown, the booked part stays.
    expect(todayPnl(exposure({ realised_today: 100, unrealised_pnl: 0, open_positions: 2, price_source: "entry", by_symbol: [sym("A")] })))
      .toEqual({ total: null, booked: 100, open: null });
    // Only some symbols priced: unknown too.
    expect(todayPnl(exposure({ realised_today: 100, unrealised_pnl: 50, open_positions: 2, price_source: "ltp",
      by_symbol: [sym("A"), sym("B")], priced_symbols: ["A"] }))).toEqual({ total: null, booked: 100, open: null });
    // A symbol converted from another currency: unknown.
    expect(todayPnl(exposure({ realised_today: 0, unrealised_pnl: 50, open_positions: 1, price_source: "ltp", by_symbol: [sym("BTCUSDT")],
      priced_symbols: ["BTCUSDT"], fx_rates_used: { "USD->INR": 84 } })).open).toBeNull();
    // A missing rate (the symbol stays in its own currency): unknown, and the headline with it.
    expect(todayPnl(exposure({ realised_today: 10, unrealised_pnl: 50, open_positions: 1, price_source: "ltp", by_symbol: [sym("BTCUSDT")],
      priced_symbols: ["BTCUSDT"], fx_missing: ["USD->INR"] }))).toEqual({ total: null, booked: 10, open: null });
    expect(todayPnl(null)).toEqual({ total: null, booked: null, open: null });
  });
});

describe("UI2-2 open positions", () => {
  const positions = [trade(1, "NIFTY 24800 CE", "LONG", 150, 121.8, "PAPER", 75), trade(2, "BANKNIFTY FUT", "SHORT", 35, 54000, "PAPER", 35),
    trade(3, "RELIANCE", "LONG", 50, 2962.2, "LIVE"), trade(4, "TCS", "LONG", 10, 4000)];
  const exp = exposure({ price_source: "ltp", priced_symbols: ["NIFTY 24800 CE", "BANKNIFTY FUT", "RELIANCE"], by_symbol: [
    { symbol: "NIFTY 24800 CE", positions: 1, quantity: 150, notional: 21390, pct_of_capital: 0, unrealised_pnl: 3120, risk_at_stop: 0, strategies: [] },
    { symbol: "BANKNIFTY FUT", positions: 1, quantity: -35, notional: 1888113.5, pct_of_capital: 0, unrealised_pnl: 1886.5, risk_at_stop: 0, strategies: [] },
    { symbol: "RELIANCE", positions: 1, quantity: 50, notional: 147410, pct_of_capital: 0, unrealised_pnl: -700, risk_at_stop: 0, strategies: [] },
    { symbol: "TCS", positions: 1, quantity: 10, notional: 40000, pct_of_capital: 0, unrealised_pnl: 0, risk_at_stop: 0, strategies: [] },
  ] });

  it("prices each position from the exposure; an unpriced symbol shows no P&L (never at its entry price)", () => {
    expect(symbolPrices(positions, exp)).toEqual({ "NIFTY 24800 CE": 142.6, "BANKNIFTY FUT": 53946.1, RELIANCE: 2948.2 });
    const rows = positionRows(positions, exp);
    expect(rows[0]).toMatchObject({ symbol: "NIFTY 24800 CE", mode: "Paper", side: "Buy", detail: "Buy · 2 lots (150) · avg 121.80" });
    expect(rows[0].pnl).toBeCloseTo(3120, 6);
    expect(rows[0].pnlPct).toBeCloseTo(17.08, 2);
    expect(rows[1]).toMatchObject({ side: "Sell", detail: "Sell · 1 lot (35) · avg 54,000.00" });
    expect(rows[1].pnl).toBeCloseTo(1886.5, 6);
    expect(rows[2]).toMatchObject({ mode: "Live", detail: "Buy · 50 shares · avg 2,962.20" });
    expect(rows[2].pnl).toBeCloseTo(-700, 6);
    expect(rows[3]).toMatchObject({ ltp: null, pnl: null, pnlPct: null });
    expect(positionRows(positions, { ...exp, price_source: "entry" }).every((r) => r.pnl === null)).toBe(true);
    // A conversion from another currency: no price is derived (an INR value against a foreign-currency entry).
    expect(symbolPrices(positions, { ...exp, fx_rates_used: { "USD->INR": 84 } })).toEqual({});
    expect(symbolPrices(positions, { ...exp, fx_missing: ["USD->INR"] })).toEqual({});
  });

  it("shows at most five", () => {
    expect(positionRows(Array.from({ length: 8 }, (_, i) => trade(i, `S${i}`, "LONG", 1, 1)), null)).toHaveLength(5);
  });
});

describe("UI2-2 active signals (today's entry proposals)", () => {
  const actions = [
    entry(1, "2026-10-19T04:35:00", "EXECUTED", { symbol: "HDFCBANK" }),
    entry(2, "2026-10-19T05:12:00", "PROPOSED"),
    entry(3, "2026-10-19T05:06:00", "PROPOSED", { symbol: "NIFTY BANK", direction: "SHORT", source: "scan", signal: {} }),
    entry(4, "2026-10-18T09:00:00", "PROPOSED", { symbol: "YESTERDAY" }),
    entry(5, "2026-10-19T05:20:00", "REJECTED"),
    { ...entry(6, "2026-10-19T05:21:00", "PROPOSED"), action: "PAUSE_DEPLOYMENT" as const },
  ];

  it("today's only, waiting first then newest, with the levels, the source and the status", () => {
    const s = signalsToday(actions, NOW);
    expect(s.count).toBe(4);
    expect(s.waiting).toBe(2);
    expect(s.taken).toBe(1);
    expect(s.rows.map((r) => r.id)).toEqual([2, 3, 5]);
    expect(s.rows[0]).toMatchObject({ time: "10:42", symbol: "NIFTY 50", side: "Long", setup: "Opening-range breakout, retest held",
      levels: "Entry 24,790.00 · Stop 24,720.00 · Target 24,930.00", source: "Autopilot · Orb nifty", status: "Waiting for you", waiting: true });
    expect(s.rows[1]).toMatchObject({ side: "Short", setup: "Orb nifty", source: "Scan · Orb nifty" });
    expect(s.rows[2].status).toBe("Skipped");
    expect(signalsToday(null, NOW)).toEqual({ rows: [], count: 0, waiting: 0, taken: 0 });
  });
});

describe("UI2-2 needs attention", () => {
  const base: AttentionInput = { nowMs: NOW, broker: CONNECTED, brokerRows: [upstox], pending: [], deployments: [], risk: null,
    exposure: null, unread: [], status: { maintenance_mode: false, maintenance_message: null, disabled_brokers: [] }, emailVerified: true,
    approvalsPath: "/copilot/watchtower" };

  it("nothing waiting: no rows (the card collapses to 'All clear')", () => {
    expect(attentionRows(base)).toEqual([]);
  });

  it("orders the rows by urgency, says what happened, and gives one action each; at most five", () => {
    const rows = attentionRows({
      ...base, broker: LOGIN,
      pending: [entry(2, "2026-10-19T05:12:00", "PROPOSED", {}, "2026-10-19T05:38:00"),
        entry(3, "2026-10-19T05:06:00", "PROPOSED", { symbol: "NIFTY BANK", direction: "SHORT", source: "scan" }, "2026-10-19T05:46:00"),
        entry(7, "2026-10-19T05:06:00", "PROPOSED", {}, null), { ...entry(8, "2026-10-19T05:00:00", "PROPOSED"), snoozed_until: "2026-10-19T06:30:00Z" }],
      deployments: [{ strategy_id: "mean_reversion", symbol: "BANKNIFTY", status: "PAUSED", pause_reason: "It hit your rule of 3 losses in a row at 10:21.", last_error: null } as Deployment],
      risk: { lockout: { kind: "DAILY_LOSS", reason: "Today's loss reached your limit.", pnl: -3000, limit: 3000, locked_at: "", until: "" } } as never,
      exposure: exposure({ warnings: ["Gross exposure is 4x your capital."] }),
      unread: [{ id: 1, title: "Your alert: NIFTY 50 crossed 24,800", created_at: "2026-10-19T05:11:00" } as NotificationEntry],
      emailVerified: false,
    });
    expect(rows.map((r) => r.id)).toEqual(["login", "approvals", "paused", "lockout", "exposure"]);
    expect(rows[0]).toMatchObject({ title: "Log in to Upstox", tone: "warn", action: { label: "Log in", login: true } });
    expect(rows[1].title).toBe("3 trades are waiting for your approval");              // the snoozed one waits
    expect(rows[1].detail).toBe("NIFTY 50 long from Autopilot · NIFTY BANK short from Scan · 1 more · first one expires in 8 min");
    expect(rows[1].action).toEqual({ label: "Review 3 trades", to: "/copilot/watchtower" });
    expect(rows[2]).toMatchObject({ title: "Autopilot paused “Mean reversion BANKNIFTY”", action: { to: "/deployments" } });
    expect(rows[3]).toMatchObject({ title: "New trades are paused for today", detail: "Today's loss reached your limit." });
  });

  it("names unseen alerts, LIVE paused by the operator, maintenance and an unverified e-mail", () => {
    const rows = attentionRows({ ...base, unread: [{ id: 1, title: "A", created_at: "2026-10-19T05:11:00" }, { id: 2, title: "B", created_at: "2026-10-19T05:15:00" }] as NotificationEntry[],
      status: { maintenance_mode: true, maintenance_message: null, disabled_brokers: ["angel_one"] }, emailVerified: false,
      brokerRows: [upstox, { ...upstox, broker_name: "zerodha", needs_login: true }] });
    expect(rows.map((r) => r.title)).toEqual(["2 brokers · 1 needs login", "2 alerts you have not seen", "Live trading is paused on Angel One",
      "Maintenance: new trades are paused", "Verify your email"]);
    expect(rows[1].detail).toBe("Newest: B");
    expect(rows[2].action).toBeUndefined();
  });

  it("never shows developer wording", () => {
    const rows = attentionRows({ ...base, broker: LOGIN, deployments: [{ strategy_id: "x_y", symbol: "S", status: "ACTIVE", last_error: "Traceback 503" } as Deployment] });
    for (const r of rows) expect(`${r.title} ${r.detail}`).not.toMatch(/traceback|503|registry|adapter|flag|backend/i);
  });
});

describe("UI2-2 today tiles", () => {
  it("four tiles from the existing answers", () => {
    const t = todayTiles(exposure({ realised_today: 2540 }), { closed_trades: 41, win_rate: 58 } as never,
      { today: { used: 0.385, limit: 10000 } } as never, { rows: [], count: 3, waiting: 2, taken: 1 });
    expect(t).toMatchObject({ booked: 2540, winRate: 58, closedTrades: 41, riskLimit: 10000 });
    expect(t.riskUsedPct).toBeCloseTo(38.5, 6);
    expect(t.riskUsed).toBeCloseTo(3850, 6);
    const none = todayTiles(null, { closed_trades: 0, win_rate: 0 } as never, null, { rows: [], count: 0, waiting: 0, taken: 0 });
    expect(none).toMatchObject({ booked: null, winRate: null, riskUsedPct: null, riskLimit: null });
  });

  it("days are IST days", () => {
    expect(istDay(Date.parse("2026-10-19T19:00:00Z"))).toBe("2026-10-20");
  });
});
