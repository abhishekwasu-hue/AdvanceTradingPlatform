import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, clearToken, setToken, tryRefresh } from "../api/client";
import { endView } from "../api/viewAs";
import { currentView } from "../api/viewState";
import type { UserResponse } from "../types";
import type { LegalAckBody } from "../legal/legal";
import { clearSignIn, markSignIn } from "../broker/signIn";
import { readGoogleReturn } from "./google";

interface AuthState {
  user: UserResponse | null;
  loading: boolean;
  /** Resolves to a challenge token when the account needs a second factor; the caller then
   * calls `completeMfaLogin`. Resolves to null when the login is complete. */
  login: (email: string, password: string) => Promise<string | null>;
  completeMfaLogin: (mfaToken: string, code: string) => Promise<void>;
  /** Z10-f: `legal` is {slug: {version, sha256}} of the legal documents accepted on the form. */
  register: (email: string, password: string, inviteCode?: string, legal?: Record<string, LegalAckBody>) => Promise<void>;
  acceptInvite: (token: string, password: string, legal?: Record<string, LegalAckBody>) => Promise<void>;
  resetPassword: (token: string, password: string) => Promise<void>;
  logout: () => void;
  /** Re-reads /auth/me (after email verification, scope changes, ...). */
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserResponse | null>(null);
  const [loading, setLoading] = useState(true);
  // GS-1: back from Google sign-in. Read during the first render, before any effect below can navigate (a redirect
  // would drop the fragment that carries the two-factor challenge); the address bar is cleaned at once.
  const [google] = useState(readGoogleReturn);

  useEffect(() => {
    // P0.3 / S6: nothing survives a reload in memory; the HttpOnly refresh cookie (or a token an older build
    // left in storage) gets a fresh access token, then the profile. After Google sign-in the refresh cookie is already
    // set, and it counts as a real sign-in.
    tryRefresh()
      .then((ok) => (ok ? api.me().then((me) => {
        if (google?.kind === "signed_in") markSignIn(me.id, google.isNew);
        setUser(me);
      }) : undefined))
      .catch(() => clearToken())
      .finally(() => setLoading(false));
  }, []);

  // P2b: each real sign-in gets its own id (the broker prompt's "Later" lasts until the next one); a new account is
  // flagged so the shell opens the Broker page first.
  async function signedIn(isNew = false) {
    const me = await api.me();
    markSignIn(me.id, isNew);
    setUser(me);
  }

  async function login(email: string, password: string): Promise<string | null> {
    const response = await api.login(email, password);
    if (response.mfa_required && response.mfa_token) return response.mfa_token;
    setToken(response.access_token, response.refresh_token);
    await signedIn();
    return null;
  }

  async function completeMfaLogin(mfaToken: string, code: string) {
    const response = await api.mfaVerifyLogin(mfaToken, code);
    setToken(response.access_token, response.refresh_token);
    await signedIn();
  }

  async function acceptInvite(token: string, password: string, legal?: Record<string, LegalAckBody>) {
    const response = await api.acceptInvite(token, password, legal);
    setToken(response.access_token, response.refresh_token);
    await signedIn();
  }

  async function refreshUser() {
    try {
      setUser(await api.me());
    } catch {
      // token gone - the next request handles it
    }
  }

  async function resetPassword(token: string, password: string) {
    const response = await api.resetPassword(token, password);
    setToken(response.access_token, response.refresh_token);
    await signedIn();
  }

  async function register(email: string, password: string, inviteCode?: string, legal?: Record<string, LegalAckBody>) {
    const response = await api.register(email, password, inviteCode, legal);
    setToken(response.access_token, response.refresh_token);
    await signedIn(true);
  }

  function logout() {
    // MO3-A1: "Sign out" during a read-only support view ends the view (the admin stays signed in; the banner's host
    // brings the admin back) - it never signs the person out.
    if (currentView()) { void endView(); return; }
    // Best effort server-side revoke; the local tokens are cleared regardless.
    api.logout().catch(() => {});
    clearToken();
    clearSignIn();
    setUser(null);
  }

  return <AuthContext.Provider value={{ user, loading, login, completeMfaLogin, register, logout, acceptInvite, resetPassword, refreshUser }}>{children}</AuthContext.Provider>;
}

/** The sign-in state, or null outside an AuthProvider (a card rendered on its own: a story, a test). */
export function useOptionalAuth(): AuthState | null {
  return useContext(AuthContext);
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
