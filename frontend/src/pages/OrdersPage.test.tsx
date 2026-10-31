import { beforeEach, describe, expect, it, vi } from "vitest";

/** M63 I-7: smoke test for the Orders page (first screen, before any answer). */

const auth = vi.hoisted(() => ({ user: null as unknown, loading: false }));
vi.mock("../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api/client")>();
  const pending = () => new Promise(() => {});                       // no answer yet: the page shows its first screen
  return { ...real, api: new Proxy({}, { get: () => pending }) };
});
vi.mock("../auth/AuthContext", () => {
  const value = () => auth;
  return { useAuth: value, useOptionalAuth: value };
});

import OrdersPage from "./OrdersPage";
import source from "./OrdersPage.tsx?raw";
import { expectNoDeveloperText, expectPlainErrorSentences, renderPage, routesErrorsThroughSentence, textOf } from "./pageSmoke.fixture";

const TRADER = { id: 1, email: "trader@example.com", tenant_id: 1, role: "OWNER", scopes: [] };

describe("Orders page", () => {
  beforeEach(() => { auth.user = TRADER; auth.loading = false; });

  it("shows its heading on the first screen", () => {
    expect(textOf(renderPage(<OrdersPage />))).toContain("Orders");
  });

  it("before the list answers: placeholder rows under the heading, never \"No orders yet\" or a 0 count", () => {
    const html = renderPage(<OrdersPage />);
    expect(html).toContain('aria-busy="true" aria-label="Loading your orders"');
    expect(textOf(html)).not.toContain("No orders yet");
    expect(textOf(html)).not.toContain("(0)");
    expectNoDeveloperText(html);
  });

  it("asks a signed-out visitor to log in, in words", () => {
    auth.user = null;
    const html = renderPage(<OrdersPage />);
    expect(textOf(html)).toContain("Log in from the Account tab to see your order blotter.");
    expectNoDeveloperText(html);
  });

  it("says a failed call in a plain sentence", () => {
    expectPlainErrorSentences();
    expect(routesErrorsThroughSentence(source)).toBe(true);
  });
});
