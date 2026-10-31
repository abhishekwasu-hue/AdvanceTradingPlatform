import { beforeEach, describe, expect, it, vi } from "vitest";

/** M63 I-7: smoke test for the Signals page (first screen, before any answer). */

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

import SignalsPage from "./SignalsPage";
import source from "./SignalsPage.tsx?raw";
import { expectNoDeveloperText, expectPlainErrorSentences, renderPage, routesErrorsThroughSentence, textOf } from "./pageSmoke.fixture";

const TRADER = { id: 1, email: "trader@example.com", tenant_id: 1, role: "OWNER", scopes: [] };

describe("Signals page", () => {
  beforeEach(() => { auth.user = TRADER; auth.loading = false; });

  it("shows its heading on the first screen", () => {
    expect(textOf(renderPage(<SignalsPage />))).toContain("Signals");
  });

  it("renders with no signal yet: says what to do first, no developer text", () => {
    const html = renderPage(<SignalsPage />);
    expect(textOf(html)).toContain("Generate a signal first");
    expectNoDeveloperText(html);
  });

  it("carries the signals-not-advice disclaimer", () => {
    expect(textOf(renderPage(<SignalsPage />))).toMatch(/not investment advice/);
  });

  it("says a failed call in a plain sentence", () => {
    expectPlainErrorSentences();
    expect(routesErrorsThroughSentence(source)).toBe(true);
  });
});
