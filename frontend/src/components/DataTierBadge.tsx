import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { DataTierBadgeInfo, DataTierResponse } from "../types";
import { tierText, tierTitle, tierTone } from "./dataTier";

/** BT1 (flag `byob_data_tier`): every number carries its data tier and timestamp. With the flag off the backend sends
 * no `data_tier` and `/market-data/data-tier` answers `enabled: false`, so these render nothing - pages look as before. */

const TONES = {
  live: "border-up/40 bg-up/10 text-up",
  eod: "border-border bg-surface-2 text-fg-muted",
  stale: "border-warn/50 bg-warn/10 text-warn",
} as const;

export function DataTierBadge({ badge, className = "" }: { badge: DataTierBadgeInfo | null | undefined; className?: string }) {
  if (!badge) return null;
  const tone = tierTone(badge);
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap rounded-md border px-1.5 py-0.5 text-[11px] font-semibold ${TONES[tone]} ${className}`}
          title={tierTitle(badge)} data-tier={badge.tier} aria-label={`Data tier: ${tierText(badge)}`}>
      <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${tone === "live" ? "bg-up" : tone === "stale" ? "bg-warn" : "bg-fg-muted"}`} />
      {tierText(badge)}
    </span>
  );
}

/** The tier this user sees for one symbol. `live` (a fresher badge from the last price response) wins when present;
 * otherwise the badge API is asked once per symbol (a lookup that takes no slot in the user's session budget) - and
 * only when the flag is on for the user's organisation (one shared check), so flag-off pages make no extra calls. */
export function SymbolTierBadge({ symbol, exchange = "NSE", live, className }: {
  symbol: string | undefined; exchange?: string; live?: DataTierBadgeInfo | null; className?: string;
}) {
  const { user } = useAuth();
  const [fetched, setFetched] = useState<DataTierResponse | null>(null);
  const needLookup = !!user && !!symbol && !live;
  useEffect(() => {
    setFetched(null);
    if (!user || !symbol || !needLookup) return;
    let cancelled = false;
    marketWideFor(user.id)
      .then((wide) => (wide.enabled ? api.marketDataTier(symbol, exchange) : wide))
      .then((r) => { if (!cancelled) setFetched(r); })
      .catch(() => { if (!cancelled) setFetched(null); });
    return () => { cancelled = true; };
  }, [user, symbol, exchange, needLookup]);
  if (live) return <DataTierBadge badge={live} className={className} />;
  return fetched?.enabled ? <DataTierBadge badge={fetched.badge} className={className} /> : null;
}

// One lookup per signed-in user (the flag is per organisation), shared by every badge on the page.
let marketWide: { userId: number; answer: Promise<DataTierResponse> } | null = null;

function marketWideFor(userId: number): Promise<DataTierResponse> {
  if (!marketWide || marketWide.userId !== userId) {
    const answer = api.marketDataTier();
    marketWide = { userId, answer };
    answer.catch(() => { if (marketWide?.answer === answer) marketWide = null; });
  }
  return marketWide.answer;
}

/** "EOD · <date>" on market-wide pages (Phase 0 has no delayed-data tier); nothing while the flag is off. */
export function MarketWideTierBadge({ className }: { className?: string }) {
  const { user } = useAuth();
  const [info, setInfo] = useState<DataTierResponse | null>(null);
  useEffect(() => {
    if (!user) { setInfo(null); return; }
    let cancelled = false;
    marketWideFor(user.id).then((r) => { if (!cancelled) setInfo(r); }).catch(() => { if (!cancelled) setInfo(null); });
    return () => { cancelled = true; };
  }, [user]);
  return info?.enabled ? <DataTierBadge badge={info.market_wide} className={className} /> : null;
}
