import type { FeatureFlag, FeatureFlags } from "../types";

/**
 * UX1 (audit section 7, handover 8b): the admin Feature flags list, grouped by area, with a search box. Pure helpers,
 * so the grouping, the search and the "Needs ... first" rule are tested without a page.
 */
export interface FlagRow {
  name: string;
  flag: FeatureFlag;
  /** The plain names of the flags this one needs that are off right now (empty: it may be turned on). */
  blockedBy: string[];
}

export interface FlagGroup {
  area: string;
  label: string;
  rows: FlagRow[];
}

/**
 * The order the groups show in (a group the server adds later goes at the end). ADM-1: the finer categories first (the
 * server's `group`), then the broad areas a flag without a group falls back to.
 */
const AREA_ORDER = ["safety", "orders", "paper", "backtest", "charts", "screens", "screen_library", "scan_actions", "ai", "alerts",
  "market_data", "look", "accounts", "help", "developers", "trading", "data", "platform"];

/** ADM-1: the status filter above the list. */
export type FlagFilter = "all" | "on" | "off" | "setup" | "decision";
export const FLAG_FILTERS: { id: FlagFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "on", label: "On" },
  { id: "off", label: "Off" },
  { id: "setup", label: "Needs setup" },
  { id: "decision", label: "Owner decision" },
];

function passes(flag: FeatureFlag, filter: FlagFilter): boolean {
  if (filter === "on") return flag.on;
  if (filter === "off") return !flag.on;
  if (filter === "setup") return flag.status === "unfinished";
  if (filter === "decision") return flag.status === "decision";
  return true;
}

export function plainName(name: string, flags: FeatureFlags): string {
  return flags[name]?.plain || name;
}

/** The dependencies of `name` that are off (fully off: an allow-list still counts as off for everyone else). */
export function blockedBy(name: string, flags: FeatureFlags): string[] {
  return (flags[name]?.depends_on ?? []).filter((dep) => flags[dep] && !flags[dep].on).map((dep) => plainName(dep, flags));
}

function matches(name: string, flag: FeatureFlag, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [name, flag.plain, flag.description, flag.turns_on, flag.needs, flag.status_label, flag.area_label, flag.group_label]
    .some((text) => (text ?? "").toLowerCase().includes(q));
}

export function groupFlags(flags: FeatureFlags, query = "", filter: FlagFilter = "all"): FlagGroup[] {
  const groups = new Map<string, FlagGroup>();
  for (const [name, flag] of Object.entries(flags)) {
    if (!matches(name, flag, query) || !passes(flag, filter)) continue;
    const area = flag.group ?? flag.area ?? "platform";
    const group = groups.get(area) ?? { area, label: flag.group_label ?? flag.area_label ?? "Other", rows: [] };
    group.rows.push({ name, flag, blockedBy: blockedBy(name, flags) });
    groups.set(area, group);
  }
  const rank = (area: string) => { const i = AREA_ORDER.indexOf(area); return i < 0 ? AREA_ORDER.length : i; };
  return [...groups.values()]
    .sort((a, b) => rank(a.area) - rank(b.area) || a.label.localeCompare(b.label))
    .map((g) => ({ ...g, rows: g.rows.sort((a, b) => (a.flag.plain ?? a.name).localeCompare(b.flag.plain ?? b.name)) }));
}

/** The status badge's tone. */
export function statusTone(status: FeatureFlag["status"]): "up" | "muted" | "warn" | "brand" {
  return status === "ready" ? "up" : status === "unfinished" ? "warn" : status === "decision" ? "brand" : "muted";
}
