import { describe, expect, it } from "vitest";
import bubbleSource from "./ChatBubbleV2.tsx?raw";
import shellSource from "./AppShellV2.tsx?raw";
import screenerSource from "../pages/ScreenerPage.tsx?raw";
import statusBarSource from "../terminal/StatusBarV2.tsx?raw";
import chainSource from "../fno/ChainTradingV2.tsx?raw";
import ticketSource from "../fno/OrderTicketV2.tsx?raw";
import watchlistSource from "../chart/WatchlistPanel.tsx?raw";
import tokensSource from "../styles/tokens.css?raw";
import { BOTTOM_ACTION, CLEAR_VAR, END_VAR, LIST_END_PAD, SCROLL_IDLE_MS, SCROLLING, bubbleRoom, chatContext, liftAbove, starterQuestions } from "./ChatBubbleV2";

describe("CH-1 the Copilot chat bubble on every page", () => {
  it("ready questions and the context carry the page's symbol; none without one", () => {
    expect(starterQuestions()).toEqual([]);
    expect(starterQuestions("NIFTY 50")).toEqual(["What is the structure of NIFTY 50 now?", "Which areas is NIFTY 50 near?",
      "What does the option chain of NIFTY 50 show?"]);
    expect(chatContext(undefined, "15m", "charts")).toBeNull();
    expect(chatContext("NIFTY 50", "15m", "charts")).toEqual({ symbol: "NIFTY 50", timeframe: "15m", page: "charts" });
    expect(chatContext("TCS", undefined, null)).toEqual({ symbol: "TCS" });
  });
  it("the same Copilot chat, behind ai_copilot, never on the Copilot pages; mounted once in the shell", () => {
    expect(bubbleSource).toContain('import("../copilot/chat/CopilotChatV2")');
    expect(bubbleSource).toContain('on("ai_copilot")');
    expect(bubbleSource).toContain('section === "copilot"');
    expect(shellSource.match(/<ChatBubbleV2 /g)).toHaveLength(1);
    for (const w of [/\bbuy\b/i, /\bsell\b/i, /target/i, /stop-loss/i]) expect(starterQuestions("X").join(" ")).not.toMatch(w);
  });
  it("never covers a page's bottom action bar: a bar it would overlap lifts it just above the bar's top", () => {
    const rest = { top: 676, bottom: 724, left: 326, right: 374 };                  // 390 x 800, its usual place
    const runBar = { top: 682, bottom: 744, left: 0, right: 390 };                  // Find setups: Save and Run
    expect(liftAbove(rest, [runBar], 800)).toBe(118);
    expect(liftAbove(rest, [runBar, { top: 640, bottom: 690, left: 0, right: 390 }], 800)).toBe(160);   // the highest wins
    expect(liftAbove(rest, [], 800)).toBeNull();
    expect(liftAbove(rest, [{ top: 300, bottom: 360, left: 0, right: 390 }], 800)).toBeNull();          // a sticky bar mid-page
    expect(liftAbove(rest, [{ top: 0, bottom: 0, left: 0, right: 0 }], 800)).toBeNull();                 // hidden (sm:hidden)
    expect(liftAbove(rest, [{ top: 682, bottom: 744, left: 0, right: 300 }], 800)).toBeNull();          // beside it, not under it
  });
  it("every bottom action bar carries the marker the bubble reads", () => {
    expect(BOTTOM_ACTION).toBe("data-bottom-action");
    expect(screenerSource.match(/data-testid="screener-bottom-bar" data-bottom-action/g)).toHaveLength(2);
    expect(statusBarSource.match(/aria-label="Status bar" data-bottom-action/g)).toHaveLength(3);
    expect(chainSource).toContain('aria-label="Chain figures" data-bottom-action');
    expect(ticketSource).toContain('data-bottom-action={stickyActions ? "" : undefined}');
    expect(bubbleSource).toContain("useClearOfBottomBars(button, shown)");
  });
  it("steps aside while a page or a list scrolls and comes back shortly after; reduced motion and the keyboard respected", () => {
    expect(SCROLL_IDLE_MS).toBeGreaterThanOrEqual(300);
    expect(SCROLL_IDLE_MS).toBeLessThanOrEqual(1000);                              // "shortly after scrolling stops"
    expect(SCROLLING).toBe("data-scrolling");
    expect(bubbleSource).toContain('document.addEventListener("scroll", onScroll, { capture: true, passive: true })');   // any scroller, not only the window
    expect(bubbleSource).toContain("window.setTimeout(rest, SCROLL_IDLE_MS)");
    const cls = bubbleSource.match(/data-chat-bubble\s+className="([^"]+)"/)![1];
    for (const c of ["data-[scrolling]:opacity-0", "data-[scrolling]:translate-y-space-4", "data-[scrolling]:pointer-events-none", "duration-base", "ease-standard"]) expect(cls).toContain(c);
    expect(tokensSource).toMatch(/prefers-reduced-motion: reduce\)\s*\{\s*:root \{ --dur-fast: 0ms; --dur-base: 0ms; \}/);   // no slide when motion is reduced
    // never out of the accessibility tree or the tab order; keyboard focus keeps it in view
    const tag = bubbleSource.match(/<button ref=\{button\}[\s\S]*?data-chat-bubble\s+className="[^"]+">/)![0];
    for (const w of ["aria-hidden", "inert", "tabIndex", "hidden"]) expect(tag).not.toContain(w);
    expect(bubbleSource).not.toMatch(/setAttribute\("(aria-hidden|inert|hidden|tabindex)"/);
    expect(bubbleSource).toContain("if (document.activeElement === el) return;");
    expect(bubbleSource).toContain('el.addEventListener("focus", onFocus)');
  });
  it("tells the page and the lists the end room it needs: the page ends above it, a list's last row scrolls clear", () => {
    expect(bubbleRoom({ top: 676, bottom: 724, left: 326, right: 374 }, 844)).toEqual({ clear: 168, end: 48 });
    expect(bubbleRoom({ top: 0, bottom: 0, left: 0, right: 0 }, 844)).toBeNull();
    expect([CLEAR_VAR, END_VAR]).toEqual(["--chat-bubble-clear", "--chat-bubble-end"]);
    // the one page container (the shell's <main>), at every width, keeps its own padding when that is more
    const main = shellSource.match(/<main id="main"[\s\S]*?>/)![0];
    expect(main.match(/calc\(var\(--chat-bubble-clear,0px\)\+var\(--space-3\)\)/g)).toHaveLength(3);
    expect(main).toContain("pb-[max(calc(6rem+env(safe-area-inset-bottom)),");
    expect(main).toContain("pb-[max(calc(9rem+env(safe-area-inset-bottom)),");
    expect(main).toContain("md:pb-[max(var(--space-10),");
    // the shared list (Charts, the Watchlist page, the Terminal): both of its scrolling lists
    expect(LIST_END_PAD).toBe("pb-[max(var(--space-2),calc(var(--chat-bubble-end,0px)+var(--space-3)))]");
    expect(watchlistSource.match(/overflow-y-auto px-space-1 \$\{LIST_END_PAD\}/g)).toHaveLength(2);
  });
});
