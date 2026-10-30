/**
 * TERM-2 step 4 R3 (owner order ATP_ORDER_TERM2.md; flag `term_strike_area`, with `ui_v2` + `terminal`): the distance from
 * each SHORT strike to the nearest stored area, on the position row and on the chain. The data is the My day read
 * (AUD-T1, GET /api/today/my-day: each open position's nearest live area measured from its held strike) - no reading
 * logic here. Words and numbers only: where the area sits and how far, never what to do about it.
 */
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { MyDayArea, MyDayPosition } from "../dashboard/myDay";
import { formatNumber } from "../ui/format";

export const AREA_POLL_MS = 60_000;

/** Only a sold option measured from its own strike. Pure. */
export function shortStrikeAreas(rows: readonly MyDayPosition[]): MyDayPosition[] {
  return rows.filter((r) => r.side === "SHORT" && r.kind === "OPTION" && r.area?.from === "strike");
}

/** "Area 0.42% above" / "Inside an area". Pure. */
export function areaShort(a: MyDayArea): string {
  return a.where === "inside" ? "Inside an area" : `Area ${formatNumber(a.distance_pct, 2)}% ${a.where}`;
}

/** "Area 0.42% above · 24,950.00-25,010.00". Pure. */
export function areaWords(a: MyDayArea): string {
  return `${areaShort(a)} · ${a.range_text}`;
}

/** Each short position's mark by its trade id. Pure. */
export function areasByTrade(rows: readonly MyDayPosition[]): Map<number, MyDayArea> {
  return new Map(shortStrikeAreas(rows).flatMap((r) => (r.area ? [[r.id, r.area] as const] : [])));
}

/** The chain's marks for one underlying + expiry, keyed "strike|CE" / "strike|PE" like the held markers. Pure. */
export function chainAreaMarks(rows: readonly MyDayPosition[], underlying: string, expiry: string | null): Map<string, string> {
  const name = underlying.toUpperCase();
  const out = new Map<string, string>();
  for (const r of shortStrikeAreas(rows)) {
    if (!r.area || r.strike == null || !r.right || !expiry || r.expiry !== expiry) continue;
    if (!r.underlying.toUpperCase().startsWith(name) && !name.startsWith(r.underlying.toUpperCase())) continue;
    out.set(`${r.strike}|${r.right}`, areaShort(r.area));
  }
  return out;
}

/** The My day positions while `on` (refreshed every minute); [] while off or when the read fails (no mark is shown). */
export function useStrikeAreas(on: boolean, read: () => Promise<{ positions: MyDayPosition[] }> = api.myDay): MyDayPosition[] {
  const [rows, setRows] = useState<MyDayPosition[]>([]);
  useEffect(() => {
    if (!on) { setRows([]); return undefined; }
    let live = true;
    const load = () => read().then((d) => { if (live) setRows(shortStrikeAreas(d.positions ?? [])); }, () => { if (live) setRows([]); });
    void load();
    const timer = window.setInterval(() => { void load(); }, AREA_POLL_MS);
    return () => { live = false; window.clearInterval(timer); };
  }, [on, read]);
  return rows;
}
