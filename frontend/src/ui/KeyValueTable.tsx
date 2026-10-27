import type { ReactNode } from "react";
import { cx } from "../components/primitives/cx";
import type { GlossaryKey } from "./glossary";
import { TermHint } from "./TermHint";

/**
 * UX-11 U3 (owner, 27 Oct): label / value lists as one shared key-value table - the label left, the value right in
 * tabular figures, one row each. A description list (<dl>), so screen readers pair each label with its value.
 */
export interface KeyValueRow { label: ReactNode; value: ReactNode; hint?: GlossaryKey; testId?: string }

export function KeyValueTable({ rows, caption, className }: { rows: readonly KeyValueRow[]; caption?: string; className?: string }) {
  return (
    <dl aria-label={caption} className={cx("divide-y divide-subtle", className)} data-kv>
      {rows.map((r, i) => (
        <div key={r.testId ?? i} className="flex min-h-9 items-baseline justify-between gap-space-3 py-space-2" data-kv-row={r.testId}>
          <dt className="flex min-w-0 items-center gap-space-1 text-t13 text-fg-muted">{r.label}{r.hint && <TermHint term={r.hint} />}</dt>
          <dd className="shrink-0 text-right font-tabular text-t14 font-medium text-fg">{r.value}</dd>
        </div>
      ))}
    </dl>
  );
}
