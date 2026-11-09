import { MessageCircle } from "lucide-react";
import { Suspense, lazy, useEffect, useRef, useState, type RefObject } from "react";
import { Skeleton } from "../components/primitives";
import { Sheet } from "../components/primitives/Dialog";
import type { AskContext } from "../copilot/askContext";
import { useMediaQuery, PHONE_QUERY } from "../utils/useMediaQuery";
import { useAskAiContext } from "./askAiEntry";

const CopilotChat = lazy(() => import("../copilot/chat/CopilotChatV2"));

/**
 * CH-1 (owner 5 Nov, "Default yes"): the Copilot's chat on every page - a round button in the corner opens the SAME
 * Copilot chat (one conversation with Copilot > Ask) in a side sheet (a bottom sheet on a phone). The symbol and the
 * timeframe of the page on screen ride with the questions (the page's Ask AI context); with a symbol, three ready
 * questions start the talk. Shown to a signed-in trader while `ai_copilot` is on, never on the Copilot pages themselves.
 * The answers are the Copilot's own (structure words, never a buy / sell call; the analyst register by default).
 */
export function starterQuestions(symbol?: string): string[] {
  if (!symbol) return [];
  return [`What is the structure of ${symbol} now?`, `Which areas is ${symbol} near?`, `What does the option chain of ${symbol} show?`];
}

export function chatContext(symbol: string | undefined, timeframe: string | undefined, page: string | null): AskContext | null {
  if (!symbol) return null;
  const pageKey = (page ?? "").toLowerCase().replace(/[^a-z0-9_-]/g, "").slice(0, 40);
  return { symbol, ...(timeframe ? { timeframe } : {}), ...(pageKey ? { page: pageKey } : {}) };
}

/** A box on screen (a DOMRect's edges), in viewport pixels. */
export interface Box { top: number; bottom: number; left: number; right: number }

/** A page's own bottom action bar (Find setups' Run bar, the terminal's status bar, the chain's strip) carries this. */
export const BOTTOM_ACTION = "data-bottom-action";

/**
 * Phone fix (bubble over Run): where the bubble's bottom edge must sit so it covers no bottom action bar. `rest` is the
 * bubble at its usual place; a bar it would overlap lifts it to just above that bar's top (the highest one wins). Null:
 * nothing in the way, the bubble stays where it is. Returned as the distance from the bottom of the screen, in px.
 */
export function liftAbove(rest: Box, bars: Box[], viewportHeight: number): number | null {
  let top: number | null = null;
  for (const b of bars) {
    if (b.bottom <= b.top || b.right <= b.left) continue;                                  // not on screen (hidden, sm:hidden)
    if (b.top >= rest.bottom || b.bottom <= rest.top || b.left >= rest.right || b.right <= rest.left) continue;
    top = top === null ? b.top : Math.min(top, b.top);
  }
  return top === null ? null : Math.max(0, viewportHeight - top);
}

/**
 * Owner fix (bubble over the list): while a page or a list scrolls the bubble steps aside (it fades and slides down,
 * instantly when motion is reduced) and comes back this long after the scrolling stops.
 */
export const SCROLL_IDLE_MS = 600;

/** The attribute the bubble carries while it steps aside for a scroll (styled by its own data-[scrolling] classes). */
export const SCROLLING = "data-scrolling";

/**
 * Owner fix (end padding): the room the bubble needs, set on <html> while it is shown. `--chat-bubble-clear`: from the
 * bottom of the screen to the bubble's top edge (the page's own end padding is at least this, plus a gap);
 * `--chat-bubble-end`: the bubble's height (a list that scrolls on its own ends with at least this, plus a gap).
 */
export const CLEAR_VAR = "--chat-bubble-clear";
export const END_VAR = "--chat-bubble-end";

/** The end padding of a list that scrolls on its own (the shared watchlist): its last row scrolls clear of the bubble. */
export const LIST_END_PAD = "pb-[max(var(--space-2),calc(var(--chat-bubble-end,0px)+var(--space-3)))]";

/** The room the page's end needs so its last row sits above the bubble: null while the bubble is not on screen. */
export function bubbleRoom(bubble: Box, viewportHeight: number): { clear: number; end: number } | null {
  if (bubble.bottom <= bubble.top || bubble.right <= bubble.left) return null;
  return { clear: Math.max(0, Math.round(viewportHeight - bubble.top)), end: Math.round(bubble.bottom - bubble.top) };
}

/**
 * Keeps the bubble clear of the page's bottom action bars as they mount, change and scroll (sticky bars), tells the
 * page and the lists how much end room it needs, and lets it step aside while anything scrolls. It never leaves the
 * accessibility tree and keeps its place in the tab order; with keyboard focus on it, it stays in view.
 */
