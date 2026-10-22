import { request } from "../api/client";
import { EMPTY, formatDateIST, formatNumber, formatPct } from "../ui/format";

/**
 * MO4 slice 2 (ATP_SESSION_GROWTH_MASTER.md PART 2 item 6): the Sector & index view - the answers of
 * `GET /api/markets/overview` and `GET /api/markets/lists/{key}` (backend app/markets/service.py) and the pure rules the
 * page shows them by. Every number is the server's, from the shared end-of-day closes; a number that is not stored is
 * null and shows as "I don't know", never 0 and never a sample. Information only: no buy / sell wording, no orders.
 */

export type Period = "day" | "week" | "month";
export const PERIODS: readonly { key: Period; label: string; long: string }[] = [
  { key: "day", label: "Day", long: "on the day" },
  { key: "week", label: "Week", long: "over the week" },
  { key: "month", label: "Month", long: "over the month" },
];

export type PeriodValues = Record<Period, number | null>;

export interface Breadth { advances: number; declines: number; unchanged: number; counted: number; missing: number }

export interface Mover { symbol: string; exchange: string; name: string; change: number | null }

export interface MarketGroup {
  kind: "index" | "sector";
  name: string;
  /** The system list with its stocks (`index-nifty50`, `sector-it`), or null when there is none. */
  list_key: string | null;
  symbol: string | null;
  exchange: string | null;
  family: string | null;
  family_label: string | null;
  close: number | null;
  method: string;
  count: number;
  change: PeriodValues;
  vs_benchmark: PeriodValues;
  breadth: Record<Period, Breadth> | null;
  strongest: Record<Period, Mover[]>;
  weakest: Record<Period, Mover[]>;
  known: boolean;
}

export type OverviewState = "ok" | "no_lists" | "no_data";

export interface MarketsOverview {
  state: OverviewState;
  message: string | null;
  as_of: string | null;
  stale: boolean;
  periods: Record<Period, string | null>;
  benchmark: { name: string; change: PeriodValues };
  families: { key: string; label: string }[];
  indices: MarketGroup[];
  sectors: MarketGroup[];
  source: string;
  note: string;
}

export interface ListMember { symbol: string; exchange: string; name: string; close: number | null; change: PeriodValues; vs_benchmark: PeriodValues }

export interface MarketList {
  state: "ok" | "no_data";
  message: string | null;
  list_key: string;
  kind: "index" | "sector";
  name: string;
  symbol: string | null;
  as_of: string | null;
  stale: boolean;
  periods: Record<Period, string | null>;
  benchmark: { name: string; change: PeriodValues };
  summary: MarketGroup | null;
  members: ListMember[];
  source: string;
  note: string;
}

export const fetchOverview = () => request<MarketsOverview>("/markets/overview");
export const fetchList = (key: string) => request<MarketList>(`/markets/lists/${encodeURIComponent(key)}`);

export const UNKNOWN = "I don't know";

export function isPeriod(v: string | null | undefined): v is Period {
  return v === "day" || v === "week" || v === "month";
}

/** "+1.20%", or "I don't know" when the move is not stored. */
export function moveText(v: number | null | undefined): string {
  return typeof v === "number" && Number.isFinite(v) ? formatPct(v) : UNKNOWN;
}

/** The move against the NIFTY 50 in percentage points: "+0.40 pts vs NIFTY 50", or null when not known. */
export function relativeText(v: number | null | undefined, benchmark: string): string | null {
  if (typeof v !== "number" || !Number.isFinite(v)) return null;
  const plain = formatNumber(v, 2);
  return `${v > 0 && plain !== "0.00" ? "+" : ""}${plain} pts vs ${benchmark}`;
}

export type Tone = "up" | "down" | "flat" | "unknown";

export function toneOf(v: number | null | undefined): Tone {
  if (typeof v !== "number" || !Number.isFinite(v)) return "unknown";
  if (Number(v.toFixed(2)) === 0) return "flat";
  return v > 0 ? "up" : "down";
}

/** The text colour of a move (v2 tokens only). */
export function toneText(v: number | null | undefined): string {
  const t = toneOf(v);
  return t === "up" ? "text-up" : t === "down" ? "text-down" : t === "flat" ? "text-fg" : "text-fg-muted";
}

/**
 * The heat-map tile's background: a stronger tint for a larger move, scaled per period (a 2% day is as strong as a 4%
 * week and an 8% month). Four steps each way; flat and unknown are the plain surface.
 */
