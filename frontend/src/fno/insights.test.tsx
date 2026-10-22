import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { hasCallWords } from "../chartAnalyze/signalLanguage";
import { liveChain } from "../optionChain/chain.fixture";
import ChainTableV2 from "../optionChain/ChainTableV2";
import { DEFAULT_COLUMNS } from "../optionChain/model";
import pageSource from "../optionChain/OptionChainPageV2.tsx?raw";
import type { Evaluation } from "../optionsBuilder/model";
import signalPartsSource from "../signals/SignalPartsV2.tsx?raw";
import positionsSource from "../trading/PositionsV2.tsx?raw";
import { formatINR } from "../ui/format";
import type { BasketPreview, FnoContract, OrderPreview } from "./api";
import { ConfirmView } from "./BasketPanelV2";
import panelSource from "./BasketPanelV2.tsx?raw";
import { aboutMoney, payoffSentence } from "./basketModel";
import { ActionSheetBody, type ActionTarget } from "./ChainActionSheetV2";
import { FuturesChainView } from "./FuturesChainV2";
import {
  GREEKS_NOTE, RISK_NOTE, STRUCTURE_EXPLAIN, chainSymbolOf, greekLines, otherSetupExpiries, riskLine, setupChainHref, setupMarks, structureOf,
  ticketPayoffSentence, type RiskBudget, type SetupStrikes, type StructureLeg,
} from "./insights";
import { ExplainV2, RiskLineV2, SetupPanelV2 } from "./InsightsV2";
import { Review, TicketBody } from "./OrderTicketV2";
import ticketSource from "./OrderTicketV2.tsx?raw";
import { newDraft, type TicketTarget } from "./ticketModel";

/**
 * F&O workspace 8b, slice 1 (owner order sections 8b and 9): the payoff in plain words from fixtures (outcome words only,
 * no buy / sell / target / stop wording, only the order's own amounts), the risk line (information only: it never
 * disables an order button and never touches an exit), Explain (education), the PAPER frame on every F&O order surface,
 * and the setup-aware chain (marks only from the user's own settings, for his own signal). Made-up numbers only.
 */
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, "\"").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const ADVICE = /\b(should|recommend\w*|advice|advise|best|target\w*|guarantee\w*|sure[- ]?shot|bullish|bearish)\b/i;
const noop = () => undefined;

const EXPAND = new Set(["Review", "TicketBody", "Line", "Segmented", "ConfirmView", "Figure"]);
function elements(node: ReactNode, out: ReactElement[] = []): ReactElement[] {
  if (Array.isArray(node)) { node.forEach((n) => elements(n, out)); return out; }
  if (!isValidElement(node)) return out;
  const type = node.type as unknown;
  if (typeof type === "function" && EXPAND.has((type as { name: string }).name)) {
    return elements((type as (p: unknown) => ReactNode)(node.props), out);
  }
  out.push(node);
  elements((node.props as { children?: ReactNode }).children, out);
  return out;
}
const button = (els: ReactElement[], attr: string, value: string) => {
  const el = els.find((e) => (e.props as Record<string, unknown>)[attr] === value);
  expect(el, `${attr}=${value}`).toBeTruthy();
  return el!.props as { disabled?: boolean };
};

const CALL: FnoContract = {
  underlying: "NIFTY", tradingsymbol: "NIFTY26NOV24500CE", exchange: "NFO", instrument_key: "NSE_FO|1", expiry: "2026-11-03", strike: 24500,
  right: "CE", lot_size: 75, tick_size: 0.05, label: "NIFTY 03 NOV 24500 CE", freeze_quantity: null,
};
const PUT: FnoContract = { ...CALL, tradingsymbol: "NIFTY26NOV24300PE", strike: 24300, right: "PE", label: "NIFTY 03 NOV 24300 PE" };
const FUT: FnoContract = { ...CALL, tradingsymbol: "NIFTY26NOVFUT", strike: null, right: "FUT", label: "NIFTY 24 NOV FUT" };

