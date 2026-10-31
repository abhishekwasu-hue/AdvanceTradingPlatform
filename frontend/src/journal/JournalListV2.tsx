import { FileDown, Plus, SlidersHorizontal } from "lucide-react";
import { useState, type ReactNode } from "react";
import { CardV2, EmptyStateV2, LoadingRowsV2 } from "../dashboard/CardV2";
import { BUTTON_SECONDARY, FIELD, LABEL } from "../trading/PartsV2";
import { chipClass } from "../ui/Chip";
import { Combobox } from "../ui/Combobox";
import { ErrorState } from "../ui/ErrorState";
import { formatDateIST, formatTimeIST } from "../ui/format";
import { PHONE_QUERY, useMediaQuery } from "../utils/useMediaQuery";
import type { JournalApi } from "./api";
import { ChartThumbV2, PnlV2, RV2, TagChipsV2 } from "./JournalPartsV2";
import { OUTCOME_LABELS, activeFilters, entryTags, filtersOn, rowKey, tagText } from "./journalModel";
import type { JournalFilters, JournalList, JournalListRow, Outcome } from "./types";

/**
 * M62 JN-3: the journal list first - date, symbol, setup, net P&L (signed ₹), R, tags and the entry chart - with the
 * filters above it (setup, tag, outcome, dates, outside-ATP entries) and the export buttons. A phone gets one card per
 * entry; a wider screen a table. A tap opens the entry editor.
 */

export type ListState = { status: "loading" } | { status: "error"; error: unknown } | { status: "ok"; data: JournalList };

const ALL = "";

export function FiltersV2({ value, onChange, setups, tags }: {
  value: JournalFilters; onChange: (f: JournalFilters) => void; setups: readonly string[]; tags: readonly string[];
}) {
  const set = (patch: Partial<JournalFilters>) => onChange({ ...value, ...patch });
  const labelled = (text: string, control: ReactNode) => <div className="min-w-0"><span aria-hidden className={LABEL}>{text}</span>{control}</div>;
  return (
    <div className="grid grid-cols-2 gap-space-3 md:grid-cols-6" data-journal-filters>
      {labelled("Setup", <Combobox label="Setup" value={value.setup ?? ALL} onChange={(v) => set({ setup: v || undefined })} dataAttrs={{ "data-filter": "setup" }}
        options={[{ value: ALL, label: "All setups" }, ...setups.map((s) => ({ value: s, label: s }))]} searchable={setups.length > 8} />)}
      {labelled("Tag", <Combobox label="Tag" value={value.tag ?? ALL} onChange={(v) => set({ tag: v || undefined })} dataAttrs={{ "data-filter": "tag" }}
        options={[{ value: ALL, label: "All tags" }, ...tags.map((t) => ({ value: t, label: tagText(t) }))]} searchable={tags.length > 8} />)}
      {labelled("Outcome", <Combobox label="Outcome" value={value.outcome ?? ALL} onChange={(v) => set({ outcome: (v || undefined) as Outcome | undefined })}
        dataAttrs={{ "data-filter": "outcome" }}
        options={[{ value: ALL, label: "All outcomes" }, ...(Object.keys(OUTCOME_LABELS) as Outcome[]).map((o) => ({ value: o, label: OUTCOME_LABELS[o] }))]} />)}
      <label className="min-w-0">
        <span className={LABEL}>From</span>
        <input type="date" className={FIELD} value={value.date_from ?? ""} max={value.date_to || undefined} data-filter="date_from"
          onChange={(e) => set({ date_from: e.target.value || undefined })} />
      </label>
      <label className="min-w-0">
        <span className={LABEL}>To</span>
        <input type="date" className={FIELD} value={value.date_to ?? ""} min={value.date_from || undefined} data-filter="date_to"
          onChange={(e) => set({ date_to: e.target.value || undefined })} />
      </label>
      <label className="col-span-2 flex min-h-11 items-center gap-space-2 self-end text-t14 text-fg md:col-span-1 md:min-h-9">
        <input type="checkbox" className="h-5 w-5 accent-brand" checked={value.include_manual !== false} data-filter="include_manual"
          onChange={(e) => set({ include_manual: e.target.checked ? undefined : false })} />
        Outside-ATP trades
      </label>
    </div>
  );
}

