import { createContext, useContext } from "react";

/**
 * NAV-2: true inside an in-place panel. A page shown in a panel uses it to fit the panel instead of the whole window
 * (the Copilot's composer sits in the panel, not fixed to the bottom of the screen). A tiny module on purpose: pages
 * read it without pulling the host in.
 */
export const InPanelContext = createContext(false);

export function useInPanel(): boolean {
  return useContext(InPanelContext);
}
