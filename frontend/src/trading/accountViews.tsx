import { useId } from "react";
import type { TradeRecord } from "../types";

/**
 * M45 MB-7 (docs/design/M45_MULTI_ACCOUNT.md, flags `ui_v2` + `multi_account`): the simulated PAPER account of each open
 * position as a small badge, and "All accounts / <each label>" as a filter. The server sends `account_label` only with
 * both flags on and only for the caller's own accounts; with no labelled row nothing here shows, so the page is exactly
 * as before.
 */

/** The distinct account labels among these positions, in a stable (alphabetical) order. */
export function accountLabels(list: readonly TradeRecord[] | null | undefined): string[] {
  const seen = new Set<string>();
  for (const p of list ?? []) if (p.account_label) seen.add(p.account_label);
  return [...seen].sort((a, b) => a.localeCompare(b));
}

/** Whether a position shows under the chosen account (`null`: all accounts). */
export function inAccount(p: TradeRecord | undefined, account: string | null): boolean {
  return account === null || p?.account_label === account;
}

/** A 44px select (never shrinks on wide screens), tokens only. */
const SELECT = "h-11 min-w-0 rounded-control border border-border bg-surface-2 px-space-3 text-t14 text-fg focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand";

export function AccountFilterV2({ labels, value, onChange }: {
  labels: readonly string[]; value: string | null; onChange: (account: string | null) => void;
}) {
  const id = useId();
  if (labels.length === 0) return null;
  return (
    <div className="mb-space-3 flex flex-wrap items-center gap-space-2" data-testid="account-filter">
      <label htmlFor={id} className="text-t13 text-fg-muted">Account</label>
      <select id={id} className={SELECT} value={value ?? ""} onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}>
        <option value="">All accounts</option>
        {labels.map((l) => <option key={l} value={l}>{l}</option>)}
      </select>
    </div>
  );
}
