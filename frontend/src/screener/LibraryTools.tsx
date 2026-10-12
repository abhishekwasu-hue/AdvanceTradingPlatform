/**
 * U9 TX2: the screen library's tools (flag `screener_tx2`) - a confluence view and watch-then-trigger lists.
 *
 * - Confluence: pick 2-6 screens (the gallery's screens and their generated mirrors), run them over the symbol list, and
 *   see each symbol with the methods and screens that flagged it, ordered by how many methods agree. It shows agreement,
 *   never a score or a recommendation; every row lists the screens (with their text) that are its evidence.
 * - Watch -> trigger: a "ready" condition and a "trigger" condition. Triggered = the trigger on the last closed bar after
 *   a ready bar; Ready = ready now, not yet triggered. The transition is itself a screen: save it, then put an alert on it.
 *
 * - Daily digest (U9 TX3, flag `screener_tx3`): pin up to 5 screens, pick a time (India time); on trading days the
 *   first matches of each pinned screen arrive grouped by method folder, in the app and in your own Telegram chat.
 *   Preview builds it now without sending.
 *
 * Research only: nothing here places an order.
 */
import { useMemo, useState } from "react";
import { Button, cx, Tabs } from "../components/primitives";
import { screenerApi } from "./api";
import {
  agreementText, confluenceChoices, MAX_CONFLUENCE, MAX_PINNED, methodLabel, MIN_CONFLUENCE, toggleChoice,
  type ConfluenceResponse, type DigestState, type MirrorsResponse, type WatchListsResponse, type WatchResponse,
} from "./library";
import { parseSymbols } from "./model";
import type { Taxonomy } from "./taxonomy";

export interface LibraryToolsProps {
  taxonomy: Taxonomy;
  mirrors: MirrorsResponse | null;
  watchLists: WatchListsResponse | null;
  /** The scan's symbol list (one per line or comma separated), used as the starting list here. */
  symbolsText: string;
  initialTab?: "confluence" | "watch" | "digest";
  /** U9 TX3: the user's digest settings (null while `screener_tx3` is off - no digest tab). */
  digest?: DigestState | null;
  /** U9 TX2 tools are shown only with `screener_tx2` on (mirrors loaded). */
  tx2?: boolean;
  /** Stories and tests: results shown without a call. */
  initialConfluence?: ConfluenceResponse | null;
  initialWatch?: WatchResponse | null;
  api?: Pick<typeof screenerApi, "confluence" | "watchTrigger" | "save" | "saveDigest" | "previewDigest">;
}

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function LibraryTools({ taxonomy, mirrors, watchLists, symbolsText, initialTab, initialConfluence = null,
  initialWatch = null, api = screenerApi, digest = null, tx2 = true }: LibraryToolsProps) {
  const [text, setText] = useState(digest && digest.symbols.length ? digest.symbols.join(", ") : symbolsText);
  const symbols = useMemo(() => parseSymbols(text), [text]);
  const items = [
    ...(tx2 ? [
      { value: "confluence", label: "Confluence", content: <Confluence taxonomy={taxonomy} mirrors={mirrors} symbols={symbols} initial={initialConfluence} api={api} /> },
      { value: "watch", label: "Watch → trigger", content: <Watch lists={watchLists} symbols={symbols} initial={initialWatch} api={api} /> },
    ] : []),
    ...(digest ? [{ value: "digest", label: "Daily digest", content: <DigestTab taxonomy={taxonomy} mirrors={mirrors} symbols={symbols} state={digest} api={api} /> }] : []),
  ];
  const [tab, setTab] = useState<string>(initialTab ?? items[0]?.value ?? "confluence");
  return (
    <div data-testid="library-tools">
      <label className="block text-t12 font-medium text-fg-muted" htmlFor="library-symbols">Symbols ({symbols.length})</label>
      <textarea id="library-symbols" value={text} onChange={(e) => setText(e.target.value)} rows={2}
                className="mt-1 w-full rounded-control border border-border bg-surface-1 p-2 font-mono text-t13 text-fg" />
      <Tabs className="mt-3" value={tab} onChange={setTab} items={items} />
    </div>
  );
}

