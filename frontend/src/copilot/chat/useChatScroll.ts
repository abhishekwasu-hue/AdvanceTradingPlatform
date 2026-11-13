import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Owner 13 Nov (chat redesign): a real chat's scrolling. An end marker sits after the newest message; while it is on
 * screen the reader is "at the latest". A new question (his own) always brings the newest message into view; an answer
 * arriving brings its turn into view only while he was at the latest - never pulling him down while he reads an older
 * answer, where a "Jump to latest" button shows instead. Works in any scroller (the page, the in-place panel, the sheet):
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

/** "Jump to latest" shows only while a conversation is on screen and he has scrolled up from its end. Pure. */
export function showJump(atLatest: boolean, turnCount: number): boolean {
  return !atLatest && turnCount > 0;
}

export function useChatScroll(lastId: number | null) {
  const has = lastId !== null;
  const end = useRef<HTMLDivElement | null>(null);
  const [atLatest, setAtLatest] = useState(true);
  const latest = useRef(true);
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
  }, []);

  // A new turn (his own question) or another conversation opened: the newest message comes into view, always.
  const newest = useRef(lastId);
  useEffect(() => {
    if (shouldJump(newest.current, lastId)) jump("auto");
    newest.current = lastId;
  }, [lastId, jump]);

  // An answer arrived: the answered turn's start comes into view only while he was at the latest.
  const reveal = useCallback((turnId: number) => {
    if (!latest.current) return;
    document.querySelector(`[data-turn='${turnId}'] [data-msg='copilot']`)?.scrollIntoView?.({ block: "start", behavior: "auto" });
  }, []);
  return { end, atLatest, jump, reveal };
}
