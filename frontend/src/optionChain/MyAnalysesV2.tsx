import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Button } from "../components/primitives/Button";
import { cx } from "../components/primitives/cx";
import { AnalysisResultView } from "./AskResultV2";
import {
  OC_ANALYSES_EVENT, cleanName, errorSentence, evaluateSpec, loadSaved, saveSaved, shownResult, withDeleted, withRenamed,
  type AnalysisSpec, type AskResponse, type SaveOutcome, type SavedItems, type SavedState,
} from "./askAi";

/**
 * OC3: "My analyses" - the user's saved Option chain analyses as their own tabs. Each is a saved spec evaluated on the
 * current chain by the server (`POST /api/option-chain/eval`): it updates with the chain (a new `refreshKey`) and uses
 * no AI. The list lives in the user's own preferences on the server, so phone and desktop show the same tabs; rename
 * and delete save the whole list on the version it was read on (another device's newer list wins and is shown).
 *
 * Standalone: `<MyAnalyses symbol="NIFTY" expiry={expiry} refreshKey={tick} />`. It reloads when AskAi saves one.
 */
export interface MyAnalysesProps {
  symbol: string;
  expiry?: string | null;
  refreshKey?: number | string;
  load?: () => Promise<SavedState>;
  save?: (items: SavedItems, version: number) => Promise<SaveOutcome>;
  evaluate?: (symbol: string, expiry: string | null, spec: AnalysisSpec) => Promise<AskResponse>;
}

export function MyAnalyses({ symbol, expiry = null, refreshKey, load = loadSaved, save = saveSaved, evaluate = evaluateSpec }: MyAnalysesProps) {
  const [saved, setSaved] = useState<SavedState | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [response, setResponse] = useState<AskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const calls = useRef({ load, save, evaluate });
  calls.current = { load, save, evaluate };

  const reload = () => {
    calls.current.load()
      .then((s) => { setSaved(s); setNote(null); })
      .catch(() => setNote("Your saved analyses could not be read. Check your connection and try again."));
  };
  useEffect(() => {
    reload();
    if (typeof window === "undefined") return;
    window.addEventListener(OC_ANALYSES_EVENT, reload);
    return () => window.removeEventListener(OC_ANALYSES_EVENT, reload);
  }, []);   // the loaders are read through `calls`

  const ids = saved ? Object.keys(saved.items) : [];
  const current = active && ids.includes(active) ? active : ids[0] ?? null;
  const spec = current && saved ? saved.items[current].spec : null;
  const specKey = spec ? JSON.stringify(spec) : "";

  useEffect(() => {
    setResponse(null); setError(null);
    if (!spec || !symbol.trim()) return;
    let live = true;
    calls.current.evaluate(symbol, expiry, spec)
      .then((r) => { if (live) setResponse(r); })
      .catch((e) => { if (live) setError(errorSentence(e)); });
    return () => { live = false; };
  }, [current, specKey, symbol, expiry]);   // a change of tab, spec, symbol or expiry starts again from empty

  useEffect(() => {
    if (!spec || !symbol.trim() || refreshKey === undefined) return;
    let live = true;
    calls.current.evaluate(symbol, expiry, spec)
      .then((r) => { if (live) setResponse(r); })
      .catch(() => { /* the last table stays; the next refresh tries again */ });
    return () => { live = false; };
  }, [refreshKey]);   // a new chain reading: the same spec, new numbers

  const store = async (items: SavedItems, done: string) => {
    if (!saved) return;
    const outcome = await calls.current.save(items, saved.version);
    if (outcome.ok) { setSaved(outcome.state); setNote(done); return; }
    if (outcome.conflict) setSaved(outcome.conflict);
    setNote(outcome.message);
  };

  return (
    <MyAnalysesView saved={saved} current={current} onPick={setActive} response={response} error={error} note={note}
      onRename={(id, name) => {
        const clean = cleanName(name);
        if (!clean || !saved) { setNote("A name uses letters, digits and simple punctuation, up to 40 characters."); return; }
        void store(withRenamed(saved.items, id, clean), `Renamed to "${clean}".`);
      }}
      onDelete={(id) => { if (saved) void store(withDeleted(saved.items, id), "Analysis deleted."); }} />
  );
}

export default MyAnalyses;

/** Arrow keys move between the tabs (wrapping), Home / End jump; null for any other key. */
export function nextId(ids: string[], current: string, key: string): string | null {
  const i = ids.indexOf(current);
  if (i < 0 || !ids.length) return null;
  if (key === "ArrowRight") return ids[(i + 1) % ids.length];
  if (key === "ArrowLeft") return ids[(i - 1 + ids.length) % ids.length];
  if (key === "Home") return ids[0];
  if (key === "End") return ids[ids.length - 1];
  return null;
}

