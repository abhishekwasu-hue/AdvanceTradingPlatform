import { ChartCandlestick, NotebookPen } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { CardV2, ChipV2, toneClass } from "../dashboard/CardV2";
import { signTone } from "../dashboard/model";
import { BUTTON_SECONDARY, FIELD } from "../trading/PartsV2";
import type { OHLCVBar } from "../types";
import { formatDateTimeIST, formatSignedINR } from "../ui/format";
import { CandleSnapshotV2 } from "./CandleSnapshotV2";
import {
  DRAFT_NOTE, NOTES_MAX, SNAPSHOT_MISSING, SNAPSHOT_NOTE, closedHow, journalApi, legLine, placedByLine, whenLine, type JournalApi, type JournalDraft,
  type JournalDrafts,
} from "./journal";
import { cropBars, snapshotWindow } from "./snapshot";

/**
 * F&O workspace 8b-k: the auto journal on the positions page (pure parts in journal.ts). Each closed PAPER F&O trade -
 * legs placed together as one - is a draft filled in from the facts and, when it came from one of his own signals, that
 * signal's structure words. He adds his notes (the existing notes field of the trades); saving changes nothing else.
 */
type State = { status: "loading" } | { status: "error" } | { status: "ok"; data: JournalDrafts };

export default function JournalDraftsV2({ journal = journalApi, refreshKey = 0 }: { journal?: JournalApi; refreshKey?: number }) {
  const [state, setState] = useState<State>({ status: "loading" });
  const [round, setRound] = useState(0);
  useEffect(() => {
    let alive = true;
    journal.drafts().then((data) => { if (alive) setState({ status: "ok", data }); }).catch(() => { if (alive) setState({ status: "error" }); });
    return () => { alive = false; };
  }, [journal, round, refreshKey]);
  return <JournalDraftsView state={state} candles={journal.candles}
    onSave={async (d, notes) => { await journal.saveNotes(d.trade_ids, notes); setRound((r) => r + 1); }} />;
}

type Candles = JournalApi["candles"];

/** Shown only once there is a draft: while loading, unreadable (the flags off, no PAPER account) or empty, the page
 * stays as it was - a closed F&O trade brings the card. */
export function JournalDraftsView({ state, onSave, candles }: { state: State; onSave: (d: JournalDraft, notes: string) => Promise<void>; candles?: Candles }) {
  const items = state.status === "ok" ? state.data.items : [];
  if (!items.length) return null;
  const drafts = items.filter((d) => d.status === "draft").length;
  return (
    <CardV2 title={<span className="inline-flex items-center gap-space-2"><NotebookPen size={18} aria-hidden className="text-brand" />Your F&amp;O journal</span>}
      count={items.length} testId="fno-journal">
      <div className="space-y-space-3" data-journal-drafts>
        <p className="text-t13 text-fg-muted">
          {drafts ? `${drafts} ${drafts === 1 ? "draft waits" : "drafts wait"} for your notes. ` : ""}{DRAFT_NOTE}
        </p>
        <ul className="space-y-space-3">
          {items.map((d) => <DraftV2 key={d.key} draft={d} onSave={onSave} candles={candles} />)}
        </ul>
      </div>
    </CardV2>
  );
}

