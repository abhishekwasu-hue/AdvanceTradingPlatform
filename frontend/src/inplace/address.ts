import { useContext } from "react";
import { UNSAFE_LocationContext } from "react-router";

/**
 * M75 NAV-5a: the address a page reads its search and hash from - the router's when there is one (inside an in-place
 * panel that is the panel's own address, not the window's), else the window's (a part rendered alone in a test). It
 * never throws outside a router, so a page that read `window.location` can switch to it without new test set-up.
 */
export function useAddress(): { search: string; hash: string } {
  const loc = useContext(UNSAFE_LocationContext)?.location;
  if (loc) return { search: loc.search, hash: loc.hash };
  return typeof window === "undefined" ? { search: "", hash: "" } : { search: window.location.search, hash: window.location.hash };
}
