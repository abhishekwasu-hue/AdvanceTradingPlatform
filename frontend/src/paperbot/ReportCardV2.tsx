import { Share2 } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api } from "../api/client";
import { CardV2, LINK_SECONDARY, LoadFailedV2, LoadingRowsV2 } from "../dashboard/CardV2";
import { svgPng } from "../growth/DisciplineCardV2";
import { cardSvgOf } from "../growth/model";
import { Chip } from "../ui/Chip";
import { Details } from "../ui/Details";
import { Signed } from "../ui/Signed";
import {
  SHARE_FILE, capText, rBasis, rText, rowName, shareCard, shareText, winText, type PaperReportCard, type ReportRow,
} from "./reportCard";

/**
 * AUD-T4 (owner audit 28 Oct; flags `ui_v2` + `paper_report_card`): the paper report card on the Paper bot page, under
 * "Your bots" - per bot (or per strategy) the closed paper trades, win %, average R, max drawdown and, when they apply,
 * credit kept on written options and plan followed from the journal. A forward test of his own paper trades, not a
 * backtest. Only the numbers stay open; how each is worked out is in the card's one Details. Share makes an image on
 * this device when tapped (no name, no amount, no id) - nothing is uploaded.
 */

type State = { status: "loading" } | { status: "error" } | { status: "ok"; data: PaperReportCard };
type View = "bot" | "strategy";

async function share(row: ReportRow): Promise<string | null> {
  const card = shareCard(row);
  const blob = await svgPng(cardSvgOf(card));
  const file = new File([blob], SHARE_FILE, { type: "image/png" });
  if (typeof navigator !== "undefined" && typeof navigator.share === "function") {
    const data: ShareData = { title: card.title, text: shareText(card) };
    if (navigator.canShare?.({ files: [file] })) data.files = [file];
    try { await navigator.share(data); } catch { /* he closed the share sheet */ }
    return null;
  }
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = SHARE_FILE;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return "Saved as an image.";
}

function Stat({ label, value, sub, testId }: { label: string; value: ReactNode; sub?: string | null; testId: string }) {
  return (
    <div className="min-w-0" data-stat={testId}>
      <dt className="text-t12 text-fg-muted">{label}</dt>
      <dd className="text-t15 font-semibold tabular-nums text-fg">{value}</dd>
      {sub && <dd className="text-t12 text-fg-muted">{sub}</dd>}
    </div>
  );
}

export function ReportRowV2({ row, names, onShare }: {
  row: ReportRow; names?: ReadonlyMap<string, string>; onShare?: (row: ReportRow) => void;
}) {
  return (
    <li className="space-y-space-2 py-space-3 first:pt-0 last:pb-0" data-report-row={row.key}>
      <div className="flex flex-wrap items-center justify-between gap-space-2">
        <p className="flex min-w-0 flex-wrap items-center gap-space-2 text-t15 font-semibold text-fg">
          <span className="min-w-0 break-words">{rowName(row, names)}</span>
          {row.symbol && <Chip tone="neutral" variant="meaning">{row.symbol}</Chip>}
        </p>
        {onShare && row.closed > 0 && (
          <button type="button" className={LINK_SECONDARY} onClick={() => onShare(row)} data-report-share>
            <Share2 size={16} aria-hidden />Share
          </button>
        )}
      </div>
      <dl className="grid grid-cols-2 gap-space-3 sm:grid-cols-3 lg:grid-cols-6">
        <Stat testId="closed" label="Closed trades" value={row.closed} />
        <Stat testId="win" label="Win %" value={winText(row.win_pct)} />
        <Stat testId="r" label="Avg R" value={rText(row.avg_r)} sub={rBasis(row)} />
        <Stat testId="drawdown" label="Max drawdown" value={row.max_drawdown > 0 ? <Signed value={-row.max_drawdown} kind="money" arrow={false} /> : "0"} />
        {row.credit_trades > 0 && <Stat testId="capture" label="Credit kept" value={capText(row.capture_pct)} />}
        {row.plan_pct != null && <Stat testId="plan" label="Plan followed" value={winText(row.plan_pct)} sub={`${row.plan_trades} journaled`} />}
      </dl>
    </li>
  );
}

export function ReportCardBodyV2({ data, view, onView, onShare, note, names }: {
  data: PaperReportCard; view: View; onView: (v: View) => void; onShare?: (row: ReportRow) => void; note?: string | null;
  names?: ReadonlyMap<string, string>;
}) {
  const rows = view === "bot" ? data.bots : data.strategies;
  if (data.total.closed === 0) return <p className="text-t14 text-fg-muted">No closed paper trades yet. The card fills in as your trades close.</p>;
  const pick = (v: View, label: string) => (
    <button type="button" aria-pressed={view === v} onClick={() => onView(v)} data-report-view={v}
      className={`inline-flex min-h-11 items-center rounded-control px-space-3 text-t13 font-medium md:min-h-9 ${view === v ? "bg-surface-2 text-fg" : "text-fg-muted hover:text-fg"}`}>
      {label}
    </button>
  );
  return (
    <div>
      <div className="mb-space-3 flex gap-space-1" role="group" aria-label="Group the report">{pick("bot", "By bot")}{pick("strategy", "By strategy")}</div>
      <ul className="divide-y divide-border-subtle">{rows.map((r) => <ReportRowV2 key={`${r.kind}:${r.key}`} row={r} names={names} onShare={onShare} />)}</ul>
      {note && <p className="mt-space-2 text-t12 text-fg-muted" role="status">{note}</p>}
      <Details id="paperbot.report-card">
        <p>A forward test: your own closed paper trades, not a backtest. Legs of one options structure count as one trade.</p>
        <p>Avg R: the net result divided by the risk at entry (entry to the invalidation level, times the quantity; for a structure, its
          maximum loss). Trades without an invalidation level are counted in the closed trades but left out of Avg R.</p>
        <p>Max drawdown: the largest fall of your running net result from its high, trades in the order they closed.</p>
        <p>Credit kept: for options you wrote, the realised net result as a share of the premium received at entry.</p>
        <p>{data.plan_note ?? "Plan followed: the share of your journaled trades without a FOMO entry or early exit tag, counted as your journal counts them."}</p>
        <p>{data.calibration.shown && data.calibration.brier != null
          ? `Calibration (Brier score, lower is closer): ${data.calibration.brier} over ${data.calibration.trades} trades.`
          : data.calibration.note}</p>
        <p>Share makes the image on this device when you tap it. It shows no stock or strategy name, no amount and no account details.</p>
      </Details>
    </div>
  );
}

export default function ReportCardV2({ names, load = () => api.paperReportCard() }: {
  /** The page's strategy names (library, templates, his own strategies). */
  names?: ReadonlyMap<string, string>;
  load?: () => Promise<PaperReportCard>;
}) {
  const [state, setState] = useState<State>({ status: "loading" });
  const [view, setView] = useState<View>("bot");
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    load().then((data) => { if (live) setState({ status: "ok", data }); }, () => { if (live) setState({ status: "error" }); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  async function onShare(row: ReportRow) {
    setNote(null);
    try { setNote(await share(row)); } catch { setNote("Could not make the image on this device."); }
  }
  return (
    <CardV2 title="Paper report card" testId="paper-report-card">
      {state.status === "loading" ? <LoadingRowsV2 rows={2} /> : state.status === "error" ? <LoadFailedV2 what="your report card" />
        : <ReportCardBodyV2 data={state.data} view={view} onView={setView} onShare={(r) => void onShare(r)} note={note} names={names} />}
    </CardV2>
  );
}
