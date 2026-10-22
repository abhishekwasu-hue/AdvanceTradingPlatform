/**
 * F&O workspace 8b, slice 1 (owner order ATP_SESSION_FNO_WORKSPACE.md, section 8b): the pure parts of the ATP-only
 * information features (tested in insights.test.tsx). Information and education only: nothing here places, blocks or
 * changes an order, and nothing here picks a strike or a trade for the user.
 *
 * - 8b-c the payoff in plain words for one ticket order (the basket's sentence is `basketModel.payoffSentence`), from
 *   the figures the server checked (price, units, max loss). Outcome words only ("you lose", "you keep", "you gain"):
 *   no buy / sell / target / stop wording; the only amounts are the user's own order's.
 * - 8b-d the risk line: an order's max loss beside the user's OWN capital, risk per trade and daily loss limit (his risk
 *   settings, `GET /api/paper/fno/risk-budget`). A warning only - it never disables a button and never holds an exit.
 * - 8b-h Explain: what the structure on the ticket or in the basket is, and what each Greek means for it (education).
 * - 8b-a the setup-aware chain: the strikes the user's OWN deployment rules work out to for HIS OWN signal
 *   (`GET /api/paper/fno/setup-strikes`), as marks on the chain. The server decides whose they are; nothing is picked.
 */
import { request } from "../api/client";
import { INDEX_SYMBOLS, STOCK_SYMBOLS } from "../optionChain/model";
import { formatINR, formatNumber, RUPEE } from "../ui/format";
import type { ContractRight, OrderPreview, Side } from "./api";
import { aboutMoney } from "./basketModel";

const priceText = (v: number) => formatNumber(Math.round(v * 100) / 100, Number.isInteger(Math.round(v * 100) / 100) ? 0 : 2);

// --- 8b-c: the payoff of one ticket order in plain words --------------------------------------------------------------

const GAP = " (more if the price jumps past it)";

/** One sentence for the outcomes of a ticket order, from the confirm step's checked figures; null while they are not
 * known (no price, no units, no contract). Information only. */
export function ticketPayoffSentence(p: Pick<OrderPreview, "contract" | "side" | "fill_price" | "price" | "units" | "stop_loss" | "max_loss">): string | null {
  const c = p.contract;
  const price = p.fill_price ?? p.price;
  const units = p.units;
  if (!c || typeof price !== "number" || !(price > 0) || typeof units !== "number" || !(units > 0)) return null;
  const name = c.underlying;
  const loss = typeof p.max_loss?.value === "number" && p.max_loss.value > 0 ? p.max_loss.value : null;
  const exitLoss = loss !== null ? `your exit level sets the loss at about ${aboutMoney(loss)}${GAP}` : null;
  if (c.right === "FUT") {
    const per = `about ${RUPEE}${formatNumber(units, 0)} for each point`;
    const lvl = priceText(price);
    if (p.side === "BUY") {
      return `Above ${lvl} at expiry you gain ${per} higher; below it you lose ${per} lower${exitLoss ? `, and ${exitLoss}` : ""}.`;
    }
    return `Below ${lvl} at expiry you gain ${per} lower; above it you lose ${per} higher${exitLoss ? `, and ${exitLoss}` : ""}.`;
  }
  if (typeof c.strike !== "number") return null;
  const premium = price * units;
  const call = c.right === "CE";
  const k = priceText(c.strike);
  const be = priceText(call ? c.strike + price : c.strike - price);
  const inside = call ? `at or below ${k}` : `at or above ${k}`;
  const beyond = call ? `above ${be}` : `below ${be}`;
  const further = call ? "the higher it closes" : "the lower it closes";
  if (p.side === "BUY") {
    const early = p.stop_loss && loss !== null ? ` (before expiry, your exit level keeps it to about ${aboutMoney(loss)}${GAP})` : "";
    return `If ${name} closes ${inside} at expiry you lose the premium, at most about ${aboutMoney(premium)}${early}; ${beyond} you gain, more ${further}.`;
  }
  return `If ${name} closes ${inside} at expiry you keep the premium, at most about ${aboutMoney(premium)}; ${beyond} you lose, more ${further}`
    + `${exitLoss ? `, and ${exitLoss}` : ""}.`;
}

// --- 8b-d: the risk line -----------------------------------------------------------------------------------------------

/** The user's own budget (backend app/paper/fno_insights.py risk_budget). */
export interface RiskBudget {
  capital: number;
  per_trade: { amount: number; pct: number };
  daily: { amount: number; pct: number };
  source: string;
  blocks: false;
}

export interface RiskLine { tone: "info" | "warn"; text: string; note: string }

export const RISK_NOTE = "From your risk settings. Information only: it does not hold back the order or any exit.";

