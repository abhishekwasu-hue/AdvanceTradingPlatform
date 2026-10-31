import * as RD from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import { watchlistsV2Api, type QuotesReply, type WatchItem } from "../chart/watchlistsV2";
import { cx } from "../components/primitives/cx";
import type { ChainRow } from "../optionChain/types";
import { formatNumber, formatPrice } from "../ui/format";
import { basketApi, type BasketApi, type BasketAsk, type BasketEvaluation, type BasketSettings, type Product } from "./api";
import { basket as defaultBasket, useBasket, type BasketStore, type NewLeg } from "./basket";
import { STRATEGIES, basketAsk, legAsk, legLabel, payoffSentence, readyStrategy, type SavedState, type StrategyName } from "./basketModel";
import { PracticeFrame } from "./OrderTicketV2";
import {
  changeSide, heldAsks, heldPayoff, levelText, netPremium, oneExpiry, outcome, pnlAt, priceGrid, sentenceInput, type PayoffLeg, type PosLeg,
} from "./positionInsights";
import { moneyText } from "./ticketModel";
import { Combobox } from "../ui/Combobox";
import { Details } from "../ui/Details";

/**
 * F&O workspace 8b, slice 2: side-by-side outcomes, information only.
 * - 8b-g "Compare a change" (Positions, legs placed together): the position now beside the same position with a change
 *   the user builds himself - the legs he would close (booked at their live price, in this comparison only) and the
 *   legs of his own basket (built on the chain: roll a strike, add a hedge). Nothing is sent: he makes any change
 *   himself with Exit now and the one ticket or the basket.
 * - 8b-j "Compare" in the basket panel: two baskets side by side (his basket, a ready strategy from his own settings, a
 *   saved basket).
 * Both sides drawn on one price scale at expiry (exact, `positionInsights.outcome`); the margin (an estimate) and the
 * probability are the Options Builder engine's (`POST /api/paper/fno/basket/evaluate`, which computes only).
 * `CompareView` holds no state (the tests render it).
 */

export interface CompareColumn {
  name: string;
  legs: PayoffLeg[];
  /** What closing legs books now (added to every price at expiry). */
  shift: number;
  /** Pre-formatted: "₹12,400", "From your broker", "Working out…", "–". */
  margin: string;
  pop: number | null;
  extra?: { label: string; value: string }[];
  /** A plain reason the column is incomplete. */
  note?: string | null;
}

const TONES = [
  { stroke: "stroke-brand", swatch: "bg-brand", dash: undefined },
  { stroke: "stroke-info", swatch: "bg-info", dash: "6 4" },
] as const;

export interface FigureRow { label: string; values: string[]; sub?: string }

const maxProfitText = (legs: readonly PayoffLeg[], shift: number) => {
  const o = outcome(legs, shift);
  if (!o) return "–";
  return o.unboundedProfit ? "Unlimited" : o.maxProfit !== null && o.maxProfit > 0 ? moneyText(o.maxProfit) : "None";
};
const maxLossText = (legs: readonly PayoffLeg[], shift: number) => {
  const o = outcome(legs, shift);
  if (!o) return "–";
  return o.unboundedLoss ? "Unlimited" : o.maxLoss !== null && o.maxLoss < 0 ? moneyText(Math.abs(o.maxLoss)) : "None";
};
const premiumText = (v: number) => (v > 0 ? `${moneyText(v)} received` : v < 0 ? `${moneyText(Math.abs(v))} paid` : "None");

/** The figures of every column, row by row (pure: tested in positionInsights.test.tsx). */
export function figureRows(columns: readonly CompareColumn[]): FigureRow[] {
  const rows: FigureRow[] = [
    { label: "Max profit", values: columns.map((c) => maxProfitText(c.legs, c.shift)) },
    { label: "Max loss", values: columns.map((c) => maxLossText(c.legs, c.shift)) },
    { label: "Breakevens", values: columns.map((c) => outcome(c.legs, c.shift)?.breakevens.map(levelText).join(" · ") || "–") },
    { label: "Net premium", values: columns.map((c) => premiumText(netPremium(c.legs))) },
    { label: "Margin", values: columns.map((c) => c.margin), sub: "ATP's estimate, not your broker's number" },
  ];
  if (columns.some((c) => c.pop !== null)) {
    rows.push({ label: "Probability of profit", values: columns.map((c) => (c.pop === null ? "–" : `${formatNumber(c.pop * 100, 1)}%`)), sub: "Model estimate" });
  }
  const extras = [...new Set(columns.flatMap((c) => (c.extra ?? []).map((e) => e.label)))];
  for (const label of extras) rows.push({ label, values: columns.map((c) => c.extra?.find((e) => e.label === label)?.value ?? "–") });
  return rows;
}

