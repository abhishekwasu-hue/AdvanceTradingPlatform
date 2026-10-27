import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { hasCallWords } from "../chartAnalyze/signalLanguage";
import { rows as chainRows } from "../optionChain/chain.fixture";
import { heldRows } from "../trading/model";
import positionsSource from "../trading/PositionsV2.tsx?raw";
import { PositionRowV2, type RowActions } from "../trading/PositionsV2";
import type { TradeRecord } from "../types";
import type { BasketEvaluation } from "./api";
import panelSource from "./BasketPanelV2.tsx?raw";
import type { NewLeg } from "./basket";
import type { ContractFacts } from "./contracts";
import { COMPARE_NOTE, ChangeEditor, CompareView, basketColumn, compareChoices, figureRows, marginText, type CompareColumn } from "./CompareV2";
import compareSource from "./CompareV2.tsx?raw";
import {
  ALERT_ONLY, alertBody, alertChoices, alertSavedText, captureBar, changeSide, heldAsks, heldPayoff, keptChoice, netPremium, outcome, pnlAt, posLeg,
  premiumCapture, priceGrid, sentenceInput, type PayoffLeg, type PosLeg,
} from "./positionInsights";
import { AlertChoicesView, CaptureLineV2, captureText } from "./PositionInsightsV2";
import insightsSource from "./PositionInsightsV2.tsx?raw";
import { payoffSentence } from "./basketModel";

/**
 * F&O workspace 8b, slice 2 (owner order sections 8b e, f, g, j and 9): the premium kept, the alerts on the user's own
 * levels, the comparison of a change and of two baskets - all from made-up fixtures (no real account, price or order).
 * The maths is checked exactly; the words carry no buy / sell / target / stop wording and no advice; an alert is an
 * instrument rule that only notifies; nothing here reaches an order path or an exit.
 */
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, "\"").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const ADVICE = /\b(should|recommend\w*|advice|advise|best|target\w*|guarantee\w*|sure[- ]?shot|bullish|bearish)\b/i;
const clean = (s: string) => { expect(hasCallWords(s), s).toBe(false); expect(ADVICE.test(s), s).toBe(false); };

const EXP = "2026-10-27";
const L = (right: PayoffLeg["right"], side: PayoffLeg["side"], strike: number, price: number, units = 75, expiry = EXP): PayoffLeg =>
  ({ right, side, strike, price, units, expiry });

// --- the payoff at expiry, exact -----------------------------------------------------------------------------------------

