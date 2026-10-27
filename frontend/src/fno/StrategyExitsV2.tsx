import { ShieldCheck, TriangleAlert } from "lucide-react";
import { cx } from "../components/primitives/cx";
import { FROM_DEFAULTS } from "../settings/tradingDefaults";
import { FIELD, LABEL } from "../trading/PartsV2";
import {
  STRATEGY_MEASURES, STRATEGY_TRAILS, exitErrors, hasExits, trailFields, unitOf, type StrategyExitForm,
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
  const unit = unitOf(form.measure);
  const set = (patch: Partial<StrategyExitForm>) => onChange({ ...patch, fromDefaults: false });
  const errors = exitErrors(form);
  const fields = trailFields(form.trailKind, form.measure);
  const level = form.measure === "SPOT";
  return (
    <section aria-label="Exits for this strategy" className="space-y-space-2 rounded-control border border-subtle p-space-3" data-basket="strategy-exits">
      <div className="flex items-center justify-between gap-space-2">
        <h3 className="flex items-center gap-space-1 text-t14 font-semibold text-fg"><ShieldCheck size={16} aria-hidden />Exits for this strategy</h3>
        {form.fromDefaults && <span className="text-t12 text-fg-muted" data-exits-from-defaults>{FROM_DEFAULTS}</span>}
      </div>
      <p className="text-t12 text-fg-muted">
        Optional. When the legs fill, all {legCount > 0 ? legCount : ""} legs exit together, sold legs first. Leave empty to keep each leg&apos;s own stop only.
      </p>

      <fieldset className="space-y-space-1">
        <legend className={LABEL}>What the exit watches</legend>
        <div className="grid grid-cols-1 gap-space-1 sm:grid-cols-3">
          {STRATEGY_MEASURES.map((m) => (
            <label key={m.key} className={cx("flex min-h-11 cursor-pointer items-start gap-space-2 rounded-control border px-space-3 py-space-2",
              form.measure === m.key ? "border-brand bg-surface-2" : "border-border")} data-exits-measure={m.key}>
              <input type="radio" name="strategy-exit-measure" checked={form.measure === m.key} onChange={() => set({ measure: m.key })}
                className="mt-px h-4 w-4 accent-brand" />
              <span className="min-w-0">
                <span className="block text-t13 font-semibold text-fg">{m.label}</span>
                <span className="block text-t12 text-fg-muted">{m.hint}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      <div className="grid grid-cols-1 gap-space-3 sm:grid-cols-2">
        <label className="block">
          <span className={LABEL}>{level ? "Overall stop-loss (spot level)" : `Overall stop-loss, a loss of (${unit})`}</span>
          <input className={FIELD} inputMode="decimal" value={form.sl} onChange={(e) => set({ sl: e.target.value })} data-exits-sl autoComplete="off" />
        </label>
        <label className="block">
          <span className={LABEL}>{level ? "Overall target (spot level)" : `Overall target, a profit of (${unit})`}</span>
          <input className={FIELD} inputMode="decimal" value={form.target} onChange={(e) => set({ target: e.target.value })} data-exits-target autoComplete="off" />
        </label>
      </div>

      <fieldset className="space-y-space-1">
        <legend className={LABEL}>Trailing (optional)</legend>
        <select className={FIELD} value={form.trailKind} data-exits-trail
          onChange={(e) => set({ trailKind: e.target.value as StrategyExitForm["trailKind"], after: "", lock: "", step: "", by: "" })}>
          {STRATEGY_TRAILS.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
        </select>
        <p className="text-t12 text-fg-muted">{STRATEGY_TRAILS.find((t) => t.key === form.trailKind)?.hint}</p>
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
        {fields.length > 0 && <p className="text-t12 text-fg-muted">The stop only moves your way, never back.</p>}
      </fieldset>

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
    </section>
  );
}
