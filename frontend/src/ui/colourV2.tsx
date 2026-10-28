import { createContext, useContext, useEffect, type ReactNode } from "react";

/**
 * Colour v2 (owner, 23 Oct; docs/design/CHART_COLOUR_REVIEW.md "Colour v2"): the visible, purposeful colour proposal,
 * behind the `colour_v2` flag (ON by default since the owner's ✅ on 24 Oct; needs `ui_v2`). The v2 shell reads the flag once and shares the answer here, so a
 * page or shared component asks `useColourV2()` instead of reading the flags itself. Outside the shell (a unit test, a
 * story, the old shell) the answer is false: every page looks exactly as before.
 *
 * The shell also marks <html> with `data-colour="v2"` while it is on, for the layered dark surfaces in tokens.css (a
 * dialog or sheet portalled to <body> takes them too).
 */
export const COLOUR_V2_FLAG = "colour_v2";

/** On only when both flags are known and on (fail closed). */
export function colourV2On(on: (name: string) => boolean): boolean {
  return on("ui_v2") && on(COLOUR_V2_FLAG);
}

const ColourV2Context = createContext(false);

export function ColourV2Provider({ on, children }: { on: boolean; children: ReactNode }) {
  useColourV2Root(on);
  return <ColourV2Context.Provider value={on}>{children}</ColourV2Context.Provider>;
}

export function useColourV2(): boolean {
  return useContext(ColourV2Context);
}

/** UX-4: sets `data-numerals="tabular"` on <html> while the v2 shell is shown (fixed-width digits everywhere, the
 * dialogs and sheets that render outside the shell included); removes it when unmounted. */
export function useTabularRoot(on: boolean): void {
  useEffect(() => {
    if (!on || typeof document === "undefined") return undefined;
    const root = document.documentElement;
    root.dataset.numerals = "tabular";
    return () => { delete root.dataset.numerals; };
  }, [on]);
}

/** UX-14 "Professional palette" (owner, 28 Oct; docs/owner-orders/ATP_ORDER_UX14.md): the colour v3 token set, behind
 * `colour_v3` (OFF until the owner's OK on the comparison page). It rides on colour v2: on only with ui_v2 and colour_v2. */
export const COLOUR_V3_FLAG = "colour_v3";

export function colourV3On(on: (name: string) => boolean): boolean {
  return colourV2On(on) && on(COLOUR_V3_FLAG);
}

/** Sets `data-palette="v3"` on <html> while `on` (the colour v3 tokens in tokens.css); removes it when off or unmounted. */
export function useColourV3Root(on: boolean): void {
  useEffect(() => {
    if (!on || typeof document === "undefined") return undefined;
    const root = document.documentElement;
    root.dataset.palette = "v3";
    return () => { delete root.dataset.palette; };
  }, [on]);
}

/** Sets `data-colour="v2"` on <html> while `on`; removes it when off or unmounted. */
export function useColourV2Root(on: boolean): void {
  useEffect(() => {
    if (!on || typeof document === "undefined") return undefined;
    const root = document.documentElement;
    root.dataset.colour = "v2";
    return () => { delete root.dataset.colour; };
  }, [on]);
}
