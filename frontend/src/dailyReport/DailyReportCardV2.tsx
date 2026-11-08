import { FileText } from "lucide-react";
import { lazy, Suspense, useEffect, useState } from "react";
import { ApiError } from "../api/errors";
import { Button, Skeleton } from "../components/primitives";
import { useFeatures } from "../ui/useFeatures";
import { formatNumber, formatPct } from "../ui/format";
import { reportApi } from "./api";
import { changeTone, directionTone, directionWord } from "./model";
import type { DailyReport } from "./types";

const Sheet = lazy(() => import("./DailyReportSheetV2"));

/**
 * M88-R S5: the "Daily market report" card in the Copilot's Read the chart tab (Market pulse). It reads the STORED report
 * (no AI call per user): the NIFTY close and structure, breadth and the conclusion; "Open the full report" opens the
 * sheet in place. Hidden while `shared_daily_report` is off. The Telegram message's link (`?report=open`) opens the sheet.
 */
export function openedFromLink(search: string): boolean {
  return new URLSearchParams(search).get("report") === "open";
}

export function ReportCardView({ report, onOpen }: { report: DailyReport | null; onOpen: () => void }) {
  if (!report) {
    return <p className="text-t13 text-fg-muted" data-report-empty>The first report is stored after the next market close.</p>;
  }
  const idx = report.index.find((r) => !r.missing);
  const b = report.breadth;
  return (
    <div className="space-y-space-3" data-report-card={report.trade_date}>
      {idx && (
        <div className="flex flex-wrap items-baseline gap-x-space-3 gap-y-space-1">
          <span className="text-t14 font-semibold text-fg">{idx.symbol}</span>
          <span className="text-t15 font-semibold font-tabular text-fg">{formatNumber(idx.close, 2)}</span>
          <span className={`text-t14 font-tabular ${changeTone(idx.change_pct)}`}>{formatPct(idx.change_pct)}</span>
          <span className="text-t13 text-fg-muted">Daily <b className={directionTone(idx.structure_d?.direction)}>{directionWord(idx.structure_d?.direction)}</b>
            {" · "}Weekly <b className={directionTone(idx.structure_w?.direction)}>{directionWord(idx.structure_w?.direction)}</b></span>
        </div>
      )}
      <div className="flex flex-wrap gap-space-1 text-t12">
        <span className="rounded-control border border-subtle px-space-2 py-space-1 text-fg">Advances <b className="text-up">{formatNumber(b.advances)}</b></span>
        <span className="rounded-control border border-subtle px-space-2 py-space-1 text-fg">Declines <b className="text-down">{formatNumber(b.declines)}</b></span>
        <span className="rounded-control border border-subtle px-space-2 py-space-1 text-fg">Stocks <b>{formatNumber(report.stocks.length)}</b></span>
      </div>
      <p className="line-clamp-3 text-t14 text-fg">{report.sections.conclusion}</p>
      <Button onClick={onOpen} icon={<FileText size={14} />}>Open the full report</Button>
      <p className="text-t12 text-fg-muted" data-report-info>{report.info_line}</p>
    </div>
  );
}

export default function DailyReportCardV2() {
  const on = useFeatures().on("shared_daily_report");
  const [report, setReport] = useState<DailyReport | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!on) return undefined;
    let live = true;
    reportApi.latest().then((r) => {
      if (!live) return;
      setReport(r.report);
      if (r.report && openedFromLink(window.location.search)) setOpen(true);
    }, (e: unknown) => {
      if (!live) return;
      if (e instanceof ApiError && e.status === 404) setReport(null);
      else setError("Could not load the daily report. Try again later.");
    });
    return () => { live = false; };
  }, [on]);
  if (!on) return null;
  return (
    <section aria-labelledby="daily-report-title" className="rounded-card border border-border bg-surface-1 p-space-4" data-daily-report>
      <h3 id="daily-report-title" className="mb-space-2 text-t15 font-semibold text-fg">Daily market report</h3>
      {error ? <p className="text-t13 text-fg-muted">{error}</p>
        : report === undefined ? <Skeleton className="h-24" label="Loading" />
          : <ReportCardView report={report} onOpen={() => setOpen(true)} />}
      {report && open && <Suspense fallback={null}><Sheet report={report} open={open} onOpenChange={setOpen} /></Suspense>}
    </section>
  );
}
