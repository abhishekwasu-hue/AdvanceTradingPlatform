import { afterEach, describe, expect, it, vi } from "vitest";
import { request } from "./client";

/** Handover 8d: a wrong email or password at sign-in shows the server's sentence, not "Sign in from the Account page"
 * (that sentence is for an anonymous visitor on a page that needs a sign-in). */
describe("the sign-in error message", () => {
  afterEach(() => vi.unstubAllGlobals());

  function stub401(detail: string) {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail }), { status: 401, headers: { "content-type": "application/json" } })));
  }

  it("a wrong email or password at login keeps the server's message", async () => {
    stub401("Wrong email or password.");
    await expect(request("/auth/login", { method: "POST", body: "{}" })).rejects.toMatchObject({ status: 401, message: "Wrong email or password." });
  });

  it("the MFA step of sign-in keeps its own message too", async () => {
    stub401("That code is not right");
    await expect(request("/auth/mfa/login", { method: "POST", body: "{}" })).rejects.toMatchObject({ message: "That code is not right" });
  });

  it("an anonymous visitor on a page that needs a sign-in still gets the plain sentence", async () => {
    stub401("Not authenticated");
    await expect(request("/backtest/run", { method: "POST", body: "{}" })).rejects.toMatchObject({
      status: 401, message: "Sign in from the Account page to use this feature.",
    });
  });
});
