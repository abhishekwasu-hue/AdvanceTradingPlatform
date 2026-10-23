import { createContext, useContext, useEffect, type ReactNode } from "react";

/**
 * Colour v2 (owner, 23 Oct; docs/design/CHART_COLOUR_REVIEW.md "Colour v2"): the visible, purposeful colour proposal,
 * behind the `colour_v2` flag (OFF; needs `ui_v2`). The v2 shell reads the flag once and shares the answer here, so a
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

/** Sets `data-colour="v2"` on <html> while `on`; removes it when off or unmounted. */
export function useColourV2Root(on: boolean): void {
  useEffect(() => {
    if (!on || typeof document === "undefined") return undefined;
    const root = document.documentElement;
    root.dataset.colour = "v2";
    return () => { delete root.dataset.colour; };
  }, [on]);
}
