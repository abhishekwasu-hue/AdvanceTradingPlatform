import { cx } from "../components/primitives/cx";

/**
 * F4 / UX-5 (owner, 27 Oct): the ONE way to pick one of a few named choices on the v2 pages - a row of option cards (a
 * native radio per card, so the keyboard and screen readers work as is), never a browser-default <select>. Taken from the
 * combined exit sheet's cards; the exit sheet and the basket's "Exits for this strategy" share it.
 */
export interface OptionCard<K extends string> { key: K; label: string; hint?: string }

export function OptionCards<K extends string>({ name, legend, options, value, onChange, dataAttr, disabled, className }: {
  /** The radio group's name (unique on the page). */
  name: string;
  legend: string;
  options: readonly OptionCard<K>[];
  value: K;
  onChange: (key: K) => void;
  /** The data attribute each card carries with its key (tests and e2e find a card by it), e.g. "data-trail-kind". */
  dataAttr?: `data-${string}`;
  /** Shown but not changeable (e.g. editing an exit already set). */
  disabled?: boolean;
  className?: string;
}) {
  return (
    <fieldset className={cx("space-y-space-1", className)} data-option-cards={name} disabled={disabled}>
      <legend className="mb-space-1 flex items-center gap-space-1 text-t12 font-medium text-fg-muted">{legend}</legend>
      <div className="grid grid-cols-1 gap-space-1 sm:grid-cols-3">
        {options.map((o) => (
          <label key={o.key} {...(dataAttr ? { [dataAttr]: o.key } : {})} data-checked={value === o.key || undefined}
            className={cx("flex min-h-11 cursor-pointer items-start gap-space-2 rounded-control border px-space-3 py-space-2",
              value === o.key ? "border-brand bg-surface-2" : "border-border")}>
            <input type="radio" name={name} value={o.key} checked={value === o.key} onChange={() => onChange(o.key)}
              className="mt-px h-4 w-4 accent-brand" />
            <span className="min-w-0">
              <span className="block text-t13 font-semibold text-fg">{o.label}</span>
              {o.hint && <span className="block text-t12 text-fg-muted">{o.hint}</span>}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
