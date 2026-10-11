/**
 * U5 D1: the Screener - a precision instrument, not a form. Three columns on a desktop (Universe 280 / the funnel /
 * Results 380); on a tablet the canvas leads and the universe folds above it; on a phone everything stacks and a sticky
 * bar keeps the live count and Run in reach. The funnel carries the page's character; everything else stays quiet.
 *
 * Research only: a scan lists symbols that meet the trader's own conditions - never a recommendation, never an order.
 */
import { Play, Save } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../api/errors";
import { Button, cx } from "../components/primitives";
import { useToast } from "../components/Toast";
import type { PageProps } from "../routes";
import { screenerApi, type RunResponse } from "../screener/api";
import { FreshnessPill } from "../screener/FreshnessPill";
import { FunnelCanvas } from "../screener/FunnelCanvas";
import { parseSymbols, runStamp, sameRun, screenText, stageAt, TIMEFRAMES, trail, whyChips, type Registry, type RunStamp, type Stage } from "../screener/model";
import { ResultBoard } from "../screener/ResultBoard";
import "../screener/screener.css";

const VALIDATE_DELAY_MS = 350;
/** D2 live counts: a preview run (not stored) once the trader pauses editing, on bars the server caches per symbol. */
const PREVIEW_DELAY_MS = 900;

function errorText(e: unknown): string {
  if (e instanceof ApiError && e.status === 404) return "The screener is not turned on for this organisation yet.";
  if (e instanceof ApiError && e.status === 409) return "Connect a broker under Settings > Brokers - the scan reads its bars from there.";
  return e instanceof Error ? e.message : String(e);
}

