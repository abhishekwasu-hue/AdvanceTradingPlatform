/**
 * U9 TX2: the screen library's tools (flag `screener_tx2`) - a confluence view and watch-then-trigger lists.
 *
 * - Confluence: pick 2-6 screens (the gallery's screens and their generated mirrors), run them over the symbol list, and
 *   see each symbol with the methods and screens that flagged it, ordered by how many methods agree. It shows agreement,
 *   never a score or a recommendation; every row lists the screens (with their text) that are its evidence.
 * - Watch -> trigger: a "ready" condition and a "trigger" condition. Triggered = the trigger on the last closed bar after
 *   a ready bar; Ready = ready now, not yet triggered. The transition is itself a screen: save it, then put an alert on it.
 *
 * Research only: nothing here places an order.
 */
import { useMemo, useState } from "react";
import { Button, cx, Tabs } from "../components/primitives";
import { Combobox } from "../ui/Combobox";
import { Details } from "../ui/Details";
import { PageFootnote } from "../ui/PageFootnote";
import { screenerApi } from "./api";
import {
  agreementText, confluenceChoices, MAX_CONFLUENCE, methodLabel, MIN_CONFLUENCE, toggleChoice,
  type ConfluenceResponse, type MirrorsResponse, type WatchListsResponse, type WatchResponse,
} from "./library";
import { parseSymbols } from "./model";
import type { Taxonomy } from "./taxonomy";

export interface LibraryToolsProps {
  taxonomy: Taxonomy;
  mirrors: MirrorsResponse | null;
  watchLists: WatchListsResponse | null;
  /** The scan's symbol list (one per line or comma separated), used as the starting list here. */
  symbolsText: string;
  initialTab?: "confluence" | "watch";
  /** Stories and tests: results shown without a call. */
  initialConfluence?: ConfluenceResponse | null;
  initialWatch?: WatchResponse | null;
  api?: Pick<typeof screenerApi, "confluence" | "watchTrigger" | "save">;
}

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function LibraryTools({ taxonomy, mirrors, watchLists, symbolsText, initialTab = "confluence", initialConfluence = null,
  initialWatch = null, api = screenerApi }: LibraryToolsProps) {
  const [tab, setTab] = useState<string>(initialTab);
  const [text, setText] = useState(symbolsText);
  const symbols = useMemo(() => parseSymbols(text), [text]);
  return (
    <div data-testid="library-tools">
      <label className="block text-t12 font-medium text-fg-muted" htmlFor="library-symbols">Symbols ({symbols.length})</label>
      <textarea id="library-symbols" value={text} onChange={(e) => setText(e.target.value)} rows={2}
                className="mt-1 w-full rounded-control border border-border bg-surface-1 p-2 font-mono text-t13 text-fg" />
      <Tabs className="mt-3" value={tab} onChange={setTab} items={[
        { value: "confluence", label: "Confluence", content: <Confluence taxonomy={taxonomy} mirrors={mirrors} symbols={symbols} initial={initialConfluence} api={api} /> },
        { value: "watch", label: "Watch → trigger", content: <Watch lists={watchLists} symbols={symbols} initial={initialWatch} api={api} /> },
      ]} />
      {/* SCR-FIX E1: the dialog's one "not advice" line */}
      <div className="mt-space-4"><PageFootnote /></div>
    </div>
  );
}

