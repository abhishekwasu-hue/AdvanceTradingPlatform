import { describe, expect, it } from "vitest";
import bubbleSource from "./ChatBubbleV2.tsx?raw";
import shellSource from "./AppShellV2.tsx?raw";
import { chatContext, starterQuestions } from "./ChatBubbleV2";

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
});
