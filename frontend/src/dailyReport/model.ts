import type { ReportRow } from "./types";

/** M88-R: the sortable stock table - each column's value; a missing value always sorts last. */
export type SortKey = "symbol" | "change_pct" | "volume_ratio" | "range_position_pct" | "fut_oi_change_pct" | "pcr" | "sector";
export const SORTS: { key: SortKey; label: string }[] = [
  { key: "change_pct", label: "Change" }, { key: "symbol", label: "Symbol" }, { key: "sector", label: "Sector" },
  { key: "volume_ratio", label: "Volume x avg" }, { key: "range_position_pct", label: "Range position" },
  { key: "fut_oi_change_pct", label: "OI change" }, { key: "pcr", label: "PCR" },
];

export function sortRows(rows: readonly ReportRow[], key: SortKey, desc: boolean): ReportRow[] {
  const value = (r: ReportRow): number | string | null => {
    const v = r[key];
    return v === undefined ? null : (v as number | string | null);
  };
  return [...rows].sort((a, b) => {
    const x = value(a), y = value(b);
    if (x === null && y === null) return a.symbol.localeCompare(b.symbol);
    if (x === null) return 1;
    if (y === null) return -1;
    const c = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y));
    return (desc ? -c : c) || a.symbol.localeCompare(b.symbol);
  });
}

export const DIRECTION: Record<string, string> = {
  up: "Up", "up (weak)": "Up, weakening", down: "Down", "down (weak)": "Down, weakening", range: "Range", none: "Not formed",
};

export function directionWord(d: string | undefined | null): string {
  return d ? DIRECTION[d] ?? d : "Not formed";
}

/** green up / red down by meaning; neutral otherwise. */
export function directionTone(d: string | undefined | null): "text-up" | "text-down" | "text-fg-muted" {
  if (!d) return "text-fg-muted";
  return d.startsWith("up") ? "text-up" : d.startsWith("down") ? "text-down" : "text-fg-muted";
}

export function changeTone(v: number | null | undefined): "text-up" | "text-down" | "text-fg" {
  return v == null || v === 0 ? "text-fg" : v > 0 ? "text-up" : "text-down";
}

export const SECTION_TITLES = {
  index_read: "NIFTY index read", breadth_read: "Breadth", sectors_read: "Sector strength", leaders_laggards: "Leaders and laggards",
  notable_structures: "Notable structures", conclusion: "Conclusion",
} as const;