describe("8b-g / 8b-j: the outcome at expiry from the legs", () => {
  it("one short call: keeps the premium below the strike, breakeven strike + premium, no limit above", () => {
    const legs = [L("CE", "SELL", 24900, 80)];
    expect(pnlAt(legs, 24800)).toBe(6000);
    expect(pnlAt(legs, 25080)).toBe(-7500);
    expect(outcome(legs)).toEqual({ maxProfit: 6000, maxLoss: null, unboundedProfit: false, unboundedLoss: true, breakevens: [24980] });
    expect(netPremium(legs)).toBe(6000);
  });

  it("a bear call spread: the credit, the width less the credit, one breakeven", () => {
    const legs = [L("CE", "SELL", 24900, 80), L("CE", "BUY", 25000, 45)];
    expect(outcome(legs)).toEqual({ maxProfit: 2625, maxLoss: -4875, unboundedProfit: false, unboundedLoss: false, breakevens: [24935] });
    expect(netPremium(legs)).toBe(2625);
  });

  it("an iron condor: two breakevens, both limits", () => {
    const legs = [L("PE", "BUY", 24300, 20), L("PE", "SELL", 24400, 40), L("CE", "SELL", 24900, 45), L("CE", "BUY", 25000, 25)];
    expect(outcome(legs)).toEqual({ maxProfit: 3000, maxLoss: -4500, unboundedProfit: false, unboundedLoss: false, breakevens: [24360, 24940] });
  });

  it("a long put and a long future", () => {
    expect(outcome([L("PE", "BUY", 24500, 100)])).toEqual({ maxProfit: 1_830_000, maxLoss: -7500, unboundedProfit: false, unboundedLoss: false, breakevens: [24400] });
    const fut = outcome([L("FUT", "BUY", 0, 24800)]);
    expect(fut?.unboundedProfit).toBe(true);
    expect(fut?.breakevens).toEqual([24800]);
    expect(netPremium([L("FUT", "BUY", 0, 24800)])).toBe(0);                   // a future has no premium
  });

  it("legs on two expiries have no single outcome (and the sentence says so)", () => {
    const legs = [L("CE", "SELL", 24900, 80), L("CE", "BUY", 24900, 120, 75, "2026-11-24")];
    expect(outcome(legs)).toBeNull();
    const xs = priceGrid([legs], null);
    expect(payoffSentence(sentenceInput(legs, 0, xs), "NIFTY")).toMatch(/different dates/);
  });

  it("closing a leg books its P&L now: the change side is the kept legs shifted by it", () => {
    const legs = [L("CE", "BUY", 25000, 45)];
    // The short 24900 call closed at 72.50 after 80.00: +562.50 booked.
    const o = outcome(legs, 562.5);
    expect(o?.maxLoss).toBe(-2812.5);                                     // 562.50 - 45 x 75
    expect(o?.unboundedProfit).toBe(true);
    expect(o?.breakevens).toEqual([25037.5]);
    // Everything closed: a flat line at what was booked.
    expect(outcome([], 262.5)).toEqual({ maxProfit: 262.5, maxLoss: 262.5, unboundedProfit: false, unboundedLoss: false, breakevens: [] });
  });

  it("one price grid for both sides, bending at every strike", () => {
    const a = [L("CE", "SELL", 24900, 80)];
    const b = [L("CE", "SELL", 24935.5, 60)];
    const xs = priceGrid([a, b], 24850);
    expect(xs[0]).toBeCloseTo(24850 * 0.94, 1);
    expect(xs[xs.length - 1]).toBeCloseTo(24935.5 * 1.06, 1);
    expect(xs).toContain(24935.5);
    expect([...xs].sort((x, y) => x - y)).toEqual(xs);
  });
});

// --- the user's own positions ----------------------------------------------------------------------------------------------

const facts = (symbol: string, right: "CE" | "PE" | "FUT", strike: number | null, over: Partial<Extract<ContractFacts, { known: true }>> = {}): ContractFacts => ({
  symbol, exchange: "NFO", known: true, listed: true, underlying: "NIFTY", right, strike, expiry: EXP, lot_size: 75,
  label: `NIFTY 27 OCT ${strike ?? ""} ${right}`.replace("  ", " "), expired: false, days_left: 8, spot_symbol: "NIFTY 50", spot_exchange: "NSE",
  roll: null, roll_note: null, ...over,
});
const trade = (id: number, symbol: string, direction: "LONG" | "SHORT", entry: number, over: Partial<TradeRecord> = {}): TradeRecord => ({
  id, mode: "PAPER", symbol, strategy_id: "manual_fno", direction, entry_time: "2026-10-19T04:30:00", entry_price: entry, quantity: 75, stop_loss: 0,
  target1: null, target2: null, exit_time: null, exit_price: null, exit_reason: null, pnl: null, charges: 0, lot_size: 75, exchange: "NFO",
  instrument_kind: "OPTION", option_position: direction === "LONG" ? "BUY" : "WRITE", underlying_symbol: "NIFTY 50", expiry: EXP, ...over,
});

const SHORT_CE = "NIFTY 24900 CE 27 OCT 26";
const LONG_CE = "NIFTY 25000 CE 27 OCT 26";
const shortLeg = (ltp: number | null = 72.5, over: Partial<TradeRecord> = {}) => posLeg(trade(301, SHORT_CE, "SHORT", 80, over), facts(SHORT_CE, "CE", 24900), ltp) as PosLeg;
const longLeg = (ltp: number | null = 41) => posLeg(trade(302, LONG_CE, "LONG", 45), facts(LONG_CE, "CE", 25000), ltp) as PosLeg;

