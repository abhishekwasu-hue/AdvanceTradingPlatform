import { errorSentence } from "../api/errors";
import * as RD from "@radix-ui/react-dialog";
import { ChevronRight, Minus, Plus, ShieldCheck, X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { cx } from "../components/primitives/cx";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { Details } from "../ui/Details";
import { activeLimits, useLiquidityLimits } from "../settings/liquidityLimits";
import { FROM_DEFAULTS, ticketPrefill, useTradingDefaults } from "../settings/tradingDefaults";
import { formatPrice } from "../ui/format";
import { useFeatures } from "../ui/useFeatures";
import { fnoApi, runOnOptions, type FnoApi, type MultiExitResult, type MultiResult, type OrderPreview, type OrderResult, type PracticeAccount, type Side } from "./api";
import {
  MAX_LOTS, ORDER_TYPES, PRACTICE_TEXT, PRACTICE_TITLE, PRODUCTS, askOf, clampLots, draftProblems, moneyText, needs, newClientRef,
  blockedBy, draftFromDefaults, newDraft, resultText, type DraftDefaults, shortcutSide, sideWord, stopHint, unitsText, type Draft, type TicketTarget,
  PRODUCT_UNSET_TEXT,
} from "./ticketModel";
import { ExplainV2, RiskLineV2, useRiskBudget } from "./InsightsV2";
import { riskLine, structureOf, ticketPayoffSentence, type RiskBudget, type RiskLine } from "./insights";
import type { ChainRow } from "../optionChain/types";
import { eventLine, legLiquidity, liquidityLine, marginLine, type EventRisk } from "./riskParts";
import { RiskChecksV2, useEventRisk } from "./RiskPartsV2";

/**
 * FNO-1 (owner order section 6): the ONE order ticket - used by the option chain now, built to be opened the same way
 * from the chart, a watchlist, a position or a basket leg later (it takes a `TicketTarget` and nothing page-specific).
 *
 * - Buy / Sell; quantity in LOTS with the units shown (lots x the instrument master's lot size); Market / Limit / SL /
 *   SL-M; MIS / NRML; the price helper (LTP, bid, ask fill the price field); a stop-loss for the position.
 * - "Review" asks the server for the confirm step: the price it fills at, the margin required and the max loss, the
 *   charges, and the risk checks in plain words - all BEFORE anything is placed. "Buy" / "Sell" places one PAPER
 *   order; a refusal says why. There is no LIVE path here and the AI never opens it with an order (ADR-0006).
 * - The "Practice mode (PAPER)" frame is always on the ticket. Keyboard: B = Buy, S = Sell (desktop; never while typing).
 * - A bottom sheet on a phone (thumb reach: the main button at the bottom, 44 px targets), a side panel from md.
 * `TicketBody` is the content without state (rendered directly by the tests); `OrderTicketV2` holds the state.
 */
export interface OrderTicketProps {
  open: boolean;
  target: TicketTarget | null;
  side: Side;
  onClose: () => void;
  /** After a PLACED order (the chain re-reads its "P" markers). */
  onPlaced?: (result: OrderResult) => void;
  api?: Pick<FnoApi, "preview" | "place"> & Partial<Pick<FnoApi, "practiceAccounts" | "placeMulti" | "exitMulti">>;
  /** 8b slice 4: the chain rows on screen when the ticket opened from the chain (its liquidity line); absent elsewhere
   * (the line then says bid-ask and open interest are not known here). */
  chainRows?: readonly ChainRow[] | null;
}

export type Step = { kind: "edit" } | { kind: "review"; preview: OrderPreview } | { kind: "done"; result: OrderResult }
  | { kind: "multi"; result: MultiResult; exit?: MultiExitResult };

/** M45 MA-6: "Run on" for the user's simulated PAPER accounts (only with `ui_v2` + `multi_account` and a Primary). */
export interface MultiTicket {
  options: { value: string; label: string }[];
  runOn: string;
  setRunOn: (value: string) => void;
  exitAll: () => Promise<void>;
}

const FIELD = "h-11 w-full rounded-control border border-border bg-surface-2 px-space-3 text-t14 text-fg font-tabular focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand";
const SEG = "flex min-h-11 flex-1 items-center justify-center rounded-control px-space-2 text-t14 font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";

function Segmented<T extends string>({ label, value, options, onChange, testId }: {
  label: string; value: T | null; options: { value: T; label: string; hint?: string }[]; onChange: (v: T) => void; testId: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} data-ticket={testId} className="flex gap-space-1 rounded-control bg-surface-2 p-space-1">
      {options.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={value === o.value} onClick={() => onChange(o.value)} data-option={o.value}
          className={cx(SEG, value === o.value ? "bg-surface-1 text-fg shadow-card" : "text-fg-muted hover:text-fg")}>
          {o.label}{o.hint && <span className="ml-space-1 hidden text-t12 font-normal text-fg-muted sm:inline">{o.hint}</span>}
        </button>
      ))}
    </div>
  );
}

