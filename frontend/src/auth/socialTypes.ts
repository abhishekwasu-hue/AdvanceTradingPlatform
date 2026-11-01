/** M73: the shapes of the sign-in provider endpoints (backend app/auth/social_routes.py). */
import type { SignInProvider } from "./social";

export interface SignInProvidersAnswer {
  google: boolean;
  facebook?: boolean;
  providers?: SignInProvider[];
  ui_v2?: boolean;
}

/** What a pending step asks for: "link" (the account's password, its code) or "signup" (an e-mail, a beta code, the terms). */
export interface SocialPendingInfo {
  kind: "link" | "signup";
  provider: string;
  label: string;
  email: string | null;
  mfa?: boolean;
  has_password?: boolean;
  intent?: string;
  needs_email?: boolean;
  legal?: boolean;
  beta?: boolean;
}

export interface SignInIdentity { provider: string; label: string; email: string | null; linked_at: string | null }

export interface SignInIdentities {
  has_password: boolean;
  identities: SignInIdentity[];
  available: { id: string; label: string; kind: string }[];
}
