import { ArrowDown, ArrowUp, FileDown } from "lucide-react";
import { useMemo, useState } from "react";
import { Button, Sheet } from "../components/primitives";
import { useInPlace } from "../inplace/context";
import { saveBlob } from "../journal/journalModel";
import { chartTabPath } from "../shell/shellRoutes";
import { formatNumber, formatPct } from "../ui/format";
import { reportApi } from "./api";
import { changeTone, directionTone, directionWord, SECTION_TITLES, SORTS, sortRows, type SortKey } from "./model";
import type { DailyReport, ReportRow, SectionKey } from "./types";

/**
 * M88-R S5: the full daily market report in place (a sheet over the Copilot tab - no navigation away): the NIFTY read,
 * breadth and sectors, the sortable table of every stock (each row opens its chart in place), leaders / laggards,
 * notable structures and the conclusion, and the PDF. Every part carries the report's information line.
 */

const TH = "whitespace-nowrap px-space-2 py-space-1 text-left text-t12 font-medium text-fg-muted";
const TD = "whitespace-nowrap px-space-2 py-space-1 align-top text-t13";

function Info({ line }: { line: string }) {
  return <p className="text-t12 text-fg-muted" data-report-info>{line}</p>;
}

function Part({ title, line, children }: { title: string; line: string; children: React.ReactNode }) {
  return (
    <section className="space-y-space-2 border-t border-subtle pt-space-3 first:border-t-0 first:pt-0" data-report-part={title}>
      <h3 className="text-t15 font-semibold text-fg">{title}</h3>
      {children}
      <Info line={line} />
    </section>
  );
}

function Text({ report, k }: { report: DailyReport; k: SectionKey }) {
  return <p className="text-t14 leading-relaxed text-fg">{report.sections[k]}</p>;
}

function Stat({ label, value, tone = "text-fg" }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-control border border-subtle px-space-3 py-space-2">
      <div className="text-t12 text-fg-muted">{label}</div>
      <div className={`text-t15 font-semibold font-tabular ${tone}`}>{value}</div>
    </div>
  );
}

export function IndexStats({ row }: { row: ReportRow }) {
  if (row.missing) return <p className="text-t13 text-fg-muted">No close stored for this day.</p>;
  return (
    <div className="grid grid-cols-2 gap-space-2 sm:grid-cols-4">
      <Stat label={`${row.symbol} close`} value={formatNumber(row.close, 2)} />
      <Stat label="Change" value={formatPct(row.change_pct)} tone={changeTone(row.change_pct)} />
      <Stat label="Daily structure" value={directionWord(row.structure_d?.direction)} tone={directionTone(row.structure_d?.direction)} />
      <Stat label="Weekly structure" value={directionWord(row.structure_w?.direction)} tone={directionTone(row.structure_w?.direction)} />
    </div>
  );
}

