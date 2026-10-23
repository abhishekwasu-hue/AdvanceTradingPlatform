import { Search } from "lucide-react";
import { useId, useState } from "react";
import { Card } from "../components/ui";
import type { FeatureFlags } from "../types";
import { groupFlags, statusTone } from "./flagList";

/**
 * UX1 (audit section 7, handover 8b): the admin Feature flags list - grouped by area, with a search box. Each row: the
 * plain description, the flag name, default, what turns on, what it needs, the status (ready / behind the scenes /
 * unfinished / owner decision) and the switch. A flag whose dependency is off cannot be turned on here ("Needs ...
 * first"); turning one off always works. Admin-only: the list is served only to a SUPER_ADMIN with two-factor.
 */
export interface FeatureFlagsCardProps {
  flags: FeatureFlags | null;
  tenants: Record<string, string>;
  onTenants: (name: string, value: string) => void;
  busy: boolean;
  /** Turn a flag on or off (off with the allow-list typed for it). */
  onSet: (name: string, on: boolean) => void;
  /** Save the allow-list of an OFF flag. */
  onSaveList: (name: string) => void;
}

const TONES = {
  up: "border-up/40 text-up",
  warn: "border-warn/40 bg-warn-tint text-warn",
  brand: "border-brand/40 text-brand",
  muted: "border-border text-fg-muted",
} as const;

export default function FeatureFlagsCard({ flags, tenants, onTenants, busy, onSet, onSaveList }: FeatureFlagsCardProps) {
  const [query, setQuery] = useState("");
  const searchId = useId();
  const groups = flags ? groupFlags(flags, query) : [];
  return (
    <Card title="Feature flags">
      <p className="mb-3 text-xs text-fg-muted">
        Turning a flag off with an organisation allow-list keeps it on for those organisations only (staged rollouts,
        beta access). Exits and PAPER trading are never behind a flag. A trader never sees a flag name: a page that is
        off says "Coming soon". Status is the audit judgement - try each flag on staging first.
      </p>
      <label htmlFor={searchId} className="sr-only">Search flags</label>
      <div className="relative mb-3 max-w-sm">
        <Search size={14} aria-hidden className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-fg-muted" />
        <input id={searchId} type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search flags"
          className="w-full rounded border border-border bg-surface-2 py-1 pl-7 pr-2 text-xs text-fg" />
      </div>
      {flags && groups.length === 0 && <p className="text-xs text-fg-muted">No flag matches "{query}".</p>}
      <div className="space-y-4">
        {groups.map((group) => (
          <section key={group.area} aria-label={group.label} data-flag-area={group.area}>
            <h3 className="mb-1 text-xs font-semibold text-fg">{group.label} <span className="font-normal text-fg-muted">({group.rows.length})</span></h3>
            <ul className="divide-y divide-border border-y border-border">
              {group.rows.map(({ name, flag, blockedBy }) => {
                const needs = [...(flag.depends_on ?? []).map((d) => flags?.[d]?.plain ?? d), ...(flag.needs ? [flag.needs] : [])];
                const blocked = !flag.on && blockedBy.length > 0;
                return (
                  <li key={name} data-flag={name} className={`grid gap-2 py-2 text-xs md:grid-cols-[minmax(0,1fr)_auto]`}>
                    <div className="min-w-0 space-y-0.5">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-semibold text-fg">{flag.plain ?? name}</span>
                        {flag.status_label && (
                          <span data-flag-status={flag.status} className={`rounded-full border px-2 py-0.5 ${TONES[statusTone(flag.status)]}`}>{flag.status_label}</span>
                        )}
                      </div>
                      <div className="font-mono text-fg-muted">{name}</div>
                      <div className="text-fg-muted">
                        {flag.default_on !== undefined && <span>Default {flag.default_on ? "ON" : "OFF"}</span>}
                        {flag.turns_on && <span> · Turns on: {flag.turns_on}</span>}
                        {needs.length > 0 && <span> · Needs: {needs.join(", ")}</span>}
                      </div>
                      {flag.unavailable_reason && <div data-flag-unavailable className="font-medium text-warn">{flag.unavailable_reason}</div>}
                      {blocked && <div data-flag-blocked className="font-medium text-warn">Needs {blockedBy.join(", ")} first</div>}
                      <div className="text-fg-muted">{flag.description}</div>
                    </div>
                    <div className="flex flex-wrap items-center gap-2 md:justify-end">
                      <span className={`font-bold ${flag.on ? "text-up" : "text-down"}`}>
                        {flag.on ? "ON" : `OFF${flag.tenants.length ? ` (${flag.tenants.length} allowed)` : ""}`}
                      </span>
                      <input aria-label={`Allow-listed organisation ids for ${flag.plain ?? name}`} placeholder="e.g. 3, 17"
                        className="w-28 rounded border border-border bg-surface-2 px-2 py-1" value={tenants[name] ?? ""}
                        onChange={(e) => onTenants(name, e.target.value)} />
                      {flag.on ? (
                        <button type="button" disabled={busy} onClick={() => onSet(name, false)}
                          className="rounded border border-down/40 px-2 py-0.5 text-down hover:bg-down/10">Turn off</button>
                      ) : (
                        <>
                          <button type="button" disabled={busy} onClick={() => onSaveList(name)}
                            className="rounded border border-border px-2 py-0.5 text-fg hover:bg-surface-2">Save list</button>
                          <button type="button" disabled={busy || blocked} onClick={() => onSet(name, true)}
                            title={blocked ? `Needs ${blockedBy.join(", ")} first` : undefined}
                            className="rounded bg-brand px-2 py-0.5 font-semibold text-on-brand hover:bg-brand-strong disabled:cursor-not-allowed disabled:opacity-50">Turn on</button>
                        </>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </div>
    </Card>
  );
}