function DigestTab({ taxonomy, mirrors, symbols, state, api }: {
  taxonomy: Taxonomy; mirrors: MirrorsResponse | null; symbols: string[]; state: DigestState; api: NonNullable<LibraryToolsProps["api"]>;
}) {
  const choices = useMemo(() => confluenceChoices(taxonomy, mirrors), [taxonomy, mirrors]);
  const [enabled, setEnabled] = useState(state.enabled);
  const [time, setTime] = useState(state.send_time);
  const [pinned, setPinned] = useState<string[]>(state.pinned);
  const [status, setStatus] = useState<string | null>(state.last_status);
  const [shown, setShown] = useState<string[] | null>(state.messages ?? null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const body = () => ({ enabled, send_time: time, pinned, symbols, exchange: state.exchange || "NSE" });
  const act = async (what: "save" | "preview") => {
    setBusy(true);
    setError(null);
    try {
      if (what === "save") { await api.saveDigest(body()); setStatus("Saved. It runs on trading days at or after the time you chose."); }
      else { setShown((await api.previewDigest(body())).messages); }
    } catch (e) { setError(message(e)); } finally { setBusy(false); }
  };
  const full = pinned.length >= MAX_PINNED;
  return (
    <div data-testid="digest-tab">
      <p className="text-t12 text-fg-muted">{state.note}</p>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <label className="flex min-h-[44px] items-center gap-2 text-t13 text-fg">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Send me the digest on trading days
        </label>
        <label className="flex items-center gap-2 text-t13 text-fg">At (India time)
          <input type="time" value={time} onChange={(e) => setTime(e.target.value)} className="min-h-[44px] rounded-control border border-border bg-surface-1 px-2 text-t13 text-fg" />
        </label>
      </div>
      <fieldset className="mt-2">
        <legend className="text-t12 text-fg-muted">Pin up to {MAX_PINNED} screens ({pinned.length} pinned)</legend>
        <ul className="mt-1 grid max-h-40 gap-1 overflow-y-auto sm:grid-cols-2" data-testid="digest-choices">
          {choices.map((c) => {
            const on = pinned.includes(c.ref);
            return (
              <li key={c.ref}>
                <label className={cx("flex min-h-[44px] items-center gap-2 rounded-control border px-2 text-t13", on ? "border-brand text-fg" : "border-border text-fg-muted")}>
                  <input type="checkbox" checked={on} disabled={!on && full} onChange={() => setPinned((p) => toggleChoice(p, c.ref, MAX_PINNED))} />
                  <span>{c.label}<span className="ml-1 text-t12 text-fg-muted">· {methodLabel(taxonomy, c.method)}</span></span>
                </label>
              </li>
            );
          })}
        </ul>
      </fieldset>
      <div className="mt-2 flex gap-2">
        <Button size="sm" variant="primary" onClick={() => void act("save")} loading={busy} disabled={enabled && (!pinned.length || !symbols.length)}>Save</Button>
        <Button size="sm" onClick={() => void act("preview")} disabled={busy || !pinned.length || !symbols.length}>Preview now</Button>
      </div>
      {error && <p role="alert" className="mt-2 text-t12 text-warn">{error}</p>}
      {status && <p role="status" className="mt-2 text-t12 text-fg">{status}{state.last_sent_on ? ` (last: ${state.last_sent_on})` : ""}</p>}
      {shown && (
        <ul className="mt-2 space-y-2" aria-label="Digest messages" data-testid="digest-messages">
          {shown.map((m, i) => <li key={i} className="whitespace-pre-wrap rounded-panel border border-border bg-surface-1 p-2 text-t13 text-fg">{m}</li>)}
        </ul>
      )}
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
          <p className="text-t12 text-fg-muted">{result.note}</p>
          {result.rows.length === 0 ? (
            <p className="mt-2 text-t13 text-fg">No symbol was flagged by any of these screens on the data shown.</p>
          ) : (
            <table className="mt-2 w-full border-collapse text-t13" data-testid="confluence-table">
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
          )}
          {result.unflagged.length > 0 && <p className="mt-2 text-t12 text-fg-muted">Not flagged: {result.unflagged.join(", ")}</p>}
          <p className="mt-2 text-t12 text-fg-muted">{result.disclaimer}</p>
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
      <select id="watch-list" value={listId} onChange={(e) => setListId(e.target.value)}
              className="mt-1 min-h-[44px] w-full rounded-control border border-border bg-surface-1 px-2 text-t13 text-fg">
        {(lists?.lists ?? []).map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
        <option value="custom">Your own ready and trigger conditions</option>
      </select>
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
            <p className="text-t12 text-fg-muted">{result.note}</p>
            <p className="mt-1 break-words font-mono text-t12 text-fg">{result.transition_text}</p>
            <Button size="sm" className="mt-2" onClick={() => void save()}>Save the trigger screen</Button>
            {saved && <p role="status" className="mt-1 text-t12 text-fg">{saved}</p>}
            <p className="mt-2 text-t12 text-fg-muted">{result.disclaimer}</p>
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