const pctText = (v: number) => (v > 0 && v < 0.1 ? "under 0.1%" : `${formatNumber(Math.round(v * 10) / 10, v >= 10 || Number.isInteger(Math.round(v * 10) / 10) ? 0 : 1)}%`);

/** The max loss beside the user's own capital and limits, in plain words - or null while either is not known. Never a
 * refusal: the caller shows it as a line, the order's buttons do not read it. */
export function riskLine(maxLoss: number | null | undefined, unbounded: boolean, b: RiskBudget | null | undefined): RiskLine | null {
  if (!b || !(b.capital > 0)) return null;
  if (unbounded) {
    return { tone: "warn", note: RISK_NOTE,
      text: `The loss here has no fixed limit. Your daily loss limit is ${formatINR(b.daily.amount)} (${pctText(b.daily.pct)} of your capital).` };
  }
  if (typeof maxLoss !== "number" || !(maxLoss > 0)) return null;
  const head = `Max loss ${formatINR(maxLoss)} is ${pctText((maxLoss / b.capital) * 100)} of your capital (${formatINR(b.capital)}).`;
  if (maxLoss > b.daily.amount) {
    return { tone: "warn", note: RISK_NOTE, text: `${head} That is more than your daily loss limit of ${formatINR(b.daily.amount)}.` };
  }
  if (maxLoss > b.per_trade.amount) {
    return { tone: "warn", note: RISK_NOTE,
      text: `${head} That is more than your risk per trade of ${formatINR(b.per_trade.amount)} (${pctText(b.per_trade.pct)}).` };
  }
  return { tone: "info", note: RISK_NOTE, text: `${head} Within your risk per trade of ${formatINR(b.per_trade.amount)}.` };
}

// --- 8b-h: Explain (education) -----------------------------------------------------------------------------------------

export type Structure =
  | "long_call" | "short_call" | "long_put" | "short_put" | "long_future" | "short_future"
  | "bull_put_spread" | "bear_put_spread" | "bear_call_spread" | "bull_call_spread"
  | "long_straddle" | "short_straddle" | "long_strangle" | "short_strangle" | "calendar" | "iron_condor" | "iron_butterfly" | "custom";

export interface StructureLeg { side: Side; right: ContractRight; strike: number; expiry: string }

/** The name of the structure a set of legs forms (by side, right, strike and expiry; the lots are not compared). */
export function structureOf(legs: readonly StructureLeg[]): Structure {
  const long = (l: StructureLeg) => l.side === "BUY";
  if (legs.length === 1) {
    const [l] = legs;
    if (l.right === "FUT") return long(l) ? "long_future" : "short_future";
    if (l.right === "CE") return long(l) ? "long_call" : "short_call";
    return long(l) ? "long_put" : "short_put";
  }
  if (legs.some((l) => l.right === "FUT")) return "custom";
  if (legs.length === 2) {
    const [a, b] = legs;
    if (a.expiry !== b.expiry) {
      return a.right === b.right && a.strike === b.strike && long(a) !== long(b) ? "calendar" : "custom";
    }
    if (a.right === b.right && long(a) !== long(b) && a.strike !== b.strike) {
      const lo = long(a) ? a : b;
      const sh = long(a) ? b : a;
      if (a.right === "PE") return lo.strike < sh.strike ? "bull_put_spread" : "bear_put_spread";
      return lo.strike > sh.strike ? "bear_call_spread" : "bull_call_spread";
    }
    if (a.right !== b.right && long(a) === long(b)) {
      if (a.strike === b.strike) return long(a) ? "long_straddle" : "short_straddle";
      const call = a.right === "CE" ? a : b;
      const put = a.right === "CE" ? b : a;
      if (call.strike > put.strike) return long(a) ? "long_strangle" : "short_strangle";
    }
    return "custom";
  }
  if (legs.length === 4 && new Set(legs.map((l) => l.expiry)).size === 1) {
    const pick = (right: ContractRight, isLong: boolean) => legs.filter((l) => l.right === right && long(l) === isLong);
    const [lp, sp, sc, lc] = [pick("PE", true), pick("PE", false), pick("CE", false), pick("CE", true)];
    if ([lp, sp, sc, lc].every((x) => x.length === 1) && lp[0].strike < sp[0].strike && sp[0].strike <= sc[0].strike && sc[0].strike < lc[0].strike) {
      return sp[0].strike === sc[0].strike ? "iron_butterfly" : "iron_condor";
    }
  }
  return "custom";
}

