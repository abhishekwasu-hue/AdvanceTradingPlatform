import { ChevronLeft, ChevronRight, Printer } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { CardV2, EmptyStateV2, LoadingRowsV2 } from "../dashboard/CardV2";
import { BUTTON_PRIMARY, BUTTON_SECONDARY, FIELD, LABEL } from "../trading/PartsV2";
import { chipClass } from "../ui/Chip";
import { ErrorState } from "../ui/ErrorState";
import { formatDateIST, formatNumber } from "../ui/format";
import { ResponsiveTable, type Column } from "../ui/ResponsiveTable";
import { PnlV2 } from "./JournalPartsV2";
import type { DailyCard, EntryKind, MonthlyReview, PlanCounts, ResultRow, ReviewGate, WeeklyExtras } from "./types";

/**
 * P2-8 J3: the reviews - a daily card, the weekly review's additions and a monthly summary he can print. Numbers from
 * his own trades only; below his "trades before percentages" setting (default 20) counts are shown, never a share, and
 * a group is marked a small sample. Every sentence comes from the server, where it passed the advice guard.
 */

export type Load<T> = { status: "loading" } | { status: "error"; error: unknown } | { status: "ok"; data: T };
export type ReviewPeriod = "day" | "week" | "month";

const PERIODS: { key: ReviewPeriod; label: string }[] = [{ key: "day", label: "Day" }, { key: "week", label: "Week" }, { key: "month", label: "Month" }];

export function PeriodSwitchV2({ value, onChange }: { value: ReviewPeriod; onChange: (p: ReviewPeriod) => void }) {
  return (
    <div className="flex gap-space-2" role="group" aria-label="Review period" data-review-period>
      {PERIODS.map((p) => (
        <button key={p.key} type="button" aria-pressed={value === p.key} onClick={() => onChange(p.key)} data-period={p.key}
          className={`${chipClass(value === p.key ? { tone: "brand", variant: "solid" } : { variant: "outline" })} min-h-9`}>{p.label}</button>
      ))}
    </div>
  );
}

function GateNoteV2({ gate }: { gate: ReviewGate }) {
  if (gate.ready) return null;
  return <p className="rounded-panel bg-surface-2 p-space-3 text-t13 text-fg-muted" data-review-gate>{gate.note}</p>;
}

export function planText(p: PlanCounts): string {
  if (!p.answered) return "Not answered yet";
  return `${formatNumber(p.yes)} of ${formatNumber(p.answered)}${p.rate != null ? ` (${formatNumber(p.rate, 0)}%)` : ""}`;
}

function Lines({ lines }: { lines: readonly string[] }) {
  return <ul className="list-disc space-y-space-1 pl-space-5 text-t14 text-fg" data-review-lines>{lines.map((l) => <li key={l}>{l}</li>)}</ul>;
}

function DoneWell({ text }: { text: string | null }) {
  if (!text) return null;
  return <p className="rounded-panel border border-subtle p-space-3 text-t14 text-fg" data-done-well><span className="font-semibold">Done well: </span>{text}</p>;
}

function Stepper({ label, onStep }: { label: string; onStep: (by: number) => void }) {
  return (
    <span className="flex items-center gap-space-2">
      <button type="button" className={BUTTON_SECONDARY} onClick={() => onStep(-1)} aria-label="Earlier"><ChevronLeft size={16} aria-hidden /></button>
      <span className="font-tabular text-t14 text-fg" data-review-label>{label}</span>
      <button type="button" className={BUTTON_SECONDARY} onClick={() => onStep(1)} aria-label="Later"><ChevronRight size={16} aria-hidden /></button>
    </span>
  );
}

function Frame<T>({ title, testId, state, onRetry, children, head }: {
  title: string; testId: string; state: Load<T>; onRetry: () => void; children: (d: T) => ReactNode; head?: ReactNode;
}) {
  return (
    <CardV2 title={title} testId={testId}>
      <div className="space-y-space-4">
        {head}
        {state.status === "loading" && <LoadingRowsV2 rows={3} />}
        {state.status === "error" && <ErrorState message={state.error} onRetry={onRetry} variant="inline" />}
        {state.status === "ok" && children(state.data)}
      </div>
    </CardV2>
  );
}

