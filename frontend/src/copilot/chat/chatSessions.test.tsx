import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ChatSessionsV2 } from "./ChatSessionsV2";
import sessionsSource from "./ChatSessionsV2.tsx?raw";
import chatSource from "./CopilotChatV2.tsx?raw";
import { conversationBody } from "./model";

const none = () => undefined;

describe("CH-2 Claude-like chat sessions", () => {
  it("a question goes into the open conversation, a new one, or (neither) the most recently used one", () => {
    expect(conversationBody(null)).toEqual({});
    expect(conversationBody({ sessionId: 7 })).toEqual({ session_id: 7 });
    expect(conversationBody({ sessionId: 7, newChat: true })).toEqual({ new_chat: true });
    expect(chatSource).toContain("chatApi.ask(question, about, target())");
    expect(chatSource).toContain("chatApi.history(sessionId)");
    expect(chatSource.match(/<ChatSessionsV2 /g)).toHaveLength(1);
  });

  it("the bar: New chat, and the open conversation's title on the list button", () => {
    const html = renderToStaticMarkup(<ChatSessionsV2 current={{ id: 2, title: "What is RSI?" }} busy={false} onNew={none} onOpen={none} onRenamed={none} onDeleted={none} />);
    expect(html).toContain("New chat");
    expect(html).toContain("What is RSI?");
    expect(html).toContain('aria-expanded="false"');
    const empty = renderToStaticMarkup(<ChatSessionsV2 current={null} busy onNew={none} onOpen={none} onRenamed={none} onDeleted={none} />);
    expect(empty).toContain("Conversations");
    expect(empty).toMatch(/data-new-chat[^>]*disabled|disabled[^>]*data-new-chat/);
  });

  it("open, rename and delete (asked once more) - only through the person's own routes", () => {
    for (const call of ["chatApi.sessions()", "chatApi.renameSession(id, name)", "chatApi.deleteSession(id)", 't("chat.deleteConfirm")'])
      expect(sessionsSource).toContain(call);
    expect(sessionsSource).not.toMatch(/placeOrder|exitNow|cancelOrder/);
  });
});
