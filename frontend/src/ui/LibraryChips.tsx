import { Fragment, type ReactNode } from "react";
import type { GlossaryKey } from "./glossary";
import { SUGGESTED_NOTE, librarySplit, type SettingRow } from "./strategyLabels";
import { TermHint } from "./TermHint";

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

/** M49 U3 (owner-approved mockup docs/ui/U3_LIBRARY.html): the card's at most three main settings, as chips ("Fast EMA 9"). */
export function LibraryMainChips({ rows }: { rows: readonly SettingRow[] }) {
  if (!rows.length) return null;
  return (
    <ul className="flex flex-wrap gap-space-1" aria-label="Main settings" data-main-settings>
      {rows.map((r) => (
        <li key={r.key} className="whitespace-nowrap rounded-pill bg-surface-2 px-space-2 py-0.5 text-t13 text-fg">
          {r.label} <b className="font-tabular font-semibold">{r.value}</b>
        </li>
      ))}
    </ul>
  );
}

/** U3: the trading word a setting is about, for its (?) - only the words the one glossary explains (src/ui/glossary.ts). */
export function settingTerm(key: string): GlossaryKey | null {
  if (/^(htf_)?ema/.test(key)) return "ema";
  if (/^rsi/.test(key)) return "rsi";
  if (/^volume/.test(key)) return "volume";
  if (/^vwap/.test(key)) return "vwap";
  return null;
}

/** U3: settings as a label / value list; a hard word gets its (?) once per list. */
export function SettingList({ rows, label }: { rows: readonly SettingRow[]; label?: string }) {
  const seen = new Set<GlossaryKey>();
  return (
    <dl aria-label={label} className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-space-3 gap-y-space-1 text-t14">
      {rows.map((r) => {
        const term = settingTerm(r.key);
        const hint = term && !seen.has(term) ? term : null;
        if (hint) seen.add(hint);
        return (
          <Fragment key={r.key}>
            <dt className="flex min-w-0 items-center gap-space-1 text-fg-muted">{r.label}{hint && <TermHint term={hint} />}</dt>
            <dd className="m-0 text-right font-tabular font-medium text-fg">{r.value}</dd>
          </Fragment>
        );
      })}
    </dl>
  );
}

/** U3: "More settings (N)" - the rest of the strategy's own settings and, apart under STRAT-X's note, the suggested stop /
 * targets. Closed by default; a native <details>, so the keyboard and screen readers work as is. */
export function LibraryMoreSettings({ more, suggested }: { more: readonly SettingRow[]; suggested: readonly SettingRow[] }) {
  const count = more.length + suggested.length;
  if (!count) return null;
  return (
    <LibraryFold title={`More settings (${count})`} name="more-settings">
      {more.length > 0 && <SettingList rows={more} label="More settings" />}
      {suggested.length > 0 && (
        <div data-suggested-exits>
          <div className="mb-space-1 text-t13 text-fg-muted">{SUGGESTED_NOTE}</div>
          <SettingList rows={suggested} label={SUGGESTED_NOTE} />
        </div>
      )}
    </LibraryFold>
  );
}

/** U3: the Library's closed fold ("+ More settings (3)", "+ How it reads the chart"), as the mockup draws it. */
export function LibraryFold({ title, name, children }: { title: string; name?: string; children: ReactNode }) {
  return (
    <details data-library-fold={name} className="group mt-space-2 border-t border-subtle pt-space-1">
      <summary className="flex min-h-11 cursor-pointer list-none items-center gap-space-1 rounded-control text-t14 font-medium text-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-8 [&::-webkit-details-marker]:hidden">
        <span aria-hidden className="inline-block w-4 font-bold group-open:hidden">+</span>
        <span aria-hidden className="hidden w-4 font-bold group-open:inline-block">−</span>
        {title}
      </summary>
      <div className="mt-space-2 space-y-space-3 pb-space-1">{children}</div>
    </details>
  );
}