function Line({ label, children, testId }: { label: string; children: ReactNode; testId?: string }) {
  return (
    <div className="flex items-start justify-between gap-space-3 border-t border-subtle py-space-2 first:border-t-0" data-line={testId}>
      <dt className="text-t13 text-fg-muted">{label}</dt>
      <dd className="text-right text-t14 font-tabular text-fg">{children}</dd>
    </div>
  );
}

/**
 * Trading Terminal T1: the ticket's state and its two server calls (Review = the confirm step, Place = one PAPER order),
 * shared by the modal ticket below and the Terminal's order panel, so both send exactly the same order through the same
 * path (`api.preview` / `api.place` with `askOf` and one client reference per ticket). `shortcuts: false` leaves the
 * B / S keys out (the Terminal's keyboard shortcuts are a later part).
 */
export interface OrderTicketState {
  draft: Draft; step: Step; busy: boolean; problem: string | null; idPrefix: string; defaults?: DraftDefaults;
  budget: RiskBudget | null; events: EventRisk | "error" | null; liquidity: RiskLine | null;
  onDraft: (patch: Partial<Draft>) => void; review: () => Promise<void>; place: () => Promise<void>; back: () => void;
  multi?: MultiTicket;
}

export function useOrderTicket({ open, target, side, onPlaced, api = fnoApi, chainRows, shortcuts = true }: Omit<OrderTicketProps, "onClose"> & {
  shortcuts?: boolean;
}): OrderTicketState {
  // Settings part C: the user's own lots / product / order type start a new ticket (v2 only; marked, changeable).
  const features = useFeatures();
  const saved = useTradingDefaults(features.on("ui_v2"));
  const defaults = useMemo<DraftDefaults | undefined>(() => {
    const p = ticketPrefill(saved);
    return Object.keys(p).length ? p : undefined;
  }, [saved]);
  const [draft, setDraft] = useState<Draft>(() => newDraft(side, defaults));
  const [step, setStep] = useState<Step>({ kind: "edit" });
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const ref = useRef<string>(newClientRef());
  const idPrefix = useId();
  // M45 MA-6: the user's simulated PAPER accounts, read when the ticket opens with `ui_v2` + `multi_account`.
  const multiOn = features.on("ui_v2") && features.on("multi_account") && typeof api.practiceAccounts === "function";
  const [practice, setPractice] = useState<PracticeAccount[]>([]);
  const [runOn, setRunOn] = useState("ONE");
  useEffect(() => {
    if (!open || !multiOn || !api.practiceAccounts) return;
    let live = true;
    api.practiceAccounts().then((a) => { if (live) setPractice(a); }).catch(() => { if (live) setPractice([]); });
    return () => { live = false; };
  }, [open, multiOn, api]);

  // A new target or side starts a fresh ticket (and a fresh id, so a second order is never mistaken for the first).
  useEffect(() => {
    if (!open) return;
    setDraft(newDraft(side, defaults));
    setStep({ kind: "edit" });
    setProblem(null);
    setRunOn("ONE");
    ref.current = newClientRef();
  }, [open, side, target?.underlying, target?.expiry, target?.strike, target?.right, defaults]);

  // 8b-d: the user's own risk budget, read on the confirm step for its risk line (information only, never a refusal).
  const budget = useRiskBudget(open && step.kind === "review");
  // 8b slice 4: the events calendar up to this contract's expiry, read on the confirm step (information only).
  const events = useEventRisk(open && step.kind === "review", target?.underlying, target?.expiry);
  // FNO-8B-28: his own liquidity limits, only while he turned them on (Settings -> My trading defaults); words only.
  const limits = activeLimits(useLiquidityLimits(open && features.on("ui_v2")));
  const liquidity = useMemo<RiskLine | null>(() => {
    if (!target) return null;
    if (!chainRows || target.right === "FUT" || target.strike === null) return liquidityLine([], false, limits);
    return liquidityLine([legLiquidity(chainRows, target.strike, target.right, target.label ?? "This strike")], true, limits);
  }, [chainRows, target, limits]);

  // Desktop shortcuts: B = Buy, S = Sell while the ticket is being filled in (never while typing in a field).
  useEffect(() => {
    if (!shortcuts || !open || step.kind !== "edit") return undefined;
    const onKey = (e: KeyboardEvent) => {
      const picked = shortcutSide(e, e.target);
      if (picked) { e.preventDefault(); setDraft((d) => ({ ...d, side: picked })); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [shortcuts, open, step.kind]);

  async function review() {
    const missing = draftProblems(draft, target?.right);
    if (!target || missing.length) { setProblem(missing[0] ?? null); return; }
    setBusy(true);
    try {
      setStep({ kind: "review", preview: await api.preview(askOf(target, draft, ref.current)) });
    } catch (e) {
      setProblem(errorSentence(e));
    } finally {
      setBusy(false);
    }
  }

  async function place() {
    if (!target) return;
    setBusy(true);
    try {
      if (runOn !== "ONE" && api.placeMulti) {
        const multi = await api.placeMulti(askOf(target, draft, ref.current), runOn);
        setStep({ kind: "multi", result: multi });
        const first = multi.results.find((r) => r.status === "PLACED");
        if (first) onPlaced?.({ status: "PLACED", trade_id: first.trade_id } as OrderResult);
        return;
      }
      const result = await api.place(askOf(target, draft, ref.current));
      setStep({ kind: "done", result });
      if (result.status === "PLACED") onPlaced?.(result);
    } catch (e) {
      setProblem(errorSentence(e));
    } finally {
      setBusy(false);
    }
  }

  async function exitAll() {
    if (step.kind !== "multi" || !api.exitMulti) return;
    setBusy(true);
    try {
      setStep({ ...step, exit: await api.exitMulti(ref.current) });
    } catch (e) {
      setProblem(errorSentence(e));
    } finally {
      setBusy(false);
    }
  }

  const options = multiOn ? runOnOptions(practice) : [];
  return {
    draft, step, busy, problem, idPrefix, defaults, budget, events, liquidity, review, place,
    multi: options.length ? { options, runOn, setRunOn, exitAll } : undefined,
    onDraft: (patch) => { setDraft((d) => ({ ...d, ...patch })); setProblem(null); },
    back: () => setStep({ kind: "edit" }),
  };
}

export default function OrderTicketV2({ open, target, side, onClose, onPlaced, api = fnoApi, chainRows }: OrderTicketProps) {
  const t = useOrderTicket({ open, target, side, onPlaced, api, chainRows });
  if (!target) return null;

  const label = target.label ?? (target.right === "FUT" ? `${target.underlying} ${target.expiry} FUT` : `${target.underlying} ${target.strike} ${target.right}`);
  return (
    <RD.Root open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <RD.Portal>
        <RD.Overlay className="fixed inset-0 z-40 bg-scrim/50" />
        <RD.Content aria-describedby={undefined} data-order-ticket data-step={t.step.kind}
          className="fixed inset-x-0 bottom-0 z-50 flex max-h-[92dvh] flex-col overflow-hidden rounded-t-panel border border-border bg-surface-1 text-fg shadow-raised focus:outline-none md:inset-y-0 md:left-auto md:right-0 md:max-h-none md:w-[26rem] md:rounded-none md:rounded-l-panel">
          <div className="flex items-start justify-between gap-space-3 px-space-4 pt-space-3">
            <div className="min-w-0">
              <div className="flex min-w-0 items-center gap-space-2">
                <RD.Title className="truncate text-t18 font-semibold text-fg">{label}</RD.Title>
                <PracticeFrame />
              </div>
              <p className="text-t13 text-fg-muted font-tabular">
                LTP {formatPrice(target.ltp)} · Bid {formatPrice(target.bid)} · Ask {formatPrice(target.ask)}
              </p>
            </div>
            <RD.Close aria-label="Close" className="-mr-space-2 flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg">
              <X size={18} aria-hidden />
            </RD.Close>
          </div>
          <TicketBody target={target} draft={t.draft} step={t.step} busy={t.busy} problem={t.problem} idPrefix={t.idPrefix} defaults={t.defaults}
            budget={t.budget} events={t.events} liquidity={t.liquidity}
            onDraft={t.onDraft} onReview={() => void t.review()} onPlace={() => void t.place()} onBack={t.back} onClose={onClose} multi={t.multi} />
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  );
}

/** The "Practice mode (PAPER)" frame on every ticket (owner order 8b-l); 8b slice 1: also on the positions page while
 * every position shown is PAPER (`className` rounds it there). */
/** UX-9: the "More options" summary line - the product and the position's stop-loss as they stand. */
export function advancedSummary(draft: Pick<Draft, "product" | "stopLoss">): string {
  const stop = draft.stopLoss.trim();
  return `${draft.product ?? "no product"} · ${stop ? `stop-loss ${stop}` : "no stop-loss"}`;
}

/** UX-9: the review asked for a stop-loss, so "More options" opens to show the field (the server's own words). */
export function needsStop(problem: string | null | undefined): boolean {
  return !!problem && /stop-loss/i.test(problem);
}

/** UI-28 U1.1: one "PAPER" chip beside the title (the mode labelled once); the sentence is its tooltip, not a band. */
export function PracticeFrame({ className }: { className?: string } = {}) {
  return (
    <span title={PRACTICE_TEXT} aria-label={`${PRACTICE_TITLE}: ${PRACTICE_TEXT}`}
      className={cx("inline-flex shrink-0 items-center gap-space-1 rounded-pill bg-warn-tint px-space-2 py-px text-t12 font-semibold text-warn", className)} data-practice-frame>
      <ShieldCheck size={12} aria-hidden />{PRACTICE_TITLE}
    </span>
  );
}

const sentence = (t: string) => t.charAt(0).toUpperCase() + t.slice(1);

/** UI-28 U3.4 (NN/g H5): "Exit on every account" asks first in the app, naming how many accounts it closes. Never disabled
 * by a check (ADR-0004); only "Exiting…" while the exits are on their way. */
export function ExitEveryAccount({ busy, accounts, onExit }: { busy: boolean; accounts: number; onExit: () => void }) {
  const [asking, setAsking] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setAsking(true)} disabled={busy} data-ticket="exit-all"
        className="min-h-11 rounded-control border border-border px-space-4 text-t14 font-semibold text-fg disabled:opacity-50">
        {busy ? "Exiting…" : "Exit on every account"}
      </button>
      <ConfirmDialog open={asking} onOpenChange={setAsking} title="Exit on every account?" confirmLabel="Exit on every account"
        consequence={`This closes the position on ${accounts} account${accounts === 1 ? "" : "s"} at the current price.`}
        onConfirm={() => { setAsking(false); onExit(); }} />
    </>
  );
}

/** The ticket's fields, the confirm step and the buttons (no state, no hooks). */
export function TicketBody({ target, draft, step, busy, problem, idPrefix, onDraft, onReview, onPlace, onBack, onClose, defaults, budget, events, liquidity, keysHint = true, stickyActions = false, multi }: {
  target: TicketTarget; draft: Draft; step: Step; busy: boolean; problem: string | null; idPrefix: string;
  /** Settings part C: the user's own ticket defaults, to mark the fields that still show them. */
  defaults?: DraftDefaults;
  /** 8b-d: the user's own risk budget for the risk line (null: the line is left out). */
  budget?: RiskBudget | null;
  /** 8b slice 4: the events up to the expiry and the strike's liquidity (information only). */
  events?: EventRisk | "error" | null;
  liquidity?: RiskLine | null;
  /** Trading Terminal T1: false where the B / S keys are not listened to (the Terminal's order panel). */
  keysHint?: boolean;
  onDraft: (patch: Partial<Draft>) => void; onReview: () => void; onPlace: () => void; onBack: () => void; onClose: () => void;
  /** Owner fix T1-2 (Terminal, phone): the main action (Review / Place) stays in view, just above the bottom navigation
   *  (4rem + its border + the home-indicator inset); from md it sits at the end of the panel as before. */
  stickyActions?: boolean;
  /** M45 MA-6: "Run on" for the user's simulated accounts (absent: the ticket is exactly as before). */
  multi?: MultiTicket;
}) {
  const n = needs(draft.orderType);
  const ids = { lots: `${idPrefix}-lots`, price: `${idPrefix}-price`, trigger: `${idPrefix}-trigger`, stop: `${idPrefix}-stop` };
  const marks = draftFromDefaults(draft, defaults);
  const fromDefaults = [marks.lots && "lots", marks.orderType && "order type", marks.product && "product"].filter(Boolean) as string[];
  const sideTone = draft.side === "BUY" ? "bg-up text-on-brand" : "bg-down text-on-brand";
  return (
    <>
      <div className={cx("min-h-0 flex-1 space-y-space-3 overflow-y-auto px-space-4 py-space-3", stickyActions && "pb-24 md:pb-space-3")} data-ticket-body>
        {step.kind === "edit" && (
          <>
            <Segmented label="Side" testId="side" value={draft.side} onChange={(v) => onDraft({ side: v })}
              options={[{ value: "BUY", label: "Buy" }, { value: "SELL", label: "Sell" }]} />
            {keysHint && <p className="hidden text-t12 text-fg-muted md:block">Keys: B for Buy, S for Sell.</p>}
            <div>
              <label htmlFor={ids.lots} className="mb-space-1 block text-t12 font-medium text-fg-muted">
                Lots{marks.lots && <span className="ml-space-1 font-normal text-brand" data-from-defaults="lots">{FROM_DEFAULTS}</span>}
              </label>
              <div className="flex items-center gap-space-2">
                <button type="button" aria-label="One lot less" onClick={() => onDraft({ lots: clampLots(draft.lots - 1) })} disabled={draft.lots <= 1}
                  className="flex h-11 w-11 shrink-0 items-center justify-center rounded-control border border-border text-fg disabled:opacity-40" data-ticket="lots-less">
                  <Minus size={16} aria-hidden />
                </button>
                <input id={ids.lots} inputMode="numeric" value={draft.lots} className={cx(FIELD, "text-center")}
                  onChange={(e) => onDraft({ lots: clampLots(Number(e.target.value || 1)) })} data-ticket="lots" />
                <button type="button" aria-label="One lot more" onClick={() => onDraft({ lots: clampLots(draft.lots + 1) })} disabled={draft.lots >= MAX_LOTS}
                  className="flex h-11 w-11 shrink-0 items-center justify-center rounded-control border border-border text-fg disabled:opacity-40" data-ticket="lots-more">
                  <Plus size={16} aria-hidden />
                </button>
              </div>
              <p className="mt-space-1 text-t13 text-fg font-tabular" data-ticket="units">{unitsText(draft.lots, target.lotSize)}</p>
            </div>
            <Segmented label="Order type" testId="order-type" value={draft.orderType} onChange={(v) => onDraft({ orderType: v })} options={ORDER_TYPES} />
            {draft.product === null && <p className="text-t12 text-fg-muted" data-ticket="product-unset">{PRODUCT_UNSET_TEXT}</p>}
            {(marks.orderType || marks.product) && (
              <p className="text-t12 text-fg-muted" data-from-defaults="type-product">
                {sentence(fromDefaults.filter((w) => w !== "lots").join(" and "))} {FROM_DEFAULTS}. Tap to change.
              </p>
            )}
            {(n.price || n.trigger) && (
              <div className="grid grid-cols-2 gap-space-2">
                {n.price && (
                  <div className={n.trigger ? "" : "col-span-2"}>
                    <label htmlFor={ids.price} className="mb-space-1 block text-t12 font-medium text-fg-muted">Limit price</label>
                    <input id={ids.price} inputMode="decimal" value={draft.price} onChange={(e) => onDraft({ price: e.target.value })} className={FIELD} data-ticket="price" />
                  </div>
                )}
                {n.trigger && (
                  <div className={n.price ? "" : "col-span-2"}>
                    <label htmlFor={ids.trigger} className="mb-space-1 block text-t12 font-medium text-fg-muted">Trigger price</label>
                    <input id={ids.trigger} inputMode="decimal" value={draft.trigger} onChange={(e) => onDraft({ trigger: e.target.value })} className={FIELD} data-ticket="trigger" />
                  </div>
                )}
                <div className="col-span-2 flex flex-wrap gap-space-2" aria-label="Price helper" data-ticket="price-helper">
                  {([["LTP", target.ltp], ["Bid", target.bid], ["Ask", target.ask]] as const).map(([name, v]) => (
                    <button key={name} type="button" disabled={typeof v !== "number"} data-helper={name}
                      onClick={() => { if (typeof v === "number") onDraft(n.price ? { price: v.toFixed(2) } : { trigger: v.toFixed(2) }); }}
                      className="min-h-11 rounded-pill border border-border px-space-3 text-t13 text-fg hover:bg-surface-2 disabled:opacity-40">
                      {name} {formatPrice(v)}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {/* UX-9 (ticket LOOK): side, lots, order type and its price stay in view; the product and the position's
                stop-loss sit under "More options", their current values in its summary line. What is sent is unchanged. */}
            <details className="group rounded-control border border-subtle" data-ticket="advanced" open={needsStop(problem) || draft.product === null || undefined}>
              <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-space-2 px-space-3 text-t13 font-medium text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand [&::-webkit-details-marker]:hidden">
                <span className="inline-flex items-center gap-space-1">
                  <ChevronRight size={14} aria-hidden className="transition-transform group-open:rotate-90" />More options
                </span>
                <span className="min-w-0 truncate font-tabular text-t12 font-normal text-fg-muted" data-ticket="advanced-summary">{advancedSummary(draft)}</span>
              </summary>
              <div className="space-y-space-3 border-t border-subtle px-space-3 py-space-3">
                <Segmented label="Product" testId="product" value={draft.product} onChange={(v) => onDraft({ product: v })} options={PRODUCTS} />
                <div>
                  <label htmlFor={ids.stop} className="mb-space-1 block text-t12 font-medium text-fg-muted">
                    Stop-loss for the position {stopHint(draft.side, target.right)}
                  </label>
                  <input id={ids.stop} inputMode="decimal" value={draft.stopLoss} onChange={(e) => onDraft({ stopLoss: e.target.value })} className={FIELD} data-ticket="stop" />
                </div>
              </div>
            </details>
            {multi && (
              <div>
                <label htmlFor={`${idPrefix}-run-on`} className="mb-space-1 block text-t12 font-medium text-fg-muted">Run on</label>
                <select id={`${idPrefix}-run-on`} value={multi.runOn} onChange={(e) => multi.setRunOn(e.target.value)} className={FIELD} data-ticket="run-on">
                  {multi.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
                {multi.runOn !== "ONE" && (
                  <p className="mt-space-1 text-t12 text-fg-muted">Your practice accounts each get their own order, sized by their own setting. The lots above are the Primary's.</p>
                )}
              </div>
            )}
          </>
        )}

        {step.kind === "review" && <Review preview={step.preview} budget={budget} events={events} liquidity={liquidity} />}

        {step.kind === "done" && (
          <p role="status" data-ticket="result"
            className={cx("rounded-control px-space-3 py-space-3 text-t14", step.result.status === "PLACED" ? "bg-surface-2 text-fg" : "bg-warn-tint text-warn")}>
            {resultText(step.result)}
          </p>
        )}

        {step.kind === "multi" && <MultiOutcome result={step.result} exit={step.exit} />}

        {problem && <p role="alert" className="rounded-control bg-warn-tint px-space-3 py-space-2 text-t13 text-warn" data-ticket="problem">{problem}</p>}
      </div>

      <div data-ticket-actions={stickyActions ? "sticky" : undefined}
        className={cx("flex gap-space-2 border-t border-subtle px-space-4 py-space-3",
          stickyActions
            // Phone: pinned just above the bottom navigation (the page scrolls the window, so "sticky" has no box to
            // stick in); from md: sticky at the end of the panel.
            ? "fixed inset-x-0 bottom-[calc(4rem+1px+env(safe-area-inset-bottom))] z-10 bg-surface-1 shadow-raised md:sticky md:inset-x-auto md:bottom-0 md:shadow-none md:pb-[calc(0.75rem+env(safe-area-inset-bottom))]"
            : "pb-[calc(0.75rem+env(safe-area-inset-bottom))]")}>
        {step.kind === "edit" && (
          <button type="button" onClick={onReview} disabled={busy || draft.product === null} data-ticket="review"
            className={cx("min-h-11 flex-1 rounded-control px-space-4 text-t15 font-semibold disabled:opacity-60", sideTone)}>
            {busy ? "Checking…" : `Review ${sideWord(draft.side)} order`}
          </button>
        )}
        {step.kind === "review" && (
          <>
            {(() => {
              const why = blockedBy(step.preview);
              return why && (
                <p className="basis-full text-t13 text-fg" data-ticket="blocked-by">
                  {why.text}{why.link && <> <a href={why.link} className="font-medium text-brand underline" data-ticket="change-limits">Change it in My limits</a></>}
                </p>
              );
            })()}
            <button type="button" onClick={onBack} data-ticket="back" className="min-h-11 rounded-control border border-border px-space-4 text-t14 font-semibold text-fg">
              Back
            </button>
            <button type="button" onClick={onPlace} disabled={busy || !step.preview.can_place} data-ticket="place"
              className={cx("min-h-11 flex-1 rounded-control px-space-4 text-t15 font-semibold disabled:opacity-50", sideTone)}>
              {busy ? "Placing…" : sideWord(draft.side)}
            </button>
          </>
        )}
        {step.kind === "multi" && !step.exit && multi && step.result.results.some((r) => r.status === "PLACED") && (
          <ExitEveryAccount busy={busy} accounts={step.result.results.filter((r) => r.status === "PLACED").length} onExit={() => void multi.exitAll()} />
        )}
        {(step.kind === "done" || step.kind === "multi") && (
          <button type="button" onClick={onClose} data-ticket="done" className="min-h-11 flex-1 rounded-control border border-border px-space-4 text-t14 font-semibold text-fg">Done</button>
        )}
      </div>
    </>
  );
}

/** The confirm step: every figure from the server, before anything is placed. 8b: the payoff in plain words, the risk
 * line (the max loss beside the user's own limits, information only: `can_place` alone decides the button) and Explain. */
export function Review({ preview: p, budget, events, liquidity }: {
  preview: OrderPreview; budget?: RiskBudget | null; events?: EventRisk | "error" | null; liquidity?: RiskLine | null;
}) {
  const problems = [...p.errors, ...p.refusals];
  const payoff = ticketPayoffSentence(p);
  const c = p.contract;
  const type = p.order_type === "MARKET" ? "Market" : p.order_type === "LIMIT" ? "Limit" : p.order_type;
  return (
    <div className="space-y-space-3" data-ticket="confirm">
      <dl className="rounded-control border border-subtle px-space-3">
        <Line label="Order" testId="order">{sideWord(p.side)} · {type} · {p.product}</Line>
        <Line label="Quantity" testId="quantity">{unitsText(p.lots, p.lot_size)}</Line>
        <Line label="Price" testId="price">
          {p.fill_price !== null ? formatPrice(p.fill_price) : formatPrice(p.price)}
          {p.price_source === "chain" && <span className="block text-t12 text-fg-muted">From the chain; placing uses the live price</span>}
        </Line>
        <Line label="Margin required" testId="margin">
          {moneyText(p.margin?.value)}
          {p.margin && <span className="block max-w-[14rem] text-t12 text-fg-muted">{p.margin.value !== null ? "Estimate. " : ""}{p.margin.basis}</span>}
          {p.margin?.note && <span className="block max-w-[14rem] text-t12 text-fg-muted" data-line="margin-note">{p.margin.note}</span>}
        </Line>
        <Line label="Max loss" testId="max-loss">
          {moneyText(p.max_loss?.value)}
          {p.max_loss && <span className="block max-w-[14rem] text-t12 text-fg-muted">{p.max_loss.basis}</span>}
        </Line>
        {p.charges && <Line label="Charges (estimate)" testId="charges">{moneyText(p.charges.total)}</Line>}
      </dl>
      {p.fill_note && p.fill_price !== null && <p className="text-t13 text-fg-muted">{p.fill_note}</p>}
      {payoff && (
        <Details id="ticket.payoff-words" label="In plain words" className="mt-0"><p data-ticket="plain-words">{payoff}</p></Details>
      )}
      <RiskLineV2 line={riskLine(p.max_loss?.value, false, budget)} />
      <RiskChecksV2 testId="ticket" lines={[eventLine(events ?? null, p.contract?.expiry), liquidity ?? null, marginLine(p.margin?.value, budget)]} />
      {problems.length > 0 && (
        <ul role="alert" className="space-y-space-1 rounded-control bg-warn-tint px-space-3 py-space-2 text-t13 text-warn" data-ticket="refusals">
          {problems.map((r) => <li key={r}>{r}</li>)}
        </ul>
      )}
      {p.smart?.note && <p className="text-t13 text-fg-muted" data-ticket="slices">{p.smart.note}</p>}
      {p.warnings.length > 0 && (
        <ul className="space-y-space-1 text-t13 text-fg-muted" data-ticket="warnings">
          {p.warnings.map((w) => <li key={w}>{w}</li>)}
        </ul>
      )}
      {c && (
        <ExplainV2 testId="ticket" underlying={c.underlying}
          structure={structureOf([{ side: p.side, right: c.right, strike: c.strike ?? 0, expiry: c.expiry }])} />
      )}
    </div>
  );
}


/** M45 MA-6: one row per practice account - placed, refused or skipped, with the reason in plain words (MB-5). */
function MultiOutcome({ result, exit }: { result: MultiResult; exit?: MultiExitResult }) {
  const placed = result.results.filter((r) => r.status === "PLACED").length;
  return (
    <div role="status" className="space-y-space-2" data-ticket="multi-result">
      <p className="text-t14 font-semibold text-fg">Placed on {placed} of {result.results.length} account{result.results.length === 1 ? "" : "s"}</p>
      <ul className="space-y-space-1">
        {result.results.map((r) => (
          <li key={r.account_id} className="rounded-control bg-surface-2 px-space-3 py-space-2 text-t13" data-multi-row={r.status}>
            <span className="font-semibold text-fg">{r.label}</span>
            <span className={cx("ml-space-2", r.status === "PLACED" ? "text-up" : "text-warn")}>
              {r.status === "PLACED" ? `${r.lots} lot${r.lots === 1 ? "" : "s"} placed` : r.status === "SKIPPED" ? "Skipped" : "Not placed"}
            </span>
            {r.reasons.length > 0 && <span className="block text-t12 text-fg-muted">{r.reasons.join("; ")}</span>}
          </li>
        ))}
      </ul>
      {exit && (
        <p className="text-t13 text-fg" data-ticket="multi-exit">
          Exit sent: {exit.results.filter((r) => r.status === "CLOSED").length} closed
          {exit.results.some((r) => r.status !== "CLOSED") ? `, ${exit.results.filter((r) => r.status !== "CLOSED").length} not closed (see Positions)` : ""}.
        </p>
      )}
    </div>
  );
}
