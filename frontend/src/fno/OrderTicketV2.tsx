import * as RD from "@radix-ui/react-dialog";
import { Minus, Plus, ShieldCheck, X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { cx } from "../components/primitives/cx";
import { FROM_DEFAULTS, ticketPrefill, useTradingDefaults } from "../settings/tradingDefaults";
import { formatPrice } from "../ui/format";
import { useFeatures } from "../ui/useFeatures";
import { fnoApi, type FnoApi, type OrderPreview, type OrderResult, type Side } from "./api";
import {
  MAX_LOTS, ORDER_TYPES, PRACTICE_TEXT, PRACTICE_TITLE, PRODUCTS, askOf, clampLots, draftProblems, moneyText, needs, newClientRef,
  draftFromDefaults, newDraft, resultText, type DraftDefaults, shortcutSide, sideWord, stopHint, unitsText, type Draft, type TicketTarget,
} from "./ticketModel";
import { ExplainV2, RiskLineV2, useRiskBudget } from "./InsightsV2";
import { riskLine, structureOf, ticketPayoffSentence, type RiskBudget } from "./insights";

/**
 * FNO-1 (owner order section 6): the ONE order ticket - used by the option chain now, built to be opened the same way
 * from the chart, a watchlist, a position or a basket leg later (it takes a `TicketTarget` and nothing page-specific).
 *
 * - Buy / Sell; quantity in LOTS with the units shown (lots x the instrument master's lot size); Market / Limit / SL /
 *   SL-M; MIS / NRML; the price helper (LTP, bid, ask fill the price field); a stop-loss for the position.
 * - "Review" asks the server for the confirm step: the price it fills at, the margin required and the max loss, the
 *   charges, and the risk checks in plain words - all BEFORE anything is placed. "Place PAPER order" places one PAPER
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
  api?: Pick<FnoApi, "preview" | "place">;
}

export type Step = { kind: "edit" } | { kind: "review"; preview: OrderPreview } | { kind: "done"; result: OrderResult };

const FIELD = "h-11 w-full rounded-control border border-border bg-surface-2 px-space-3 text-t14 text-fg font-tabular focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand";
const SEG = "flex min-h-11 flex-1 items-center justify-center rounded-control px-space-2 text-t14 font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";

function Segmented<T extends string>({ label, value, options, onChange, testId }: {
  label: string; value: T; options: { value: T; label: string; hint?: string }[]; onChange: (v: T) => void; testId: string;
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

export default function OrderTicketV2({ open, target, side, onClose, onPlaced, api = fnoApi }: OrderTicketProps) {
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

  // A new target or side starts a fresh ticket (and a fresh id, so a second order is never mistaken for the first).
  useEffect(() => {
    if (!open) return;
    setDraft(newDraft(side, defaults));
    setStep({ kind: "edit" });
    setProblem(null);
    ref.current = newClientRef();
  }, [open, side, target?.underlying, target?.expiry, target?.strike, target?.right, defaults]);

  // 8b-d: the user's own risk budget, read on the confirm step for its risk line (information only, never a refusal).
  const budget = useRiskBudget(open && step.kind === "review");

  // Desktop shortcuts: B = Buy, S = Sell while the ticket is being filled in (never while typing in a field).
  useEffect(() => {
    if (!open || step.kind !== "edit") return undefined;
    const onKey = (e: KeyboardEvent) => {
      const picked = shortcutSide(e, e.target);
      if (picked) { e.preventDefault(); setDraft((d) => ({ ...d, side: picked })); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, step.kind]);

  if (!target) return null;

  async function review() {
    const missing = draftProblems(draft, target?.right);
    if (!target || missing.length) { setProblem(missing[0] ?? null); return; }
    setBusy(true);
    try {
      setStep({ kind: "review", preview: await api.preview(askOf(target, draft, ref.current)) });
    } catch (e) {
      setProblem(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function place() {
    if (!target) return;
    setBusy(true);
    try {
      const result = await api.place(askOf(target, draft, ref.current));
      setStep({ kind: "done", result });
      if (result.status === "PLACED") onPlaced?.(result);
    } catch (e) {
      setProblem(String(e));
    } finally {
      setBusy(false);
    }
  }

  const label = target.label ?? (target.right === "FUT" ? `${target.underlying} ${target.expiry} FUT` : `${target.underlying} ${target.strike} ${target.right}`);
  return (
    <RD.Root open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <RD.Portal>
        <RD.Overlay className="fixed inset-0 z-40 bg-scrim/50" />
        <RD.Content aria-describedby={undefined} data-order-ticket data-step={step.kind}
          className="fixed inset-x-0 bottom-0 z-50 flex max-h-[92dvh] flex-col overflow-hidden rounded-t-panel border-2 border-warn bg-surface-1 text-fg shadow-raised focus:outline-none md:inset-y-0 md:left-auto md:right-0 md:max-h-none md:w-[26rem] md:rounded-none md:rounded-l-panel">
          <PracticeFrame />
          <div className="flex items-start justify-between gap-space-3 px-space-4 pt-space-3">
            <div className="min-w-0">
              <RD.Title className="truncate text-t18 font-semibold text-fg">{label}</RD.Title>
              <p className="text-t13 text-fg-muted font-tabular">
                LTP {formatPrice(target.ltp)} · Bid {formatPrice(target.bid)} · Ask {formatPrice(target.ask)}
              </p>
            </div>
            <RD.Close aria-label="Close" className="-mr-space-2 flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg">
              <X size={18} aria-hidden />
            </RD.Close>
          </div>
          <TicketBody target={target} draft={draft} step={step} busy={busy} problem={problem} idPrefix={idPrefix} defaults={defaults} budget={budget}
            onDraft={(patch) => { setDraft((d) => ({ ...d, ...patch })); setProblem(null); }}
            onReview={review} onPlace={place} onBack={() => setStep({ kind: "edit" })} onClose={onClose} />
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  );
}

/** The "Practice mode (PAPER)" frame on every ticket (owner order 8b-l); 8b slice 1: also on the positions page while
 * every position shown is PAPER (`className` rounds it there). */
export function PracticeFrame({ className }: { className?: string } = {}) {
  return (
    <div className={cx("flex flex-wrap items-center gap-x-space-2 bg-warn-tint px-space-4 py-space-2 text-t13 font-semibold text-warn", className)} data-practice-frame>
      <ShieldCheck size={16} aria-hidden className="shrink-0" />
      <span className="shrink-0">{PRACTICE_TITLE}</span>
      <span className="font-normal">{PRACTICE_TEXT}</span>
    </div>
  );
}

const sentence = (t: string) => t.charAt(0).toUpperCase() + t.slice(1);

/** The ticket's fields, the confirm step and the buttons (no state, no hooks). */
export function TicketBody({ target, draft, step, busy, problem, idPrefix, onDraft, onReview, onPlace, onBack, onClose, defaults, budget }: {
  target: TicketTarget; draft: Draft; step: Step; busy: boolean; problem: string | null; idPrefix: string;
  /** Settings part C: the user's own ticket defaults, to mark the fields that still show them. */
  defaults?: DraftDefaults;
  /** 8b-d: the user's own risk budget for the risk line (null: the line is left out). */
  budget?: RiskBudget | null;
  onDraft: (patch: Partial<Draft>) => void; onReview: () => void; onPlace: () => void; onBack: () => void; onClose: () => void;
}) {
  const n = needs(draft.orderType);
  const ids = { lots: `${idPrefix}-lots`, price: `${idPrefix}-price`, trigger: `${idPrefix}-trigger`, stop: `${idPrefix}-stop` };
  const marks = draftFromDefaults(draft, defaults);
  const fromDefaults = [marks.lots && "lots", marks.orderType && "order type", marks.product && "product"].filter(Boolean) as string[];
  const sideTone = draft.side === "BUY" ? "bg-up text-on-brand" : "bg-down text-on-brand";
  return (
    <>
      <div className="min-h-0 flex-1 space-y-space-3 overflow-y-auto px-space-4 py-space-3" data-ticket-body>
        {step.kind === "edit" && (
          <>
            <Segmented label="Side" testId="side" value={draft.side} onChange={(v) => onDraft({ side: v })}
              options={[{ value: "BUY", label: "Buy" }, { value: "SELL", label: "Sell" }]} />
            <p className="hidden text-t12 text-fg-muted md:block">Keys: B for Buy, S for Sell.</p>
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
            <Segmented label="Product" testId="product" value={draft.product} onChange={(v) => onDraft({ product: v })} options={PRODUCTS} />
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
            <div>
              <label htmlFor={ids.stop} className="mb-space-1 block text-t12 font-medium text-fg-muted">
                Stop-loss for the position {stopHint(draft.side, target.right)}
              </label>
              <input id={ids.stop} inputMode="decimal" value={draft.stopLoss} onChange={(e) => onDraft({ stopLoss: e.target.value })} className={FIELD} data-ticket="stop" />
            </div>
          </>
        )}

        {step.kind === "review" && <Review preview={step.preview} budget={budget} />}

        {step.kind === "done" && (
          <p role="status" data-ticket="result"
            className={cx("rounded-control px-space-3 py-space-3 text-t14", step.result.status === "PLACED" ? "bg-surface-2 text-fg" : "bg-warn-tint text-warn")}>
            {resultText(step.result)}
          </p>
        )}

        {problem && <p role="alert" className="rounded-control bg-warn-tint px-space-3 py-space-2 text-t13 text-warn" data-ticket="problem">{problem}</p>}
      </div>

      <div className="flex gap-space-2 border-t border-subtle px-space-4 py-space-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))]">
        {step.kind === "edit" && (
          <button type="button" onClick={onReview} disabled={busy} data-ticket="review"
            className={cx("min-h-11 flex-1 rounded-control px-space-4 text-t15 font-semibold disabled:opacity-60", sideTone)}>
            {busy ? "Checking…" : `Review ${sideWord(draft.side)} order`}
          </button>
        )}
        {step.kind === "review" && (
          <>
            <button type="button" onClick={onBack} data-ticket="back" className="min-h-11 rounded-control border border-border px-space-4 text-t14 font-semibold text-fg">
              Back
            </button>
            <button type="button" onClick={onPlace} disabled={busy || !step.preview.can_place} data-ticket="place"
              className={cx("min-h-11 flex-1 rounded-control px-space-4 text-t15 font-semibold disabled:opacity-50", sideTone)}>
              {busy ? "Placing…" : step.preview.can_place ? "Place PAPER order" : "Cannot place"}
            </button>
          </>
        )}
        {step.kind === "done" && (
          <button type="button" onClick={onClose} data-ticket="done" className="min-h-11 flex-1 rounded-control border border-border px-space-4 text-t14 font-semibold text-fg">Done</button>
        )}
      </div>
    </>
  );
}

/** The confirm step: every figure from the server, before anything is placed. 8b: the payoff in plain words, the risk
 * line (the max loss beside the user's own limits, information only: `can_place` alone decides the button) and Explain. */
export function Review({ preview: p, budget }: { preview: OrderPreview; budget?: RiskBudget | null }) {
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
        <p className="rounded-control bg-surface-2 px-space-3 py-space-2 text-t14 text-fg" data-ticket="plain-words">
          <span className="block text-t12 font-semibold text-fg-muted">In plain words (information only)</span>
          {payoff}
        </p>
      )}
      <RiskLineV2 line={riskLine(p.max_loss?.value, false, budget)} />
      {problems.length > 0 && (
        <ul role="alert" className="space-y-space-1 rounded-control bg-warn-tint px-space-3 py-space-2 text-t13 text-warn" data-ticket="refusals">
          {problems.map((r) => <li key={r}>{r}</li>)}
        </ul>
      )}
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
