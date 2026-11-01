import { Suspense, createContext, lazy, useContext, useLayoutEffect, useMemo, useRef, type ComponentType, type LazyExoticComponent, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router";
import { Dialog, Sheet } from "../components/primitives/Dialog";
import { SkeletonRows } from "../components/primitives/Feedback";
import { useFeatures } from "../ui/useFeatures";
import { PHONE_QUERY, useMediaQuery } from "../utils/useMediaQuery";
import { DEFAULT_REGISTRY } from "./panels";
import { createInPlaceApi, openPanels, type InPlaceApi, type NavigateFn } from "./api";
import { presentationFor, type PanelEntry, type PanelParams, type PanelProps, type PanelRegistry } from "./registry";

export type { InPlaceApi } from "./api";

/**
 * M75 rule 1 / NAV-1 (docs/design/M75_NO_NAVIGATION.md section 4): the one in-place host. A click opens a facility
 * IN PLACE - a side panel (desktop) or bottom sheet (phone), a drawer or a modal - on top of the tab the user is
 * working in, and closing it returns to exactly the same state:
 * - The PATH never changes. The open panels are written as `?panel=` on the same path (one per level, bottom first),
 *   pushed as a history entry, so react-router keeps the tab's route element mounted (its scroll, filters, chat and
 *   half-filled forms stay as they were), the phone Back button closes the top panel (M75N-2), a reload re-opens it and
 *   a copied address reproduces it.
 * - The stack is read from the address only (no second copy to drift): Back, Forward and a deep link all just work.
 * - At most three panels deep; a fourth replaces the top one. Escape and the close button pop one level; focus goes
 *   back to the button that opened the panel.
 * - Opening a path no panel is registered for (or while `ui_v2` is off, or the facility's flag is off) navigates as
 *   today, so every button keeps working while the areas convert one by one (NAV-2..8). Every full route still shows
 *   the full page when opened directly.
 */

const InPlaceContext = createContext<InPlaceApi | null>(null);

function warnUnregistered(path: string) {
  if (import.meta.env.DEV) console.warn(`[in-place] no panel is registered for ${path}: navigating as before (docs/design/M75_NO_NAVIGATION.md)`);
}

const lazyPanels = new WeakMap<PanelEntry, LazyExoticComponent<ComponentType<PanelProps>>>();
function panelComponent(entry: PanelEntry): LazyExoticComponent<ComponentType<PanelProps>> {
  let c = lazyPanels.get(entry);
  if (!c) { c = lazy(entry.load); lazyPanels.set(entry, c); }
  return c;
}

export function InPlaceProvider({ children, registry = DEFAULT_REGISTRY }: { children: ReactNode; registry?: PanelRegistry }) {
  const location = useLocation();
  const navigate = useNavigate();
  const features = useFeatures();
  const enabled = features.on("ui_v2");
  const on = features.on;
  const phone = useMediaQuery(PHONE_QUERY);
  const open = useMemo(() => (enabled ? openPanels(location.search, registry, on) : []), [enabled, location.search, registry, on]);
  // What had focus when each level opened (the clicked button): it gets focus back when that level closes.
  const openers = useRef<(HTMLElement | null)[]>([]);

  // The latest location for the callbacks, read at call time (a click handler made in an earlier render still writes
  // on top of the address as it is now).
  const here = useRef(location);
  here.current = location;

  const api = useMemo<InPlaceApi>(() => ({ ...createInPlaceApi({
    enabled, on, registry,
    location: () => here.current,
    navigate: navigate as NavigateFn,
    onUnregistered: warnUnregistered,
    onOpenLevel: (level) => {
      const active = typeof document === "undefined" ? null : document.activeElement;
      openers.current[level] = active instanceof HTMLElement && active !== document.body ? active : null;
    },
  }), stack: open.map((p) => p.spec) }), [enabled, on, registry, navigate, open]);

  return (
    <InPlaceContext.Provider value={api}>
      {children}
      {open.map((p, i) => (
        <PanelLayer key={`${i}:${p.spec.kind}`} entry={p.entry} params={p.spec.params ?? {}} phone={phone}
          top={i === open.length - 1} level={i} openers={openers}
          close={api.close} replace={(params) => api.replace({ kind: p.spec.kind, params })} />
      ))}
    </InPlaceContext.Provider>
  );
}

function PanelLayer({ entry, params, phone, top, level, openers, close, replace }: {
  entry: PanelEntry; params: PanelParams; phone: boolean; top: boolean; level: number;
  openers: { current: (HTMLElement | null)[] }; close: () => void; replace: (params: PanelParams) => void;
}) {
  // A panel opened by Forward or a deep link (not by open()) gets back what had focus when it appeared. A layout effect
  // runs before Radix moves focus into the panel (its focus scope does that in a passive effect).
  useLayoutEffect(() => {
    if (openers.current[level]?.isConnected) return;
    const active = document.activeElement;
    openers.current[level] = active instanceof HTMLElement && active !== document.body ? active : null;
  }, [level, openers]);
  const Panel = panelComponent(entry);
  const shape = presentationFor(entry.presentation, phone);
  const onOpenChange = (o: boolean) => { if (!o && top) close(); };
  const onCloseAutoFocus = (e: Event) => {
    e.preventDefault();
    const el = openers.current[level];
    openers.current[level] = null;
    if (el?.isConnected) el.focus({ preventScroll: true });
  };
  const body = (
    <div data-inplace-panel={entry.kind} data-inplace-shape={shape.shell === "dialog" ? "modal" : shape.side}>
      <Suspense fallback={<SkeletonRows rows={4} label={`Loading ${entry.title(params)}`} />}>
        <Panel params={params} close={close} replace={replace} />
      </Suspense>
    </div>
  );
  const common = { open: true, onOpenChange, title: entry.title(params), onCloseAutoFocus, wide: shape.wide };
  return shape.shell === "dialog" ? <Dialog {...common}>{body}</Dialog> : <Sheet side={shape.side} {...common}>{body}</Sheet>;
}

/**
 * The in-place API. Outside the host (the classic shell) it navigates exactly as today, so a converted button works in
 * both shells.
 */
export function useInPlace(): InPlaceApi {
  const ctx = useContext(InPlaceContext);
  const navigate = useNavigate();
  const fallback = useMemo<InPlaceApi>(() => ({
    enabled: false,
    stack: [],
    handles: () => false,
    open: (target) => {
      const path = typeof target === "string" ? target : DEFAULT_REGISTRY.get(target.kind)?.href(target.params ?? {});
      if (path) navigate(path);
    },
    close: () => undefined,
    replace: () => undefined,
  }), [navigate]);
  return ctx ?? fallback;
}
