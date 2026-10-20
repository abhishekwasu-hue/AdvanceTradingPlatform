import { Link, Navigate, useParams } from "react-router";
import type { Page } from "../components/Sidebar";
import { legacyCopilotPath } from "../copilot/tabs";
import { PAGES } from "../routes";

/** The parts both shells share (moved here from App.tsx unchanged, so the v2 shell can use them too). */

/** The old Copilot addresses (/ai-copilot/study, /today, ...) open the redesigned tab that now holds that content. */
export function LegacyCopilot() {
  const { tab } = useParams<{ tab?: string }>();
  let stored: string | null = null;
  try { stored = localStorage.getItem("atp_copilot_tab"); } catch { /* storage unavailable */ }
  return <Navigate to={legacyCopilotPath(tab, stored)} replace />;
}

/** Links the backend sends (email verification, password reset, team invite) and the broker OAuth round-trip
 * (Settings -> "Login to Upstox" -> Upstox -> /api/broker/upstox/oauth/callback) land on the app root with a query
 * string. They keep working: the root forwards them, query intact, to the page that handles them. */
export function legacyTarget(search: string): Page | null {
  const params = new URLSearchParams(search);
  if (params.has("invite") || params.has("reset") || params.has("verify")) return "account";
  if (params.has("broker")) return "settings";
  const named = params.get("page");                // web-push notifications open /?page=notifications
  if (named === "ai-copilot") return "copilot";        // the Copilot's old page id
  if (named && (PAGES as string[]).includes(named)) return named as Page;
  return null;
}

/** The query to carry to a forwarded page, minus the `page=` that only chose the destination. */
export function withoutPageParam(search: string): string {
  const params = new URLSearchParams(search);
  params.delete("page");
  const rest = params.toString();
  return rest ? `?${rest}` : "";
}

export function PageLoading() {
  return (
    <div className="space-y-3" aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading page</span>
      <div className="h-6 w-48 animate-pulse rounded bg-panel2" />
      <div className="h-32 animate-pulse rounded-xl bg-panel2/70" />
      <div className="h-32 animate-pulse rounded-xl bg-panel2/50" />
    </div>
  );
}

export function NotFound() {
  return (
    <div className="rounded-xl border border-border bg-panel p-6 text-sm text-slate-200">
      <p className="font-semibold text-slate-100">This page does not exist.</p>
      <p className="mt-1 text-muted">The link may be old or mistyped.</p>
      <Link to="/" className="mt-3 inline-block text-brand hover:underline">Go to the dashboard</Link>
    </div>
  );
}
