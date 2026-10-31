import { beforeEach, describe, expect, it, vi } from "vitest";

/** M63 I-7: smoke test for the AI Copilot page (first screen, before any answer). */

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

import AiCopilotPage from "./AiCopilotPage";
import { expectNoDeveloperText, renderPage, textOf } from "./pageSmoke.fixture";

const TRADER = { id: 1, email: "trader@example.com", tenant_id: 1, role: "OWNER", scopes: [] };

describe("AI Copilot page", () => {
  beforeEach(() => { auth.user = TRADER; auth.loading = false; });

  it("shows its heading on the first screen", () => {
    expect(textOf(renderPage(<AiCopilotPage />, "/copilot"))).toContain("Your trading copilot");
  });

  it("renders with no data yet and shows no developer text", () => {
    expectNoDeveloperText(renderPage(<AiCopilotPage />, "/copilot"));
  });

  it("says it is not investment advice", () => {
    expect(textOf(renderPage(<AiCopilotPage />, "/copilot"))).toMatch(/not investment advice/);
  });

  it("asks a signed-out visitor to sign in, in words", () => {
    auth.user = null;
    const html = renderPage(<AiCopilotPage />, "/copilot");
    expect(textOf(html)).toContain("Sign in from the Account page to use the AI Copilot.");
    expectNoDeveloperText(html);
  });
});
