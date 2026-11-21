import { Pin, PinOff, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { CardV2, EmptyStateV2, LoadingRowsV2 } from "../dashboard/CardV2";
import { BUTTON_PRIMARY, BUTTON_SECONDARY, FIELD, LABEL } from "../trading/PartsV2";
import { chipClass } from "../ui/Chip";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { ErrorState } from "../ui/ErrorState";
import { formatDateIST } from "../ui/format";
import type { Load } from "./ReviewsV2";
import type { EntryKind, ProofFact, ProofItem, ProofPage } from "./types";

/**
 * P2-8 J4: "My proof" - facts of good process from his own trades (stop kept, plan followed on a loss, waited for his
 * setup, no hurry after a loss, exit as planned, streaks, a dip recovered without a rule-break tag), each with its date
 * and trade, plus entries he writes himself. He pins the ones that matter (pinned first). Facts only: no praise text,
 * no performance claim.
 */

export const PROOF_KIND: Record<string, string> = {
  stop_kept: "Stop kept", plan_on_loss: "Process win", waited_setup: "Waited", no_revenge: "Calm after a loss",
  exit_as_planned: "Exit as planned", streak: "Streak", recovered: "Recovered", own: "Your entry",
};

/** "Your last good decision" (Today and the top of the Journal). */
export function LastGoodV2({ fact, afterLoss, onOpen }: { fact: ProofFact | null; afterLoss: string | null; onOpen?: (kind: EntryKind, id: number) => void }) {
  if (!fact && !afterLoss) return null;
  return (
    <CardV2 title="Your last good decision" testId="last-good" tour="journal-last-good">
      <div className="space-y-space-2">
        {fact && (
          <p className="text-t14 text-fg" data-last-good>
            {fact.text}{fact.at && <span className="ml-space-2 text-t12 text-fg-muted">{formatDateIST(fact.at)}</span>}
            {onOpen && fact.entry_kind && fact.entry_id != null && (
              <button type="button" className="ml-space-2 text-t13 text-brand hover:underline" onClick={() => onOpen(fact.entry_kind as EntryKind, fact.entry_id as number)}>Open</button>
            )}
          </p>
        )}
        {afterLoss && <p className="text-t14 text-fg-muted" data-after-loss>{afterLoss}</p>}
      </div>
    </CardV2>
  );
}

function ProofRow({ item, onPin, onRemove, onOpen }: {
  item: ProofItem; onPin: (item: ProofItem) => void; onRemove: (item: ProofItem) => void; onOpen: (kind: EntryKind, id: number) => void;
}) {
  return (
    <li className="flex items-start justify-between gap-space-3 border-b border-subtle py-space-2 last:border-0" data-proof={item.key}>
      <span className="min-w-0">
        <span className="flex flex-wrap items-center gap-space-2">
          <span className={chipClass({ tone: item.own ? "info" : "up", variant: "tint" })}>{PROOF_KIND[item.kind] ?? item.kind}</span>
          {item.at && <span className="font-tabular text-t12 text-fg-muted">{formatDateIST(item.at)}</span>}
        </span>
        <span className="mt-px block text-t14 text-fg">{item.text}</span>
        {item.entry_kind && item.entry_id != null && (
          <button type="button" className="text-t13 text-brand hover:underline" onClick={() => onOpen(item.entry_kind as EntryKind, item.entry_id as number)}>Open the trade</button>
        )}
      </span>
      <span className="flex shrink-0 gap-space-1">
        <button type="button" className={BUTTON_SECONDARY} aria-pressed={item.pinned} aria-label={item.pinned ? "Unpin" : "Pin"} onClick={() => onPin(item)} data-proof-pin>
          {item.pinned ? <PinOff size={16} aria-hidden /> : <Pin size={16} aria-hidden />}
        </button>
        {item.own && (
          <button type="button" className={BUTTON_SECONDARY} aria-label="Remove" onClick={() => onRemove(item)} data-proof-remove><Trash2 size={16} aria-hidden /></button>
        )}
      </span>
    </li>
  );
}

export function ProofV2({ state, onRetry, onPin, onAdd, onRemove, onOpen }: {
  state: Load<ProofPage>; onRetry: () => void; onPin: (item: ProofItem) => void; onAdd: (text: string, day?: string) => Promise<boolean>;
  onRemove: (item: ProofItem) => Promise<void>; onOpen: (kind: EntryKind, id: number) => void;
}) {
  const [text, setText] = useState("");
  const [day, setDay] = useState("");
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState<ProofItem | null>(null);
  return (
    <div className="space-y-space-4">
      {state.status === "ok" && <LastGoodV2 fact={state.data.last_good} afterLoss={state.data.after_loss} onOpen={onOpen} />}
      <CardV2 title="My proof" testId="my-proof" tour="journal-proof" count={state.status === "ok" ? state.data.items.length : undefined}>
        <div className="space-y-space-4">
          {state.status === "loading" && <LoadingRowsV2 rows={4} />}
          {state.status === "error" && <ErrorState message={state.error} onRetry={onRetry} variant="inline" />}
          {state.status === "ok" && state.data.items.length === 0 && (
            <EmptyStateV2 text={`No facts yet from the last ${state.data.days} days. They appear as you trade.`} />
          )}
          {state.status === "ok" && state.data.items.length > 0 && (
            <ul data-proof-list>{state.data.items.map((i) => <ProofRow key={i.key} item={i} onPin={onPin} onRemove={setRemoving} onOpen={onOpen} />)}</ul>
          )}
          <div className="grid gap-space-2 md:grid-cols-[1fr_auto_auto] md:items-end" data-proof-add>
            <label className="min-w-0">
              <span className={LABEL}>Add your own</span>
              <input className={FIELD} value={text} maxLength={300} placeholder="e.g. skipped a weak setup" onChange={(e) => setText(e.target.value)} data-proof-text />
            </label>
            <label className="min-w-0">
              <span className={LABEL}>Date</span>
              <input className={FIELD} type="date" value={day} onChange={(e) => setDay(e.target.value)} data-proof-day />
            </label>
            <button type="button" className={BUTTON_PRIMARY} disabled={busy || !text.trim()} data-proof-save
              onClick={async () => { setBusy(true); try { if (await onAdd(text, day || undefined)) { setText(""); setDay(""); } } finally { setBusy(false); } }}>
              <Plus size={16} aria-hidden />Add
            </button>
          </div>
        </div>
      </CardV2>
      <ConfirmDialog open={removing !== null} onOpenChange={(o) => { if (!o) setRemoving(null); }} title="Remove this entry?"
        consequence="Your own entry is removed from My proof." confirmLabel="Remove" busy={busy}
        onConfirm={async () => { if (removing) { setBusy(true); try { await onRemove(removing); } finally { setBusy(false); setRemoving(null); } } }} />
    </div>
  );
}
