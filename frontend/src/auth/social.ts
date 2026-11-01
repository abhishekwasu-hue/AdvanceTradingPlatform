/**
 * M73 SL-1 / SL-2: sign-in providers (Google, Facebook; the list is the server's, so a later provider - M74 "Continue
 * with your broker" - needs no change here). A button exists only for a provider the server lists at /auth/providers
 * (its credentials are set); nothing in the Android app (Google and Facebook refuse sign-in inside an embedded web
 * view; the app signs in with the password, ADR-0028).
 *
 * The server sends the browser back with one of (app/auth/social_routes.py):
 *   ?social=ok | new                        signed in (the refresh cookie is set; the reload refresh picks it up)
 *   ?social=mfa      #social_mfa=<token>     the provider confirmed the account; the authenticator code is still needed
 *   ?social=link     #social_pending=<t>     an account uses this e-mail: confirm with its password
 *   ?social=complete #social_pending=<t>     a new account: terms / beta code / an e-mail still to give
 *   ?social=linked                          Settings › Account: linked
 *   ?social_error=<code>                    refused - said in plain words below
 * each with &provider=<id>. GS-1's ?google=... / ?google_error=... / #google_mfa= are still read.
 * `readSocialReturn` reads it once and cleans the address bar at once (no token is left in the history).
 */
import { apiUrl } from "../api/base";

export interface SignInProvider { id: string; label: string; kind: string; start: string }
export type SocialIntent = "signin" | "signup" | "invite";

const LABELS: Record<string, string> = { google: "Google", facebook: "Facebook" };

/** Pure: the buttons to show, from the /auth/providers answer (anything unexpected shows none). */
export function providersFrom(answer: unknown, native: boolean): SignInProvider[] {
  if (native || !answer || typeof answer !== "object") return [];
  const list = (answer as { providers?: unknown }).providers;
  if (!Array.isArray(list)) {
    // A server from before M73 answers only { google: true }.
    return (answer as { google?: unknown }).google === true
      ? [{ id: "google", label: "Google", kind: "oidc", start: "/auth/google/start" }] : [];
  }
  return list.filter((p): p is SignInProvider => !!p && typeof p === "object"
    && typeof (p as SignInProvider).id === "string" && typeof (p as SignInProvider).label === "string"
    && typeof (p as SignInProvider).start === "string" && (p as SignInProvider).start.startsWith("/auth/"));
}

/** Pure: whether the platform-wide `ui_v2` layout is on for the signed-out page. */
export function signInV2From(answer: unknown): boolean {
  return !!answer && typeof answer === "object" && (answer as { ui_v2?: unknown }).ui_v2 === true;
}

/** The address a button goes to. `invite` only with the "invite" intent (Join team). */
export function socialStartUrl(provider: SignInProvider, intent: SocialIntent = "signin", invite?: string | null): string {
  const q = new URLSearchParams({ intent });
  if (intent === "invite" && invite) q.set("invite", invite);
  return `${apiUrl(provider.start)}?${q.toString()}`;
}

const MESSAGES: Record<string, (p: string) => string> = {
  cancelled: (p) => `${p} sign-in was cancelled. You can try again, or use your email.`,
  expired: (p) => `The ${p} sign-in took too long or was started in another window. Please try again.`,
  failed: (p) => `${p} sign-in did not work this time. Please try again, or use your email.`,
  unverified: (p) => `${p} has not verified this email address, so it cannot be used to sign in.`,
  no_account: (p) => `No account uses this ${p} email. Ask your organisation owner for an invite.`,
  inactive: () => "This account is switched off. Ask your organisation owner.",
  locked: () => "Too many sign-in attempts from this network. Please wait a few minutes and try again.",
  wait: () => "Too many sign-in attempts for this account. Please wait a minute and try again.",
  signup_form: () => "Create your account with the sign-up form first. After that, this sign-in works.",
  invite_invalid: () => "This invitation is no longer valid. Ask the owner for a new one.",
  email_taken: () => "An account already uses the invited email. Sign in instead.",
  invite_email_mismatch: (p) => `This invitation is for another email. Continue with the ${p} account of the invited email, or choose a password below.`,
  linked_elsewhere: (p) => `This ${p} account is already linked to another account.`,
  already_linked: (p) => `Your account is already linked to another ${p} account. Unlink it first.`,
};

