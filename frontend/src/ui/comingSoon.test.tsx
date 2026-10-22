import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({ role: "OWNER" as string }));
vi.mock("../auth/AuthContext", () => ({ useOptionalAuth: () => ({ user: { role: auth.role } }) }));

import { closedFlag, PAGE_FLAGS } from "../shell/pageFlags";
import { landingAfterSignIn } from "../auth/landing";
import { COMING_SOON_MESSAGE, NOT_AVAILABLE_MESSAGE, errorSentence } from "../api/errors";
import { ComingSoon, ComingSoonNote } from "./ComingSoon";
import { ErrorState } from "./ErrorState";

const render = (node: React.ReactNode) => renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>);

describe("UX1 Coming soon", () => {
  it("a trader sees 'Coming soon' and never the flag name", () => {
    auth.role = "OWNER";
    const html = render(<ComingSoon flag="screener_v2" />);
    expect(html).toContain("Coming soon");
    expect(html).toContain('href="/"');
    expect(html).not.toContain("screener_v2");
    expect(render(<ComingSoonNote what="Saving plans" flag="options_builder_saved" />)).not.toContain("options_builder_saved");
  });

  it("a platform administrator also sees which switch it is", () => {
    auth.role = "SUPER_ADMIN";
    expect(render(<ComingSoon flag="screener_v2" />)).toContain("Admin: screener_v2");
    auth.role = "OWNER";
  });

  it("closes a page only on a real answer that its flag is off", () => {
    const off = { answered: true, on: () => false };
    expect(closedFlag("screener", off)).toBe("screener_v2");
    expect(closedFlag("journal", off)).toBe("discipline_engine");
    expect(closedFlag("dashboard", off)).toBeNull();                         // not behind a flag
    expect(closedFlag("screener", { answered: false, on: () => false })).toBeNull();
    expect(closedFlag("screener", { answered: true, on: () => true })).toBeNull();
    expect(closedFlag(null, off)).toBeNull();
    expect(PAGE_FLAGS.marketplace).toBe("marketplace");
  });
});

describe("UX1 landing after sign-in", () => {
  it("the account address moves on to Today; any other address stays", () => {
    expect(landingAfterSignIn("/account")).toBe("/");
    expect(landingAfterSignIn("/settings/account/")).toBe("/");
    expect(landingAfterSignIn("/charts")).toBeNull();
    expect(landingAfterSignIn("/")).toBeNull();
    expect(landingAfterSignIn("//evil.example.com")).toBeNull();
  });
});

describe("UX1 error state", () => {
  it("a bare 'Not found' or a flag sentence never reaches the trader", () => {
    expect(errorSentence("Not found")).toBe(NOT_AVAILABLE_MESSAGE);
    expect(errorSentence(new Error("Not found. It may have been removed."))).toBe(NOT_AVAILABLE_MESSAGE);
    expect(errorSentence("The 'vision_byok' feature is currently disabled by the platform operator")).toBe(COMING_SOON_MESSAGE);
    expect(errorSentence("Connect a broker first.")).toBe("Connect a broker first.");
    const html = renderToStaticMarkup(<ErrorState message="Not found" onRetry={() => {}} />);
    expect(html).toContain(NOT_AVAILABLE_MESSAGE);
    expect(html).toContain("Try again");
    expect(html).not.toMatch(/>Not found</);
  });
});
