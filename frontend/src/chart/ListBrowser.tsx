import { ChevronDown, ChevronRight, Lock } from "lucide-react";
import { cx } from "../components/primitives/cx";
import { sameRef, type BrowserGroup, type ListRef } from "./systemLists";

/**
 * WL1 (handover item 4): every list the trader can open, by group - the read-only system groups first ("Indices",
 * "Index stocks", "Sectors", "F&O"), then "My lists". Each group opens and closes (on a phone the groups stay short
 * until opened); a search finds a list by name across the groups. A system list carries a lock and "read-only".
 * Presentational: the panel owns the state.
 */
export interface ListBrowserProps {
  groups: BrowserGroup[];
  /** The groups shown open (a search opens every group that has a match). */
  open: ReadonlySet<string>;
  onToggle: (group: string) => void;
  current: ListRef | null;
  onPick: (ref: ListRef) => void;
  query: string;
  onQuery: (query: string) => void;
  touch?: boolean;
}

const focus = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";

export function ListBrowser({ groups, open, onToggle, current, onPick, query, onQuery, touch = false }: ListBrowserProps) {
  const searching = query.trim().length > 0;
  const tall = touch ? "min-h-11" : "min-h-11 md:min-h-9";
  return (
    <div data-list-browser className="min-h-0 flex-1 overflow-y-auto border-b border-subtle px-space-1 pb-space-2">
      <div className="p-space-1">
        <label className="sr-only" htmlFor="watchlist-find">Find a list</label>
        <input id="watchlist-find" type="search" value={query} onChange={(e) => onQuery(e.target.value)} placeholder="Find a list (e.g. bank, IT, NIFTY 50)"
          className={cx("h-11 w-full rounded-control border border-border bg-surface-2 px-space-2 text-t14 text-fg placeholder:text-fg-muted focus:border-brand focus:outline-none", !touch && "md:h-9")} />
      </div>
      {groups.length === 0 && <p className="px-space-2 py-space-3 text-t13 text-fg-muted">No list matches "{query.trim()}".</p>}
      {groups.map((g) => {
        const isOpen = searching || open.has(g.id);
        const Chevron = isOpen ? ChevronDown : ChevronRight;
        return (
          <section key={g.id} data-group={g.id} aria-label={g.label}>
            <h3 className="m-0">
              <button type="button" aria-expanded={isOpen} aria-controls={`watchlist-group-${g.id}`} onClick={() => onToggle(g.id)}
                className={cx("flex w-full items-center gap-space-1 rounded-control px-space-2 text-left text-t13 font-semibold text-fg hover:bg-surface-2", tall, focus)}>
                <Chevron size={14} aria-hidden className="shrink-0 text-fg-muted" />
                <span className="min-w-0 flex-1 truncate">{g.label}</span>
                <span className="text-t12 font-normal text-fg-muted">{g.entries.length}</span>
              </button>
            </h3>
            {isOpen && (
              <ul id={`watchlist-group-${g.id}`} aria-label={`${g.label} lists`} className="pb-space-1">
                {g.entries.length === 0 && <li className="px-space-3 py-space-2 text-t12 text-fg-muted">No lists here yet.</li>}
                {g.entries.map((e) => {
                  const chosen = sameRef(e.ref, current);
                  return (
                    <li key={e.ref.kind === "system" ? e.ref.key : `own-${e.ref.id}`}>
                      <button type="button" aria-current={chosen ? "true" : undefined} onClick={() => onPick(e.ref)}
                        data-read-only={e.readOnly ? "true" : undefined}
                        className={cx("flex w-full items-center gap-space-2 rounded-control pl-space-6 pr-space-2 text-left text-t14 hover:bg-surface-2", tall, chosen && "bg-surface-2 font-semibold", focus)}>
                        <span className="min-w-0 flex-1 truncate">{e.name}</span>
                        {e.readOnly && (
                          <span className="inline-flex shrink-0 items-center gap-0.5 text-t12 text-fg-muted" title="Read-only list: copy it to change it">
                            <Lock size={12} aria-hidden /><span className="sr-only">read-only</span>
                          </span>
                        )}
                        <span className="shrink-0 text-t12 tabular-nums text-fg-muted">{e.count}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}
