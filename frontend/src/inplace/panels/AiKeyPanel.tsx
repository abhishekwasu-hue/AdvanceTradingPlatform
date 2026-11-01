import { useRef } from "react";
import type { AiConnection } from "../../aiKeys/connect";
import ConnectAiPanelV2 from "../../aiKeys/ConnectAiPanelV2";
import { AiSection } from "../../settings/SettingsSectionV2";
import { useFeatures } from "../../ui/useFeatures";
import type { PanelProps } from "../registry";

const ready = (c: AiConnection | null) => !!(c?.connected && c.ok && c.consent?.accepted !== false);

/** NAV-1: the Connect AI panel in a modal. Opened while no key is connected, it closes by itself once a key is
 * connected and tested; opened with a key in place, it stays open (to see, fix or disconnect it).
 * NAV-2: without `ai_one_key` it shows what Settings › AI shows then (the key guides and today's AI cards), so the
 * Copilot's "Connect" and "AI on" chips open in place with the flag either way. */
export default function AiKeyPanel({ close }: PanelProps) {
  const before = useRef<boolean | null>(null);
  if (!useFeatures().on("ai_one_key")) return <AiSection />;
  return (
    <ConnectAiPanelV2 onChange={(c) => {
      const now = ready(c);
      if (before.current === false && now) close();
      before.current = now;
    }} />
  );
}