export default function ScreenerPage(_props: PageProps) {
  const toast = useToast();
  const [registry, setRegistry] = useState<Registry | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [name, setName] = useState("Untitled scan");
  const [scanTf, setScanTf] = useState("1d");
  const [symbolsText, setSymbolsText] = useState("");
  const [stages, setStages] = useState<Stage[]>([]);
  const [problems, setProblems] = useState<Record<string, string>>({});
  const [otherProblem, setOtherProblem] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [run, setRun] = useState<RunResponse | null>(null);
  const [ran, setRan] = useState<RunStamp | null>(null);         // what the shown run was made of
  const [validating, setValidating] = useState(false);
  const [live, setLive] = useState(true);
  const [preview, setPreview] = useState<{ res: RunResponse; stamp: RunStamp } | null>(null);
  const previewSeq = useRef(0);
  const [ranAt, setRanAt] = useState<Date | null>(null);
  const [runKey, setRunKey] = useState(0);
  const [savedId, setSavedId] = useState<number | undefined>(undefined);
  const validateSeq = useRef(0);

  useEffect(() => {
    screenerApi.registry().then((r) => setRegistry(r.entries)).catch((e: unknown) => setLoadError(errorText(e)));
  }, []);

  const symbols = useMemo(() => parseSymbols(symbolsText), [symbolsText]);
  const text = useMemo(() => (registry ? screenText(registry, stages) : ""), [registry, stages]);

  // Every edit is checked on the server (debounced); a problem lands on the stage it is about. Run waits for the check
  // of the current text (`validating`), so a scan is never run on the previous text's verdict.
  useEffect(() => {
    const seq = ++validateSeq.current;                // also invalidates a check in flight for an older text
    if (!registry || !text) { setProblems({}); setOtherProblem(null); setValidating(false); return undefined; }
    setValidating(true);
    const timer = window.setTimeout(() => {
      screenerApi.validate(text, scanTf).then((v) => {
        if (seq !== validateSeq.current) return;
        const byStage: Record<string, string> = {};
        let other: string | null = null;
        for (const p of v.problems) {
          const id = stageAt(registry, stages, p.pos);
          if (id && !byStage[id]) byStage[id] = p.message;
          else if (!id && !other) other = p.message;
        }
        setProblems(byStage);
        setOtherProblem(other);
      }).catch(() => undefined)                        // a failed check is not a problem in the scan; Run reports errors
        .finally(() => { if (seq === validateSeq.current) setValidating(false); });
    }, VALIDATE_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [registry, text, scanTf, stages]);

  const canRun = !!registry && !!text && symbols.length > 0 && !validating && Object.keys(problems).length === 0 && !otherProblem && !running;
  const now = useMemo(() => (registry ? runStamp(registry, stages, scanTf, symbols) : null), [registry, stages, scanTf, symbols]);
  const fresh = !!now && sameRun(ran, now);
  const liveFresh = !fresh && !!now && !!preview && sameRun(preview.stamp, now);

  // Live counts while editing: after the check passes and the trader pauses, a preview run fills the trails.
  const canPreview = live && !!registry && !!text && symbols.length > 0 && !validating && Object.keys(problems).length === 0 && !otherProblem && !running && !fresh;
  useEffect(() => {
    const seq = ++previewSeq.current;
    if (!canPreview || !now || (preview && sameRun(preview.stamp, now))) return undefined;
    const stamp = now;
    const timer = window.setTimeout(() => {
      screenerApi.preview(text, scanTf, symbols).then((res) => {
        if (seq === previewSeq.current) setPreview({ res, stamp });
      }).catch(() => undefined);                       // a live count is a convenience; Run reports errors
    }, PREVIEW_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [canPreview, now, preview, text, scanTf, symbols]);

  const doRun = useCallback(async () => {
    if (!registry || !canRun || !now) return;
    setRunning(true);
    const stamp = now;                                 // the scan as sent, not as it may be by the time the reply lands
    try {
      const out = await screenerApi.run(text, scanTf, symbols);
      setRun(out);
      setRan(stamp);
      setRanAt(new Date());
      setRunKey((k) => k + 1);
    } catch (e) {
      toast.error(errorText(e));
    } finally {
      setRunning(false);
    }
  }, [registry, canRun, now, text, scanTf, symbols, toast]);

  const showStage = (id: string) => {
    const handle = document.querySelector<HTMLElement>(`[data-stage-id="${id}"] button`);
    handle?.scrollIntoView({ block: "center", behavior: "smooth" });
    handle?.focus({ preventScroll: true });
  };

  const doSave = async () => {
    if (!text) return;
    try {
      const saved = await screenerApi.save(name.trim() || "Untitled scan", text, scanTf, savedId);
      setSavedId(saved.id);
      toast.success(`Saved "${saved.name}".`);
    } catch (e) {
      toast.error(errorText(e));
    }
  };

  // the counts shown: the run's while the scan is the one that ran, else a live preview's for the current scan
  const shown = fresh ? run : liveFresh ? preview?.res ?? null : null;
  const rows = trail(stages, shown?.funnel ?? null, !!shown);
  const killer = rows.find((r) => r.kills);
  const matchedNow = shown ? shown.matched.length : null;

  if (loadError) {
    return <div className="rounded-panel border border-border bg-surface-1 p-4 text-t13 text-fg-muted">{loadError}</div>;
  }

  return (
    <div className="screener-page pb-20 sm:pb-0">
      {/* Header strip: name (edit in place), freshness, Run (primary) / Save */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <label className="min-w-0 flex-1">
          <span className="sr-only">Scan name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={120}
                 className="w-full max-w-md rounded-control border border-transparent bg-transparent px-1 text-t24 font-semibold text-fg hover:border-border focus:border-border focus:outline-none focus:ring-2 focus:ring-brand" />
        </label>
        <FreshnessPill ranAt={ranAt} source={run?.data_source ?? null} scanTf={scanTf} />
        <div className="hidden items-center gap-2 sm:flex">
          <Button size="sm" onClick={() => void doSave()} disabled={!text}><Save size={14} /> Save</Button>
          <Button size="sm" variant="primary" onClick={() => void doRun()} disabled={!canRun} loading={running} data-testid="run-scan">
            <Play size={14} /> Run
          </Button>
        </div>
      </div>

      <div className="screener-grid">
        {/* Universe */}
        <aside aria-label="Universe" className="screener-universe rounded-panel border border-border bg-surface-1 p-4">
          <h2 className="text-t15 font-semibold text-fg">Universe</h2>
          <div className="screener-universe-body">
          <div>
          <label className="mt-3 block text-t12 text-fg-muted" htmlFor="screener-symbols">Symbols (up to 50)</label>
          <textarea id="screener-symbols" value={symbolsText} onChange={(e) => setSymbolsText(e.target.value)} rows={4}
                    placeholder="RELIANCE, TCS, INFY"
                    className="mt-1 w-full resize-y rounded-control border border-border bg-surface-inset px-2 py-1.5 font-mono text-t13 text-fg placeholder:text-fg-muted/60 focus:outline-none focus:ring-2 focus:ring-brand" />
          <p className="mt-1 text-t12 text-fg-muted"><span className="font-mono font-tabular text-fg">{symbols.length}</span> symbols</p>
          </div>
          <div>
          <label className="mt-3 block text-t12 text-fg-muted" htmlFor="screener-tf">Scan timeframe</label>
          <select id="screener-tf" value={scanTf} onChange={(e) => setScanTf(e.target.value)}
                  className="mt-1 h-9 w-full rounded-control border border-border bg-surface-2 px-2 font-mono text-t13 text-fg focus:outline-none focus:ring-2 focus:ring-brand">
            {TIMEFRAMES.map((tf) => <option key={tf} value={tf}>{tf}</option>)}
          </select>
          </div>
          <label className="mt-3 flex items-center gap-2 text-t12 text-fg-muted">
            <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} className="h-3.5 w-3.5 accent-[rgb(var(--signal))]" data-testid="live-counts" />
            Live counts while editing
          </label>
          <p className="mt-4 text-t12 text-fg-muted">Closed bars only, read from your broker. A scan lists symbols that meet your own conditions; it is not advice.</p>
          </div>
        </aside>

        {/* Conditions: the funnel */}
        <div className="screener-canvas">
          {registry ? (
            <FunnelCanvas stages={stages} registry={registry} scanTf={scanTf} universe={symbols.length} funnel={shown?.funnel ?? null}
                          fresh={!!shown} live={liveFresh} matched={matchedNow} problems={problems} runKey={runKey} onChange={setStages} />
          ) : (
            <div className="h-40 animate-pulse rounded-panel border border-border bg-surface-1" aria-label="Loading the conditions" />
          )}
          {otherProblem && <p role="alert" className="mt-2 text-t12 text-warn">{otherProblem}</p>}
        </div>

        {/* Results */}
        <div className="screener-results space-y-3">
          {killer && registry && (
            <div role="status" className="rounded-panel border border-warn/50 bg-surface-1 p-3 text-t13 text-fg">
              <p>This condition removes every symbol. Loosen it or disable it to see candidates.</p>
              <div className="mt-2 flex gap-2">
                <Button size="sm" onClick={() => showStage(killer.id)}>Show the stage</Button>
                <Button size="sm" onClick={() => setStages((all) => all.map((s) => (s.id === killer.id ? { ...s, enabled: false } : s)))}>Disable it</Button>
              </div>
            </div>
          )}
          <ResultBoard results={run?.results ?? null} running={running} runKey={runKey} stale={!!run && !fresh}
                       why={fresh && registry ? (symbol) => whyChips(registry, stages, run?.funnel ?? null, symbol) : undefined} />
          {run && <p className="text-t12 text-fg-muted">{run.disclaimer}</p>}
        </div>
      </div>

      {/* Phone: the live count and Run stay in reach */}
      <div className={cx("fixed inset-x-0 bottom-0 z-20 flex items-center justify-between gap-3 border-t border-border bg-surface-1 px-4 py-2 sm:hidden")}>
        <p className="text-t12 text-fg-muted">
          Matched <span className="font-mono font-tabular text-t15 font-semibold text-signal">{matchedNow ?? "–"}</span>
          <span> of <span className="font-mono font-tabular">{symbols.length}</span></span>
        </p>
        <div className="flex gap-2">
          <Button size="sm" onClick={() => void doSave()} disabled={!text} aria-label="Save"><Save size={14} /></Button>
          <Button size="sm" variant="primary" onClick={() => void doRun()} disabled={!canRun} loading={running} data-testid="run-scan-mobile"><Play size={14} /> Run</Button>
        </div>
      </div>
    </div>
  );
}
