import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

vi.mock("./AuthContext", () => ({
  useAuth: () => ({ login: vi.fn(), completeMfaLogin: vi.fn(), register: vi.fn(), acceptInvite: vi.fn(), resetPassword: vi.fn() }),
}));

import SignInScreen from "./SignInScreen";

const html = () => renderToStaticMarkup(<MemoryRouter initialEntries={["/charts"]}><SignInScreen /></MemoryRouter>);

describe("UX1 sign-in screen", () => {
  it("one card: email, password, Forgot password, Sign in - no sidebar, no data", () => {
    const out = html();
    expect(out).toContain('data-screen="sign-in"');
    expect(out).toContain(">Sign in</h1>");
    expect(out).toMatch(/<label[^>]*for="[^"]+"[^>]*>Email<\/label>/);
    expect(out).toMatch(/<label[^>]*for="[^"]+"[^>]*>Password<\/label>/);
    expect(out).toContain("Forgot password?");
    expect(out).toContain('aria-label="Show password"');
    expect(out).not.toMatch(/sidebar|Backend|Not found/i);
  });

  it("'Create an account' stays closed until the server says sign-up is open; Google stays hidden", () => {
    const out = html();
    expect(out).not.toContain("Create an account");
    expect(out).toContain("Ask your team owner for an invite link.");
    expect(out).not.toContain("Continue with Google");
  });
});
