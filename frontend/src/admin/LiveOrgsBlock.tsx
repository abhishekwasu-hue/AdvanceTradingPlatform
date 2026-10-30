import { useState } from "react";

/**
 * M55 S1b: "LIVE for organisations" - the organisations the owner granted LIVE. LIVE runs for one only when the LIVE
 * switch is on, the PAPER-only lock is off and it is on this list. The owner grants / revokes behind the typed
 * confirmation; everyone else sees the list read-only. Nothing is granted by default.
 */
export interface LiveOrgsBlockProps {
  orgs: { id: number; name: string }[];
  canChange: boolean;
  confirmText: string;
  busy: boolean;
  onGrant?: (tenantId: number, granted: boolean, confirm: string) => void;
}

export default function LiveOrgsBlock({ orgs, canChange, confirmText, busy, onGrant }: LiveOrgsBlockProps) {
  const [orgId, setOrgId] = useState("");
  const [typed, setTyped] = useState("");
  const confirmed = typed === confirmText;
  const id = Number(orgId.trim());
  const validId = Number.isInteger(id) && id > 0;
  const editable = canChange && !!onGrant;
  return (
    <div data-live-orgs className="space-y-1 pt-1 text-fg-muted">
      <div className="font-semibold text-fg">LIVE for organisations</div>
      {orgs.length === 0 ? (
        <div data-live-orgs-empty>No organisation has LIVE.</div>
      ) : (
        <ul className="space-y-0.5">
          {orgs.map((org) => (
            <li key={org.id} data-live-org={org.id} className="flex flex-wrap items-center gap-2">
              <span className="text-fg">{org.name}</span>
              <span>#{org.id}</span>
              {editable && (
                <button type="button" disabled={busy || !confirmed} onClick={() => onGrant?.(org.id, false, typed)}
                  className="rounded border border-border px-2 py-0.5 text-fg hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-50">
                  Revoke
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {editable ? (
        <div className="flex flex-wrap items-center gap-2">
          <input aria-label="Organisation id to grant LIVE" placeholder="Organisation id" data-live-org-id
            className="w-28 rounded border border-border bg-surface-2 px-2 py-1 text-fg" value={orgId}
            onChange={(e) => setOrgId(e.target.value)} />
          <input aria-label={`Type ${confirmText} to confirm`} placeholder={`Type ${confirmText}`} data-live-org-confirm
            className="w-32 rounded border border-border bg-surface-2 px-2 py-1 text-fg" value={typed}
            onChange={(e) => setTyped(e.target.value)} />
          <button type="button" disabled={busy || !confirmed || !validId} onClick={() => onGrant?.(id, true, typed)}
            className="rounded border border-down/40 px-2 py-0.5 text-down hover:bg-down/10 disabled:cursor-not-allowed disabled:opacity-50">
            Grant
          </button>
        </div>
      ) : (
        <div data-live-orgs-readonly>Only the owner can grant or revoke LIVE.</div>
      )}
    </div>
  );
}
