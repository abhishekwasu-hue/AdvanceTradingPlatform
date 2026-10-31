/**
 * GS-1: Google sign-in on the web. The button exists only while the server says `google: true` at /auth/providers
 * (both credentials set on the server); otherwise nothing is rendered. Not in the Android app: Google refuses sign-in
 * inside an embedded web view, and the app signs in with the password (ADR-0028).
 *
 * The server sends the browser back to the app with one of:
 *   ?google=ok | ?google=new          signed in (the refresh cookie is set; the normal reload refresh picks it up)
 *   ?google=mfa#google_mfa=<token>    Google confirmed the e-mail; the authenticator code is still needed
 *   ?google_error=<code>              refused - said in plain words below
 * `readGoogleReturn` reads that once and removes it from the address bar at once (the challenge token is never left
 * in the history).
 */
import { apiUrl } from "../api/base";

export const GOOGLE_START_PATH = "/auth/google/start";

export function googleStartUrl(): string {
  return apiUrl(GOOGLE_START_PATH);
}

const MESSAGES: Record<string, string> = {
  cancelled: "Google sign-in was cancelled. You can try again, or log in with your password.",
  expired: "The Google sign-in took too long or was started in another window. Please try again.",
  failed: "Google sign-in did not work this time. Please try again, or log in with your password.",
  unverified: "Google has not verified this e-mail address, so it cannot be used to sign in.",
  no_account: "No account uses this Google e-mail. Ask your organisation owner for an invite.",
  inactive: "This account is switched off. Ask your organisation owner.",
  locked: "Too many sign-in attempts from this network. Please wait a few minutes and try again.",
  wait: "Too many sign-in attempts for this account. Please wait a minute and try again.",
  signup_form: "Create your account with the sign-up form first. After that, Continue with Google works.",
};

/** The plain-words message for a refusal code (an unknown code reads as a general failure). */
export function googleErrorMessage(code: string): string {
  return MESSAGES[code] ?? MESSAGES.failed;
}

/** Pure: whether the button shows, from the /auth/providers answer (anything unexpected hides it). */
export function googleEnabled(providers: unknown, native: boolean): boolean {
  if (native || !providers || typeof providers !== "object") return false;
  return (providers as { google?: unknown }).google === true;
}

export type GoogleReturn =
  | { kind: "signed_in"; isNew: boolean }
  | { kind: "mfa"; mfaToken: string }
  | { kind: "error"; message: string };

/** Pure: what the address says (search like "?google=ok", hash like "#google_mfa=..."), or null. */
export function parseGoogleReturn(search: string, hash: string): GoogleReturn | null {
  const q = new URLSearchParams(search);
  const error = q.get("google_error");
  if (error) return { kind: "error", message: googleErrorMessage(error) };
  const outcome = q.get("google");
  if (outcome === "ok" || outcome === "new") return { kind: "signed_in", isNew: outcome === "new" };
  if (outcome === "mfa") {
    const token = new URLSearchParams(hash.replace(/^#/, "")).get("google_mfa");
    return token ? { kind: "mfa", mfaToken: token } : { kind: "error", message: googleErrorMessage("expired") };
  }
  return null;
}

/** Pure: the search string without the Google return parameters ("" when nothing else is left). */
export function stripGoogleParams(search: string): string {
  const q = new URLSearchParams(search);
  q.delete("google");
  q.delete("google_error");
  const rest = q.toString();
  return rest ? `?${rest}` : "";
}

let pending: GoogleReturn | null | undefined;

/** Reads the return once per page load and cleans the address bar (the fragment and the parameters). */
export function readGoogleReturn(): GoogleReturn | null {
  if (pending !== undefined) return pending;
  pending = null;
  try {
    pending = parseGoogleReturn(window.location.search, window.location.hash);
    if (pending) {
      const hash = window.location.hash.includes("google_mfa=") ? "" : window.location.hash;
      window.history.replaceState(window.history.state, "", window.location.pathname + stripGoogleParams(window.location.search) + hash);
    }
  } catch {
    // intentional: no URL API - nothing to read
  }
  return pending;
}

/** For the sign-in page: the MFA step or the message, handed over once. */
export function takeGoogleReturn(): GoogleReturn | null {
  const value = readGoogleReturn();
  pending = null;
  return value;
}
