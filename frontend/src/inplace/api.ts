import { decodePanel, encodePanel, openTransition, panelValues, withPanels, type PanelEntry, type PanelRegistry, type PanelSpec } from "./registry";

/**
 * M75 NAV-1: the in-place API, without React (InPlaceProvider wires it to the router; the tests to a made-up history).
 * The stack is read from the address only: `?panel=` on the same path, one value per level, bottom first.
 */

export interface InPlaceApi {
  /** True while panels open in place (`ui_v2` on); false: `open()` navigates as today. */
  enabled: boolean;
  /** The open panels, bottom first. */
  stack: readonly PanelSpec[];
  /** Opens a panel by kind and parameters, or by an app address (`/charts/NIFTY?tf=15min`) resolved through the
   * registry. An address no panel answers for is navigated to as today (with `opts.state`; `opts.newTab`: a new browser
   * tab, as the button did before). */
  open: (target: PanelSpec | string, opts?: OpenOptions) => void;
  /** Closes the top panel (Back when this tab pushed it; otherwise the address loses it). */
  close: () => void;
  /** Replaces the top panel (or its parameters) without a new history entry; opens it when none is open. */
  replace: (spec: PanelSpec) => void;
  /** True when `open(path)` would show a panel (not navigate). */
  handles: (path: string) => boolean;
}

/** NAV-2: what a converted button hands over, exactly as its navigation did before. */
export interface OpenOptions {
  /** The navigation state the facility's page reads (a prompt, a draft id, an idea). In place, the panel's page reads it
   * from its own location (InPlaceProvider gives each panel one); otherwise the navigation carries it as today. */
  state?: unknown;
  /** The button opened a new browser tab before: when no panel shows it (or `ui_v2` is off) it still does. */
  newTab?: boolean;
}

export interface RouterLocation { pathname: string; search: string; hash: string; state: unknown }

export interface NavigateFn {
  (to: string | { pathname: string; search: string; hash: string }, opts?: { replace?: boolean; state?: unknown; preventScrollReset?: boolean }): void;
  (delta: number): void;
}

/** The history entry's marker: how many panel levels this browser tab pushed on top of the entry it started from. A
 * panel opened from a deep link was not pushed, so closing it rewrites the address instead of going back. */
export const STATE_KEY = "atpInPlace";

export function pushedIn(state: unknown): number {
  const n = state && typeof state === "object" ? (state as Record<string, unknown>)[STATE_KEY] : 0;
  return typeof n === "number" && n > 0 ? n : 0;
}
function withPushed(state: unknown, n: number): Record<string, unknown> {
  return { ...(state && typeof state === "object" ? state as Record<string, unknown> : {}), [STATE_KEY]: n };
}

/** NAV-2: the state each open panel was handed (by level, bottom first), kept in the same history entry. */
export const HANDOFF_KEY = "atpInPlaceHandoff";

export function handoffs(state: unknown): unknown[] {
  const h = state && typeof state === "object" ? (state as Record<string, unknown>)[HANDOFF_KEY] : undefined;
  return Array.isArray(h) ? h : [];
}

export interface OpenPanel { spec: PanelSpec; entry: PanelEntry; value: string }

/** The open panels in a search string, with their entries (a value nobody registered, or switched off, is skipped). */
export function openPanels(search: string, registry: PanelRegistry, on: (flag: string) => boolean): OpenPanel[] {
  const out: OpenPanel[] = [];
  for (const value of panelValues(search)) {
    const spec = decodePanel(value, registry);
    const entry = spec ? registry.get(spec.kind) : undefined;
    if (spec && entry && (!entry.enabled || entry.enabled(on))) out.push({ spec, entry, value });
  }
  return out;
}

export interface ApiDeps {
  enabled: boolean;
  on: (flag: string) => boolean;
  registry: PanelRegistry;
  /** The current location (read at call time). */
  location: () => RouterLocation;
  navigate: NavigateFn;
  /** Called with the new level's index when a panel opens (the host remembers the clicked button there). */
  onOpenLevel?: (level: number) => void;
  /** Called for an address no panel answers for (a dev warning). */
  onUnregistered?: (path: string) => void;
  /** Opens an address in a new browser tab (a button that did so before, when no panel shows it). */
  newTab?: (path: string) => void;
}

export function createInPlaceApi(d: ApiDeps): InPlaceApi {
  const isOn = (entry: PanelEntry | undefined): entry is PanelEntry => !!entry && d.enabled && (!entry.enabled || entry.enabled(d.on));
  const values = () => (d.enabled ? openPanels(d.location().search, d.registry, d.on).map((p) => p.value) : []);
  const write = (next: string[], replace: boolean, pushed: number, handed?: unknown[]) => {
    const loc = d.location();
    const state = withPushed(loc.state, pushed);
    if (handed) state[HANDOFF_KEY] = handed;
    d.navigate({ pathname: loc.pathname, search: withPanels(loc.search, next), hash: loc.hash },
      { replace, state, preventScrollReset: true });
  };
  return {
    enabled: d.enabled,
    stack: d.enabled ? openPanels(d.location().search, d.registry, d.on).map((p) => p.spec) : [],
    handles: (path) => { const spec = d.registry.resolve(path); return !!spec && isOn(d.registry.get(spec.kind)); },
    open: (target, opts) => {
      const spec = typeof target === "string" ? d.registry.resolve(target) : target;
      const entry = spec ? d.registry.get(spec.kind) : undefined;
      if (!spec || !isOn(entry)) {
        const path = typeof target === "string" ? target : entry?.href(target.params ?? {});
        if (typeof target === "string" && d.enabled && !spec) d.onUnregistered?.(target);
        if (!path) return;
        if (opts?.newTab && d.newTab) d.newTab(path);
        else if (opts?.state !== undefined) d.navigate(path, { state: opts.state });
        else d.navigate(path);
        return;
      }
      const t = openTransition(values(), encodePanel(spec, entry));
      if (!t) return;
      const level = t.values.length - 1;
      d.onOpenLevel?.(level);
      const loc = d.location();
      const pushed = pushedIn(loc.state);
      const before = handoffs(loc.state);
      const handed = before.slice(0, level);
      handed[level] = opts?.state ?? null;
      // Nothing handed over at any level: the history entry's state stays exactly as it was (plus the level count).
      const keep = before.length === 0 && opts?.state === undefined;
      write(t.values, t.replace, t.replace ? pushed : pushed + 1, keep ? undefined : handed);
    },
    close: () => {
      const now = values();
      if (!now.length) return;
      const loc = d.location();
      const pushed = pushedIn(loc.state);
      if (pushed > 0) d.navigate(-1);                 // the entry this tab pushed: Back and close are the same step
      else {
        const before = handoffs(loc.state);
        write(now.slice(0, -1), true, 0, before.length ? before.slice(0, now.length - 1) : undefined);
      }
    },
    replace: (spec) => {
      const entry = d.registry.get(spec.kind);
      if (!isOn(entry)) return;
      const now = values();
      write([...now.slice(0, Math.max(0, now.length - 1)), encodePanel(spec, entry)], true, pushedIn(d.location().state));
    },
  };
}
