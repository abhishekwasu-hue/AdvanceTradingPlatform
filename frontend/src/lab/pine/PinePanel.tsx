/**
 * Handover 11c-1 (flag `strategy_lab_pine`): the Pine Script panel on the Screener. Paste a TradingView Pine script (or
 * start from a template), see line by line what translates and what does not (with the closest supported
 * alternative), read each signal condition in plain words, and run one as a screen on the symbols in the universe.
 *
 * The script is translated on the server and never run. Signal conditions only: lots, stops, targets and exits stay the
 * trader's own settings. Lazy-loaded by the Screener page, so it costs nothing while the flag is off.
 */
import { useEffect, useRef, useState } from "react";
import { ApiError, friendlyError } from "../../api/errors";
import { Button, cx } from "../../components/primitives";
import {
  inputWords, KIND_LABEL, lineNumbers, lineRange, pineApi, SEVERITY_CLASS, SEVERITY_LABEL, sortDiagnostics, statusLine,
  type PineApi, type PineRunResponse, type PineTemplate, type PineTranslation,
} from "./pine";

export interface PinePanelProps {
  symbols: string[];
  baseTf: string;
  api?: PineApi;
  initialSource?: string;
  initialResult?: PineTranslation | null;
  initialTemplates?: PineTemplate[];
}

function errorText(e: unknown): string {
  if (e instanceof ApiError && e.status === 404) return "Pine Script is not turned on for this organisation yet.";
  if (e instanceof ApiError && e.status === 409) return "Connect a broker on the Broker page - the screen reads its bars from there.";
  return friendlyError(e);
}

