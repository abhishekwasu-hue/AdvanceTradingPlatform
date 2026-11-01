import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import screenSource from "./SignInScreen.tsx?raw";
import { OrUseEmail, ProviderButton, SignInOptionsList } from "./SocialButtons";
import { parseSocialReturn, providersFrom, signInV2From, socialErrorMessage, socialStartUrl, stripSocialParams, type SignInProvider } from "./social";
import { linkTokenFrom } from "./SignInScreen";
import { ApiError } from "../api/errors";
import { canUnlink, validIdentities } from "../settings/SignInMethodsCardV2";

const GOOGLE: SignInProvider = { id: "google", label: "Google", kind: "oidc", start: "/auth/google/start" };
const FACEBOOK: SignInProvider = { id: "facebook", label: "Facebook", kind: "oauth2", start: "/auth/facebook/start" };

describe("M73 SL-1: which buttons", () => {
  it("a provider not configured on the server has no button", () => {
    expect(providersFrom({ google: false, facebook: false, providers: [], ui_v2: true }, false)).toEqual([]);
    expect(renderToStaticMarkup(<SignInOptionsList providers={[]} />)).toBe("");
    expect(providersFrom({ google: false, facebook: true, providers: [FACEBOOK] }, false)).toEqual([FACEBOOK]);
  });

  it("nothing unexpected shows a button, and none in the app", () => {
    expect(providersFrom(null, false)).toEqual([]);
    expect(providersFrom({ providers: [{ id: "x" }, { id: "y", label: "Y", start: "https://evil.example.com" }] }, false)).toEqual([]);
    expect(providersFrom({ providers: [GOOGLE, FACEBOOK] }, true)).toEqual([]);
    expect(providersFrom({ google: true }, false)).toEqual([GOOGLE]);        // a server from before M73
  });

  it("reads the signed-out ui_v2 switch, off unless the server says true", () => {
    expect(signInV2From({ ui_v2: true })).toBe(true);
    expect(signInV2From({ ui_v2: "true" })).toBe(false);
    expect(signInV2From(undefined)).toBe(false);
  });

  it("brand buttons with the exact labels, 48 px tall, the official marks, then 'or use email'", () => {
    const html = renderToStaticMarkup(<SignInOptionsList providers={[GOOGLE, FACEBOOK]} intent="signup" />);
    expect(html).toContain("Continue with Google");
    expect(html).toContain("Continue with Facebook");
    expect(html.indexOf("Continue with Google")).toBeLessThan(html.indexOf("Continue with Facebook"));
    expect(html).toContain('href="/api/v1/auth/google/start?intent=signup"');
    expect(html).toContain('href="/api/v1/auth/facebook/start?intent=signup"');
    expect(html.match(/h-12/g)?.length).toBe(2);
    expect(html).toContain("rgb(var(--social-google-blue))");
    expect(html).toContain("bg-social-facebook");
    expect(html).not.toMatch(/#[0-9a-fA-F]{6}/);                               // colours only from the tokens
    expect(renderToStaticMarkup(<OrUseEmail />)).toContain("or use email");
  });

  it("Join team carries the invite; other intents never do", () => {
    expect(socialStartUrl(GOOGLE, "invite", "tok-1")).toBe("/api/v1/auth/google/start?intent=invite&invite=tok-1");
    expect(socialStartUrl(GOOGLE, "signin", "tok-1")).toBe("/api/v1/auth/google/start?intent=signin");
    const html = renderToStaticMarkup(<ProviderButton provider={FACEBOOK} href={socialStartUrl(FACEBOOK, "invite", "t")} />);
    expect(html).toContain('data-provider="facebook"');
  });

  it("the list takes extra entries (M74: Continue with your broker)", () => {
    const html = renderToStaticMarkup(<SignInOptionsList providers={[GOOGLE]} extra={[{ key: "broker", node: <span>Continue with your broker</span> }]} />);
    expect(html.indexOf("Continue with Google")).toBeLessThan(html.indexOf("Continue with your broker"));
  });

  it("the sign-in page puts the list on top of Sign in, Create account and Join team under ui_v2", () => {
    expect(screenSource).toContain('const showProviders = v2 && providers.length > 0 && (mode === "signin" || mode === "register" || (mode === "invite" && !!invite?.valid));');
    expect(screenSource.indexOf("<SignInOptionsList")).toBeLessThan(screenSource.indexOf("<form onSubmit"));
    expect(screenSource).toContain("Forgot password?");
  });
});

describe("M73: the return from a provider", () => {
  it("reads every outcome and GS-1's old parameters", () => {
    expect(parseSocialReturn("?social=ok&provider=facebook", "")).toEqual({ kind: "signed_in", isNew: false, provider: "facebook" });
    expect(parseSocialReturn("?social=new&provider=google", "")).toEqual({ kind: "signed_in", isNew: true, provider: "google" });
    expect(parseSocialReturn("?social=mfa&provider=google", "#social_mfa=a.b")).toEqual({ kind: "mfa", mfaToken: "a.b", provider: "google" });
    expect(parseSocialReturn("?social=link&provider=google", "#social_pending=p.q")).toEqual({ kind: "link", token: "p.q", provider: "google" });
    expect(parseSocialReturn("?social=complete&provider=facebook", "#social_pending=p.q")).toEqual({ kind: "complete", token: "p.q", provider: "facebook" });
    expect(parseSocialReturn("?social=linked&provider=google", "")).toEqual({ kind: "linked", provider: "google" });
    expect(parseSocialReturn("?google=mfa", "#google_mfa=x.y")).toEqual({ kind: "mfa", mfaToken: "x.y", provider: "google" });
    expect(parseSocialReturn("?social=link&provider=google", "")?.kind).toBe("error");          // no token: expired
    expect(parseSocialReturn("?invite=x", "")).toBeNull();
  });

  it("says each refusal in plain words with the provider's name", () => {
    expect(parseSocialReturn("?social_error=no_account&provider=facebook", "")).toEqual({
      kind: "error", provider: "facebook", message: "No account uses this Facebook email. Ask your organisation owner for an invite.",
    });
    for (const code of ["cancelled", "expired", "failed", "unverified", "no_account", "inactive", "locked", "wait",
      "invite_invalid", "email_taken", "invite_email_mismatch", "linked_elsewhere", "already_linked"]) {
      expect(socialErrorMessage(code, "google")).toMatch(/^[A-Z][^_]+\.$/);
    }
    expect(socialErrorMessage("brand_new_code", "google")).toBe(socialErrorMessage("failed", "google"));
  });

  it("removes only its own parameters", () => {
    expect(stripSocialParams("?social=ok&provider=google")).toBe("");
    expect(stripSocialParams("?social_error=failed&provider=google&tab=x")).toBe("?tab=x");
    expect(stripSocialParams("?provider=x")).toBe("?provider=x");             // not a return: untouched
  });

  it("an e-mail that is an account's turns the finish step into the link step", () => {
    const body = JSON.stringify({ detail: { code: "link_required", token: "l.t", message: "x" } });
    expect(linkTokenFrom(new ApiError(409, "x", "", body))).toBe("l.t");
    expect(linkTokenFrom(new ApiError(409, "x", "", '{"detail":"Email already registered"}'))).toBeNull();
    expect(linkTokenFrom(new Error("x"))).toBeNull();
  });
});

describe("M73 SL-2: Settings › Account", () => {
  it("an unexpected answer (an older server, an error page) shows no card", () => {
    expect(validIdentities({ detail: "Not found" })).toBe(false);
    expect(validIdentities(null)).toBe(false);
    expect(validIdentities({ has_password: true, identities: [], available: [] })).toBe(true);
  });

  it("the last way to sign in cannot be unlinked", () => {
    const one = { provider: "google", label: "Google", email: null, linked_at: null };
    expect(canUnlink({ has_password: false, identities: [one], available: [] })).toBe(false);
    expect(canUnlink({ has_password: true, identities: [one], available: [] })).toBe(true);
    expect(canUnlink({ has_password: false, identities: [one, { ...one, provider: "facebook" }], available: [] })).toBe(true);
  });
});
