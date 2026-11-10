import { useId, useState } from "react";
import { ApiError } from "../api/errors";
import { CardV2 } from "../dashboard/CardV2";
import { BUTTON_SECONDARY, FIELD, LABEL, NoteV2 } from "../trading/PartsV2";
import { Combobox } from "../ui/Combobox";
import { Details } from "../ui/Details";
import {
  EMPTY_SIZE_DRAFT, RISK_HINT, calculateSize, sizeBodyOf, sizeWords, type SizeDraft, type SizeResult, type SizeUnit,
} from "./sizeCalculator";

const UNIT_OPTIONS = [{ value: "", label: "Not set" }, { value: "SHARES", label: "Shares (cash)" }, { value: "LOTS", label: "F&O lots" }] as const;

/**
 * M94 R1: the position-size calculator on Settings › Risk limits - closed until the trader opens it, every field empty
 * (nothing pre-filled; the reference is a hint), information only: it places nothing and saves nothing. The size is the
 * server's own risk-based sizing (the same as an entry sized by "Risk-based sizing"), rounded down to whole lots or shares.
 */
export default function SizeCalculatorV2({ calculate = calculateSize, initiallyOpen = false, initialDraft = EMPTY_SIZE_DRAFT, initialResult = null }: {
  calculate?: typeof calculateSize; initiallyOpen?: boolean; initialDraft?: SizeDraft; initialResult?: SizeResult | null;
} = {}) {
  const id = useId();
  const [open, setOpen] = useState(initiallyOpen);
  const [draft, setDraft] = useState<SizeDraft>(initialDraft);
  const [result, setResult] = useState<SizeResult | null>(initialResult);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (patch: Partial<SizeDraft>) => { setDraft({ ...draft, ...patch }); setResult(null); setProblem(null); };

  async function onCalculate() {
    const { body, problem: p } = sizeBodyOf(draft);
    if (p || !body) { setProblem(p ?? "Check the values."); return; }
    setBusy(true); setProblem(null);
    try {
      setResult(await calculate(body));
    } catch (e) {
      setProblem(e instanceof ApiError || e instanceof Error ? e.message : "Could not work out the size. Try again.");
    } finally {
      setBusy(false);
    }
  }

  const field = (key: keyof SizeDraft, label: string, unit: string, hint?: string) => (
    <div className="min-w-0">
      <label htmlFor={`${id}-${key}`} className={LABEL}>{label} ({unit})</label>
      <input id={`${id}-${key}`} inputMode="decimal" className={FIELD} value={draft[key]} placeholder={hint ?? "Value"}
        onChange={(e) => set({ [key]: e.target.value } as Partial<SizeDraft>)} data-field={key} />
    </div>
  );
  const words = result ? sizeWords(result) : null;

  return (
    <CardV2 title="Position-size calculator" testId="size-calculator">
      <p className="text-t13 text-fg-muted">Your risk per trade ÷ the stop distance, rounded down to whole lots or shares. Places and saves nothing.</p>
      {!open && (
        <button type="button" className={`${BUTTON_SECONDARY} mt-space-3`} aria-expanded={false} onClick={() => setOpen(true)} data-testid="size-calculator-open">
          Open the calculator
        </button>
      )}
      {open && (
        <div className="mt-space-3 space-y-space-3" data-testid="size-calculator-form">
          <div className="grid grid-cols-1 gap-space-2 sm:grid-cols-2">
            {field("account", "Account", "₹", "Your account")}
            <div className="min-w-0">
              <label htmlFor={`${id}-riskPct`} className={LABEL}>Risk per trade (%)</label>
              <input id={`${id}-riskPct`} inputMode="decimal" className={FIELD} value={draft.riskPct} placeholder="Value" aria-describedby={`${id}-hint`}
                onChange={(e) => set({ riskPct: e.target.value })} data-field="riskPct" />
              <p id={`${id}-hint`} className="mt-space-1 text-t12 text-fg-muted" data-testid="size-calculator-hint">Hint: {RISK_HINT}</p>
            </div>
            {field("riskRupees", "Or risk per trade", "₹", "Optional")}
            {field("entry", "Entry price", "₹")}
            {field("stop", "Stop price", "₹")}
            <div className="min-w-0">
              <label htmlFor={`${id}-unit`} className={LABEL}>Size in</label>
              <Combobox id={`${id}-unit`} label="Size in" value={draft.unit} className="w-full" options={UNIT_OPTIONS}
                onChange={(v) => set({ unit: v as SizeUnit | "" })} dataAttrs={{ "data-field": "unit" }} />
            </div>
            {draft.unit === "LOTS" && field("lotSize", "Lot size", "qty", "The contract's lot")}
          </div>
          <div className="flex flex-wrap items-center gap-space-2">
            <button type="button" className={BUTTON_SECONDARY} disabled={busy} onClick={() => void onCalculate()} data-testid="size-calculator-run">
              {busy ? "Working out…" : "Work out the size"}
            </button>
            <button type="button" className={BUTTON_SECONDARY} disabled={busy} onClick={() => { setDraft(EMPTY_SIZE_DRAFT); setResult(null); setProblem(null); }}>
              Clear
            </button>
          </div>
          {problem && <NoteV2 kind="warn" testId="size-calculator-problem">{problem}</NoteV2>}
          {words && result && (
            <div className="rounded-control border border-border bg-surface-2 p-space-3" data-testid="size-calculator-result" aria-live="polite">
              <p className={`text-t20 font-semibold tabular-nums ${result.quantity > 0 ? "text-fg" : "text-warn"}`}>{words.size}</p>
              <p className="mt-space-1 text-t12 text-fg-muted tabular-nums">{words.detail}</p>
            </div>
          )}
          <Details id="size-calculator.how" label="How it works" className="mt-0">
            <p>With both a % and a rupee amount, the smaller applies - as Risk-based sizing does when an entry is placed. Less than one lot is never rounded up.</p>
          </Details>
        </div>
      )}
    </CardV2>
  );
}
