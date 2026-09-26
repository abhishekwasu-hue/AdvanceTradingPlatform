import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, clearToken, getToken, setToken } from "../api/client";
import type { UserResponse } from "../types";

interface AuthState {
  user: UserResponse | null;
  loading: boolean;
  /** Resolves to a challenge token when the account needs a second factor; the caller then
   * calls `completeMfaLogin`. Resolves to null when the login is complete. */
  login: (email: string, password: string) => Promise<string | null>;
  completeMfaLogin: (mfaToken: string, code: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  acceptInvite: (token: string, password: string) => Promise<void>;
  resetPassword: (token: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = getToken();
    if (!token) {
      setLoading(false);
      return;
    }
    api
      .me()
      .then(setUser)
      .catch(() => clearToken())
      .finally(() => setLoading(false));
  }, []);

  async function login(email: string, password: string): Promise<string | null> {
    const response = await api.login(email, password);
    if (response.mfa_required && response.mfa_token) return response.mfa_token;
    setToken(response.access_token, response.refresh_token);
    setUser(await api.me());
    return null;
  }

  async function completeMfaLogin(mfaToken: string, code: string) {
    const response = await api.mfaVerifyLogin(mfaToken, code);
    setToken(response.access_token, response.refresh_token);
    setUser(await api.me());
  }

  async function acceptInvite(token: string, password: string) {
    const response = await api.acceptInvite(token, password);
    setToken(response.access_token, response.refresh_token);
    setUser(await api.me());
  }

  async function resetPassword(token: string, password: string) {
    const response = await api.resetPassword(token, password);
    setToken(response.access_token, response.refresh_token);
    setUser(await api.me());
  }

  async function register(email: string, password: string) {
    const response = await api.register(email, password);
    setToken(response.access_token, response.refresh_token);
    setUser(await api.me());
  }

  function logout() {
    // Best effort server-side revoke; the local tokens are cleared regardless.
    api.logout().catch(() => {});
    clearToken();
    setUser(null);
  }

  return <AuthContext.Provider value={{ user, loading, login, completeMfaLogin, register, logout, acceptInvite, resetPassword }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