describe("the position as these features read it", () => {
  it("a short position is the SELL side; the exit and profit levels only where they belong", () => {
    const l = shortLeg(72.5, { stop_loss: 120, target1: 40 });
    expect(l).toMatchObject({ side: "SELL", strike: 24900, right: "CE", units: 75, lotSize: 75, entry: 80, ltp: 72.5, exitLevel: 120, profitLevel: 40,
      spotSymbol: "NIFTY 50", underlying: "NIFTY" });
    // A level on the wrong side of the entry is not his exit / profit level here (a short's exit sits above the entry).
    expect(shortLeg(72.5, { stop_loss: 1, target1: 95 })).toMatchObject({ exitLevel: null, profitLevel: null });
    expect(posLeg(trade(9, SHORT_CE, "SHORT", 80), facts(SHORT_CE, "CE", 24900, { expired: true }), 1)).toBeNull();
    expect(posLeg(trade(9, SHORT_CE, "SHORT", 80), { symbol: SHORT_CE, exchange: "NFO", known: false }, 1)).toBeNull();
  });

  it("whole lots for the engine's margin estimate, else none", () => {
    expect(heldAsks([shortLeg(), longLeg()])).toEqual([
      { underlying: "NIFTY", expiry: EXP, strike: 24900, right: "CE", side: "SELL", lots: 1, price: 80, stop_loss: null, iv: null },
      { underlying: "NIFTY", expiry: EXP, strike: 25000, right: "CE", side: "BUY", lots: 1, price: 45, stop_loss: null, iv: null },
    ]);
    expect(heldAsks([{ ...shortLeg(), units: 50 }])).toBeNull();
  });
});

// --- 8b-e: premium capture -------------------------------------------------------------------------------------------------

describe("8b-e: the premium kept so far", () => {
  it("a credit spread: received, the cost to close now, kept and the share", () => {
    const c = premiumCapture([shortLeg(72.5), longLeg(41)]);
    expect(c).toEqual({ received: 2625, closeNow: 2362.5, kept: 262.5, pct: 10 });
    expect(captureBar(c!)).toBe(10);
    expect(captureText(c!)).toBe("You have kept ₹263 of the ₹2,625 premium received (10%). Closing now costs ₹2,363.");
  });

  it("one sold option; the premium grown past what came in; prices still loading", () => {
    expect(premiumCapture([shortLeg(20)])).toEqual({ received: 6000, closeNow: 1500, kept: 4500, pct: 75 });
    const grown = premiumCapture([shortLeg(120), longLeg(50)])!;
    expect(grown).toEqual({ received: 2625, closeNow: 5250, kept: -2625, pct: -100 });
    expect(captureBar(grown)).toBe(0);
    expect(captureText(grown)).toBe("The premium has grown: closing now costs ₹5,250, ₹2,625 more than the ₹2,625 received.");
    const waiting = premiumCapture([shortLeg(null)])!;
    expect(waiting).toEqual({ received: 6000, closeNow: null, kept: null, pct: null });
    expect(captureText(waiting)).toMatch(/shows once the live prices load/);
  });

  it("nothing for a long option, a debit or a future", () => {
    expect(premiumCapture([longLeg()])).toBeNull();
    expect(premiumCapture([{ ...shortLeg(), entry: 30 }, longLeg()])).toBeNull();          // 30 in, 45 out: a debit
    expect(premiumCapture([{ ...shortLeg(), right: "FUT", strike: 0 }])).toBeNull();
    expect(premiumCapture([])).toBeNull();
  });

  it("the line renders in plain words, with no call or advice words", () => {
    const html = text(renderToStaticMarkup(<CaptureLineV2 legs={[shortLeg(), longLeg()]} daysLeft="8 days left" />));
    expect(html).toContain("Premium kept so far");
    expect(html).toContain("8 days left");
    clean(html);
    expect(renderToStaticMarkup(<CaptureLineV2 legs={[longLeg()]} />)).toBe("");
  });
});

// --- 8b-f: structure alerts on his own levels ------------------------------------------------------------------------------

