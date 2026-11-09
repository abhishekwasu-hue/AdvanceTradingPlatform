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

/** Keeps the bubble clear of the page's bottom action bars as they mount, change and scroll (sticky bars). */
function useClearOfBottomBars(ref: RefObject<HTMLElement>, active: boolean) {
  useEffect(() => {
    const el = ref.current;
    if (!active || !el || typeof window === "undefined") return;
    let frame = 0;
    const place = () => {
      frame = 0;
      el.style.bottom = "";                                                                  // measure it at its usual place
      const bars = [...document.querySelectorAll(`[${BOTTOM_ACTION}]`)].map((b) => b.getBoundingClientRect());
      const lift = liftAbove(el.getBoundingClientRect(), bars, window.innerHeight);
      if (lift !== null) el.style.bottom = `calc(${Math.round(lift)}px + var(--space-3))`;
    };
    const schedule = () => { if (!frame) frame = window.requestAnimationFrame(place); };
    schedule();
    const watch = new MutationObserver(schedule);
    watch.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["class", "hidden"] });   // never "style": the bubble sets its own
    window.addEventListener("resize", schedule);
    document.addEventListener("scroll", schedule, { capture: true, passive: true });
    return () => {
      if (frame) window.cancelAnimationFrame(frame);
      watch.disconnect();
      window.removeEventListener("resize", schedule);
      document.removeEventListener("scroll", schedule, { capture: true });
      el.style.bottom = "";
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
        className="fixed bottom-[calc(env(safe-area-inset-bottom)+4.75rem)] right-space-4 z-30 flex h-12 w-12 items-center justify-center rounded-full bg-brand text-on-brand shadow-lg hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 md:bottom-space-6 md:right-space-6">
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
