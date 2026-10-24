import type { StoredBrokerInfo } from "../types";
import { formatDateTimeIST } from "../ui/format";

/** The label a stored credential row acts on: rows saved before accounts existed are the primary one. */
export const rowLabel = (s: StoredBrokerInfo) => s.account_label ?? "primary";

/** "Connected brokers" on Settings: one row per stored broker account. Each row's Authenticate and Remove act on
 * that row's own account (broker + label), never on the primary account of the same broker. */
export default function StoredBrokerList({ stored, busy, onAuthenticate, onDelete }: {
  stored: StoredBrokerInfo[];
  busy: boolean;
  onAuthenticate: (broker: string, label: string) => void;
  onDelete: (broker: string, label: string) => void;
}) {
  if (stored.length === 0) return <div className="text-sm text-fg-muted py-2">None stored yet.</div>;
  return (
    <div className="space-y-1.5">
      {stored.map((s) => (
        <div key={`${s.broker_name}/${rowLabel(s)}`} className="flex items-center justify-between rounded border border-border px-3 py-2 text-sm">
          <div>
            <span className="font-medium text-fg capitalize">{s.broker_name}</span>{" "}
            <span className="text-xs text-fg-muted">/ {rowLabel(s)}</span>{" "}
            <span className="text-fg-muted text-xs">updated {formatDateTimeIST(s.updated_at)}</span>
          </div>
          <div className="flex gap-3">
            <button onClick={() => onAuthenticate(s.broker_name, rowLabel(s))} disabled={busy} className="text-xs text-brand hover:underline disabled:opacity-50">
              Authenticate
            </button>
            <button onClick={() => onDelete(s.broker_name, rowLabel(s))} className="text-xs text-down hover:underline">
              Remove
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
