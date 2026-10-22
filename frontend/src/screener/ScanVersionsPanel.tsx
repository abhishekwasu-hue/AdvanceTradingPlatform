/**
 * Handover 11j step 6 (flag `screener_copilot`, behind `ui_v2`): the versions of the trader's saved scan, newest first,
 * each with what changed from the one before. "Open this version" puts its blocks back in the builder; nothing is saved
 * until he presses Save. Kept on this device (see versions.ts).
 */
import { Button } from "../components/primitives";
import { changes, savedAt, type ScanVersion } from "./versions";

export interface ScanVersionsPanelProps {
  versions: ScanVersion[];
  onOpen: (v: ScanVersion) => void;
}

export default function ScanVersionsPanel({ versions, onOpen }: ScanVersionsPanelProps) {
  if (!versions.length) {
    return <p className="text-t13 text-fg-muted" data-testid="scan-versions-empty">Save the scan to start its history. Every save that changes it adds a version.</p>;
  }
  const rows = versions.map((v, i) => ({ v, n: i + 1, lines: changes(versions[i - 1], v) })).reverse();
  return (
    <div className="space-y-3 text-t13" data-testid="scan-versions">
      <ol className="space-y-2">
        {rows.map(({ v, n, lines }) => (
          <li key={`${n}-${v.at}`} className="rounded-panel border border-border bg-surface-1 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="font-semibold text-fg">
                Version {n}{n === versions.length && <span className="ml-2 text-t12 font-normal text-fg-muted">(latest)</span>}
              </p>
              <p className="text-t12 text-fg-muted">Saved {savedAt(v.at)} · {v.texts.length} {v.texts.length === 1 ? "condition" : "conditions"} · {v.base_tf}</p>
            </div>
            <ul className="mt-1 space-y-0.5">
              {lines.map((line) => <li key={line} className="text-t12 text-fg">{line}</li>)}
            </ul>
            {n !== versions.length && (
              <Button size="sm" className="mt-2" onClick={() => onOpen(v)} data-testid={`scan-version-open-${n}`}>Open this version</Button>
            )}
          </li>
        ))}
      </ol>
      <p className="text-t12 text-fg-muted">Kept on this device. Opening a version changes nothing until you save.</p>
    </div>
  );
}
