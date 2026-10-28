import { Minus, Plus, TriangleAlert } from "lucide-react";
import { useConfirm } from "../ui/ConfirmDialog";
import { useCallback, useEffect, useId, useMemo, useState, type ReactNode } from "react";
import { Dialog } from "../components/primitives/Dialog";
import { friendlyError } from "../api/client";
import type { TradeRecord } from "../types";
import { Details } from "../ui/Details";
import { Signed } from "../ui/Signed";
import {
  FIELD_LABEL, clampLots, exitPartCopy, exitPartPieces, modifyChanges, modifyFields, modifyStart, newRef, openLots, positionActionsApi, reverseCopy,
  reverseNeedsStop, reverseStopLabel, runAction, waitingLine, waitingQuantity, type ModifyAsk, type PositionActionsApi, type WaitingOrder,
} from "./positionActions";

const BUTTON = "inline-flex h-11 items-center justify-center rounded-control px-space-4 text-t14 font-semibold transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:opacity-50 md:h-9";
const QUIET = `${BUTTON} border border-border bg-surface-1 text-fg hover:bg-surface-2`;
const STRONG = `${BUTTON} bg-fg text-surface-1 hover:opacity-90`;
const SMALL = "inline-flex min-h-11 items-center rounded-control border border-border px-space-3 text-t13 font-semibold text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand lg:min-h-9";
const INPUT = "h-11 w-28 rounded-control border border-border bg-surface-1 px-space-2 font-tabular text-t14 text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:h-9";

export type PositionActionAsk = { kind: "part" | "reverse"; position: TradeRecord };

/** The line after "Exit part": the closed lots' profit / loss is the shared signed value (UX-11 U2: + ▲ / − ▼ / 0 muted). */
export function ExitPartResultLine({ pieces }: { pieces: ReturnType<typeof exitPartPieces> }) {
  return <span data-exit-part-result>{pieces.before}<Signed value={pieces.pnl} />{pieces.after}</span>;
}

/**
 * Terminal T2 (TT-9): the confirm sheet of "Exit part" (a lots stepper, 1 to the open lots) and "Reverse" (all lots close,
 * then the other side opens with the same lots; a sold side asks for his own stop-loss, nothing pre-filled). The
 * reference is made when the sheet opens and reused on every tap, so a double tap never acts twice. A hedge before its
 * sold leg: the server's warning keeps the sheet open and the next tap confirms - a warning, never a block (ADR-0004).
 */
