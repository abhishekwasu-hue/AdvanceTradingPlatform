import { createContext, useCallback, useContext, useMemo } from "react";
import { useNavigate } from "react-router";
import type { InPlaceApi } from "./api";

/**
 * M75 NAV-1 / NAV-6: the in-place host's context and its hooks, in a module of their own so a converted link anywhere
 * (also in the classic shell's first download) needs neither the registry nor the panels: the host (InPlaceProvider)
 * provides the API; outside the host (the classic shell) the hooks navigate exactly as today.
 */
export const InPlaceContext = createContext<InPlaceApi | null>(null);

/** A button that opened a new browser tab before still does when no panel shows its address. */
export function newBrowserTab(path: string) {
  window.open(path, "_blank", "noopener");
}

/** The in-place API. Outside the host (the classic shell) it navigates exactly as today, so a converted button works in
 * both shells. */
export function useInPlace(): InPlaceApi {
  const ctx = useContext(InPlaceContext);
  const navigate = useNavigate();
  const fallback = useMemo<InPlaceApi>(() => ({
    enabled: false,
    stack: [],
    handles: () => false,
    ownView: () => false,
    open: (target, opts) => {
      if (typeof target !== "string") return;      // a panel by kind exists only inside the host
      if (opts?.newTab) newBrowserTab(target);
      else navigate(target, opts?.state !== undefined ? { state: opts.state } : undefined);
    },
    close: () => undefined,
    replace: () => undefined,
  }), [navigate]);
  return ctx ?? fallback;
}

/**
 * NAV-4: `open(path)` for a part that may be drawn outside any router (the basket panel in its own tests): the host's
 * open when the v2 shell's host is there (a panel in place, or the router's navigation as today), else the page load
 * the button did before.
 */
export function useInPlaceOpen(): (path: string) => void {
  const ctx = useContext(InPlaceContext);
  return useCallback((path: string) => {
    if (ctx) ctx.open(path);
    else window.location.assign(path);
  }, [ctx]);
}
