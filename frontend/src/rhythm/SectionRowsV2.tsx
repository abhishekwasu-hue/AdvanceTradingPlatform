import type { ReactNode } from "react";
import { cx } from "../components/primitives/cx";
import { Chip } from "../ui/Chip";
import { Details } from "../ui/Details";
import { DirectionChip } from "../ui/DirectionChip";
import { formatINR, formatNumber, formatPrice } from "../ui/format";
import { KeyValueTable, type KeyValueRow } from "../ui/KeyValueTable";
import { LevelChip } from "../ui/LevelChip";
import { budgetLevel, type BudgetLevel } from "../ui/levels";
import { ProgressStat } from "../ui/ProgressStat";
import { ResponsiveTable } from "../ui/ResponsiveTable";
import { Signed } from "../ui/Signed";
import { TrendChip } from "../ui/TrendChip";
import type { Location, PriceArea, RhythmSection, RowOf, SectionKind, SectionRow, StructureEvent } from "./types";

/**
 * UX-11b C2 (owner, 28 Oct; docs/owner-orders/ATP_ORDER_UX11B.md): the Today briefs, numbers first. Each section is
 * rendered from its typed `rows` by its `kind` - tables, Signed numbers, chips from the one colour table (src/ui/levels.ts),
 * bars - instead of sentences. Explanations sit in Details; the user's OWN brief shows the buyer / seller area as a price
 * range there (owner default; never on a public surface). A kind without a renderer (or a section without rows) falls
 * back to its lines.
 */

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2031-06-13" -> "Fri 13 Jun" (a calendar day; no time zone shift). */
export function dayLabel(iso: string | null | undefined): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ?? "");
  if (!m) return "";
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  return `${WEEKDAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
}

function rowsOf<T extends SectionRow["type"]>(rows: readonly SectionRow[], type: T): RowOf<T>[] {
  return rows.filter((r): r is RowOf<T> => r.type === type);
}

const pct = (v: number | null | undefined) => <Signed value={v} kind="pct" />;
const money = (v: number | null | undefined) => <Signed value={v} kind="money" />;

/** A stale or past reading: an amber chip with the day it is from. */
function AsOf({ iso, what = "as of" }: { iso: string | null | undefined; what?: string }) {
  if (!iso) return null;
  return <Chip tone="warn" variant="meaning" icon="!" name="stale">{what} {dayLabel(iso)}</Chip>;
}

const LOCATION: Record<Location, { tone: "up" | "down" | "neutral"; label: string }> = {
  IN_BUYER: { tone: "up", label: "In buyer area" },
  IN_SELLER: { tone: "down", label: "In seller area" },
  NEARER_BUYER: { tone: "neutral", label: "Nearer buyer" },
  NEARER_SELLER: { tone: "neutral", label: "Nearer seller" },
  ABOVE_BUYER: { tone: "neutral", label: "Above buyer" },
  BELOW_SELLER: { tone: "neutral", label: "Below seller" },
};

export function LocationChip({ location }: { location: Location | null | undefined }) {
  if (!location) return <span className="text-fg-muted">–</span>;
  const m = LOCATION[location];
  return <Chip tone={m.tone} variant="meaning" icon={m.tone === "up" ? "▲" : m.tone === "down" ? "▼" : "◆"} name={`location-${location.toLowerCase()}`} className="whitespace-nowrap">{m.label}</Chip>;
}

/** BOS coloured by its direction; a change of character (CHoCH) is amber - the owner's table. */
export function StructureEventChip({ event }: { event: StructureEvent | null | undefined }) {
  if (!event) return <span className="text-fg-muted">–</span>;
  const up = event.direction === "UP";
  if (event.kind === "CHOCH") {
    return <Chip tone="warn" variant="meaning" icon={up ? "▲" : "▼"} name="event-choch" className="whitespace-nowrap">Character change</Chip>;
  }
  return <Chip tone={up ? "up" : "down"} variant="meaning" icon={up ? "▲" : "▼"} name={`event-bos-${up ? "up" : "down"}`} className="whitespace-nowrap">Break {up ? "up" : "down"}</Chip>;
}

const level = (v: number) => (Number.isInteger(v) ? formatNumber(v, 0) : formatPrice(v));
const area = (a: PriceArea | null) => (a ? `${level(a.low)}–${level(a.high)}` : null);

function Structure({ section }: { section: RhythmSection }) {
  const rows = rowsOf(section.rows ?? [], "index");
  const areas = rows.filter((r) => r.buyer_area || r.seller_area);
  return (
    <>
      <ResponsiveTable caption={section.title} phone="stack" rowKey={(r) => r.symbol} rows={rows}
        columns={[
          { key: "index", header: "Index", cell: (r) => <span className="inline-flex flex-wrap items-center gap-space-1">{r.label}<AsOf iso={r.last_read} what="read" /></span> },
          { key: "now", header: "Swings", cell: (r) => (r.trend ? <TrendChip trend={r.trend} /> : <span className="text-fg-muted">–</span>) },
          ...(rows.some((r) => r.daily) ? [
            { key: "daily", header: "Daily", cell: (r: RowOf<"index">) => (r.daily ? <TrendChip trend={r.daily} /> : <span className="text-fg-muted">–</span>) },
            { key: "weekly", header: "Weekly", cell: (r: RowOf<"index">) => (r.weekly ? <TrendChip trend={r.weekly} /> : <span className="text-fg-muted">–</span>) },
          ] : []),
          { key: "event", header: "Last event", cell: (r) => <StructureEventChip event={r.event} /> },
          { key: "where", header: "Price", cell: (r) => <LocationChip location={r.location} /> },
        ]} />
      {areas.length > 0 && (
        <Details id={`rhythm.${section.id}.areas`} label="Areas">
          <ul className="space-y-space-1">
            {areas.map((r) => (
              <li key={r.symbol} className="font-tabular">
                {r.label}: {[area(r.buyer_area) && `buyer area ${area(r.buyer_area)}`, area(r.seller_area) && `seller area ${area(r.seller_area)}`].filter(Boolean).join(" · ")}
              </li>
            ))}
          </ul>
        </Details>
      )}
    </>
  );
}

/** A bar coloured by meaning (inside green, near amber, reached red) with its words beside it. */
function MeaningBar({ label, share, level, text }: { label: string; share: number; level: BudgetLevel; text: string }) {
  const fill = level === "REACHED" ? "bg-down" : level === "NEAR" ? "bg-warn" : "bg-up";
  return (
    <div className="min-w-0" data-meaning-bar={level.toLowerCase()}>
      <div className="flex items-baseline justify-between gap-space-3">
        <span className="text-t13 text-fg-muted">{label}</span>
        <span className="font-tabular text-t14 font-medium text-fg">{text}</span>
      </div>
      <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(Math.min(1, share) * 100)}
        aria-valuetext={text} className="mt-space-1 h-1.5 overflow-hidden rounded-pill bg-surface-2">
        <div className={cx("h-full rounded-pill", fill)} style={{ width: `${Math.min(100, Math.max(0, share * 100))}%` }} />
      </div>
    </div>
  );
}

/** Where a reading sits among the past year's (0-100): a neutral bar and the share in words. */
function PercentileBar({ label, pct: share }: { label: string; pct: number }) {
  const v = Math.min(100, Math.max(0, share));
  const text = `${formatNumber(v, 0)}% of closes below`;
  return (
    <div className="min-w-0" data-percentile>
      <div className="flex items-baseline justify-between gap-space-3">
        <span className="text-t13 text-fg-muted">{label}</span>
        <span className="font-tabular text-t13 text-fg">{text}</span>
      </div>
      <div role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={v} aria-valuetext={text}
        className="relative mt-space-1 h-1.5 rounded-pill bg-range-track">
        <span aria-hidden style={{ left: `${v}%` }}
          className="absolute top-1/2 h-3.5 w-1 -translate-x-1/2 -translate-y-1/2 rounded-pill bg-range ring-2 ring-surface-1" />
      </div>
    </div>
  );
}

function Mood({ section }: { section: RhythmSection }) {
  const rows = section.rows ?? [];
  const cues = rowsOf(rows, "cues")[0];
  const vix = rowsOf(rows, "vix")[0];
  const flows = rowsOf(rows, "flows")[0];
  return (
    <div className="space-y-space-3">
      {cues && (
        <div>
          <div className="flex items-center gap-space-2 text-t13 text-fg-muted">Global cues <LevelChip scale="mood" level={cues.mood} /></div>
          <ResponsiveTable caption="Global markets" className="mt-space-2" rowKey={(m) => m.key} rows={cues.markets}
            columns={[
              { key: "m", header: "Market", cell: (m) => <span className="inline-flex flex-wrap items-center gap-space-1">{m.name}{m.stale && <Chip tone="warn" variant="meaning" icon="!" name="delayed">delayed</Chip>}</span> },
              { key: "chg", header: "Chg %", align: "right", cell: (m) => pct(m.change_pct) },
              { key: "last", header: "Last", align: "right", cell: (m) => (m.last == null ? "–" : formatNumber(m.last, 2)) },
            ]} />
        </div>
      )}
      {(vix || flows) && (
        <KeyValueTable caption="India VIX and flows" rows={[
          ...(vix ? [{
            label: "India VIX", testId: "vix",
            value: <span className="inline-flex items-center gap-space-2">{formatNumber(vix.value, 2)}{vix.change_pct != null && pct(vix.change_pct)}<LevelChip scale="vix" level={vix.level} /><AsOf iso={vix.as_of} what="close of" /></span>,
          }] : []),
          ...(flows ? [
            { label: <>FII net (₹ cr){flows.as_of && <span className="text-t12"> · {dayLabel(flows.as_of)}</span>}</>, testId: "fii",
              value: <Signed value={flows.fii_crore} kind="count" /> },
            { label: "DII net (₹ cr)", testId: "dii", value: <Signed value={flows.dii_crore} kind="count" /> },
          ] : []),
        ] satisfies KeyValueRow[]} />
      )}
      {vix?.percentile != null && <PercentileBar label="VIX vs the past year" pct={vix.percentile} />}
    </div>
  );
}

function EventChips({ rows }: { rows: readonly SectionRow[] }) {
  const events = rowsOf(rows, "event");
  const holidays = rowsOf(rows, "holiday");
  const notes = events.filter((e) => e.window || e.detail || e.pauses_entries);
  return (
    <>
      <div className="flex flex-wrap gap-space-2" data-event-chips>
        {events.map((e, i) => (
          <Chip key={`e${i}`} tone={e.pauses_entries ? "warn" : "info"} variant="meaning" icon="◆" name="event">
            {dayLabel(e.date)} · {e.label}{e.scope ? ` (${e.scope})` : ""}
          </Chip>
        ))}
        {holidays.map((h) => (
          <Chip key={h.date} tone="warn" variant="meaning" icon="!" name="holiday">{dayLabel(h.date)} · Market holiday</Chip>
        ))}
      </div>
      {(notes.length > 0 || holidays.some((h) => h.name)) && (
        <Details id="rhythm.events" label="About these days">
          <ul className="space-y-space-1">
            {notes.map((e, i) => (
              <li key={i}>
                {dayLabel(e.date)} · {e.label}{e.window ? ` ${e.window}` : ""}{e.detail ? ` - ${e.detail}` : ""}
                {e.pauses_entries ? ". New entries pause in its window; exits always work." : ""}
              </li>
            ))}
            {holidays.filter((h) => h.name).map((h) => <li key={h.date}>{dayLabel(h.date)}: {h.name}</li>)}
          </ul>
        </Details>
      )}
    </>
  );
}

function Setups({ section }: { section: RhythmSection }) {
  const rows = rowsOf(section.rows ?? [], "setup");
  return (
    <>
      <ResponsiveTable caption={section.title} rowKey={(r) => r.symbol} rows={rows}
        columns={[
          { key: "s", header: "Symbol", cell: (r) => <span className="font-medium">{r.symbol}</span> },
          { key: "side", header: "Side", cell: (r) => (r.side ? <DirectionChip side={r.side === "LONG" ? "long" : "short"} /> : <span className="text-fg-muted">–</span>) },
          { key: "src", header: "From", cell: (r) => (r.source === "scan" ? "Scan" : "Strategy") },
        ]} />
      <Details id={`rhythm.${section.id}.why`} label="Why">
        <ul className="space-y-space-1">{rows.map((r) => <li key={r.symbol}>{r.symbol}: {r.detail}</li>)}</ul>
      </Details>
    </>
  );
}

function Positions({ section }: { section: RhythmSection }) {
  const rows = rowsOf(section.rows ?? [], "position");
  const more = rowsOf(section.rows ?? [], "more")[0];
  return (
    <>
      <ResponsiveTable caption={section.title} rowKey={(r, i) => `${r.symbol}${i}`} rows={rows}
        columns={[
          { key: "s", header: "Symbol", cell: (r) => <span className="inline-flex flex-wrap items-center gap-space-1 font-medium">{r.symbol}{r.near && <Chip tone="warn" variant="meaning" icon="!" name="near">Near invalidation</Chip>}</span> },
          { key: "pnl", header: "Open P&L", align: "right", cell: (r) => money(r.pnl) },
          { key: "side", header: "Side", cell: (r) => <DirectionChip side={r.side === "SHORT" ? "short" : "long"} /> },
        ]} />
      {more && <p className="mt-space-2 text-t13 text-fg-muted">{more.count} more on My positions.</p>}
    </>
  );
}

function Budget({ section }: { section: RhythmSection }) {
  const b = rowsOf(section.rows ?? [], "budget")[0];
  if (!b) return null;
  const used = Math.max(0, b.limit - b.left);
  const level = budgetLevel(used, b.limit) ?? "INSIDE";
  return (
    <div className="space-y-space-2">
      <MeaningBar label="Daily loss budget used" share={b.limit > 0 ? used / b.limit : 0} level={level}
        text={`${formatINR(used)} of ${formatINR(b.limit)}`} />
      <div className="flex flex-wrap items-center gap-space-2">
        <LevelChip scale="budget" level={level} />
        {b.losses_in_row > 0 && (
          <Chip tone={b.pause_after != null && b.losses_in_row >= b.pause_after - 1 ? "warn" : "neutral"} variant="meaning" icon="◆" name="losses-in-row">
            Losses in a row {b.losses_in_row}{b.pause_after != null ? ` / ${b.pause_after}` : ""}
          </Chip>
        )}
      </div>
    </div>
  );
}

function Day({ section }: { section: RhythmSection }) {
  const d = rowsOf(section.rows ?? [], "day")[0];
  if (!d) return null;
  const kv: KeyValueRow[] = [];
  if (d.booked != null) kv.push({ label: "Booked P&L", testId: "booked", value: money(d.booked) });
  if (d.open_pnl != null) kv.push({ label: "Open P&L", testId: "open", value: money(d.open_pnl) });
  if (d.inside_limits != null) {
    kv.push({ label: "Your daily limits", testId: "limits", value: d.inside_limits
      ? <Chip tone="up" variant="meaning" icon="✓" name="limits-inside">Inside</Chip>
      : <Chip tone="down" variant="meaning" icon="✕" name="limits-breached">Breached</Chip> });
  }
  return (
    <div className="space-y-space-2">
      {kv.length > 0 && <KeyValueTable caption={section.title} rows={kv} />}
      {d.discipline != null && <ProgressStat label="Discipline score (your own plan)" done={d.discipline} of={100} />}
    </div>
  );
}

function Market({ section }: { section: RhythmSection }) {
  const rows = section.rows ?? [];
  const moves = rowsOf(rows, "index_move");
  const breadth = rowsOf(rows, "breadth")[0];
  const sectors = rowsOf(rows, "sectors")[0];
  const week = moves.some((m) => m.sessions > 1);
  const total = breadth ? breadth.advances + breadth.declines : 0;
  return (
    <div className="space-y-space-3">
      {moves.length > 0 && (
        <ResponsiveTable caption={section.title} rowKey={(m) => m.label} rows={moves}
          columns={[
            { key: "i", header: "Index", cell: (m) => <span className="inline-flex flex-wrap items-center gap-space-1">{m.label}<AsOf iso={m.as_of} what="close of" /></span> },
            { key: "p", header: week ? "5 sessions" : "Chg %", align: "right", cell: (m) => pct(m.change_pct) },
            { key: "c", header: "Close", align: "right", cell: (m) => formatNumber(m.close, 2) },
          ]} />
      )}
      {breadth && total > 0 && (
        <div data-breadth>
          <div className="flex items-baseline justify-between gap-space-3 text-t13">
            <span className="text-fg-muted">Breadth{breadth.as_of ? ` (${dayLabel(breadth.as_of)})` : ""}</span>
            <span className="font-tabular"><span className="text-up">▲ {formatNumber(breadth.advances)} rose</span> · <span className="text-down">▼ {formatNumber(breadth.declines)} fell</span></span>
          </div>
          <div role="img" aria-label={`${breadth.advances} shares rose and ${breadth.declines} fell`} className="mt-space-1 flex h-1.5 overflow-hidden rounded-pill bg-surface-2">
            <div className="h-full bg-up" style={{ width: `${(100 * breadth.advances) / total}%` }} />
            <div className="h-full bg-down" style={{ width: `${(100 * breadth.declines) / total}%` }} />
          </div>
        </div>
      )}
      {sectors && (
        <KeyValueTable caption="Sectors" rows={[
          { label: `Strongest: ${sectors.strongest.label}`, testId: "strongest", value: pct(sectors.strongest.change_pct) },
          { label: `Weakest: ${sectors.weakest.label}`, testId: "weakest", value: pct(sectors.weakest.change_pct) },
        ]} />
      )}
    </div>
  );
}

function Next({ section }: { section: RhythmSection }) {
  const rows = section.rows ?? [];
  const next = rowsOf(rows, "next_session")[0];
  const carry = rowsOf(rows, "carry")[0];
  return (
    <div className="space-y-space-2">
      <div className="flex flex-wrap gap-space-2">
        {next && <Chip tone="info" variant="meaning" icon="◆" name="next-session">Next session {dayLabel(next.date)}</Chip>}
        {carry && <Chip tone="neutral" variant="meaning" icon="◆" name="carry">{carry.count} open position{carry.count === 1 ? "" : "s"} carried</Chip>}
      </div>
      <EventChips rows={rows} />
    </div>
  );
}

function Week({ section }: { section: RhythmSection }) {
  const rows = section.rows ?? [];
  const rec = rowsOf(rows, "record")[0];
  const disc = rowsOf(rows, "discipline")[0];
  const cost = rowsOf(rows, "costliest")[0];
  const days = rowsOf(rows, "days_inside")[0];
  const kv: KeyValueRow[] = [];
  if (rec) {
    kv.push({ label: "Closed", testId: "closed", value: formatNumber(rec.closed) },
      { label: "Won", testId: "won", value: formatNumber(rec.won) },
      { label: "Lost", testId: "lost", value: formatNumber(rec.lost) },
      { label: rec.after_charges ? "Net after charges" : "Net", testId: "net", value: money(rec.net) });
  }
  if (disc) {
    kv.push({ label: "Discipline score", testId: "discipline", value: (
      <span className="inline-flex items-baseline gap-space-2">{disc.score} / 100
        {disc.previous != null && <Signed value={disc.score - disc.previous} kind="count" className="text-t13" />}
      </span>) });
  }
  if (cost) kv.push({ label: `Cost the most: ${cost.label}`, testId: "costliest", value: money(cost.net) });
  return (
    <div className="space-y-space-2">
      {kv.length > 0 && <KeyValueTable caption={section.title} rows={kv} />}
      {days && <ProgressStat label="Days inside your own daily limits" done={days.inside} of={days.days} />}
    </div>
  );
}

const RENDER: Partial<Record<SectionKind, (p: { section: RhythmSection }) => ReactNode>> = {
  mood: Mood,
  events: ({ section }) => <EventChips rows={section.rows ?? []} />,
  structure: Structure,
  setups: Setups,
  positions: Positions,
  budget: Budget,
  day: Day,
  market: Market,
  next: Next,
  week: Week,
};

/** True when the section has a typed view: a kind with a renderer and at least one row (else the caller shows lines). */
export function hasTypedView(section: RhythmSection): boolean {
  return Boolean(section.kind && RENDER[section.kind] && section.rows?.length);
}

/** The typed view of a section (check `hasTypedView` first). */
export function SectionRowsV2({ section }: { section: RhythmSection }): ReactNode {
  const render = section.kind ? RENDER[section.kind] : undefined;
  if (!render || !section.rows?.length) return null;
  return <div data-rows={section.kind}>{render({ section })}</div>;
}
