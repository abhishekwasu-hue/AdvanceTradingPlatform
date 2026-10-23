import { useEffect, useRef, useState } from "react";
import { setStepUpHandler } from "../api/client";
import { Dialog } from "./primitives/Dialog";
import StepUpDialog from "./StepUpDialog";

/**
 * A4: for a platform administrator only. When an admin change is refused because the last two-factor check is older than
 * the server's window, this asks for the authenticator code once and the change goes through (api/client.ts repeats the
 * request). Several refusals at the same moment share one question. Cancel leaves the change undone, with its message.
 */
export default function StepUpHost({ enabled }: { enabled: boolean }) {
  const [reason, setReason] = useState<string | null>(null);
  const pending = useRef<{ promise: Promise<boolean>; resolve: (ok: boolean) => void } | null>(null);

  useEffect(() => {
    if (!enabled) return;
    setStepUpHandler((why) => {
      if (pending.current) return pending.current.promise;
      let resolve: (ok: boolean) => void = () => {};
      const promise = new Promise<boolean>((r) => { resolve = r; });
      pending.current = { promise, resolve };
      setReason(why);
      return promise;
    });
    return () => {
      setStepUpHandler(null);
      pending.current?.resolve(false);
      pending.current = null;
    };
  }, [enabled]);

  function finish(ok: boolean) {
    const current = pending.current;
    pending.current = null;
    setReason(null);
    current?.resolve(ok);
  }

  if (!enabled || reason === null) return null;
  return (
    <Dialog open onOpenChange={(open) => { if (!open) finish(false); }} title="Confirm it's you">
      <StepUpDialog reason={reason} onVerified={() => finish(true)} onCancel={() => finish(false)} />
    </Dialog>
  );
}