function preview(over: Partial<OrderPreview> = {}): OrderPreview {
  return {
    mode: "PAPER", practice: "Practice mode (PAPER)", contract: CALL, label: CALL.label, side: "BUY", lots: 2, lot_size: 75, units: 150,
    order_type: "MARKET", product: "MIS", price: 250, price_source: "live", fill_price: 250, fill_note: null, stop_loss: null,
    margin: { value: 37500, basis: "The premium paid.", estimated: true }, max_loss: { value: 37500, basis: "The premium you pay." },
    charges: { total: 61.2, estimated: true }, risk: { risk_per_trade: 50000, allowed_lots: 5 }, errors: [], refusals: [], warnings: [],
    can_place: true, confirm_label: "Place PAPER order", as_of: "2026-10-27T05:00:00+00:00", ...over,
  };
}

const BUDGET: RiskBudget = { capital: 1_000_000, per_trade: { amount: 10_000, pct: 1 }, daily: { amount: 50_000, pct: 5 }, source: "Your risk settings", blocks: false };

/** The numbers in a sentence, as written ("24,750", "₹37,500" -> "37,500"). */
const numbersIn = (s: string) => (s.match(/\d[\d,]*(?:\.\d+)?/g) ?? []);
const digits = (s: string) => s.replace(/[^\d.,]/g, "");

// --- 8b-c: the payoff in plain words ---------------------------------------------------------------------------------------

describe("8b-c the payoff in plain words (ticket and basket), from fixtures", () => {
  const cases: { name: string; p: OrderPreview; allowed: string[]; expected: string }[] = [
    { name: "long call", p: preview(), allowed: ["24,500", "24,750", digits(aboutMoney(37500))],
      expected: "If NIFTY closes at or below 24,500 at expiry you lose the premium, at most about ₹37,500; above 24,750 you gain, more the higher it closes." },
    { name: "long call with an exit level", p: preview({ stop_loss: 150, max_loss: { value: 15000, basis: "To your exit." } }),
      allowed: ["24,500", "24,750", digits(aboutMoney(37500)), digits(aboutMoney(15000))],
      expected: "If NIFTY closes at or below 24,500 at expiry you lose the premium, at most about ₹37,500 (before expiry, your exit level keeps it to about ₹15,000 (more if the price jumps past it)); above 24,750 you gain, more the higher it closes." },
    { name: "short put", p: preview({ contract: PUT, side: "SELL", price: 80, fill_price: 80, stop_loss: 160, max_loss: { value: 12000, basis: "To your exit." } }),
      allowed: ["24,300", "24,220", digits(aboutMoney(12000))],
      expected: "If NIFTY closes at or above 24,300 at expiry you keep the premium, at most about ₹12,000; below 24,220 you lose, more the lower it closes, and your exit level sets the loss at about ₹12,000 (more if the price jumps past it)." },
    { name: "long future", p: preview({ contract: FUT, units: 75, lots: 1, price: 24600, fill_price: 24600, stop_loss: 24500, max_loss: { value: 7500, basis: "To your exit." } }),
      allowed: ["24,600", "75", digits(aboutMoney(7500))],
      expected: "Above 24,600 at expiry you gain about ₹75 for each point higher; below it you lose about ₹75 for each point lower, and your exit level sets the loss at about ₹7,500 (more if the price jumps past it)." },
    { name: "short future", p: preview({ contract: FUT, side: "SELL", units: 75, lots: 1, price: 24600, fill_price: 24600, stop_loss: 24700, max_loss: { value: 7500, basis: "To your exit." } }),
      allowed: ["24,600", "75", digits(aboutMoney(7500))],
      expected: "Below 24,600 at expiry you gain about ₹75 for each point lower; above it you lose about ₹75 for each point higher, and your exit level sets the loss at about ₹7,500 (more if the price jumps past it)." },
  ];

  it.each(cases)("$name: one sentence of outcomes, the order's own amounts only, no call or advice words", ({ p, allowed, expected }) => {
    const s = ticketPayoffSentence(p)!;
    expect(s).toBe(expected);
    expect(hasCallWords(s)).toBe(false);
    expect(s).not.toMatch(ADVICE);
    for (const n of numbersIn(s)) expect(allowed, `${n} in "${s}"`).toContain(n);
  });

  it("no sentence while the figures are not known", () => {
    expect(ticketPayoffSentence(preview({ contract: null }))).toBeNull();
    expect(ticketPayoffSentence(preview({ fill_price: null, price: null }))).toBeNull();
    expect(ticketPayoffSentence(preview({ units: null }))).toBeNull();
  });

  it("the basket's sentence (FNO-2) passes the same word rule", () => {
    const prices = Array.from({ length: 241 }, (_, i) => 23500 + i * 8.33333);
    const pnl = (s: number) => ((200 - Math.max(24500 - s, 0)) - (100 - Math.max(24300 - s, 0))) * 75;
    const e = { prices, at_expiry: prices.map(pnl), breakevens: [24400], net_premium: 7500,
      extremes: { max_profit: 7500, max_loss: -7500, unbounded_profit: false, unbounded_loss: false } } as unknown as Evaluation;
    const s = payoffSentence(e, "NIFTY");
    expect(hasCallWords(s)).toBe(false);
    for (const n of numbersIn(s)) expect(["24,300", "24,400", "7,500"]).toContain(n);
  });

  it("the ticket's confirm step shows the sentence under 'In plain words (information only)'", () => {
    const html = text(renderToStaticMarkup(<MemoryRouter><Review preview={preview()} /></MemoryRouter>));
    expect(html).toContain("In plain words (information only)");
    expect(html).toContain("you lose the premium, at most about ₹37,500");
  });
});

