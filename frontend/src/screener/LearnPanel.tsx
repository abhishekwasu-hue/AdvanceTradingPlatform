/**
 * U9 TX4: learn mode for one library screen (flag `screener_tx4`) - its method and setup, its provenance (version,
 * change log, source note, the exact ScreenQL and its SHA-256), three past examples on the shared end-of-day history
 * with each one's evidence chart (as of that bar) and what the close did after it, and "Fork to My screens".
 *
 * Education only: three examples are illustrations, never a statistic, a forecast or advice - the panel says so.
 */
import { useMemo, useState } from "react";
import { Button } from "../components/primitives";
import { screenerApi } from "./api";
import { EvidenceChart } from "./EvidenceChart";
import { visibleDrawables } from "./evidence";
import { changeText, MAX_LEARN_SYMBOLS, type ExamplesResponse, type LearnExample, type LearnResponse } from "./library";
import { parseSymbols } from "./model";

export interface LearnPanelProps {
  learn: LearnResponse;
  onBack: () => void;
  /** Stories and tests: examples shown without a call. */
  initialExamples?: ExamplesResponse | null;
  canFork?: boolean;
  api?: Pick<typeof screenerApi, "examples" | "fork">;
}

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function LearnPanel({ learn, onBack, initialExamples = null, canFork = true, api = screenerApi }: LearnPanelProps) {
  const [text, setText] = useState("RELIANCE, TCS, INFY, HDFCBANK, ICICIBANK");
  const symbols = useMemo(() => parseSymbols(text, MAX_LEARN_SYMBOLS), [text]);
  const [result, setResult] = useState<ExamplesResponse | null>(initialExamples);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [forked, setForked] = useState<string | null>(null);
  const p = learn.provenance;
  const load = async () => {
    setBusy(true);
    setError(null);
    try { setResult(await api.examples(learn.id, symbols)); } catch (e) { setError(message(e)); } finally { setBusy(false); }
  };
  const fork = async () => {
    try {
      const row = await api.fork(learn.id);
      setForked(`Saved to My screens as "${row.name}". It is yours to edit; later versions of this screen do not change it.`);
    } catch (e) { setError(message(e)); }
  };
  return (
    <article className="rounded-panel border border-border bg-surface-1 p-3" aria-labelledby="learn-title" data-testid="learn-panel">
      <div className="flex items-center justify-between gap-2">
        <h3 id="learn-title" className="text-t15 font-semibold text-fg">{learn.name} <span className="font-mono text-t12 text-fg-muted">v{p.version}</span></h3>
        <Button size="sm" variant="ghost" onClick={onBack}>Back to the screens</Button>
      </div>
      <p className="mt-1 text-t12 text-fg-muted">{learn.note}</p>

      <section aria-label="The method" className="mt-3">
        <h4 className="text-t13 font-semibold text-fg">The method</h4>
        {learn.method && <p className="mt-1 text-t13 text-fg">{learn.method.label}: <span className="text-fg-muted">{learn.method.definition}</span></p>}
        {learn.setup && <p className="text-t13 text-fg">{learn.setup.label}: <span className="text-fg-muted">{learn.setup.definition}</span></p>}
        {learn.page ? <a className="text-t12 text-brand underline" href={learn.page}>Read the method page</a> : <p className="text-t12 text-fg-muted">{learn.page_note}</p>}
      </section>

      <section aria-label="Provenance" className="mt-3" data-testid="learn-provenance">
        <h4 className="text-t13 font-semibold text-fg">Version and source</h4>
        <ul className="mt-1 text-t12 text-fg">
          {p.changes.map((c) => <li key={c.version}><span className="font-mono">v{c.version}</span> · {c.note}</li>)}
        </ul>
        <p className="mt-1 text-t12 text-fg-muted">{p.source_note}</p>
        {p.text && <p className="mt-1 break-words font-mono text-t12 text-fg">{p.text}</p>}
        {p.text_sha256 && <p className="font-mono text-t12 text-fg-muted" title="SHA-256 of the screen's text">sha256 {p.text_sha256.slice(0, 16)}…</p>}
        {canFork && <Button size="sm" className="mt-2" onClick={() => void fork()}>Fork to My screens</Button>}
        {forked && <p role="status" className="mt-1 text-t12 text-fg">{forked}</p>}
      </section>

      <section aria-label="Past examples" className="mt-3">
        <h4 className="text-t13 font-semibold text-fg">Past examples</h4>
        <label className="mt-1 block text-t12 text-fg-muted" htmlFor="learn-symbols">Symbols to look in (up to {MAX_LEARN_SYMBOLS}, end-of-day history)</label>
        <input id="learn-symbols" value={text} onChange={(e) => setText(e.target.value)}
               className="mt-1 min-h-[44px] w-full rounded-control border border-border bg-surface-1 px-2 font-mono text-t13 text-fg" />
        <Button size="sm" className="mt-2" onClick={() => void load()} loading={busy} disabled={symbols.length === 0}>Show past examples</Button>
        {error && <p role="alert" className="mt-2 text-t12 text-warn">{error}</p>}
        {result && (
          <div className="mt-2" aria-live="polite" data-testid="learn-examples">
            {result.examples.length === 0
              ? <p className="text-t13 text-fg">No complete past example in these symbols' shared history.</p>
              : <ul className="flex flex-col gap-3">{result.examples.map((ex) => <Example key={`${ex.symbol}-${ex.date}`} ex={ex} after={result.after_sessions ?? [5, 10, 20]} />)}</ul>}
            {result.problems.length > 0 && <p className="mt-2 text-t12 text-fg-muted">Not used: {result.problems.slice(0, 5).join("; ")}</p>}
            <p className="mt-2 text-t12 text-fg-muted">{result.note}</p>
          </div>
        )}
      </section>
    </article>
  );
}

function Example({ ex, after }: { ex: LearnExample; after: number[] }) {
  const drawables = useMemo(() => (ex.pack ? visibleDrawables(ex.pack, new Set(), false) : []), [ex.pack]);
  return (
    <li className="rounded-panel border border-border p-2" data-example={`${ex.symbol}-${ex.date}`}>
      <p className="text-t13 text-fg"><span className="font-mono font-semibold">{ex.symbol}</span> · matched on {ex.date} · close {ex.close.toFixed(2)}</p>
      <p className="text-t12 text-fg-muted">
        What the close did after:{" "}
        {after.map((n, i) => <span key={n}>{i > 0 ? " · " : ""}{n} sessions <span className="font-mono text-fg">{changeText(ex.after[String(n)])}</span></span>)}
      </p>
      {ex.pack && <div className="mt-2"><EvidenceChart pack={ex.pack} drawables={drawables} width={640} height={280} /></div>}
    </li>
  );
}
