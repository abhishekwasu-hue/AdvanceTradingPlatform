import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { hasCallWords } from "../chartAnalyze/signalLanguage";
import { liveChain } from "../optionChain/chain.fixture";
import pageSource from "../optionChain/OptionChainPageV2.tsx?raw";
import positionsSource from "../trading/PositionsV2.tsx?raw";
import type { AlertRule } from "../alerts/rules";
import type { PositionGreeks } from "../types";
import chainTradingSource from "./ChainTradingV2.tsx?raw";
import { COCKPIT_NOTE, alertsSet, cockpitRiskLine, daysBetween, summarise, upcomingExpiries, type CockpitUnit } from "./cockpit";
import { CockpitView } from "./CockpitV2";
import cockpitSource from "./CockpitV2.tsx?raw";
import {
  TRADE_NOTE, setupChainHref, setupTrade, setupTradeNote, type RiskBudget, type SetupItem, type SetupStrikes,
} from "./insights";
import { SetupPanelV2 } from "./InsightsV2";
import { DRAFT_NOTE, closedHow, legLine, placedByLine, type JournalDraft } from "./journal";
import { JournalDraftsView } from "./JournalDraftsV2";
import journalSource from "./JournalDraftsV2.tsx?raw";
import type { PosLeg } from "./positionInsights";

/**
 * F&O workspace 8b, slice 3 (owner order sections 8b b, i, k and 9), from fixtures: "Trade this setup" fills the EXISTING
 * ticket / basket from the user's own deployment settings and places nothing; the cockpit is read only (no control
 * trades, the figures are his positions' own); the journal draft is facts plus his signal's structure words, no advice.
 * Made-up numbers only.
 */
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, "\"").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const ADVICE = /\b(should|recommend\w*|advice|advise|best|target\w*|guarantee\w*|sure[- ]?shot|bullish|bearish)\b/i;
const noop = () => undefined;

const ITEM: SetupItem = {
  id: 7, settings: "Bull put spread · main strike 1 strike out of the money · wing 2 strikes away · nearest expiry", expiry: "2026-11-03",
  legs: [{ label: "Main strike", right: "PE", strike: 24400, ratio: 1, role: "short" }, { label: "Wing", right: "PE", strike: 24200, ratio: 1, role: "long" }],
  note: null, lots: 2, stop_pct: 50,
};
const ANSWER: SetupStrikes = {
  source: "your_settings", signal: "action:12", underlying: "NIFTY", setup: "Made-up setup", side: "bull_put",
  reference: { text: "the current price", price: 24480 }, message: null, items: [ITEM],
};

