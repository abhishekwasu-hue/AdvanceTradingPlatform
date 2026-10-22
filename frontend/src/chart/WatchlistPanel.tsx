import { ArrowDown, ArrowUp, GripVertical, PanelRightClose, Pencil, Plus, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, useSyncExternalStore, type KeyboardEvent, type PointerEvent } from "react";
import { cx } from "../components/primitives/cx";
import { EMPTY, formatPct, formatPrice } from "../ui/format";
import { SymbolPicker } from "../ui/SymbolPicker";
import { ConfirmBox } from "./ConfirmBox";
import {
  MAX_ITEMS, NAME_MAX, QUOTE_ROWS, QuotePoller, SWITCHED_OFF, WatchlistsStore, direction, itemKey, moveItem, watchlistsV2Api,
  type QuoteRow, type WatchItem, type WatchlistV2,
} from "./watchlistsV2";

/**
 * UI2-4c (Charts page plan §3, UI v2 proposal §6): the trader's watchlists next to the chart - a right column on wide
 * screens, a bottom sheet on a phone. Only while the `watchlists` flag is on (CHP-4; the page does not mount it
 * otherwise).
 * - The lists: switch, create, rename, delete (asks first). The server's limits apply (CHP-7).
 * - Rows: symbol, exchange, last price and change (sign and arrow as well as colour). One batch quote for the first 50
 *   rows every 10 s, skipped while the tab is hidden.
 * - Add with the symbol search, remove, reorder by dragging the handle or with the arrow keys on it (Alt+↑/↓ on a row).
 *   Every change is saved with the list's version (watchlistsV2.ts).
 * - A row opens its symbol on the chart; the timeframe, chart type and indicators stay.
 */
export interface WatchlistPanelProps {
  /** The symbol on the chart (its row is marked). */
  symbol: string;
  exchange: string;
  onOpen: (item: WatchItem) => void;
  /** The list shown (null: the first), kept by the page. */
  listId: number | null;
  onListId: (id: number | null) => void;
  /** This user's recent-symbols key for the add search. */
  storageKey: string | null;
  /** Wide screens: hides the column. */
  onClose?: () => void;
  /** The phone sheet: 44 px targets everywhere. */
  touch?: boolean;
  className?: string;
}

const focus = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";

/** The prices of the first 50 rows (QuotePoller: a reorder asks for nothing new; a changed set waits out the 10 s). */
function useQuotes(items: readonly WatchItem[]) {
  const [quotes, setQuotes] = useState<{ rows: Map<string, QuoteRow>; failed: boolean }>({ rows: new Map(), failed: false });
  const poller = useMemo(() => new QuotePoller(watchlistsV2Api.quotes, {
    data: (reply) => setQuotes({ rows: new Map(reply.items.map((r) => [itemKey(r), r])), failed: false }),
    error: () => setQuotes((q) => ({ ...q, failed: true })),
  }), []);
  useEffect(() => () => poller.close(), [poller]);
  useEffect(() => { poller.update(items); }, [poller, items]);
  return quotes;
}

function Change({ row }: { row: QuoteRow | undefined }) {
  const dir = direction(row);
  if (!row || row.missing || dir === null) return <span className="text-t12 text-fg-muted">{EMPTY}</span>;
  const Arrow = dir === "up" ? ArrowUp : dir === "down" ? ArrowDown : null;
  return (
    <span data-direction={dir} className={cx("inline-flex items-center gap-0.5 text-t12 tabular-nums", dir === "up" ? "text-up" : dir === "down" ? "text-down" : "text-fg-muted")}>
      {Arrow && <Arrow size={12} aria-hidden />}{formatPct(row.change_pct)}
    </span>
  );
}