export const HEAT_SCALE: Record<Period, number> = { day: 2, week: 4, month: 8 };
const UP_STEPS = ["bg-up/10", "bg-up/20", "bg-up/30", "bg-up/40"] as const;
const DOWN_STEPS = ["bg-down/10", "bg-down/20", "bg-down/30", "bg-down/40"] as const;

export function heatClass(v: number | null | undefined, period: Period): string {
  const t = toneOf(v);
  if (t === "unknown" || t === "flat" || typeof v !== "number") return "bg-surface-2";
  const step = Math.min(3, Math.floor((Math.abs(v) / HEAT_SCALE[period]) * 4));
  return (t === "up" ? UP_STEPS : DOWN_STEPS)[step];
}

/** "8 rose · 3 fell · 1 unchanged" (the stocks with a stored move), or null without stocks. */
export function breadthText(b: Breadth | null | undefined): string | null {
  if (!b || b.counted === 0) return null;
  const parts = [`${b.advances} rose`, `${b.declines} fell`];
  if (b.unchanged) parts.push(`${b.unchanged} unchanged`);
  return parts.join(" · ");
}

/** The share of rising stocks, 0-100, for the breadth bar; null without stocks. */
export function advanceShare(b: Breadth | null | undefined): number | null {
  if (!b || b.counted === 0) return null;
  return Math.round((b.advances / b.counted) * 100);
}

/** Groups by their move over the period, largest first; the ones without a move last (by name). */
export function rankGroups<T extends { name: string; change: PeriodValues }>(groups: readonly T[], period: Period): T[] {
  return [...groups].sort((a, b) => {
    const x = a.change[period], y = b.change[period];
    if (x === null && y === null) return a.name.localeCompare(b.name);
    if (x === null) return 1;
    if (y === null) return -1;
    return y - x || a.name.localeCompare(b.name);
  });
}

/** Leaders and laggards over the period: the top and the bottom of the ranking, up to `n` each and never more than half
 * of the groups with a move each (so a short list never calls its lower half "leading"); one group alone leads. */
export function leadersAndLaggards<T extends { name: string; change: PeriodValues }>(groups: readonly T[], period: Period, n = 3): { leaders: T[]; laggards: T[] } {
  const known = rankGroups(groups, period).filter((g) => g.change[period] !== null);
  const take = Math.min(n, Math.max(1, Math.floor(known.length / 2)));
  const leaders = known.slice(0, take);
  const laggards = known.slice(leaders.length).reverse().slice(0, take);
  return { leaders, laggards };
}

/** "At the close of 21 Oct 2026" / "Over the week to 21 Oct 2026" - what the moves stand on. */
export function asOfText(asOf: string | null, period: Period): string {
  if (!asOf) return "";
  const day = formatDateIST(asOf);
  return period === "day" ? `At the close of ${day}` : period === "week" ? `Over the week to ${day}` : `Over the month to ${day}`;
}

/**
 * The page's "ready-made dish" in plain words: the market's move, the strongest and weakest sector and how many sectors
 * rose. Each sentence only when its numbers are known; never advice.
 */
export function dishLines(o: MarketsOverview, period: Period): string[] {
  const lines: string[] = [];
  const when = PERIODS.find((p) => p.key === period)?.long ?? "";
  const bench = o.benchmark.change[period];
  if (bench !== null) {
    lines.push(`${o.benchmark.name} ${toneOf(bench) === "up" ? "rose" : toneOf(bench) === "down" ? "fell" : "was unchanged"} ${when}${toneOf(bench) === "flat" ? "" : ` (${formatPct(bench)})`}.`);
  } else {
    lines.push(`${UNKNOWN} how ${o.benchmark.name} moved ${when}: its close is not stored.`);
  }
  const { leaders, laggards } = leadersAndLaggards(o.sectors, period, 1);
  if (leaders.length && laggards.length) {
    lines.push(`Strongest sector: ${leaders[0].name} (${formatPct(leaders[0].change[period])}). Weakest: ${laggards[0].name} (${formatPct(laggards[0].change[period])}).`);
  } else if (leaders.length) {
    lines.push(`Strongest sector: ${leaders[0].name} (${formatPct(leaders[0].change[period])}).`);
  }
  const known = o.sectors.filter((s) => s.change[period] !== null);
  if (known.length) {
    const up = known.filter((s) => (s.change[period] ?? 0) > 0).length;
    lines.push(`${up} of ${known.length} sectors rose ${when}.`);
  }
  return lines;
}

/** A stock's close, or "–" when not stored. */
export function closeText(v: number | null | undefined): string {
  return typeof v === "number" && Number.isFinite(v) ? formatNumber(v, 2) : EMPTY;
}
