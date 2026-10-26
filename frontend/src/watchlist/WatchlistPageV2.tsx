import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { useAuth } from "../auth/AuthContext";
import { readView, saveView, type ChartsView } from "../chart/chartsPage";
import { systemListsApi } from "../chart/systemLists";
import { WatchlistPanel } from "../chart/WatchlistPanel";
import type { WatchItem } from "../chart/watchlistsV2";
import { cx } from "../components/primitives/cx";
import { CARD } from "../dashboard/CardV2";
import type { PageProps } from "../routes";
import { chartTabPath } from "../shell/shellRoutes";
import { PageIntro } from "../ui/PageIntro";
import { recentKeyFor } from "../ui/SymbolPicker";
import { useFeatures } from "../ui/useFeatures";
import { PHONE_QUERY, useMediaQuery } from "../utils/useMediaQuery";
import { NOT_FILLED_NOTE, readyMadeState, type ReadyMade } from "./model";

/**
 * P1-8 (2) W-PR: the Watchlist page at /watchlist - v2 only, behind the `watchlists` flag (the shell leaves it out of the
 * navigation and shows "Coming soon" at the address while the flag is off). On a phone it is a tab of the bottom bar.
 *
 * It shows the very same watchlist panel as Charts (src/chart/WatchlistPanel.tsx: the list chooser, search, sort, copy
 * to my list, live prices with `data_hub`) - nothing about the panel changes here. The list shown is shared with Charts
 * (the Charts view's `watchlist_id` / `watchlist_system`), so both open on the same list. A row opens its symbol on
 * Charts. While the ready-made lists are not filled yet the page says so in one line, never sample rows.
 */
export default function WatchlistPageV2(_props: PageProps) {
  const navigate = useNavigate();
  const { user } = useAuth();
  const userId = user?.id ?? null;
  const features = useFeatures();
  const phone = useMediaQuery(PHONE_QUERY);

  const [view, setViewState] = useState<ChartsView>(() => readView(userId));
  const [viewUser, setViewUser] = useState(userId);
  if (viewUser !== userId) { setViewUser(userId); setViewState(readView(userId)); }
  // Only the list choice changes here; the rest of the Charts view is read fresh, so nothing Charts saved is lost.
  const keep = useCallback((patch: Pick<Partial<ChartsView>, "watchlist_id" | "watchlist_system">) => setViewState(() => {
    const next = { ...readView(userId), ...patch };
    saveView(userId, next);
    return next;
  }), [userId]);

  const readyMade = useReadyMade();
  const open = (item: WatchItem) => navigate(chartTabPath(item.symbol, item.exchange));

  return (
    <div data-page="watchlist" className="mx-auto flex w-full max-w-[760px] flex-col gap-space-4">
      <PageIntro id="watchlist" lines={["Tap a symbol to open its chart. Ready-made lists are read-only; copy one to make a list of your own."]} />
      <h1 className="sr-only md:not-sr-only md:text-t24 md:font-semibold md:text-fg">Watchlist</h1>
      {readyMade === "empty" && (
        <p role="status" data-ready-made="empty" className={cx(CARD, "p-space-4 text-t14 text-fg-muted")}>{NOT_FILLED_NOTE}</p>
      )}
      <div className={cx(CARD, "flex flex-col")}>
        <WatchlistPanel symbol="" exchange="" onOpen={open} listId={view.watchlist_id} onListId={(id) => keep({ watchlist_id: id })}
          systemKey={view.watchlist_system} onSystemKey={(key) => keep({ watchlist_system: key })}
          storageKey={recentKeyFor(userId)} touch={phone} className="flex-1" liveTicks={features.on("data_hub")} />
      </div>
    </div>
  );
}

/** Whether the ready-made lists are filled yet (one read; a failure says nothing). */
function useReadyMade(): ReadyMade {
  const [state, setState] = useState<ReadyMade>("unknown");
  useEffect(() => {
    let live = true;
    systemListsApi.groups().then((r) => { if (live) setState(readyMadeState(r)); }, () => { if (live) setState("unknown"); });
    return () => { live = false; };
  }, []);
  return state;
}
