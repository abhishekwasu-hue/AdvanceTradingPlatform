import { KeyRound } from "lucide-react";
import { Suspense, lazy, useState } from "react";
import type { BrokerConnection } from "../types";
import { useAuth } from "../auth/AuthContext";
import { ASK_OWNER, bannerFor, canChangeBrokers } from "./brokerState";
import { BTN_PRIMARY } from "./parts";
import { useBrokerConnections } from "./useBrokerConnections";

const ConnectPanel = lazy(() => import("./ConnectPanel"));

/**
 * P2b: the Dashboard's one daily-renewal banner - "<Broker> needs today's login" and "Renew", which opens the same
 * Connect panel (one tap, or one paste). Only for a broker with a daily login; never for a data-only connection (an
 * Upstox analytics token is simply connected).
 */
export function RenewBannerView({ connection, onRenew, canChange = true }: { connection: BrokerConnection; onRenew?: () => void; canChange?: boolean }) {
  return (
    <div role="status" data-renew-banner={connection.broker}
      className="flex flex-wrap items-center gap-space-3 rounded-panel border border-warn/40 bg-warn-tint px-space-4 py-space-3">
      <KeyRound size={18} aria-hidden className="shrink-0 text-warn" />
      <span className="min-w-0 flex-1 text-t14 font-semibold text-fg">{connection.name} needs today&apos;s login</span>
      {canChange
        ? <button type="button" onClick={onRenew} className={BTN_PRIMARY}>Renew</button>
        : <span className="basis-full text-t13 text-fg">{ASK_OWNER}</span>}
    </div>
  );
}

export default function RenewBanner() {
  const { rows, refresh } = useBrokerConnections();
  const canChange = canChangeBrokers(useAuth().user);
  const due = bannerFor(rows);
  // Kept while the panel is open, so "Connected ✓" still shows after the banner itself goes away.
  const [renewing, setRenewing] = useState<BrokerConnection | null>(null);
  const [open, setOpen] = useState(false);
  return (
    <>
      {due && <RenewBannerView connection={due} canChange={canChange} onRenew={() => { setRenewing(due); setOpen(true); }} />}
      {renewing && (
        <Suspense fallback={null}>
          <ConnectPanel broker={renewing.broker} mode="renew" accountLabel={renewing.account_label}
            open={open} onOpenChange={setOpen} onChanged={refresh} />
        </Suspense>
      )}
    </>
  );
}
