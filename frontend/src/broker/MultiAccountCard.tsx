import { useEffect, useState } from "react";
import { api } from "../api/client";
import { ApiError, friendlyError } from "../api/errors";
import type { MultiAccountRegistry, MultiAccountRow } from "../types";
import { formatDateTimeIST } from "../ui/format";
import { BTN_SECONDARY } from "./parts";

/**
 * M45 MA-1b (`ui_v2` + `multi_account`): "Primary and secondary accounts" on the Broker page's Advanced section - the
 * user's own broker accounts (never one another member linked), exactly one Primary across all brokers, Secondaries
 * with an optional group, each account's on / off state and broker login. Moving the Primary asks first. While an open
 * LIVE position has no account recorded, no account takes a role and the positions are listed. A registry only:
 * nothing here places, routes or sizes an order.
 */

const ROLE_TEXT: Record<string, string> = { PRIMARY: "Primary", SECONDARY: "Secondary" };

function accountName(a: MultiAccountRow): string {
  return a.display_name ?? `${a.broker_name} ${a.account_label}`;
}

function sessionText(a: MultiAccountRow): string {
  if (a.token_status === "VALID") return a.token_expires_at ? `Logged in until ${formatDateTimeIST(a.token_expires_at)}` : "Logged in";
  if (a.token_status === "EXPIRED") return "Login expired - log in again";
  if (a.token_status === "MISSING") return "No login stored";
  return "Login not checked yet";
}

function errorCode(e: unknown): string | null {
  if (!(e instanceof ApiError) || !e.body) return null;
  try {
    const parsed = JSON.parse(e.body) as { detail?: { code?: unknown } };
    return typeof parsed.detail?.code === "string" ? parsed.detail.code : null;
  } catch {
    return null;
  }
}

export default function MultiAccountCard() {
  const [registry, setRegistry] = useState<MultiAccountRegistry | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [confirmFor, setConfirmFor] = useState<number | null>(null);
  const [groups, setGroups] = useState<Record<number, string>>({});

  function refresh() {
    api.myAccounts()
      .then((r) => { setRegistry(r); setGroups(Object.fromEntries(r.accounts.map((a) => [a.id, a.group_name ?? ""]))); })
      .catch((e) => setError(friendlyError(e)));
  }
  useEffect(refresh, []);

  async function act(id: number, fn: () => Promise<unknown>) {
    setBusy(id); setError(null);
    try {
      await fn();
      setConfirmFor(null);
      refresh();
    } catch (e) {
      if (errorCode(e) === "confirm_primary") setConfirmFor(id);
      else setError(friendlyError(e));
    } finally {
      setBusy(null);
    }
  }

  const accounts = registry?.accounts ?? [];
  const blocking = registry?.unaccounted_live_trades ?? [];
  const primary = accounts.find((a) => a.role === "PRIMARY");

  return (
    <section aria-labelledby="multi-account-title" className="space-y-space-3" data-testid="multi-account-card">
      <h3 id="multi-account-title" className="text-t14 font-semibold text-fg">Primary and secondary accounts</h3>
      <p className="text-t12 text-fg-muted">
        Choose one Primary account and, if you like, Secondary accounts in groups. Only accounts you connected yourself
        are listed. This only records your choice; it does not place or copy any order. Trading several accounts needs a
        lawyer's clearance before real-money use.
      </p>
      {blocking.length > 0 && (
        <div role="status" className="rounded-control border border-warn/40 bg-warn/[0.06] p-space-3 text-t12 text-fg">
          You hold open real-money positions with no account recorded ({blocking.map((t) => t.symbol).join(", ")}). Close
          or reconcile them before giving an account a role. Their exits keep running as today.
        </div>
      )}
      {error && <div role="alert" className="text-t12 text-down">{error}</div>}
      {registry && accounts.length === 0 && (
        <div className="text-t12 text-fg-muted">No accounts you connected yourself yet. Connect a broker account first.</div>
      )}
      <ul className="space-y-space-2">
        {accounts.map((a) => (
          <li key={a.id} className="rounded-control border border-border p-space-3" data-testid={`multi-account-${a.id}`}>
            <div className="flex flex-wrap items-center gap-space-2">
              <span className="text-t14 font-semibold text-fg capitalize">{accountName(a)}</span>
              {a.role && (
                <span className={`rounded-full border px-space-2 py-px text-t12 font-semibold ${a.role === "PRIMARY" ? "border-brand/50 text-brand" : "border-border text-fg"}`}>
                  {ROLE_TEXT[a.role]}{a.role === "SECONDARY" && a.group_name ? ` · ${a.group_name}` : ""}
                </span>
              )}
              <span className={`text-t12 ${a.enabled ? "text-up" : "text-warn"}`}>{a.enabled ? "On" : "Off - no new entries"}</span>
            </div>
            <div className="mt-space-1 text-t12 text-fg-muted"><span className="capitalize">{a.broker_name}</span> · {sessionText(a)}</div>
            {confirmFor === a.id ? (
              <div className="mt-space-2 flex flex-wrap items-center gap-space-2 text-t12 text-fg">
                <span>Make this the Primary account? {primary ? `${accountName(primary)} becomes Secondary.` : ""}</span>
                <button type="button" className={BTN_SECONDARY} disabled={busy === a.id}
                        onClick={() => act(a.id, () => api.setMyAccountRole(a.id, "PRIMARY", true))}>Yes, make Primary</button>
                <button type="button" className={BTN_SECONDARY} onClick={() => setConfirmFor(null)}>Cancel</button>
              </div>
            ) : (
              <div className="mt-space-2 flex flex-wrap items-center gap-space-2">
                {a.role !== "PRIMARY" && (
                  <button type="button" className={BTN_SECONDARY} disabled={busy === a.id || blocking.length > 0}
                          onClick={() => act(a.id, () => api.setMyAccountRole(a.id, "PRIMARY"))}>Make Primary</button>
                )}
                {a.role !== "SECONDARY" && (
                  <button type="button" className={BTN_SECONDARY} disabled={busy === a.id || blocking.length > 0}
                          onClick={() => act(a.id, () => api.setMyAccountRole(a.id, "SECONDARY"))}>Make Secondary</button>
                )}
                {a.role && (
                  <button type="button" className={BTN_SECONDARY} disabled={busy === a.id}
                          onClick={() => act(a.id, () => api.setMyAccountRole(a.id, null))}>Remove role</button>
                )}
              </div>
            )}
            {a.role === "SECONDARY" && (
              <form className="mt-space-2 flex flex-wrap items-center gap-space-2"
                    onSubmit={(ev) => { ev.preventDefault(); void act(a.id, () => api.setMyAccountGroup(a.id, (groups[a.id] ?? "").trim() || null)); }}>
                <label htmlFor={`group-${a.id}`} className="text-t12 text-fg-muted">Group</label>
                <input id={`group-${a.id}`} value={groups[a.id] ?? ""} maxLength={50} placeholder="e.g. Family"
                       onChange={(ev) => setGroups((g) => ({ ...g, [a.id]: ev.target.value }))}
                       className="h-11 w-48 rounded-control border border-border bg-surface-1 px-space-3 text-t14 text-fg" />
                <button type="submit" className={BTN_SECONDARY} disabled={busy === a.id}>Save group</button>
              </form>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
