import { AlertOctagon, RefreshCw, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { BrokerTokenInfo, ReconciliationReport, ReconciliationStatus } from "../types";

/**
 * Phase G1 (safety rule 8): after a LIVE order FAILED - the broker call raised or timed out, so
 * the platform cannot know whether the broker holds the position - new LIVE entries are refused
 * until a position reconciliation against the broker comes back with zero mismatches. The worker
 * re-runs that reconciliation every cycle while flagged; this banner shows the state and lets the
 * operator run it by hand after they have fixed the difference.
 */
export default function BrokerUncertainBanner() {
  const [status, setStatus] = useState<ReconciliationStatus | null>(null);
  const [brokers, setBrokers] = useState<BrokerTokenInfo[]>([]);
  const [report, setReport] = useState<ReconciliationReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function refresh() {
    try {
      setStatus(await api.reconciliationStatus());
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => {
    void refresh();
    api.brokerTokenStatus().then(setBrokers).catch(() => setBrokers([]));
  }, []);

  async function reconcile(brokerName: string) {
    setBusy(true);
    setError(null);
    try {
      setReport(await api.runReconciliation(brokerName));
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!status || !status.broker_uncertain) return null;

  return (
    <div className="rounded-lg border border-danger/50 bg-danger/[0.08] px-3 py-2.5 text-xs space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-start gap-2">
          <AlertOctagon size={14} className="text-danger shrink-0 mt-0.5" />
          <div>
            <div className="font-bold text-danger">LIVE entries blocked - broker state uncertain</div>
            <div className="text-slate-300">
              {status.broker_uncertain_reason}
              {status.broker_uncertain_since && ` (since ${new Date(status.broker_uncertain_since).toLocaleString()})`}.
              Exits keep running. The worker reconciles against the broker every cycle and lifts the block
              the moment the books agree; if a position at the broker is not on the platform (or vice versa),
              close or record it, then run reconciliation.
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {brokers.map((b) => (
            <button
              key={b.broker_name}
              onClick={() => reconcile(b.broker_name)}
              disabled={busy || b.needs_login}
              title={b.needs_login ? "Log in to the broker first" : "Fetch positions from the broker and compare"}
              className="flex items-center gap-1.5 rounded bg-brand hover:bg-brand-dim text-white font-semibold px-3 py-1 disabled:opacity-50 capitalize"
            >
              <RefreshCw size={12} className={busy ? "animate-spin" : ""} /> Reconcile {b.broker_name}
            </button>
          ))}
        </div>
      </div>
      {error && <div className="text-danger">{error}</div>}
      {report && (
        <div className="rounded border border-border bg-panel2 px-2 py-1.5">
          <div className="flex items-center gap-1.5 font-semibold text-slate-200">
            {report.mismatched_count === 0 ? <ShieldCheck size={12} className="text-accent" /> : <AlertOctagon size={12} className="text-danger" />}
            {report.broker_name}: {report.mismatched_count} mismatch(es) across {report.items.length} symbol(s)
          </div>
          {report.items.filter((i) => i.status !== "MATCHED").map((i) => (
            <div key={i.symbol} className="text-muted">
              <span className="font-mono text-slate-300">{i.symbol}</span> {i.status}: {i.detail}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
