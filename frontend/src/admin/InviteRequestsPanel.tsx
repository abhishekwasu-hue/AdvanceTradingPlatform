import { useCallback, useEffect, useState } from "react";
import { request } from "../api/client";
import { friendlyError } from "../api/errors";
import { SkeletonRows } from "../components/primitives";
import { formatDateIST, formatTimeIST } from "../ui/format";
import { BUTTON, Panel, Pill } from "./AdminSectionsV2";

/**
 * M95 PR-3 (M95-Q3): Admin › Users & business › Invite requests - the "Ask for an invite" requests from the sign-in
 * screen while sign-up is closed, newest first. The admin sends the invite the usual way and marks the request invited,
 * or dismisses it. Nothing here sends an e-mail.
 */

export type InviteRequestStatus = "new" | "invited" | "dismissed";
export interface InviteRequestItem {
  id: number; email: string; note: string | null; status: InviteRequestStatus; created_at: string;
  handled_by: string | null; handled_at: string | null;
}
export interface InviteRequestList { counts: Record<InviteRequestStatus, number>; requests: InviteRequestItem[] }

type Filter = InviteRequestStatus | "all";
const FILTERS: { id: Filter; label: string }[] = [
  { id: "new", label: "New" }, { id: "invited", label: "Invited" }, { id: "dismissed", label: "Dismissed" }, { id: "all", label: "All" },
];
const TONE = { new: "warn", invited: "ok", dismissed: "off" } as const;
const LABEL: Record<InviteRequestStatus, string> = { new: "New", invited: "Invited", dismissed: "Dismissed" };

export const inviteRequestsApi = {
  list: (filter: Filter) => request<InviteRequestList>(`/admin/invite-requests?status=${filter}`),
  mark: (id: number, status: "invited" | "dismissed") =>
    request<InviteRequestItem>(`/admin/invite-requests/${id}`, { method: "PATCH", body: JSON.stringify({ status }) }),
};

const when = (iso: string | null) => (iso ? `${formatDateIST(iso)} ${formatTimeIST(iso)}` : "-");

/** One request: the address, the status, the note, when it came (and who handled it); the two marks while it is new. */
export function InviteRequestRow({ r, busy, onMark }: { r: InviteRequestItem; busy: boolean; onMark: (status: "invited" | "dismissed") => void }) {
  return (
    <li data-invite-request-row={r.id} className="flex flex-col gap-space-2 py-space-2 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-space-2">
          <span className="break-all font-medium text-fg">{r.email}</span>
          <Pill tone={TONE[r.status]}>{LABEL[r.status]}</Pill>
        </div>
        {r.note && <p className="mt-space-1 whitespace-pre-wrap break-words text-fg">{r.note}</p>}
        <p className="mt-space-1 text-fg-muted">
          Asked {when(r.created_at)}{r.handled_at ? ` · ${LABEL[r.status].toLowerCase()} ${when(r.handled_at)}${r.handled_by ? ` by ${r.handled_by}` : ""}` : ""}
        </p>
      </div>
      {r.status === "new" && (
        <div className="flex shrink-0 gap-space-2">
          <button type="button" className={BUTTON} disabled={busy} onClick={() => onMark("invited")}>Mark invited</button>
          <button type="button" className={BUTTON} disabled={busy} onClick={() => onMark("dismissed")}>Dismiss</button>
        </div>
      )}
    </li>
  );
}

export default function InviteRequestsPanel() {
  const [filter, setFilter] = useState<Filter>("new");
  const [data, setData] = useState<InviteRequestList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  const load = useCallback(() => {
    setError(null);
    inviteRequestsApi.list(filter).then(setData, (e) => setError(friendlyError(e)));
  }, [filter]);
  useEffect(load, [load]);

  async function mark(id: number, status: "invited" | "dismissed") {
    setBusy(id);
    setError(null);
    try {
      await inviteRequestsApi.mark(id, status);
      load();
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Panel title="Invite requests" id="invite-requests"
      note="People who asked for an invite on the sign-in screen while sign-up is closed. Send the invite the usual way (a team invite link or a beta invite code), then mark the request invited. Nothing here sends an e-mail.">
      <div className="mb-space-3 flex flex-wrap gap-space-2" role="group" aria-label="Show requests">
        {FILTERS.map((f) => (
          <button key={f.id} type="button" aria-pressed={filter === f.id} onClick={() => setFilter(f.id)}
            className={`${BUTTON} ${filter === f.id ? "border-brand text-brand" : ""}`}>
            {f.label}{data && f.id !== "all" ? ` (${data.counts[f.id] ?? 0})` : ""}
          </button>
        ))}
      </div>
      {error && <p role="alert" className="mb-space-2 text-t12 text-down">{error}</p>}
      {!data ? (!error && <SkeletonRows rows={2} label="Loading" />) : data.requests.length === 0 ? (
        <p className="text-t12 text-fg-muted">No requests here.</p>
      ) : (
        <ul className="divide-y divide-subtle text-t12" data-invite-requests>
          {data.requests.map((r) => (
            <InviteRequestRow key={r.id} r={r} busy={busy === r.id} onMark={(status) => mark(r.id, status)} />
          ))}
        </ul>
      )}
    </Panel>
  );
}
