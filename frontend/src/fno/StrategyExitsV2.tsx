import { ShieldCheck, TriangleAlert } from "lucide-react";
import { FROM_DEFAULTS } from "../settings/tradingDefaults";
import { FIELD, LABEL } from "../trading/PartsV2";
import { Details } from "../ui/Details";
import { OptionCards } from "../ui/OptionCards";
import { Signed } from "../ui/Signed";
import {
  STRATEGY_MEASURES, STRATEGY_TRAILS, exitErrors, hasExits, trailFields, unitOf, type SentencePart, type StrategyExitForm,
} from "./strategyExits";

/**
 * SX1 / SX2 (owner, 24 Oct): "Exits for this strategy" on the basket ticket, before Review - an overall stop-loss and
 * target (combined ₹, % of the net premium or the spot level) and an optional trailing. Empty boxes unless the user saved
 * his own defaults (then marked "from your defaults"); it can be left empty. No state here (the panel holds the form);
 * nothing is sent until the basket is placed, and the exit is set only on the legs that fill.
 */
export function StrategyExitsV2({ form, onChange, legCount, onSaveDefaults, saveNote }: {
  form: StrategyExitForm;
  onChange: (patch: Partial<StrategyExitForm>) => void;
  legCount: number;
  /** "Save as my defaults" (absent: the button is hidden). */
  onSaveDefaults?: () => void;
  saveNote?: string | null;
}) {
  const unit = form.measure ? unitOf(form.measure) : null;       // M68 PR-3: no measure chosen yet - no unit
  const set = (patch: Partial<StrategyExitForm>) => onChange({ ...patch, fromDefaults: false });
  const errors = exitErrors(form);
  const fields = form.measure ? trailFields(form.trailKind, form.measure) : [];
  const level = form.measure === "SPOT";
  return (
    <section aria-label="Exits for this strategy" className="space-y-space-2 rounded-control border border-subtle p-space-3" data-basket="strategy-exits">
      <div className="flex items-center justify-between gap-space-2">
        <h3 className="flex items-center gap-space-1 text-t14 font-semibold text-fg"><ShieldCheck size={16} aria-hidden />Exits for this strategy</h3>
        {form.fromDefaults && <span className="text-t12 text-fg-muted" data-exits-from-defaults>{FROM_DEFAULTS}</span>}
      </div>
      <p className="text-t12 text-fg-muted">Optional. All legs exit together, sold legs first.</p>

      <OptionCards name="strategy-exit-measure" legend="What the exit watches" options={STRATEGY_MEASURES} value={form.measure}
        dataAttr="data-exits-measure" onChange={(measure) => set({ measure })} />

      <div className="grid grid-cols-1 gap-space-3 sm:grid-cols-2">
        <label className="block">
          <span className={LABEL}>{level ? "Overall stop-loss (spot level)" : unit ? `Overall stop-loss, a loss of (${unit})` : "Overall stop-loss"}</span>
          <input className={FIELD} inputMode="decimal" value={form.sl} onChange={(e) => set({ sl: e.target.value })} data-exits-sl autoComplete="off" />
        </label>
        <label className="block">
          <span className={LABEL}>{level ? "Overall target (spot level)" : unit ? `Overall target, a profit of (${unit})` : "Overall target"}</span>
          <input className={FIELD} inputMode="decimal" value={form.target} onChange={(e) => set({ target: e.target.value })} data-exits-target autoComplete="off" />
        </label>
      </div>

      {/* F4: the same option cards as the combined exit sheet (UX-5: no browser-default select). */}
      <div className="space-y-space-1" data-exits-trail={form.trailKind}>
        <OptionCards name="strategy-exit-trail" legend="Trailing (optional)" options={STRATEGY_TRAILS} value={form.trailKind}
          dataAttr="data-exits-trail-kind" onChange={(trailKind) => set({ trailKind, after: "", lock: "", step: "", by: "" })} />
        {fields.length > 0 && (
          <div className="grid grid-cols-1 gap-space-3 sm:grid-cols-2">
            {fields.map((f) => (
              <label key={f.key} className="block">
                <span className={LABEL}>{f.label}</span>
                <input className={FIELD} inputMode="decimal" value={form[f.key]} onChange={(e) => set({ [f.key]: e.target.value })}
                  data-exits-trail-field={f.key} autoComplete="off" />
              </label>
            ))}
          </div>
        )}
      </div>

      {hasExits(form) && errors.length > 0 && (
        <ul className="space-y-space-1 text-t13 text-fg" role="alert" data-exits-errors>
          {errors.map((r) => (
            <li key={r} className="flex items-start gap-space-1"><TriangleAlert size={14} aria-hidden className="mt-px shrink-0 text-warn" />{r}</li>
          ))}
        </ul>
      )}
      {onSaveDefaults && hasExits(form) && errors.length === 0 && (
        <div className="flex flex-wrap items-center gap-space-2">
          <button type="button" onClick={onSaveDefaults} data-exits-save-defaults
            className="min-h-11 rounded-control border border-border px-space-3 text-t13 font-semibold text-fg hover:bg-surface-2">
            Save as my defaults
          </button>
          {saveNote && <span className="text-t12 text-fg-muted" role="status">{saveNote}</span>}
        </div>
      )}

      {/* F7 / UX-11 U1: the longer explanations, once for the section. */}
      <Details id="fno.strategy-exits">
        <p>When the legs fill, all {legCount > 0 ? `${legCount} ` : ""}legs exit together, sold legs first. Leave it empty to keep each leg&apos;s own stop only.</p>
        <p>It is set only on the legs that fill. Change or remove it later on Positions.</p>
        {form.trailKind !== "NONE" && <p>The stop only moves your way, never back.</p>}
      </Details>
    </section>
  );
}

/** F3: a sentence with signed profit / loss figures (the shared signed value: + ▲ / − ▼ / 0 grey). */
export function ExitSentenceV2({ parts }: { parts: readonly SentencePart[] }) {
  return (
    <>
      {parts.map((p, i) => (typeof p === "string"
        ? <span key={i}>{p}</span>
        : <Signed key={i} value={p.signed} kind={p.kind} decimals={p.kind === "pct" ? (p.signed % 1 ? 1 : 0) : undefined} />))}
    </>
  );
}
