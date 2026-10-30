import { describe, expect, it, vi } from "vitest";

/** M55 C11: smoke test for the CoachGuide page. */

vi.mock("../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api/client")>();
  const pending = () => new Promise(() => {});                       // no answer yet: the page shows its first screen
  return { ...real, api: new Proxy({}, { get: () => pending }) };
});
vi.mock("../auth/AuthContext", () => {
  const value = () => ({ user: { id: 1, email: "trader@example.com", tenant_id: 1, role: "OWNER", scopes: [] }, loading: false });
  return { useAuth: value, useOptionalAuth: value };
});

import CoachGuidePage from "./CoachGuidePage";
import coachSource from "../components/TradeCoach.tsx?raw";
import guideSource from "../components/GuideChat.tsx?raw";
import { ApiError, NETWORK_MESSAGE } from "../api/errors";
import source from "./CoachGuidePage.tsx?raw";
import { expectNoDeveloperText, expectPlainErrorSentences, renderPage } from "./pageSmoke.fixture";

describe("CoachGuide page", () => {
  it("shows its heading on the first screen", () => {
    expect(renderPage(<CoachGuidePage />)).toContain("Coach &amp; Guide");
  });

  it("shows no developer text", () => {
    expectNoDeveloperText(renderPage(<CoachGuidePage />));
  });

  it("says a failed call in a plain sentence", () => {
    expectPlainErrorSentences();
    // the page has no calls of its own: the coach and the guide show the client's sentence, never a stack
    expect(String(new ApiError(0, NETWORK_MESSAGE))).toBe(NETWORK_MESSAGE);
    for (const s of [coachSource, guideSource]) expect(s).toMatch(/String\(e\)\.replace\(\/\^Error:/);
    expect(source).not.toMatch(/e\.stack|JSON\.stringify\(e/);
  });
});
