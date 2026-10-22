import { useCallback, useLayoutEffect, useRef } from "react";
import { useLocation } from "react-router";

/**
 * UI2-1 review M2: the command bar and the More sheet are opened by a shortcut or a button outside the dialog (no
 * Radix Trigger), so Radix would drop focus on <body> when they close. This remembers what had focus when the dialog
 * opened and puts focus back there on close - unless the trader went to another page meanwhile (the shell then moves
 * focus to the new page's content itself). `fallback` names the opener when the browser did not focus it on the
 * click (Safari does not focus buttons on click).
 * Returns the dialog's `onCloseAutoFocus` handler.
 */
export function useReturnFocus(open: boolean, fallback?: () => HTMLElement | null): (e: Event) => void {
  const { pathname } = useLocation();
  const opener = useRef<HTMLElement | null>(null);
  const openedOn = useRef<string | null>(null);
  const current = useRef(pathname);
  current.current = pathname;
  // A layout effect runs before Radix moves focus into the dialog (its focus scope does that in a passive effect).
  useLayoutEffect(() => {
    if (!open) return;
    const active = typeof document === "undefined" ? null : document.activeElement;
    opener.current = active instanceof HTMLElement && active !== document.body ? active : null;
    openedOn.current = current.current;
  }, [open]);
  const fallbackRef = useRef(fallback);
  fallbackRef.current = fallback;
  return useCallback((e: Event) => {
    e.preventDefault();
    if (openedOn.current !== current.current) return;                 // a new page: the shell focuses its content
    const target = opener.current?.isConnected ? opener.current : fallbackRef.current?.() ?? null;
    target?.focus({ preventScroll: true });
  }, []);
}
