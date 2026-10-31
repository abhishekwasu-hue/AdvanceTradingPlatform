import { Search } from "lucide-react";
import { useId, useState } from "react";
import { Card } from "../components/ui";
import type { FeatureFlags } from "../types";
import { FLAG_FILTERS, groupFlags, statusTone, type FlagFilter } from "./flagList";
import LiveOrgsBlock from "./LiveOrgsBlock";

/**
 * UX1 (audit section 7, handover 8b): the admin Feature flags list - grouped by area, with a search box. Each row: the
 * plain description, the flag name, default, what turns on, what it needs, the status (ready / behind the scenes /
 * unfinished / owner decision) and the switch. A flag whose dependency is off cannot be turned on here ("Needs ...
 * first"); turning one off always works. ADM-1 (owner 30 Oct): finer categories, each folded with "N of M on"; a filter
 * (On / Off / Needs setup / Owner decision); the flag name, default, needs, note and allow-list fold into Details. Admin-only: the list is served only to a SUPER_ADMIN with two-factor.
 */
export interface FeatureFlagsCardProps {
  flags: FeatureFlags | null;
  tenants: Record<string, string>;
  onTenants: (name: string, value: string) => void;
  busy: boolean;
  /** Turn a flag on or off (off with the allow-list typed for it); `confirm` is the owner's typed LIVE confirmation. */
  onSet: (name: string, on: boolean, confirm?: string) => void;
  /** Save the allow-list of an OFF flag. */
  onSaveList: (name: string) => void;
  /** M55 S1b: the owner grants (or revokes) LIVE for one organisation, with the typed confirmation. */
  onGrantLive?: (tenantId: number, granted: boolean, confirm: string) => void;
}

const TONES = {
  up: "border-up/40 text-up",
  warn: "border-warn/40 bg-warn-tint text-warn",
  brand: "border-brand/40 text-brand",
  muted: "border-border text-fg-muted",
} as const;

