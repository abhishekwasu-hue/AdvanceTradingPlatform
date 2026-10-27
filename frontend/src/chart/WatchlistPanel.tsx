import { ArrowDown, ArrowUp, ChevronDown, Copy, GripVertical, Lock, PanelRightClose, Pencil, Plus, Repeat, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type KeyboardEvent, type PointerEvent } from "react";
import { cx } from "../components/primitives/cx";
import { EMPTY, formatDateIST, formatPct, formatPrice } from "../ui/format";
import { SymbolPicker } from "../ui/SymbolPicker";
import { useLiveTicks } from "../live/liveTicks";
import { SkeletonRows } from "../components/primitives/Feedback";
import { flashClass, useTickFlash } from "../ui/useTickFlash";
import {
  contractItems, contractSubline, contractsApi, contractsKey, holdsTarget, known, rollEdit, splitExpired, type ContractFacts, type KnownContract,
} from "../fno/contracts";
import { ConfirmBox } from "./ConfirmBox";
import { ListBrowser } from "./ListBrowser";
import {
  MY_LISTS, ROW_SORTS, browserGroups, copyNote, filterRows, groupOf, namesCount, pickList, sortRows, systemListsApi,
  type ListRef, type RowSort, type SystemGroup, type SystemItem, type SystemList,
} from "./systemLists";
import {
  MAX_ITEMS, NAME_MAX, QUOTE_ROWS, QuotePoller, SWITCHED_OFF, WatchlistsStore, direction, itemKey, moveItem, quoteItems, watchlistError,
  watchlistsV2Api, withTicks,
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
 *
 * WL1 (handover item 4): when the server has system lists, a list chooser shows every list by group - "Indices",
 * "Index stocks", "Sectors", "F&O" (read-only, marked with a lock) and then "My lists" - each group opening and closing.
 * A system list has a search on symbol or company, a sort (list order, A to Z, top gainers / losers, price) and "Copy
 * to my list" (a list of the trader's own, which he can change). On a first open with no list of his own, the
 * "Indices" list shows. Without system lists the panel is exactly as before.
 */
export interface WatchlistPanelProps {
  /** The symbol on the chart (its row is marked). */
  symbol: string;
  exchange: string;
  onOpen: (item: WatchItem) => void;
  /** The list shown (null: the first), kept by the page. */
  listId: number | null;
  onListId: (id: number | null) => void;
  /** WL1: the system list shown (its key; null: one of the trader's own), kept by the page. */
  systemKey?: string | null;
  onSystemKey?: (key: string | null) => void;
  /** This user's recent-symbols key for the add search. */
  storageKey: string | null;
  /** Wide screens: hides the column. */
  onClose?: () => void;
  /** The phone sheet: 44 px targets everywhere. */
  touch?: boolean;
  /** TM Part 1 (flag `data_hub`): prices move with the live ticks of the user's own broker connection. */
  liveTicks?: boolean;
  className?: string;
}

const focus = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";

/** The prices of the first 50 rows (QuotePoller: a reorder asks for nothing new; a changed set waits out the 10 s). */
function useQuotes(items: readonly WatchItem[], live = false) {
  const [quotes, setQuotes] = useState<{ rows: Map<string, QuoteRow>; failed: boolean }>({ rows: new Map(), failed: false });
  const poller = useMemo(() => new QuotePoller(watchlistsV2Api.quotes, {
    data: (reply) => setQuotes({ rows: new Map(reply.items.map((r) => [itemKey(r), r])), failed: false }),
    error: () => setQuotes((q) => ({ ...q, failed: true })),
  }), []);
  useEffect(() => () => poller.close(), [poller]);
  useEffect(() => { poller.update(items); }, [poller, items]);
  // TM Part 1 (flag data_hub): the rows' prices move with the live ticks of the user's own broker connection.
  const ticks = useLiveTicks(useMemo(() => quoteItems(items), [items]), live);
  return useMemo(() => (ticks.size === 0 ? quotes : { ...quotes, rows: withTicks(quotes.rows, ticks) }), [quotes, ticks]);
}

/** FNO-4: what the list's F&O contract rows are (name, days left, Expired, the roll target), read again when the set of
 * contract rows changes and every few minutes (a contract expires at the close). Any failure - `ui_v2` off included -
 * reads as "nothing known", so the rows show as before. */
const CONTRACTS_REFRESH_MS = 5 * 60_000;
export function useContractFacts(items: readonly WatchItem[]): ReadonlyMap<string, ContractFacts> {
  const [facts, setFacts] = useState<ReadonlyMap<string, ContractFacts>>(new Map());
  const key = contractsKey(items);
  const latest = useRef(items);
  latest.current = items;
  useEffect(() => {
    if (!key) { setFacts(new Map()); return undefined; }
    let live = true;
    const load = () => {
      contractsApi.lookup(contractItems(latest.current)).then(
        (r) => { if (live) setFacts(new Map((r?.items ?? []).map((f) => [itemKey(f), f]))); },
        () => { if (live) setFacts(new Map()); },
      );
    };
    load();
    const timer = setInterval(load, CONTRACTS_REFRESH_MS);
    return () => { live = false; clearInterval(timer); };
  }, [key]);
  return facts;
}

/** WL1: the system groups, read once. Any failure (the flag off included) reads as "none", so the panel is as before. */
function useSystemGroups(off: boolean): SystemGroup[] {
  const [groups, setGroups] = useState<SystemGroup[]>([]);
  useEffect(() => {
    if (off) return undefined;
    let live = true;
    systemListsApi.groups().then((r) => { if (live) setGroups(Array.isArray(r?.groups) ? r.groups : []); }, () => { if (live) setGroups([]); });
    return () => { live = false; };
  }, [off]);
  return groups;
}

/** WL1: the rows of the system lists opened, kept while the panel is open ("failed": they could not be read). */
function useSystemList(key: string | null) {
  const [lists, setLists] = useState<Map<string, SystemList | "failed">>(new Map());
  const loading = useRef(new Set<string>());                   // one read per list at a time
  const load = useCallback((k: string) => {
    if (loading.current.has(k)) return;
    loading.current.add(k);
    const done = (v: SystemList | "failed") => { loading.current.delete(k); setLists((m) => new Map(m).set(k, v)); };
    systemListsApi.list(k).then(done, () => done("failed"));
  }, []);
  useEffect(() => { if (key && !lists.has(key)) load(key); }, [key, lists, load]);
  const retry = (k: string) => { setLists((m) => { const n = new Map(m); n.delete(k); return n; }); };
  return { list: key ? lists.get(key) : undefined, retry };
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

/** FNO-4: a row's name and second line - a contract as traders write it ("NIFTY 28 OCT 22800 CE", "6 days left · OI
 * 12.5 L"), anything else as before (symbol, exchange). */
export function RowName({ item, facts, q }: { item: WatchItem; facts: ContractFacts | undefined; q: QuoteRow | undefined }) {
  if (known(facts)) {
    return (
      <span className="min-w-0 flex-1 py-space-1" data-contract={facts.right}>
        <span className="block break-words font-semibold leading-tight" title={item.symbol}>{facts.label}</span>
        <span className="block text-t12 text-fg-muted">{contractSubline(facts, q?.oi)}</span>
      </span>
    );
  }
  return (
    <span className="min-w-0 flex-1">
      <span className="block truncate font-semibold">{item.symbol}</span>
      <span className="block text-t12 text-fg-muted">{item.exchange}</span>
    </span>
  );
}

/** FNO-4: the list's expired contracts, below the others - each with "Roll to next expiry" (the same strike in the next
 * expiry, replacing the row in place: a watchlist edit only, nothing is traded) and remove. */
export function ExpiredRows({ rows, facts, listName, inList, onRoll, onRemove, iconBtn, touch }: {
  rows: readonly WatchItem[]; facts: ReadonlyMap<string, ContractFacts>; listName: string;
  inList: (f: ContractFacts | undefined) => boolean; onRoll: (item: WatchItem, f: KnownContract) => void; onRemove: (item: WatchItem) => void;
  iconBtn: string; touch: boolean;
}) {
  if (rows.length === 0) return null;
  return (
    <div data-expired className="border-t border-subtle px-space-1 pb-space-2">
      <h3 className="px-space-2 pb-space-1 pt-space-2 text-t12 font-semibold uppercase tracking-wide text-fg-muted">Expired ({rows.length})</h3>
      <ul aria-label={`Expired contracts in ${listName}`}>
        {rows.map((item) => {
          const f = facts.get(itemKey(item));
          if (!known(f)) return null;
          const already = inList(f);
          return (
            <li key={itemKey(item)} data-expired-row={item.symbol} className="flex flex-wrap items-center gap-x-space-1 gap-y-space-1 rounded-control px-space-2 py-space-1">
              <span className="min-w-0 basis-full">
                <span className="block break-words font-semibold leading-tight text-fg-muted" title={item.symbol}>{f.label}</span>
                <span className="block text-t12 text-fg-muted">
                  {f.roll ? `Next: ${f.roll.label}` : "The next expiry is not listed yet"}
                </span>
              </span>
              {f.roll && (
                <button type="button" onClick={() => onRoll(item, f)} data-action="roll"
                  title={already ? `${f.roll.label} is already in this list: this removes the expired row` : `Replace with ${f.roll.label}`}
                  className={cx("inline-flex shrink-0 items-center gap-space-1 rounded-control border border-border px-space-2 text-t13 font-semibold text-brand hover:bg-surface-2",
                    touch ? "min-h-11" : "min-h-11 md:min-h-9", focus)}>
                  <Repeat size={14} aria-hidden />Roll to next expiry
                </button>
              )}
              <button type="button" aria-label={`Remove ${f.label}`} title="Remove from this list" className={cx(iconBtn, "ml-auto")} onClick={() => onRemove(item)}>
                <X size={14} aria-hidden />
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Price({ q }: { q: QuoteRow | undefined }) {
  // UX-7: a new price shows a short up / down tint.
  const flash = useTickFlash(q && !q.missing ? q.ltp : null);
  return (
    <span className="text-right">
      <span data-flash={flash ?? undefined} className={cx("block rounded-control px-space-1 tabular-nums transition-colors duration-base", flashClass(flash))}
        title={q?.missing ? q.error ?? undefined : undefined}>
        {q && !q.missing && q.ltp != null ? formatPrice(q.ltp) : EMPTY}
      </span>
      <Change row={q} />
    </span>
  );
}

export function WatchlistPanel({ symbol, exchange, onOpen, listId, onListId, systemKey = null, onSystemKey, storageKey, onClose, touch = false, className, liveTicks = false }: WatchlistPanelProps) {
  const store = useMemo(() => new WatchlistsStore(), []);
  const state = useSyncExternalStore(store.subscribe, store.getState);
  useEffect(() => { void store.load(); }, [store]);

  // WL1: the system lists (none: the panel works exactly as before).
  const [wantedKey, setWantedKey] = useState<string | null>(systemKey);
  useEffect(() => { setWantedKey(systemKey); }, [systemKey]);
  const systemGroups = useSystemGroups(state.off);
  const hasSystem = systemGroups.length > 0;
  // Chosen once the trader's own lists are known, so a first open does not flash a system list before his own.
  const ref: ListRef | null = state.off || !state.loaded || !hasSystem ? null : pickList(wantedKey, listId, state.lists, systemGroups);
  const sys = useSystemList(ref?.kind === "system" ? ref.key : null);
  const systemList = ref?.kind === "system" && sys.list && sys.list !== "failed" ? sys.list : null;
  const systemSummary = ref?.kind === "system" ? systemGroups.flatMap((g) => g.lists).find((l) => l.key === ref.key) : undefined;

  const list: WatchlistV2 | undefined = ref?.kind === "own" ? state.lists.find((l) => l.id === ref.id) : hasSystem ? undefined : state.lists.find((l) => l.id === listId) ?? state.lists[0];
  const [rowQuery, setRowQuery] = useState("");
  const [sort, setSort] = useState<RowSort>("list");
  const matching = useMemo(() => (systemList ? filterRows(systemList.items, rowQuery) : []), [systemList, rowQuery]);
  const items = useMemo(() => (state.off ? [] : systemList ? matching : list?.items ?? []), [list, state.off, systemList, matching]);
  // The price poll keeps the list's own rows (so a lookup answering later never restarts it and leaves a gap).
  const quotes = useQuotes(items, liveTicks);
  // FNO-4: the contract rows of the trader's own list; expired ones move to "Expired" below the others (no price shown).
  const ownItems = useMemo(() => (systemList || state.off ? [] : list?.items ?? []), [list, state.off, systemList]);
  const facts = useContractFacts(ownItems);
  const split = useMemo(() => splitExpired(items, systemList ? new Map() : facts), [items, facts, systemList]);
  const live = split.live;

  const [naming, setNaming] = useState<null | "new" | "rename">(null);
  const [name, setName] = useState("");
  const [asking, setAsking] = useState(false);
  const [picker, setPicker] = useState(0);                     // a new key empties the add search after each add
  const [drag, setDrag] = useState<{ from: number; to: number } | null>(null);
  const [browsing, setBrowsing] = useState(false);
  const [listQuery, setListQuery] = useState("");
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set([MY_LISTS]));
  const [notice, setNotice] = useState<string | null>(null);
  const [copying, setCopying] = useState(false);
  const rowsRef = useRef<HTMLUListElement>(null);
  const refKey = ref ? (ref.kind === "system" ? `s:${ref.key}` : `o:${ref.id}`) : "";
  useEffect(() => { setAsking(false); setNaming(null); setRowQuery(""); setSort("list"); }, [list?.id, refKey]);

  const tall = touch ? "min-h-11 min-w-11" : "min-h-11 min-w-11 md:min-h-9 md:min-w-9";
  const iconBtn = cx("inline-flex shrink-0 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg disabled:opacity-40", tall, focus);
  const textBtn = cx("rounded-control px-space-3 text-t14 font-semibold", touch ? "min-h-11" : "min-h-11 md:min-h-9", focus);
  const field = cx("h-11 min-w-0 rounded-control border border-border bg-surface-2 px-space-2 text-t14 text-fg", !touch && "md:h-9", focus);

  const choose = (next: ListRef) => {
    setBrowsing(false);
    setListQuery("");
    setNotice(null);
    if (next.kind === "system") { setWantedKey(next.key); onSystemKey?.(next.key); } else { setWantedKey(null); onSystemKey?.(null); onListId(next.id); }
  };
  const openBrowser = () => {
    const g = groupOf(ref, systemGroups);
    if (g) setOpenGroups((s) => new Set(s).add(g));
    setNaming(null);
    setAsking(false);
    setBrowsing((b) => !b);
  };
  const toggleGroup = (g: string) => setOpenGroups((s) => { const n = new Set(s); if (n.has(g)) n.delete(g); else n.add(g); return n; });

  const startNaming = (mode: "new" | "rename") => { setAsking(false); setBrowsing(false); setNaming(mode); setName(mode === "rename" ? list?.name ?? "" : ""); };
  const submitName = async () => {
    const clean = name.split(/\s+/).filter(Boolean).join(" ");
    if (!clean) return;
    if (naming === "new") {
      const made = await store.create(clean);
      if (made) { choose({ kind: "own", id: made.id }); setNaming(null); }
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
  const copyList = async () => {
    if (!systemSummary || copying) return;
    setCopying(true);
    try {
      const made = await systemListsApi.copy(systemSummary.key);
      store.adopt(made);
      choose({ kind: "own", id: made.id });
      setNotice(copyNote(made));
    } catch (e) {
      setNotice(watchlistError(e, "create", systemSummary.name));
    } finally {
      setCopying(false);
    }
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
  // Positions are counted among the rows still trading (the expired ones sit below them, not draggable).
  const dragEnd = () => {
    if (drag && drag.to !== drag.from) move(live[drag.from], items.indexOf(live[drag.to]));
    setDrag(null);
  };
  // On the handle the arrow keys move the row; anywhere else in the row Alt+arrow does.
  const keyMove = (e: KeyboardEvent<HTMLLIElement>, index: number) => {
    const handle = !!(e.target as HTMLElement).closest("[data-handle]");
    if ((e.key !== "ArrowUp" && e.key !== "ArrowDown") || (!handle && !e.altKey)) return;
    e.preventDefault();
    const to = index + (e.key === "ArrowUp" ? -1 : 1);
    if (to >= 0 && to < live.length) move(live[index], items.indexOf(live[to]));
  };
  const shown = drag ? moveItem(live, drag.from, drag.to) : live;
  const roll = (item: WatchItem, f: KnownContract) => {
    const edit = rollEdit(item, f);
    if (!list || !edit || !f.roll) return;
    setNotice(`Rolled ${f.label} to ${f.roll.label} in ${list.name}. Nothing was traded.`);
    void store.edit(list.id, edit);
  };
  const priced = useMemo(() => new Map(matching.slice(0, QUOTE_ROWS).map((i) => [itemKey(i), quotes.rows.get(itemKey(i))]).filter((e): e is [string, QuoteRow] => !!e[1])),
    [matching, quotes.rows]);
  const systemRows = useMemo(() => sortRows(matching, sort, priced), [matching, sort, priced]);
  const currentName = systemSummary?.name ?? list?.name ?? "Watchlist";
  const currentCount = systemSummary?.count ?? list?.items.length ?? 0;

  return (
    <section aria-label="Watchlist" data-watchlist className={cx("flex min-h-0 flex-col text-t14 text-fg", className)}>
      <div className="flex items-center gap-space-1 border-b border-subtle p-space-2">
        {hasSystem && !state.off ? (
          <>
            <button type="button" aria-expanded={browsing} aria-label={`Choose a list. Shown: ${currentName}`} onClick={openBrowser}
              className={cx("flex h-11 min-w-0 flex-1 items-center gap-space-1 rounded-control border border-border bg-surface-2 px-space-2 text-left text-t14 font-semibold text-fg", !touch && "md:h-9", focus)}>
              {systemSummary && <Lock size={12} aria-hidden className="shrink-0 text-fg-muted" />}
              <span className="min-w-0 flex-1 truncate">{currentName}{!namesCount(currentName) && <> <span className="font-normal text-fg-muted">({currentCount})</span></>}</span>
              <ChevronDown size={14} aria-hidden className="shrink-0 text-fg-muted" />
            </button>
            <button type="button" aria-label="New list" title="New list" className={iconBtn} onClick={() => startNaming("new")}><Plus size={16} aria-hidden /></button>
            {list && <button type="button" aria-label="Rename list" title="Rename this list" className={iconBtn} onClick={() => startNaming("rename")}><Pencil size={16} aria-hidden /></button>}
            {list && <button type="button" aria-label="Delete list" title="Delete this list" className={iconBtn} onClick={() => { setNaming(null); setBrowsing(false); setAsking(true); }}><Trash2 size={16} aria-hidden /></button>}
          </>
        ) : state.lists.length > 0 && !state.off ? (
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

      {browsing && hasSystem && (
        <ListBrowser groups={browserGroups(systemGroups, state.lists, listQuery)} open={openGroups} onToggle={toggleGroup} current={ref}
          onPick={choose} query={listQuery} onQuery={setListQuery} touch={touch} />
      )}

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

      {(state.error || state.note || notice) && (
        <div role="status" className="space-y-space-1 border-b border-subtle px-space-3 py-space-2 text-t13">
          {state.error && <p className="text-fg">{state.error}</p>}
          {state.error && !state.loaded && <button type="button" className={cx(textBtn, "px-0 text-brand hover:underline")} onClick={() => void store.load()}>Try again</button>}
          {state.note && <p className="text-fg-muted">{state.note}</p>}
          {notice && <p className="text-fg-muted">{notice}</p>}
        </div>
      )}

      {!state.loaded && <SkeletonRows label="Loading your watchlists…" />}

      {state.off && <p role="status" className="px-space-3 py-space-4 text-t13 text-fg-muted">{SWITCHED_OFF}</p>}

      {state.loaded && !state.off && !state.error && state.lists.length === 0 && !hasSystem && !naming && (
        <div className="space-y-space-2 px-space-3 py-space-4 text-t13 text-fg-muted">
          <p>You have no watchlists yet. A list keeps the symbols you follow one tap away from the chart.</p>
          <button type="button" className={cx(textBtn, "bg-brand text-on-brand hover:bg-brand-strong")} onClick={() => startNaming("new")}>Create a list</button>
        </div>
      )}

      {systemSummary && !state.off && !browsing && (
        <SystemListView summary={systemSummary} list={sys.list} rows={systemRows} total={systemList?.items.length ?? systemSummary.count} matching={matching.length}
          query={rowQuery} onQuery={setRowQuery} sort={sort} onSort={setSort} quotes={priced} quotesFailed={quotes.failed}
          current={(i) => i.symbol === symbol && i.exchange === exchange} onOpen={onOpen} onCopy={() => void copyList()} copying={copying}
          onRetry={() => sys.retry(systemSummary.key)} touch={touch} field={field} textBtn={textBtn} />
      )}

      {list && !state.off && !browsing && (
        <>
          <div className="p-space-2">
            <SymbolPicker key={picker} value="" exchange="NSE" onPick={add} storageKey={storageKey} label={`Add a symbol to ${list.name}`} className="w-full" />
            {items.length >= MAX_ITEMS && <p className="mt-space-1 text-t12 text-fg-muted">This list holds the most it can ({MAX_ITEMS}). Remove a symbol to add another.</p>}
          </div>
          {items.length === 0 ? (
            <p className="px-space-3 pb-space-4 text-t13 text-fg-muted">This list is empty. Search a symbol above to add it.</p>
          ) : live.length === 0 ? null : (
            <ul ref={rowsRef} aria-label={`Symbols in ${list.name}`} className="min-h-0 flex-1 overflow-y-auto px-space-1 pb-space-2">
              {shown.map((item) => {
                const index = live.indexOf(item);
                const q = items.indexOf(item) < QUOTE_ROWS ? quotes.rows.get(itemKey(item)) : undefined;
                const current = item.symbol === symbol && item.exchange === exchange;
                return (
                  <li key={itemKey(item)} data-row className={cx("flex items-center rounded-control", current && "bg-surface-2", drag && live[drag.from] === item && "opacity-60")}
                    onKeyDown={(e) => keyMove(e, index)}>
                    <button type="button" data-handle aria-label={`Move ${item.symbol}`} title="Drag, or use the arrow keys, to move"
                      onPointerDown={(e) => dragStart(e, index)} onPointerMove={dragMove} onPointerUp={dragEnd} onPointerCancel={() => setDrag(null)}
                      className={cx(iconBtn, "cursor-grab touch-none", !touch && "md:min-w-7")}>
                      <GripVertical size={14} aria-hidden />
                    </button>
                    <button type="button" aria-current={current ? "true" : undefined} onClick={() => onOpen(item)}
                      className={cx("flex min-w-0 flex-1 items-center gap-space-2 rounded-control px-space-1 text-left hover:bg-surface-2", touch ? "min-h-11" : "min-h-11 md:min-h-9", focus)}>
                      <RowName item={item} facts={facts.get(itemKey(item))} q={q} />
                      <Price q={q} />
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
          <ExpiredRows rows={split.expired} facts={facts} listName={list.name} inList={(f) => holdsTarget(list, f)} onRoll={roll}
            onRemove={(item) => void store.edit(list.id, { kind: "remove", item })} iconBtn={iconBtn} touch={touch} />
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

export interface SystemListViewProps {
  summary: { key: string; name: string; count: number; refreshed_at?: string | null };
  /** The rows as loaded: undefined while loading, "failed" when they could not be read. */
  list: SystemList | "failed" | undefined;
  /** The rows to show: searched and sorted. */
  rows: SystemItem[];
  total: number;
  matching: number;
  query: string;
  onQuery: (q: string) => void;
  sort: RowSort;
  onSort: (s: RowSort) => void;
  quotes: ReadonlyMap<string, QuoteRow>;
  quotesFailed: boolean;
  current: (item: WatchItem) => boolean;
  onOpen: (item: WatchItem) => void;
  onCopy: () => void;
  copying: boolean;
  onRetry: () => void;
  touch: boolean;
  field: string;
  textBtn: string;
}

/** WL1: one read-only system list - "read-only" and "Copy to my list" on top, a search and a sort, then the rows (no
 * handle, no remove: the list is the exchange's, rebuilt by the server). */
export function SystemListView({ summary, list, rows, total, matching, query, onQuery, sort, onSort, quotes, quotesFailed, current, onOpen, onCopy,
  copying, onRetry, touch, field, textBtn }: SystemListViewProps) {
  return (
    <div data-system-list={summary.key} className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-space-2 border-b border-subtle px-space-3 py-space-2">
        <p className="inline-flex min-w-0 flex-1 basis-48 items-center gap-space-1 text-t12 text-fg-muted">
          <Lock size={12} aria-hidden className="shrink-0" />
          <span>Read-only list{summary.refreshed_at ? ` · updated ${formatDateIST(summary.refreshed_at)}` : ""}</span>
        </p>
        <button type="button" onClick={onCopy} disabled={copying} title="Make your own copy you can change"
          className={cx(textBtn, "inline-flex items-center gap-space-1 border border-border px-space-2 text-brand hover:bg-surface-2 disabled:opacity-50")}>
          <Copy size={14} aria-hidden />{copying ? "Copying…" : "Copy to my list"}
        </button>
      </div>
      <div className="flex items-center gap-space-1 p-space-2">
        <label className="sr-only" htmlFor="watchlist-row-search">Search in {summary.name}</label>
        <input id="watchlist-row-search" type="search" value={query} onChange={(e) => onQuery(e.target.value)} placeholder="Search symbol or company"
          className={cx(field, "flex-1 placeholder:text-fg-muted")} />
        <label className="sr-only" htmlFor="watchlist-row-sort">Sort the list</label>
        <select id="watchlist-row-sort" value={sort} onChange={(e) => onSort(e.target.value as RowSort)} className={cx(field, "w-32 shrink-0")}>
          {ROW_SORTS.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
        </select>
      </div>
      {list === undefined && <SkeletonRows label="Loading the list…" />}
      {list === "failed" && (
        <div role="status" className="space-y-space-1 px-space-3 py-space-4 text-t13">
          <p className="text-fg">This list could not be loaded. Check your connection, then try again.</p>
          <button type="button" className={cx(textBtn, "px-0 text-brand hover:underline")} onClick={onRetry}>Try again</button>
        </div>
      )}
      {list && list !== "failed" && rows.length === 0 && (
        <p className="px-space-3 pb-space-4 text-t13 text-fg-muted">{query.trim() ? `No symbol or company in this list matches "${query.trim()}".` : "This list is empty just now."}</p>
      )}
      {list && list !== "failed" && rows.length > 0 && (
        <ul aria-label={`Symbols in ${summary.name}`} className="min-h-0 flex-1 overflow-y-auto px-space-1 pb-space-2">
          {rows.map((item) => {
            const q = quotes.get(itemKey(item));
            const on = current(item);
            return (
              <li key={itemKey(item)} data-row className={cx("flex items-center rounded-control", on && "bg-surface-2")}>
                <button type="button" aria-current={on ? "true" : undefined} onClick={() => onOpen({ symbol: item.symbol, exchange: item.exchange })}
                  className={cx("flex min-w-0 flex-1 items-center gap-space-2 rounded-control px-space-2 text-left hover:bg-surface-2", touch ? "min-h-11" : "min-h-11 md:min-h-9", focus)}>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-semibold">{item.symbol}</span>
                    <span className="block truncate text-t12 text-fg-muted">{item.name ? `${item.exchange} · ${item.name}` : item.exchange}</span>
                  </span>
                  <Price q={q} />
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {list && list !== "failed" && (matching > QUOTE_ROWS || quotesFailed || matching < total) && (
        <p className="border-t border-subtle px-space-3 py-space-2 text-t12 text-fg-muted">
          {quotesFailed ? "Prices could not be loaded just now. They try again every 10 seconds."
            : matching > QUOTE_ROWS ? `Prices show for the first ${QUOTE_ROWS} symbols. Search to see the price of another.`
              : `${matching} of ${total} symbols match.`}
        </p>
      )}
    </div>
  );
}