export function DailyCardV2({ state, day, onStep, onRetry, onOpen }: {
  state: Load<DailyCard>; day: string; onStep: (by: number) => void; onRetry: () => void; onOpen: (kind: EntryKind, id: number) => void;
}) {
  return (
    <Frame title="Your day" testId="daily-card" state={state} onRetry={onRetry} head={<Stepper label={formatDateIST(`${day}T06:00:00Z`)} onStep={onStep} />}>
      {(d) => (
        <>
          <GateNoteV2 gate={d.gate} />
          {d.trades === 0 ? <EmptyStateV2 text="No trades on this day." /> : (
            <>
              <dl className="grid grid-cols-2 gap-space-3 md:grid-cols-4" data-daily-tiles>
                <Tile label="Trades" value={formatNumber(d.trades)} />
                <Tile label="Result" value={d.net_pnl == null ? "Open" : <PnlV2 value={d.net_pnl} />} />
                <Tile label="Plan followed" value={planText(d.plan)} />
                <Tile label="Closed" value={formatNumber(d.closed)} />
              </dl>
              {d.feelings.length > 0 && (
                <p className="flex flex-wrap items-center gap-space-1 text-t13 text-fg-muted" data-daily-feelings>
                  Feelings:
                  {d.feelings.map((f) => <span key={f.feeling} className={chipClass({ variant: "outline" })}>{f.feeling} · {f.count}</span>)}
                </p>
              )}
              {d.confirmed_tags.length > 0 && (
                <p className="flex flex-wrap gap-space-1" data-daily-tags>
                  {d.confirmed_tags.map((t) => <span key={t.tag} className={chipClass({ variant: "outline" })}>{t.label} · {t.count}</span>)}
                </p>
              )}
              <DoneWell text={d.done_well} />
              <ul className="flex flex-wrap gap-space-2">
                {d.entries.map((e) => (
                  <li key={`${e.kind}-${e.id}`}>
                    <button type="button" className={BUTTON_SECONDARY} onClick={() => onOpen(e.kind, e.id)}>{e.symbol} <PnlV2 value={e.pnl} /></button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </Frame>
  );
}

function Tile({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="min-w-0 rounded-panel border border-subtle p-space-3">
      <dt className="text-t12 text-fg-muted">{label}</dt>
      <dd className="m-0 truncate font-tabular text-t15 font-semibold text-fg">{value}</dd>
    </div>
  );
}

const RESULT_COLUMNS = (head: string): readonly Column<ResultRow>[] => [
  { key: "value", header: head, cell: (r) => <span className="text-fg">{String(r.value)}{r.small && <span className="ml-space-1 text-t12 text-fg-muted">small sample</span>}</span> },
  { key: "trades", header: "Trades", align: "right", cell: (r) => formatNumber(r.trades) },
  { key: "wins", header: "Wins", align: "right", cell: (r) => (r.win_rate != null ? `${formatNumber(r.wins)} (${formatNumber(r.win_rate, 0)}%)` : formatNumber(r.wins)) },
  { key: "net", header: "Net", align: "right", cell: (r) => <PnlV2 value={r.net_pnl} /> },
];

export function ReflectionV2({ question, answer, onSave }: { question: string; answer: string | null; onSave: (text: string) => Promise<void> }) {
  const [text, setText] = useState(answer ?? "");
  const [busy, setBusy] = useState(false);
  useEffect(() => { setText(answer ?? ""); }, [answer]);
  return (
    <label className="block min-w-0" data-reflection>
      <span className={LABEL}>{question}</span>
      <span className="flex gap-space-2">
        <input className={FIELD} value={text} maxLength={1000} onChange={(e) => setText(e.target.value)} data-reflection-input />
        <button type="button" className={BUTTON_PRIMARY} disabled={busy || text === (answer ?? "")} data-reflection-save
          onClick={async () => { setBusy(true); try { await onSave(text); } finally { setBusy(false); } }}>Save</button>
      </span>
    </label>
  );
}

export function WeeklyExtrasV2({ state, onRetry, onOpen, onAnswer }: {
  state: Load<WeeklyExtras>; onRetry: () => void; onOpen: (kind: EntryKind, id: number) => void; onAnswer: (week: string, text: string) => Promise<void>;
}) {
  return (
    <Frame title="Your week in your own words" testId="weekly-extras" state={state} onRetry={onRetry}>
      {(d) => (
        <>
          <GateNoteV2 gate={d.gate} />
          {d.lines.length > 0 ? <Lines lines={d.lines} /> : <EmptyStateV2 text="Check in on a trade to see this part." />}
          <DoneWell text={d.done_well} />
          {d.feelings.length > 0 && <ResponsiveTable caption="Results by feeling before" columns={RESULT_COLUMNS("Feeling")} rows={d.feelings} rowKey={(r) => String(r.value)} phone="stack" />}
          {d.confidence.length > 0 && <ResponsiveTable caption="Results by confidence" columns={RESULT_COLUMNS("Confidence")} rows={d.confidence} rowKey={(r) => String(r.value)} phone="stack" />}
          {d.notable.length > 0 && (
            <div>
              <span className={LABEL}>Notable trades</span>
              <ul className="flex flex-wrap gap-space-2" data-notable>
                {d.notable.map((n) => (
                  <li key={`${n.kind}-${n.id}`}><button type="button" className={BUTTON_SECONDARY} onClick={() => onOpen(n.kind, n.id)}>{n.symbol} <PnlV2 value={n.pnl} /></button></li>
                ))}
              </ul>
            </div>
          )}
          <ReflectionV2 question={d.question} answer={d.answer} onSave={(text) => onAnswer(d.week, text)} />
        </>
      )}
    </Frame>
  );
}

const SETUP_COLUMNS: readonly Column<MonthlyReview["setups"][number]>[] = [
  { key: "setup", header: "Setup", cell: (r) => <span className="text-fg">{r.setup}{r.small && <span className="ml-space-1 text-t12 text-fg-muted">small sample</span>}</span> },
  { key: "trades", header: "Trades", align: "right", cell: (r) => formatNumber(r.trades) },
  { key: "net", header: "Net", align: "right", cell: (r) => <PnlV2 value={r.net_pnl} /> },
];
const WEEK_COLUMNS: readonly Column<MonthlyReview["weeks"][number]>[] = [
  { key: "week", header: "Week", cell: (r) => r.week },
  { key: "score", header: "Discipline", align: "right", cell: (r) => (r.score == null ? "–" : `${formatNumber(r.score)} / 100`) },
  { key: "plan", header: "Plan followed", align: "right", cell: (r) => planText(r.plan) },
  { key: "trades", header: "Trades", align: "right", cell: (r) => formatNumber(r.trades) },
];

export function MonthlyV2({ state, month, onStep, onRetry, onAnswer }: {
  state: Load<MonthlyReview>; month: string; onStep: (by: number) => void; onRetry: () => void; onAnswer: (month: string, text: string) => Promise<void>;
}) {
  const head = (
    <span className="flex flex-wrap items-center justify-between gap-space-2">
      <Stepper label={month} onStep={onStep} />
      <button type="button" className={BUTTON_SECONDARY} onClick={() => window.print()} data-month-print><Printer size={16} aria-hidden />Print</button>
    </span>
  );
  return (
    <Frame title="Your month" testId="monthly-review" state={state} onRetry={onRetry} head={head}>
      {(d) => (
        <>
          <GateNoteV2 gate={d.gate} />
          <Lines lines={d.lines} />
          <DoneWell text={d.done_well} />
          {d.weeks.length > 0 && <ResponsiveTable caption="Week by week" columns={WEEK_COLUMNS} rows={d.weeks} rowKey={(r) => r.week} phone="stack" />}
          {d.setups.length > 0 && <ResponsiveTable caption="By setup" columns={SETUP_COLUMNS} rows={d.setups} rowKey={(r) => r.setup} phone="stack" />}
          <ReflectionV2 question={d.question} answer={d.answer} onSave={(text) => onAnswer(d.month, text)} />
        </>
      )}
    </Frame>
  );
}

/** "2026-11-20" moved by `by` days; "2026-11" moved by `by` months (pure, no time zone). */
export function shiftDay(day: string, by: number): string {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + by);
  return d.toISOString().slice(0, 10);
}

export function shiftMonth(month: string, by: number): string {
  const [y, m] = month.split("-").map(Number);
  const total = y * 12 + (m - 1) + by;
  return `${Math.floor(total / 12)}-${String((total % 12) + 1).padStart(2, "0")}`;
}
