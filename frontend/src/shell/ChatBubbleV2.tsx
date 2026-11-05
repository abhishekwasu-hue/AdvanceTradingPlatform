import { MessageCircle } from "lucide-react";
import { Suspense, lazy, useState } from "react";
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

export default function ChatBubbleV2({ on, signedIn, section }: { on: (flag: string) => boolean; signedIn: boolean; section: string | null }) {
  const [open, setOpen] = useState(false);
  const phone = useMediaQuery(PHONE_QUERY);
  const ctx = useAskAiContext();
  if (!signedIn || !on("ai_copilot") || section === "copilot") return null;
  const symbol = ctx?.analyze?.symbol ?? ctx?.symbol;
  const context = chatContext(symbol, ctx?.analyze?.timeframe, section);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)} aria-label={symbol ? `Chat with the Copilot about ${symbol}` : "Chat with the Copilot"}
        data-chat-bubble
        className="fixed bottom-[calc(env(safe-area-inset-bottom)+4.75rem)] right-space-4 z-30 flex h-12 w-12 items-center justify-center rounded-full bg-brand text-white shadow-lg hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 md:bottom-space-6 md:right-space-6">
        <MessageCircle size={22} aria-hidden />
      </button>
      <Sheet side={phone ? "bottom" : "right"} open={open} onOpenChange={setOpen} title={symbol ? `Copilot · ${symbol}` : "Copilot"}>
        <div data-chat-sheet className="min-h-0">
          {open && (
            <Suspense fallback={<Skeleton className="h-40" panel label="Loading" />}>
              <CopilotChat context={context} starters={starterQuestions(symbol)} />
            </Suspense>
          )}
        </div>
      </Sheet>
    </>
  );
}
