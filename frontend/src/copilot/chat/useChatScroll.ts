import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Owner 13 Nov (chat redesign): a real chat's scrolling. An end marker sits after the newest message; while it is on
 * screen the reader is "at the latest". A new question (his own) always brings the newest message into view; an answer
 * arriving brings its turn into view unless he has scrolled away himself - never pulling him down while he reads an
 * older answer, where a "Jump to latest" button shows instead. Works in any scroller (the page, the in-place panel, the sheet):
 * the marker's visibility is read with an IntersectionObserver against the viewport, which already clips by every
 * scrolling parent. Without one (old browsers, tests) the reader counts as at the latest.
 */
/** A new question (his own) or another conversation opened: the newest message comes into view. Pure. */
export function shouldJump(previousLastId: number | null, lastId: number | null): boolean {
  return lastId !== null && lastId !== previousLastId;
}

/** The turns whose answer (or error) has just landed - each once; a turn restored from the server never. Pure: `seen`
 * is updated in place. */
export function newlySettled(turns: readonly { id: number; answer: unknown; error: string | null; at?: string | null }[], seen: Set<number>): number[] {
  const out: number[] = [];
  for (const turn of turns) {
    if (!(turn.answer || turn.error) || seen.has(turn.id)) continue;
    seen.add(turn.id);
    if (!turn.at) out.push(turn.id);
  }
  return out;
}

/** "Jump to latest" shows only while a conversation is on screen and HE has scrolled up from its end (his own wheel,
 * touch or key) - never because the screen itself brought an answer's start into view. Pure. */
export function showJump(atLatest: boolean, turnCount: number, scrolledByHim = true): boolean {
  return !atLatest && turnCount > 0 && scrolledByHim;
}

export function useChatScroll(lastId: number | null) {
  const has = lastId !== null;
  const end = useRef<HTMLDivElement | null>(null);
  const [atLatest, setAtLatest] = useState(true);
  const latest = useRef(true);
  // His own scrolling (wheel, touch, scroll keys) - set until he jumps back or asks again.
  const [scrolledByHim, setScrolledByHim] = useState(false);
  const himRef = useRef(false);
  useEffect(() => {
    const mine = () => { himRef.current = true; setScrolledByHim(true); };
    const key = (e: KeyboardEvent) => { if (["PageUp", "PageDown", "ArrowUp", "ArrowDown", "Home", "End"].includes(e.key)) mine(); };
    window.addEventListener("wheel", mine, { passive: true });
    window.addEventListener("touchmove", mine, { passive: true });
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("wheel", mine);
      window.removeEventListener("touchmove", mine);
      window.removeEventListener("keydown", key);
    };
  }, []);
  useEffect(() => {
    const el = end.current;
    if (!el || typeof IntersectionObserver === "undefined") return undefined;
    const io = new IntersectionObserver((entries) => {
      const seen = entries.some((e) => e.isIntersecting);
      latest.current = seen;
      setAtLatest(seen);
    }, { rootMargin: "0px 0px 24px 0px" });
    io.observe(el);
    return () => io.disconnect();
  }, [has]);                      // the marker exists once a turn does

  // His "Jump to latest" tap scrolls smoothly; the automatic moves are instant, so a fast answer never races a scroll
  // still on its way (the marker would read "not at the latest" mid-scroll and the answer would stay out of view).
  const jump = useCallback((behavior: ScrollBehavior = "smooth") => {
    end.current?.scrollIntoView?.({ block: "end", behavior });
    latest.current = true;
    himRef.current = false;
    setScrolledByHim(false);
  }, []);

  // A new turn (his own question) or another conversation opened: the newest message comes into view, always.
  const newest = useRef(lastId);
  useEffect(() => {
    if (shouldJump(newest.current, lastId)) jump("auto");
    newest.current = lastId;
  }, [lastId, jump]);

  // An answer arrived: the answered turn's start comes into view unless he has scrolled away himself since his
  // question (his own wheel / touch / keys) - never decided by an observer reading that may land after the paint.
  const reveal = useCallback((turnId: number) => {
    if (himRef.current) return;
    document.querySelector(`[data-turn='${turnId}'] [data-msg='copilot']`)?.scrollIntoView?.({ block: "start", behavior: "auto" });
  }, []);
  return { end, atLatest, jump, reveal, scrolledByHim };
}