export default function FeatureFlagsCard({ flags, tenants, onTenants, busy, onSet, onSaveList, onGrantLive }: FeatureFlagsCardProps) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<FlagFilter>("all");
  const [typed, setTyped] = useState<Record<string, string>>({});
  const searchId = useId();
  const groups = flags ? groupFlags(flags, query, filter) : [];
  const all = flags ? Object.values(flags) : [];
  // ADM-1: each category is folded; a search or a filter opens every category that still has a row.
  const narrowed = query.trim() !== "" || filter !== "all";
  return (
    <Card title="Feature flags">
      <p className="mb-3 text-xs text-fg-muted" data-flag-summary>
        {all.filter((f) => f.on).length} of {all.length} switches on, in {flags ? groupFlags(flags).length : 0} categories.
      </p>
      <details className="mb-3 text-xs text-fg-muted">
        <summary className="cursor-pointer">How flags work</summary>
        <p className="mt-1">Open a category to change a switch; Details under each switch says what it does.</p>
        <p className="mt-1">
          Turning a flag off with an organisation allow-list keeps it on for those organisations only (staged rollouts,
          beta access). Exits and PAPER trading are never behind a flag. A trader never sees a flag name: a page that is
          off says "Coming soon". Status is the audit judgement - try each flag on staging first.
        </p>
      </details>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <label htmlFor={searchId} className="sr-only">Search flags</label>
        <div className="relative w-full max-w-sm">
          <Search size={14} aria-hidden className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-fg-muted" />
          <input id={searchId} type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search flags"
            className="w-full rounded border border-border bg-surface-2 py-1 pl-7 pr-2 text-xs text-fg" />
        </div>
        <div role="group" aria-label="Show" className="flex flex-wrap gap-1" data-flag-filters>
          {FLAG_FILTERS.map((f) => (
            <button key={f.id} type="button" aria-pressed={filter === f.id} onClick={() => setFilter(f.id)} data-flag-filter={f.id}
              className={`rounded-full border px-2 py-0.5 text-xs ${filter === f.id ? "border-brand bg-brand text-on-brand" : "border-border text-fg-muted hover:text-fg"}`}>
              {f.label}
            </button>
          ))}
        </div>
      </div>
      {flags && groups.length === 0 && <p className="text-xs text-fg-muted">No flag matches{query.trim() ? ` "${query}"` : " this filter"}.</p>}
      <div className="divide-y divide-border border-y border-border">
        {groups.map((group) => (
          <details key={`${group.area}-${narrowed}`} open={narrowed} aria-label={group.label} data-flag-area={group.area} className="group/area">
            <summary className="flex cursor-pointer items-center justify-between gap-2 py-2 text-xs">
              <span className="font-semibold text-fg">{group.label}</span>
              <span className="shrink-0 whitespace-nowrap text-fg-muted" data-flag-area-count>{group.rows.filter((r) => r.flag.on).length} of {group.rows.length} on</span>
            </summary>
            <ul className="mb-2 divide-y divide-border border-t border-border">
              {group.rows.map(({ name, flag, blockedBy }) => {
                const needs = [...(flag.depends_on ?? []).map((d) => flags?.[d]?.plain ?? d), ...(flag.needs ? [flag.needs] : [])];
                const blocked = !flag.on && blockedBy.length > 0;
                // M55 S1: the switch shown now would open LIVE (live_trading on, the PAPER-only lock off) - owner only.
                const opensLive = !!flag.owner_only && (flag.opens_live_when_on ? !flag.on : flag.on);
                const confirmText = flag.confirm_text ?? "ENABLE LIVE";
                const confirmed = (typed[name] ?? "") === confirmText;
                return (
                  <li key={name} data-flag={name} className="grid gap-2 py-2 pl-3 text-xs md:grid-cols-[minmax(0,1fr)_auto]">
                    <div className="min-w-0 space-y-0.5">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-semibold text-fg">{flag.plain ?? name}</span>
                        {flag.status_label && (
                          <span data-flag-status={flag.status} className={`rounded-full border px-2 py-0.5 ${TONES[statusTone(flag.status)]}`}>{flag.status_label}</span>
                        )}
                      </div>
                      {flag.unavailable_reason && <div data-flag-unavailable className="font-medium text-warn">{flag.unavailable_reason}</div>}
                      {blocked && <div data-flag-blocked className="font-medium text-warn">Needs {blockedBy.join(", ")} first</div>}
                      <details className="text-fg-muted" data-flag-details>
                        <summary className="cursor-pointer">Details</summary>
                        <div className="mt-0.5 space-y-0.5">
                          <div className="font-mono">{name}</div>
                          <div>
                            {flag.default_on !== undefined && <span>Default {flag.default_on ? "ON" : "OFF"}</span>}
                            {flag.turns_on && <span> · Turns on: {flag.turns_on}</span>}
                            {needs.length > 0 && <span> · Needs: {needs.join(", ")}</span>}
                          </div>
                          {flag.fate_note && <div data-flag-fate={flag.fate}>{flag.fate_note}</div>}
                          <div>{flag.description}</div>
                          {!flag.on && flag.fate !== "removed_from_view" && !flag.owner_only && (
                            <div className="flex flex-wrap items-center gap-2 pt-1">
                              <span>Keep it on for these organisations only:</span>
                              <input aria-label={`Allow-listed organisation ids for ${flag.plain ?? name}`} placeholder="e.g. 3, 17"
                                className="w-28 rounded border border-border bg-surface-2 px-2 py-1 text-fg" value={tenants[name] ?? ""}
                                onChange={(e) => onTenants(name, e.target.value)} />
                              <button type="button" disabled={busy} onClick={() => onSaveList(name)}
                                className="rounded border border-border px-2 py-0.5 text-fg hover:bg-surface-2">Save list</button>
                            </div>
                          )}
                        </div>
                      </details>
                      {flag.live_orgs && (
                        <LiveOrgsBlock orgs={flag.live_orgs} canChange={!!flag.can_switch_live} confirmText={confirmText}
                          busy={busy} onGrant={onGrantLive} />
                      )}
                    </div>
                    <div className="flex flex-wrap items-center gap-2 md:justify-end">
                      <span className={`font-bold ${flag.on ? "text-up" : "text-down"}`}>
                        {flag.on ? "ON" : `OFF${flag.tenants.length && !flag.owner_only ? ` (${flag.tenants.length} allowed)` : ""}`}
                      </span>
                      {opensLive && !flag.can_switch_live ? (
                        <span data-flag-owner-only className="text-fg-muted">Only the owner can switch LIVE on</span>
                      ) : opensLive ? (
                        <>
                          <input aria-label={`Type ${confirmText} to confirm`} placeholder={`Type ${confirmText}`} data-flag-confirm
                            className="w-32 rounded border border-border bg-surface-2 px-2 py-1 text-fg" value={typed[name] ?? ""}
                            onChange={(e) => setTyped({ ...typed, [name]: e.target.value })} />
                          <button type="button" disabled={busy || !confirmed} onClick={() => onSet(name, !flag.on, typed[name])}
                            className="rounded border border-down/40 px-2 py-0.5 text-down hover:bg-down/10 disabled:cursor-not-allowed disabled:opacity-50">
                            {flag.on ? "Turn off" : "Turn on"}
                          </button>
                        </>
                      ) : flag.on ? (
                        <button type="button" disabled={busy} onClick={() => onSet(name, false)}
                          className="rounded border border-down/40 px-2 py-0.5 text-down hover:bg-down/10">Turn off</button>
                      ) : flag.fate === "removed_from_view" ? null : (
                        <button type="button" disabled={busy || blocked} onClick={() => onSet(name, true)}
                          title={blocked ? `Needs ${blockedBy.join(", ")} first` : undefined}
                          className="rounded bg-brand px-2 py-0.5 font-semibold text-on-brand hover:bg-brand-strong disabled:cursor-not-allowed disabled:opacity-50">Turn on</button>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          </details>
        ))}
      </div>
    </Card>
  );
}
