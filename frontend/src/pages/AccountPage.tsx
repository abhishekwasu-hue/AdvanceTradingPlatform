import { useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { Card } from "../components/ui";

export default function AccountPage() {
  const { user, login, register, logout } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  if (user) {
    return (
      <div className="space-y-4 max-w-md">
        <h1 className="text-xl font-semibold text-slate-100">Account</h1>
        <Card>
          <div className="text-sm text-muted mb-1">Signed in as</div>
          <div className="text-base text-slate-100 mb-4">{user.email}</div>
          <button
            onClick={logout}
            className="rounded border border-border hover:bg-panel2 text-slate-200 px-4 py-1.5 text-sm"
          >
            Log out
          </button>
        </Card>
        <p className="text-xs text-muted">
          Broker credentials and paper trades are tied to this account. Anonymous use of Signals
          and Backtesting still works without logging in - signing in additionally saves your
          paper-execute fills to the Positions tab.
        </p>
      </div>
    );
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      if (mode === "login") {
        await login(email, password);
      } else {
        await register(email, password);
      }
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="max-w-sm space-y-4">
      <h1 className="text-xl font-semibold text-slate-100">{mode === "login" ? "Log in" : "Create account"}</h1>
      <Card>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="block text-xs text-muted mb-1">Email</label>
            <input
              type="email"
              required
              className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">Password</label>
            <input
              type="password"
              required
              minLength={6}
              className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          {error && <div className="text-xs text-danger">{error}</div>}
          <button
            type="submit"
            disabled={loading}
            className="w-full rounded bg-accent/90 hover:bg-accent text-slate-900 font-semibold px-4 py-1.5 text-sm disabled:opacity-50"
          >
            {loading ? "Please wait…" : mode === "login" ? "Log in" : "Create account"}
          </button>
        </form>
        <button
          onClick={() => setMode(mode === "login" ? "register" : "login")}
          className="mt-3 text-xs text-muted hover:text-slate-200 underline"
        >
          {mode === "login" ? "Need an account? Register" : "Already have an account? Log in"}
        </button>
      </Card>
    </div>
  );
}
