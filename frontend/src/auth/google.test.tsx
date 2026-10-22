import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import accountSource from "./SignInScreen.tsx?raw";
import { GoogleButton } from "./GoogleSignInButton";
import { googleEnabled, googleErrorMessage, googleStartUrl, parseGoogleReturn, stripGoogleParams } from "./google";

describe("Google sign-in button (GS-1)", () => {
  it("renders nothing while the server says google: false", () => {
    expect(googleEnabled({ google: false }, false)).toBe(false);
    expect(renderToStaticMarkup(<GoogleButton show={googleEnabled({ google: false }, false)} />)).toBe("");
  });

  it("renders the button when the server says google: true", () => {
    const html = renderToStaticMarkup(<GoogleButton show={googleEnabled({ google: true }, false)} />);
    expect(html).toContain("Continue with Google");
    expect(html).toContain(`href="${googleStartUrl()}"`);
    expect(googleStartUrl()).toBe("/api/v1/auth/google/start");
  });

  it("stays hidden for anything unexpected, and inside the app", () => {
    expect(googleEnabled(null, false)).toBe(false);
    expect(googleEnabled({}, false)).toBe(false);
    expect(googleEnabled({ google: "true" }, false)).toBe(false);
    expect(googleEnabled({ google: true }, true)).toBe(false);
  });

  it("shows on the login form only", () => {
    expect(accountSource).toContain('{mode === "signin" && <GoogleSignInButton />}');      // UX1: the sign-in screen
  });
});

describe("the return from Google", () => {
  it("reads a sign-in, the authenticator step and refusals", () => {
    expect(parseGoogleReturn("?google=ok", "")).toEqual({ kind: "signed_in", isNew: false });
    expect(parseGoogleReturn("?google=new", "")).toEqual({ kind: "signed_in", isNew: true });
    expect(parseGoogleReturn("?google=mfa", "#google_mfa=abc.def")).toEqual({ kind: "mfa", mfaToken: "abc.def" });
    expect(parseGoogleReturn("?google_error=no_account", "")).toEqual({
      kind: "error", message: "No account uses this Google e-mail. Ask your organisation owner for an invite.",
    });
    expect(parseGoogleReturn("", "")).toBeNull();
    expect(parseGoogleReturn("?invite=x", "")).toBeNull();
  });

  it("an authenticator step without its token reads as expired", () => {
    expect(parseGoogleReturn("?google=mfa", "")).toEqual({ kind: "error", message: googleErrorMessage("expired") });
  });

  it("says every refusal in plain words, an unknown code as a general failure", () => {
    for (const code of ["cancelled", "expired", "failed", "unverified", "no_account", "inactive", "locked", "wait", "signup_form"]) {
      expect(googleErrorMessage(code)).toMatch(/^[A-Z][^_]+\.$/);          // a sentence, no internal code
    }
    expect(googleErrorMessage("something_new")).toBe(googleErrorMessage("failed"));
  });

  it("removes only its own parameters from the address", () => {
    expect(stripGoogleParams("?google=ok")).toBe("");
    expect(stripGoogleParams("?google_error=failed&tab=x")).toBe("?tab=x");
  });
});
