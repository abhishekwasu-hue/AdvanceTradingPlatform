import * as RD from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { Suspense, lazy, useEffect, useState } from "react";
import { useFeatures } from "../ui/useFeatures";
import { CONNECT_AI_EVENT } from "./connect";

// A lazy chunk: the shell carries only this listener; the panel and the key guides load when it first opens.
const ConnectAiPanelV2 = lazy(() => import("./ConnectAiPanelV2"));

/**
 * Settings redesign A1: the first time the user starts ANY AI feature without a key (the server answers
 * `ai_not_connected`), the Connect AI panel opens in place, over the page. Only with `ui_v2` and `ai_one_key` on.
 */
export default function ConnectAiHostV2() {
  const features = useFeatures();
  const on = features.on("ui_v2") && features.on("ai_one_key");
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!on) return;
    const show = () => setOpen(true);
    window.addEventListener(CONNECT_AI_EVENT, show);
    return () => window.removeEventListener(CONNECT_AI_EVENT, show);
  }, [on]);

  if (!on) return null;
  return (
    <RD.Root open={open} onOpenChange={setOpen}>
      <RD.Portal>
        <RD.Overlay className="fixed inset-0 z-40 bg-scrim/50" />
        <RD.Content aria-describedby={undefined} data-connect-ai-dialog
          className="fixed inset-x-0 bottom-0 z-50 max-h-[90dvh] overflow-y-auto rounded-t-panel bg-surface p-space-4 pb-[calc(1rem+env(safe-area-inset-bottom))] text-fg shadow-raised focus:outline-none md:inset-x-auto md:left-1/2 md:top-1/2 md:bottom-auto md:w-full md:max-w-lg md:-translate-x-1/2 md:-translate-y-1/2 md:rounded-panel">
          <div className="mb-space-2 flex items-center justify-between">
            <RD.Title className="text-t15 font-semibold text-fg">This needs AI</RD.Title>
            <RD.Close aria-label="Close" className="-mr-space-2 flex h-11 w-11 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg">
              <X size={18} aria-hidden />
            </RD.Close>
          </div>
          {open && (
            <Suspense fallback={null}>
              <ConnectAiPanelV2 onChange={(c) => { if (c?.connected && c.ok && c.consent?.accepted !== false) setOpen(false); }} />
            </Suspense>
          )}
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  );
}