describe("8b-f: alerts on the user's own levels - an alert only", () => {
  it("one short call: the breakeven and the short strike on the underlying, his exit and profit levels on the contract", () => {
    const choices = alertChoices([shortLeg(72.5, { stop_loss: 120, target1: 40 })], 24850);
    expect(choices.map((c) => [c.key, c.symbol, c.exchange, c.level, c.op])).toEqual([
      ["be-0", "NIFTY 50", "NSE", 24980, ">="],
      ["short-CE-24900", "NIFTY 50", "NSE", 24900, ">="],
      ["exit", SHORT_CE, "NFO", 120, ">="],
      ["profit", SHORT_CE, "NFO", 40, "<="],
    ]);
    expect(choices[0].label).toBe("NIFTY 50 reaches 24,980 (your breakeven)");
    choices.forEach((c) => { clean(c.label); clean(alertSavedText(c)); });
  });

  it("the direction is from where the price is now; unknown, from the side the level sits on", () => {
    const condor = [
      posLeg(trade(1, "A", "LONG", 20), facts("A", "PE", 24300), 18), posLeg(trade(2, "B", "SHORT", 40), facts("B", "PE", 24400), 35),
      posLeg(trade(3, "C", "SHORT", 45), facts("C", "CE", 24900), 40), posLeg(trade(4, "D", "LONG", 25), facts("D", "CE", 25000), 22),
    ] as PosLeg[];
    const none = alertChoices(condor, null);
    expect(none.filter((c) => c.key.startsWith("be-")).map((c) => [c.level, c.op])).toEqual([[24360, "<="], [24940, ">="]]);
    expect(none.filter((c) => c.key.startsWith("short-")).map((c) => [c.level, c.op])).toEqual([[24400, "<="], [24900, ">="]]);
    // The price already above the upper breakeven: reaching it again is from above.
    expect(alertChoices(condor, 24960).find((c) => c.key === "be-1")?.op).toBe("<=");
    // A group has no exit / profit level of one contract.
    expect(none.some((c) => c.key === "exit" || c.key === "profit")).toBe(false);
  });

  it("'I have kept N% of the premium': only on one sold option, N typed by the user (1-99), on the tick", () => {
    expect(keptChoice(shortLeg(), "50")).toMatchObject({ symbol: SHORT_CE, level: 40, op: "<=", why: "50% of the premium kept" });
    expect(keptChoice(shortLeg(), "33")?.level).toBe(53.6);
    expect(keptChoice(shortLeg(), "")).toBeNull();
    expect(keptChoice(shortLeg(), "0")).toBeNull();
    expect(keptChoice(shortLeg(), "100")).toBeNull();
    expect(keptChoice(longLeg(), "50")).toBeNull();
  });

  it("the alert is the existing instrument rule: a condition and a delivery, no action of any kind", () => {
    const c = alertChoices([shortLeg()], 24850)[0];
    const body = alertBody(c);
    expect(body).toEqual({
      name: "NIFTY 50 at or above 24,980 (your breakeven)", kind: "instrument", symbol: "NIFTY 50", exchange: "NSE", condition: "close >= 24980",
      base_tf: "1m", priority: "normal", cooldown_minutes: 60, mode: "instant", digest_every: "hourly",
    });
    expect(Object.keys(body).some((k) => /then|action|order|side|lots|quantity|exit/i.test(k))).toBe(false);
    expect(ALERT_ONLY).toMatch(/Nothing is traded/);
  });

  it("the panel renders the choices in plain words; Save is the only button and it saves an alert", () => {
    const leg = shortLeg(72.5, { stop_loss: 120 });
    const html = renderToStaticMarkup(<AlertChoicesView choices={alertChoices([leg], 24850)} picked="be-0" kept="50" keptLeg={leg} note={null} busy={false}
      idPrefix="t" onPick={() => undefined} onKept={() => undefined} onSave={() => undefined} />);
    const words = text(html);
    expect(words).toContain("Tell me when");
    expect(words).toContain("NIFTY 27 OCT 24900 CE reaches 120 (your exit level)");
    expect(words).toContain("(NIFTY 27 OCT 24900 CE at 40.00)");
    expect(html.match(/<button/g)).toHaveLength(1);
    expect(words).toContain("Save alert");
    clean(words);
  });
});

// --- 8b-g: compare a change; 8b-j: compare two baskets ---------------------------------------------------------------------

const newLeg = (over: Partial<NewLeg> = {}): NewLeg => ({ underlying: "NIFTY", expiry: EXP, strike: 25000, right: "CE", side: "SELL", lots: 1, price: 41,
  label: null, iv: null, stopLoss: 60, ...over });

