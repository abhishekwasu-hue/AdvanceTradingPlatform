import { useSyncExternalStore } from "react";

/** PW2: true while the media query matches; re-renders when it changes (rotation, window resize). */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      if (typeof window === "undefined" || !window.matchMedia) return () => undefined;
      const mql = window.matchMedia(query);
      mql.addEventListener("change", onChange);
      return () => mql.removeEventListener("change", onChange);
    },
    () => typeof window !== "undefined" && !!window.matchMedia && window.matchMedia(query).matches,
    () => false,
  );
}

/** Phones: below Tailwind's md breakpoint, where the sidebar becomes a drawer and the bottom navigation shows. */
export const PHONE_QUERY = "(max-width: 767px)";
/** TERM-2 / CT-1 T3: below the desktop grid (phone and tablet up to 1023 px) - one constant for every page that switches there. */
export const NARROW_QUERY = "(max-width: 1023px)";
