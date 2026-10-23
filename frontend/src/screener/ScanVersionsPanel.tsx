/**
 * Handover 11j step 6 (flag `screener_copilot`, behind `ui_v2`): the versions of the trader's saved scan, newest first,
 * each with what changed from the one before. "Open this version" puts its blocks back in the builder; nothing is saved
 * until he presses Save, and that save is a new version (no version is ever changed). LAB-18: kept with his account on
 * the server (at most `max` per scan); the history this device kept before was uploaded once.
 */
import { Button } from "../components/primitives";
import type { ServerVersion } from "./api";
import { savedAt } from "./versions";

export interface ScanVersionsPanelProps {
  versions: ServerVersion[];
  onOpen: (v: ServerVersion) => void;
  max?: number;
  opening?: number | null;  // the version being loaded
}

export default function ScanVersionsPanel({ versions, onOpen, max = 50, opening = null }: ScanVersionsPanelProps) {
  if (!versions.length) {
    return <p className="text-t13 text-fg-muted" data-testid="scan-versions-empty">Save the scan to start its history. Every save that changes it adds a version.</p>;
  }
  const rows = [...versions].reverse();
  return (
    <div className="space-y-3 text-t13" data-testid="scan-versions">
      <ol className="space-y-2">
        {rows.map((v) => (
          <li key={v.version} className="rounded-panel border border-border bg-surface-1 p-3" data-testid={`scan-version-${v.number}`}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="font-semibold text-fg">
                Version {v.number}{v.latest && <span className="ml-2 text-t12 font-normal text-fg-muted">(latest)</span>}
              </p>
              <p className="text-t12 text-fg-muted">
                {v.created_at ? `Saved ${savedAt(v.created_at)} · ` : ""}{v.conditions} {v.conditions === 1 ? "condition" : "conditions"} · {v.base_tf}
              </p>
            </div>
            <ul className="mt-1 space-y-0.5">
              {v.change.map((line) => <li key={line} className="text-t12 text-fg">{line}</li>)}
            </ul>
            {!v.latest && (
              <Button size="sm" className="mt-2" onClick={() => onOpen(v)} loading={opening === v.version} disabled={opening != null}
                      data-testid={`scan-version-open-${v.number}`}>Open this version</Button>
            )}
          </li>
        ))}
      </ol>
      <p className="text-t12 text-fg-muted">
        Kept with your account, the newest {max} for each scan. Opening a version changes nothing until you save; saving adds it as the newest version.
      </p>
    </div>
  );
}
