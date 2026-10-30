import { errorSentence } from "../api/errors";
import { useEffect, useState } from "react";
import { ListChecks, RefreshCw } from "lucide-react";
import { request } from "../api/client";
import { Card } from "./ui";
import { SkeletonRows } from "./primitives";
import { EMPTY, formatDateTimeIST } from "../ui/format";

/**
 * WL1 (handover item 4), the admin side of the read-only system watchlists: when they were last rebuilt, what changed
 * (lists created / changed / dropped / held, the symbols added and removed) and the symbols that could not be mapped to
 * the instrument master. "Refresh now" rebuilds them from the tables already held (no download). SUPER_ADMIN only on the
 * server (GET / POST /api/admin/system-watchlists).
 */
export interface SystemListChange { key: string; name: string; kind: "created" | "changed" | "dropped" | "held"; added: string[]; removed: string[]; reason?: string }
export interface SystemListUnmapped { list: string; symbol: string; reason: string }
export interface SystemListRun {
  id: number; started_at: string | null; finished_at: string | null; lists_total: number; lists_changed: number;
  changes: SystemListChange[]; unmapped_count: number; note: string | null; unmapped?: SystemListUnmapped[];
}
export interface SystemListsStatus {
  last_refresh: SystemListRun | null; history: SystemListRun[]; lists: number; groups: Record<string, number>;
  universe_sync_enabled: boolean; refresh_hour_ist: number;
}

export const systemListsAdminApi = {
  status: () => request<SystemListsStatus>("/admin/system-watchlists"),
  refresh: () => request<unknown>("/admin/system-watchlists/refresh", { method: "POST" }),
};

const GROUP_NAMES: Record<string, string> = { INDICES: "Indices", INDEX_STOCKS: "Index stocks", SECTORS: "Sectors", FNO: "F&O stocks" };
const KIND_TONE: Record<SystemListChange["kind"], string> = { created: "text-up", changed: "text-fg", dropped: "text-down", held: "text-warn" };
const SHOWN = 12;   // symbols shown per change before "+N more"

export function when(iso: string | null | undefined): string {
  if (!iso) return "never";
  const text = formatDateTimeIST(iso);                     // REV-1 M3: IST, never the device's zone
  return text === EMPTY ? iso : text;
}

function symbols(list: string[]): string {
  return list.length <= SHOWN ? list.join(", ") : `${list.slice(0, SHOWN).join(", ")} +${list.length - SHOWN} more`;
}

/** The card's body for a given status (pure, so it is tested without the network). */
export function SystemListsView({ status, onRefresh, busy = false }: { status: SystemListsStatus; onRefresh?: () => void; busy?: boolean }) {
  const last = status.last_refresh;
  const unmapped = last?.unmapped ?? [];
  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap items-center gap-3">
        <ListChecks size={16} className="text-fg-muted" />
        <span className="text-fg-muted">Last refresh: <span className="text-fg font-semibold">{when(last?.finished_at ?? last?.started_at)}</span></span>
        <span className="text-fg-muted">{status.lists} list{status.lists === 1 ? "" : "s"}
          {Object.keys(status.groups).length > 0 && ` (${Object.entries(status.groups).map(([g, n]) => `${GROUP_NAMES[g] ?? g} ${n}`).join(", ")})`}</span>
        {onRefresh && (
          <button type="button" disabled={busy} onClick={onRefresh}
            className="ml-auto inline-flex items-center gap-1 rounded border border-border px-2 py-1 text-xs text-fg hover:bg-surface-2 disabled:opacity-50">
            <RefreshCw size={12} /> Refresh now
          </button>
        )}
      </div>
      <p className="text-xs text-fg-muted">
        Rebuilt daily after {status.refresh_hour_ist}:00 IST with the market closed, from the index and classification files
        the universe sync holds (universe sync is {status.universe_sync_enabled ? "on" : "off"}).
      </p>
      {last?.note && <div className="text-xs text-warn">{last.note}</div>}

      <div>
        <h4 className="text-xs font-semibold text-fg mb-1">Changes in the last refresh ({last ? last.changes.length : 0})</h4>
        {!last || last.changes.length === 0 ? (
          <p className="text-xs text-fg-muted">No list changed.</p>
        ) : (
          <ul className="space-y-1 text-xs">
            {last.changes.map((c) => (
              <li key={`${c.key}-${c.kind}`} className="rounded border border-border/60 px-2 py-1">
                <span className={`font-semibold ${KIND_TONE[c.kind]}`}>{c.name}</span> <span className="text-fg-muted">{c.kind}</span>
                {c.added.length > 0 && <div className="text-up">Added: {symbols(c.added)}</div>}
                {c.removed.length > 0 && <div className="text-down">Removed: {symbols(c.removed)}</div>}
                {c.reason && <div className="text-fg-muted">{c.reason}</div>}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <h4 className="text-xs font-semibold text-fg mb-1">Unmapped symbols ({last?.unmapped_count ?? 0})</h4>
        {unmapped.length === 0 ? (
          <p className="text-xs text-fg-muted">Every symbol mapped to the instrument master.</p>
        ) : (
          <div className="max-h-64 overflow-auto rounded border border-border/60">
            <table className="w-full text-xs">
              <thead className="text-fg-muted text-left"><tr><th className="px-2 py-1">List</th><th className="px-2 py-1">Symbol</th><th className="px-2 py-1">Why</th></tr></thead>
              <tbody>
                {unmapped.map((u, i) => (
                  <tr key={`${u.list}-${u.symbol}-${i}`} className="border-t border-border/40">
                    <td className="px-2 py-1">{u.list}</td><td className="px-2 py-1 font-mono">{u.symbol}</td><td className="px-2 py-1 text-fg-muted">{u.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {status.history.length > 1 && (
        <div>
          <h4 className="text-xs font-semibold text-fg mb-1">Recent refreshes</h4>
          <ul className="text-xs text-fg-muted space-y-0.5">
            {status.history.map((r) => (
              <li key={r.id}>{when(r.finished_at ?? r.started_at)}: {r.lists_changed} of {r.lists_total} lists changed, {r.unmapped_count} unmapped{r.note ? ` (${r.note})` : ""}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function SystemListsCard() {
  const [status, setStatus] = useState<SystemListsStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = () => { systemListsAdminApi.status().then((s) => { setStatus(s); setError(null); }, (e) => setError(errorSentence(e))); };
  useEffect(load, []);
  const refresh = async () => {
    setBusy(true);
    try { await systemListsAdminApi.refresh(); load(); } catch (e) { setError(errorSentence(e)); } finally { setBusy(false); }
  };
  return (
    <Card title="System watchlists">
      {error && <div className="text-xs text-down mb-2">{error}</div>}
      {status ? <SystemListsView status={status} onRefresh={refresh} busy={busy} /> : !error && <SkeletonRows rows={2} label="Loading" />}
    </Card>
  );
}