function StockTable({ rows, onOpen }: { rows: ReportRow[]; onOpen: (symbol: string) => void }) {
  const [key, setKey] = useState<SortKey>("change_pct");
  const [desc, setDesc] = useState(true);
  const sorted = useMemo(() => sortRows(rows, key, desc), [rows, key, desc]);
  return (
    <div className="space-y-space-2">
      <div className="flex flex-wrap items-center gap-space-1" role="group" aria-label="Sort the stocks">
        {SORTS.map((s) => (
          <button key={s.key} type="button" aria-pressed={key === s.key}
            onClick={() => { if (key === s.key) setDesc(!desc); else { setKey(s.key); setDesc(s.key !== "symbol" && s.key !== "sector"); } }}
            className={`inline-flex min-h-9 items-center gap-space-1 rounded-control border px-space-2 text-t12 ${key === s.key ? "border-brand bg-brand/10 text-fg" : "border-subtle text-fg-muted hover:text-fg"}`}>
            {s.label}{key === s.key && (desc ? <ArrowDown size={12} aria-hidden /> : <ArrowUp size={12} aria-hidden />)}
          </button>
        ))}
      </div>
      <ul className="space-y-space-2 sm:hidden" data-report-stock-cards>
        {sorted.map((r) => (
          <li key={r.symbol} className="rounded-control border border-subtle p-space-3" data-report-card-row={r.symbol}>
            <div className="flex items-baseline justify-between gap-space-2">
              <button type="button" onClick={() => onOpen(r.symbol)} className="min-h-9 text-left text-t14 font-semibold text-fg underline-offset-2 hover:underline"
                aria-label={`Open the ${r.symbol} chart`}>{r.symbol}</button>
              <b className={`text-t14 font-tabular ${changeTone(r.change_pct)}`}>{r.missing ? "–" : formatPct(r.change_pct)}</b>
            </div>
            {r.sector && <div className="truncate text-t12 text-fg-muted">{r.sector}</div>}
            <div className="mt-space-1 flex flex-wrap gap-x-space-3 gap-y-space-1 text-t12 text-fg-muted">
              <span>D <b className={directionTone(r.structure_d?.direction)}>{directionWord(r.structure_d?.direction)}</b> · W <b className={directionTone(r.structure_w?.direction)}>{directionWord(r.structure_w?.direction)}</b></span>
              {r.volume_ratio != null && <span>Vol <b className="text-fg">{formatNumber(r.volume_ratio, 2)}×</b></span>}
              {r.fut_oi_change_pct != null && <span>OI <b className={changeTone(r.fut_oi_change_pct)}>{formatPct(r.fut_oi_change_pct, 1)}</b></span>}
              {r.pcr != null && <span>PCR <b className="text-fg">{formatNumber(r.pcr, 2)}</b></span>}
            </div>
            <p className="mt-space-1 text-t13 text-fg">{r.missing ? "No close stored for this day." : r.read ?? "–"}</p>
          </li>
        ))}
      </ul>
      <div className="-mx-space-1 hidden overflow-x-auto px-space-1 sm:block">
        <table className="w-full border-collapse" data-report-stocks>
          <thead><tr className="border-b border-subtle">
            <th className={`${TH} sticky left-0 bg-surface-1`}>Symbol</th><th className={`${TH} text-right`}>Change</th>
            <th className={`${TH} text-right`}>Vol x avg</th><th className={TH}>Structure D / W</th><th className={`${TH} text-right`}>Range pos.</th>
            <th className={`${TH} text-right`}>OI chg</th><th className={`${TH} text-right`}>PCR</th><th className={TH}>Read</th>
          </tr></thead>
          <tbody>
            {sorted.map((r) => (
              <tr key={r.symbol} className="border-b border-subtle" data-report-row={r.symbol}>
                <td className={`${TD} sticky left-0 bg-surface-1`}>
                  <button type="button" onClick={() => onOpen(r.symbol)} className="min-h-9 text-left font-semibold text-fg underline-offset-2 hover:underline"
                    aria-label={`Open the ${r.symbol} chart`}>{r.symbol}</button>
                  {r.sector && <div className="text-t12 text-fg-muted">{r.sector}</div>}
                </td>
                <td className={`${TD} text-right font-tabular ${changeTone(r.change_pct)}`}>{r.missing ? "–" : formatPct(r.change_pct)}</td>
                <td className={`${TD} text-right font-tabular text-fg`}>{r.volume_ratio == null ? "–" : `${formatNumber(r.volume_ratio, 2)}×`}</td>
                <td className={TD}>
                  <span className={directionTone(r.structure_d?.direction)}>{directionWord(r.structure_d?.direction)}</span>
                  <span className="text-fg-muted"> / </span>
                  <span className={directionTone(r.structure_w?.direction)}>{directionWord(r.structure_w?.direction)}</span>
                </td>
                <td className={`${TD} text-right font-tabular text-fg`}>{r.range_position_pct == null ? "–" : `${formatNumber(r.range_position_pct, 0)}%`}</td>
                <td className={`${TD} text-right font-tabular ${changeTone(r.fut_oi_change_pct)}`}>{r.fut_oi_change_pct == null ? "–" : formatPct(r.fut_oi_change_pct, 1)}</td>
                <td className={`${TD} text-right font-tabular text-fg`}>{r.pcr == null ? "–" : formatNumber(r.pcr, 2)}</td>
                <td className={`${TD} min-w-56 whitespace-normal text-fg`}>{r.missing ? "No close stored for this day." : r.read ?? "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function ReportBody({ report, onOpen }: { report: DailyReport; onOpen: (symbol: string) => void }) {
  const line = report.info_line;
  const b = report.breadth;
  return (
    <div className="space-y-space-4" data-daily-report-body>
      <Part title={SECTION_TITLES.index_read} line={line}>
        {report.index.map((r) => <IndexStats key={r.symbol} row={r} />)}
        <Text report={report} k="index_read" />
      </Part>
      <Part title={SECTION_TITLES.breadth_read} line={line}>
        <div className="grid grid-cols-2 gap-space-2 sm:grid-cols-4">
          <Stat label="Advances" value={formatNumber(b.advances)} tone="text-up" />
          <Stat label="Declines" value={formatNumber(b.declines)} tone="text-down" />
          <Stat label="New recent high" value={`${formatNumber(b.new_recent_high)} (${formatNumber(b.new_recent_high_pct, 0)}%)`} />
          <Stat label="New recent low" value={`${formatNumber(b.new_recent_low)} (${formatNumber(b.new_recent_low_pct, 0)}%)`} />
        </div>
        <Text report={report} k="breadth_read" />
      </Part>
      <Part title={SECTION_TITLES.sectors_read} line={line}>
        <ul className="grid gap-space-1 sm:grid-cols-2" data-report-sectors>
          {report.sectors.map((s) => (
            <li key={s.sector} className="flex items-center justify-between gap-space-2 rounded-control border border-subtle px-space-3 py-space-1 text-t13">
              <span className="min-w-0 truncate text-fg">{s.sector} <span className="text-fg-muted">· {s.stocks}</span></span>
              <b className={`font-tabular ${changeTone(s.avg_change_pct)}`}>{formatPct(s.avg_change_pct)}</b>
            </li>
          ))}
        </ul>
        <Text report={report} k="sectors_read" />
      </Part>
      <Part title={`Stocks (${report.stocks.length})`} line={line}>
        <StockTable rows={report.stocks} onOpen={onOpen} />
      </Part>
      <Part title={SECTION_TITLES.leaders_laggards} line={line}>
        <div className="flex flex-wrap gap-space-1 text-t13">
          {report.leaders.map((s) => <span key={`l-${s}`} className="rounded-control bg-up/10 px-space-2 py-space-1 text-up">{s}</span>)}
          {report.laggards.map((s) => <span key={`g-${s}`} className="rounded-control bg-down/10 px-space-2 py-space-1 text-down">{s}</span>)}
        </div>
        <Text report={report} k="leaders_laggards" />
      </Part>
      <Part title={SECTION_TITLES.notable_structures} line={line}><Text report={report} k="notable_structures" /></Part>
      <Part title={SECTION_TITLES.conclusion} line={line}><Text report={report} k="conclusion" /></Part>
    </div>
  );
}

export default function DailyReportSheetV2({ report, open, onOpenChange }: { report: DailyReport; open: boolean; onOpenChange: (open: boolean) => void }) {
  const inPlace = useInPlace();
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function download() {
    setBusy(true); setNote(null);
    try {
      const { blob, filename } = await reportApi.pdf(report.id);
      saveBlob(blob, filename);
    } catch {
      setNote("Could not download the PDF. Try again.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Sheet open={open} onOpenChange={onOpenChange} wide title="Daily market report" description={report.info_line}
      footer={<>
        {note && <span className="mr-auto self-center text-t12 text-down" role="status">{note}</span>}
        <Button variant="secondary" onClick={() => void download()} disabled={busy} icon={<FileDown size={14} />}>Download PDF</Button>
      </>}>
      <ReportBody report={report} onOpen={(symbol) => { onOpenChange(false); inPlace.open(chartTabPath(symbol)); }} />
    </Sheet>
  );
}
