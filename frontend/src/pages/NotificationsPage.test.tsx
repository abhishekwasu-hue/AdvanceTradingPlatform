import { describe, expect, it, vi } from "vitest";

/** M55 C11: smoke test for the Notifications page. */

vi.mock("../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api/client")>();
  const pending = () => new Promise(() => {});                       // no answer yet: the page shows its first screen
  return { ...real, api: new Proxy({}, { get: () => pending }) };
});
const who = vi.hoisted(() => ({ signedIn: true }));
vi.mock("../auth/AuthContext", () => {
  const value = () => ({ user: !who.signedIn ? null : { id: 1, email: "trader@example.com", tenant_id: 1, role: "OWNER", scopes: [] }, loading: false });
  return { useAuth: value, useOptionalAuth: value };
});

import NotificationsPage from "./NotificationsPage";
import source from "./NotificationsPage.tsx?raw";
import { textOf, expectNoDeveloperText, expectPlainErrorSentences, renderPage, routesErrorsThroughSentence } from "./pageSmoke.fixture";

describe("Notifications page", () => {
  it("shows its heading on the first screen", () => {
    expect(renderPage(<NotificationsPage />)).toContain("Notifications");
  });

  it("shows no developer text", () => {
    expectNoDeveloperText(renderPage(<NotificationsPage />));
  });

  it("says a failed call in a plain sentence", () => {
    expectPlainErrorSentences();
    expect(routesErrorsThroughSentence(source)).toBe(true);
  });

  it("asks a signed-out visitor to log in, in words", () => {
    who.signedIn = false;
    try {
      const html = renderPage(<NotificationsPage />);
      expect(html).toContain("Notifications");
      expect(textOf(html)).toMatch(/Log in/);
      expectNoDeveloperText(html);
    } finally { who.signedIn = true; }
  });
});