describe("8b-g: the position now vs with the change he built", () => {
  it("roll the spread up: close both legs (booked at their live prices) and add the basket's two new legs", () => {
    const open = [shortLeg(72.5), longLeg(41)];
    const change = changeSide(open, new Set([301, 302]), [newLeg({ strike: 25000, price: 41, side: "SELL" }), newLeg({ strike: 25100, price: 22, side: "BUY" })]);
    expect(change.booked).toBe(262.5);                                      // (80 - 72.5) x 75 + (41 - 45) x 75
    expect(change.newPremium).toBe(1425);                                   // (41 - 22) x 75
    expect(change.legs).toHaveLength(2);
    expect(change.notes).toEqual([]);
    expect(outcome(change.legs, change.booked ?? 0)).toEqual({
      maxProfit: 1687.5, maxLoss: -5812.5, unboundedProfit: false, unboundedLoss: false, breakevens: [25022.5],
    });
    // Keeping both and adding a hedge leg: three legs, nothing booked.
    const hedge = changeSide(open, new Set(), [newLeg({ strike: 25100, price: 22, side: "BUY" })]);
    expect([hedge.legs.length, hedge.booked]).toEqual([3, 0]);
  });

  it("left out, said plainly: another underlying, an unpriced leg, a leg closed with no live price", () => {
    const change = changeSide([shortLeg(null), longLeg()], new Set([301]), [newLeg({ underlying: "BANKNIFTY" }), newLeg({ price: null })]);
    expect(change.booked).toBeNull();
    expect(change.added).toEqual([]);
    expect(change.notes.join(" ")).toMatch(/no live price yet.*BANKNIFTY: left out here.*no price: enter one in the basket/);
  });

  it("the side-by-side figures: profit, loss, breakevens, premium, margin (an estimate), what closing books", () => {
    const open = [shortLeg(72.5), longLeg(41)];
    const columns: CompareColumn[] = [
      { name: "Now", legs: heldPayoff(open), shift: 0, margin: "₹4,875.00", pop: null },
      { name: "With your change", legs: heldPayoff([longLeg()]), shift: 562.5, margin: "–", pop: null, extra: [{ label: "Booked now by closing", value: "₹562.50" }] },
    ];
    expect(figureRows(columns)).toEqual([
      { label: "Max profit", values: ["₹2,625.00", "Unlimited"] },
      { label: "Max loss", values: ["₹4,875.00", "₹2,812.50"] },
      { label: "Breakevens", values: ["24,935", "25,037.5"] },
      { label: "Net premium", values: ["₹2,625.00 received", "₹3,375.00 paid"] },
      { label: "Margin", values: ["₹4,875.00", "–"], sub: "ATP's estimate, not your broker's number" },
      { label: "Booked now by closing", values: ["–", "₹562.50"] },
    ]);
    const html = text(renderToStaticMarkup(<CompareView columns={columns} underlying="NIFTY" spot={24850} testId="t" />));
    expect(html).toContain("Now: in plain words (information only)");
    expect(html).toContain("If NIFTY closes below 24,935 at expiry you keep up to about ₹2,600; above 24,935 you lose up to about ₹4,900.");
    clean(html);
    clean(COMPARE_NOTE);
  });

  it("the change editor: Keep / Close in the comparison only, the basket's legs, a way to the chain - no order button", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter><ChangeEditor legs={[shortLeg(), longLeg()]} closed={new Set([301])} added={[newLeg()]} adjust="/option-chain?underlying=NIFTY" onToggle={() => undefined} /></MemoryRouter>,
    );
    const words = text(html);
    expect(words).toContain("Short 1 lot · NIFTY 27 OCT 25000 CE at 41.00");
    expect(words).toContain("Open the chain");
    expect((html.match(/<button[^>]*>(.*?)<\/button>/g) ?? []).map((b) => text(b).trim())).toEqual(["Keep", "Close", "Keep", "Close"]);
    clean(words);
  });
});

