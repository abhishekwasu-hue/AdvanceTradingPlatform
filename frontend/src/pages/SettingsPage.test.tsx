import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** M63 I-7: smoke test for the Settings page (first screen, before any answer). */

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

import SettingsPage from "./SettingsPage";
import { expectNoDeveloperText, renderPage, textOf } from "./pageSmoke.fixture";

const TRADER = { id: 1, email: "trader@example.com", tenant_id: 1, role: "OWNER", scopes: [] };

describe("Settings page", () => {
  beforeEach(() => {
    auth.user = TRADER; auth.loading = false;
    // The web-push section reads the browser's display mode while it renders (as in ui/dots.test.tsx).
    vi.stubGlobal("window", { matchMedia: () => ({ matches: false }) });
  });
  afterEach(() => { vi.unstubAllGlobals(); });

  it("shows its heading on the first screen", () => {
    expect(textOf(renderPage(<SettingsPage />, "/settings"))).toContain("Settings");
  });

  it("renders with no data yet and shows no developer text", () => {
    const html = renderPage(<SettingsPage />, "/settings");
    expect(textOf(html)).toContain("Prepare for LIVE");
    expectNoDeveloperText(html);
  });

  it("asks a signed-out visitor to log in, in words", () => {
    auth.user = null;
    const html = renderPage(<SettingsPage />, "/settings");
    expect(textOf(html)).toContain("Log in from the Account tab to manage your settings.");
    expectNoDeveloperText(html);
  });
});
