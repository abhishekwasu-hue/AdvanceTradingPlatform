import * as RD from "@radix-ui/react-dialog";
import { ArrowLeftRight, ExternalLink, FolderOpen, Layers, Minus, Plus, Save, Scale, Trash2, X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { request } from "../api/client";
import { cx } from "../components/primitives/cx";
import { Details } from "../ui/Details";
import type { ChainExpiry, LiveChain } from "../optionChain/types";
import { PayoffCanvas } from "../optionsBuilder/PayoffCanvas";
import type { Leg as BuilderLeg } from "../optionsBuilder/model";
import { kiteFnoBasketLink, openBrokerAppLink } from "../proposals/brokerLinks";
import { activeLimits, useLiquidityLimits } from "../settings/liquidityLimits";
import { ticketPrefill, useTradingDefaults } from "../settings/tradingDefaults";
import { useFeatures } from "../ui/useFeatures";
import { formatNumber, formatPrice } from "../ui/format";
import { Signed } from "../ui/Signed";
import {
  basketApi, type BasketApi, type BasketNumbers, type BasketPreview, type BasketResult, type BasketSettings, type Product,
} from "./api";
import { basket as defaultBasket, useBasket, type BasketLeg, type BasketStore, type NewLeg } from "./basket";
import {
  MAX_SAVED, STATUS_WORD, STRATEGIES, STRATEGY_TEXT, basketAsk, basketProblems, builderHandoff, legLabel, legsOf, loadBaskets, nameProblem,
  needsStop, newSavedId, payoffSentence, readyStrategy, remainingLegs, resultHeadline, saveBaskets, savedOf, stopFor, strikeStep, writeHandoff,
  type SavedState, type StrategyName,
} from "./basketModel";
import { PracticeFrame } from "./OrderTicketV2";
import { StrategyCompareV2 } from "./CompareV2";
import { ExplainV2, RiskLineV2, useRiskBudget } from "./InsightsV2";
import { riskLine, structureOf, type RiskBudget, type RiskLine } from "./insights";
import { eventLine, legLiquidity, liquidityLine, marginLine } from "./riskParts";
import { RiskChecksV2, useEventRisk } from "./RiskPartsV2";
import { IncompleteV2, SmartConfirmV2 } from "./SmartExecutionV2";
import { ExitSentenceV2, StrategyExitsV2 } from "./StrategyExitsV2";
import {
  defaultsOfForm, exitErrors, exitSentence, exitsAsk, prefillForm, saveExitDefaults, useExitDefaults, type StrategyExitForm,
} from "./strategyExits";
import { PRODUCT_UNSET_TEXT, moneyText, newClientRef, sideWord } from "./ticketModel";

/**
 * FNO-2 (owner order section 5, 8b-c): the basket panel, opened from the basket badge - a side drawer from md, a bottom
 * sheet on a phone, always framed "Practice mode (PAPER)".
 *
 * - Each leg: Buy / Sell, the contract as traders write it, lots, the planning price, a sold leg's stop-loss; remove.
 * - One-tap ready strategies from the chain on screen around the ATM (or the strike the user picks), with the user's own
 *   spread width, lots and stop from their options settings, else a neutral start the panel names (the server says which).
 * - The numbers from the Options Builder's engine: the payoff at expiry and today (T+0) - drag a strike on the chart to
 *   move a leg - the payoff in plain words, max profit and loss, breakevens, PoP, the net premium, the basket's Greeks and
 *   the margin WITH the hedge beside the short legs alone (ATP's estimate, labelled; never the broker's number).
 * - "Execute basket": a confirm step with every leg checked (hedge legs first), the total margin and the max loss; then
 *   every leg one PAPER order through the same ticket, hedge legs first. The result shows each leg plainly (filled,
 *   refused, not placed) and what the user can do; nothing is retried by itself, and "Place the remaining legs" is the
 *   user's own tap. Exits stay on Positions / the chain ("Exit now", never blocked).
 * - Save / load baskets (the user's own preferences), "Open in Options Builder", "Trade in your broker app" (pre-filled
 *   in the user's own broker app; ATP places nothing there).
 * - 8b-j "Compare": two baskets side by side (his basket, a ready strategy from his settings, a saved basket) - the
 *   payoff at expiry on one scale, max profit / loss, breakevens, margin (estimate) and PoP. Information only.
 * The AI never opens this with legs (ADR-0006). `BasketBody`, `ConfirmView` and `ResultView` hold no state (tests render them).
 */
export interface BasketChain {
  symbol: string;
  live: LiveChain | null;
  expiries: readonly ChainExpiry[];
  /** Reads another expiry's chain (a Calendar's far leg). */
  loadChain?: (expiry: string) => Promise<LiveChain>;
}

type Fetcher = <T>(path: string, init?: RequestInit) => Promise<T>;
export type PanelStep =
  | { kind: "edit" }
  | { kind: "confirm"; preview: BasketPreview | null; legs: NewLeg[]; remaining?: boolean }
  | { kind: "done"; result: BasketResult; legs: NewLeg[] };
type Tool = null | "save" | "load" | "compare";
type Note = { tone: "done" | "warn"; text: string } | null;

const FIELD = "h-11 w-full min-w-0 rounded-control border border-border bg-surface-2 px-space-2 text-t14 text-fg font-tabular focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand";
const CHIP = "min-h-11 shrink-0 rounded-pill border border-border px-space-3 text-t13 font-semibold text-fg hover:bg-surface-2 disabled:opacity-40";
const TOOL = "inline-flex min-h-11 items-center gap-space-1 rounded-control border border-border px-space-3 text-t13 font-semibold text-fg hover:bg-surface-2 disabled:opacity-40";
const EVAL_DELAY_MS = 300;

const num = (text: string): number | null => {
  const v = Number(text.replace(/,/g, "").trim());
  return text.trim() !== "" && Number.isFinite(v) && v > 0 ? v : null;
};
const strip = (l: BasketLeg): NewLeg => { const { id: _id, ...rest } = l; return rest; };

export default function BasketPanelV2({ open, onClose, chain, store = defaultBasket, api = basketApi, fetcher = request, onPlaced }: {
  open: boolean;
  onClose: () => void;
  chain: BasketChain;
  store?: BasketStore;
  api?: BasketApi;
  fetcher?: Fetcher;
  /** After at least one leg filled (the chain re-reads its "P" markers). */
  /** After at least one leg was placed (8b slice 3: with the result, so the chain can note the signal in the journal). */
  onPlaced?: (result: BasketResult) => void;
}) {
  const legs = useBasket(store);
  // Owner fix T1-5: the product starts from his own trading defaults (Settings -> My trading defaults), not a fixed MIS;
  // MIS only while he saved none (the line under the choice says so). His own pick in this basket stays.
  const features = useFeatures();
  const savedProduct = ticketPrefill(useTradingDefaults(open && features.on("ui_v2"), fetcher)).product ?? null;
  const [product, setProductState] = useState<Product | null>(savedProduct);
  const picked = useRef(false);
  useEffect(() => { if (savedProduct && !picked.current) setProductState(savedProduct); }, [savedProduct]);
  const setProduct = (p: Product) => { picked.current = true; setProductState(p); };
  const [numbers, setNumbers] = useState<BasketNumbers | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [settings, setSettings] = useState<BasketSettings | null>(null);
  const [around, setAround] = useState<number | null>(null);
  const [step, setStep] = useState<PanelStep>({ kind: "edit" });
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);
  const [tool, setTool] = useState<Tool>(null);
  const [saved, setSaved] = useState<SavedState | null>(null);
  const [saveName, setSaveName] = useState("");
  const ref = useRef(newClientRef());
  const seq = useRef(0);
  // SX1: "Exits for this strategy" - empty, or from his own saved defaults until he types (then his own values).
  const exitDefaults = useExitDefaults(open && features.on("ui_v2"), fetcher);
  const [exitTyped, setExitTyped] = useState<StrategyExitForm | null>(null);
  const [exitVersion, setExitVersion] = useState<number | null>(null);
  const [exitNote, setExitNote] = useState<string | null>(null);
  const exitForm = exitTyped ?? prefillForm(exitDefaults?.value, legs);
  const idPrefix = useId();
  // 8b-d: the user's own risk budget for the risk line (information only: Execute reads only the server's checks).
  const budget = useRiskBudget(open && legs.length > 0);

  const live = chain.live;
  const spot = live?.summary.spot ?? null;
  const rows = useMemo(() => live?.rows ?? [], [live]);
  const atm = live?.summary.atm_strike ?? null;
  const pick = around ?? atm;
  // 8b slice 4: the risk line's other parts - the events up to the latest leg's expiry and each leg's liquidity on the
  // chain on screen (information only; the margin part is set beside each step's own margin figure below).
  const lastExpiry = legs.reduce<string | null>((m, l) => (m === null || l.expiry > m ? l.expiry : m), null);
  const events = useEventRisk(open && legs.length > 0, legs[0]?.underlying ?? chain.symbol, lastExpiry);
  // FNO-8B-28: his own liquidity limits, only while he turned them on (the basket is a v2 page part); words only.
  const limits = activeLimits(useLiquidityLimits(open && legs.length > 0, fetcher));
  const checks = useMemo<(RiskLine | null)[]>(() => [
    eventLine(events, lastExpiry),
    legs.length ? liquidityLine(legs.map((l) => legLiquidity(l.expiry === live?.expiry && l.underlying === chain.symbol ? rows : [], l.strike, l.right,
      l.label ?? `${formatNumber(l.strike, 0)} ${l.right}`)), true, limits) : null,
  ], [events, lastExpiry, legs, live?.expiry, chain.symbol, rows, limits]);

  useEffect(() => {
    if (!open) return undefined;
    setStep({ kind: "edit" });
    setNote(null);
    let alive = true;
    api.settings(chain.symbol).then((s) => { if (alive) setSettings(s); }).catch(() => { if (alive) setSettings(null); });
    return () => { alive = false; };
  }, [open, chain.symbol, api]);

  // The numbers follow every edit (debounced); an older reply never overwrites a newer one.
  useEffect(() => {
    const n = ++seq.current;
    if (!open || !legs.length) { setNumbers(null); setProblems([]); return undefined; }
    if (!spot) { setNumbers(null); setProblems(["The price of the underlying is not known yet: the numbers appear once the chain loads."]); return undefined; }
    const other = legs.find((l) => l.underlying !== chain.symbol);
    if (other) { setNumbers(null); setProblems([`These legs are on ${other.underlying}: open its option chain to see the numbers.`]); return undefined; }
    const timer = window.setTimeout(() => {
      api.evaluate(basketAsk(legs.map(strip), spot, product)).then((out) => {
        if (n !== seq.current) return;
        if (out.ok) { setNumbers(out); setProblems([]); } else { setNumbers(null); setProblems(out.errors); }
      }).catch((e: unknown) => { if (n === seq.current) setProblems([e instanceof Error ? e.message : String(e)]); });
    }, EVAL_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [open, legs, spot, product, api, chain.symbol]);

  async function ready(name: StrategyName) {
    if (pick === null || !live?.expiry) { setNote({ tone: "warn", text: "The chain is not loaded yet." }); return; }
    if (legs.length && !window.confirm(`Replace the ${legs.length} leg${legs.length === 1 ? "" : "s"} in your basket with a ${name}?`)) return;
    let next: { expiry: string; rows: LiveChain["rows"] } | null = null;
    if (name === "Calendar") {
      const later = chain.expiries.map((e) => e.date).filter((d) => d > (live.expiry ?? "")).sort()[0];
      if (later && chain.loadChain) {
        try { next = { expiry: later, rows: (await chain.loadChain(later)).rows }; } catch { next = null; }
      }
    }
    const s = settings ?? { width_steps: 2, lots: 1, stop_pct: 50 };
    const built = readyStrategy(name, { underlying: chain.symbol, expiry: live.expiry, rows, around: pick, settings: s, next });
    if (built.problem) { setNote({ tone: "warn", text: built.problem }); return; }
    store.replace(built.legs);
    setNote({ tone: "done", text: `${name} around ${formatNumber(pick, 0)}: ${built.legs.length} legs. Edit any leg before you execute.` });
  }

  function moveStrike(id: string, strike: number) {
    const leg = legs.find((l) => l.id === id);
    if (!leg || leg.right === "FUT") return;                   // a future has no strike to move
    const row = leg.expiry === live?.expiry ? rows.find((r) => r.strike === strike) : undefined;
    const quote = row ? (leg.right === "CE" ? row.call : row.put) : null;
    const price = quote?.ltp && quote.ltp > 0 ? quote.ltp : leg.price;
    store.update(id, { strike, label: null, price, iv: quote?.iv ?? leg.iv ?? null,
      stopLoss: leg.side === "SELL" && settings ? stopFor(price, settings.stop_pct) ?? leg.stopLoss : leg.stopLoss });
  }

  async function confirm(subset?: NewLeg[]) {
    const sending = subset ?? legs.map(strip);
    const remaining = subset !== undefined;
    const missing = [...basketProblems(sending), ...(remaining ? [] : exitErrors(exitForm))];
    if (missing.length) { setNote({ tone: "warn", text: missing[0] }); return; }
    ref.current = newClientRef();
    setStep({ kind: "confirm", preview: null, legs: sending, remaining });
    setBusy(true);
    try {
      const preview = await api.preview(basketAsk(sending, sending.every((l) => l.underlying === chain.symbol) ? spot : null, product));
      setStep({ kind: "confirm", preview, legs: sending, remaining });
    } catch (e) {
      setStep({ kind: "edit" });
      setNote({ tone: "warn", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }

  async function execute() {
    if (step.kind !== "confirm" || product === null) return;
    setBusy(true);
    try {
      // SX3: the strategy exit goes with the first run only (the remaining legs keep their own stops).
      const exits = step.remaining ? null : exitsAsk(exitForm);
      const result = await api.execute({ ...basketAsk(step.legs, spot, product), basket_ref: ref.current, ...(exits ? { exits } : {}) });
      setStep({ kind: "done", result, legs: step.legs });
      if (result.placed > 0) onPlaced?.(result);
    } catch (e) {
      setNote({ tone: "warn", text: `${e instanceof Error ? e.message : String(e)} Check Positions before trying again.` });
      setStep({ kind: "edit" });
    } finally {
      setBusy(false);
    }
  }

  async function saveExits() {
    try {
      const version = exitVersion ?? exitDefaults?.version ?? 0;
      const out = await saveExitDefaults(defaultsOfForm(exitForm), version, fetcher);
      setExitVersion(out.version);
      setExitNote("Saved as your defaults.");
    } catch (e) {
      setExitNote(e instanceof Error ? e.message : String(e));
    }
  }

  async function openTool(next: Tool) {
    setTool(tool === next ? null : next);
    if (next && !saved) {
      try { setSaved(await loadBaskets(fetcher)); } catch (e) { setNote({ tone: "warn", text: e instanceof Error ? e.message : String(e) }); }
    }
  }

  async function writeSaved(items: SavedState["items"], done: string) {
    if (!saved) return;
    const out = await saveBaskets(items, saved.version, fetcher);
    if (out.ok) { setSaved(out.state); setNote({ tone: "done", text: done }); setTool(null); }
    else { if (out.conflict) setSaved(out.conflict); setNote({ tone: "warn", text: out.message }); }
  }

  async function save() {
    const bad = nameProblem(saveName);
    if (bad || !saved) { setNote({ tone: "warn", text: bad ?? "Your saved baskets are still loading." }); return; }
    if (Object.keys(saved.items).length >= MAX_SAVED) { setNote({ tone: "warn", text: `You can keep ${MAX_SAVED} baskets. Delete one first.` }); return; }
    await writeSaved({ ...saved.items, [newSavedId()]: savedOf(legs.map(strip), saveName, product) }, `Saved "${saveName.trim()}".`);
  }

  function load(id: string) {
    const s = saved?.items[id];
    if (!s) return;
    if (legs.length && !window.confirm(`Replace the ${legs.length} leg${legs.length === 1 ? "" : "s"} in your basket with "${s.name}"?`)) return;
    store.replace(legsOf(s));
    if (s.product) setProduct(s.product);
    setTool(null);
    setNote({ tone: "done", text: `Loaded "${s.name}". Prices are the ones saved; the confirm step reads the live ones.` });
  }

  async function remove(id: string) {
    if (!saved) return;
    const { [id]: _gone, ...rest } = saved.items;
    await writeSaved(rest, "Deleted.");
  }

  function openBuilder() {
    if (!numbers || !spot) return;
    const h = builderHandoff(legs.map(strip), numbers.lot_sizes, spot, strikeStep(rows));
    if (!h || !writeHandoff(h)) { setNote({ tone: "warn", text: "Every leg needs a price before it can open in the Options Builder." }); return; }
    window.location.assign("/option-chain/plan");
  }

  async function brokerApp() {
    try {
      const link = await api.brokerLink(basketAsk(legs.map(strip), spot, product));
      const form = link.available ? kiteFnoBasketLink(link.orders, link.publisher_api_key) : null;
      if (!form) { setNote({ tone: "warn", text: link.available ? "This basket cannot be pre-filled in your broker app." : link.why }); return; }
      openBrokerAppLink(form);
      setNote({ tone: "done", text: `Opened in ${form.brokerLabel}: ${form.summary}. Review and place it there; ATP placed nothing.` });
    } catch (e) {
      setNote({ tone: "warn", text: e instanceof Error ? e.message : String(e) });
    }
  }

  const handlers: BodyHandlers = {
    onLeg: (id, patch) => store.update(id, patch),
    onRemove: (id) => store.remove(id),
    onReady: (name) => { void ready(name); },
    onAround: setAround,
    onProduct: setProduct,
    onStrike: moveStrike,
    onReview: () => { void confirm(); },
    onFlip: () => legs.forEach((l) => store.update(l.id, { side: l.side === "BUY" ? "SELL" : "BUY" })),
    onClear: () => { if (window.confirm("Remove every leg from the basket?")) store.clear(); },
    onTool: (t) => { void openTool(t); },
    onSaveName: setSaveName,
    onSave: () => { void save(); },
    onLoad: load,
    onDelete: (id) => { void remove(id); },
    onBuilder: openBuilder,
    onBrokerApp: () => { void brokerApp(); },
  };

  return (
    <RD.Root open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <RD.Portal>
        <RD.Overlay className="fixed inset-0 z-40 bg-scrim/50" />
        <RD.Content aria-describedby={undefined} data-basket-panel data-step={step.kind}
          className="fixed inset-x-0 bottom-0 z-50 flex max-h-[94dvh] flex-col overflow-hidden rounded-t-panel border border-border bg-surface-1 text-fg shadow-raised focus:outline-none md:inset-y-0 md:left-auto md:right-0 md:max-h-none md:w-[44rem] md:rounded-none md:rounded-l-panel">
          <div className="flex items-center justify-between gap-space-3 px-space-4 pt-space-3">
            <div className="flex min-w-0 items-center gap-space-2">
              <RD.Title className="text-t18 font-semibold text-fg">
                Basket <span className="text-t14 font-normal text-fg-muted">{legs.length} leg{legs.length === 1 ? "" : "s"}{chain.symbol ? ` · ${chain.symbol}` : ""}</span>
              </RD.Title>
              <PracticeFrame />
            </div>
            <RD.Close aria-label="Close" className="-mr-space-2 flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg">
              <X size={18} aria-hidden />
            </RD.Close>
          </div>
          {step.kind === "edit" && (
            <BasketBody legs={legs} numbers={numbers} problems={problems} settings={settings} rows={rows} pick={pick} atm={atm} spot={spot}
              product={product} note={note} tool={tool} saved={saved} saveName={saveName} idPrefix={idPrefix} on={handlers} budget={budget} checks={checks}
              exits={(
                <StrategyExitsV2 form={exitForm} legCount={legs.length} saveNote={exitNote} onSaveDefaults={() => { void saveExits(); }}
                  onChange={(patch) => { setExitNote(null); setExitTyped({ ...exitForm, ...patch }); }} />
              )}
              compare={tool === "compare" ? (
                <StrategyCompareV2 underlying={chain.symbol} legs={legs.map(strip)} saved={saved} rows={rows} expiry={live?.expiry ?? null} around={pick}
                  settings={settings} spot={spot} product={product} api={api} />
              ) : null} />
          )}
          {step.kind === "confirm" && (
            <ConfirmView preview={step.preview} busy={busy} budget={budget} checks={checks} onBack={() => setStep({ kind: "edit" })} onPlace={() => { void execute(); }}
              exitWords={step.remaining ? "The exits for this strategy were set with the first legs; these legs keep their own stops."
                : <ExitSentenceV2 parts={exitSentence(exitForm, step.legs.length, step.legs[0]?.underlying ?? null)} />} />
          )}
          {step.kind === "done" && (
            <ResultView result={step.result} onRemaining={() => { void confirm(remainingLegs(step.legs, step.result)); }} onEdit={() => setStep({ kind: "edit" })}
              onClear={() => { store.clear(); onClose(); }} onClose={onClose} api={api} />
          )}
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  );
}

export interface BodyHandlers {
  onLeg: (id: string, patch: Partial<NewLeg>) => void;
  onRemove: (id: string) => void;
  onReady: (name: StrategyName) => void;
  onAround: (strike: number) => void;
  onProduct: (p: Product) => void;
  onStrike: (id: string, strike: number) => void;
  onReview: () => void;
  onFlip: () => void;
  onClear: () => void;
  onTool: (t: Tool) => void;
  onSaveName: (name: string) => void;
  onSave: () => void;
  onLoad: (id: string) => void;
  onDelete: (id: string) => void;
  onBuilder: () => void;
  onBrokerApp: () => void;
}

/** The builder's legs for the payoff chart (needs the lot sizes from the server's answer). */
export function chartLegs(legs: readonly BasketLeg[], numbers: BasketNumbers): BuilderLeg[] {
  return legs.map((l, i) => ({ id: l.id, direction: l.side, option_type: l.right, strike: l.right === "FUT" ? l.price ?? 0 : l.strike, premium: l.price ?? 0, lots: l.lots,
    lot_size: numbers.lot_sizes[i] ?? 1, expiry: l.expiry, iv: typeof l.iv === "number" ? l.iv / 100 : null }));
}

function Figure({ label, children, sub, testId }: { label: string; children: ReactNode; sub?: string; testId?: string }) {
  return (
    <div className="flex items-start justify-between gap-space-3 border-t border-subtle py-space-2 first:border-t-0" data-figure={testId}>
      <dt className="text-t13 text-fg-muted">{label}</dt>
      <dd className="text-right text-t14 font-tabular text-fg">
        {children}
        {sub && <span className="block max-w-[18rem] text-t12 text-fg-muted">{sub}</span>}
      </dd>
    </div>
  );
}

/** The panel's edit view: ready strategies, the legs, the numbers and the tools (no state, no hooks). */
export function BasketBody({ legs, numbers, problems, settings, rows, pick, atm, spot, product, note, tool, saved, saveName, idPrefix, on, budget, compare, checks, exits }: {
  legs: readonly BasketLeg[]; numbers: BasketNumbers | null; problems: string[]; settings: BasketSettings | null; rows: LiveChain["rows"];
  pick: number | null; atm: number | null; spot: number | null; product: Product | null; note: Note; tool: Tool; saved: SavedState | null;
  saveName: string; idPrefix: string; on: BodyHandlers;
  /** 8b-d: the user's own risk budget for the risk line (null: the line is left out). */
  budget?: RiskBudget | null;
  /** 8b-j: the comparison, shown while the Compare tool is open. */
  compare?: ReactNode;
  /** 8b slice 4: the events and liquidity parts of the risk line (the margin part is added here). */
  checks?: readonly (RiskLine | null)[];
  /** SX1: "Exits for this strategy", shown before Review once there is a leg. */
  exits?: ReactNode;
}) {
  const step = strikeStep(rows);
  const ext = numbers?.extremes ?? null;
  const hedge = numbers?.margin.hedge ?? null;
  const g = numbers?.summary.greeks;
  const underlying = legs[0]?.underlying ?? "";
  return (
    <>
      <div className="min-h-0 flex-1 space-y-space-4 overflow-y-auto px-space-4 py-space-3" data-basket-body>
        <section aria-label="Ready strategies" data-basket="ready" className="space-y-space-2">
          <div className="flex items-end justify-between gap-space-3">
            <h3 className="text-t14 font-semibold text-fg">Ready strategies</h3>
            <label className="flex items-center gap-space-2 text-t12 text-fg-muted">
              Around
              <select value={pick ?? ""} onChange={(e) => on.onAround(Number(e.target.value))} disabled={!rows.length} data-basket="around"
                className="h-11 rounded-control border border-border bg-surface-2 px-space-2 text-t14 font-tabular text-fg md:h-9">
                {rows.map((r) => <option key={r.strike} value={r.strike}>{formatNumber(r.strike, 0)}{r.strike === atm ? " (ATM)" : ""}</option>)}
              </select>
            </label>
          </div>
          <div className="-mx-space-4 flex gap-space-2 overflow-x-auto px-space-4 pb-space-1">
            {STRATEGIES.map((name) => (
              <button key={name} type="button" className={CHIP} disabled={!rows.length} title={STRATEGY_TEXT[name]} onClick={() => on.onReady(name)} data-strategy={name}>
                {name}
              </button>
            ))}
          </div>
          <p className="text-t12 text-fg-muted" data-basket="settings-note">
            {settings?.note ?? "Legs are built from the chain's prices around the strike you pick; edit any leg."}
          </p>
        </section>

        {note && (
          <p role="status" data-basket-note={note.tone}
            className={cx("rounded-control px-space-3 py-space-2 text-t13", note.tone === "done" ? "bg-surface-2 text-fg" : "bg-warn-tint text-warn")}>
            {note.text}
          </p>
        )}

        <section aria-label="Legs" className="space-y-space-2">
          {legs.length === 0 && (
            <p className="rounded-control bg-surface-2 px-space-3 py-space-3 text-t13 text-fg-muted" data-basket="empty">
              No legs yet. Tap a strike on the chain and choose Add to basket, or pick a ready strategy.
            </p>
          )}
          <ol className="space-y-space-2" data-basket="legs">
            {legs.map((l, i) => (
              <LegRow key={l.id} leg={l} n={i + 1} lotSize={numbers?.lot_sizes[i] ?? null} idPrefix={`${idPrefix}-${i}`} on={on} />
            ))}
          </ol>
        </section>

        {legs.length > 0 && (
          <section aria-label="The basket's numbers" className="space-y-space-3" data-basket="numbers">
            {problems.length > 0 && (
              <ul role="alert" className="space-y-space-1 rounded-control bg-warn-tint px-space-3 py-space-2 text-t13 text-warn" data-basket="problems">
                {problems.map((p) => <li key={p}>{p}</li>)}
              </ul>
            )}
            {numbers && spot && (
              <>
                <div className="rounded-panel border border-subtle p-space-2" data-basket="payoff">
                  <PayoffCanvas evaluation={numbers} legs={chartLegs(legs, numbers)} spot={spot} step={step ?? 50} onStrikeChange={on.onStrike} />
                </div>
                {/* UI-28 U1.2: the payoff in words is on demand (ⓘ Details), never a paragraph under the chart. */}
                <Details id="basket.payoff-words" label="In plain words" className="mt-0"><p data-basket="plain-words">{payoffSentence(numbers, underlying)}</p></Details>
                <RiskLineV2 line={ext ? riskLine(ext.max_loss === null ? null : Math.abs(ext.max_loss), ext.unbounded_loss, budget) : null} />
                {checks && <RiskChecksV2 testId="basket" lines={[...checks, marginLine(hedge?.with_hedge.value ?? numbers.margin.value, budget)]} />}
                <dl className="rounded-control border border-subtle px-space-3">
                  <Figure label={numbers.net_premium >= 0 ? "Net premium received" : "Net premium paid"} testId="net">{moneyText(Math.abs(numbers.net_premium))}</Figure>
                  <Figure label="Max profit" testId="max-profit">{ext ? (ext.unbounded_profit ? "Unlimited" : moneyText(ext.max_profit)) : "See the chart"}</Figure>
                  <Figure label="Max loss" testId="max-loss">{ext ? (ext.unbounded_loss ? "Unlimited" : moneyText(ext.max_loss)) : "See the chart"}</Figure>
                  <Figure label="Breakevens" testId="breakevens">{numbers.breakevens?.length ? numbers.breakevens.map((b) => formatNumber(Math.round(b), 0)).join(" · ") : "–"}</Figure>
                  <Figure label="Probability of profit" testId="pop" sub="Model estimate">{`${(numbers.summary.pop * 100).toFixed(1)}%`}</Figure>
                  {hedge && (
                    <Figure label="Margin (estimate)" testId="margin" sub={`${hedge.with_hedge.basis}${hedge.hedged ? " With the hedge legs." : ""}`}>
                      {hedge.with_hedge.value !== null ? moneyText(hedge.with_hedge.value) : "From your broker"}
                    </Figure>
                  )}
                  {hedge?.without_hedge && (
                    <Figure label="Without the hedge legs" testId="margin-unhedged" sub={hedge.without_hedge.basis}>
                      {hedge.without_hedge.value !== null ? moneyText(hedge.without_hedge.value) : "From your broker"}
                    </Figure>
                  )}
                </dl>
                {g && (
                  <dl className="grid grid-cols-4 gap-space-2 rounded-control bg-surface-2 px-space-2 py-space-2 text-center" aria-label="The basket's Greeks" data-basket="greeks">
                    {([["Delta", g.delta.toFixed(1)], ["Gamma", g.gamma.toFixed(3)], ["Theta", g.theta.toFixed(0)], ["Vega", g.vega.toFixed(0)]] as const).map(([k, v]) => (
                      <div key={k} className="min-w-0"><dt className="text-t12 text-fg-muted">{k}</dt><dd className="truncate font-tabular text-t14 text-fg">{v}</dd></div>
                    ))}
                  </dl>
                )}
                {hedge && <p className="text-t12 text-fg-muted">{hedge.note} {numbers.disclaimer}</p>}
                <ExplainV2 testId="basket" underlying={underlying} greeks={g ?? null} structure={structureOf(legs)} />
              </>
            )}
          </section>
        )}

        {tool === "save" && (
          <section aria-label="Save this basket" className="space-y-space-2 rounded-control border border-subtle p-space-3" data-basket="save">
            <label htmlFor={`${idPrefix}-name`} className="block text-t12 font-medium text-fg-muted">Name</label>
            <div className="flex gap-space-2">
              <input id={`${idPrefix}-name`} value={saveName} maxLength={40} onChange={(e) => on.onSaveName(e.target.value)} className={FIELD} data-basket="save-name" />
              <button type="button" onClick={on.onSave} disabled={!saved || !legs.length} className={TOOL} data-basket="save-confirm">Save</button>
            </div>
          </section>
        )}
        {tool === "load" && (
          <section aria-label="Saved baskets" className="space-y-space-2 rounded-control border border-subtle p-space-3" data-basket="load">
            {!saved && <p className="text-t13 text-fg-muted">Loading your saved baskets…</p>}
            {saved && Object.keys(saved.items).length === 0 && <p className="text-t13 text-fg-muted">No saved baskets yet.</p>}
            <ul className="space-y-space-1">
              {saved && Object.entries(saved.items).map(([id, s]) => (
                <li key={id} className="flex items-center justify-between gap-space-2" data-saved={id}>
                  <span className="min-w-0 truncate text-t14 text-fg">{s.name} <span className="text-t12 text-fg-muted">{s.underlying} · {s.legs.length} legs</span></span>
                  <span className="flex shrink-0 gap-space-1">
                    <button type="button" onClick={() => on.onLoad(id)} className={TOOL} data-saved-load={id}>Load</button>
                    <button type="button" onClick={() => on.onDelete(id)} aria-label={`Delete ${s.name}`} className={TOOL} data-saved-delete={id}><Trash2 size={16} aria-hidden /></button>
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}

        {legs.length > 0 && exits}

        {tool === "compare" && compare}

        <div className="flex flex-wrap gap-space-2" data-basket="tools">
          <button type="button" className={TOOL} onClick={() => on.onTool("save")} disabled={!legs.length} aria-expanded={tool === "save"} data-tool="save"><Save size={16} aria-hidden />Save</button>
          <button type="button" className={TOOL} onClick={() => on.onTool("load")} aria-expanded={tool === "load"} data-tool="load"><FolderOpen size={16} aria-hidden />Load</button>
          <button type="button" className={TOOL} onClick={() => on.onTool("compare")} disabled={!rows.length && !legs.length} aria-expanded={tool === "compare"} data-tool="compare"><Scale size={16} aria-hidden />Compare</button>
          <button type="button" className={TOOL} onClick={on.onBuilder} disabled={!numbers} data-tool="builder"><Layers size={16} aria-hidden />Open in Options Builder</button>
          <button type="button" className={TOOL} onClick={on.onBrokerApp} disabled={!legs.length} data-tool="broker-app"><ExternalLink size={16} aria-hidden />Trade in your broker app</button>
          <button type="button" className={TOOL} onClick={on.onFlip} disabled={!legs.length} data-tool="flip"><ArrowLeftRight size={16} aria-hidden />Flip Buy / Sell</button>
          <button type="button" className={TOOL} onClick={on.onClear} disabled={!legs.length} data-tool="clear"><Trash2 size={16} aria-hidden />Clear</button>
        </div>
      </div>

      {product === null && <p className="border-t border-subtle px-space-4 pt-space-2 text-t13 text-fg-muted" data-basket="product-unset">{PRODUCT_UNSET_TEXT}</p>}
      <div className="flex items-center gap-space-2 border-t border-subtle px-space-4 py-space-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))]">
        <div role="radiogroup" aria-label="Product" className="flex shrink-0 gap-space-1 rounded-control bg-surface-2 p-space-1" data-basket="product">
          {(["MIS", "NRML"] as const).map((p) => (
            <button key={p} type="button" role="radio" aria-checked={product === p} onClick={() => on.onProduct(p)} data-option={p}
              className={cx("min-h-11 rounded-control px-space-3 text-t13 font-semibold", product === p ? "bg-surface-1 text-fg shadow-card" : "text-fg-muted")}>
              {p}
            </button>
          ))}
        </div>
        <button type="button" onClick={on.onReview} disabled={!legs.length || product === null} data-basket="execute"
          className="min-h-11 flex-1 rounded-control bg-brand px-space-4 text-t15 font-semibold text-on-brand disabled:opacity-50">
          Execute basket
        </button>
      </div>
    </>
  );
}

function LegRow({ leg: l, n, lotSize, idPrefix, on }: { leg: BasketLeg; n: number; lotSize: number | null; idPrefix: string; on: BodyHandlers }) {
  const buy = l.side === "BUY";
  return (
    <li className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-space-2 gap-y-space-2 rounded-control border border-subtle px-space-3 py-space-2"
      data-leg={n} data-side={l.side}>
      <button type="button" onClick={() => on.onLeg(l.id, { side: buy ? "SELL" : "BUY" })} aria-label={`Leg ${n}: ${sideWord(l.side)}. Tap to change`}
        className={cx("min-h-11 w-14 rounded-control text-t13 font-semibold text-on-brand", buy ? "bg-up" : "bg-down")} data-leg-side>
        {sideWord(l.side)}
      </button>
      <span className="min-w-0">
        <span className="block truncate text-t14 font-semibold text-fg" data-leg-label>{legLabel(l)}</span>
        <span className="block text-t12 text-fg-muted font-tabular">
          {lotSize ? `${formatNumber(l.lots * lotSize, 0)} qty` : `${l.lots} lot${l.lots === 1 ? "" : "s"}`}{l.price ? ` · ${formatPrice(l.price)}` : ""}
        </span>
      </span>
      <button type="button" onClick={() => on.onRemove(l.id)} aria-label={`Remove leg ${n}`} data-leg-remove
        className="flex h-11 w-11 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg">
        <X size={16} aria-hidden />
      </button>
      <div className="col-span-3 grid grid-cols-[auto_minmax(0,1fr)_minmax(0,1fr)] items-end gap-space-2">
        <div className="flex items-center gap-space-1" aria-label={`Lots of leg ${n}`}>
          <button type="button" aria-label={`One lot less on leg ${n}`} disabled={l.lots <= 1} onClick={() => on.onLeg(l.id, { lots: Math.max(1, l.lots - 1) })}
            className="flex h-11 w-11 items-center justify-center rounded-control border border-border text-fg disabled:opacity-40" data-leg-less><Minus size={14} aria-hidden /></button>
          <span className="w-8 text-center text-t14 font-tabular text-fg" data-leg-lots>{l.lots}</span>
          <button type="button" aria-label={`One lot more on leg ${n}`} disabled={l.lots >= 500} onClick={() => on.onLeg(l.id, { lots: Math.min(500, l.lots + 1) })}
            className="flex h-11 w-11 items-center justify-center rounded-control border border-border text-fg disabled:opacity-40" data-leg-more><Plus size={14} aria-hidden /></button>
        </div>
        <label className="min-w-0 text-t12 text-fg-muted">
          Price
          <input inputMode="decimal" id={`${idPrefix}-price`} defaultValue={l.price ?? ""} key={`p${l.price ?? ""}`} className={FIELD} data-leg-price
            onBlur={(e) => on.onLeg(l.id, { price: num(e.target.value) })} />
        </label>
        {needsStop(l) ? (
          <label className="min-w-0 text-t12 text-fg-muted">
            Stop-loss
            <input inputMode="decimal" id={`${idPrefix}-stop`} defaultValue={l.stopLoss ?? ""} key={`s${l.stopLoss ?? ""}`} className={FIELD} data-leg-stop
              onBlur={(e) => on.onLeg(l.id, { stopLoss: num(e.target.value) })} />
          </label>
        ) : <span aria-hidden />}
      </div>
    </li>
  );
}

/** The confirm step: every leg as it would be placed (hedge legs first), the total margin and max loss (no state). */
export function ConfirmView({ preview: p, busy, onBack, onPlace, budget, checks, exitWords: exitLine }: {
  preview: BasketPreview | null; busy: boolean; onBack: () => void; onPlace: () => void;
  /** SX5: the exits for this strategy in plain words (absent: the line is left out). */
  exitWords?: ReactNode;
  /** 8b-d: the risk line beside the total max loss (information only: the button reads `can_execute` alone). */
  budget?: RiskBudget | null;
  /** 8b slice 4: the events and liquidity parts (the margin part is added from the totals). */
  checks?: readonly (RiskLine | null)[];
}) {
  const hedge = p?.totals?.margin.hedge ?? null;
  const ext = p?.totals?.extremes ?? null;
  return (
    <>
      <div className="min-h-0 flex-1 space-y-space-3 overflow-y-auto px-space-4 py-space-3" data-basket="confirm">
        {!p && <p className="text-t14 text-fg-muted">Checking every leg at the live price…</p>}
        {p && (
          <>
            <p className="text-t13 text-fg-muted">The order they are placed in: hedge legs first, then the sold legs, each at the live price.</p>
            <ol className="space-y-space-2" data-basket="confirm-legs">
              {p.legs.map((r) => {
                const issues = [...r.errors, ...r.refusals];
                return (
                  <li key={r.seq} className="rounded-control border border-subtle px-space-3 py-space-2" data-confirm-leg={r.seq} data-role={r.role}>
                    <div className="flex items-center justify-between gap-space-2">
                      <span className="min-w-0 truncate text-t14 font-semibold text-fg">
                        {r.seq}. {sideWord(r.side)} {r.label}
                      </span>
                      <span className="shrink-0 rounded-pill bg-surface-2 px-space-2 text-t12 text-fg-muted">{r.role === "hedge" ? "Hedge" : "Sold leg"}</span>
                    </div>
                    <p className="text-t13 text-fg-muted font-tabular">
                      {r.units ? `${formatNumber(r.units, 0)} qty` : `${r.lots} lot(s)`} · {r.fill_price !== null ? `at ${formatPrice(r.fill_price)}` : "no fill price"}
                      {r.stop_loss ? ` · stop ${formatPrice(r.stop_loss)}` : ""}
                    </p>
                    {issues.length > 0 && <ul className="mt-space-1 space-y-space-1 text-t13 text-warn">{issues.map((x) => <li key={x}>{x}</li>)}</ul>}
                  </li>
                );
              })}
            </ol>
            {exitLine && (
              <div className="rounded-control border border-subtle bg-surface-2 px-space-3 py-space-2" data-basket="confirm-exits">
                <p className="text-t13 font-semibold text-fg">Exits for this strategy</p>
                <p className="text-t13 text-fg">{exitLine}</p>
              </div>
            )}
            {p.smart && <SmartConfirmV2 smart={p.smart} />}
            <dl className="rounded-control border border-subtle px-space-3" data-basket="confirm-totals">
              {!p.smart && (
                <Figure label="Total margin (estimate)" testId="total-margin" sub={hedge ? `${hedge.with_hedge.basis} ${hedge.note}` : undefined}>
                  {hedge && hedge.with_hedge.value !== null ? moneyText(hedge.with_hedge.value) : "From your broker"}
                </Figure>
              )}
              <Figure label="Max loss" testId="total-max-loss">{ext ? (ext.unbounded_loss ? "Unlimited" : moneyText(ext.max_loss)) : "See the chart"}</Figure>
              {p.totals && <Figure label="Charges to open (estimate)" testId="total-charges">{moneyText(p.totals.costs)}</Figure>}
            </dl>
            {ext && <RiskLineV2 line={riskLine(ext.max_loss === null ? null : Math.abs(ext.max_loss), ext.unbounded_loss, budget)} />}
            {checks && <RiskChecksV2 testId="basket-confirm" lines={[...checks, marginLine(hedge?.with_hedge.value, budget)]} />}
            {p.totals_errors.length > 0 && <p className="text-t13 text-fg-muted">{p.totals_errors[0]}</p>}
            {p.refusals.length > 0 && (
              <ul role="alert" className="space-y-space-1 rounded-control bg-warn-tint px-space-3 py-space-2 text-t13 text-warn" data-basket="confirm-refusals">
                {p.refusals.map((x) => <li key={x}>{x}</li>)}
              </ul>
            )}
          </>
        )}
      </div>
      <div className="flex gap-space-2 border-t border-subtle px-space-4 py-space-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))]">
        <button type="button" onClick={onBack} data-basket="back" className="min-h-11 rounded-control border border-border px-space-4 text-t14 font-semibold text-fg">Back</button>
        <button type="button" onClick={onPlace} disabled={busy || !p?.can_execute} data-basket="place"
          className="min-h-11 flex-1 rounded-control bg-brand px-space-4 text-t15 font-semibold text-on-brand disabled:opacity-50">
          {busy ? "Placing…" : p?.confirm_label ?? "Cannot place"}
        </button>
      </div>
    </>
  );
}

/** What happened to each leg, in plain words, and what the user can do (no state). */
export function ResultView({ result: r, onRemaining, onEdit, onClear, onClose, api = basketApi }: {
  result: BasketResult; onRemaining: () => void; onEdit: () => void; onClear: () => void; onClose: () => void;
  /** TM Part 7: "Close the filled legs" of an incomplete basket. */
  api?: Pick<BasketApi, "closeFilled">;
}) {
  const left = r.legs.some((l) => l.status !== "PLACED");
  // TM Part 7 (flag smart_execution): the incomplete state carries its own two choices.
  const incomplete = r.state === "INCOMPLETE";
  return (
    <>
      <div className="min-h-0 flex-1 space-y-space-3 overflow-y-auto px-space-4 py-space-3" data-basket="result" data-status={r.status}>
        <p role="status" className={cx("rounded-control px-space-3 py-space-3 text-t14", r.status === "PLACED" ? "bg-surface-2 text-fg" : "bg-warn-tint text-warn")}>
          {resultHeadline(r)}
        </p>
        {r.reasons.length > 0 && <ul className="space-y-space-1 text-t13 text-warn">{r.reasons.map((x) => <li key={x}>{x}</li>)}</ul>}
        {r.exits && (
          <p className={cx("rounded-control px-space-3 py-space-2 text-t13", r.exits.status === "ARMED" ? "bg-surface-2 text-fg" : "bg-warn-tint text-warn")}
            data-basket="result-exits" data-exits-status={r.exits.status}>
            {r.exits.status === "ARMED" ? <ExitResultV2 exits={r.exits} />
              : `Strategy exit not set: ${r.exits.reason}. The legs keep their own stops.`}
          </p>
        )}
        <ol className="space-y-space-2">
          {r.legs.map((l) => (
            <li key={l.seq} className="rounded-control border border-subtle px-space-3 py-space-2" data-result-leg={l.seq} data-leg-status={l.status}>
              <div className="flex items-center justify-between gap-space-2">
                <span className="min-w-0 truncate text-t14 font-semibold text-fg">{l.seq}. {sideWord(l.side)} {l.label}</span>
                <span className={cx("shrink-0 rounded-pill px-space-2 text-t12 font-semibold", l.status === "PLACED" ? "bg-surface-2 text-fg" : "bg-warn-tint text-warn")}>
                  {STATUS_WORD[l.status]}
                </span>
              </div>
              {l.filled && <p className="text-t13 text-fg-muted font-tabular">{formatNumber(l.filled.quantity, 0)} qty at {formatPrice(l.filled.price)}</p>}
              {l.reasons.length > 0 && <ul className="mt-space-1 space-y-space-1 text-t13 text-warn">{l.reasons.map((x) => <li key={x}>{x}</li>)}</ul>}
            </li>
          ))}
        </ol>
        {incomplete && <IncompleteV2 message={r.message ?? resultHeadline(r)} filledTradeIds={r.filled_trade_ids ?? []}
          pendingCount={r.legs.filter((l) => l.status !== "PLACED").length} onCompleteRest={onRemaining} api={api} />}
        {!incomplete && r.next_steps.length > 0 && <ul className="space-y-space-1 text-t13 text-fg" data-basket="next-steps">{r.next_steps.map((x) => <li key={x}>{x}</li>)}</ul>}
      </div>
      <div className="flex flex-wrap gap-space-2 border-t border-subtle px-space-4 py-space-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))]">
        {left && r.placed > 0 && !incomplete && (
          <button type="button" onClick={onRemaining} data-basket="remaining" className="min-h-11 flex-1 rounded-control bg-brand px-space-4 text-t14 font-semibold text-on-brand">
            Place the remaining legs
          </button>
        )}
        {r.placed === 0 && (
          <button type="button" onClick={onEdit} data-basket="edit" className="min-h-11 flex-1 rounded-control border border-border px-space-4 text-t14 font-semibold text-fg">
            Back to the basket
          </button>
        )}
        {r.status === "PLACED" && (
          <button type="button" onClick={onClear} data-basket="clear-done" className="min-h-11 flex-1 rounded-control border border-border px-space-4 text-t14 font-semibold text-fg">
            Clear the basket
          </button>
        )}
        <button type="button" onClick={onClose} data-basket="done" className="min-h-11 flex-1 rounded-control border border-border px-space-4 text-t14 font-semibold text-fg">Done</button>
      </div>
    </>
  );
}

/** SX / F3: the strategy exit set on the filled legs - a ₹ stop and target as the shared signed value, else the words. */
function ExitResultV2({ exits: x }: { exits: Extract<NonNullable<BasketResult["exits"]>, { status: "ARMED" }> }) {
  if (x.measure !== "MTM_INR" || (x.stop_now == null && x.target_value == null)) {
    return <>{`Strategy exit set on the filled legs: ${x.words} Change or remove it on Positions.`}</>;
  }
  return (
    <span title={x.words}>
      Strategy exit set on the filled legs
      {x.stop_now != null && <>: stop at <Signed value={x.stop_now} /></>}
      {x.target_value != null && <>{x.stop_now != null ? ", target " : ": target "}<Signed value={x.target_value} /></>}
      . Change or remove it on Positions.
    </span>
  );
}
