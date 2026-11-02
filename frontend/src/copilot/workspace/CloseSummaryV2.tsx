import { useEffect, useState } from "react";
import { cx } from "../../components/primitives/cx";
import { formatINR, formatNumber } from "../../ui/format";
import { closeSummaryApi, summaryRows, summaryTitle, type CloseSummary, type CloseSummaryApi } from "./closeSummary";

/**
 * M76 MP-13 (owner order M76 D-3; flag `copilot_workspace`): his day in the workspace, numbers only - trades, the
 * check-card lines he accepted, his own checklist and fill vs plan, and what stays open. "So far today" until the close,
 * "Close summary" after. Read-only: nothing placed, held back or changed. Rows: `closeSummary.ts` (closeSummary.test.tsx).
 */
const TONE = { up: "text-up", down: "text-down", flat: "text-fg" } as const;
const money = (v: number) => formatINR(v);
const num = (v: number) => formatNumber(v, 2, { trim: true });

export function CloseSummaryV2({ api = closeSummaryApi }: { api?: CloseSummaryApi }) {
  const [s, setS] = useState<CloseSummary | null>(null);
  const [failed, setFailed] = useState(false);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    setFailed(false);
    api.read().then((v) => { if (live) setS(v); }, () => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [api, tick]);

  const modes = s ? (["PAPER", "LIVE"] as const).filter((m) => s.modes[m]) : [];
  return (
    <section aria-labelledby="workspace-close-summary" className="rounded-panel border border-subtle bg-surface-1 p-space-4" data-close-summary={s ? modes.length : failed ? "failed" : "loading"}>
      <div className="mb-space-2 flex items-center justify-between gap-space-2">
        <h3 id="workspace-close-summary" className="text-t14 font-semibold text-fg">{summaryTitle(s)}</h3>
        <button type="button" onClick={() => setTick((n) => n + 1)} data-close-summary-refresh
          className="min-h-11 text-t13 font-semibold text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9">Refresh</button>
      </div>
      {failed && <p className="text-t13 text-fg-muted">The summary could not be read just now.</p>}
      {s && modes.length === 0 && <p className="text-t13 text-fg-muted" data-close-summary-empty>No trades today and nothing open.</p>}
      {s && modes.map((m) => (
        <div key={m} className="mt-space-2 first:mt-0" data-close-mode={m}>
          {modes.length > 1 && <p className="mb-space-1 text-t12 font-semibold text-fg-muted">{m === "PAPER" ? "Paper" : "Live"}</p>}
          <table className="w-full table-fixed text-t13 tabular-nums">
            <tbody>
              {summaryRows(s.modes[m]!, money, num).map((r) => (
                <tr key={r.key} data-row={r.key} className="border-b border-subtle align-top last:border-b-0">
                  <th scope="row" className="w-2/5 py-space-1 pr-space-2 text-left font-normal text-fg-muted">{r.label}</th>
                  <td className={cx("break-words py-space-1 font-medium", TONE[r.tone ?? "flat"])}>{r.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </section>
  );
}

export default CloseSummaryV2;
