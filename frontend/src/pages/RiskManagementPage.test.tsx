import { beforeEach, describe, expect, it, vi } from "vitest";

/** M63 I-7: smoke test for the Risk Management page (first screen, before any answer). */

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

import RiskManagementPage from "./RiskManagementPage";
import source from "./RiskManagementPage.tsx?raw";
import { expectNoDeveloperText, expectPlainErrorSentences, renderPage, routesErrorsThroughSentence, textOf } from "./pageSmoke.fixture";

const TRADER = { id: 1, email: "trader@example.com", tenant_id: 1, role: "OWNER", scopes: [] };

describe("Risk Management page", () => {
  beforeEach(() => { auth.user = TRADER; auth.loading = false; });

  it("shows a loading placeholder (not a blank or broken screen) while the settings are read", () => {
    const html = renderPage(<RiskManagementPage />);
    expect(html).toMatch(/Loading/);
    expectNoDeveloperText(html);
  });

  it("shows its heading and asks a signed-out visitor to log in, in words", () => {
    auth.user = null;
    const html = renderPage(<RiskManagementPage />);
    expect(textOf(html)).toContain("Risk Management");
    expect(textOf(html)).toContain("Log in from the Account tab");
    expectNoDeveloperText(html);
  });

  it("says a failed call in a plain sentence", () => {
    expectPlainErrorSentences();
    expect(routesErrorsThroughSentence(source)).toBe(true);
  });
});
