/**
 * U8 EV1 / EV2: "Show on chart" for a matched result row - the evidence chart, a "Why it matched" list of the screen's
 * blocks (toggle a block to hide its drawing, hover to pick it out), the verify strip (each condition with the engine's
 * value at the matched bar and whether it passed) and "Send to my Telegram" (the same chart as an image, to the user's
 * own linked chat). Failed conditions stay hidden unless "Show failed conditions" is on (the pack is then read with
 * debug). Informational: the panel explains a match; it never suggests a trade.
 *
 * EV3 (flag evidence_context; the pack then carries `context`): the trend strip (the engine's DowTrend on the screen's
 * timeframe and above, as of the matched bar) and replay - step back bar by bar through the pack's window and see the
 * screen recomputed from the shared history as of that bar (never stored, never sent), then return to the match.
 */
import { ChevronLeft, ChevronRight, RotateCcw, Send } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { ApiError } from "../api/client";
import { Button, Dialog, cx } from "../components/primitives";
import { EvidenceChart, EvidenceLegend } from "./EvidenceChart";
import {
  barTimeText, evidenceApi, replaySteps, stepReplay, trendTone, verifyRows, visibleDrawables, type EvidenceContext, type EvidencePack,
} from "./evidence";
import { canReplay, type RowState } from "./evidence";

export type SendState = { status: "idle" | "sending" | "sent" | "failed"; message?: string };

/** EV3 replay controls: `index` of `steps` (the last step is the stored pack itself). */
export interface ReplayControls {
  steps: number; index: number; loading: boolean; error?: string | null;
  onStep: (delta: number) => void; onReset: () => void;
}

export interface EvidenceViewProps {
  pack: EvidencePack;
  hiddenBlocks: ReadonlySet<string>;
  showFailed: boolean;
  onToggleBlock?: (blockId: string) => void;
  onShowFailed?: (on: boolean) => void;
  onSend?: () => void;
  send?: SendState;
  chartWidth?: number;
  replay?: ReplayControls;
}

const TONE = { up: "border-signal/50 text-signal", down: "border-warn/50 text-warn", flat: "border-border text-fg-muted" } as const;
const ARROW = { up: "▲", down: "▼", flat: "◆" } as const;

/** EV3: the trend strip - one chip per timeframe, the tag in words (never colour alone), its expression and bar in the title. */
export function ContextStrip({ context, rowText }: { context: readonly EvidenceContext[]; rowText: (id: string) => string }) {
  return (
    <section aria-label="Trend on other timeframes" className="flex flex-wrap items-center gap-1.5 text-t12" data-testid="context-strip">
      <span className="text-fg-muted">Trend (Dow):</span>
      {context.map((c) => {
        const tone = trendTone(c.tag);
        const reads = c.used_by.map(rowText).filter(Boolean);
        const title = [c.expr, c.bar_close ? `as of the bar that closed ${c.bar_close}` : null, c.note, reads.length ? `read by: ${reads.join("; ")}` : null]
          .filter(Boolean).join(" · ");
        return (
          <span key={c.tf} title={title} data-tf={c.tf}
                className={cx("inline-flex items-center gap-1 rounded-control border bg-surface-2 px-1.5 py-0.5 font-tabular", TONE[tone], reads.length > 0 && "font-semibold")}>
            <span aria-hidden>{ARROW[tone]}</span>{c.text}{reads.length ? <span className="sr-only"> (read by the screen)</span> : null}
          </span>
        );
      })}
    </section>
  );
}

/** EV3: step back / forward through the pack's window, and back to the matched bar. */
export function ReplayBar({ replay, pack }: { replay: ReplayControls; pack: EvidencePack }) {
  const atMatch = replay.index >= replay.steps - 1;
  return (
    <div className="flex flex-wrap items-center gap-2 text-t12" data-testid="replay-bar">
      <span className="text-fg-muted">Replay:</span>
      <Button variant="secondary" onClick={() => replay.onStep(-1)} disabled={replay.index <= 0 || replay.loading} aria-label="Step back one bar">
        <ChevronLeft size={14} aria-hidden />
      </Button>
      <span className="font-tabular text-fg" aria-live="polite">bar {replay.index + 1} of {replay.steps}{atMatch ? " (the match)" : ""}</span>
      <Button variant="secondary" onClick={() => replay.onStep(1)} disabled={atMatch || replay.loading} aria-label="Step forward one bar">
        <ChevronRight size={14} aria-hidden />
      </Button>
      {!atMatch && (
        <Button variant="secondary" onClick={replay.onReset} aria-label="Back to the matched bar">
          <RotateCcw size={14} aria-hidden /> Back to the match
        </Button>
      )}
      {replay.loading && <span role="status" className="text-fg-muted">Replaying…</span>}
      {replay.error && <span role="alert" className="text-warn">{replay.error}</span>}
      {pack.replay && !replay.loading && (
        <span role="status" className="basis-full text-fg-muted">Replayed as of the {barTimeText(pack)}. {pack.replay.note}</span>
      )}
    </div>
  );
}

