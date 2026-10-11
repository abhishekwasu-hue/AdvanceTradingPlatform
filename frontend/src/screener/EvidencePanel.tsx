/**
 * U8 EV1 / EV2: "Show on chart" for a matched result row - the evidence chart, a "Why it matched" list of the screen's
 * blocks (toggle a block to hide its drawing, hover to pick it out), the verify strip (each condition with the engine's
 * value at the matched bar and whether it passed) and "Send to my Telegram" (the same chart as an image, to the user's
 * own linked chat). Failed conditions stay hidden unless "Show failed conditions" is on (the pack is then read with
 * debug). Informational: the panel explains a match; it never suggests a trade.
 */
import { Send } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { ApiError } from "../api/client";
import { Button, Dialog, cx } from "../components/primitives";
import { EvidenceChart, EvidenceLegend } from "./EvidenceChart";
import { barTimeText, evidenceApi, verifyRows, visibleDrawables, type EvidencePack } from "./evidence";

export type SendState = { status: "idle" | "sending" | "sent" | "failed"; message?: string };

export interface EvidenceViewProps {
  pack: EvidencePack;
  hiddenBlocks: ReadonlySet<string>;
  showFailed: boolean;
  onToggleBlock?: (blockId: string) => void;
  onShowFailed?: (on: boolean) => void;
  onSend?: () => void;
  send?: SendState;
  chartWidth?: number;
}

function Mark({ passed }: { passed: boolean | null }) {
  if (passed == null) return <span className="text-fg-muted"><span aria-hidden>–</span><span className="sr-only">not evaluated</span></span>;
  return passed
    ? <span className="text-signal"><span aria-hidden>✅</span><span className="sr-only">passed</span></span>
    : <span className="text-warn"><span aria-hidden>❌</span><span className="sr-only">did not pass</span></span>;
}

/** The panel's content (no dialog), for tests and the story. */
export function EvidenceView({ pack, hiddenBlocks, showFailed, onToggleBlock, onShowFailed, onSend, send, chartWidth = 720 }: EvidenceViewProps) {
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
                <td className="px-3 py-1.5"><Mark passed={r.passed} /></td>
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

  useEffect(() => { setHidden(new Set()); setSend({ status: "idle" }); setPack(null); setShowFailed(false); }, [evidenceId]);

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
    <Dialog open={evidenceId != null} onOpenChange={(open) => { if (!open) onClose(); }} size="xl"
            title={`Why ${pack?.symbol ?? symbol ?? ""} matched`} description="Only the screen's own conditions are drawn, with the engine's numbers.">
      {error ? <p role="alert" className="text-warn">{error}</p>
        : !pack ? <p className="text-fg-muted">Loading the evidence…</p>
          : <EvidenceView pack={pack} hiddenBlocks={hidden} showFailed={showFailed} onToggleBlock={toggle} onShowFailed={setShowFailed}
                          onSend={doSend} send={send} />}
    </Dialog>
  );
}

export default EvidencePanel;
