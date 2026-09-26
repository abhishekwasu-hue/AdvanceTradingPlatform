import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, clearToken, getToken, setToken } from "../api/client";
import type { UserResponse } from "../types";

interface AuthState {
  user: UserResponse | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  acceptInvite: (token: string, password: string) => Promise<void>;
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

  async function login(email: string, password: string) {
    const response = await api.login(email, password);
    setToken(response.access_token, response.refresh_token);
    setUser(await api.me());
  }

  async function acceptInvite(token: string, password: string) {
    const response = await api.acceptInvite(token, password);
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

  return <AuthContext.Provider value={{ user, loading, login, register, logout, acceptInvite }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
