import { KeyRound, RefreshCw } from "lucide-react";
import { Suspense, lazy, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { ASK_OWNER, canChangeBrokers } from "../broker/brokerState";
import { BTN_PRIMARY } from "../broker/parts";
import { useGateShowing } from "./gateShowing";
import { liveTicks, useHubNotice, type HubNotice } from "./liveTicks";

const ConnectPanel = lazy(() => import("../broker/ConnectPanel"));

/**
 * TM Part 1 (flag `data_hub`, with `ui_v2`): the app's one notice about live prices, above every page.
 * - "Live prices are reconnecting ..." while the broker connection reconnects (it resubscribes every symbol by itself).
 * - "Your <Broker> login has ended. Log in again to resume live prices." with ONE "Log in again" button, which opens the
 *   same renew panel as the Broker page; after the renewal everything resumes on its own - charts, the option chain,
 *   watchlists and alerts - and the notice goes away.
 * Nothing while prices are live. Plain words only; no ids or technical terms.
 */
export function LivePricesNotice({ notice, onRenew, canChange = true }: { notice: HubNotice; onRenew?: () => void; canChange?: boolean }) {
  if (notice.kind === "reconnecting") {
    return (
      <div role="status" aria-live="polite" data-live-notice="reconnecting"
        className="flex flex-wrap items-center gap-space-3 rounded-panel border border-subtle bg-surface-1 px-space-4 py-space-3">
        <RefreshCw size={18} aria-hidden className="shrink-0 animate-spin text-fg-muted motion-reduce:animate-none" />
        <span className="min-w-0 flex-1 text-t14 text-fg">{notice.message}</span>
      </div>
    );
  }
  return (
    <div role="status" aria-live="polite" data-live-notice="renew"
      className="flex flex-wrap items-center gap-space-3 rounded-panel border border-warn/40 bg-warn-tint px-space-4 py-space-3">
      <KeyRound size={18} aria-hidden className="shrink-0 text-warn" />
      <span className="min-w-0 flex-1 text-t14 font-semibold text-fg">{notice.message}</span>
      {canChange
        ? <button type="button" onClick={onRenew} className={BTN_PRIMARY}>Log in again</button>
        : <span className="basis-full text-t14 text-fg">{ASK_OWNER.replace("connect a broker", `log in to ${notice.brokerName} again`)}</span>}
    </div>
  );
}

export default function LivePricesBanner({ enabled }: { enabled: boolean }) {
  const found = useHubNotice(enabled);
  // ONE renew prompt: when the page's own broker card already asks to log in, this notice does not ask again.
  const gate = useGateShowing();
  const notice = found?.kind === "renew" && gate ? null : found;
  const canChange = canChangeBrokers(useAuth().user);
  const [renewing, setRenewing] = useState<{ broker: string; accountLabel: string } | null>(null);
  const [open, setOpen] = useState(false);
  if (!enabled) return null;
  return (
    <>
      {notice && (
        <div className="px-space-4 pt-space-3 md:px-space-8">
          <LivePricesNotice notice={notice} canChange={canChange}
            onRenew={notice.kind === "renew" ? () => { setRenewing({ broker: notice.broker, accountLabel: notice.accountLabel }); setOpen(true); } : undefined} />
        </div>
      )}
      {renewing && (
        <Suspense fallback={null}>
          <ConnectPanel broker={renewing.broker} mode="renew" accountLabel={renewing.accountLabel}
            open={open} onOpenChange={setOpen} onChanged={() => liveTicks().reloadStatus()} />
        </Suspense>
      )}
    </>
  );
}
