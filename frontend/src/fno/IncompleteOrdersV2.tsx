/**
 * SE-7 (owner, 23 Oct; flag smart_execution, behind ui_v2): on Positions, a multi-leg order that stopped half way.
 * What filled, what did not, his three taps - "Complete the leg(s)", "Exit the filled leg(s)" (T1-4), "Keep them" - and the time
 * left before his own rule applies (Settings › My trading defaults: close the filled legs, or keep them, after N
 * minutes). Nothing here places or closes anything by itself: the countdown only shows when his rule will apply.
 * Backend: app/execution/incomplete_structures.py, routes app/execution/incomplete_routes.py.
 */
import { LiveBadge } from "../ui/PaperBadge";
import { Clock, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { completeLabel, exitFilledLabel, incompleteApi, type IncompleteAction, type IncompleteApi, type IncompleteItem } from "./incompleteApi";
import { sideWord } from "./ticketModel";
import { formatNumber } from "../ui/format";

const POLL_MS = 15_000;

/** "4:05" from seconds; "0:00" at and after the deadline. */
export function countdownText(seconds: number | null): string {
  const s = Math.max(0, Math.floor(seconds ?? 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

const STATE_TEXT: Record<string, string> = {
  COMPLETING: "Sending the rest of the order on the next check, hedge legs first.",
  COMPLETING_SENT: "Sending the rest of the order now, hedge legs first.",
  CLOSING: "Closing the filled legs, sold legs first.",
};

export function IncompleteCardV2({ item, secondsLeft, busy, onAnswer, note }: {
  item: IncompleteItem; secondsLeft: number | null; busy: boolean; onAnswer: (action: IncompleteAction) => void; note?: string | null;
}) {
  const waiting = item.state === "INCOMPLETE";
  const keep = item.on_no_answer === "keep";
  return (
    <section className="space-y-space-3 rounded-panel border border-warn/40 bg-warn-tint px-space-4 py-space-3" data-incomplete={item.group_id}
      data-state={item.state} aria-label="Incomplete order">
      <p className="flex items-start gap-space-2 text-t15 font-semibold text-fg" role="alert">
        <TriangleAlert size={16} aria-hidden className="mt-0.5 shrink-0 text-warn" />
        <span>Incomplete order: {item.underlying}</span>{item.mode === "LIVE" && <LiveBadge />}
      </p>
      <p className="text-t14 text-fg">A leg did not fill, so nothing more was sent and nothing was undone.</p>
      <div className="grid gap-space-3 md:grid-cols-2">
        <div>
          <p className="text-t13 font-semibold text-fg-muted">Filled ({item.filled.length})</p>
          <ul className="space-y-space-1 text-t14 text-fg" data-incomplete-part="filled">
            {item.filled.map((l) => (
              <li key={l.trade_id}>{sideWord(l.side)} {l.symbol} · {formatNumber(l.quantity, 0)} qty
                {l.entry_price !== null && <span className="text-fg-muted font-tabular"> · at {formatNumber(l.entry_price, 2)}</span>}
                <span className="text-fg-muted"> · {l.role === "hedge" ? "hedge" : "sold leg"}</span></li>
            ))}
          </ul>
        </div>
        <div>
          <p className="text-t13 font-semibold text-fg-muted">Not placed ({item.pending.length})</p>
          <ul className="space-y-space-1 text-t14 text-fg" data-incomplete-part="pending">
            {item.pending.map((l) => (
              <li key={`${l.symbol}-${l.side}`}>{sideWord(l.side)} {l.symbol} · {formatNumber(l.quantity, 0)} qty
                <span className="text-fg-muted"> · {l.role === "hedge" ? "hedge" : "sold leg"}</span></li>
            ))}
          </ul>
        </div>
      </div>
      {item.failure && <p className="text-t13 text-fg-muted">What stopped it: {item.failure}</p>}
      {waiting ? (
        <>
          <p className="flex items-center gap-space-2 text-t14 text-fg" data-incomplete-part="countdown">
            <Clock size={16} aria-hidden className="shrink-0 text-warn" />
            <span>
              <span className="font-semibold font-tabular" data-countdown>{countdownText(secondsLeft)}</span> left to choose. Then{" "}
              {keep ? "the filled legs stay open" : "the filled legs are closed, sold legs first"} (your setting).
            </span>
          </p>
          <div className="grid grid-cols-1 gap-space-2 sm:grid-cols-3">
            <button type="button" onClick={() => onAnswer("complete_rest")} disabled={busy} data-incomplete-action="complete_rest"
              className="min-h-11 rounded-control bg-brand px-space-4 text-t14 font-semibold text-on-brand disabled:opacity-50">
              {completeLabel(item.pending.length)}
            </button>
            <button type="button" onClick={() => onAnswer("close_filled")} disabled={busy} data-incomplete-action="close_filled"
              className="min-h-11 rounded-control border border-border bg-surface-1 px-space-4 text-t14 font-semibold text-fg disabled:opacity-50">
              {exitFilledLabel(item.filled.filter((l) => l.open).length || item.filled.length)}
            </button>
            <button type="button" onClick={() => onAnswer("keep")} disabled={busy} data-incomplete-action="keep"
              className="min-h-11 rounded-control border border-border bg-surface-1 px-space-4 text-t14 font-semibold text-fg disabled:opacity-50">
              Keep them
            </button>
          </div>
          <p className="text-t13 text-fg-muted">Closing goes sold legs first, then the hedges. Each leg can also be closed with Exit now.</p>
        </>
      ) : (
        <p className="text-t14 text-fg" data-incomplete-part="progress">{STATE_TEXT[item.state] ?? item.message}</p>
      )}
      {note && <p className="text-t13 text-fg" role="status">{note}</p>}
    </section>
  );
}

/** Positions: every incomplete order of his, refreshed every few seconds; the countdown ticks locally between reads. */
export default function IncompleteOrdersV2({ api = incompleteApi }: { api?: IncompleteApi }) {
  const [items, setItems] = useState<IncompleteItem[]>([]);
  const [readAt, setReadAt] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const load = useCallback(() => api.list().then((v) => { setItems(v.items); setReadAt(Date.now()); }).catch(() => undefined), [api]);  // intentional: polled; the list keeps its last read (with its time) and the next poll tries again
  useEffect(() => {
    void load();
    const poll = window.setInterval(() => { void load(); }, POLL_MS);
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
    return () => { window.clearInterval(poll); window.clearInterval(tick); };
  }, [load]);
  if (items.length === 0) return null;

  async function answer(groupId: string, action: IncompleteAction) {
    setBusy(groupId);
    try {
      const r = await api.answer(groupId, action);
      setNotes((n) => ({ ...n, [groupId]: r.message }));
      await load();
    } catch (e) {
      setNotes((n) => ({ ...n, [groupId]: `${e instanceof Error ? e.message : String(e)} Each leg can still be closed with Exit now.` }));
    } finally {
      setBusy(null);
    }
  }
  const elapsed = Math.max(0, Math.floor((now - readAt) / 1000));
  return (
    <div className="space-y-space-3" data-block="incomplete-orders">
      {items.map((item) => (
        <IncompleteCardV2 key={item.group_id} item={item} busy={busy === item.group_id} note={notes[item.group_id]}
          secondsLeft={item.seconds_left === null ? null : item.seconds_left - elapsed}
          onAnswer={(action) => { void answer(item.group_id, action); }} />
      ))}
    </div>
  );
}
