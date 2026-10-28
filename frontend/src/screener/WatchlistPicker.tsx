/**
 * AMW-2 (flag `watchlists`): pick one of the caller's own watchlists and load its symbols into the page's universe
 * (Screener: "My watchlists"; Scanner: "Load watchlist"), plus a small manage panel - save the current symbols as a
 * new list, replace the chosen list's symbols, delete it. The parent renders this only while the flag is on; a failed
 * load renders nothing. A watchlist is a list of names: loading one runs nothing.
 */
import { useEffect, useState } from "react";
import { Combobox } from "../ui/Combobox";
import { isWatchlists, loadNote, optionLabel, replaceWarning, shownList, watchlistsApi, type Watchlist, type WatchlistClient } from "./watchlists";

interface Props {
  /** The page's current symbols (what "Save as new" and "Replace" store). */
  symbols: string[];
  onLoad: (symbols: string[], name: string) => void;
  /** The page's symbol cap, to say when a longer list is cut. */
  max?: number;
  label?: string;
  /** Tests and server rendering: the lists to start from (no fetch then). */
  initial?: Watchlist[];
  /** Tests: the selection to start from (e.g. a list that was just deleted). */
  initialSelected?: number | "";
  client?: WatchlistClient;
}

const field = "rounded border border-border bg-surface-2 px-2 py-1 text-xs text-fg focus:outline-none focus:ring-2 focus:ring-brand";
const button = "rounded border border-border px-2 py-1 text-xs text-fg hover:bg-surface-2 disabled:opacity-50";

export function WatchlistPicker({ symbols, onLoad, max, label = "My watchlists", initial, initialSelected, client = watchlistsApi }: Props) {
  const [lists, setLists] = useState<Watchlist[] | null>(initial ?? null);
  const [failed, setFailed] = useState(false);
  const [selected, setSelected] = useState<number | "">(initialSelected ?? initial?.[0]?.id ?? "");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (initial) return;
    client.list().then((l) => {
      if (!isWatchlists(l)) { setFailed(true); return; }
      setLists(l);
      setSelected((s) => (s === "" && l.length ? l[0].id : s));
    }).catch(() => setFailed(true));
  }, [client, initial]);

  if (failed || lists === null) return null;
  // What the select shows and Load / Replace / Delete act on: the selected list, or the first one when the selection
  // is gone (deleted, or not loaded yet) - so the select and the buttons never disagree.
  const chosen = shownList(lists, selected);

  async function act(done: string, fn: () => Promise<unknown>) {
    setBusy(true); setError(null); setMessage(null);
    try {
      await fn();
      const fresh = await client.list();
      if (isWatchlists(fresh)) setLists(fresh);
      setMessage(done);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const saveNew = () => act(`Saved "${name.trim()}".`, async () => {
    const created = await client.create(name.trim(), symbols);
    setSelected(created.id);
    setName("");
  });
  const replace = () => {
    if (!chosen) return;
    // A shorter list (e.g. the Screener's first 50 of a longer list) would drop symbols: ask first.
    const warning = replaceWarning(chosen, symbols.length);
    if (warning && !window.confirm(warning)) return;
    void act(`Updated "${chosen.name}".`, () => client.update(chosen.id, chosen.name, symbols));
  };
  const remove = () => {
    if (!chosen || !window.confirm(`Delete the watchlist "${chosen.name}"?`)) return;
    void act(`Deleted "${chosen.name}".`, async () => { await client.remove(chosen.id); setSelected(""); });
  };
  const note = loadNote(chosen, max);

  return (
    <div className="mt-3 space-y-2 text-xs" data-testid="watchlist-picker">
      <label className="block text-fg-muted" htmlFor="watchlist-select">{label}</label>
      {lists.length === 0 ? (
        <p className="text-fg-muted">No watchlists yet. Type symbols, then save them as a list below.</p>
      ) : (
        <div className="flex gap-2">
          {/* SCR-FIX D8: the shared picker, not the browser's select */}
          <Combobox id="watchlist-select" label={label} className="min-w-0 flex-1" value={chosen ? String(chosen.id) : ""}
                    onChange={(v) => setSelected(v ? Number(v) : "")} searchable={lists.length > 12}
                    options={lists.map((w) => ({ value: String(w.id), label: optionLabel(w) }))} />
          <button type="button" className={button} disabled={!chosen || chosen.symbols.length === 0}
                  onClick={() => chosen && onLoad(max !== undefined ? chosen.symbols.slice(0, max) : chosen.symbols, chosen.name)}
                  data-testid="watchlist-load">Load</button>
        </div>
      )}
      {note && <p className="text-fg-muted">{note}</p>}
      <details>
        <summary className="cursor-pointer text-fg-muted">Manage watchlists</summary>
        <div className="mt-2 space-y-2">
          <div className="flex gap-2">
            <input aria-label="New watchlist name" className={`${field} min-w-0 flex-1`} value={name} maxLength={60}
                   placeholder="Name" onChange={(e) => setName(e.target.value)} />
            <button type="button" className={button} disabled={busy || !name.trim()} onClick={() => void saveNew()}>
              Save {symbols.length} as new
            </button>
          </div>
          {chosen && (
            <div className="flex flex-wrap gap-2">
              <button type="button" className={button} disabled={busy} onClick={replace}>
                Replace &quot;{chosen.name}&quot; with these {symbols.length}
              </button>
              <button type="button" className={`${button} text-down`} disabled={busy} onClick={remove}>Delete &quot;{chosen.name}&quot;</button>
            </div>
          )}
        </div>
      </details>
      {error && <p role="alert" className="text-down">{error}</p>}
      {message && <p className="text-up">{message}</p>}
    </div>
  );
}