function when(iso: string | null): { date: string; time: string } {
  return iso ? { date: formatDateIST(iso), time: formatTimeIST(iso) } : { date: "–", time: "" };
}

function SourceChip({ row }: { row: JournalListRow }) {
  if (row.outside_atp) return <span className={chipClass({ tone: "warn", variant: "tint" })} data-outside-atp>Outside ATP</span>;
  return row.mode ? <span className={chipClass({ variant: "outline" })}>{row.mode === "PAPER" ? "Paper" : row.mode === "LIVE" ? "Live" : row.mode}</span> : null;
}

function RowCardV2({ row, api, onOpen }: { row: JournalListRow; api: Pick<JournalApi, "image">; onOpen: (r: JournalListRow) => void }) {
  const w = when(row.entry_time);
  return (
    <li>
      <button type="button" onClick={() => onOpen(row)} data-journal-row={rowKey(row)}
        className="flex w-full items-start gap-space-3 rounded-panel border border-subtle bg-surface-1 p-space-3 text-left hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
        <ChartThumbV2 api={api} path={row.chart} label={`${row.symbol} chart at entry`} />
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline justify-between gap-space-2">
            <span className="truncate text-t15 font-semibold text-fg">{row.symbol}</span>
            <PnlV2 value={row.pnl} />
          </span>
          <span className="mt-px flex items-baseline justify-between gap-space-2 text-t13 text-fg-muted">
            <span className="truncate">{row.setup ?? "–"}{row.timeframe ? ` · ${row.timeframe}` : ""}</span>
            <RV2 value={row.r_multiple} />
          </span>
          <span className="mt-space-1 flex flex-wrap items-center gap-space-1 text-t12 text-fg-muted">
            <span className="font-tabular">{w.date} {w.time}</span>
            <SourceChip row={row} />
            <TagChipsV2 tags={entryTags(row)} max={2} />
          </span>
        </span>
      </button>
    </li>
  );
}