export function PositionActionSheetV2({ ask, onClose, onDone, api = positionActionsApi }: {
  ask: PositionActionAsk | null; onClose: () => void; onDone: (text: ReactNode, ok: boolean) => void; api?: PositionActionsApi;
}) {
  const [lots, setLots] = useState(1);
  const [stop, setStop] = useState("");
  const [ref, setRef] = useState(newRef);
  const [busy, setBusy] = useState(false);
  const [warning, setWarning] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const stopId = useId();
  const position = ask?.position;
  useEffect(() => { setLots(1); setStop(""); setRef(newRef()); setWarning(null); setProblem(null); }, [position?.id, ask?.kind]);
  if (!ask || !position) return null;
  const held = openLots(position);
  const needsStop = ask.kind === "reverse" && reverseNeedsStop(position);
  const copy = ask.kind === "part" ? exitPartCopy(position.symbol, lots, held) : reverseCopy(position.symbol, held, position.direction === "LONG");
  const stopLabel = reverseStopLabel(position);
  const close = () => { setWarning(null); setProblem(null); onClose(); };

  async function go() {
    if (!ask || !position) return;
    const stopLoss = stop.trim() === "" ? undefined : Number(stop);
    // DEF-0 D7: the stop is optional (only the trader's own setting asks for one - the server says so); a typed one must be a price
    if (needsStop && stopLoss !== undefined && (!Number.isFinite(stopLoss) || stopLoss <= 0)) {
      setProblem("The stop-loss is not a price. Nothing has been closed.");
      return;
    }
    setBusy(true);
    const step = await runAction(ask.kind === "part"
      ? { kind: "part", tradeId: position.id, symbol: position.symbol, lots, ref }
      : { kind: "reverse", tradeId: position.id, symbol: position.symbol, ref, stopLoss }, warning !== null, api);
    setBusy(false);
    if (step.kind === "warn") { setWarning(step.warning); return; }
    setWarning(null);
    const p = step.pieces;
    onDone(p && p.pnl !== null ? <ExitPartResultLine pieces={p} /> : step.text, step.ok);
  }

  return (
    <Dialog open onOpenChange={(o) => { if (!o) close(); }} title={copy.title}
      description={<span className="text-t14 text-fg" data-consequence>{copy.body}</span>}
      footer={(
        <>
          <button type="button" onClick={close} className={QUIET}>Keep it open</button>
          <button type="button" onClick={go} disabled={busy} data-confirm data-testid="position-action-confirm" className={STRONG}>
            {warning ? `${copy.confirm} anyway` : copy.confirm}
          </button>
        </>
      )}>
      <div className="space-y-space-3">
        {ask.kind === "part" && (
          <div className="flex items-center gap-space-2" data-lots-stepper>
            <span className="text-t14 text-fg">Lots to exit</span>
            <button type="button" aria-label="One lot less" onClick={() => setLots((n) => clampLots(n - 1, held))} className={SMALL}><Minus size={14} aria-hidden /></button>
            <input aria-label="Lots to exit" inputMode="numeric" value={lots} className={`${INPUT} w-16 text-center`} data-testid="exit-part-lots"
              onChange={(e) => setLots(clampLots(Number(e.target.value), held))} />
            <button type="button" aria-label="One lot more" onClick={() => setLots((n) => clampLots(n + 1, held))} className={SMALL}><Plus size={14} aria-hidden /></button>
            <span className="text-t13 text-fg-muted">of {held}</span>
          </div>
        )}
        {needsStop && (
          <div className="flex flex-wrap items-center gap-space-2">
            <label htmlFor={stopId} className="text-t14 text-fg" data-stop-unit>{stopLabel.label}</label>
            <input id={stopId} inputMode="decimal" value={stop} onChange={(e) => setStop(e.target.value)} className={INPUT} data-testid="reverse-stop" />
          </div>
        )}
        {problem && <p className="text-t13 text-warn" role="alert">{problem}</p>}
        {warning ? (
          <p className="flex items-start gap-space-2 rounded-control border border-border bg-surface-2 p-space-3 text-t14 text-fg" data-hedge-warning role="alert">
            <TriangleAlert size={16} aria-hidden className="mt-px shrink-0 text-fg-muted" />
            <span>{warning}</span>
          </p>
        ) : null}
        <Details id={`terminal.${ask.kind === "part" ? "exit-part" : "reverse"}`} className="mt-space-1">
          {needsStop && <p>{stopLabel.detail}</p>}
          {copy.details.map((line) => <p key={line}>{line}</p>)}
        </Details>
      </div>
    </Dialog>
  );
}

/**
 * Terminal T2 (TT-10): his own waiting orders - limit / stop entries that wait for their price and the stops / targets
 * armed on his positions - each with Modify (its price, trigger or lots; an exit its level) and Cancel (a confirm first).
 * Nothing shows when he has none.
 */
