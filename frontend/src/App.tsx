import { Bell, LifeBuoy, Menu, MessageSquarePlus, UserCircle2 } from "lucide-react";
import { Suspense, lazy, useCallback, useEffect, useRef, useState, type ComponentType, type ReactNode } from "react";
import { BrowserRouter, Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { ThemeToggle } from "./components/AppearanceCard";
import BottomNav from "./components/BottomNav";
import ErrorBoundary from "./components/ErrorBoundary";
import PwaBanners from "./pwa/PwaBanners";
import LegalAcceptanceBanner from "./legal/LegalAcceptanceBanner";
import Sidebar, { NAV, type Page } from "./components/Sidebar";
import { ToastProvider } from "./components/Toast";
import { api } from "./api/client";
import { PAGES, PAGE_COMPONENTS, isStatusPath, pageFromPath, pathFor } from "./routes";
import { flagOffFallback, shellMode } from "./shell/aliases";
import ShellWaitingV2 from "./shell/ShellWaitingV2";
import { useWaitCap } from "./shell/shellWait";
import { LegacyCopilot, NotFound, PageLoading, legacyTarget, withoutPageParam } from "./shell/common";
import { usePaperOnly } from "./setup/paperOnly";
import { setV2ThemeDefault } from "./theme";
import { FeaturesProvider, useFeatures, useUiV2, v2ThemeDefaultFrom } from "./ui/useFeatures";
import type { SystemStatus } from "./types";

const ChartWindow = lazy(() => import("./pages/ChartWindow"));
const ContractChartWindow = lazy(() => import("./pages/ContractChartWindow"));
const FuturesBasisWindow = lazy(() => import("./pages/FuturesBasisWindow"));
// CH5: the multi-chart workspace (flag `chart_workspace`), its own tab and chunk like the single chart.
const ChartWorkspace = lazy(() => import("./pages/ChartWorkspace"));
// Z10-e (G7): the public status page - no login, no shell, its own small chunk.
const StatusPage = lazy(() => import("./pages/StatusPage"));
// PW6: loaded on first open. The top bar sits outside every error boundary, so a chunk that fails to load (offline)
// renders nothing instead of taking the shell down.
type FeedbackDialogProps = { open: boolean; onOpenChange: (open: boolean) => void; pathname: string; initialText?: string };
const FeedbackDialog = lazy<ComponentType<FeedbackDialogProps>>(
  () => import("./feedback/FeedbackDialog").catch(() => ({ default: () => null })),
);
// Z10-f: the help desk (flag `support_bot`) and the public legal pages, each its own chunk.
type SupportDialogProps = { open: boolean; onOpenChange: (open: boolean) => void; onFeedback: (draft: string) => void };
const SupportDialog = lazy<ComponentType<SupportDialogProps>>(
  () => import("./support/SupportDialog").catch(() => ({ default: () => null })),
);
const LegalPage = lazy(() => import("./legal/LegalPage"));
// UI2-1: the v2 shell (sidebar sections, bottom bar + More sheet, command bar, /charts, broker gate on data pages) -
// its own chunk, loaded only while `ui_v2` is on for the organisation.
const AppShellV2 = lazy(() => import("./shell/AppShellV2"));

function TopBar({ page, onMenu }: { page: Page | null; onMenu: () => void }) {
  const { user } = useAuth();
  const location = useLocation();
  const title = page === null ? "Page not found" : page === "account" ? "Account" : NAV.find((n) => n.id === page)?.label ?? "";
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedbackLoaded, setFeedbackLoaded] = useState(false);
  const [feedbackDraft, setFeedbackDraft] = useState<string | undefined>(undefined);
  // Z10-f: the help button only while the operator has turned `support_bot` on for this organisation (UI2-0: read
  // from the shell's single /system/features fetch).
  const features = useFeatures();
  const supportOn = !!user && features.on("support_bot");
  // P3: on the front page in PAPER (no LIVE deployment, read successfully) no LIVE or e-mail notice; other pages keep them.
  const paperOnly = usePaperOnly(!!user && page === "dashboard");
  const [supportOpen, setSupportOpen] = useState(false);
  const [supportLoaded, setSupportLoaded] = useState(false);
  useEffect(() => {
    const load = () => api.systemStatus().then(setStatus).catch(() => {});
    load();
    const id = window.setInterval(load, 60_000);
    return () => window.clearInterval(id);
  }, []);

  return (
    <header className="shrink-0 border-b border-border bg-panel/80 pt-[env(safe-area-inset-top)] backdrop-blur">
      <div className="flex h-14 items-center justify-between pl-[max(0.75rem,env(safe-area-inset-left))] pr-[max(0.75rem,env(safe-area-inset-right))] md:px-6">
      <div className="flex items-center gap-3 min-w-0">
        <button onClick={onMenu} aria-label="Open navigation menu" aria-controls="app-navigation" className="-ml-2 flex h-11 w-11 items-center justify-center rounded-md text-slate-200 hover:bg-panel2 md:hidden"><Menu size={18} /></button>
        <span className="min-w-0 shrink truncate text-[15px] font-semibold text-fg">{title}</span>
        {status?.maintenance_mode && (
          <span className="truncate rounded-md border border-amber-500/50 bg-amber-500/10 px-2 py-0.5 text-[11px] font-bold text-amber-300" title={status.maintenance_message ?? ""}>
            MAINTENANCE: no new entries{status.maintenance_message ? ` - ${status.maintenance_message}` : ""}
          </span>
        )}
        {user && user.email_verified === false && !paperOnly && (
          <Link to={pathFor("account")} className="truncate rounded-md border border-sky-500/50 bg-sky-500/10 px-2 py-0.5 text-[11px] font-bold text-sky-300 hover:bg-sky-500/20" title="LIVE trading and broker credentials may require a verified address">
            <span className="sm:hidden">Verify</span><span className="hidden sm:inline">Verify your email</span>
          </Link>
        )}
        {status && status.disabled_brokers.length > 0 && !paperOnly && (
          <span className="rounded-md border border-rose-500/40 bg-rose-500/10 px-2 py-0.5 text-[11px] font-bold text-rose-300">
            LIVE paused on {status.disabled_brokers.join(", ")}
          </span>
        )}
      </div>
      <div className="flex items-center gap-1.5">
        <ThemeToggle />
        {user && supportOn && (
          <button
            type="button"
            onClick={() => { setSupportLoaded(true); setSupportOpen(true); }}
            title="Help"
            aria-label="Help"
            className="p-2 rounded-md text-muted transition-colors hover:text-slate-200 hover:bg-panel2"
          >
            <LifeBuoy size={18} />
          </button>
        )}
        {user && supportOn && supportLoaded && (
          <Suspense fallback={null}>
            <SupportDialog
              open={supportOpen}
              onOpenChange={setSupportOpen}
              onFeedback={(draft) => { setSupportOpen(false); setFeedbackDraft(draft); setFeedbackLoaded(true); setFeedbackOpen(true); }}
            />
          </Suspense>
        )}
        {user && (
          <button
            type="button"
            onClick={() => { setFeedbackDraft(undefined); setFeedbackLoaded(true); setFeedbackOpen(true); }}
            title="Send feedback"
            aria-label="Send feedback"
            className="p-2 rounded-md text-muted transition-colors hover:text-slate-200 hover:bg-panel2"
          >
            <MessageSquarePlus size={18} />
          </button>
        )}
        {user && feedbackLoaded && (
          <Suspense fallback={null}>
            <FeedbackDialog open={feedbackOpen} onOpenChange={setFeedbackOpen} pathname={location.pathname} initialText={feedbackDraft} />
          </Suspense>
        )}
        <Link
          to={pathFor("notifications")}
          title="Notifications"
          aria-label="Notifications"
          aria-current={page === "notifications" ? "page" : undefined}
          className={`flex h-11 w-11 items-center justify-center rounded-md transition-colors md:h-9 md:w-9 ${
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
          className={`flex h-11 w-11 items-center justify-center rounded-md transition-colors md:h-9 md:w-9 ${
            page === "account" ? "text-brand bg-panel2" : "text-muted hover:text-slate-200 hover:bg-panel2"
          }`}
        >
          <UserCircle2 size={18} className={user ? "text-accent" : ""} />
        </Link>
      </div>
      </div>
    </header>
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
    document.title = label ? `${label} · AiAlgoDesk` : "AiAlgoDesk";
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
        <PwaBanners />
        <LegalAcceptanceBanner />
        {/* The window scrolls, not <main>. Other pages keep overflow-y-auto (wide tables without their own wrapper
            scroll inside <main>); the Copilot uses overflow-x-clip, which is not a scroll container, so its sticky tab
            bar sticks to the viewport (its own wide content scrolls in its own wrappers). */}
        <main ref={mainRef} tabIndex={-1} className={`flex-1 ${page === "copilot" ? "overflow-x-clip" : "overflow-y-auto"} p-3 ${page === "screener" ? "pb-[calc(9rem+env(safe-area-inset-bottom))]" : "pb-[calc(5rem+env(safe-area-inset-bottom))]"} md:p-6 ${page === "screener" || page === "options-builder" ? "max-w-none" : "max-w-6xl"} outline-none`}>
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
      <BottomNav page={page} moreOpen={menuOpen} onMore={() => setMenuOpen((o) => !o)} />
    </div>
  );
}

/** UI2-1: the v2 shell while `ui_v2` is known and on; today's shell, exactly as before, otherwise. While it is not
 * known yet, a data page waits on a neutral screen instead of showing today's page (shell/aliases.ts shellMode). */
function Shell() {
  const { user, loading } = useAuth();
  const features = useFeatures();
  const v2 = useUiV2();
  const location = useLocation();
  const known = { v2, authLoading: loading, signedIn: !!user, featuresLoaded: features.loaded, pathname: location.pathname };
  // The wait is bounded (review R2-1): after SHELL_WAIT_MS, "not known" counts as "off" - today's shell.
  const capped = useWaitCap(shellMode(known) === "wait");
  const mode = shellMode({ ...known, capped });
  if (mode === "today") return <AppShell />;
  if (mode === "wait") return <ShellWaitingV2 />;
  if (mode === "fallback") return <Navigate to={`${flagOffFallback(location.pathname)}${location.search}`} replace />;
  return (
    <ErrorBoundary title="The app">
      <Suspense fallback={<ShellWaitingV2 />}><AppShellV2 /></Suspense>
    </ErrorBoundary>
  );
}

/** UI2-0: the organisation's feature flags, read once for everything below (useFeatures), refetched per signed-in
 * user. With `ui_v2` on, the theme default follows the system (UI-1); with it off nothing changes. */
function ShellFeatures({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  return (
    <FeaturesProvider userKey={user?.id ?? null}>
      <FeaturesFreshness />
      <V2ThemeDefault />
      {children}
    </FeaturesProvider>
  );
}

/** UI2-0: keeps the shared flags fresh without asking the server on every click (review M3, L8):
 * - a route change re-reads at most once per 30 s (refreshIfStale; pages used to read on mount);
 * - a re-read of the same user (email verified, scopes changed) re-reads at once, as the top bar did on `user`;
 * - a different user is left to the provider, which reads for the new id itself. */
function FeaturesFreshness() {
  const { user } = useAuth();
  const { pathname } = useLocation();
  const { refresh, refreshIfStale } = useFeatures();
  const firstPath = useRef(true);
  useEffect(() => {
    if (firstPath.current) { firstPath.current = false; return; }
    if (user) refreshIfStale();
  }, [pathname]);                                   // route changes only (user and the callback are read as they are now)
  const seen = useRef(user);
  useEffect(() => {
    const before = seen.current;
    seen.current = user;
    if (user && before && before !== user && before.id === user.id) refresh();
  }, [user, refresh]);
  return null;
}

function V2ThemeDefault() {
  // Only a real answer moves the theme default: a failed read never clears the v2 hint (review L7).
  const v2 = v2ThemeDefaultFrom(useFeatures());
  useEffect(() => { if (v2 !== null) setV2ThemeDefault(v2); }, [v2]);
  return null;
}

export default function App() {
  // Z10-e: `/status` is public and stands alone - outside the router, the shell and the sign-in refresh.
  if (isStatusPath(window.location.pathname)) {
    return (
      <ErrorBoundary title="The status page">
        <Suspense fallback={null}><StatusPage /></Suspense>
      </ErrorBoundary>
    );
  }
  // `?chart=SYMBOL` is a chart opened in its own browser tab (a chart's "New tab" button): just the chart, no shell.
  // `?workspace=1` (CH5) is the multi-chart workspace, also on its own.
  const params = new URLSearchParams(window.location.search);
  return (
    <BrowserRouter>
      <AuthProvider>
        <ShellFeatures>
        <ToastProvider>
          {params.has("basis") ? (
            <ErrorBoundary title="The futures basis chart">
              <Suspense fallback={null}><FuturesBasisWindow params={params} /></Suspense>
            </ErrorBoundary>
          ) : params.has("contract") ? (
            <ErrorBoundary title="The contract chart">
              <Suspense fallback={null}><ContractChartWindow params={params} /></Suspense>
            </ErrorBoundary>
          ) : params.has("workspace") ? (
            <ErrorBoundary title="The chart workspace">
              <Suspense fallback={null}><ChartWorkspace params={params} /></Suspense>
            </ErrorBoundary>
          ) : params.has("chart") ? (
            <ErrorBoundary title="The chart">
              <Suspense fallback={null}><ChartWindow params={params} /></Suspense>
            </ErrorBoundary>
          ) : (
            <Routes>
              {/* Z10-f: the legal pages are public and stand alone (no shell, no sign-in) - the sign-up form links here. */}
              <Route path="/legal/:slug" caseSensitive element={
                <ErrorBoundary title="The legal page"><Suspense fallback={null}><LegalPage /></Suspense></ErrorBoundary>
              } />
              <Route path="*" element={<Shell />} />
            </Routes>
          )}
        </ToastProvider>
        </ShellFeatures>
      </AuthProvider>
    </BrowserRouter>
  );
}
