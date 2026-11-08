import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
export { CONNECT_LIVE, lastCloseText } from "./lastClose";

/**
 * M86 D2 / D3: what a user WITHOUT a connected broker sees - the last end-of-day close with its date, and one in-place
 * "Connect your broker for live prices" button per card (src/ui/ConnectLive.tsx).
 */

// One broker-status read shared by every card of the page (a chart, the chain, the watchlist), re-read after 30 s.
const TTL_MS = 30_000;
let cache: { userId: number; at: number; read: Promise<boolean> } | null = null;

/** True when this user has no broker stored at all (the server's own answer); false while unknown or on a failure. */
export function noBrokerRead(userId: number): Promise<boolean> {
  if (cache && cache.userId === userId && Date.now() - cache.at < TTL_MS) return cache.read;
  const read = api.brokerTokenStatus().then((rows) => Array.isArray(rows) && rows.length === 0).catch(() => false);
  cache = { userId, at: Date.now(), read };
  return read;
}

/** Tests, and after a broker was connected: the next read asks the server again. */
export function forgetNoBroker(): void { cache = null; }

/** The signed-in user's id; null outside the sign-in provider (a card rendered on its own, e.g. in a unit test). */
function useUserId(): number | null {
  try {
    return useAuth().user?.id ?? null;
  } catch {
    return null;                       // intentional: no provider - nobody is signed in here, so nothing is asked
  }
}

/** The signed-in user has no broker stored (false until known). */
export function useNoBroker(): boolean {
  const userId = useUserId();
  const [known, setKnown] = useState<{ userId: number | null; none: boolean }>({ userId: null, none: false });
  useEffect(() => {
    if (userId === null) return undefined;
    let live = true;
    void noBrokerRead(userId).then((none) => { if (live) setKnown({ userId, none }); });
    return () => { live = false; };
  }, [userId]);
  return known.userId !== null && known.userId === userId && known.none;
}
