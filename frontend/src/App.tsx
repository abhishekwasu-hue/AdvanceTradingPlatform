import { Bell, Menu, UserCircle2 } from "lucide-react";
import { Suspense, lazy, useCallback, useEffect, useRef, useState } from "react";
import { BrowserRouter, Link, Navigate, Route, Routes, useLocation, useNavigate, useParams } from "react-router";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { ThemeToggle } from "./components/AppearanceCard";
import ErrorBoundary from "./components/ErrorBoundary";
import Sidebar, { NAV, type Page } from "./components/Sidebar";
import { ToastProvider } from "./components/Toast";
import { api } from "./api/client";
import { PAGES, PAGE_COMPONENTS, pageFromPath, pathFor } from "./routes";
import { legacyCopilotPath } from "./copilot/tabs";
import type { SystemStatus } from "./types";

const ChartWindow = lazy(() => import("./pages/ChartWindow"));

/** The old Copilot addresses (/ai-copilot/study, /today, ...) open the redesigned tab that now holds that content. */
function LegacyCopilot() {
  const { tab } = useParams<{ tab?: string }>();
  let stored: string | null = null;
  try { stored = localStorage.getItem("atp_copilot_tab"); } catch { /* storage unavailable */ }
  return <Navigate to={legacyCopilotPath(tab, stored)} replace />;
}
function TopBar({ page, onMenu }: { page: Page | null; onMenu: () => void }) {
  const { user } = useAuth();
  const title = page === null ? "Page not found" : page === "account" ? "Account" : NAV.find((n) => n.id === page)?.label ?? "";
  const [status, setStatus] = useState<SystemStatus | null>(null);
  useEffect(() => {
    const load = () => api.systemStatus().then(setStatus).catch(() => {});
    load();
    const id = window.setInterval(load, 60_000);
    return () => window.clearInterval(id);
  }, []);

  return (
    <header className="h-14 shrink-0 border-b border-border bg-panel/80 backdrop-blur flex items-center justify-between px-3 md:px-6">
      <div className="flex items-center gap-3 min-w-0">
        <button onClick={onMenu} aria-label="Open navigation menu" aria-controls="app-navigation" className="rounded-md p-1.5 text-slate-200 hover:bg-panel2 md:hidden"><Menu size={18} /></button>
        <span className="text-[15px] font-semibold text-fg">{title}</span>
        {status?.maintenance_mode && (
          <span className="truncate rounded-md border border-amber-500/50 bg-amber-500/10 px-2 py-0.5 text-[11px] font-bold text-amber-300" title={status.maintenance_message ?? ""}>
            MAINTENANCE: no new entries{status.maintenance_message ? ` - ${status.maintenance_message}` : ""}
          </span>
        )}
        {user && user.email_verified === false && (
          <Link to={pathFor("account")} className="truncate rounded-md border border-sky-500/50 bg-sky-500/10 px-2 py-0.5 text-[11px] font-bold text-sky-300 hover:bg-sky-500/20" title="LIVE trading and broker credentials may require a verified address">
            Verify your email
          </Link>
        )}
        {status && status.disabled_brokers.length > 0 && (
          <span className="rounded-md border border-rose-500/40 bg-rose-500/10 px-2 py-0.5 text-[11px] font-bold text-rose-300">
            LIVE paused on {status.disabled_brokers.join(", ")}
          </span>
        )}
      </div>
      <div className="flex items-center gap-1.5">
        <ThemeToggle />
        <Link
          to={pathFor("notifications")}
          title="Notifications"
          aria-label="Notifications"
          aria-current={page === "notifications" ? "page" : undefined}
          className={`p-2 rounded-md transition-colors ${
            page === "notifications" ? "text-brand bg-panel2" : "text-muted hover:text-slate-200 hover:bg-panel2"
          }`}
        >
          <Bell size={18} />
        </Link>
        <Link
          to={pathFor("account")}
          title="Account"
          aria-label="Account"
          aria-current={page === "account" ? "page" : undefined}
          className={`p-2 rounded-md transition-colors ${
            page === "account" ? "text-brand bg-panel2" : "text-muted hover:text-slate-200 hover:bg-panel2"
          }`}
        >
          <UserCircle2 size={18} className={user ? "text-accent" : ""} />
        </Link>
      </div>
    </header>
  );
}

/** Links the backend sends (email verification, password reset, team invite) and the broker OAuth round-trip
 * (Settings -> "Login to Upstox" -> Upstox -> /api/broker/upstox/oauth/callback) land on the app root with a query
 * string. They keep working: the root forwards them, query intact, to the page that handles them. */