/** The chart, the sentences and the figures, side by side (no state, no hooks). */
export function CompareView({ columns, underlying, spot, testId }: { columns: readonly CompareColumn[]; underlying: string; spot: number | null; testId: string }) {
  const xs = priceGrid(columns.map((c) => c.legs), spot);
  const rows = figureRows(columns);
  return (
    <section aria-label="Side by side" className="space-y-space-3" data-compare={testId}>
      <div className="flex flex-wrap gap-x-space-4 gap-y-space-1" aria-hidden>
        {columns.map((c, i) => (
          <span key={c.name} className="inline-flex items-center gap-space-2 text-t13 text-fg">
            <span className={cx("h-1 w-5 rounded-pill", TONES[i % TONES.length].swatch)} />{c.name}
          </span>
        ))}
      </div>
      <CompareChart columns={columns} xs={xs} spot={spot} underlying={underlying} />
      <ul className="space-y-space-2" data-compare-sentences>
        {columns.map((c) => (
          <li key={c.name} className="rounded-control bg-surface-2 px-space-3 py-space-2 text-t14 text-fg">
            <span className="block text-t12 font-semibold text-fg-muted">{c.name}: in plain words</span>
            {xs.length ? payoffSentence(sentenceInput(c.legs, c.shift, xs), underlying) : "The numbers show once a change is picked."}
            {c.note && <span className="mt-space-1 block text-t12 text-fg-muted">{c.note}</span>}
          </li>
        ))}
      </ul>
      <table className="w-full table-fixed text-t13" data-compare-figures>
        <thead>
          <tr className="text-left text-t12 text-fg-muted">
            <th scope="col" className="w-[34%] py-space-1 font-medium"><span className="sr-only">Figure</span></th>
            {columns.map((c) => <th key={c.name} scope="col" className="py-space-1 text-right font-semibold text-fg">{c.name}</th>)}
          </tr>
        </thead>
        <tbody className="divide-y divide-subtle">
          {rows.map((r) => (
            <tr key={r.label} data-figure-row={r.label}>
              <th scope="row" className="py-space-2 pr-space-2 text-left align-top font-normal text-fg-muted">
                {r.label}{r.sub && <span className="block text-t12">{r.sub}</span>}
              </th>
              {r.values.map((v, i) => <td key={columns[i].name} className="break-words py-space-2 pl-space-2 text-right align-top font-tabular text-fg">{v}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

const W = 640;
const H = 200;
const PAD = 8;

function CompareChart({ columns, xs, spot, underlying }: { columns: readonly CompareColumn[]; xs: readonly number[]; spot: number | null; underlying: string }) {
  const series = columns.map((c) => (oneExpiry(c.legs) && xs.length ? xs.map((x) => pnlAt(c.legs, x, c.shift)) : null));
  const all = series.flatMap((s) => s ?? []);
  if (!xs.length || !all.length) {
    return <p className="rounded-control bg-surface-2 px-space-3 py-space-3 text-t13 text-fg-muted">The chart shows once each side has legs expiring on one day.</p>;
  }
  const top = Math.max(0, ...all);
  const bottom = Math.min(0, ...all);
  const span = top - bottom || 1;
  const sx = (x: number) => PAD + ((x - xs[0]) / (xs[xs.length - 1] - xs[0] || 1)) * (W - 2 * PAD);
  const sy = (y: number) => PAD + ((top - y) / span) * (H - 2 * PAD);
  const path = (ys: readonly number[]) => ys.map((y, i) => `${i ? "L" : "M"}${sx(xs[i]).toFixed(1)},${sy(y).toFixed(1)}`).join("");
  return (
    <figure className="rounded-panel border border-subtle p-space-2" data-compare-chart>
      <div className="flex justify-between text-t12 text-fg-muted font-tabular"><span>{moneyText(top)}</span><span>P&amp;L at expiry</span></div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="h-40 w-full md:h-48" role="img"
        aria-label={`The outcome at expiry of ${columns.map((c) => c.name).join(" and ")} against the price of ${underlying}`}>
        <line x1={PAD} x2={W - PAD} y1={sy(0)} y2={sy(0)} className="stroke-fg-muted" strokeWidth={1} strokeDasharray="2 3" vectorEffect="non-scaling-stroke" />
        {spot && spot >= xs[0] && spot <= xs[xs.length - 1] && (
          <line x1={sx(spot)} x2={sx(spot)} y1={PAD} y2={H - PAD} className="stroke-border" strokeWidth={1} vectorEffect="non-scaling-stroke" />
        )}
        {series.map((ys, i) => ys && (
          <path key={columns[i].name} d={path(ys)} className={cx("fill-none", TONES[i % TONES.length].stroke)} strokeWidth={2.25}
            strokeDasharray={TONES[i % TONES.length].dash} vectorEffect="non-scaling-stroke" data-curve={i} />
        ))}
      </svg>
      <div className="flex justify-between text-t12 text-fg-muted font-tabular">
        <span>{moneyText(bottom)}</span>
      </div>
      <figcaption className="flex justify-between text-t12 text-fg-muted font-tabular">
        <span>{levelText(Math.round(xs[0]))}</span>
        {spot ? <span>{underlying} now {levelText(spot)}</span> : <span>{underlying} at expiry</span>}
        <span>{levelText(Math.round(xs[xs.length - 1]))}</span>
      </figcaption>
    </figure>
  );
}

// --- shared: the engine's margin and probability for one side ---------------------------------------------------------

type Evaluate = Pick<BasketApi, "evaluate">;
export type Quotes = (items: WatchItem[]) => Promise<QuotesReply>;

/** "₹12,400" (the estimate with the hedge), "From your broker" (only the broker can say), "–" (not worked out). */
export function marginText(out: BasketEvaluation | null): string {
  if (!out || !out.ok) return "–";
  const v = out.margin.hedge?.with_hedge.value ?? out.margin.value;
  return typeof v === "number" ? moneyText(v) : "From your broker";
}

/** The underlying's price now (the watchlist's batch quote), read once per open; null while unknown. */
function useSpot(item: WatchItem | null, on: boolean, quotes: Quotes): number | null {
  const [spot, setSpot] = useState<number | null>(null);
  const key = item ? `${item.exchange}:${item.symbol}` : "";
  useEffect(() => {
    if (!on || !item) { setSpot(null); return undefined; }
    let alive = true;
    quotes([item]).then((r) => {
      const ltp = r.items.find((q) => q.symbol === item.symbol)?.ltp ?? null;
      if (alive) setSpot(typeof ltp === "number" && ltp > 0 ? ltp : null);
    }).catch(() => { if (alive) setSpot(null); });
    return () => { alive = false; };
  }, [on, key]);                                                     // `item` is read through `key`
  return spot;
}

/** The engine's answer for `ask` (null legs: nothing to ask), the newest only. */
function useEvaluation(ask: BasketAsk | null, api: Evaluate): { out: BasketEvaluation | null; busy: boolean } {
  const [state, setState] = useState<{ out: BasketEvaluation | null; busy: boolean }>({ out: null, busy: false });
  const key = ask ? JSON.stringify(ask) : "";
  const seq = useRef(0);
  useEffect(() => {
    const n = ++seq.current;
    if (!ask) { setState({ out: null, busy: false }); return undefined; }
    setState((s) => ({ out: s.out, busy: true }));
    const timer = window.setTimeout(() => {
      api.evaluate(ask).then((out) => { if (n === seq.current) setState({ out, busy: false }); },
        () => { if (n === seq.current) setState({ out: null, busy: false }); });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [key, api]);                                                    // `ask` is read through `key`
  return state;
}

// --- 8b-g: compare a change on the positions page ---------------------------------------------------------------------

const DIALOG = "fixed inset-x-0 bottom-0 z-50 flex max-h-[94dvh] flex-col overflow-hidden rounded-t-panel border border-border bg-surface-1 text-fg shadow-raised focus:outline-none md:inset-y-0 md:left-auto md:right-0 md:max-h-none md:w-[44rem] md:rounded-none md:rounded-l-panel";
const SEG = "min-h-11 rounded-control px-space-3 text-t13 font-semibold";

export const COMPARE_NOTE = "Nothing here places or changes an order: make any change yourself with Exit now, the order ticket or the basket.";

const legWords = (side: "BUY" | "SELL", lots: number) => `${side === "BUY" ? "Long" : "Short"} ${formatNumber(lots, 0)} lot${lots === 1 ? "" : "s"}`;

export function AdjustCompareV2({ open, onClose, title, legs, adjust, store = defaultBasket, api = basketApi, quotes = watchlistsV2Api.quotes }: {
  open: boolean;
  onClose: () => void;
  /** "Basket · 2 legs" */
  title: string;
  legs: readonly PosLeg[];
  /** The chain of the position's expiry, where the user builds the change. */
  adjust: string | null;
  store?: BasketStore;
  api?: Evaluate;
  quotes?: Quotes;
}) {
  const basketLegs = useBasket(store);
  const [closed, setClosed] = useState<ReadonlySet<number>>(new Set());
  useEffect(() => { if (open) setClosed(new Set()); }, [open]);
  const first = legs[0];
  const spot = useSpot(first ? { symbol: first.spotSymbol, exchange: first.spotExchange } : null, open, quotes);
  const newLegs = useMemo(() => basketLegs.map(({ id: _id, ...rest }) => rest as NewLeg), [basketLegs]);
  const change = useMemo(() => changeSide(legs, closed, newLegs), [legs, closed, newLegs]);

  const nowAsks = heldAsks(legs);
  const keptAsks = heldAsks(legs.filter((l) => !closed.has(l.id)));
  const changeLegs = keptAsks ? [...keptAsks, ...change.added.map(legAsk)] : null;
  const nowEval = useEvaluation(open && spot && nowAsks?.length ? { legs: nowAsks, spot } : null, api);
  const changeEval = useEvaluation(open && spot && changeLegs?.length ? { legs: changeLegs, spot } : null, api);
  const margin = (e: { out: BasketEvaluation | null; busy: boolean }, any: boolean) => (!any ? "None" : !spot ? "–" : e.busy ? "Working out…" : marginText(e.out));

  const columns: CompareColumn[] = [
    { name: "Now", legs: heldPayoff(legs), shift: 0, margin: margin(nowEval, !!nowAsks?.length), pop: null },
    { name: "With your change", legs: change.legs, shift: change.booked ?? 0, margin: margin(changeEval, !!changeLegs?.length), pop: null,
      extra: [{ label: "Booked now by closing", value: change.booked === null ? "–" : change.booked === 0 && closed.size === 0 ? "None" : moneyText(change.booked) }],
      note: change.notes.join(" ") || null },
  ];
  const underlying = first?.underlying ?? "";
  if (!first) return null;
  return (
    <RD.Root open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <RD.Portal>
        <RD.Overlay className="fixed inset-0 z-40 bg-scrim/50" />
        <RD.Content aria-describedby={undefined} data-adjust-compare className={DIALOG}>
          <div className="flex items-center justify-between gap-space-3 px-space-4 pt-space-3">
            <div className="min-w-0">
              <div className="flex items-center gap-space-2"><RD.Title className="text-t18 font-semibold text-fg">Compare a change</RD.Title><PracticeFrame /></div>
              <p className="text-t13 text-fg-muted">{title}</p>
            </div>
            <RD.Close aria-label="Close" className="-mr-space-2 flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg">
              <X size={18} aria-hidden />
            </RD.Close>
          </div>
          <div className="min-h-0 flex-1 space-y-space-4 overflow-y-auto px-space-4 py-space-3" data-compare-body>
            <p className="text-t13 text-fg-muted">{COMPARE_NOTE}</p>
            <CompareView columns={columns} underlying={underlying} spot={spot} testId="adjust" />
            <ChangeEditor legs={legs} closed={closed} added={change.added} adjust={adjust}
              onToggle={(id, close) => setClosed((s) => { const n = new Set(s); if (close) n.add(id); else n.delete(id); return n; })} />
          </div>
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  );
}

/** Keep / close each open leg (in this comparison only) and the basket's legs on this underlying (no state). */
export function ChangeEditor({ legs, closed, added, adjust, onToggle }: {
  legs: readonly PosLeg[]; closed: ReadonlySet<number>; added: readonly NewLeg[]; adjust: string | null; onToggle: (id: number, close: boolean) => void;
}) {
  return (
    <section aria-label="Your change" className="space-y-space-3" data-change-editor>
      <h3 className="text-t14 font-semibold text-fg">Your change</h3>
      <ul className="space-y-space-2">
        {legs.map((l) => {
          const lots = l.lotSize ? l.units / l.lotSize : null;
          const close = closed.has(l.id);
          return (
            <li key={l.id} className="flex flex-wrap items-center justify-between gap-space-2 rounded-control border border-subtle px-space-3 py-space-2" data-change-leg={l.id}>
              <span className="min-w-0">
                <span className="block text-t14 font-semibold text-fg">{l.label}</span>
                <span className="block text-t12 text-fg-muted font-tabular">
                  {lots && Number.isInteger(lots) ? legWords(l.side, lots) : l.side === "BUY" ? "Long" : "Short"} · entry {formatPrice(l.entry)} · now {formatPrice(l.ltp)}
                </span>
              </span>
              <span role="radiogroup" aria-label={`${l.label} in this comparison`} className="flex shrink-0 gap-space-1 rounded-control bg-surface-2 p-space-1">
                {([["Keep", false], ["Close", true]] as const).map(([word, value]) => (
                  <button key={word} type="button" role="radio" aria-checked={close === value} onClick={() => onToggle(l.id, value)} data-option={word.toLowerCase()}
                    className={cx(SEG, close === value ? "bg-surface-1 text-fg shadow-card" : "text-fg-muted")}>{word}</button>
                ))}
              </span>
            </li>
          );
        })}
      </ul>
      <div className="space-y-space-2" data-change-added>
        <h4 className="text-t13 font-semibold text-fg">New legs from your basket</h4>
        {added.length === 0 ? (
          <p className="rounded-control bg-surface-2 px-space-3 py-space-3 text-t13 text-fg-muted">
            Your basket has no legs on this underlying. Build the new legs on the chain (another strike, a hedge) with Add to basket, then open this again.
          </p>
        ) : (
          <ul className="space-y-space-1">
            {added.map((l, i) => (
              <li key={`${legLabel(l)}-${i}`} className="text-t13 text-fg font-tabular">{legWords(l.side, l.lots)} · {legLabel(l)} at {formatPrice(l.price)}</li>
            ))}
          </ul>
        )}
        {adjust && <Link to={adjust} className="inline-flex min-h-11 items-center text-t14 font-semibold text-brand hover:underline" data-change-chain>Open the chain</Link>}
      </div>
    </section>
  );
}

// --- 8b-j: compare two baskets in the basket panel ----------------------------------------------------------------------

export interface Choice { key: string; name: string; legs: NewLeg[]; problem: string | null }

/** The baskets the user can compare: his basket, the ready strategies from the chain and his own settings, and his
 * saved baskets on this underlying (a Calendar needs the next chain, so it is left out here). Pure. */
export function compareChoices({ underlying, legs, saved, rows, expiry, around, settings }: {
  underlying: string; legs: readonly NewLeg[]; saved: SavedState | null; rows: readonly ChainRow[]; expiry: string | null; around: number | null;
  settings: Pick<BasketSettings, "width_steps" | "lots" | "stop_pct"> | null;
}): Choice[] {
  const out: Choice[] = [];
  if (legs.length) out.push({ key: "basket", name: "Your basket", legs: [...legs], problem: null });
  if (expiry && around !== null && rows.length) {
    for (const name of STRATEGIES.filter((s): s is Exclude<StrategyName, "Calendar"> => s !== "Calendar")) {
      const built = readyStrategy(name, { underlying, expiry, rows, around, settings: settings ?? { width_steps: 2, lots: 1, stop_pct: 50 } });
      out.push({ key: `ready:${name}`, name, legs: built.legs, problem: built.problem });
    }
  }
  for (const [id, s] of Object.entries(saved?.items ?? {})) {
    if (s.underlying !== underlying) continue;
    out.push({ key: `saved:${id}`, name: s.name, legs: s.legs.map((l) => ({ underlying, expiry: l.expiry, strike: l.strike ?? 0, right: l.right, side: l.side,
      lots: l.lots, price: l.price ?? null, stopLoss: l.stop_loss ?? null, label: null, iv: null })), problem: null });
  }
  return out;
}

/** The column of one basket from the engine's answer (its lot sizes, margin and probability). */
export function basketColumn(choice: Choice, out: BasketEvaluation | null, busy: boolean): CompareColumn {
  if (choice.problem) return { name: choice.name, legs: [], shift: 0, margin: "–", pop: null, note: choice.problem };
  if (!out || !out.ok) {
    return { name: choice.name, legs: [], shift: 0, margin: busy ? "Working out…" : "–", pop: null,
      note: busy ? null : out && !out.ok ? out.errors[0] ?? null : "Every leg needs a price to compare." };
  }
  const legs: PayoffLeg[] = choice.legs.map((l, i) => ({ right: l.right, side: l.side, strike: l.right === "FUT" ? 0 : l.strike, price: l.price ?? 0,
    units: l.lots * (out.lot_sizes[i] ?? 0), expiry: l.expiry }));
  return { name: choice.name, legs, shift: 0, margin: marginText(out), pop: out.summary?.pop ?? null };
}

export function StrategyCompareV2({ underlying, legs, saved, rows, expiry, around, settings, spot, product, api = basketApi }: {
  underlying: string; legs: readonly NewLeg[]; saved: SavedState | null; rows: readonly ChainRow[]; expiry: string | null; around: number | null;
  settings: BasketSettings | null; spot: number | null; product: Product | null; api?: Evaluate;
}) {
  const choices = useMemo(() => compareChoices({ underlying, legs, saved, rows, expiry, around, settings }),
    [underlying, legs, saved, rows, expiry, around, settings]);
  const [picked, setPicked] = useState<[string, string]>(["", ""]);
  const a = choices.find((c) => c.key === picked[0]) ?? choices[0] ?? null;
  const b = choices.find((c) => c.key === picked[1] && c.key !== a?.key) ?? choices.find((c) => c.key !== a?.key) ?? null;
  const askOf = (c: Choice | null): BasketAsk | null => (c && !c.problem && c.legs.length && spot ? basketAsk(c.legs, spot, product) : null);
  const ea = useEvaluation(askOf(a), api);
  const eb = useEvaluation(askOf(b), api);
  if (choices.length < 2 || !a || !b) {
    return <p className="rounded-control bg-surface-2 px-space-3 py-space-3 text-t13 text-fg-muted" data-basket="compare">Two baskets are needed to compare: add legs, or open the chain to use the ready strategies.</p>;
  }
  const select = (i: 0 | 1, current: Choice) => (
    <div className="block min-w-0 flex-1 text-t12 text-fg-muted">
      <span aria-hidden>{i === 0 ? "First" : "Second"}</span>
      <Combobox label={i === 0 ? "First" : "Second"} value={current.key} className="mt-px w-full" dataAttrs={{ "data-compare-pick": String(i) }}
        onChange={(v) => setPicked((p) => (i === 0 ? [v, p[1]] : [p[0], v]))}
        options={choices.filter((c) => i === 0 || c.key !== a.key).map((c) => ({ value: c.key, label: c.name }))} />
    </div>
  );
  return (
    <section aria-label="Compare two baskets" className="space-y-space-3 rounded-control border border-subtle p-space-3" data-basket="compare">
      <div className="flex gap-space-2">{select(0, a)}{select(1, b)}</div>
      {!spot && <p className="text-t13 text-fg-muted">The numbers appear once the chain&apos;s price is known.</p>}
      <CompareView columns={[basketColumn(a, ea.out, ea.busy), basketColumn(b, eb.out, eb.busy)]} underlying={underlying} spot={spot} testId="baskets" />
      <Details id="baskets.compare" className="mt-0"><p>Comparing places nothing. Load a basket or pick a ready strategy to work with it.</p></Details>
    </section>
  );
}
