import { RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { errorSentence } from "../api/errors";
import { cx } from "../components/primitives/cx";
import type { BrokerTokenInfo, ReconciliationReport, ReconciliationStatus } from "../types";
import { formatDateTimeIST } from "../ui/format";

/**
 * Owner 20 Nov ("live trading cha issue tumhi solve kela nahi ka"): after a LIVE order FAILED, new LIVE entries wait
 * until a reconciliation with the broker comes back clean (Phase G1, safety rule 8). The worker re-checks only in a
 * cycle with a LIVE bot, and the one "Reconcile" button sat on the old Deployments page, so a manual ticket's failed
 * order kept LIVE closed with no way out in the v2 screens. This card shows the pause in plain words with ONE action:
 * check with the broker now (the same route; it lifts the pause only when the books agree). Exits are never blocked.
 */
export function plainPauseText(status: Pick<ReconciliationStatus, "broker_uncertain_since">): string {
  const since = status.broker_uncertain_since ? ` since ${formatDateTimeIST(status.broker_uncertain_since)}` : "";
  return `New LIVE orders are paused${since}: an earlier LIVE order failed, so ATP checks your broker's positions first.`;
}

export function reportText(report: ReconciliationReport): string {
  return report.mismatched_count === 0
    ? "Your broker's positions match ATP's. LIVE orders are open again."
    : `${report.mismatched_count} position(s) differ from your broker: close or record them, then check again.`;
}

export function BrokerCheckV2({ className, onCleared }: { className?: string; onCleared?: () => void }) {
  const [status, setStatus] = useState<ReconciliationStatus | null>(null);
  const [brokers, setBrokers] = useState<BrokerTokenInfo[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    api.reconciliationStatus().then((s) => { if (live) setStatus(s); }, () => { if (live) setStatus(null); });
    api.brokerTokenStatus().then((b) => { if (live) setBrokers(b); }, () => { if (live) setBrokers([]); });
    return () => { live = false; };
  }, []);

  async function check(brokerName: string) {
    setBusy(true);
    setNote(null);
    try {
      const report = await api.runReconciliation(brokerName);
      setNote(reportText(report));
      const next = await api.reconciliationStatus();
      setStatus(next);
      if (!next.broker_uncertain) onCleared?.();
    } catch (e) {
      setNote(errorSentence(e));
    } finally {
      setBusy(false);
    }
  }

  if (!status?.broker_uncertain) {
    return note ? <p role="status" className={cx("text-t13 text-up", className)} data-broker-check="cleared">{note}</p> : null;
  }
  return (
    <section role="alert" data-broker-check="paused"
      className={cx("space-y-space-2 rounded-panel border border-warn bg-warn-tint px-space-3 py-space-2 text-t13", className)}>
      <p className="font-semibold text-warn">{plainPauseText(status)}</p>
      <p className="text-fg-muted">Exits keep running.</p>
      <div className="flex flex-wrap gap-space-2">
        {brokers.map((b) => (
          <button key={b.broker_name} type="button" onClick={() => void check(b.broker_name)} disabled={busy || b.needs_login}
            data-broker-check-run={b.broker_name} title={b.needs_login ? "Log in to the broker first" : undefined}
            className="inline-flex min-h-11 items-center gap-space-2 rounded-control bg-brand px-space-3 text-t14 font-semibold text-on-brand disabled:opacity-50 md:min-h-9">
            <RefreshCw size={14} aria-hidden className={busy ? "animate-spin" : ""} />
            Check with {b.broker_name.charAt(0).toUpperCase() + b.broker_name.slice(1)} now
          </button>
        ))}
        {brokers.length === 0 && <span className="text-fg-muted">Connect your broker to check.</span>}
      </div>
      {note && <p className="text-fg" data-broker-check-note>{note}</p>}
      <details className="text-t12 text-fg-muted">
        <summary className="cursor-pointer">Details</summary>
        <p className="mt-space-1 break-words">{status.broker_uncertain_reason}</p>
      </details>
    </section>
  );
}
