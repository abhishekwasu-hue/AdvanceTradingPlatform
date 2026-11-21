import { CardV2 } from "../dashboard/CardV2";
import { BUTTON_SECONDARY } from "../trading/PartsV2";
import { formatDateTimeIST } from "../ui/format";
import type { EntryKind, WaitingCheckin } from "./types";

/**
 * P2-8 J2: the prompt at OPEN and at CLOSE - his entries waiting for the 20-second check-in, at the top of the Trades
 * tab. One tap opens the entry; "Not now" puts that prompt away. It never blocks anything; an empty list shows nothing.
 */
export function WaitingCheckinsV2({ items, onOpen, onDismiss }: {
  items: readonly WaitingCheckin[]; onOpen: (kind: EntryKind, id: number) => void; onDismiss: (w: WaitingCheckin) => void;
}) {
  if (items.length === 0) return null;
  return (
    <CardV2 title="20-second check-in" testId="waiting-checkins" count={items.length}>
      <ul className="space-y-space-2">
        {items.map((w) => (
          <li key={`${w.kind}-${w.id}-${w.moment}`} className="flex flex-wrap items-center justify-between gap-space-2" data-waiting={`${w.kind}-${w.id}`}>
            <span className="min-w-0">
              <span className="block truncate text-t14 font-semibold text-fg">{w.symbol}</span>
              <span className="text-t12 text-fg-muted">{w.moment === "open" ? "Opened" : "Closed"} {formatDateTimeIST(w.at)}</span>
            </span>
            <span className="flex gap-space-2">
              <button type="button" className={BUTTON_SECONDARY} onClick={() => onOpen(w.kind, w.id)} data-waiting-open>Check in</button>
              <button type="button" className={BUTTON_SECONDARY} onClick={() => onDismiss(w)} data-waiting-dismiss>Not now</button>
            </span>
          </li>
        ))}
      </ul>
    </CardV2>
  );
}