function RowsTableV2({ rows, api, onOpen }: { rows: readonly JournalListRow[]; api: Pick<JournalApi, "image">; onOpen: (r: JournalListRow) => void }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-t14" data-journal-table>
        <caption className="sr-only">Journal entries</caption>
        <thead>
          <tr className="border-b border-subtle text-left text-t12 font-medium text-fg-muted">
            <th scope="col" className="py-space-2 pr-space-3 font-medium">Date</th>
            <th scope="col" className="py-space-2 pr-space-3 font-medium">Symbol</th>
            <th scope="col" className="py-space-2 pr-space-3 font-medium">Setup</th>
            <th scope="col" className="py-space-2 pr-space-3 text-right font-medium">Net P&amp;L</th>
            <th scope="col" className="py-space-2 pr-space-3 text-right font-medium">R</th>
            <th scope="col" className="py-space-2 pr-space-3 font-medium">Tags</th>
            <th scope="col" className="py-space-2 font-medium"><span className="sr-only">Chart</span></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const w = when(r.entry_time);
            return (
              <tr key={rowKey(r)} className="cursor-pointer border-b border-subtle last:border-0 hover:bg-surface-2" onClick={() => onOpen(r)}
                data-journal-row={rowKey(r)}>
                <td className="whitespace-nowrap py-space-2 pr-space-3 font-tabular text-fg-muted">{w.date}<span className="block text-t12">{w.time}</span></td>
                <td className="py-space-2 pr-space-3">
                  <button type="button" className="text-left font-semibold text-fg hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
                    onClick={(e) => { e.stopPropagation(); onOpen(r); }}>{r.symbol}</button>
                  <span className="ml-space-2"><SourceChip row={r} /></span>
                </td>
                <td className="py-space-2 pr-space-3 text-fg">{r.setup ?? "–"}{r.timeframe && <span className="block text-t12 text-fg-muted">{r.timeframe}</span>}</td>
                <td className="whitespace-nowrap py-space-2 pr-space-3 text-right"><PnlV2 value={r.pnl} /></td>
                <td className="whitespace-nowrap py-space-2 pr-space-3 text-right"><RV2 value={r.r_multiple} /></td>
                <td className="py-space-2 pr-space-3"><TagChipsV2 tags={entryTags(r)} /></td>
                <td className="py-space-2"><ChartThumbV2 api={api} path={r.chart} label={`${r.symbol} chart at entry`} /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function ExportButtonsV2({ onExport, busy }: { onExport: (format: "csv" | "pdf") => void; busy: "csv" | "pdf" | null }) {
  return (
    <span className="flex gap-space-2" data-journal-export>
      {(["csv", "pdf"] as const).map((f) => (
        <button key={f} type="button" className={BUTTON_SECONDARY} disabled={busy !== null} onClick={() => onExport(f)} data-export={f}>
          <FileDown size={16} aria-hidden />{busy === f ? "Preparing…" : `Export ${f.toUpperCase()}`}
        </button>
      ))}
    </span>
  );
}

export function JournalListV2({ state, filters, onFilters, onOpen, onAddManual, onRetry, onMore, api, exportButtons }: {
  state: ListState; filters: JournalFilters; onFilters: (f: JournalFilters) => void; onOpen: (r: JournalListRow) => void;
  onAddManual: () => void; onRetry: () => void; onMore: () => void; api: Pick<JournalApi, "image">; exportButtons: ReactNode;
}) {
  const phone = useMediaQuery(PHONE_QUERY);
  const [choices, setChoices] = useState<{ setups: string[]; tags: string[] }>({ setups: [], tags: [] });
  if (state.status === "ok" && (state.data.setups.join("|") !== choices.setups.join("|") || state.data.tags.join("|") !== choices.tags.join("|"))) {
    setChoices({ setups: state.data.setups, tags: state.data.tags });
  }
  const [showFilters, setShowFilters] = useState(false);
  const rows = state.status === "ok" ? state.data.entries : [];
  const active = activeFilters(filters);
  return (
    <CardV2 testId="journal-list" count={state.status === "ok" ? state.data.total : undefined}
      title="Your trades">
      <div className="space-y-space-4">
        <div className="flex flex-wrap items-center justify-between gap-space-2">
          <button type="button" className={BUTTON_SECONDARY} onClick={onAddManual} data-add-manual>
            <Plus size={16} aria-hidden />Add a trade done outside ATP
          </button>
          {exportButtons}
        </div>
        {/* a phone: the filters fold behind one button (the list comes first) */}
        {phone && (
          <button type="button" className={BUTTON_SECONDARY} aria-expanded={showFilters} onClick={() => setShowFilters((v) => !v)} data-filters-toggle>
            <SlidersHorizontal size={16} aria-hidden />{active ? `Filters (${active})` : "Filters"}
          </button>
        )}
        {(!phone || showFilters) && <FiltersV2 value={filters} onChange={onFilters} setups={choices.setups} tags={choices.tags} />}
        {state.status === "loading" && <LoadingRowsV2 rows={4} />}
        {state.status === "error" && <ErrorState message={state.error} onRetry={onRetry} variant="inline" />}
        {state.status === "ok" && rows.length === 0 && (
          <EmptyStateV2 text={filtersOn(filters) ? "No entries match these filters." : "No trades in your journal yet. Every PAPER or LIVE fill lands here."}
            action={filtersOn(filters) ? { label: "Clear filters", onClick: () => onFilters({}) } : undefined} />
        )}
        {rows.length > 0 && (phone
          ? <ul className="space-y-space-2" data-journal-cards>{rows.map((r) => <RowCardV2 key={rowKey(r)} row={r} api={api} onOpen={onOpen} />)}</ul>
          : <RowsTableV2 rows={rows} api={api} onOpen={onOpen} />)}
        {state.status === "ok" && state.data.truncated && (
          <button type="button" className={BUTTON_SECONDARY} onClick={onMore} data-show-more>Show more</button>
        )}
      </div>
    </CardV2>
  );
}
