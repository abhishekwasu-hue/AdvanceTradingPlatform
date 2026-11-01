import type { ComponentType } from "react";

/**
 * M75 rule 1 / NAV-1 (docs/design/M75_NO_NAVIGATION.md section 4): the registry of in-place panels. One entry per
 * facility: which full route it answers for (`match`), how it is shown (`presentation`), the full route to fall back to
 * (`href`) and the component, loaded only when the panel first opens (`load`). Nothing here renders; the host
 * (InPlaceProvider) reads it.
 */

/** How a panel is shown: a side panel (wide for a workspace such as a chart), a drawer, or a centred modal. On a phone
 * every presentation except `modal` is a bottom sheet. */
export type Presentation = "side" | "side-wide" | "drawer" | "modal";

export type PanelParams = Readonly<Record<string, string>>;

/** What a panel is: its kind and its parameters (a chart's symbol, timeframe, exchange). */
export interface PanelSpec { kind: string; params?: PanelParams }

/** What every panel component receives from the host. */
export interface PanelProps {
  params: PanelParams;
  /** Closes this panel (one level), back to the exact state underneath. */
  close: () => void;
  /** Changes this panel's parameters in place (the address follows, no new history entry). */
  replace: (params: PanelParams) => void;
}

export interface PanelEntry {
  /** Lower case letters, digits and dashes: it is the first part of the `?panel=` value. */
  kind: string;
  /** The parameter names in the order they are written in `?panel=` (`chart:NIFTY:15min:NSE`). */
  keys: readonly string[];
  title: (params: PanelParams) => string;
  presentation: Presentation;
  /** The parameters when this panel answers for a full route (pathname and search), null otherwise. */
  match: (url: { pathname: string; search: URLSearchParams }) => PanelParams | null;
  /** The full route that shows the same facility as a page: the link's real address and the fallback navigation. */
  href: (params: PanelParams) => string;
  /** False while the facility is switched off for the organisation: the host then navigates as today. */
  enabled?: (on: (flag: string) => boolean) => boolean;
  load: () => Promise<{ default: ComponentType<PanelProps> }>;
}

export interface PanelRegistry {
  entries: readonly PanelEntry[];
  get: (kind: string) => PanelEntry | undefined;
  /** The panel registered for an app address (`/charts/NIFTY?tf=15min`), or null when none answers for it. */
  resolve: (path: string) => PanelSpec | null;
}

const KIND = /^[a-z][a-z0-9-]*$/;

/** Splits an app address into its pathname (no trailing slash) and search; an address on another site is no route. */
export function splitPath(path: string): { pathname: string; search: URLSearchParams } | null {
  let url: URL;
  try { url = new URL(path, "http://app.invalid"); }
  catch { return null; }   // intentional: not an address at all - no panel answers for it
  if (url.origin !== "http://app.invalid") return null;   // another site: never a panel
  return { pathname: url.pathname.replace(/\/+$/, "") || "/", search: url.searchParams };
}

export function createRegistry(entries: readonly PanelEntry[]): PanelRegistry {
  const byKind = new Map<string, PanelEntry>();
  for (const e of entries) {
    if (!KIND.test(e.kind)) throw new Error(`in-place panel kind "${e.kind}" must be lower case letters, digits and dashes`);
    if (byKind.has(e.kind)) throw new Error(`in-place panel kind "${e.kind}" is registered twice`);
    byKind.set(e.kind, e);
  }
  return {
    entries,
    get: (kind) => byKind.get(kind),
    resolve: (path) => {
      const url = splitPath(path);
      if (!url) return null;
      for (const e of entries) {
        const params = e.match(url);
        if (params) return { kind: e.kind, params };
      }
      return null;
    },
  };
}

// ---- `?panel=` -------------------------------------------------------------------------------------------------

/** A parameter value inside `?panel=`: `~` and `:` are escaped as `~7E` / `~3A`, so the value survives any page that
 * rewrites the search with URLSearchParams (which decodes once). */
function escapePart(v: string): string { return v.replace(/~/g, "~7E").replace(/:/g, "~3A"); }
function unescapePart(v: string): string { return v.replace(/~3A/gi, ":").replace(/~7E/gi, "~"); }

/** The decoded `?panel=` value of a panel: `chart:NIFTY 50:15min` (empty trailing parameters left out). */
export function encodePanel(spec: PanelSpec, entry: Pick<PanelEntry, "keys">): string {
  const parts = entry.keys.map((k) => spec.params?.[k] ?? "");
  while (parts.length && parts[parts.length - 1] === "") parts.pop();
  return [spec.kind, ...parts.map(escapePart)].join(":");
}

/** The panel a `?panel=` value names, or null for a kind nobody registered. */
export function decodePanel(value: string, registry: Pick<PanelRegistry, "get">): PanelSpec | null {
  const [kind = "", ...parts] = value.split(":");
  const entry = registry.get(kind);
  if (!entry) return null;
  const params: Record<string, string> = {};
  entry.keys.forEach((k, i) => { const v = parts[i]; if (v) params[k] = unescapePart(v); });
  return { kind, params };
}

/** The panels open in a search string, bottom first (one `panel` parameter per level). */
export function panelValues(search: string): string[] {
  return new URLSearchParams(search).getAll("panel");
}

/** `search` with its `panel` parameters replaced by `values` (the other parameters kept in order). The `:` separators
 * are written as they are, so the address reads `?panel=chart:NIFTY%2050:15min`. */
export function withPanels(search: string, values: readonly string[]): string {
  const rest = new URLSearchParams(search);
  rest.delete("panel");
  const parts = rest.toString() ? [rest.toString()] : [];
  for (const v of values) parts.push(`panel=${encodeURIComponent(v).replace(/%3A/gi, ":")}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

/** At most three panels deep: opening a fourth replaces the top one. */
export const MAX_DEPTH = 3;

/** The stack after opening `value` on top of `values`: `replace` when the top is replaced instead of pushed (the
 * stack is full), null when that panel is already on top (nothing to do). */
export function openTransition(values: readonly string[], value: string, max = MAX_DEPTH): { values: string[]; replace: boolean } | null {
  if (values[values.length - 1] === value) return null;
  if (values.length >= max) return { values: [...values.slice(0, max - 1), value], replace: true };
  return { values: [...values, value], replace: false };
}

/** How a presentation is drawn at this width. */
export function presentationFor(p: Presentation, phone: boolean): { shell: "sheet" | "dialog"; side?: "right" | "bottom"; wide: boolean } {
  if (p === "modal") return { shell: "dialog", wide: false };
  if (phone) return { shell: "sheet", side: "bottom", wide: false };
  return { shell: "sheet", side: "right", wide: p === "side-wide" };
}
