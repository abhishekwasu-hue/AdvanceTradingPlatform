import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { EvidenceView } from "../screener/EvidencePanel";
import sample from "../screener/evidenceSample.json";
import type { EvidencePack } from "../screener/evidence";

/** U8 EV1 - the evidence chart for one matched symbol (the backend's own pack for a range breakout with trend, volume
 * and a demand zone): only the screen's conditions are drawn, with the verify strip. Follows the toolbar's theme and
 * colour-blind setting. */
const meta: Meta = { title: "Screener/Evidence chart" };
export default meta;
type Story = StoryObj;

const pack = sample as unknown as EvidencePack;

function Interactive({ startFailed = false }: { startFailed?: boolean }) {
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [showFailed, setShowFailed] = useState(startFailed);
  return (
    <div className="max-w-5xl p-4">
      <EvidenceView pack={pack} hiddenBlocks={hidden} showFailed={showFailed} onShowFailed={setShowFailed}
                    onToggleBlock={(id) => setHidden((h) => { const n = new Set(h); if (n.has(id)) n.delete(id); else n.add(id); return n; })}
                    onSend={() => undefined} send={{ status: "idle" }} />
    </div>
  );
}

export const Matched: Story = { render: () => <Interactive /> };
export const WithFailedConditions: Story = { render: () => <Interactive startFailed /> };
