import type { ReactNode } from "react";
import { Dialog } from "../components/primitives/Dialog";

/**
 * UI2-9a (UI v2 proposal §8 "Colour", §13 UI-11): the confirm step of a destructive action - "Remove", "Delete" - on v2
 * screens. The action itself is a neutral button; this dialog names the consequence in a sentence and asks again. Red
 * stays for price and P&L only, so the confirm button is the neutral filled one, never red and never brand blue.
 *
 * Not for "Exit now" on My positions: a panic exit keeps its existing confirm (and any step-up) unchanged (UI-11).
 */
export interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The question: "Remove this event?" */
  title: string;
  /** What changes once confirmed, in one or two sentences. */
  consequence: ReactNode;
  /** The confirm button's label: "Remove event". */
  confirmLabel: string;
  onConfirm: () => void;
  busy?: boolean;
}

const BUTTON = "inline-flex h-11 items-center justify-center rounded-control px-space-4 text-t14 font-semibold transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:opacity-50 md:h-9";

export function ConfirmDialog({ open, onOpenChange, title, consequence, confirmLabel, onConfirm, busy = false }: ConfirmDialogProps) {
  return (
    // The consequence is the dialog's description, so a screen reader reads it with the title (aria-describedby).
    <Dialog open={open} onOpenChange={onOpenChange} title={title}
      description={<span className="text-t14 text-fg" data-consequence>{consequence}</span>}
      footer={(
        <>
          <button type="button" onClick={() => onOpenChange(false)} className={`${BUTTON} border border-border bg-surface-1 text-fg hover:bg-surface-2`}>
            Cancel
          </button>
          <button type="button" onClick={onConfirm} disabled={busy} data-confirm
            className={`${BUTTON} bg-fg text-surface-1 hover:opacity-90`}>
            {confirmLabel}
          </button>
        </>
      )}>
      {/* A short body line, so the dialog has no empty padded box; the shared Dialog is left as it is for every other use. */}
      <p className="text-t13 text-fg-muted">You can add it again later.</p>
    </Dialog>
  );
}