function Mark({ passed, state }: { passed: boolean | null; state?: RowState }) {
  // SCR-FIX B5: an OR branch that was not needed (another branch made the OR true) is grey, never ✅ or a plain "–"
  if (state === "not_needed") return <span className="text-fg-muted" title="Another branch of the OR was true"><span aria-hidden>○</span><span className="sr-only">not needed</span></span>;
  if (passed == null) return <span className="text-fg-muted"><span aria-hidden>–</span><span className="sr-only">not evaluated</span></span>;
  return passed
    ? <span className="text-signal"><span aria-hidden>✅</span><span className="sr-only">passed</span></span>
    : <span className="text-warn"><span aria-hidden>❌</span><span className="sr-only">did not pass</span></span>;
}

/** The panel's content (no dialog), for tests and the story. */
export function EvidenceView({ pack, hiddenBlocks, showFailed, onToggleBlock, onShowFailed, onSend, send, chartWidth = 720, replay }: EvidenceViewProps) {
  const [highlight, setHighlight] = useState<string | null>(null);
  const drawables = useMemo(() => visibleDrawables(pack, hiddenBlocks, showFailed), [pack, hiddenBlocks, showFailed]);
  const rows = verifyRows(pack, showFailed);
  const rowById = new Map(pack.rows.map((r) => [r.id, r]));
  const hiddenFailed = pack.rows.filter((r) => r.passed === false).length;
  return (
    <div className="flex flex-col gap-4" data-testid="evidence-view">
      <div className="flex flex-wrap items-baseline justify-between gap-2 text-t12 text-fg-muted">
        <span>
          <span className="font-mono text-t13 font-semibold text-fg">{pack.symbol}</span> · {pack.base_tf} · {barTimeText(pack)}
          {pack.data.source && <> · data: {pack.data.source}</>}
        </span>
        <span>{pack.screen.name ? <>Screen: <span className="text-fg">{pack.screen.name}</span></> : <span className="font-mono">{pack.screen.text}</span>}</span>
      </div>
      {pack.context && pack.context.length > 0 && <ContextStrip context={pack.context} rowText={(id) => rowById.get(id)?.text ?? ""} />}
      {replay && replay.steps > 1 && <ReplayBar replay={replay} pack={pack} />}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_260px]">
        <div className="min-w-0 rounded-panel border border-border bg-surface-1 p-2">
          <EvidenceChart pack={pack} drawables={drawables} width={chartWidth} highlight={highlight} />
          <div className="px-2 pb-1 pt-2"><EvidenceLegend drawables={drawables} /></div>
        </div>
        <section aria-label="Why it matched" className="flex flex-col gap-2">
          <h3 className="text-t13 font-semibold text-fg">Why it matched</h3>
          <ul className="flex flex-col gap-1.5">
            {pack.blocks.map((b, i) => {
              const drawnIds = b.rows.map((id) => rowById.get(id)?.drawable).filter((d): d is string => !!d);
              const hidden = hiddenBlocks.has(b.id);
              return (
                <li key={b.id} className="flex items-start gap-2 rounded-control border border-border bg-surface-2 px-2 py-1.5 text-t12"
                    onMouseEnter={() => setHighlight(drawnIds[0] ?? null)} onMouseLeave={() => setHighlight(null)}
                    onFocus={() => setHighlight(drawnIds[0] ?? null)} onBlur={() => setHighlight(null)}>
                  <input type="checkbox" id={`ev-block-${b.id}`} className="mt-0.5" checked={!hidden} disabled={!drawnIds.length}
                         onChange={() => onToggleBlock?.(b.id)} aria-describedby={`ev-block-text-${b.id}`} />
                  <label htmlFor={`ev-block-${b.id}`} className="min-w-0 flex-1">
                    <span className="sr-only">{`Show block ${i + 1} on the chart: `}</span>
                    <span id={`ev-block-text-${b.id}`} className="break-words font-mono text-fg">{b.text}</span>
                    {!drawnIds.length && <span className="block text-fg-muted">not drawn (shown in the strip)</span>}
                  </label>
                  <Mark passed={b.passed} />
                </li>
              );
            })}
          </ul>
          <label className="mt-1 inline-flex items-center gap-2 text-t12 text-fg-muted">
            <input type="checkbox" checked={showFailed} onChange={(e) => onShowFailed?.(e.target.checked)} />
            Show failed conditions{hiddenFailed && !showFailed ? ` (${hiddenFailed})` : ""}
          </label>
        </section>
      </div>
      <section aria-label="Verify" className="overflow-x-auto rounded-panel border border-border">
        <table className="w-full border-collapse text-t12" data-testid="verify-strip">
          <caption className="sr-only">Each condition with the engine's value at the matched bar and whether it passed</caption>
          <thead className="bg-surface-2 text-fg-muted">
            <tr>
              <th scope="col" className="px-3 py-1.5 text-left font-medium">Condition</th>
              <th scope="col" className="px-3 py-1.5 text-left font-medium">Values at the matched bar</th>
              <th scope="col" className="px-3 py-1.5 text-left font-medium">Result</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className={cx("border-t border-border/60", r.passed === false && "opacity-70")} data-row={r.id}>
                <td className="px-3 py-1.5 font-mono text-fg">{r.text}</td>
                <td className="px-3 py-1.5 font-tabular text-fg">
                  {r.verify}
                  {r.note && <span className="block text-fg-muted">{r.note}</span>}
                </td>
                <td className="px-3 py-1.5"><Mark passed={r.passed} state={r.state} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        {pack.summary.not_shown > 0 && <p className="border-t border-border px-3 py-1.5 text-t12 text-fg-muted">+{pack.summary.not_shown} more conditions not listed</p>}
      </section>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-t12 text-fg-muted">{pack.disclaimer}</p>
        {pack.can_send && onSend && (
          <div className="flex items-center gap-2">
            {send?.status === "sent" && <span role="status" className="text-t12 text-signal">Sent to your Telegram.</span>}
            {send?.status === "failed" && <span role="alert" className="text-t12 text-warn">{send.message}</span>}
            <Button variant="secondary" onClick={onSend} disabled={send?.status === "sending"} aria-label="Send this chart to my Telegram">
              <Send size={14} aria-hidden /> {send?.status === "sending" ? "Sending…" : "Send to my Telegram"}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

/** The dialog: loads the pack (again with debug when "Show failed conditions" is turned on). */
export function EvidencePanel({ evidenceId, symbol, onClose }: { evidenceId: number | null; symbol?: string; onClose: () => void }) {
  const [pack, setPack] = useState<EvidencePack | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showFailed, setShowFailed] = useState(false);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [send, setSend] = useState<SendState>({ status: "idle" });
  const [step, setStep] = useState<number | null>(null);                 // EV3: null = the stored pack
  const [replayed, setReplayed] = useState<EvidencePack | null>(null);
  const [replayLoading, setReplayLoading] = useState(false);
  const [replayError, setReplayError] = useState<string | null>(null);
  const [refused, setRefused] = useState(false);                          // SCR-FIX B6: the server refused a replay
  const steps = useMemo(() => (pack?.context ? replaySteps(pack) : []), [pack]);

  useEffect(() => {
    if (evidenceId == null) return;
    let live = true;
    setError(null);
    evidenceApi.get(evidenceId, showFailed).then(
      (p) => { if (live) setPack(p); },
      (e: unknown) => { if (live) setError(e instanceof ApiError && e.status === 404 ? "This evidence is not available." : "The evidence could not be loaded."); },
    );
    return () => { live = false; };
  }, [evidenceId, showFailed]);

  useEffect(() => {
    setHidden(new Set()); setSend({ status: "idle" }); setPack(null); setShowFailed(false);
    setStep(null); setReplayed(null); setReplayError(null); setReplayLoading(false); setRefused(false);
  }, [evidenceId]);

  // EV3 replay: one request per step; a newer step (or closing / going back to the match) drops an older answer.
  useEffect(() => {
    setReplayError(null);
    if (evidenceId == null || step == null || step >= steps.length - 1) { setReplayed(null); setReplayLoading(false); return; }
    let live = true;
    setReplayLoading(true);
    evidenceApi.replay(evidenceId, steps[step], showFailed).then(
      (p) => { if (live) { setReplayed(p); setReplayLoading(false); } },
      (e: unknown) => {
        if (!live) return;
        setReplayLoading(false);
        // SCR-FIX B6: a refusal (this evidence cannot be replayed) hides the controls and shows the stored pack again -
        // never step buttons beside "Replay is not available"; anything else (a network problem) can be stepped again
        if (e instanceof ApiError && (e.status === 404 || e.status === 422)) { setRefused(true); setStep(null); return; }
        setReplayError(e instanceof ApiError ? e.message : "Replay could not be loaded. Try again.");
      },
    );
    return () => { live = false; };
  }, [evidenceId, step, steps, showFailed]);

  const toggle = (id: string) => setHidden((h) => { const next = new Set(h); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  const doSend = async () => {
    if (evidenceId == null) return;
    setSend({ status: "sending" });
    try {
      await evidenceApi.sendTelegram(evidenceId);
      setSend({ status: "sent" });
    } catch (e) {
      setSend({ status: "failed", message: e instanceof ApiError ? e.message : "Could not send." });
    }
  };

  return (
    <Dialog open={evidenceId != null} onOpenChange={(open) => { if (!open) onClose(); }} wide
            title={`Why ${pack?.symbol ?? symbol ?? ""} matched`} description="Only the screen's own conditions are drawn, with the engine's numbers.">
      {error ? <p role="alert" className="text-warn">{error}</p>
        : !pack ? <p className="text-fg-muted">Loading the evidence…</p>
          : <EvidenceView pack={replayed ?? pack} hiddenBlocks={hidden} showFailed={showFailed} onToggleBlock={toggle} onShowFailed={setShowFailed}
                          onSend={doSend} send={send}
                          replay={canReplay(pack, steps.length, refused) ? {
                            steps: steps.length, index: step ?? steps.length - 1, loading: replayLoading, error: replayError,
                            onStep: (delta) => setStep((s) => stepReplay(s, delta, steps.length)),
                            onReset: () => setStep(null),
                          } : undefined} />}
    </Dialog>
  );
}

export default EvidencePanel;