function DraftV2({ draft, onSave, candles }: { draft: JournalDraft; onSave: (d: JournalDraft, notes: string) => Promise<void>; candles?: Candles }) {
  const [notes, setNotes] = useState(draft.notes ?? "");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ tone: "done" | "warn"; text: string } | null>(null);
  const id = useId();
  const how = closedHow(draft);
  async function save() {
    setBusy(true); setNote(null);
    try {
      await onSave(draft, notes.trim());
      setNote({ tone: "done", text: "Saved to your journal." });
    } catch {
      setNote({ tone: "warn", text: "Your notes were not saved. Try again." });
    } finally {
      setBusy(false);
    }
  }
  return (
    <li className="rounded-control border border-border px-space-3 py-space-3" data-draft={draft.key} data-draft-status={draft.status}>
      <div className="flex flex-wrap items-start justify-between gap-space-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-space-2">
            <span className="text-t15 font-semibold text-fg">{draft.title}</span>
            <ChipV2 kind="outline">Paper</ChipV2>
            <ChipV2 kind={draft.status === "draft" ? "solid" : "outline"}>{draft.status === "draft" ? "Draft" : "Noted"}</ChipV2>
          </div>
          <p className="mt-px text-t12 text-fg-muted">{whenLine(draft)} · {placedByLine(draft)}</p>
        </div>
        <div className="text-right">
          <div className="text-t12 text-fg-muted">P&amp;L</div>
          <div className={`font-tabular text-t15 font-semibold ${toneClass(signTone(draft.pnl))}`}>{formatSignedINR(draft.pnl)}</div>
        </div>
      </div>
      <ul className="mt-space-2 space-y-px font-tabular text-t13 text-fg" data-draft-legs>
        {draft.legs.map((l) => <li key={l.trade_id} className="break-words">{legLine(l)}</li>)}
      </ul>
      {how && <p className="mt-px text-t12 text-fg-muted">Closed: {how}</p>}
      {draft.signal && (
        <div className="mt-space-2 rounded-control bg-surface-2 px-space-3 py-space-2 text-t13" data-draft-signal>
          <p className="font-semibold text-fg">From your signal{draft.signal.setup ? `: ${draft.signal.setup}` : ""}</p>
          {draft.signal.words.length > 0 && (
            <ul className="mt-px list-disc pl-space-4 text-fg">{draft.signal.words.map((w) => <li key={w}>{w}</li>)}</ul>
          )}
        </div>
      )}
      {candles && <SnapshotV2 draft={draft} candles={candles} />}
      <label htmlFor={id} className="mt-space-3 block text-t12 font-medium text-fg-muted">Your notes</label>
      <textarea id={id} value={notes} maxLength={NOTES_MAX} rows={2} onChange={(e) => setNotes(e.target.value)} data-draft="notes"
        placeholder="What did you see, and how did you feel about it?" className={`${FIELD} mt-space-1 h-auto min-h-20 w-full py-space-2`} />
      <div className="mt-space-2 flex flex-wrap items-center gap-space-2">
        <button type="button" onClick={save} disabled={busy || notes.trim() === (draft.notes ?? "")} className={BUTTON_SECONDARY} data-draft="save">
          {busy ? "Saving…" : "Save notes"}
        </button>
        {note && <span role="status" className={`text-t13 ${note.tone === "done" ? "text-fg-muted" : "text-warn"}`}>{note.text}</span>}
      </div>
    </li>
  );
}

type Snap = { status: "idle" } | { status: "loading" } | { status: "error" } | { status: "ok"; bars: OHLCVBar[] };

/** 8b slice 4: the chart snapshot of the trade, read only when he asks for it (one candle read; nothing is stored). */
export function SnapshotV2({ draft, candles, now = Date.now() }: { draft: JournalDraft; candles: Candles; now?: number }) {
  const [snap, setSnap] = useState<Snap>({ status: "idle" });
  const opened = draft.opened_at ? Date.parse(draft.opened_at) : NaN;
  const closed = draft.closed_at ? Date.parse(draft.closed_at) : NaN;
  if (!Number.isFinite(opened) || !Number.isFinite(closed)) return null;
  const symbol = draft.spot_symbol ?? draft.underlying;
  async function show() {
    setSnap({ status: "loading" });
    const w = snapshotWindow(opened, closed, now);
    try {
      const bars = cropBars(await candles(symbol, draft.spot_exchange ?? "NSE", w), opened, closed, w.timeframe);
      setSnap(bars.length ? { status: "ok", bars } : { status: "error" });
    } catch {
      setSnap({ status: "error" });
    }
  }
  if (snap.status === "idle" || snap.status === "loading") {
    return (
      <button type="button" onClick={() => { void show(); }} disabled={snap.status === "loading"} data-draft-chart="show"
        className={`${BUTTON_SECONDARY} mt-space-2`}>
        <ChartCandlestick size={16} aria-hidden /> {snap.status === "loading" ? "Drawing the chart…" : "Show the chart"}
      </button>
    );
  }
  if (snap.status === "error") return <p className="mt-space-2 text-t13 text-fg-muted" data-draft-chart="missing">{SNAPSHOT_MISSING}</p>;
  const marks = [{ at: opened, label: "Opened" }, { at: closed, label: "Closed" }];
  return (
    <div className="mt-space-2 rounded-control border border-subtle px-space-2 py-space-2" data-draft-chart="shown">
      <p className="px-space-1 text-t12 font-semibold text-fg-muted">{symbol}: the chart while the trade was open</p>
      <CandleSnapshotV2 bars={snap.bars} marks={marks} testId={draft.key} title={`${symbol} chart for this trade`}
        summary={`${symbol} from before it opened (${formatDateTimeIST(opened)}) to after it closed (${formatDateTimeIST(closed)}).`} />
      <p className="px-space-1 text-t12 text-fg-muted">{SNAPSHOT_NOTE}</p>
    </div>
  );
}
