import { Check, Info, TriangleAlert } from "lucide-react";
import { cx } from "../components/primitives/cx";
import { Details } from "../ui/Details";
import { VISIBLE_LINES, factKey, type CheckLine, type WarningId } from "./checkCard";
import type { RiskLine } from "./insights";
import { CHECKS_NOTE } from "./riskParts";

/**
 * M76 MP-2: the ONE compact check card of a ticket (flag `trade_warnings`). It sits in the ticket's scroll area above the
 * action bar - never fixed, never over Place or the price fields, no pop-up. Worst first, three lines visible, the rest
 * behind "N more" (expands in place). Each line: one sentence of facts, "Edit" (back to the field) and "Don't show again"
 * (turns that type's switch off; Settings › Trade settings › Warnings turns it back on). A line whose type has "ask me"
 * on gets "Accept and continue" after Place was pressed; no line ever disables Place. The other checks (information,
 * sources) stay folded under "Other checks and sources", as before.
 */
export function CheckCardV2({ lines, pending = 0, others = [], asking = [], expanded, onExpand, onEdit, onDontShow, onAccept, testId }: {
  lines: readonly CheckLine[];
  /** Checks still running (the card fills in when they come back; Place does not wait). */
  pending?: number;
  /** Information lines (not warnings) and their sources, folded. */
  others?: readonly (RiskLine | null)[];
  /** Lines Place is asking him to accept (their type's "ask me" is on). */
  asking?: readonly CheckLine[];
  expanded: boolean;
  onExpand: (open: boolean) => void;
  onEdit?: (id: WarningId) => void;
  onDontShow?: (id: WarningId) => void;
  onAccept?: (line: CheckLine) => void;
  testId: string;
}) {
  const shown = expanded ? lines : lines.slice(0, VISIBLE_LINES);
  const more = lines.length - shown.length;
  const info = others.filter((l): l is RiskLine => l !== null);
  const notes = [...new Set([...info.map((l) => l.note), CHECKS_NOTE].filter(Boolean))];
  const askKeys = new Set(asking.map(factKey));
  return (
    <section aria-label="Before you place" data-check-card={testId} data-lines={lines.length} data-pending={pending || undefined}
      className="space-y-space-2 rounded-control border border-subtle px-space-3 py-space-2 text-t13">
      <h4 className="text-t12 font-semibold text-fg-muted">Before you place</h4>
      {lines.length > 0 ? (
        <ul className="space-y-space-2">
          {shown.map((l) => {
            const ask = askKeys.has(factKey(l));
            return (
              <li key={factKey(l)} data-check-line={l.id} data-severity={l.severity} data-asking={ask ? "yes" : undefined}
                className={cx("space-y-space-1", ask && "rounded-control bg-warn-tint px-space-2 py-space-1")}>
                <p className={cx("flex items-start gap-space-2", l.severity === "mistake" ? "text-warn" : "text-fg")}>
                  {l.severity === "mistake" ? <TriangleAlert size={16} aria-hidden className="mt-0.5 shrink-0" /> : <Info size={16} aria-hidden className="mt-0.5 shrink-0" />}
                  <span className="min-w-0 break-words font-tabular">{l.text}</span>
                </p>
                <p className="flex flex-wrap gap-x-space-3 pl-6 text-t12">
                  {ask && onAccept && (
                    <button type="button" onClick={() => onAccept(l)} data-check-accept={l.id}
                      className="min-h-11 font-semibold text-brand underline sm:min-h-0">Accept and continue</button>
                  )}
                  {onEdit && <button type="button" onClick={() => onEdit(l.id)} data-check-edit={l.id} className="min-h-11 font-medium text-brand sm:min-h-0">Edit</button>}
                  {onDontShow && (
                    <button type="button" onClick={() => onDontShow(l.id)} data-check-mute={l.id} className="min-h-11 text-fg-muted underline sm:min-h-0">
                      Don't show again
                    </button>
                  )}
                </p>
              </li>
            );
          })}
        </ul>
      ) : pending === 0 ? (
        <p className="flex items-center gap-space-2 text-fg" data-check-clear><Check size={16} aria-hidden className="shrink-0 text-up" />No warnings.</p>
      ) : null}
      {(more > 0 || (expanded && lines.length > VISIBLE_LINES)) && (
        <button type="button" onClick={() => onExpand(!expanded)} data-check-more className="min-h-11 text-t12 font-medium text-brand sm:min-h-0">
          {expanded ? "Show fewer" : `${more} more`}
        </button>
      )}
      {pending > 0 && <p className="text-t12 text-fg-muted" data-check-pending>Checking…</p>}
      <Details id={`checks.${testId}`} label={info.length ? `${info.length} other check${info.length === 1 ? "" : "s"} and sources` : "Sources"} className="mt-0">
        {info.length > 0 && (
          <ul className="space-y-space-2">
            {info.map((l) => (
              <li key={l.text} className="flex items-start gap-space-2 text-fg" data-risk-part="info">
                <Info size={16} aria-hidden className="mt-0.5 shrink-0" />
                <span>{l.text}</span>
              </li>
            ))}
          </ul>
        )}
        {notes.map((n) => <p key={n} className="text-t12">{n}</p>)}
      </Details>
    </section>
  );
}
