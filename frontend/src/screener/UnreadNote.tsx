/**
 * SCR-FIX B1: the one line a scan's answer shows when some symbols could not be read - "N matched · M could not be
 * read", the grouped reason in plain words, and ONE action (Log in / Connect broker / Retry / Try another timeframe).
 */
import { Button } from "../components/primitives";
import type { ReadAction, UnreadReason } from "./api";
import { ACTION_LABEL, countLine, type ReadTally } from "./readCounts";

export interface UnreadNoteProps {
  /** the answer's counts; null for a run-level error (nothing could be read) */
  tally: ReadTally | null;
  reason: Pick<UnreadReason, "text" | "action"> & { count?: number } | null;
  onAction: (action: ReadAction) => void;
  /** M49 U6: the page says "Matched N of M" once in its results header - this note then gives only the reason. */
  hideCount?: boolean;
}

export function UnreadNote({ tally, reason, onAction, hideCount = false }: UnreadNoteProps) {
  if (tally ? tally.unread <= 0 : !reason) return null;
  const count = reason?.count;
  return (
    <div role="status" className="rounded-panel border border-warn/50 bg-surface-1 p-3 text-t13 text-fg" data-testid="scan-unread">
      {tally && !hideCount && <p className="font-medium" data-testid="scan-unread-count">{countLine(tally)}</p>}
      {reason && (
        <p className="mt-1 text-fg-muted" data-testid="scan-unread-reason">
          {count != null && tally ? `${count} ${count === 1 ? "stock" : "stocks"}: ` : ""}{reason.text}
        </p>
      )}
      {reason && (
        <div className="mt-2">
          <Button size="sm" onClick={() => onAction(reason.action)} data-testid="scan-unread-action">{ACTION_LABEL[reason.action]}</Button>
        </div>
      )}
    </div>
  );
}

export default UnreadNote;
