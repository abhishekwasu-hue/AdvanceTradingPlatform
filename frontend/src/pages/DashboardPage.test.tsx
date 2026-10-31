import { beforeEach, describe, expect, it, vi } from "vitest";

/** M63 I-7: smoke test for the Dashboard page (first screen, before any answer). */

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

import DashboardPage from "./DashboardPage";
import source from "./DashboardPage.tsx?raw";
import { expectNoDeveloperText, expectPlainErrorSentences, renderPage, routesErrorsThroughSentence, textOf } from "./pageSmoke.fixture";

const TRADER = { id: 1, email: "trader@example.com", tenant_id: 1, role: "OWNER", scopes: [] };

describe("Dashboard page", () => {
  beforeEach(() => { auth.user = TRADER; auth.loading = false; });

  it("shows its greeting heading on the first screen", () => {
    expect(textOf(renderPage(<DashboardPage />))).toMatch(/Good (morning|afternoon|evening), trader/);
  });

  it("renders with empty data and shows no developer text", () => {
    const html = renderPage(<DashboardPage />);
    expect(textOf(html)).toContain("Nothing deployed yet.");
    expectNoDeveloperText(html);
  });

  it("asks a signed-out visitor to log in, in words", () => {
    auth.user = null;
    const html = renderPage(<DashboardPage />);
    expect(textOf(html)).toContain("Log in to see your book");
    expectNoDeveloperText(html);
  });

  it("says a failed call in a plain sentence", () => {
    expectPlainErrorSentences();
    expect(routesErrorsThroughSentence(source)).toBe(true);
  });
});