// --- 8b-d: the risk line -------------------------------------------------------------------------------------------------

describe("8b-d the risk line: the user's own limits, information only", () => {
  it("within, above the risk per trade, above the daily limit, no fixed limit", () => {
    expect(riskLine(5000, false, BUDGET)).toEqual({ tone: "info", note: RISK_NOTE,
      text: `Max loss ${formatINR(5000)} is 0.5% of your capital (${formatINR(1_000_000)}). Within your risk per trade of ${formatINR(10_000)}.` });
    expect(riskLine(37500, false, BUDGET)?.tone).toBe("warn");
    expect(riskLine(37500, false, BUDGET)?.text).toContain(`That is more than your risk per trade of ${formatINR(10_000)} (1%).`);
    expect(riskLine(60000, false, BUDGET)?.text).toContain(`That is more than your daily loss limit of ${formatINR(50_000)}.`);
    expect(riskLine(null, true, BUDGET)?.text).toBe(`The loss here has no fixed limit. Your daily loss limit is ${formatINR(50_000)} (5% of your capital).`);
    expect(riskLine(500, false, BUDGET)?.text).toContain("is under 0.1% of your capital");
  });

  it("no line without the user's budget or a max loss", () => {
    expect(riskLine(5000, false, null)).toBeNull();
    expect(riskLine(null, false, BUDGET)).toBeNull();
    expect(riskLine(5000, false, { ...BUDGET, capital: 0 })).toBeNull();
  });

  it("never blocks: the ticket's Place button follows can_place alone, with or without a warning", () => {
    const target: TicketTarget = { underlying: "NIFTY", expiry: "2026-11-03", strike: 24500, right: "CE", label: CALL.label, lotSize: 75, ltp: 250, bid: 249, ask: 251 };
    for (const canPlace of [true, false]) {
      const p = preview({ can_place: canPlace, max_loss: { value: 900_000, basis: "x" } });      // far above every limit
      for (const budget of [null, BUDGET]) {
        const els = elements(<TicketBody target={target} draft={newDraft("BUY")} step={{ kind: "review", preview: p }} busy={false} problem={null}
          idPrefix="t" budget={budget} onDraft={noop} onReview={noop} onPlace={noop} onBack={noop} onClose={noop} />);
        expect(button(els, "data-ticket", "place").disabled).toBe(!canPlace);
      }
    }
    const html = text(renderToStaticMarkup(<MemoryRouter><Review preview={preview({ max_loss: { value: 900_000, basis: "x" } })} budget={BUDGET} /></MemoryRouter>));
    expect(html).toContain("That is more than your daily loss limit");
    expect(html).toContain(RISK_NOTE);
  });

  it("never blocks: the basket's Execute button follows can_execute alone", () => {
    const totals = { margin: { value: 1, estimated: true, basis: "", note: "", hedge: { with_hedge: { value: 1, estimated: true, basis: "" }, without_hedge: null, benefit: null, hedged: false, source: "estimate", note: "" } },
      extremes: { max_profit: 1, max_loss: -900_000, unbounded_profit: false, unbounded_loss: false }, net_premium: 1, costs: 1, breakevens: null };
    for (const can of [true, false]) {
      const p = { mode: "PAPER", practice: "", legs: [], refusals: [], totals, totals_errors: [], can_execute: can, product: "MIS", confirm_label: null, as_of: "" } as unknown as BasketPreview;
      for (const budget of [null, BUDGET]) {
        const els = elements(<ConfirmView preview={p} busy={false} onBack={noop} onPlace={noop} budget={budget} />);
        expect(button(els, "data-basket", "place").disabled).toBe(!can);
      }
    }
  });

  it("the line has no control of its own (it cannot hold an order or an exit) and the exit paths are unchanged", () => {
    const html = renderToStaticMarkup(<RiskLineV2 line={riskLine(900_000, false, BUDGET)} />);
    expect(html).toContain("data-risk-line=\"warn\"");
    expect(html).not.toMatch(/<(button|a|input|select)\b/);
    // The exits (Exit now on the chain and on Positions) do not read the risk line or the budget.
    expect(positionsSource).not.toMatch(/riskLine|useRiskBudget/);
    expect(panelSource.match(/riskLine\(/g)?.length).toBe(2);           // the panel's numbers and the confirm step only
  });
});

// --- 8b-h: Explain ---------------------------------------------------------------------------------------------------------

describe("8b-h Explain: the structure and each Greek in plain words (education)", () => {
  const leg = (side: "BUY" | "SELL", right: "CE" | "PE" | "FUT", strike: number, expiry = "2026-11-03"): StructureLeg => ({ side, right, strike, expiry });
  it("names the common structures", () => {
    expect(structureOf([leg("BUY", "CE", 100)])).toBe("long_call");
    expect(structureOf([leg("SELL", "PE", 100)])).toBe("short_put");
    expect(structureOf([leg("BUY", "FUT", 0)])).toBe("long_future");
    expect(structureOf([leg("SELL", "PE", 100), leg("BUY", "PE", 90)])).toBe("bull_put_spread");
    expect(structureOf([leg("BUY", "PE", 100), leg("SELL", "PE", 90)])).toBe("bear_put_spread");
    expect(structureOf([leg("SELL", "CE", 100), leg("BUY", "CE", 110)])).toBe("bear_call_spread");
    expect(structureOf([leg("BUY", "CE", 100), leg("SELL", "CE", 110)])).toBe("bull_call_spread");
    expect(structureOf([leg("BUY", "CE", 100), leg("BUY", "PE", 100)])).toBe("long_straddle");
    expect(structureOf([leg("SELL", "CE", 110), leg("SELL", "PE", 90)])).toBe("short_strangle");
    expect(structureOf([leg("BUY", "CE", 100, "2026-11-10"), leg("SELL", "CE", 100)])).toBe("calendar");
    expect(structureOf([leg("BUY", "PE", 80), leg("BUY", "CE", 120), leg("SELL", "PE", 90), leg("SELL", "CE", 110)])).toBe("iron_condor");
    expect(structureOf([leg("BUY", "PE", 80), leg("BUY", "CE", 120), leg("SELL", "PE", 100), leg("SELL", "CE", 100)])).toBe("iron_butterfly");
    expect(structureOf([leg("BUY", "CE", 100), leg("BUY", "CE", 110), leg("SELL", "PE", 90)])).toBe("custom");
  });

  it("every explanation is education: no call words, no advice words", () => {
    for (const [key, s] of Object.entries(STRUCTURE_EXPLAIN)) {
      expect(hasCallWords(`${s.name} ${s.text}`), key).toBe(false);
      expect(`${s.name} ${s.text}`, key).not.toMatch(ADVICE);
    }
    const lines = greekLines({ delta: 12.3, gamma: -0.004, theta: 410, vega: -800 }, "NIFTY");
    expect(lines.map((l) => l.name)).toEqual(["Delta", "Gamma", "Theta", "Vega"]);
    expect(lines[0].text).toBe("For each point NIFTY rises, the value changes by about ₹12 up; a fall works the other way.");
    expect(lines[2].text).toContain("about ₹410 up (time decay)");
    expect(lines[3].text).toContain("about ₹800 down");
    for (const l of [...lines, ...greekLines({ delta: 0.1, gamma: 0, theta: 0, vega: 0 }, "NIFTY")]) {
      expect(hasCallWords(l.text)).toBe(false);
      expect(l.text).not.toMatch(ADVICE);
    }
  });

  it("the basket shows Explain with the Greeks; the ticket with the structure", () => {
    const html = text(renderToStaticMarkup(<ExplainV2 structure="bull_put_spread" greeks={{ delta: 12.3, gamma: -0.004, theta: 410, vega: -800 }} underlying="NIFTY" testId="basket" />));
    expect(html).toContain("Explain this");
    expect(html).toContain("Bull put spread");
    expect(html).toContain(GREEKS_NOTE);
    expect(panelSource).toContain("<ExplainV2 testId=\"basket\"");
    const review = renderToStaticMarkup(<MemoryRouter><Review preview={preview()} /></MemoryRouter>);
    expect(review).toContain("data-explain=\"ticket\"");
    expect(text(review)).toContain("Long call");
  });
});

// --- 8b-l: the PAPER frame on every F&O order surface ------------------------------------------------------------------------

describe("8b-l the PAPER frame on every F&O order surface", () => {
  it("the ticket, the basket panel and the positions page carry the frame", () => {
    expect(ticketSource).toContain("<PracticeFrame />");
    expect(panelSource).toContain("<PracticeFrame />");
    // Positions: while every position shown is PAPER (a LIVE one, never in Phase 0, has its own "Live" chip per row).
    expect(positionsSource).toMatch(/list\.every\(\(p\) => p\.mode === "PAPER"\) && <PracticeFrame/);
  });

  it("the chain's action sheet (Buy / Sell, You hold, Exit) and the futures chain with trading on are marked", () => {
    const target: ActionTarget = { underlying: "NIFTY", expiry: "2026-11-03", strike: 24500, right: "CE", leg: { oi: null, oi_change: null, ltp: 250, iv: null, volume: null, bid: 249, ask: 251, delta: null, gamma: null, theta: null, vega: null }, spotSymbol: "NIFTY 50" };
    const on = { onTrade: noop, onExit: noop, onPanel: noop, onBasket: noop, onWatchlist: noop, onLevel: noop, onAlert: noop, onChart: noop };
    const sheet = renderToStaticMarkup(<MemoryRouter><ActionSheetBody target={target} contract={CALL} contractProblem={null} held={[]} panel={null} note={null}
      watchlists={null} level="" watchlistsOn alertsOn {...on} /></MemoryRouter>);
    expect(sheet).toContain("data-practice-frame");
    const futures = renderToStaticMarkup(<MemoryRouter><FuturesChainView data={null} problem={null} loading trading watchlistsOn timeframe={null} panel={null}
      watchlists={null} note={null} basketCount={0} on={{} as never} /></MemoryRouter>);
    expect(futures).toContain("data-practice-frame");
  });
});

// --- 8b-a: the setup-aware chain ---------------------------------------------------------------------------------------------

const ANSWER: SetupStrikes = {
  source: "your_settings", signal: "action:12", underlying: "NIFTY", setup: "Made-up setup", side: "bull_put",
  reference: { text: "the current price", price: 24480 }, message: null,
  items: [{ id: 7, settings: "Bull put spread · main strike 1 strike out of the money · wing 2 strikes away · nearest expiry", expiry: "2026-11-03",
    legs: [{ label: "Main strike", right: "PE", strike: 24400, ratio: 1 }, { label: "Wing", right: "PE", strike: 24200, ratio: 1 }], note: null }],
};

describe("8b-a the setup-aware chain: marks only from the user's own settings, for his own signal", () => {
  it("marks the strikes of the expiry on screen; another expiry is offered, never switched by itself", () => {
    expect([...setupMarks(ANSWER, "2026-11-03")]).toEqual([["24400|PE", "Main"], ["24200|PE", "Wing"]]);
    expect(setupMarks(ANSWER, "2026-11-10").size).toBe(0);
    expect(otherSetupExpiries(ANSWER, "2026-11-10")).toEqual(["2026-11-03"]);
    expect(otherSetupExpiries(ANSWER, "2026-11-03")).toEqual([]);
  });

  it("nothing is marked when the server says the signal or the deployment is not the user's own", () => {
    const notYours: SetupStrikes = { ...ANSWER, underlying: null, side: "none", reference: null, items: [], message: "This signal is not one of yours, so nothing is marked on the chain." };
    expect(setupMarks(notYours, "2026-11-03").size).toBe(0);
    expect(setupMarks(null, "2026-11-03").size).toBe(0);
    const html = text(renderToStaticMarkup(<SetupPanelV2 state={{ status: "ready", data: notYours }} symbol="NIFTY" expiry="2026-11-03" onExpiry={noop} onClear={noop} />));
    expect(html).toContain("This signal is not one of yours");
    // The page passes marks only for the answer's own underlying.
    expect(pageSource).toContain("setupState.data.underlying === symbol");
  });

  it("the chain rings the marked cells and labels the strike; without a signal the table is unchanged", () => {
    const live = liveChain();
    const marked = renderToStaticMarkup(<ChainTableV2 live={live} prefs={DEFAULT_COLUMNS} centreKey="k" onPick={noop} setup={setupMarks(ANSWER, live.expiry)} />);
    expect(marked.match(/data-setup="put"/g)?.length).toBeGreaterThan(0);
    expect(marked).not.toContain("data-setup=\"call\"");
    expect(text(marked)).toContain("Main put , from your settings");
    const plain = renderToStaticMarkup(<ChainTableV2 live={live} prefs={DEFAULT_COLUMNS} centreKey="k" onPick={noop} />);
    expect(plain).not.toContain("data-setup");
    expect(plain).not.toContain("data-setup-mark");
  });

  it("the panel says the strikes come from his settings and are not picked, in structure words only, with no order button", () => {
    const html = renderToStaticMarkup(<SetupPanelV2 state={{ status: "ready", data: ANSWER }} symbol="NIFTY" expiry="2026-11-03" onExpiry={noop} onClear={noop} />);
    const words = text(html);
    expect(words).toContain("From your settings");
    expect(words).toContain("ATP does not pick strikes");
    expect(words).toContain("Main strike: 24,400 Put");
    expect(hasCallWords(words)).toBe(false);
    expect(words).not.toMatch(ADVICE);
    expect(html.match(/<button/g)?.length).toBe(1);                    // "Hide marks" only
    const other = text(renderToStaticMarkup(<SetupPanelV2 state={{ status: "ready", data: ANSWER }} symbol="BANKNIFTY" expiry="2026-11-03" onExpiry={noop} onClear={noop} />));
    expect(other).toContain("This signal is on NIFTY");
    expect(other).not.toContain("24,400");
  });

  it("'Strikes on the chain' only for the user's own signals with a side, on a symbol with an option chain", () => {
    expect(setupChainHref({ id: "action:12", symbol: "NIFTY 50", direction: "LONG" })).toBe("/option-chain?underlying=NIFTY&signal=action%3A12");
    expect(setupChainHref({ id: "history:3", symbol: "NIFTY BANK", direction: "SHORT" })).toBe("/option-chain?underlying=BANKNIFTY&signal=history%3A3");
    expect(setupChainHref({ id: "action:12", symbol: "NIFTY 50", direction: null })).toBeNull();
    expect(setupChainHref({ id: "alert:4", symbol: "NIFTY 50", direction: "LONG" })).toBeNull();
    expect(setupChainHref({ id: "trade:4", symbol: "NIFTY 50", direction: "LONG" })).toBeNull();
    expect(setupChainHref({ id: "action:12", symbol: "DEMO 50", direction: "LONG" })).toBeNull();
    expect(chainSymbolOf("nifty fin service")).toBe("FINNIFTY");
    expect(signalPartsSource.match(/<SetupChainLinkV2 item=\{item\} \/>/g)?.length).toBe(2);   // phone card and detail
  });
});
