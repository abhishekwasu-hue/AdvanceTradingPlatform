import { istDayOf, msToNextIstMidnight } from "./model";

/** Small margin after midnight so the timer never fires a moment early (timers can run a few ms ahead of the clock). */
const AFTER_MIDNIGHT_MS = 1_000;

export interface IstDateWatch {
  now?: () => Date;
  /** Where "focus" is heard (the window). */
  target?: EventTarget;
  /** Where "visibilitychange" is heard (the document); its `visibilityState` is read. */
  doc?: EventTarget & { visibilityState?: string };
}

/**
 * COP-2 A (owner 11 Nov, "when the calendar date changes, the old information must go away automatically"): calls
 * `onChange` once each time the India date changes while the page stays open - at the next 00:00 IST (a timer, armed
 * again after each midnight), and when the tab comes back (visible again or focused) on a later India date than the
 * one last seen. Returns the cleanup.
 */
export function watchIstDate(onChange: () => void, opts: IstDateWatch = {}): () => void {
  const now = opts.now ?? (() => new Date());
  const target = opts.target ?? (typeof window !== "undefined" ? window : undefined);
  const doc = opts.doc ?? (typeof document !== "undefined" ? document : undefined);
  let day = istDayOf(now());
  let timer: ReturnType<typeof setTimeout> | undefined;
  const check = () => {
    const today = istDayOf(now());
    if (today !== day) { day = today; onChange(); }
  };
  const arm = () => {
    timer = setTimeout(() => { check(); arm(); }, msToNextIstMidnight(now()) + AFTER_MIDNIGHT_MS);
  };
  const onVisible = () => { if (!doc || doc.visibilityState === undefined || doc.visibilityState === "visible") check(); };
  arm();
  target?.addEventListener("focus", check);
  doc?.addEventListener("visibilitychange", onVisible);
  return () => {
    if (timer !== undefined) clearTimeout(timer);
    target?.removeEventListener("focus", check);
    doc?.removeEventListener("visibilitychange", onVisible);
  };
}
