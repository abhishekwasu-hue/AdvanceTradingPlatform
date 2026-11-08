import { Play, Save } from "lucide-react";
import { useEffect, useState } from "react";
import { Panel, Pill } from "../admin/AdminSectionsV2";
import { errorSentence } from "../api/errors";
import { Button, SkeletonRows } from "../components/primitives";
import { formatDateIST, formatDateTimeIST, formatINR, formatNumber } from "../ui/format";
import { reportApi } from "./api";
import type { ReportAdmin, ReportRun } from "./types";

/**
 * M88-R (Admin › System): the shared daily report's setting ("Shared report symbols", "Recent bars"), Run now on the
 * admin's own AI key, and the last runs - status, the reason a run stored nothing, AI requests, tokens and cost.
 */

const TH = "py-space-1 pr-space-3 text-left font-medium text-fg-muted";
const TD = "py-space-1 pr-space-3 align-top";

export function parseSymbols(text: string): string[] {
  return [...new Set(text.split(/[\n,]/).map((s) => s.trim().toUpperCase().replace(/\s+/g, " ")).filter(Boolean))];
}

export function runTone(run: ReportRun): "ok" | "warn" | "off" {
  return run.status === "ok" ? "ok" : run.status === "failed" ? "warn" : "off";
}

export function RunsTable({ runs }: { runs: ReportRun[] }) {
  if (!runs.length) return <p className="text-t13 text-fg-muted">No run yet.</p>;
  return (
    <div className="-mx-space-1 overflow-x-auto px-space-1">
      <table className="w-full text-t12" data-report-runs>
        <thead><tr><th className={TH}>Close</th><th className={TH}>Status</th><th className={TH}>AI requests</th><th className={TH}>Tokens</th>
          <th className={TH}>Cost</th><th className={TH}>Started by</th></tr></thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id} className="border-t border-subtle" data-report-run={r.status}>
              <td className={`${TD} whitespace-nowrap text-fg`}>{formatDateIST(`${r.trade_date}T10:00:00Z`)}</td>
              <td className={`${TD} min-w-40`}>
                <Pill tone={runTone(r)}>{r.status === "ok" ? "Stored" : r.status === "failed" ? "Not stored" : "Running"}</Pill>
                {r.error && <div className="mt-space-1 text-fg">{r.error}</div>}
                {r.next_try_at && <div className="text-fg-muted">Tried again {formatDateTimeIST(r.next_try_at)}</div>}
              </td>
              <td className={`${TD} font-tabular text-fg`}>{formatNumber(r.ai_requests)}</td>
              <td className={`${TD} whitespace-nowrap font-tabular text-fg`}>{formatNumber(r.tokens)}
                <div className="text-fg-muted">{formatNumber(r.input_tokens)} in · {formatNumber(r.output_tokens)} out</div></td>
              <td className={`${TD} whitespace-nowrap font-tabular text-fg`}>{formatINR(r.cost_inr, { paise: true })}</td>
              <td className={`${TD} text-fg-muted`}>{r.trigger === "run_now" ? "Run now" : "Schedule"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function DailyReportAdminCard() {
  const [data, setData] = useState<ReportAdmin | null>(null);
  const [symbols, setSymbols] = useState("");
  const [bars, setBars] = useState(20);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function take(d: ReportAdmin) {
    setData(d); setSymbols(d.settings.symbols.join("\n")); setBars(d.settings.recent_bars);
  }
  useEffect(() => {
    let live = true;
    reportApi.admin().then((d) => { if (live) take(d); }, (e: unknown) => { if (live) setNote(errorSentence(e)); });
    return () => { live = false; };
  }, []);

  async function save() {
    setBusy(true); setNote(null);
    try { take(await reportApi.saveSettings(parseSymbols(symbols), bars)); setNote("Saved."); }
    catch (e) { setNote(errorSentence(e)); }
    finally { setBusy(false); }
  }
  async function runNow() {
    setBusy(true); setNote(null);
    try {
      await reportApi.runNow();
      take(await reportApi.admin());
      setNote("Run started. The result shows below.");
    } catch (e) { setNote(errorSentence(e)); }
    finally { setBusy(false); }
  }

  return (
    <Panel title="Daily market report" id="daily-report"
      note="One shared end-of-day report a trading day: one AI request on your own AI key, stored and read by every user. Information only.">
      {!data ? (note ? <p className="text-t12 text-down">{note}</p> : <SkeletonRows rows={2} label="Loading" />) : (
        <div className="space-y-space-3" data-report-admin>
          <div className="flex flex-wrap items-center gap-space-2">
            <Pill tone={data.flag_on ? "ok" : "off"}>{data.flag_on ? "On" : "Off"}</Pill>
            {!data.flag_on && <span className="text-t12 text-fg-muted">Turn on "shared_daily_report" in Feature flags; nothing runs while it is off.</span>}
            {data.key.message && <span className="text-t12 text-warn">{data.key.message}</span>}
          </div>
          <label className="block text-t13 text-fg">Shared report symbols
            <textarea className="mt-space-1 block min-h-24 w-full rounded-control border border-border bg-surface-1 p-space-2 text-t13 text-fg"
              value={symbols} onChange={(e) => setSymbols(e.target.value)} placeholder={data.examples.join("\n")} aria-describedby="report-symbols-help" />
          </label>
          <p id="report-symbols-help" className="text-t12 text-fg-muted">One per line: an index ("NIFTY 50"), "NIFTY 50 constituents" (read from NSE's official list on each run) or a stock symbol. Empty = no report.</p>
          <label className="flex flex-wrap items-center gap-space-2 text-t13 text-fg">Recent bars
            <input type="number" min={5} max={250} value={bars} onChange={(e) => setBars(Number(e.target.value))}
              className="min-h-9 w-24 rounded-control border border-border bg-surface-1 px-space-2 text-t13 text-fg" />
            <span className="text-t12 text-fg-muted">Daily bars for the volume average and the recent high / low</span>
          </label>
          <div className="flex flex-wrap gap-space-2">
            <Button onClick={() => void save()} disabled={busy} icon={<Save size={14} />}>Save</Button>
            <Button variant="primary" onClick={() => void runNow()} disabled={busy || !data.key.connected || !data.settings.symbols.length} icon={<Play size={14} />}>Run now</Button>
          </div>
          {note && <p className="text-t12 text-fg-muted" role="status">{note}</p>}
          <RunsTable runs={data.runs} />
        </div>
      )}
    </Panel>
  );
}