/** What each structure is, in plain words. Education only: how it behaves, never whether to use it. */
export const STRUCTURE_EXPLAIN: Record<Structure, { name: string; text: string }> = {
  long_call: { name: "Long call", text: "You pay a premium for the right to the move above the strike until expiry. The most you can lose is that premium. It gains once the price is above the strike by more than the premium, and each day that passes takes a little of its time value." },
  short_call: { name: "Short call", text: "You receive a premium and take on the move above the strike. The most you keep is that premium; above the strike the loss grows with the price and has no fixed limit, which is why the ticket asks for an exit level. Each day that passes works in your favour." },
  long_put: { name: "Long put", text: "You pay a premium for the right to the move below the strike until expiry. The most you can lose is that premium. It gains once the price is below the strike by more than the premium, and each day that passes takes a little of its time value." },
  short_put: { name: "Short put", text: "You receive a premium and take on the move below the strike. The most you keep is that premium; below the strike the loss grows as the price falls, which is why the ticket asks for an exit level. Each day that passes works in your favour." },
  long_future: { name: "Long future", text: "A future moves one for one with the underlying: every point up is a gain of one point per unit, every point down a loss of the same. There is no premium and no fixed limit on either side; your exit level sets where the loss is taken." },
  short_future: { name: "Short future", text: "A short future moves against the underlying one for one: every point down is a gain per unit, every point up a loss of the same. There is no fixed limit on the loss; your exit level sets where it is taken." },
  bull_put_spread: { name: "Bull put spread", text: "Two puts of one expiry: a short put and a long put lower down as the hedge. You receive a net premium. It keeps that premium when the price stays above the short strike at expiry; the most it can lose is the gap between the strikes minus the premium. The long put is what fixes that limit." },
  bear_put_spread: { name: "Bear put spread", text: "Two puts of one expiry: a long put and a short put lower down. You pay a net premium, which is the most it can lose. It gains as the price falls toward the lower strike; the most it can make is the gap between the strikes minus the premium." },
  bear_call_spread: { name: "Bear call spread", text: "Two calls of one expiry: a short call and a long call higher up as the hedge. You receive a net premium. It keeps that premium when the price stays below the short strike at expiry; the most it can lose is the gap between the strikes minus the premium. The long call is what fixes that limit." },
  bull_call_spread: { name: "Bull call spread", text: "Two calls of one expiry: a long call and a short call higher up. You pay a net premium, which is the most it can lose. It gains as the price rises toward the higher strike; the most it can make is the gap between the strikes minus the premium." },
  long_straddle: { name: "Long straddle", text: "A long call and a long put at the same strike. You pay two premiums, which is the most it can lose (when the price ends right at the strike). It gains on a large move either way, larger than the two premiums together." },
  short_straddle: { name: "Short straddle", text: "A short call and a short put at the same strike. You receive two premiums, the most it can keep (when the price ends at the strike). A large move either way turns it into a loss with no fixed limit, so each leg needs an exit level." },
  long_strangle: { name: "Long strangle", text: "A long call above and a long put below the price. Cheaper than a straddle, it needs a larger move either way to gain. The two premiums are the most it can lose." },
  short_strangle: { name: "Short strangle", text: "A short call above and a short put below the price. You receive two premiums and keep them while the price stays between the strikes. Outside them the loss has no fixed limit, so each leg needs an exit level." },
  calendar: { name: "Calendar", text: "The same strike on two expiries: the near one short, the far one long. The near option loses its time value faster, which is where it can gain; it gains most when the price stays near the strike until the near expiry. The legs expire on different dates, so the chart shows the value day by day." },
  iron_condor: { name: "Iron condor", text: "A bull put spread below the price and a bear call spread above it, one expiry. You receive a net premium and keep it while the price stays between the two short strikes. The long options at the edges fix the most it can lose on either side." },
  iron_butterfly: { name: "Iron butterfly", text: "A short call and a short put at the same strike, with a long put below and a long call above as the hedges. You receive a larger premium than a condor, kept in full only when the price ends right at the middle strike. The edges fix the most it can lose." },
  custom: { name: "Your own combination", text: "These legs do not form one of the common shapes. The chart and the figures show how they behave together: read the payoff at expiry and today, and the most it can make and lose." },
};

export interface GreekValues { delta: number; gamma: number; theta: number; vega: number }

const r0 = (v: number) => formatNumber(Math.abs(Math.round(v)), 0);

/** What each Greek of the basket means, with its own value (education). The Greeks are per unit of money: delta in ₹
 * per point of the underlying, theta per day, vega per IV point (the Options Builder's engine). */
