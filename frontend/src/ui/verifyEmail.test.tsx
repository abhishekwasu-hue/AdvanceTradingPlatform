import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import NeedsAttentionV2 from "../dashboard/NeedsAttentionV2";
import type { BrokerLogin } from "./BrokerGate";
import { resendText, resendVerification } from "./verifyEmail";
import accountSource from "../pages/AccountPage.tsx?raw";
import appSource from "../App.tsx?raw";
import topBarSource from "../shell/TopBarV2.tsx?raw";

const idle: BrokerLogin = { busy: null, error: null, codeStep: null, login: () => undefined, submitCode: () => undefined };

afterEach(() => vi.restoreAllMocks());

describe("user-friendly pass item 1: 'Verify your email' in Needs attention and Settings › Account only", () => {
  it("neither top bar carries the chip any more", () => {
    expect(topBarSource).not.toMatch(/text: "Verify your email"/);
    expect(appSource).not.toMatch(/email_verified === false/);
    expect(appSource).not.toMatch(/>Verify your email</);
  });

  it("the Needs attention row has a Resend button, not a link away", () => {
    const html = renderToStaticMarkup(<MemoryRouter><NeedsAttentionV2 loginBroker={null} login={idle} onHide={() => undefined} rows={[
      { id: "email", icon: "account", tone: "neutral", title: "Verify your email", detail: "Open the link we emailed you.", dismissible: true, action: { label: "Resend link", resend: true } },
    ]} /></MemoryRouter>);
    expect(html).toContain("Verify your email");
    expect(html).toMatch(/<button[^>]*>.*Resend link/s);
    expect(html).not.toMatch(/<a [^>]*href="\/settings\/account"/);
    expect(html).toContain('data-hide="email"');
  });

  it("both places send the link the same way and say what happened in plain words", async () => {
    expect(resendText({ sent: true, already_verified: false })).toBe("We sent you a new link. Check your inbox.");
    expect(resendText({ sent: false, already_verified: false })).toBe("We could not send the email right now. Try again later.");
    expect(resendText({ sent: false, already_verified: true })).toBe("Your email is already verified.");
    vi.spyOn(api, "resendVerification").mockResolvedValueOnce({ sent: true, already_verified: false });
    await expect(resendVerification()).resolves.toBe("We sent you a new link. Check your inbox.");
    vi.spyOn(api, "resendVerification").mockRejectedValueOnce(new Error("boom"));
    const text = await resendVerification();
    expect(text).not.toMatch(/boom|Traceback/);
    expect(accountSource).toContain("resendVerification().then(setVerifyMessage)");
    expect(accountSource).not.toMatch(/LIVE trading and broker credentials may require it/);
  });
});