describe("8b-j: two baskets side by side", () => {
  const settings = { width_steps: 1, lots: 1, stop_pct: 50 };
  it("the choices: his basket, the ready strategies from his settings (no calendar), his saved baskets on this underlying", () => {
    const saved = { version: 3, items: {
      a1: { name: "My condor", underlying: "NIFTY", product: "MIS" as const, legs: [{ expiry: EXP, strike: 24500, right: "CE" as const, side: "SELL" as const, lots: 1, price: 100 }] },
      b2: { name: "Bank one", underlying: "BANKNIFTY", product: "MIS" as const, legs: [] },
    } };
    const choices = compareChoices({ underlying: "NIFTY", legs: [newLeg()], saved, rows: chainRows(), expiry: EXP, around: 24500, settings });
    expect(choices.map((c) => c.name)).toEqual(["Your basket", "Bull put spread", "Bear call spread", "Iron condor", "Straddle", "Strangle", "My condor"]);
    expect(choices.every((c) => c.problem === null)).toBe(true);
    expect(compareChoices({ underlying: "NIFTY", legs: [], saved: null, rows: [], expiry: null, around: null, settings })).toEqual([]);
  });

  it("a column from the engine's answer: its lot sizes for the units, its margin and probability", () => {
    const choice = { key: "basket", name: "Your basket", legs: [newLeg({ side: "SELL", strike: 24900, price: 80 }), newLeg({ side: "BUY", strike: 25000, price: 45 })], problem: null };
    const out = { ok: true, errors: [], lot_sizes: [75, 75], summary: { pop: 0.62 }, margin: { value: 5000, hedge: { with_hedge: { value: 4875 } } } } as unknown as BasketEvaluation;
    const col = basketColumn(choice, out, false);
    expect(col.legs.map((l) => l.units)).toEqual([75, 75]);
    expect(col.margin).toBe("₹4,875.00");
    expect(col.pop).toBe(0.62);
    expect(figureRows([col, col]).find((r) => r.label === "Probability of profit")?.values).toEqual(["62.0%", "62.0%"]);
    expect(basketColumn(choice, { ok: false, errors: ["No price for X."] }, false).note).toBe("No price for X.");
    expect(marginText(null)).toBe("–");
  });
});

// --- nothing here reaches an order path or an exit ------------------------------------------------------------------------

describe("information only: no order path, no exit touched", () => {
  it("the new modules call no order, basket-execute, exit or alert-action route", () => {
    for (const src of [compareSource, insightsSource]) {
      expect(src).not.toMatch(/\/paper\/fno\/order|basket\/execute|basketApi\.execute|\.execute\(|\.place\(|exit-now|exitNow|\/then|armRuleThen|putRuleThen/);
    }
    expect(insightsSource).toMatch(/"\/alerts\/rules", \{ method: "POST"/);
    // The basket panel's Compare is a tool of the edit view: Execute basket keeps its own confirm step.
    expect(panelSource).toMatch(/tool === "compare" && compare/);
    expect(positionsSource).toMatch(/StructureAlertV2/);
  });

  it("on a position of its own: Exit now is still there, never disabled, beside Tell me when; inside a group the leg keeps Exit", () => {
    const p = trade(301, SHORT_CE, "SHORT", 80, { stop_loss: 120 });
    const [row] = heldRows([p], null).map((r) => ({ ...r, ltp: 72.5, pnl: 562.5 }));
    const actions: RowActions = {
      exitNowOn: true, canTrade: true, onExit: () => undefined, onCheck: () => undefined,
      fno: { facts: new Map([[`NFO:${SHORT_CE}`, facts(SHORT_CE, "CE", 24900)]]), onAdd: () => undefined, alertsOn: true, onCompare: () => undefined },
    };
    const html = renderToStaticMarkup(<MemoryRouter><PositionRowV2 row={row} position={p} actions={actions} /></MemoryRouter>);
    const exit = html.match(/<button[^>]*data-testid="exit-now"[^>]*>/)?.[0] ?? "";
    expect(exit).not.toBe("");
    expect(exit).not.toMatch(/ disabled=""/);
    expect(html).toContain("data-testid=\"fno-alert\"");
    expect(html).toContain("data-capture=\"kept\"");
    const grouped = renderToStaticMarkup(<MemoryRouter><PositionRowV2 row={row} position={p} actions={actions} inGroup /></MemoryRouter>);
    expect(grouped).toContain("data-testid=\"exit-now\"");
    expect(grouped).not.toContain("data-testid=\"fno-alert\"");
    // Without the alert rules' flag: no Tell me when; the premium kept stays (it reads nothing new).
    const off = renderToStaticMarkup(<MemoryRouter><PositionRowV2 row={row} position={p} actions={{ ...actions, fno: { ...actions.fno!, alertsOn: false } }} /></MemoryRouter>);
    expect(off).not.toContain("fno-alert");
    expect(off).toContain("exit-now");
  });
});
