import { Eye } from "lucide-react";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useNavigate } from "react-router";
import { tryRefresh } from "../api/client";
import { endView } from "../api/viewAs";
import { currentView, subscribeView, timeLeft, type ViewInfo } from "../api/viewState";
import { useAuth } from "../auth/AuthContext";

const FOCUS = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";

export function useView(): ViewInfo | null {
  return useSyncExternalStore(subscribeView, currentView, currentView);
}

/**
 * MO3-A1: the fixed banner of the admin's read-only "View as user" - "Viewing as <name> - read only" with the time
 * left and "End" - over every page of either shell while the view is open. It also switches the app between the two
 * people: when a view opens the app re-reads who is signed in (the person) and opens their front page; when it ends
 * (End, expiry, a reload's refresh) the admin's own sign-in comes back from the refresh cookie and the Support section
 * opens on the same person.
 */
export default function ViewAsBanner() {
  const view = useView();
  const { refreshUser, logout } = useAuth();
  const navigate = useNavigate();
  const previous = useRef<ViewInfo | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [ending, setEnding] = useState(false);

  useEffect(() => {
    const before = previous.current;
    previous.current = view;
    if (!before && view) {
      void refreshUser().then(() => navigate("/"));
    } else if (before && !view) {
      setEnding(false);
      void (async () => {
        if (await tryRefresh()) {
          await refreshUser();
          navigate(`/admin?section=support&user=${before.userId}`);
        } else {
          logout();
        }
      })();
    }
  }, [view]);  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!view) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 15_000);
    return () => window.clearInterval(id);
  }, [view]);

  if (!view) return null;
  return (
    <div role="status" aria-live="polite" data-view-as-banner={view.userId}
      className="sticky top-0 z-[60] flex items-center justify-center gap-space-3 border-b border-warn/40 bg-warn-tint px-space-4 py-space-2 pt-[max(var(--space-2),env(safe-area-inset-top))] text-t14 text-fg">
      <Eye size={16} aria-hidden className="shrink-0 text-warn" />
      <p className="min-w-0 flex-1 md:flex-none">
        <span className="block truncate md:inline">Viewing as <strong className="font-semibold">{view.name}</strong><span className="hidden md:inline"> - read only</span></span>
        <span className="block text-t12 text-fg-muted md:ml-space-3 md:inline"><span className="font-medium text-fg md:hidden">Read only · </span>{timeLeft(view.expiresAt, now)}</span>
      </p>
      <button type="button" disabled={ending} onClick={() => { setEnding(true); void endView(); }}
        className={`inline-flex h-11 shrink-0 items-center rounded-control border border-border bg-surface-1 px-space-3 text-t14 font-medium text-fg hover:bg-surface-2 disabled:opacity-60 md:h-8 ${FOCUS}`}>
        {ending ? "Ending…" : "End"}
      </button>
    </div>
  );
}