/** The saved tabs and the selected one's table for a state already in hand (pure apart from the rename / delete steps). */
export function MyAnalysesView({ saved, current, onPick, response, error, note, onRename, onDelete }: {
  saved: SavedState | null; current: string | null; onPick: (id: string) => void; response: AskResponse | null;
  error: string | null; note: string | null; onRename: (id: string, name: string) => void; onDelete: (id: string) => void;
}) {
  const [renaming, setRenaming] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [confirming, setConfirming] = useState<string | null>(null);
  const ids = saved ? Object.keys(saved.items) : [];
  const onKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    const next = current ? nextId(ids, current, e.key) : null;
    if (next) { e.preventDefault(); onPick(next); }
  };
  const shown = shownResult(response);
  return (
    <section className="space-y-space-3 rounded-panel border border-subtle bg-surface-1 p-space-3 md:p-space-4" aria-label="My analyses"
      data-oc-my-analyses={saved ? ids.length : "loading"}>
      <h2 className="text-t15 font-semibold text-fg">My analyses</h2>
      {note && <p className="text-t13 text-fg-muted" role="status">{note}</p>}
      {!saved && !note && <p className="text-t14 text-fg-muted">Reading your saved analyses…</p>}
      {saved && !ids.length && (
        <p className="text-t14 text-fg-muted">No saved analyses yet. Ask AI about this chain and choose Save as My analysis.</p>
      )}
      {saved && current && (
        <>
          <div role="tablist" aria-label="My analyses" className="flex gap-space-1 overflow-x-auto rounded-panel bg-surface-2 p-space-1">
            {ids.map((id) => {
              const on = id === current;
              return (
                <button key={id} type="button" role="tab" id={`oc-my-tab-${id}`} aria-selected={on} aria-controls={`oc-my-panel-${id}`}
                  tabIndex={on ? 0 : -1} onClick={() => onPick(id)} onKeyDown={onKey}
                  className={cx("min-h-11 shrink-0 rounded-control px-space-3 text-t14 transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand",
                    on ? "bg-surface-1 font-semibold text-fg shadow-card" : "text-fg-muted hover:text-fg")}>
                  {saved.items[id].name}
                </button>
              );
            })}
          </div>
          <div role="tabpanel" id={`oc-my-panel-${current}`} aria-labelledby={`oc-my-tab-${current}`} tabIndex={0} className="space-y-space-3">
            <div className="flex flex-wrap items-center gap-space-2">
              {renaming === current ? (
                <form className="flex flex-wrap items-center gap-space-2" onSubmit={(e) => { e.preventDefault(); onRename(current, draft); setRenaming(null); }}>
                  <label className="sr-only" htmlFor="oc-my-rename">New name</label>
                  <input id="oc-my-rename" value={draft} maxLength={40} onChange={(e) => setDraft(e.target.value)}
                    className="min-h-11 rounded-control border border-border bg-surface-2 px-space-2 text-t14 text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand" />
                  <Button type="submit" className="min-h-11">Save name</Button>
                  <Button variant="ghost" className="min-h-11" onClick={() => setRenaming(null)}>Cancel</Button>
                </form>
              ) : confirming === current ? (
                <>
                  <span className="text-t13 text-fg">Delete &quot;{saved.items[current].name}&quot;?</span>
                  <Button variant="danger" className="min-h-11" onClick={() => { onDelete(current); setConfirming(null); }}>Delete</Button>
                  <Button variant="ghost" className="min-h-11" onClick={() => setConfirming(null)}>Keep it</Button>
                </>
              ) : (
                <>
                  <Button variant="ghost" className="min-h-11" onClick={() => { setDraft(saved.items[current].name); setRenaming(current); }}>Rename</Button>
                  <Button variant="ghost" className="min-h-11" onClick={() => setConfirming(current)}>Delete</Button>
                </>
              )}
            </div>
            {error && <p className="text-t14 text-fg" role="alert">{error}</p>}
            {!response && !error && <p className="text-t14 text-fg-muted">Reading the option chain…</p>}
            {response && !shown && response.message && <p className="text-t14 text-fg-muted">{response.message}</p>}
            {response && shown && <AnalysisResultView evaluated={shown} disclaimer={response.disclaimer} asOf={response.data?.data_as_of} />}
          </div>
        </>
      )}
    </section>
  );
}