export function providerLabel(id: string | null | undefined): string {
  return (id && LABELS[id]) || "This";
}

/** The plain-words message for a refusal code (an unknown code reads as a general failure). */
export function socialErrorMessage(code: string, provider?: string | null): string {
  return (MESSAGES[code] ?? MESSAGES.failed)(providerLabel(provider));
}

export type SocialReturn =
  | { kind: "signed_in"; isNew: boolean; provider: string }
  | { kind: "mfa"; mfaToken: string; provider: string }
  | { kind: "link"; token: string; provider: string }
  | { kind: "complete"; token: string; provider: string }
  | { kind: "linked"; provider: string }
  | { kind: "error"; message: string; provider: string };

/** Pure: what the address says (search like "?social=ok&provider=google", hash like "#social_mfa=..."), or null. */
export function parseSocialReturn(search: string, hash: string): SocialReturn | null {
  const q = new URLSearchParams(search);
  const h = new URLSearchParams(hash.replace(/^#/, ""));
  const legacy = q.has("google") || q.has("google_error");
  const provider = legacy ? "google" : (q.get("provider") ?? "");
  const error = q.get("social_error") ?? q.get("google_error");
  if (error) return { kind: "error", message: socialErrorMessage(error, provider), provider };
  const outcome = q.get("social") ?? q.get("google");
  if (!outcome) return null;
  if (outcome === "ok" || outcome === "new") return { kind: "signed_in", isNew: outcome === "new", provider };
  if (outcome === "linked") return { kind: "linked", provider };
  const expired = { kind: "error", message: socialErrorMessage("expired", provider), provider } as const;
  if (outcome === "mfa") {
    const token = h.get("social_mfa") ?? h.get("google_mfa");
    return token ? { kind: "mfa", mfaToken: token, provider } : expired;
  }
  if (outcome === "link" || outcome === "complete") {
    const token = h.get("social_pending");
    return token ? { kind: outcome, token, provider } : expired;
  }
  return null;
}

const OWN_PARAMS = ["social", "social_error", "provider", "google", "google_error"];
const OWN_HASH = /(social_mfa|social_pending|google_mfa)=/;

/** Pure: the search string without the return parameters ("" when nothing else is left). */
export function stripSocialParams(search: string): string {
  const q = new URLSearchParams(search);
  if (!q.has("social") && !q.has("social_error") && !q.has("google") && !q.has("google_error")) return search;
  for (const k of OWN_PARAMS) q.delete(k);
  const rest = q.toString();
  return rest ? `?${rest}` : "";
}

let pending: SocialReturn | null | undefined;

/** Reads the return once per page load and cleans the address bar (the fragment and the parameters). */
export function readSocialReturn(): SocialReturn | null {
  if (pending !== undefined) return pending;
  pending = null;
  try {
    pending = parseSocialReturn(window.location.search, window.location.hash);
    if (pending) {
      const hash = OWN_HASH.test(window.location.hash) ? "" : window.location.hash;
      window.history.replaceState(window.history.state, "", window.location.pathname + stripSocialParams(window.location.search) + hash);
    }
  } catch {
    // intentional: no URL API - nothing to read
  }
  return pending;
}

/** For the page that acts on it (the sign-in screen, Settings › Account): handed over once. `kinds` limits which. */
export function takeSocialReturn(kinds?: SocialReturn["kind"][]): SocialReturn | null {
  const value = readSocialReturn();
  if (!value || (kinds && !kinds.includes(value.kind))) return null;
  pending = null;
  return value;
}

/** Test hook: forget what was read. */
export function resetSocialReturn(): void {
  pending = undefined;
}
