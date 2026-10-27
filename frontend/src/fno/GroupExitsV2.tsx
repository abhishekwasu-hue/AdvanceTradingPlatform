import { TriangleAlert } from "lucide-react";
import { useState } from "react";
import { Dialog } from "../components/primitives/Dialog";
import { Details } from "../ui/Details";
import { confirmCopy, groupExitApi, runExit, type GroupExitApi, type GroupExitAsk } from "./groupExits";

const BUTTON = "inline-flex h-11 items-center justify-center rounded-control px-space-4 text-t14 font-semibold transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:opacity-50 md:h-9";

/**
 * TT-8: the confirm sheet of "Exit this leg" / "Exit the whole group", in plain words. A hedge exited before its sold leg
 * shows the warning and "Exit this leg anyway" - a warning, never a block (ADR-0004). When the server finds the hedge
 * first (the page did not know yet), the sheet stays open with the server's own warning and asks again.
 * `onDone(text, ok)`: the line for the page's notice; the sheet closes.
 */
export function GroupExitSheetV2({ ask, onClose, onDone, api = groupExitApi }: {
  ask: GroupExitAsk | null; onClose: () => void; onDone: (text: string, ok: boolean) => void; api?: GroupExitApi;
}) {
  const [busy, setBusy] = useState(false);
  const [serverWarning, setServerWarning] = useState<string | null>(null);
  if (!ask) return null;
  const current: GroupExitAsk = ask.kind === "leg" && serverWarning ? { ...ask, warning: serverWarning } : ask;
  const copy = confirmCopy(current);
  const close = () => { setServerWarning(null); onClose(); };

  async function go() {
    if (!ask) return;
    setBusy(true);
    const step = await runExit(ask, serverWarning !== null, api);
    setBusy(false);
    if (step.kind === "warn") { setServerWarning(step.warning); return; }
    setServerWarning(null);
    onDone(step.text, step.ok);
  }

  return (
    <Dialog open onOpenChange={(o) => { if (!o) close(); }} title={copy.title}
      description={<span className="text-t14 text-fg" data-consequence>{copy.body}</span>}
      footer={(
        <>
          <button type="button" onClick={close} className={`${BUTTON} border border-border bg-surface-1 text-fg hover:bg-surface-2`}>
            Keep it open
          </button>
          <button type="button" onClick={go} disabled={busy} data-confirm data-testid="group-exit-confirm"
            className={`${BUTTON} bg-fg text-surface-1 hover:opacity-90`}>
            {copy.confirm}
          </button>
        </>
      )}>
      {copy.warning ? (
        <p className="flex items-start gap-space-2 rounded-control border border-border bg-surface-2 p-space-3 text-t14 text-fg" data-hedge-warning role="alert">
          <TriangleAlert size={16} aria-hidden className="mt-px shrink-0 text-fg-muted" />
          <span>{copy.warning}</span>
        </p>
      ) : (
        // F7 / UX-11 U1: the body stays one line (the consequence); the explanation goes into Details, once per dialog.
        <Details id="fno.group-exit" className="mt-0">
          <p>{current.kind === "leg" ? "The stop and target of the other legs keep working." : "If a leg has no price right now, nothing closes and you see why."}</p>
        </Details>
      )}
    </Dialog>
  );
}