function Confluence({ taxonomy, mirrors, symbols, initial, api }: {
  taxonomy: Taxonomy; mirrors: MirrorsResponse | null; symbols: string[]; initial: ConfluenceResponse | null;
  api: NonNullable<LibraryToolsProps["api"]>;
}) {
  const choices = useMemo(() => confluenceChoices(taxonomy, mirrors), [taxonomy, mirrors]);
  const [picked, setPicked] = useState<string[]>(initial ? initial.screens.map((s) => s.ref) : []);
  const [result, setResult] = useState<ConfluenceResponse | null>(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const full = picked.length >= MAX_CONFLUENCE;
  const canRun = picked.length >= MIN_CONFLUENCE && symbols.length > 0 && !busy;
  const run = async () => {
    setBusy(true);
    setError(null);
    try { setResult(await api.confluence(picked, symbols)); } catch (e) { setError(message(e)); } finally { setBusy(false); }
  };
  return (
    <div>
      <fieldset>
        <legend className="text-t12 text-fg-muted">Pick {MIN_CONFLUENCE} to {MAX_CONFLUENCE} screens ({picked.length} picked)</legend>
        <ul className="mt-1 grid max-h-48 gap-1 overflow-y-auto sm:grid-cols-2" data-testid="confluence-choices">
          {choices.map((c) => {
            const on = picked.includes(c.ref);
            return (
              <li key={c.ref}>
                <label className={cx("flex min-h-[44px] items-center gap-2 rounded-control border px-2 text-t13",
                                     on ? "border-brand text-fg" : "border-border text-fg-muted")}>
                  <input type="checkbox" checked={on} disabled={!on && full} onChange={() => setPicked((p) => toggleChoice(p, c.ref))} />
                  <span>{c.label}<span className="ml-1 text-t12 text-fg-muted">· {methodLabel(taxonomy, c.method)}</span></span>
                </label>
              </li>
            );
          })}
        </ul>
      </fieldset>
      <div className="mt-2 flex items-center gap-2">
        <Button size="sm" variant="primary" onClick={() => void run()} disabled={!canRun} loading={busy}>Run confluence</Button>
        {symbols.length === 0 && <span className="text-t12 text-fg-muted">Add symbols above.</span>}
      </div>
      {error && <p role="alert" className="mt-2 text-t12 text-warn">{error}</p>}
      {result && (
        <div className="mt-3" aria-live="polite">
          <Details id="screener.confluence.about" className="mt-0">
            <p>{result.note}</p>
            <p>{result.disclaimer}</p>
          </Details>
          {result.rows.length === 0 ? (
            <p className="mt-2 text-t13 text-fg">No symbol was flagged by any of these screens on the data shown.</p>
          ) : (
            // SCR-FIX D9: a wide table scrolls inside its own box on a phone, never the page
            <div className="mt-2 overflow-x-auto" data-testid="confluence-scroll">
            <table className="w-full min-w-[32rem] border-collapse text-t13" data-testid="confluence-table">
              <caption className="sr-only">Symbols by how many methods and screens flagged them</caption>
              <thead>
                <tr className="text-left text-t12 text-fg-muted">
                  <th scope="col" className="py-1 pr-2 font-medium">Symbol</th>
                  <th scope="col" className="py-1 pr-2 font-medium">Agreement</th>
                  <th scope="col" className="py-1 font-medium">Flagged by (the evidence)</th>
                </tr>
              </thead>
              <tbody>
                {result.rows.map((r) => (
                  <tr key={r.symbol} className="border-t border-border align-top" data-symbol={r.symbol}>
                    <th scope="row" className="py-1 pr-2 text-left font-mono font-semibold text-fg">{r.symbol}</th>
                    <td className="py-1 pr-2 text-fg">{agreementText(r)}
                      <div className="text-t12 text-fg-muted">{r.methods.map((m) => methodLabel(taxonomy, m)).join(", ")}</div></td>
                    <td className="py-1">
                      <ul>{r.screens.map((s) => <li key={s.ref} title={s.text}>{s.name} <span className="text-t12 text-fg-muted">({methodLabel(taxonomy, s.method)})</span></li>)}</ul>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          )}
          {result.unflagged.length > 0 && <p className="mt-2 text-t12 text-fg-muted">Not flagged: {result.unflagged.join(", ")}</p>}
        </div>
      )}
    </div>
  );
}

function Watch({ lists, symbols, initial, api }: {
  lists: WatchListsResponse | null; symbols: string[]; initial: WatchResponse | null; api: NonNullable<LibraryToolsProps["api"]>;
}) {
  const [listId, setListId] = useState<string>(lists?.lists[0]?.id ?? "custom");
  const [ready, setReady] = useState("");
  const [trigger, setTrigger] = useState("");
  const [result, setResult] = useState<WatchResponse | null>(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const custom = listId === "custom";
  const chosen = lists?.lists.find((l) => l.id === listId);
  const canRun = symbols.length > 0 && !busy && (!custom || (ready.trim() !== "" && trigger.trim() !== ""));
  const run = async () => {
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      setResult(await api.watchTrigger(custom ? { ready, trigger, base_tf: "1d", symbols } : { list_id: listId, symbols }));
    } catch (e) { setError(message(e)); } finally { setBusy(false); }
  };
  const save = async () => {
    if (!result) return;
    try {
      const row = await api.save(`${result.name} - triggered`, result.transition_text, result.base_tf);
      setSaved(`Saved as "${row.name}". Add an alert on it to hear about the next transition.`);
    } catch (e) { setError(message(e)); }
  };
  return (
    <div>
      <label className="block text-t12 font-medium text-fg-muted" htmlFor="watch-list">List</label>
      {/* SCR-FIX D8: the shared picker, not the browser's select */}
      <Combobox id="watch-list" label="List" value={listId} onChange={setListId} className="mt-1 w-full"
                options={[...(lists?.lists ?? []).map((l) => ({ value: l.id, label: l.name })), { value: "custom", label: "Your own ready and trigger conditions" }]} />
      {chosen && !custom && <p className="mt-1 text-t12 text-fg-muted">{chosen.definition}</p>}
      {custom && (
        <div className="mt-2 grid gap-2">
          <label className="text-t12 text-fg-muted">Ready (ScreenQL)
            <input value={ready} onChange={(e) => setReady(e.target.value)} className="mt-1 w-full rounded-control border border-border bg-surface-1 p-2 font-mono text-t13 text-fg" />
          </label>
          <label className="text-t12 text-fg-muted">Trigger (ScreenQL)
            <input value={trigger} onChange={(e) => setTrigger(e.target.value)} className="mt-1 w-full rounded-control border border-border bg-surface-1 p-2 font-mono text-t13 text-fg" />
          </label>
        </div>
      )}
      <div className="mt-2"><Button size="sm" variant="primary" onClick={() => void run()} disabled={!canRun} loading={busy}>Run the lists</Button></div>
      {error && <p role="alert" className="mt-2 text-t12 text-warn">{error}</p>}
      {result && (
        <div className="mt-3 grid gap-3 sm:grid-cols-2" aria-live="polite" data-testid="watch-results">
          <WatchColumn title="Triggered" symbols={result.triggered} empty="Nothing triggered on the last closed bar." />
          <WatchColumn title="Ready" symbols={result.ready} empty="Nothing is ready." />
          <div className="sm:col-span-2">
            <p className="break-words font-mono text-t12 text-fg">{result.transition_text}</p>
            <Button size="sm" className="mt-2" onClick={() => void save()}>Save the trigger screen</Button>
            {saved && <p role="status" className="mt-1 text-t12 text-fg">{saved}</p>}
            <Details id="screener.watch.about">
              <p>{result.note}</p>
              <p>{result.disclaimer}</p>
            </Details>
          </div>
        </div>
      )}
    </div>
  );
}

function WatchColumn({ title, symbols, empty }: { title: string; symbols: string[]; empty: string }) {
  return (
    <section aria-label={title} className="rounded-panel border border-border bg-surface-1 p-2">
      <h4 className="text-t13 font-semibold text-fg">{title} <span className="font-mono text-fg-muted">({symbols.length})</span></h4>
      {symbols.length === 0 ? <p className="mt-1 text-t12 text-fg-muted">{empty}</p>
        : <ul className="mt-1 flex flex-wrap gap-1">{symbols.map((s) => <li key={s} className="rounded-control border border-border px-1.5 font-mono text-t12 text-fg">{s}</li>)}</ul>}
    </section>
  );
}
