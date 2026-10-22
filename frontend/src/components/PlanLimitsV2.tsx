/**
 * AMW-1 (flag `plan_limits_v2`): the plan's limits for the AMW port's features, with the caller's own usage where the
 * feature that owns it counts it. Shown inside the Billing card only while the flag is on (the server then sends
 * `limits_v2`); with the flag off nothing here renders.
 */

/** Display order and wording. `unit` follows the number; `kind` says how usage compares to the limit. */
export const PLAN_LIMITS_V2: { key: string; label: string; unit?: string; kind: "count" | "amount" | "max" | "min" | "retention" }[] = [
  { key: "watchlists", label: "Watchlists", kind: "count" },
  { key: "screen_symbols", label: "Symbols per screener run", kind: "max" },
  { key: "reports_per_day", label: "Reports a day", kind: "count" },
  { key: "report_storage_mb", label: "Report storage", unit: "MB", kind: "amount" },
  { key: "report_retention_days", label: "Reports kept for", unit: "days", kind: "retention" },
  { key: "report_schedules", label: "Report schedules", kind: "count" },
  { key: "min_schedule_interval_min", label: "Shortest schedule interval", unit: "min", kind: "min" },
  { key: "lab_strategies", label: "Strategy Lab strategies", kind: "count" },
  { key: "lab_cpu_seconds_per_day", label: "Strategy Lab CPU a day", unit: "s", kind: "amount" },
];

export interface PlanLimitRow { key: string; label: string; limit: string; used: string | null; full: boolean }

const num = (v: number, unit?: string) => `${v.toLocaleString("en-IN")}${unit ? ` ${unit}` : ""}`;

/** One row per known limit the server sent; usage only for counted limits that some feature counts (non-null). */
export function planLimitRows(limits: Record<string, number>, usage?: Record<string, number | null> | null): PlanLimitRow[] {
  const rows: PlanLimitRow[] = [];
  for (const def of PLAN_LIMITS_V2) {
    const limit = limits[def.key];
    if (typeof limit !== "number") continue;
    const raw = def.kind === "count" || def.kind === "amount" ? usage?.[def.key] : null;
    const used = typeof raw === "number" ? raw : null;
    rows.push({
      key: def.key, label: def.label, limit: def.kind === "min" ? `at least ${num(limit, def.unit)}` : num(limit, def.unit),
      used: used === null ? null : num(used, def.unit), full: used !== null && used >= limit,
    });
  }
  return rows;
}

/** A plan tile's one-line summary of the v2 limits. */
export function planTileLine(limits: Record<string, number>): string {
  const parts: string[] = [];
  if (typeof limits.watchlists === "number") parts.push(`${num(limits.watchlists)} watchlists`);
  if (typeof limits.screen_symbols === "number") parts.push(`${num(limits.screen_symbols)} symbols/scan`);
  if (typeof limits.reports_per_day === "number") parts.push(`${num(limits.reports_per_day)} reports/day`);
  if (typeof limits.lab_strategies === "number") parts.push(`${num(limits.lab_strategies)} Lab strategies`);
  return parts.join(" · ");
}

export function PlanLimitsV2({ limits, usage }: { limits: Record<string, number>; usage?: Record<string, number | null> | null }) {
  const rows = planLimitRows(limits, usage);
  if (rows.length === 0) return null;
  return (
    <div className="mt-3 text-xs" data-testid="plan-limits-v2">
      <div className="text-xs font-bold uppercase tracking-wider text-fg-muted mb-1">Your plan&apos;s feature limits</div>
      <table className="w-full text-xs">
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className="border-t border-border/60">
              <td className="py-1">{r.label}</td>
              <td className={`py-1 text-right ${r.full ? "text-warn font-bold" : ""}`}>{r.used !== null ? `${r.used} of ${r.limit}` : r.limit}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-1 text-xs text-fg-muted">Counts are your own. A limit with no count belongs to a feature that is not available yet.</div>
    </div>
  );
}
