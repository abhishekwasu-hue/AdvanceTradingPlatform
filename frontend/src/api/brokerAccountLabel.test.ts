import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./client";

/** Every per-account broker action names the account it acts on, so a second account at the same broker is never
 * mistaken for the primary one. */
describe("broker login calls carry the account label", () => {
  afterEach(() => vi.unstubAllGlobals());

  function stubFetch(body: unknown) {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    return () => (fetchMock.mock.calls as unknown as [string, RequestInit][]).map(([url, init]) => [String(url), init?.method ?? "GET"]);
  }

  it("authenticate sends the row's label, and the primary by default", async () => {
    const calls = stubFetch({});
    await api.authenticateBroker("upstox", "second");
    await api.authenticateBroker("upstox");
    expect(calls()).toEqual([
      [expect.stringMatching(/\/broker\/upstox\/authenticate\?account_label=second$/), "POST"],
      [expect.stringMatching(/\/broker\/upstox\/authenticate\?account_label=primary$/), "POST"],
    ]);
  });

  it("the Upstox OAuth start sends the label", async () => {
    const calls = stubFetch({ authorization_url: "https://example.com" });
    await api.upstoxOAuthStart("second");
    expect(calls()[0][0]).toMatch(/\/broker\/upstox\/oauth\/start\?account_label=second$/);
  });

  it("the login page and the pasted code send the label", async () => {
    const calls = stubFetch({});
    await api.brokerLoginUrl("zerodha", "second");
    await api.brokerLoginCode("zerodha", "abc", "second");
    expect(calls().map(([url]) => url)).toEqual([
      expect.stringMatching(/\/broker\/zerodha\/login-url\?account_label=second$/),
      expect.stringMatching(/\/broker\/zerodha\/login-code\?account_label=second$/),
    ]);
  });
});