export function WatchlistPanel({ symbol, exchange, onOpen, listId, onListId, storageKey, onClose, touch = false, className }: WatchlistPanelProps) {
  const store = useMemo(() => new WatchlistsStore(), []);
  const state = useSyncExternalStore(store.subscribe, store.getState);
  useEffect(() => { void store.load(); }, [store]);
  const list: WatchlistV2 | undefined = state.lists.find((l) => l.id === listId) ?? state.lists[0];
  const items = useMemo(() => (state.off ? [] : list?.items ?? []), [list, state.off]);
  const quotes = useQuotes(items);

  const [naming, setNaming] = useState<null | "new" | "rename">(null);
  const [name, setName] = useState("");
  const [asking, setAsking] = useState(false);
  const [picker, setPicker] = useState(0);                     // a new key empties the add search after each add
  const [drag, setDrag] = useState<{ from: number; to: number } | null>(null);
  const rowsRef = useRef<HTMLUListElement>(null);
  useEffect(() => { setAsking(false); setNaming(null); }, [list?.id]);

  const tall = touch ? "min-h-11 min-w-11" : "min-h-11 min-w-11 md:min-h-9 md:min-w-9";
  const iconBtn = cx("inline-flex shrink-0 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg disabled:opacity-40", tall, focus);
  const textBtn = cx("rounded-control px-space-3 text-t14 font-semibold", touch ? "min-h-11" : "min-h-11 md:min-h-9", focus);

  const startNaming = (mode: "new" | "rename") => { setAsking(false); setNaming(mode); setName(mode === "rename" ? list?.name ?? "" : ""); };
  const submitName = async () => {
    const clean = name.split(/\s+/).filter(Boolean).join(" ");
    if (!clean) return;
    if (naming === "new") {
      const made = await store.create(clean);
      if (made) { onListId(made.id); setNaming(null); }
    } else if (list) {
      setNaming(null);
      if (clean !== list.name) await store.edit(list.id, { kind: "rename", name: clean });
    }
  };
  const removeList = async () => {
    if (!list) return;
    setAsking(false);
    if (await store.remove(list.id)) onListId(null);
  };
  const add = (item: WatchItem) => {
    setPicker((n) => n + 1);
    if (list) void store.edit(list.id, { kind: "add", item });
  };
  const move = (item: WatchItem, to: number) => { if (list) void store.edit(list.id, { kind: "move", item, to }); };

  // Reorder by dragging the handle (pointer events, the handle only, so the sheet still scrolls) or with the keys.
  const rowAt = (y: number) => {
    const rows = [...(rowsRef.current?.querySelectorAll("[data-row]") ?? [])];
    const i = rows.findIndex((r) => y < r.getBoundingClientRect().top + r.getBoundingClientRect().height / 2);
    return i < 0 ? rows.length - 1 : i;
  };
  const dragStart = (e: PointerEvent<HTMLButtonElement>, index: number) => {
    if (e.button !== 0) return;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    setDrag({ from: index, to: index });
  };
  const dragMove = (e: PointerEvent<HTMLButtonElement>) => { if (drag) setDrag({ ...drag, to: rowAt(e.clientY) }); };
  const dragEnd = () => {
    if (drag && drag.to !== drag.from) move(items[drag.from], drag.to);
    setDrag(null);
  };
  // On the handle the arrow keys move the row; anywhere else in the row Alt+arrow does.
  const keyMove = (e: KeyboardEvent<HTMLLIElement>, index: number) => {
    const handle = !!(e.target as HTMLElement).closest("[data-handle]");
    if ((e.key !== "ArrowUp" && e.key !== "ArrowDown") || (!handle && !e.altKey)) return;
    e.preventDefault();
    const to = index + (e.key === "ArrowUp" ? -1 : 1);
    if (to >= 0 && to < items.length) move(items[index], to);
  };
  const shown = drag ? moveItem(items, drag.from, drag.to) : items;

  return (
    <section aria-label="Watchlist" data-watchlist className={cx("flex min-h-0 flex-col text-t14 text-fg", className)}>
      <div className="flex items-center gap-space-1 border-b border-subtle p-space-2">
        {state.lists.length > 0 && !state.off ? (
          <>
            <label className="sr-only" htmlFor="watchlist-pick">Watchlist</label>
            <select id="watchlist-pick" value={list?.id ?? ""} onChange={(e) => onListId(Number(e.target.value))}
              className={cx("h-11 min-w-0 flex-1 rounded-control border border-border bg-surface-2 px-space-2 text-t14 font-semibold text-fg", !touch && "md:h-9", focus)}>
              {state.lists.map((l) => <option key={l.id} value={l.id}>{l.name} ({l.items.length})</option>)}
            </select>
            <button type="button" aria-label="New list" title="New list" className={iconBtn} onClick={() => startNaming("new")}><Plus size={16} aria-hidden /></button>
            <button type="button" aria-label="Rename list" title="Rename this list" className={iconBtn} disabled={!list} onClick={() => startNaming("rename")}><Pencil size={16} aria-hidden /></button>
            <button type="button" aria-label="Delete list" title="Delete this list" className={iconBtn} disabled={!list} onClick={() => { setNaming(null); setAsking(true); }}><Trash2 size={16} aria-hidden /></button>
          </>
        ) : (
          <h2 className="min-w-0 flex-1 px-space-1 text-t15 font-semibold">Watchlist</h2>
        )}
        {onClose && <button type="button" aria-label="Hide watchlist" title="Hide the watchlist" className={iconBtn} onClick={onClose}><PanelRightClose size={16} aria-hidden /></button>}
      </div>

      {naming && (
        <form className="flex items-center gap-space-1 border-b border-subtle p-space-2" onSubmit={(e) => { e.preventDefault(); void submitName(); }}>
          <label className="sr-only" htmlFor="watchlist-name">{naming === "new" ? "Name of the new list" : "New name of the list"}</label>
          <input id="watchlist-name" autoFocus value={name} maxLength={NAME_MAX} onChange={(e) => setName(e.target.value)} placeholder="List name"
            onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); setNaming(null); } }}
            className={cx("h-11 min-w-0 flex-1 rounded-control border border-border bg-surface-2 px-space-2 text-t14 text-fg placeholder:text-fg-muted focus:border-brand focus:outline-none", !touch && "md:h-9")} />
          <button type="submit" disabled={!name.trim()} className={cx(textBtn, "bg-brand text-on-brand hover:bg-brand-strong disabled:opacity-50")}>{naming === "new" ? "Create" : "Save"}</button>
          <button type="button" aria-label="Cancel" className={iconBtn} onClick={() => setNaming(null)}><X size={16} aria-hidden /></button>
        </form>
      )}

      {asking && list && (
        <ConfirmBox title={`Delete the list "${list.name}"?`} confirm="Delete list" onConfirm={() => void removeList()} onCancel={() => setAsking(false)}
          body={`Its ${list.items.length} ${list.items.length === 1 ? "symbol goes" : "symbols go"} with it, and this cannot be undone. Your charts and drawings stay.`}
          className="m-space-2" />
      )}

      {(state.error || state.note) && (
        <div role="status" className="space-y-space-1 border-b border-subtle px-space-3 py-space-2 text-t13">
          {state.error && <p className="text-fg">{state.error}</p>}
          {state.error && !state.loaded && <button type="button" className={cx(textBtn, "px-0 text-brand hover:underline")} onClick={() => void store.load()}>Try again</button>}
          {state.note && <p className="text-fg-muted">{state.note}</p>}
        </div>
      )}

      {!state.loaded && <p className="px-space-3 py-space-4 text-t13 text-fg-muted">Loading your watchlists…</p>}

      {state.off && <p role="status" className="px-space-3 py-space-4 text-t13 text-fg-muted">{SWITCHED_OFF}</p>}

      {state.loaded && !state.off && !state.error && state.lists.length === 0 && !naming && (
        <div className="space-y-space-2 px-space-3 py-space-4 text-t13 text-fg-muted">
          <p>You have no watchlists yet. A list keeps the symbols you follow one tap away from the chart.</p>
          <button type="button" className={cx(textBtn, "bg-brand text-on-brand hover:bg-brand-strong")} onClick={() => startNaming("new")}>Create a list</button>
        </div>
      )}

      {list && !state.off && (
        <>
          <div className="p-space-2">
            <SymbolPicker key={picker} value="" exchange="NSE" onPick={add} storageKey={storageKey} label={`Add a symbol to ${list.name}`} className="w-full" />
            {items.length >= MAX_ITEMS && <p className="mt-space-1 text-t12 text-fg-muted">This list holds the most it can ({MAX_ITEMS}). Remove a symbol to add another.</p>}
          </div>
          {items.length === 0 ? (
            <p className="px-space-3 pb-space-4 text-t13 text-fg-muted">This list is empty. Search a symbol above to add it.</p>
          ) : (
            <ul ref={rowsRef} aria-label={`Symbols in ${list.name}`} className="min-h-0 flex-1 overflow-y-auto px-space-1 pb-space-2">
              {shown.map((item) => {
                const index = items.indexOf(item);
                const q = index < QUOTE_ROWS ? quotes.rows.get(itemKey(item)) : undefined;
                const current = item.symbol === symbol && item.exchange === exchange;
                return (
                  <li key={itemKey(item)} data-row className={cx("flex items-center rounded-control", current && "bg-surface-2", drag && items[drag.from] === item && "opacity-60")}
                    onKeyDown={(e) => keyMove(e, index)}>
                    <button type="button" data-handle aria-label={`Move ${item.symbol}`} title="Drag, or use the arrow keys, to move"
                      onPointerDown={(e) => dragStart(e, index)} onPointerMove={dragMove} onPointerUp={dragEnd} onPointerCancel={() => setDrag(null)}
                      className={cx(iconBtn, "cursor-grab touch-none", !touch && "md:min-w-7")}>
                      <GripVertical size={14} aria-hidden />
                    </button>
                    <button type="button" aria-current={current ? "true" : undefined} onClick={() => onOpen(item)}
                      className={cx("flex min-w-0 flex-1 items-center gap-space-2 rounded-control px-space-1 text-left hover:bg-surface-2", touch ? "min-h-11" : "min-h-11 md:min-h-9", focus)}>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-semibold">{item.symbol}</span>
                        <span className="block text-t12 text-fg-muted">{item.exchange}</span>
                      </span>
                      <span className="text-right">
                        <span className="block tabular-nums" title={q?.missing ? q.error ?? undefined : undefined}>
                          {q && !q.missing && q.ltp != null ? formatPrice(q.ltp) : EMPTY}
                        </span>
                        <Change row={q} />
                      </span>
                    </button>
                    <button type="button" aria-label={`Remove ${item.symbol}`} title="Remove from this list" className={iconBtn}
                      onClick={() => void store.edit(list.id, { kind: "remove", item })}>
                      <X size={14} aria-hidden />
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          {(items.length > QUOTE_ROWS || quotes.failed) && (
            <p className="border-t border-subtle px-space-3 py-space-2 text-t12 text-fg-muted">
              {quotes.failed ? "Prices could not be loaded just now. They try again every 10 seconds." : `Prices show for the first ${QUOTE_ROWS} symbols. Move a symbol up to see its price.`}
            </p>
          )}
        </>
      )}
    </section>
  );
}
