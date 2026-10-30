import { describe, expect, it, vi } from "vitest";

/** M55 C11: smoke test for the Fundamentals page. */

vi.mock("../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api/client")>();
  const pending = () => new Promise(() => {});                       // no answer yet: the page shows its first screen
  return { ...real, api: new Proxy({}, { get: () => pending }) };
});
vi.mock("../auth/AuthContext", () => {
  const value = () => ({ user: { id: 1, email: "trader@example.com", tenant_id: 1, role: "OWNER", scopes: [] }, loading: false });
  return { useAuth: value, useOptionalAuth: value };
});

import FundamentalsPage from "./FundamentalsPage";
import source from "./FundamentalsPage.tsx?raw";
import { expectNoDeveloperText, expectPlainErrorSentences, renderPage, routesErrorsThroughSentence } from "./pageSmoke.fixture";

describe("Fundamentals page", () => {
  it("shows its heading on the first screen", () => {
    expect(renderPage(<FundamentalsPage />)).toContain("Company research");
  });

  it("shows no developer text", () => {
    expectNoDeveloperText(renderPage(<FundamentalsPage />));
  });

  it("says a failed call in a plain sentence", () => {
    expectPlainErrorSentences();
    expect(routesErrorsThroughSentence(source)).toBe(true);
  });
});
