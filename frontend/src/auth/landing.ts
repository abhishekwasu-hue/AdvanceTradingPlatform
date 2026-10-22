import { routerPath } from "../shell/aliases";

/** UX1: the addresses that are only the sign-in / account screen - signing in there moves on to Today. */
const SIGN_IN_ADDRESSES = ["/account", "/settings/account"];

/**
 * Where the app goes the moment a visitor finishes signing in (App.tsx SignInGate), or null to stay. The sign-in
 * screen is shown at whatever address was asked for, so "the page they asked for" is simply that address: stay. The
 * one exception is the Account page itself (audit P01: "after sign-in the user stays on the Account page") - it goes to
 * Today (`/`). The answer is always a fixed same-origin path or null: nothing from the address or a query string is
 * ever used as a destination, so this cannot be turned into an open redirect.
 */
export function landingAfterSignIn(pathname: string): string | null {
  const { path, malformed } = routerPath(pathname);
  if (malformed) return null;
  const clean = `/${path.replace(/^\/+|\/+$/g, "")}`;
  return SIGN_IN_ADDRESSES.includes(clean) ? "/" : null;
}
