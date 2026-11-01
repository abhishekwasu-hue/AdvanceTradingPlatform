import { useRef } from "react";
import type { AiConnection } from "../../aiKeys/connect";
import ConnectAiPanelV2 from "../../aiKeys/ConnectAiPanelV2";
import type { PanelProps } from "../registry";

const ready = (c: AiConnection | null) => !!(c?.connected && c.ok && c.consent?.accepted !== false);

/** NAV-1: the Connect AI panel in a modal. Opened while no key is connected, it closes by itself once a key is
 * connected and tested; opened with a key in place, it stays open (to see, fix or disconnect it). */
export default function AiKeyPanel({ close }: PanelProps) {
  const before = useRef<boolean | null>(null);
  return (
    <ConnectAiPanelV2 onChange={(c) => {
      const now = ready(c);
      if (before.current === false && now) close();
      before.current = now;
    }} />
  );
}
