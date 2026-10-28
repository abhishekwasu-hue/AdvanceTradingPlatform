import { useState, type ReactNode } from "react";
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
  /** The short body line under the consequence (default: "You can add it again later."; null: none). */
  note?: ReactNode;
}

const BUTTON = "inline-flex h-11 items-center justify-center rounded-control px-space-4 text-t14 font-semibold transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:opacity-50 md:h-9";

export function ConfirmDialog({ open, onOpenChange, title, consequence, confirmLabel, onConfirm, busy = false, note = "You can add it again later." }: ConfirmDialogProps) {
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
      {note != null && <p className="text-t13 text-fg-muted">{note}</p>}
    </Dialog>
  );
}

/**
 * UI-28 U3.3 / U3.4 (NN/g H4 / H5): the in-app replacement for window.confirm - `await ask(...)` resolves true on the
 * confirm button, false on Cancel / Escape. Render `dialog` once in the component. (A panic "Exit now" keeps its own
 * confirm, UI-11.)
 */
export function useConfirm(): {
  ask: (title: string, consequence: ReactNode, confirmLabel: string) => Promise<boolean>;
  dialog: ReactNode;
} {
  const [state, setState] = useState<{ title: string; consequence: ReactNode; confirmLabel: string; resolve: (ok: boolean) => void } | null>(null);
  const ask = (title: string, consequence: ReactNode, confirmLabel: string) =>
    new Promise<boolean>((resolve) => setState({ title, consequence, confirmLabel, resolve }));
  const done = (ok: boolean) => { state?.resolve(ok); setState(null); };
  const dialog = state ? (
    <ConfirmDialog open onOpenChange={(o) => { if (!o) done(false); }} title={state.title} consequence={state.consequence}
      confirmLabel={state.confirmLabel} onConfirm={() => done(true)} note={null} />
  ) : null;
  return { ask, dialog };
}