function useClearOfBottomBars(ref: RefObject<HTMLElement>, active: boolean) {
  useEffect(() => {
    const el = ref.current;
    if (!active || !el || typeof window === "undefined") return;
    const root = document.documentElement;
    let frame = 0;
    let idle = 0;
    // Its box without the step-aside slide (also half way through it), so the room never changes while a page scrolls.
    const box = (): Box => {
      const b = el.getBoundingClientRect();
      const t = getComputedStyle(el).transform;
      const dy = t && t !== "none" && typeof DOMMatrixReadOnly !== "undefined" ? new DOMMatrixReadOnly(t).m42 : 0;
      return { top: b.top - dy, bottom: b.bottom - dy, left: b.left, right: b.right };
    };
    const place = () => {
      frame = 0;
      el.style.bottom = "";                                                                  // measure it at its usual place
      const bars = [...document.querySelectorAll(`[${BOTTOM_ACTION}]`)].map((b) => b.getBoundingClientRect());
      const lift = liftAbove(box(), bars, window.innerHeight);
      if (lift !== null) el.style.bottom = `calc(${Math.round(lift)}px + var(--space-3))`;
      const room = bubbleRoom(box(), window.innerHeight);
      if (room) {
        root.style.setProperty(CLEAR_VAR, `${room.clear}px`);
        root.style.setProperty(END_VAR, `${room.end}px`);
      } else {
        root.style.removeProperty(CLEAR_VAR);
        root.style.removeProperty(END_VAR);
      }
    };
    const schedule = () => { if (!frame) frame = window.requestAnimationFrame(place); };
    const rest = () => { idle = 0; el.removeAttribute(SCROLLING); };
    const onScroll = (e: Event) => {
      schedule();
      const t = e.target;
      if (t instanceof Element && t.closest("[role='dialog']")) return;                     // the chat sheet's own scroll
      if (document.activeElement === el) return;                                             // keyboard focus keeps it in view
      el.setAttribute(SCROLLING, "");
      if (idle) window.clearTimeout(idle);
      idle = window.setTimeout(rest, SCROLL_IDLE_MS);
    };
    const onFocus = () => { if (idle) window.clearTimeout(idle); rest(); };
    schedule();
    const watch = new MutationObserver(schedule);
    watch.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["class", "hidden"] });   // never "style": the bubble sets its own
    window.addEventListener("resize", schedule);
    document.addEventListener("scroll", onScroll, { capture: true, passive: true });
    el.addEventListener("focus", onFocus);
    return () => {
      if (frame) window.cancelAnimationFrame(frame);
      if (idle) window.clearTimeout(idle);
      watch.disconnect();
      window.removeEventListener("resize", schedule);
      document.removeEventListener("scroll", onScroll, { capture: true });
      el.removeEventListener("focus", onFocus);
      el.style.bottom = "";
      el.removeAttribute(SCROLLING);
      root.style.removeProperty(CLEAR_VAR);
      root.style.removeProperty(END_VAR);
    };
  }, [ref, active]);
}

export default function ChatBubbleV2({ on, signedIn, section }: { on: (flag: string) => boolean; signedIn: boolean; section: string | null }) {
  const [open, setOpen] = useState(false);
  const phone = useMediaQuery(PHONE_QUERY);
  const ctx = useAskAiContext();
  const button = useRef<HTMLButtonElement>(null);
  const onCopilot = section === "copilot";                                          // the Copilot pages have their own chat
  const shown = signedIn && on("ai_copilot") && !onCopilot;
  useClearOfBottomBars(button, shown);
  if (!shown) return null;
  const symbol = ctx?.analyze?.symbol ?? ctx?.symbol;
  const context = chatContext(symbol, ctx?.analyze?.timeframe, section);
  return (
    <>
      <button ref={button} type="button" onClick={() => setOpen(true)} aria-label={symbol ? `Chat with the Copilot about ${symbol}` : "Chat with the Copilot"}
        data-chat-bubble
        className="fixed bottom-[calc(env(safe-area-inset-bottom)+4.75rem)] right-space-4 z-30 flex h-12 w-12 items-center justify-center rounded-full bg-brand text-on-brand shadow-lg transition-[opacity,transform] duration-base ease-standard hover:opacity-90 data-[scrolling]:pointer-events-none data-[scrolling]:translate-y-space-4 data-[scrolling]:opacity-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 md:bottom-space-6 md:right-space-6">
        <MessageCircle size={22} aria-hidden />
      </button>
      <Sheet side={phone ? "bottom" : "right"} open={open} onOpenChange={setOpen} title={symbol ? `Copilot · ${symbol}` : "Copilot"}>
        <div data-chat-sheet className="min-h-0">
          {open && (
            <Suspense fallback={<Skeleton className="h-40" panel label="Loading" />}>
              <CopilotChat context={context} starters={starterQuestions(symbol)} compact />
            </Suspense>
          )}
        </div>
      </Sheet>
    </>
  );
}
