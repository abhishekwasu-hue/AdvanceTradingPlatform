import { X } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError } from "../../api/errors";
import { Button, Skeleton } from "../../components/primitives";
import { Combobox } from "../../ui/Combobox";
import { ConfirmDialog } from "../../ui/ConfirmDialog";
import { Details } from "../../ui/Details";
import { EMPTY_MEMORY, TF_WORDS, drop, keep, memoryApi, type CopilotMemory, type MemoryView } from "./model";

/**
 * M72 PR-11 (Settings › AI, behind `ui_v2` + `copilot_memory`): "What the Copilot remembers" - his own instruments,
 * timeframes and style, shown and editable; suggestions from his own lists, trades and questions to keep or ignore;
 * Delete clears it (as does "Delete my Copilot conversation"). Nothing is remembered until he saves.
 */
const CHIP = "inline-flex min-h-9 items-center gap-space-1 rounded-pill border border-border bg-surface-2 px-space-3 text-t13 text-fg";

export function MemoryForm({ view, busy, onSave, onDelete }: { view: MemoryView; busy: boolean; onSave: (m: CopilotMemory) => void; onDelete: () => void }) {
  const [m, setM] = useState<CopilotMemory>({ ...EMPTY_MEMORY, ...view.memory });
  const [asking, setAsking] = useState(false);
  const extraSymbols = view.suggested.instruments.filter((s) => !m.instruments.includes(s.symbol));
  const extraTfs = view.suggested.timeframes.filter((t) => !m.timeframes.includes(t.timeframe));
  const styleOptions = [{ value: "", label: "Not set" }, ...Object.entries(view.styles).map(([value, label]) => ({ value, label }))];
  return (
    <div className="space-y-space-3" data-copilot-memory>
      <section className="space-y-space-1">
        <h4 className="text-t13 font-medium text-fg-muted">Instruments</h4>
        <div className="flex flex-wrap gap-space-1" data-memory="instruments">
          {m.instruments.length === 0 && <span className="text-t13 text-fg-muted">None saved.</span>}
          {m.instruments.map((s) => (
            <span key={s} className={CHIP}>{s}
              <button type="button" aria-label={`Forget ${s}`} className="text-fg-muted hover:text-fg" onClick={() => setM(drop(m, { symbol: s }))}><X size={14} aria-hidden /></button>
            </span>
          ))}
        </div>
      </section>
      <section className="space-y-space-1">
        <h4 className="text-t13 font-medium text-fg-muted">Timeframes</h4>
        <div className="flex flex-wrap gap-space-1" data-memory="timeframes">
          {m.timeframes.length === 0 && <span className="text-t13 text-fg-muted">None saved.</span>}
          {m.timeframes.map((t) => (
            <span key={t} className={CHIP}>{TF_WORDS[t]}
              <button type="button" aria-label={`Forget ${TF_WORDS[t]}`} className="text-fg-muted hover:text-fg" onClick={() => setM(drop(m, { timeframe: t }))}><X size={14} aria-hidden /></button>
            </span>
          ))}
        </div>
      </section>
      <div className="flex max-w-xs flex-col gap-space-1">
        <span className="text-t13 font-medium text-fg-muted">Style</span>
        <Combobox label="Style" value={m.style ?? ""} options={styleOptions} onChange={(v) => setM({ ...m, style: v || null })} dataAttrs={{ "data-memory": "style" }} />
      </div>
      {(extraSymbols.length > 0 || extraTfs.length > 0) && (
        <section className="space-y-space-1" data-memory="suggested">
          <h4 className="text-t13 font-medium text-fg-muted">From your own activity - tap to keep</h4>
          <div className="flex flex-wrap gap-space-1">
            {extraSymbols.map((s) => (
              <button key={s.symbol} type="button" className={`${CHIP} border-dashed`} title={`From ${s.from.join(", ")}`} onClick={() => setM(keep(m, { symbol: s.symbol }))}>
                + {s.symbol}
              </button>
            ))}
            {extraTfs.map((t) => (
              <button key={t.timeframe} type="button" className={`${CHIP} border-dashed`} title={`From ${t.from.join(", ")}`} onClick={() => setM(keep(m, { timeframe: t.timeframe }))}>
                + {TF_WORDS[t.timeframe]}
              </button>
            ))}
          </div>
        </section>
      )}
      <Details id="copilot.memory" className="mt-0">
        <p className="text-t13 text-fg-muted">The Copilot uses these only for your own questions when you do not name an instrument or timeframe. They
          never set a strike, lots, a price or a level, and nobody else sees them. Delete my Copilot conversation also deletes them.</p>
      </Details>
      <div className="flex flex-wrap gap-space-2">
        <Button type="button" variant="primary" size="v2" disabled={busy} onClick={() => onSave(m)}>Save</Button>
        <Button type="button" variant="secondary" size="v2" disabled={busy} onClick={() => setAsking(true)}>Delete</Button>
      </div>
      <ConfirmDialog open={asking} onOpenChange={setAsking} title="Delete what the Copilot remembers?" consequence="Your saved instruments, timeframes and style are deleted."
        confirmLabel="Delete" note="You can save them again later." onConfirm={() => { setAsking(false); onDelete(); }} />
    </div>
  );
}

export default function CopilotMemoryV2() {
  const [view, setView] = useState<MemoryView | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [hidden, setHidden] = useState(false);
  const [round, setRound] = useState(0);
  useEffect(() => {
    let live = true;
    memoryApi.read().then((v) => { if (live) setView(v); }, (e: unknown) => {
      if (!live) return;
      if (e instanceof ApiError && e.status === 404) setHidden(true);
      else setNote("Could not load what the Copilot remembers.");
    });
    return () => { live = false; };
  }, [round]);
  if (hidden) return null;
  if (!view) return note ? <p className="text-t13 text-fg-muted">{note}</p> : <Skeleton className="h-20" label="Loading" />;

  async function act(run: () => Promise<unknown>, done: string) {
    setBusy(true); setNote(null);
    try { await run(); setNote(done); setRound((n) => n + 1); }
    catch { setNote("Could not save. Try again."); }
    finally { setBusy(false); }
  }
  return (
    <div className="space-y-space-2">
      <MemoryForm key={`${round}-${JSON.stringify(view.memory)}`} view={view} busy={busy}
        onSave={(m) => void act(() => memoryApi.save(m), "Saved.")} onDelete={() => void act(() => memoryApi.remove(), "Deleted.")} />
      {note && <p className="text-t13 text-fg-muted" role="status">{note}</p>}
    </div>
  );
}
