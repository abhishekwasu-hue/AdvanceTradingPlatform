import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const request = vi.fn();
vi.mock("../api/client", () => ({ request: (...args: unknown[]) => request(...args) }));

import InviteRequest, { InviteRequestSlot, NOTE_MAX, sendInviteRequest } from "./InviteRequest";
import signInSource from "./SignInScreen.tsx?raw";
import source from "./InviteRequest.tsx?raw";

/** M95 PR-3 (M95-Q3): "Ask for an invite" on the sign-in screen, only while sign-up is closed. */

describe("the invite request slot", () => {
  it("renders nothing while sign-up is open", () => {
    expect(renderToStaticMarkup(<InviteRequestSlot signUpOpen />)).toBe("");
  });

  it("while sign-up is closed: the invite-link line and an 'Ask for an invite' button, the form closed", () => {
    const html = renderToStaticMarkup(<InviteRequestSlot signUpOpen={false} />);
    expect(html).toContain("New here? Ask your team owner for an invite link.");
    expect(html).toContain(">Ask for an invite</button>");
    expect(html).toContain('data-invite-request="closed"');
    expect(html).not.toContain("<form");
  });

  it("without ui_v2: the old invite-link line only, exactly as before", () => {
    expect(renderToStaticMarkup(<InviteRequestSlot signUpOpen={false} v2={false} />)).toBe("<span>New here? Ask your team owner for an invite link.</span>");
    expect(renderToStaticMarkup(<InviteRequestSlot signUpOpen v2={false} />)).toBe("");
  });

  it("the open form: e-mail, an optional note capped at 500, one send button - phone-sized controls", () => {
    const html = renderToStaticMarkup(<InviteRequest startOpen />);
    expect(html).toMatch(/<label[^>]*for="[^"]+"[^>]*>Your e-mail<\/label>/);
    expect(html).toMatch(/<input[^>]*type="email"[^>]*autoComplete="email"/);
    expect(html).toContain("(optional)");
    expect(html).toContain(`maxLength="${NOTE_MAX}"`);
    expect(NOTE_MAX).toBe(500);
    expect(html).toContain("0 / 500");
    expect(html).toContain("Send request");
    expect(html).toContain("h-11");
  });

  it("is mounted where the old invite line was, with the smallest change", () => {
    expect(signInSource).toContain("<InviteRequestSlot signUpOpen={signUpOpen} v2={v2} />");
    expect(signInSource).not.toContain("<span>New here? Ask your team owner for an invite link.</span>");
  });

  it("uses tokens only for text sizes and colours", () => {
    expect(source).not.toMatch(/text-(xs|sm|base|lg|xl)\b|#[0-9a-f]{3,6}\b|text-(red|green|gray|slate)-/i);
  });
});

describe("sending a request", () => {
  beforeEach(() => request.mockReset());

  it("posts the trimmed address and note; an empty note is sent as none", async () => {
    request.mockResolvedValue({ message: "Thank you." });
    await sendInviteRequest("  me@example.com ", "  hi  ");
    expect(request).toHaveBeenCalledWith("/auth/invite-request", { method: "POST", body: JSON.stringify({ email: "me@example.com", note: "hi" }) });
    await sendInviteRequest("me@example.com", "   ");
    expect(request).toHaveBeenLastCalledWith("/auth/invite-request", { method: "POST", body: JSON.stringify({ email: "me@example.com", note: null }) });
  });
});
