import { AlertTriangle } from "lucide-react";
import { useEffect, useState } from "react";
import { NOTICE_REFRESH_MS, loadStatusSessions, type StatusNotice } from "./sessions";

/**
 * AUD-T8 (flag `status_page`, in the v2 shell): the one notice above every page while the platform is degraded during
 * an open market - the trading engine has not checked in, or market data is running late. The server words it
 * (`GET /api/v1/status/sessions` -> `notice`); nothing is shown while all is well, while every market is closed, or when
 * the read fails. The same look as the live-prices notice (src/live/LivePricesBanner.tsx). A "See status" link when the
 * public status page is on.
 */
export function DegradedNoticeView({ notice, statusLink }: { notice: StatusNotice | null; statusLink: boolean }) {
  if (!notice) return null;
  const outage = notice.state === "outage";
  return (
    <div className="px-space-4 pt-space-3 md:px-space-8">
      <div role="status" aria-live="polite" data-degraded-notice={notice.state}
        className={`flex flex-wrap items-center gap-space-3 rounded-panel border px-space-4 py-space-3 ${outage ? "border-down/40 bg-down/10" : "border-warn/40 bg-warn-tint"}`}>
        <AlertTriangle size={18} aria-hidden className={`shrink-0 ${outage ? "text-down" : "text-warn"}`} />
        <span className="min-w-0 flex-1 text-t14 text-fg">{notice.text}</span>
        {statusLink && (
          <a href="/status" className="text-t13 font-medium text-brand underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
            See status
          </a>
        )}
      </div>
    </div>
  );
}

export default function DegradedNotice({ enabled }: { enabled: boolean }) {
  const [state, setState] = useState<{ notice: StatusNotice | null; statusLink: boolean }>({ notice: null, statusLink: false });
  useEffect(() => {
    if (!enabled) { setState({ notice: null, statusLink: false }); return; }
    let alive = true;
    const run = () => loadStatusSessions().then((load) => {
      if (!alive) return;
      setState(load.kind === "ok" ? { notice: load.body.notice, statusLink: load.body.public_page } : { notice: null, statusLink: false });
    });
    void run();
    const id = window.setInterval(() => { void run(); }, NOTICE_REFRESH_MS);
    return () => { alive = false; window.clearInterval(id); };
  }, [enabled]);
  if (!enabled) return null;
  return <DegradedNoticeView notice={state.notice} statusLink={state.statusLink} />;
}