export default function PinePanel({ symbols, baseTf, api = pineApi, initialSource = "", initialResult = null, initialTemplates }: PinePanelProps) {
  const [source, setSource] = useState(initialSource);
  const [templates, setTemplates] = useState<PineTemplate[]>(initialTemplates ?? []);
  const [result, setResult] = useState<PineTranslation | null>(initialResult);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState<number | null>(null);
  const [run, setRun] = useState<PineRunResponse | null>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const gutterRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (initialTemplates) return;
    api.templates().then((t) => setTemplates(t.templates)).catch(() => setTemplates([]));
  }, [api, initialTemplates]);

  const doTranslate = (text = source) => {
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    setRun(null);
    api.translate(text, baseTf).then(setResult).catch((e: unknown) => setError(errorText(e))).finally(() => setBusy(false));
  };
  const pickTemplate = (id: string) => {
    const t = templates.find((x) => x.id === id);
    if (!t) return;
    setSource(t.source);
    doTranslate(t.source);
  };
  const showLine = (line: number) => {
    const el = textRef.current;
    if (!el) return;
    const [start, end] = lineRange(source, line);
    el.focus();
    el.setSelectionRange(start, end);
  };
  const doRun = (index: number) => {
    setRunning(index);
    setError(null);
    api.run(source, index, baseTf, symbols).then(setRun).catch((e: unknown) => setError(errorText(e))).finally(() => setRunning(null));
  };

  const numbers = lineNumbers(source);
  const diagnostics = result ? sortDiagnostics(result.diagnostics) : [];
  return (
    <div data-testid="pine-panel" className="space-y-4 text-t13">
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col text-t12 text-fg-muted">
          Start from a template
          <select value="" onChange={(e) => pickTemplate(e.target.value)} data-testid="pine-template"
                  className="min-h-[44px] rounded-control border border-border bg-surface-1 px-2 text-t13 text-fg">
            <option value="">Choose…</option>
            {templates.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        </label>
        <Button variant="primary" onClick={() => doTranslate()} loading={busy} disabled={!source.trim()} data-testid="pine-translate">Translate</Button>
      </div>

      <div className="flex overflow-hidden rounded-control border border-border bg-surface-inset">
        <pre ref={gutterRef} aria-hidden className="m-0 select-none overflow-hidden border-r border-border-subtle px-2 py-2 text-right font-mono text-t12 leading-5 text-fg-muted"
             data-testid="pine-gutter">{numbers.join("\n")}</pre>
        <textarea ref={textRef} value={source} onChange={(e) => setSource(e.target.value)} spellCheck={false} wrap="off" rows={14}
                  aria-label="Your Pine script" placeholder={'//@version=5\nindicator("My signal")\n...'}
                  onScroll={(e) => { if (gutterRef.current) gutterRef.current.scrollTop = e.currentTarget.scrollTop; }}
                  className="min-h-[14rem] w-full resize-y bg-transparent px-2 py-2 font-mono text-t12 leading-5 text-fg outline-none" />
      </div>

      {error && <p role="alert" className="text-t12 text-warn">{error}</p>}

      {result && (
        <div className="space-y-3" aria-live="polite">
          <p className={cx("text-t13", result.refused || !result.ok ? "text-warn" : "text-fg")} data-testid="pine-status">{statusLine(result)}</p>

          {result.signals.length > 0 && (
            <ul className="space-y-2" data-testid="pine-signals">
              {result.signals.map((s) => (
                <li key={s.index} className="rounded-panel border border-border bg-surface-1 p-3">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <p className="font-semibold text-fg">{KIND_LABEL[s.kind]} <span className="font-normal text-fg-muted">· line {s.line}{s.label.startsWith(KIND_LABEL[s.kind]) ? "" : ` · ${s.label}`}</span></p>
                    {s.ok && (
                      <Button size="sm" onClick={() => doRun(s.index)} loading={running === s.index} disabled={!symbols.length || running !== null}
                              data-testid={`pine-run-${s.index}`}>Run as screen</Button>
                    )}
                  </div>
                  {s.ok ? (
                    <>
                      <p className="mt-1 text-fg">{s.words}</p>
                      {inputWords(result, s).length > 0 && <p className="mt-1 text-t12 text-fg-muted">Inputs: {inputWords(result, s).join(", ")}</p>}
                      <details className="mt-1 text-t12 text-fg-muted">
                        <summary className="cursor-pointer">Advanced: the screen condition</summary>
                        <code className="mt-1 block whitespace-pre-wrap break-all font-mono text-fg">{s.text}</code>
                      </details>
                    </>
                  ) : (
                    <p className="mt-1 text-t12 text-warn">{s.problems[0] ?? "This condition did not translate."}</p>
                  )}
                </li>
              ))}
            </ul>
          )}
          {!symbols.length && result.signals.some((s) => s.ok) && (
            <p className="text-t12 text-fg-muted">Add symbols in the universe column to run a condition as a screen.</p>
          )}

          {run && (
            <div className="rounded-panel border border-border bg-surface-1 p-3" data-testid="pine-run-result">
              <p className="text-fg">Matched <span className="font-mono font-semibold text-signal">{run.matched.length}</span> of {run.scanned}
                {run.matched.length > 0 && <>: <span className="font-mono">{run.matched.join(", ")}</span></>}</p>
              <p className="mt-1 text-t12 text-fg-muted">{run.disclaimer}</p>
            </div>
          )}

          {diagnostics.length > 0 && (
            <div>
              <p className="text-t12 font-semibold text-fg-muted">Line by line</p>
              <ul className="mt-1 space-y-1" data-testid="pine-diagnostics">
                {diagnostics.map((d, i) => (
                  <li key={i} className="flex flex-wrap items-baseline gap-2">
                    <button type="button" onClick={() => showLine(d.line)} className="min-h-[24px] font-mono text-t12 text-brand underline-offset-2 hover:underline">
                      Line {d.line}
                    </button>
                    <span className={cx("rounded border px-1.5 text-t12", SEVERITY_CLASS[d.severity])}>{SEVERITY_LABEL[d.severity]}</span>
                    <span className="text-fg">{d.message}</span>
                    {d.alternative && <span className="text-t12 text-fg-muted">Try: {d.alternative}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <p className="text-t12 text-fg-muted">{result.note}</p>
        </div>
      )}
    </div>
  );
}