function legacyTarget(search: string): Page | null {
  const params = new URLSearchParams(search);
  if (params.has("invite") || params.has("reset") || params.has("verify")) return "account";
  if (params.has("broker")) return "settings";
  const named = params.get("page");                // web-push notifications open /?page=notifications
  if (named === "ai-copilot") return "copilot";        // the Copilot's old page id
  if (named && (PAGES as string[]).includes(named)) return named as Page;
  return null;
}

/** The query to carry to a forwarded page, minus the `page=` that only chose the destination. */
function withoutPageParam(search: string): string {
  const params = new URLSearchParams(search);
  params.delete("page");
  const rest = params.toString();
  return rest ? `?${rest}` : "";
}

function PageLoading() {
  return (
    <div className="space-y-3" aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading page</span>
      <div className="h-6 w-48 animate-pulse rounded bg-panel2" />
      <div className="h-32 animate-pulse rounded-xl bg-panel2/70" />
      <div className="h-32 animate-pulse rounded-xl bg-panel2/50" />
    </div>
  );
}

function NotFound() {
  return (
    <div className="rounded-xl border border-border bg-panel p-6 text-sm text-slate-200">
      <p className="font-semibold text-slate-100">This page does not exist.</p>
      <p className="mt-1 text-muted">The link may be old or mistyped.</p>
      <Link to="/" className="mt-3 inline-block text-brand hover:underline">Go to the dashboard</Link>
    </div>
  );
}

function AppShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const page = pageFromPath(location.pathname);
  const [menuOpen, setMenuOpen] = useState(false);
  const mainRef = useRef<HTMLElement>(null);
  const go = useCallback((p: Page) => navigate(pathFor(p)), [navigate]);

  // A new page starts at the top, keeps the browser tab title meaningful, and moves keyboard focus to the content.
  const first = useRef(true);
  useEffect(() => {
    const label = page === null ? "Not found" : page === "account" ? "Account" : NAV.find((n) => n.id === page)?.label ?? "";
    document.title = label ? `${label} · Advance Trading` : "Advance Trading";
    if (first.current) { first.current = false; return; }
    window.scrollTo(0, 0);                       // the window scrolls (the shell is min-h-screen), not <main>
    mainRef.current?.focus({ preventScroll: true });
  }, [page]);

  const legacy = location.pathname === "/" ? legacyTarget(location.search) : null;

  return (
    <div className="min-h-screen flex">
      <Sidebar page={page} open={menuOpen} onClose={() => setMenuOpen(false)} />
      <div className="flex-1 flex flex-col min-w-0">
        <TopBar page={page} onMenu={() => setMenuOpen(true)} />
        {/* The window scrolls, not <main>. Other pages keep overflow-y-auto (wide tables without their own wrapper
            scroll inside <main>); the Copilot uses overflow-x-clip, which is not a scroll container, so its sticky tab
            bar sticks to the viewport (its own wide content scrolls in its own wrappers). */}
        <main ref={mainRef} tabIndex={-1} className={`flex-1 ${page === "copilot" ? "overflow-x-clip" : "overflow-y-auto"} p-3 md:p-6 ${page === "screener" ? "max-w-none" : "max-w-6xl"} outline-none`}>
          {/* Keyed by page: an error on one page is forgotten when the trader moves to another. */}
          <ErrorBoundary key={page ?? "not-found"} title={page ? NAV.find((n) => n.id === page)?.label : undefined}>
            <Suspense fallback={<PageLoading />}>
              {legacy ? <Navigate to={`${pathFor(legacy)}${withoutPageParam(location.search)}`} replace /> : (
                <Routes>
                  {PAGES.map((p) => {
                    const PageComponent = PAGE_COMPONENTS[p];
                    const element = <PageComponent onNavigate={go} />;
                    return p === "copilot"
                      ? <Route key={p} path="/copilot/:tab?" caseSensitive element={element} />
                      : <Route key={p} path={pathFor(p)} caseSensitive element={element} />;
                  })}
                  <Route path="/ai-copilot/:tab?" caseSensitive element={<LegacyCopilot />} />
                  <Route path="*" element={<NotFound />} />
                </Routes>
              )}
            </Suspense>
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}

export default function App() {
  // `?chart=SYMBOL` is a chart opened in its own browser tab (a chart's "New tab" button): just the chart, no shell.
  const params = new URLSearchParams(window.location.search);
  return (
    <BrowserRouter>
      <AuthProvider>
        <ToastProvider>
          {params.has("chart") ? (
            <ErrorBoundary title="The chart">
              <Suspense fallback={null}><ChartWindow params={params} /></Suspense>
            </ErrorBoundary>
          ) : <AppShell />}
        </ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}