export function WaitingOrdersV2({ api = positionActionsApi, onChanged, refresh = 0 }: { api?: PositionActionsApi; onChanged?: () => void; refresh?: number }) {
  const [rows, setRows] = useState<WaitingOrder[] | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const confirmBox = useConfirm();
  const load = useCallback(() => { api.waiting().then(setRows, () => setRows(null)); }, [api]);
  useEffect(() => { load(); }, [load, refresh]);
  if (!rows || rows.length === 0) return note ? <p className={`text-t13 ${note.ok ? "text-fg-muted" : "text-warn"}`} role="status">{note.text}</p> : null;

  async function cancel(o: WaitingOrder) {
    if (!(await confirmBox.ask("Cancel this waiting order?", waitingLine(o), "Cancel order"))) return;
    try {
      const r = await api.cancel(o.id);
      setNote({ ok: true, text: r.status === "CANCELLED" ? "Waiting order cancelled." : `Not cancelled: it is ${r.status_words.toLowerCase()}.` });
    } catch (e) {
      setNote({ ok: false, text: `Not cancelled. ${friendlyError(e).trim() || "Try again in a moment."}` });
    }
    load();
    onChanged?.();
  }

  return (
    <section className="space-y-space-2" data-waiting-orders aria-label="Waiting orders">
      {confirmBox.dialog}
      <h3 className="text-t13 font-semibold text-fg">Waiting orders</h3>
      {note && <p className={`text-t13 ${note.ok ? "text-fg-muted" : "text-warn"}`} role="status">{note.text}</p>}
      <ul className="divide-y divide-subtle">
        {rows.map((o) => (
          <li key={o.id} className="py-space-1" data-waiting={o.id}>
            <div className="flex flex-wrap items-center gap-space-2">
              <span className="min-w-0 flex-1 text-t13 text-fg">{waitingLine(o)}</span>
              <span className="text-t12 text-fg-muted">{o.status_words}</span>
              {o.status === "RESTING" && (
                <>
                  <button type="button" className={SMALL} onClick={() => setEditing(editing === o.id ? null : o.id)} data-testid="waiting-modify">Modify</button>
                  <button type="button" className={SMALL} onClick={() => void cancel(o)} data-testid="waiting-cancel">Cancel</button>
                </>
              )}
            </div>
            {editing === o.id && (
              <ModifyFormV2 order={o} api={api} onDone={(text, ok) => { setEditing(null); setNote({ ok, text }); load(); onChanged?.(); }} />
            )}
            <Details id="terminal.waiting-order" className="mt-space-1">
              {o.purpose === "ENTRY" && <p>{waitingQuantity(o)}</p>}
              <p>{o.purpose === "ENTRY"
                ? "Modify changes its price, trigger or lots; every entry check runs again when it is placed."
                : "A stop or target closes the whole position: Modify changes its level only. Use Exit part to close some lots now."}</p>
            </Details>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function ModifyFormV2({ order, api, onDone }: { order: WaitingOrder; api: PositionActionsApi; onDone: (text: string, ok: boolean) => void }) {
  const fields = modifyFields(order);
  // F5: the form opens with the order's current values; Save stays off until one of them differs.
  const start = useMemo(() => modifyStart(order), [order]);
  const [values, setValues] = useState<Partial<Record<keyof ModifyAsk, string>>>(start);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const confirmBox = useConfirm();
  const base = useId();
  const changes = modifyChanges(start, values);
  async function save() {
    const ask = changes;
    if (ask === "same") return;
    if (!ask) { setProblem("Enter numbers above zero."); return; }
    if (!(await confirmBox.ask("Change this waiting order?", waitingLine(order), "Change order"))) return;
    setBusy(true);
    try {
      const r = await api.modify(order.id, ask);
      onDone(`Changed: ${waitingLine(r)}.`, true);
    } catch (e) {
      setProblem(`Not changed. ${friendlyError(e).trim() || "Try again in a moment."}`);
    }
    setBusy(false);
  }
  return (
    <div className="mt-space-1 flex flex-wrap items-end gap-space-2" data-modify-form={order.id}>
      {confirmBox.dialog}
      {fields.map((f) => (
        <label key={f} htmlFor={`${base}-${f}`} className="flex flex-col gap-px text-t12 text-fg-muted">
          {FIELD_LABEL[f]}
          <input id={`${base}-${f}`} inputMode={f === "lots" ? "numeric" : "decimal"} value={values[f] ?? ""} className={INPUT}
            onChange={(e) => setValues((v) => ({ ...v, [f]: e.target.value }))} data-field={f} />
        </label>
      ))}
      <button type="button" onClick={() => void save()} disabled={busy || changes === "same"} className={SMALL} data-testid="waiting-save">Save change</button>
      {problem && <p className="w-full text-t13 text-warn" role="alert">{problem}</p>}
    </div>
  );
}
