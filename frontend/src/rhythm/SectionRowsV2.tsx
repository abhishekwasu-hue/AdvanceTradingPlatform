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
import { Sparkline } from "../ui/Sparkline";
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
  const expiries = rowsOf(rows, "expiry");
  const notes = events.filter((e) => e.window || e.detail || e.pauses_entries);
  return (
    <>
      <div className="flex flex-wrap gap-space-2" data-event-chips>
        {events.map((e, i) => (
          <Chip key={`e${i}`} tone={e.pauses_entries ? "warn" : "info"} variant="meaning" icon="◆" name="event">
            {dayLabel(e.date)} · {e.label}{e.scope ? ` (${e.scope})` : ""}
          </Chip>
        ))}
        {expiries.map((x) => (
          <Chip key={`x${x.date}${x.underlying}`} tone="warn" variant="meaning" icon="!" name="expiry">
            {dayLabel(x.date)} · {x.underlying} expiry ({x.count})
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
          { key: "s", header: "Symbol", cell: (r) => (
            <span className="inline-flex flex-wrap items-center gap-space-1 font-medium">
              {r.symbol}<DirectionChip side={r.side === "SHORT" ? "short" : "long"} />
              {r.near && <Chip tone="warn" variant="meaning" icon="!" name="near">Near invalidation</Chip>}
            </span>) },
          { key: "pnl", header: "Open P&L", align: "right", cell: (r) => money(r.pnl) },
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

// --- the dish pages (UX-11b C3; app/growth/dishes.py) ---------------------------------------------------------------

const winPct = (won: number, closed: number) => (closed > 0 ? `${formatNumber((100 * won) / closed, 0)}%` : "–");

function Waiting({ section }: { section: RhythmSection }) {
  const n = rowsOf(section.rows ?? [], "waiting")[0];
  return (
    <div className="space-y-space-2">
      {n && <Chip tone="info" variant="meaning" icon="◆" name="waiting">{n.count} waiting for you</Chip>}
      {rowsOf(section.rows ?? [], "setup").length > 0 && <Setups section={section} />}
    </div>
  );
}

function Sources({ section }: { section: RhythmSection }) {
  const r = rowsOf(section.rows ?? [], "sources")[0];
  if (!r) return null;
  return (
    <div className="flex flex-wrap gap-space-2">
      {Object.entries(r.counts).map(([k, n]) => (
        <Chip key={k} tone="neutral" variant="meaning" icon="◆" name={`source-${k}`}>{n} from {r.words[k] ?? k}</Chip>
      ))}
      {r.more && <span className="text-t13 text-fg-muted">and more on the list</span>}
    </div>
  );
}

function Record({ section }: { section: RhythmSection }) {
  const rows = rowsOf(section.rows ?? [], "strategy_record");
  return (
    <ResponsiveTable caption={section.title} phone="stack" rowKey={(r) => r.name} rows={rows}
      columns={[
        { key: "s", header: "Strategy", cell: (r) => r.name },
        { key: "w", header: "Win %", align: "right", cell: (r) => winPct(r.won, r.closed) },
        { key: "c", header: "Closed", align: "right", cell: (r) => formatNumber(r.closed) },
        { key: "won", header: "Won", align: "right", cell: (r) => <span className="text-up">▲ {formatNumber(r.won)}</span> },
        { key: "lost", header: "Lost", align: "right", cell: (r) => <span className="text-down">▼ {formatNumber(r.lost)}</span> },
      ]} />
  );
}

function Risk({ section }: { section: RhythmSection }) {
  const r = rowsOf(section.rows ?? [], "risk")[0];
  if (!r) return null;
  const kv: KeyValueRow[] = [{ label: `Open P&L (${r.positions} position${r.positions === 1 ? "" : "s"})`, testId: "open", value: money(r.open_pnl) }];
  if (r.at_invalidation != null) kv.push({ label: "If each reached its invalidation", testId: "at-invalidation", value: money(r.at_invalidation) });
  const share = r.of_loss_limit_pct;
  const level = share != null ? budgetLevel(share, 100) : null;
  return (
    <div className="space-y-space-2">
      <KeyValueTable caption={section.title} rows={kv} />
      {share != null && level && <MeaningBar label="Of your daily loss limit" share={share / 100} level={level} text={`${formatNumber(share)}%`} />}
      <div className="flex flex-wrap gap-space-2">
        {r.no_invalidation > 0 && <Chip tone="warn" variant="meaning" icon="!" name="no-invalidation">{r.no_invalidation} without an invalidation</Chip>}
        {r.prices_missing && <Chip tone="neutral" variant="meaning" icon="◆" name="prices-missing">Some prices not in yet</Chip>}
      </div>
    </div>
  );
}

function Premium({ section }: { section: RhythmSection }) {
  const rows = rowsOf(section.rows ?? [], "premium");
  return (
    <ResponsiveTable caption={section.title} phone="stack" rowKey={(r) => r.label} rows={rows}
      columns={[
        { key: "p", header: "Position", cell: (r) => r.label },
        { key: "k", header: "Kept", align: "right", cell: (r) => (r.kept_pct == null ? "–" : `${formatNumber(r.kept_pct, 0)}%`) },
        { key: "v", header: "Kept ₹", align: "right", cell: (r) => money(r.kept) },
        { key: "r", header: "Received", align: "right", cell: (r) => formatINR(r.received) },
      ]} />
  );
}

function Discipline({ section }: { section: RhythmSection }) {
  const d = rowsOf(section.rows ?? [], "discipline")[0];
  if (!d) return null;
  const series = [...(d.history ?? []), d.score];
  return (
    <div className="flex items-center justify-between gap-space-3" data-discipline>
      <div>
        <p className="font-tabular text-t24 font-semibold text-fg">{d.score}<span className="text-t14 font-normal text-fg-muted"> / 100</span></p>
        {d.previous != null && (
          <p className="text-t13 text-fg-muted"><Signed value={d.score - d.previous} kind="count" /> on last week</p>
        )}
      </div>
      {series.length >= 2 && (
        <Sparkline values={series} tone={d.previous == null || d.score === d.previous ? "neutral" : d.score > d.previous ? "up" : "down"} />
      )}
    </div>
  );
}

function Extremes({ section }: { section: RhythmSection }) {
  const rows = section.rows ?? [];
  const pair = [...rowsOf(rows, "best"), ...rowsOf(rows, "worst")];
  if (pair.length === 0) return rowsOf(rows, "even").length ? <p className="text-t14 text-fg-muted">About even over four weeks.</p> : null;
  return (
    <ResponsiveTable caption={section.title} rowKey={(r) => r.type} rows={pair}
      columns={[
        // Two columns so it fits a phone (owner, 28 Oct): the closed count sits under the setup's name.
        { key: "n", header: "Setup", cell: (r) => (
          <span className="block min-w-0" data-extreme={r.type}>
            <span className="block text-t12 text-fg-muted">{r.type === "best" ? "Best" : "Cost the most"}</span>
            <span className="block">{r.name}</span>
            <span className="block font-tabular text-t12 text-fg-muted">{formatNumber(r.closed)} closed</span>
          </span>) },
        { key: "v", header: "Net", align: "right", cell: (r) => money(r.net) },
      ]} />
  );
}

function Habits({ section }: { section: RhythmSection }) {
  const rows = section.rows ?? [];
  const habits = rowsOf(rows, "habit");
  const tags = rowsOf(rows, "suggested_tags")[0];
  const bare = rowsOf(rows, "no_note")[0];
  return (
    <div className="space-y-space-2">
      {habits.length > 0 && (
        <ResponsiveTable caption={section.title} phone="stack" rowKey={(r) => r.label} rows={habits}
          columns={[
            { key: "t", header: "Pattern", cell: (r) => r.label },
            { key: "n", header: "Trades", align: "right", cell: (r) => formatNumber(r.trades) },
            { key: "v", header: "Net", align: "right", cell: (r) => (r.net == null ? "–" : money(r.net)) },
          ]} />
      )}
      <div className="flex flex-wrap gap-space-2">
        {tags && <Chip tone="warn" variant="meaning" icon="!" name="suggested-tags">{tags.count} tag{tags.count === 1 ? "" : "s"} to confirm</Chip>}
        {bare && <Chip tone="neutral" variant="meaning" icon="◆" name="no-note">{bare.count} of {bare.closed} without a note</Chip>}
      </div>
    </div>
  );
}

const PLACE: Record<"ABOVE_HIGH" | "BELOW_LOW" | "BETWEEN", { tone: "up" | "down" | "neutral"; mark: string; label: string }> = {
  ABOVE_HIGH: { tone: "up", mark: "▲", label: "Above the last swing high" },
  BELOW_LOW: { tone: "down", mark: "▼", label: "Below the last swing low" },
  BETWEEN: { tone: "neutral", mark: "◆", label: "Between the last swings" },
};

function Story({ section }: { section: RhythmSection }) {
  const r = rowsOf(section.rows ?? [], "story")[0];
  if (!r) return null;
  const place = r.place ? PLACE[r.place] : null;
  return (
    <KeyValueTable caption={section.title} rows={[
      { label: "Daily swings", testId: "daily", value: <TrendChip trend={r.daily} /> },
      ...(r.weekly ? [{ label: "Weekly swings", testId: "weekly", value: <TrendChip trend={r.weekly} /> }] : []),
      ...(r.event ? [{ label: "Last event", testId: "event", value: <StructureEventChip event={r.event} /> }] : []),
      ...(place ? [{ label: "Last close", testId: "place", value: <Chip tone={place.tone} variant="meaning" icon={place.mark} name="place">{place.label}</Chip> }] : []),
      ...(r.as_of ? [{ label: "Read from", testId: "as-of", value: <AsOf iso={r.as_of} what="close of" /> }] : []),
    ] satisfies KeyValueRow[]} />
  );
}

function Yours({ section }: { section: RhythmSection }) {
  const rows = section.rows ?? [];
  const held = rowsOf(rows, "held")[0];
  const setup = rowsOf(rows, "setup")[0];
  const lists = rowsOf(rows, "lists")[0];
  const kv: KeyValueRow[] = [];
  if (held) {
    kv.push({ label: `Open here (${held.count})`, testId: "held", value: (
      <span className="inline-flex items-center gap-space-2">{held.near && <Chip tone="warn" variant="meaning" icon="!" name="near">Near invalidation</Chip>}{money(held.open_pnl)}</span>) });
  }
  if (setup) {
    kv.push({ label: setup.today ? "Your setup today" : "Your latest setup", testId: "setup", value: (
      setup.side ? <DirectionChip side={setup.side === "LONG" ? "long" : "short"} /> : <span>{setup.source === "scan" ? "Scan match" : "Strategy"}</span>) });
  }
  return (
    <div className="space-y-space-2">
      {kv.length > 0 && <KeyValueTable caption={section.title} rows={kv} />}
      {lists && (
        <div className="flex flex-wrap gap-space-2">
          {lists.names.map((n) => <Chip key={n} tone="neutral" variant="meaning" icon="◆" name="list">{n}</Chip>)}
        </div>
      )}
      {setup && <Details id={`dish.${section.id}.setup`} label="Why"><p>{setup.detail}</p></Details>}
    </div>
  );
}

function Matches({ section }: { section: RhythmSection }) {
  const rows = section.rows ?? [];
  const scans = rowsOf(rows, "scan");
  const listed = rowsOf(rows, "on_lists")[0];
  return (
    <div className="space-y-space-2">
      <ResponsiveTable caption={section.title} phone="stack" rowKey={(r) => r.name} rows={scans}
        columns={[
          { key: "n", header: "Scan", cell: (r) => r.name },
          { key: "c", header: "Matches", align: "right", cell: (r) => formatNumber(r.count) },
          { key: "s", header: "Symbols", cell: (r) => r.symbols.slice(0, 4).join(", ") + (r.symbols.length > 4 ? ` +${r.symbols.length - 4}` : "") },
        ]} />
      {listed && <p className="text-t13 text-fg-muted">On your lists: <span className="text-fg">{listed.symbols.join(", ")}</span></p>}
    </div>
  );
}

function Scans({ section }: { section: RhythmSection }) {
  const a = rowsOf(section.rows ?? [], "active")[0];
  return a ? <Chip tone="info" variant="meaning" icon="◆" name="active-scans">{a.count} scan{a.count === 1 ? "" : "s"} running on their own</Chip> : null;
}

const RENDER: Partial<Record<SectionKind, (p: { section: RhythmSection }) => ReactNode>> = {
  waiting: Waiting,
  sources: Sources,
  record: Record,
  risk: Risk,
  premium: Premium,
  discipline: Discipline,
  extremes: Extremes,
  habits: Habits,
  story: Story,
  yours: Yours,
  matches: Matches,
  scans: Scans,
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