export function greekLines(g: GreekValues, underlying: string): { name: string; value: string; text: string }[] {
  const d = g.delta;
  const dText = Math.abs(d) < 0.5
    ? `Close to zero: a small move in ${underlying} barely changes the value just now.`
    : `For each point ${underlying} rises, the value changes by about ${RUPEE}${r0(d)} ${d > 0 ? "up" : "down"}; a fall works the other way.`;
  const t = g.theta;
  const tText = Math.abs(t) < 0.5
    ? "Close to zero: the passing of a day barely changes the value just now."
    : `With nothing else changing, each day that passes moves the value about ${RUPEE}${r0(t)} ${t > 0 ? "up" : "down"} (time decay).`;
  const v = g.vega;
  const vText = Math.abs(v) < 0.5
    ? "Close to zero: a change in implied volatility barely moves the value just now."
    : `If implied volatility rises by one point, the value moves about ${RUPEE}${r0(v)} ${v > 0 ? "up" : "down"}; a fall works the other way.`;
  return [
    { name: "Delta", value: d.toFixed(1), text: dText },
    { name: "Gamma", value: g.gamma.toFixed(3), text: `How much delta itself changes for each point ${underlying} moves. The larger it is, the faster the value speeds up or slows down as the price moves.` },
    { name: "Theta", value: t.toFixed(0), text: tText },
    { name: "Vega", value: v.toFixed(0), text: vText },
  ];
}

export const GREEKS_NOTE = "The Greeks are a model's estimate for small moves today. They change as the price, the days left and volatility change.";

// --- 8b-a: the setup-aware chain ---------------------------------------------------------------------------------------

export interface SetupLeg { label: string; right: "CE" | "PE"; strike: number; ratio: number }
export interface SetupItem { id: number; settings: string; expiry: string | null; legs: SetupLeg[] | null; note: string | null }
export interface SetupStrikes {
  source: "your_settings";
  signal: string;
  underlying: string | null;
  setup: string | null;
  side: "bull_put" | "bear_call" | "none";
  reference: { text: string; price: number } | null;
  items: SetupItem[];
  message: string | null;
}

export const FROM_YOUR_SETTINGS = "From your settings";
export const NOT_PICKED = "ATP does not pick strikes. These are where your own deployment settings land; the choice is yours.";

/** The marks on the chain: "strike|CE" -> a short label ("Main", "Wing", "2nd"), only for the expiry on screen. */
export function setupMarks(answer: SetupStrikes | null | undefined, expiry: string | null | undefined): Map<string, string> {
  const out = new Map<string, string>();
  if (!answer || !expiry) return out;
  for (const item of answer.items) {
    if (item.expiry !== expiry || !item.legs) continue;
    for (const leg of item.legs) {
      const key = `${leg.strike}|${leg.right}`;
      if (!out.has(key)) out.set(key, leg.label === "Main strike" ? "Main" : leg.label === "Wing" ? "Wing" : "2nd");
    }
  }
  return out;
}

/** The expiries the user's settings land on that differ from the one on screen (to offer a switch). */
export function otherSetupExpiries(answer: SetupStrikes | null | undefined, expiry: string | null | undefined): string[] {
  if (!answer) return [];
  return [...new Set(answer.items.filter((i) => i.legs && i.expiry && i.expiry !== expiry).map((i) => i.expiry as string))];
}

const SPOT_TO_CHAIN: Record<string, string> = { "NIFTY 50": "NIFTY", "NIFTY BANK": "BANKNIFTY", "NIFTY FIN SERVICE": "FINNIFTY" };

/** The option chain's symbol for a signal's symbol, or null when the symbol has no option chain in ATP's lists. */
export function chainSymbolOf(symbol: string): string | null {
  const s = symbol.trim().toUpperCase();
  const name = SPOT_TO_CHAIN[s] ?? s;
  return (INDEX_SYMBOLS as readonly string[]).includes(name) || STOCK_SYMBOLS.includes(name) ? name : null;
}

/** "Strikes on the chain" for one of the user's own signals with a side: the chain page with the signal id. */
export function setupChainHref(item: { id: string; symbol: string; direction: "LONG" | "SHORT" | null }): string | null {
  const kind = item.id.split(":")[0];
  if (!item.direction || (kind !== "action" && kind !== "history")) return null;
  const name = chainSymbolOf(item.symbol);
  if (!name) return null;
  return `/option-chain?${new URLSearchParams({ underlying: name, signal: item.id }).toString()}`;
}

export const SIGNAL_PARAM = /^(action|history):\d{1,12}$/;

const q = (params: Record<string, string | number | null>) =>
  new URLSearchParams(Object.entries(params).filter(([, v]) => v !== null).map(([k, v]) => [k, String(v)])).toString();

export const insightsApi = {
  riskBudget: () => request<RiskBudget>("/paper/fno/risk-budget"),
  setupStrikes: (signal: string, spot: number | null) => request<SetupStrikes>(`/paper/fno/setup-strikes?${q({ signal, spot: spot && spot > 0 ? spot : null })}`),
};
export type InsightsApi = typeof insightsApi;
