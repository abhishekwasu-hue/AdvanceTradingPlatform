import { SUGGESTED_NOTE, librarySplit } from "./strategyLabels";

/** The Strategy Library card's settings chips (SB-UX SU7, REV-B2) and, apart under STRAT-X's note, the built-in's suggested
 * stop and targets - values for the trader's own settings, never a rule of the strategy. */
export function LibraryChips({ params }: { params: Record<string, unknown> | null | undefined }) {
  const chips = librarySplit(params);
  return (
    <>
      {chips.settings.length > 0 && (
        <ul className="flex flex-wrap gap-1.5" aria-label="Settings" data-rule-chips>
          {chips.settings.map((c) => (
            <li key={c} className="rounded-full border border-border bg-surface-2 px-2 py-0.5 text-xs text-fg">{c}</li>
          ))}
        </ul>
      )}
      {chips.suggested.length > 0 && (
        <div className="mt-2" data-suggested-exits>
          <div className="mb-1 text-xs text-fg-muted">{SUGGESTED_NOTE}</div>
          <ul className="flex flex-wrap gap-1.5" aria-label={SUGGESTED_NOTE}>
            {chips.suggested.map((c) => (
              <li key={c} className="rounded-full border border-dashed border-border px-2 py-0.5 text-xs text-fg-muted">{c}</li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}