describe("8b-b Trade this setup: the existing ticket / basket, pre-filled from his own settings", () => {
  const live = liveChain();

  it("two legs open the basket with his lots, the chain's prices and his sold-leg stop", () => {
    const t = setupTrade(ITEM, "NIFTY", "2026-11-03", live.rows);
    expect(t.kind).toBe("basket");
    if (t.kind !== "basket") return;
    expect(t.legs.map((l) => [l.strike, l.right, l.side, l.lots, l.price])).toEqual([[24400, "PE", "SELL", 2, 150], [24200, "PE", "BUY", 2, 50]]);
    expect(t.legs[0].stopLoss).toBe(225);              // 50% above 150, on the tick
    expect(t.legs[1].stopLoss).toBeNull();             // a held leg needs none
    expect(t.lotsFromSettings).toBe(true);
    expect(t.stopsToSet).toBe(0);
  });

  it("settings without lots or a stop start at 1 lot and ask him for the exit level - nothing is made up", () => {
    const t = setupTrade({ ...ITEM, lots: null, stop_pct: null }, "NIFTY", "2026-11-03", live.rows);
    if (t.kind !== "basket") throw new Error(t.kind);
    expect(t.legs.map((l) => l.lots)).toEqual([1, 1]);
    expect(t.legs[0].stopLoss).toBeNull();
    expect(setupTradeNote(t)).toContain("Set the exit level of the short leg in the basket");
    expect(setupTradeNote(t)).toContain("each leg starts at 1 lot");
  });

  it("one leg opens the order ticket on that strike and side; a ratio leg keeps its ratio", () => {
    const single = setupTrade({ ...ITEM, legs: [{ label: "Main strike", right: "PE", strike: 24400, ratio: 1, role: "long" }] }, "NIFTY", "2026-11-03", live.rows);
    expect(single).toMatchObject({ kind: "ticket", side: "BUY", target: { underlying: "NIFTY", expiry: "2026-11-03", strike: 24400, right: "PE", ltp: 150 } });
    const ratio = setupTrade({ ...ITEM, legs: [{ label: "Main strike", right: "CE", strike: 24500, ratio: 1, role: "long" },
      { label: "Second strike (× 2)", right: "CE", strike: 24700, ratio: 2, role: "short" }] }, "NIFTY", "2026-11-03", live.rows);
    if (ratio.kind !== "basket") throw new Error(ratio.kind);
    expect(ratio.legs.map((l) => [l.side, l.lots])).toEqual([["BUY", 2], ["SELL", 4]]);
  });

  it("another expiry, a strike not on screen or an unreadable answer opens nothing and says why", () => {
    expect(setupTrade(ITEM, "NIFTY", "2026-11-10", live.rows)).toMatchObject({ kind: "none" });
    expect(setupTrade({ ...ITEM, legs: [{ ...ITEM.legs![0], strike: 99999 }] }, "NIFTY", "2026-11-03", live.rows)).toMatchObject({ kind: "none" });
    expect(setupTrade({ ...ITEM, legs: [{ label: "Main strike", right: "PE", strike: 24400, ratio: 1 }] }, "NIFTY", "2026-11-03", live.rows)).toMatchObject({ kind: "none" });
    expect(setupTrade({ ...ITEM, legs: null, note: "The option list is not loaded yet." }, "NIFTY", "2026-11-03", live.rows))
      .toEqual({ kind: "none", problem: "The option list is not loaded yet." });
  });

  it("the panel's button only opens; its words carry no call or advice wording", () => {
    const html = renderToStaticMarkup(<SetupPanelV2 state={{ status: "ready", data: ANSWER }} symbol="NIFTY" expiry="2026-11-03" onExpiry={noop} onClear={noop}
      tradeOf={(i) => setupTrade(i, "NIFTY", "2026-11-03", live.rows)} onTrade={noop} />);
    const words = text(html);
    expect(words).toContain("Trade this setup");
    expect(words).toContain(TRADE_NOTE);
    expect(html.match(/<button/g)?.length).toBe(2);               // "Hide marks" and "Trade this setup"
    expect(hasCallWords(words)).toBe(false);
    expect(words).not.toMatch(ADVICE);
    // Without the PAPER account the page passes no `onTrade`: the panel is exactly slice 1's.
    const plain = renderToStaticMarkup(<SetupPanelV2 state={{ status: "ready", data: ANSWER }} symbol="NIFTY" expiry="2026-11-03" onExpiry={noop} onClear={noop} />);
    expect(plain.match(/<button/g)?.length).toBe(1);
  });

  it("the signal card's link opens the chain with the pre-fill asked for once", () => {
    expect(setupChainHref({ id: "action:12", symbol: "NIFTY 50", direction: "LONG" }, true)).toBe("/option-chain?underlying=NIFTY&signal=action%3A12&trade=1");
    expect(setupChainHref({ id: "action:12", symbol: "NIFTY 50", direction: null }, true)).toBeNull();
    expect(pageSource).toContain("next.delete(TRADE_PARAM)");      // a reload never reopens it
    expect(pageSource).toContain("autoDone.current = true");
  });

  it("nothing automatic places an order: tradeSetup only fills and opens; placing stays the ticket's / basket's own button", () => {
    const body = chainTradingSource.slice(chainTradingSource.indexOf("function tradeSetup"), chainTradingSource.indexOf("if (!enabled) return"));
    expect(body).toContain("basketStore.replace(t.legs)");
    expect(body).toContain("setTicket(");
    expect(body).not.toMatch(/\.place\(|\.execute\(|\.preview\(|exitNow/);
    expect(body).toContain("confirmBox.ask(\"Replace your basket?\"");   // his basket is replaced only on his yes (UI-28 U3.3: in-app)
    // The journal note is sent only after HIS placement, from the ticket's / basket's onPlaced.
    expect(chainTradingSource).toMatch(/onPlaced=\{\(r\) => \{ reloadHeld\(\); noteSignal\(\[r\.trade_id\]\)/);
  });
});

// --- 8b-i: the cockpit -------------------------------------------------------------------------------------------------

const pos = (id: number, over: Partial<PosLeg>): PosLeg => ({
  id, label: `NIFTY ${id}`, symbol: `NIFTY${id}`, exchange: "NFO", underlying: "NIFTY", spotSymbol: "NIFTY 50", spotExchange: "NSE", expiry: "2026-11-03",
  strike: 24900, right: "CE", side: "SELL", units: 75, lotSize: 75, entry: 80, ltp: 60, exitLevel: null, profitLevel: null, ...over,
});
const SPREAD: CockpitUnit = { key: "basket:1", title: "Basket · 2 legs", pnl: 1200,
  legs: [pos(1, { strike: 24900, side: "SELL", entry: 80, ltp: 60 }), pos(2, { strike: 25000, side: "BUY", entry: 45, ltp: 41 })] };
const NAKED: CockpitUnit = { key: "trade:3", title: "NIFTY 27 OCT 24300 PE", pnl: -300,
  legs: [pos(3, { strike: 24300, right: "PE", side: "SELL", entry: 50, ltp: 54, expiry: "2026-10-28" })] };
const LONG: CockpitUnit = { key: "trade:4", title: "NIFTY 27 OCT 24500 CE", pnl: 150,
  legs: [pos(4, { strike: 24500, side: "BUY", entry: 100, ltp: 102 })] };
const BUDGET: RiskBudget = { capital: 1_000_000, per_trade: { amount: 10_000, pct: 1 }, daily: { amount: 50_000, pct: 5 }, source: "Your risk settings", blocks: false };

describe("8b-i the cockpit: his own open PAPER F&O positions, read only", () => {
  it("adds up the P&L, the premium kept over the short-premium lines and the most they can lose at expiry", () => {
    const s = summarise([SPREAD, LONG]);
    expect(s.positions).toBe(3);
    expect(s.lines).toBe(2);
    expect(s.pnl).toBe(1350);
    // Received (80 - 45) x 75 = 2,625; closing costs (60 - 41) x 75 = 1,425; kept 1,200 = 45.7%.
    expect(s.premium).toEqual({ received: 2625, kept: 1200, pct: 45.7, lines: 1 });
    // The spread loses at most (100 - 35) x 75 = 4,875; the long call at most its 7,500 premium.
    expect(s.worst).toBe(12375);
    expect(s.unbounded).toBe(false);
    expect(summarise([SPREAD, NAKED]).unbounded).toBe(false);   // a short put's loss is bounded at zero
    const nakedCall: CockpitUnit = { ...NAKED, legs: [pos(5, { right: "CE", side: "SELL" })] };
    expect(summarise([nakedCall])).toMatchObject({ unbounded: true, worst: null });
    expect(summarise([{ ...SPREAD, pnl: null }]).pnl).toBeNull();
  });

  it("the risk line is beside his own limits, in plain words, information only", () => {
    const line = cockpitRiskLine(summarise([SPREAD, LONG]), BUDGET);
    expect(line?.tone).toBe("info");
    expect(line?.text).toContain("Within your daily loss limit of");
    expect(line?.note).toContain("does not hold back the order or any exit");
    expect(cockpitRiskLine(summarise([SPREAD]), { ...BUDGET, daily: { amount: 1000, pct: 0.1 } })?.tone).toBe("warn");
    expect(cockpitRiskLine(summarise([{ ...NAKED, legs: [pos(5, { right: "CE", side: "SELL" })] }]), BUDGET)?.text).toContain("no fixed limit");
    expect(cockpitRiskLine(summarise([SPREAD]), null)).toBeNull();
  });

  it("the expiries come soonest first; the alerts are his active ones on these contracts or their underlying", () => {
    const e = upcomingExpiries([SPREAD, NAKED], "2026-10-27");
    expect(e.map((x) => [x.expiry, x.daysLeft, x.legs])).toEqual([["2026-10-28", 1, 1], ["2026-11-03", 7, 2]]);
    expect(e[0].when).toContain("Expires tomorrow");
    expect(daysBetween("2026-10-27", "2026-11-03")).toBe(7);
    const rule = (id: number, symbol: string | null, over: Partial<AlertRule> = {}): AlertRule => ({
      id, name: `rule ${id}`, kind: "instrument", screen_id: null, symbol, condition: "close >= 1", base_tf: "1m", priority: "normal",
      cooldown_minutes: 60, mode: "instant", digest_every: "hourly", status: "active", expires_at: null, ...over,
    } as AlertRule);
    const got = alertsSet([rule(1, "NIFTY 50"), rule(2, "NIFTY1"), rule(3, "BANKNIFTY"), rule(4, "NIFTY2", { status: "paused" }), rule(5, null, { kind: "screen" })], [SPREAD]);
    expect(got.map((r) => r.id)).toEqual([1, 2]);
  });

  it("the card has no control at all and says each position keeps its own Exit", () => {
    const greeks = { net: { net_delta: -12.5, net_gamma: -0.01, net_theta: 310, net_vega: -95 } } as PositionGreeks;
    const units = [SPREAD, LONG];
    const html = renderToStaticMarkup(<MemoryRouter><CockpitView units={units} summary={summarise(units)} greeks={greeks}
      line={cockpitRiskLine(summarise(units), BUDGET)} expiries={upcomingExpiries(units, "2026-10-27")} alerts={[]} /></MemoryRouter>);
    expect(html).not.toContain("<button");
    expect(html).not.toContain("<a ");
    const words = text(html);
    expect(words).toContain("F&O at a glance");
    expect(words).toContain(COCKPIT_NOTE);
    expect(words).toContain("Premium kept so far");
    expect(words).toContain("Net theta");
    expect(hasCallWords(words)).toBe(false);
    expect(words).not.toMatch(ADVICE);
    // It reads only: no write call in the component.
    expect(cockpitSource).not.toMatch(/method: "(POST|PUT|PATCH|DELETE)"|\.place\(|exitNow|createAlertRule/);
    // Mounted on the positions page only while F&O PAPER trading is on, above the list that keeps every Exit.
    expect(positionsSource).toContain("const cockpit = fnoOn ? cockpitUnits(rows, byId, actions, fno.groups) : [];");
    expect(positionsSource.indexOf("<CockpitV2")).toBeLessThan(positionsSource.indexOf("title=\"Open positions\""));
  });
});

// --- 8b-k: the auto journal --------------------------------------------------------------------------------------------

const DRAFT: JournalDraft = {
  key: "basket:abc", kind: "basket", title: "Basket · 2 legs", underlying: "NIFTY", trade_ids: [11, 12],
  opened_at: "2026-10-21T04:45:00+00:00", closed_at: "2026-10-21T08:50:00+00:00",
  legs: [
    { trade_id: 11, symbol: "NIFTYX24400PE", side: "short", quantity: 150, lot_size: 75, entry_price: 150, entry_at: null, exit_price: 120, exit_at: null, exit_reason: "MANUAL_EXIT", pnl: 4500, charges: 40 },
    { trade_id: 12, symbol: "NIFTYX24200PE", side: "long", quantity: 150, lot_size: 75, entry_price: 50, entry_at: null, exit_price: 40, exit_at: null, exit_reason: "MANUAL_EXIT", pnl: -1500, charges: 40 },
  ],
  pnl: 3000, charges: 80, placed_by: "you", strategy: null,
  signal: { setup: "Made-up setup", words: ["The pullback reached a seller area", "A handover is forming inside the area"] }, notes: null, status: "draft",
};

describe("8b-k the auto journal: a draft from facts and his signal's structure words", () => {
  it("each leg is a line of facts; how it closed is as recorded; who placed it", () => {
    expect(legLine(DRAFT.legs[0])).toBe("Short NIFTYX24400PE · 150 units (2 lots) · in 150.00 · out 120.00 · +₹4,500.00");
    expect(closedHow(DRAFT)).toBe("Manual exit");
    expect(placedByLine(DRAFT)).toBe("Placed by you");
    expect(placedByLine({ placed_by: "paper_bot", strategy: "Made-up bot" })).toBe("Placed by your paper bot (Made-up bot)");
  });

  it("the card shows the draft, his signal's words and a notes field; saving is its only button", () => {
    const html = renderToStaticMarkup(<JournalDraftsView state={{ status: "ok", data: { mode: "PAPER", days: 14, items: [DRAFT, { ...DRAFT, key: "trade:9", signal: null, status: "noted", notes: "Waited for the area." }] } }}
      onSave={async () => undefined} />);
    const words = text(html);
    expect(words).toContain("Your F&O journal");
    expect(words).toContain("1 draft waits for your notes");
    expect(words).toContain("From your signal: Made-up setup");
    expect(words).toContain("The pullback reached a seller area");
    expect(words).toContain(DRAFT_NOTE);
    expect(html.match(/<button/g)?.length).toBe(2);              // "Save notes" on each draft, nothing else
    expect(html).toContain("data-draft-status=\"noted\"");
    expect(words).not.toMatch(ADVICE);
    const signalText = DRAFT.signal ? [DRAFT.signal.setup ?? "", ...DRAFT.signal.words].join(" ") : "";
    expect(hasCallWords(signalText)).toBe(false);
    // Saving writes the trades' own notes field only.
    expect(journalSource).not.toMatch(/\.place\(|exitNow|basket/);
  });

  it("no draft, still loading or unreadable: no card at all; the card is mounted with F&O PAPER trading", () => {
    expect(renderToStaticMarkup(<JournalDraftsView state={{ status: "ok", data: { mode: "PAPER", days: 14, items: [] } }} onSave={async () => undefined} />)).toBe("");
    expect(renderToStaticMarkup(<JournalDraftsView state={{ status: "error" }} onSave={async () => undefined} />)).toBe("");
    expect(renderToStaticMarkup(<JournalDraftsView state={{ status: "loading" }} onSave={async () => undefined} />)).toBe("");
    expect(positionsSource).toMatch(/\{fnoOn && \(\s*<Suspense fallback=\{null\}>\s*<JournalDraftsV2/);
  });
});
