import { describe, expect, it } from "vitest";
import bubbleSource from "./ChatBubbleV2.tsx?raw";
import shellSource from "./AppShellV2.tsx?raw";
import screenerSource from "../pages/ScreenerPage.tsx?raw";
import statusBarSource from "../terminal/StatusBarV2.tsx?raw";
import chainSource from "../fno/ChainTradingV2.tsx?raw";
import ticketSource from "../fno/OrderTicketV2.tsx?raw";
import { BOTTOM_ACTION, chatContext, liftAbove, starterQuestions } from "./ChatBubbleV2";

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
});
