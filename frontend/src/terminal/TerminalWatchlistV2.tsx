import { useCallback, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { readView, saveView, type ChartsView } from "../chart/chartsPage";
import { WatchlistPanel } from "../chart/WatchlistPanel";
import type { WatchItem } from "../chart/watchlistsV2";
import { rootOf } from "../copilot/workspace/positionCards";
import { useInPlace } from "../inplace/context";
import { chartTabPath } from "../shell/shellRoutes";
import { recentKeyFor } from "../ui/SymbolPicker";
import { isOptionable } from "../ui/symbolUniverse";

/**
 * UX-3 (owner 25 Oct, UX2 plan PR 8): the watchlist as a Terminal panel - the very same panel as Charts and the Watchlist
 * page (src/chart/WatchlistPanel.tsx: the user's lists + the system lists, live price and change with arrows, add with
 * the symbol search, remove, reorder), nothing about it copied. The list shown is the one Charts and the Watchlist page
 * show (the Charts view's `watchlist_id` / `watchlist_system`).
 *
 * A row is linked to the Terminal: a symbol with options (an index or an F&O stock) becomes the ONE active symbol, so the
 * chart, the chain, the basket and the order panel follow it (the context bar's own change); any other symbol (a share
 * without options, a contract) opens on Charts in place, as on the Watchlist page.
 */
export type WatchlistTarget = { kind: "underlying"; underlying: string } | { kind: "chart"; path: string };

/** Where a row of the watchlist takes the Terminal. Pure. */
export function watchlistTarget(item: WatchItem): WatchlistTarget {
  const root = rootOf(item.symbol);
  return isOptionable(root) ? { kind: "underlying", underlying: root } : { kind: "chart", path: chartTabPath(item.symbol, item.exchange) };
}

/** The row to mark: the one he opened while it is still the active symbol, else the active symbol's spot. Pure. */
export function markedItem(picked: WatchItem | null, underlying: string, spotSymbol: string): WatchItem {
  if (picked && rootOf(picked.symbol) === underlying) return picked;
  return { symbol: spotSymbol, exchange: underlying === "SENSEX" ? "BSE" : "NSE" };
}

export function TerminalWatchlistV2({ underlying, spotSymbol, onUnderlying, touch = false, liveTicks = false }: {
  underlying: string;
  spotSymbol: string;
  onUnderlying: (underlying: string) => void;
  /** The phone tab: 44 px targets everywhere. */
  touch?: boolean;
  liveTicks?: boolean;
}) {
  const inPlace = useInPlace();
  const { user } = useAuth();
  const userId = user?.id ?? null;
  const [view, setViewState] = useState<ChartsView>(() => readView(userId));
  const [viewUser, setViewUser] = useState(userId);
  if (viewUser !== userId) { setViewUser(userId); setViewState(readView(userId)); }
  // Only the list choice changes here; the rest of the Charts view is read fresh, so nothing Charts saved is lost.
  const keep = useCallback((patch: Pick<Partial<ChartsView>, "watchlist_id" | "watchlist_system">) => setViewState(() => {
    const next = { ...readView(userId), ...patch };
    saveView(userId, next);
    return next;
  }), [userId]);
  const [picked, setPicked] = useState<WatchItem | null>(null);
  const open = (item: WatchItem) => {
    const to = watchlistTarget(item);
    if (to.kind === "underlying") { setPicked(item); onUnderlying(to.underlying); } else inPlace.open(to.path);
  };
  const mark = markedItem(picked, underlying, spotSymbol);
  return (
    <div data-panel-body="watchlist" className="flex h-full min-h-0 flex-col">
      <WatchlistPanel symbol={mark.symbol} exchange={mark.exchange} onOpen={open} listId={view.watchlist_id} onListId={(id) => keep({ watchlist_id: id })}
        systemKey={view.watchlist_system} onSystemKey={(key) => keep({ watchlist_system: key })}
        storageKey={recentKeyFor(userId)} touch={touch} narrow={!touch} className="flex-1" liveTicks={liveTicks} />
    </div>
  );
}
