/**
 * LIVE-2 (owner order LIVE-1, binding: `live_trading` is the one switch): the PAPER / LIVE choice of the basket and of
 * Reverse - the ticket's own control (M96-Q1: the "Mode" segmented choice and its one sentence while LIVE is off), so
 * every order path looks and behaves the same. PAPER unless he picks LIVE; nothing here decides whether LIVE can go -
 * the server reads the switch and his own broker session again when he places.
 */
import { useEffect, useState } from "react";
import { cx } from "../components/primitives/cx";
import type { LiveStatus, OrderMode } from "./api";
import { Segmented } from "./OrderTicketV2";
import { LIVE_OFF_TEXT, ORDER_MODES } from "./ticketModel";

/** Whether his LIVE choice can go now (the server's `live-status`), read while `enabled`; null until known or on a failure. */
export function useLiveStatus(enabled: boolean, read?: () => Promise<LiveStatus>): LiveStatus | null {
  const [live, setLive] = useState<LiveStatus | null>(null);
  useEffect(() => {
    if (!enabled || !read) return undefined;
    let on = true;
    read().then((s) => { if (on) setLive(s); }).catch(() => { if (on) setLive(null); });
    return () => { on = false; };
  }, [enabled, read]);
  return live;
}

/** LIVE is picked and the server said it cannot go now (unknown is not "off": the server decides at Place). Pure. */
export function liveIsOff(mode: OrderMode, live: LiveStatus | null): boolean {
  return mode === "LIVE" && live !== null && !live.open;
}

/** The mode choice with the ticket's markup; while LIVE is off, the one plain sentence under it. */
export function OrderModeChoice({ mode, onMode, live, testId, className }: {
  mode: OrderMode; onMode: (m: OrderMode) => void; live: LiveStatus | null; testId: string; className?: string;
}) {
  return (
    <div className={className} data-mode-choice={testId}>
      <Segmented label="Mode" testId={testId} value={mode} onChange={onMode} options={ORDER_MODES} />
      {liveIsOff(mode, live) && <p className="mt-space-1 text-t13 text-warn" data-live-off={testId}>{live?.text ?? LIVE_OFF_TEXT}</p>}
    </div>
  );
}

/** The confirm step names the mode in large text, before anything else (as the ticket's Review does). */
export function ConfirmModeLine({ mode, what, testId }: { mode: OrderMode; what: string; testId: string }) {
  return (
    <p className={cx("text-t18 font-semibold", mode === "LIVE" ? "text-warn" : "text-fg")} data-confirm-mode={testId}>
      {mode === "LIVE" ? `LIVE ${what} · real money` : `PAPER ${what} · practice`}
    </p>
  );
}
