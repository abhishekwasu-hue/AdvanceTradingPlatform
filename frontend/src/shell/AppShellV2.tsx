import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState, type ComponentType, type ReactNode } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router";
import { useAuth } from "../auth/AuthContext";
import BrokerPromptHost from "../broker/BrokerPromptHost";
import ReferralClaimHost from "../growth/ReferralClaimHost";
import ConnectAiHostV2 from "../aiKeys/ConnectAiHostV2";
import { brokerDot } from "../broker/brokerState";
import { useBrokerConnections } from "../broker/useBrokerConnections";
import ErrorBoundary from "../components/ErrorBoundary";
import type { Page } from "../components/Sidebar";
import LegalAcceptanceBanner from "../legal/LegalAcceptanceBanner";
import LivePricesBanner from "../live/LivePricesBanner";
import PwaBanners from "../pwa/PwaBanners";
import { PAGES, PAGE_COMPONENTS, pathFor, type PageProps } from "../routes";
import { BrokerGate } from "../ui/BrokerGate";
import { useFeatures } from "../ui/useFeatures";
import { STORED_HISTORY_PATHS, storedDailyFor } from "../ui/dataPages";
import BottomNavV2 from "./BottomNavV2";
import CommandBarV2, { currentPlatform, isApplePlatform, isCommandShortcut, otherDialogOpen } from "./CommandBarV2";
import { LegacyCopilot, NotFound, PageLoading, legacyTarget, withoutPageParam } from "./common";
import MoreSheetV2 from "./MoreSheetV2";
import { navEntryFor, visiblePinned, visibleSections, type NavContext } from "./navModel";
import PageTabsV2 from "./PageTabsV2";
import { OWN_BROKER_STATES, PAGE_COMPONENTS_V2 } from "./pagesV2";
import { MARKETS_ADDRESS, SETUP_ADDRESS, TRADING_DEFAULTS_ADDRESS } from "./aliases";
import { IDEAS_ADDRESS } from "../ideas/model";
import { LEARN_ADDRESS } from "../learn/model";
import { SETTINGS_SECTIONS } from "../settings/paths";
import { closedFlag } from "./pageFlags";
import { ComingSoon } from "../ui/ComingSoon";
import { V2_ALIASES, routerPath, shellGates, shellKeyFromPath, shellTitle, v2RedirectFor } from "./shellRoutes";
import SidebarV2 from "./SidebarV2";
import TopBarV2 from "./TopBarV2";
import { usePaperOnly } from "../setup/paperOnly";
import { liveHiddenFrom } from "../settings/useLiveHidden";
import { useStrategyNames } from "../ui/useStrategyNames";
import { tourIdFor } from "../tours/pages";
import { useTours } from "../tours/state";
import TourHost from "../tours/TourHost";

type FeedbackDialogProps = { open: boolean; onOpenChange: (open: boolean) => void; pathname: string; initialText?: string };
const FeedbackDialog = lazy<ComponentType<FeedbackDialogProps>>(
  () => import("../feedback/FeedbackDialog").catch(() => ({ default: () => null })),
);
type SupportDialogProps = { open: boolean; onOpenChange: (open: boolean) => void; onFeedback: (draft: string) => void };
const SupportDialog = lazy<ComponentType<SupportDialogProps>>(
  () => import("../support/SupportDialog").catch(() => ({ default: () => null })),
);

/**
 * UI2-1 (UI v2 proposal §2, §4, §9, §12): the v2 app shell, used only while `ui_v2` is on (App.tsx keeps today's
 * shell otherwise, unchanged). It holds the sidebar (md and up), the top bar with the command bar, the phone bottom bar
 * and More sheet, the v2 routes (`/charts`, the merged pages' new addresses, the old addresses' redirects) and the
 * broker gate in front of every data page (src/ui/dataPages.ts): no page that shows market or broker data renders
 * until the trader's broker session is usable - "Connect your broker" / "Log in to <Broker>" instead, failing closed.
 */
export default function AppShellV2() {
  const location = useLocation();
  const navigate = useNavigate();
  const { user } = useAuth();
  const features = useFeatures();
  // The router decodes each segment before it matches (/%70ositions is Positions): every decision below reads the
  // path the router sees, and the gate fails closed on an address that cannot be decoded (review M1).
  const path = routerPath(location.pathname).path;
  const key = shellKeyFromPath(path);
  // P3: on the front page in PAPER the top bar shows no LIVE or e-mail notice (only after the deployments were read).
  const frontPaperOnly = usePaperOnly(!!user && key === "dashboard");
  // User-friendly pass item 2: while `phase0_paper_only` is on, no LIVE notice on any page.
  const liveHidden = liveHiddenFrom(features);
  // Item 6: the Library's names, read once per sign-in, so no screen shows a strategy id (src/ui/strategyName.ts).
  useStrategyNames(user?.id ?? null);
  const title = shellTitle(key);
  const role = user?.role ?? null;
  const ctx = useMemo<NavContext>(() => ({ on: features.on, role }), [features.on, role]);
  const sections = useMemo(() => visibleSections(ctx), [ctx]);
  const pinned = useMemo(() => visiblePinned(ctx), [ctx]);
  // P2b: the broker's status dot on the Broker entry (and on More while it needs attention).
  const dot = brokerDot(useBrokerConnections().rows);
  const accountPath = user ? "/settings/account" : pathFor("account");
  const go = useCallback((p: Page) => navigate(pathFor(p)), [navigate]);
  // MO2 part C: this page's tips (welcome and page tours, src/tours) and the "?" that replays them.
  const tourId = user ? tourIdFor(key, path) : null;
  const tours = useTours(user?.id ?? null);
  const replayTour = tourId && tours.known && tours.store ? () => tours.store?.request({ kind: "page", id: tourId }) : undefined;

  const [moreOpen, setMoreOpen] = useState(false);
  const [commandOpen, setCommandOpen] = useState(false);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedbackLoaded, setFeedbackLoaded] = useState(false);
  const [feedbackDraft, setFeedbackDraft] = useState<string | undefined>(undefined);
  const supportOn = !!user && features.on("support_bot");
  const [supportOpen, setSupportOpen] = useState(false);
  const [supportLoaded, setSupportLoaded] = useState(false);
  const openFeedback = useCallback((draft?: string) => { setFeedbackDraft(draft); setFeedbackLoaded(true); setFeedbackOpen(true); }, []);
  const openHelp = useCallback(() => { setSupportLoaded(true); setSupportOpen(true); }, []);

  // Ctrl K / Cmd K opens (and closes) the command bar from anywhere in the app.
  useEffect(() => {
    const apple = isApplePlatform(currentPlatform());
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || !isCommandShortcut(e, apple) || otherDialogOpen(document)) return;
      e.preventDefault();
      setMoreOpen(false);
      setCommandOpen((o) => !o);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // A new page closes the More sheet, starts at the top, names the browser tab and moves focus to the content.
  const mainRef = useRef<HTMLElement>(null);
  const first = useRef(true);
  useEffect(() => { setMoreOpen(false); }, [location.pathname]);
  // The sheet is a phone control: a window widened past the phone layout (a tablet turned) closes it.
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const wide = window.matchMedia("(min-width: 768px)");
    const onChange = () => { if (wide.matches) setMoreOpen(false); };
    wide.addEventListener?.("change", onChange);
    return () => wide.removeEventListener?.("change", onChange);
  }, []);
  useEffect(() => {
    document.title = title ? `${title} · AiAlgoDesk` : "AiAlgoDesk";
    if (first.current) { first.current = false; return; }
    window.scrollTo(0, 0);
    mainRef.current?.focus({ preventScroll: true });
  }, [key, title]);

  const legacy = location.pathname === "/" ? legacyTarget(location.search) : null;
  const redirect = legacy ? null : v2RedirectFor(path, location.search, { signedIn: !!user, on: features.on });

  // UI2-4b2: the Charts page (v2 only, so not one of PAGES) comes from the same registry as the other v2 pages.
  const ChartsPage: ComponentType<PageProps> = PAGE_COMPONENTS_V2.charts ?? NotFound;
  // UI2-7: Settings → Setup (v2 only), the same way.
  const SetupPage: ComponentType<PageProps> = PAGE_COMPONENTS_V2.setup ?? NotFound;
  // Settings part C: Settings → My trading defaults (v2 only), the same way.
  const TradingDefaultsPage: ComponentType<PageProps> = PAGE_COMPONENTS_V2["trading-defaults"] ?? NotFound;
  // Idea library slice 1 (v2 only), the same way.
  const IdeasPage: ComponentType<PageProps> = PAGE_COMPONENTS_V2.ideas ?? NotFound;
  // MO4 slice 2: the Sector & index view (v2 only), the same way.
  const MarketsPage: ComponentType<PageProps> = PAGE_COMPONENTS_V2.markets ?? NotFound;
  // Readiness C13: the Learn page (v2 only), the same way.
  const LearnPage: ComponentType<PageProps> = PAGE_COMPONENTS_V2.learn ?? NotFound;
  const routes = (
    <ErrorBoundary key={key ?? "not-found"} title={key ? title : undefined}>
      <Suspense fallback={<PageLoading />}>
        <Routes>
          {PAGES.map((p) => {
            // UI v2 §12: the page's v2 layout where it has one (UI2-2: the dashboard), today's page otherwise.
            const PageComponent = PAGE_COMPONENTS_V2[p] ?? PAGE_COMPONENTS[p];
            const element = <PageComponent onNavigate={go} />;
            return p === "copilot"
              ? <Route key={p} path="/copilot/:tab?" caseSensitive element={element} />
              : <Route key={p} path={pathFor(p)} caseSensitive element={element} />;
          })}
          <Route path="/charts/:symbol?" caseSensitive element={<ChartsPage onNavigate={go} />} />
          <Route path={SETUP_ADDRESS} caseSensitive element={<SetupPage onNavigate={go} />} />
          <Route path={TRADING_DEFAULTS_ADDRESS} caseSensitive element={<TradingDefaultsPage onNavigate={go} />} />
          <Route path={IDEAS_ADDRESS} caseSensitive element={<IdeasPage onNavigate={go} />} />
          <Route path={MARKETS_ADDRESS} caseSensitive element={<MarketsPage onNavigate={go} />} />
          <Route path={LEARN_ADDRESS} caseSensitive element={<LearnPage onNavigate={go} />} />
          {SETTINGS_SECTIONS.map(({ key: k, path: p }) => {
            const SectionPage: ComponentType<PageProps> = PAGE_COMPONENTS_V2[k] ?? NotFound;
            return <Route key={p} path={p} caseSensitive element={<SectionPage onNavigate={go} />} />;
          })}
          {V2_ALIASES.map(({ path, page }) => {
            // UX3-9: a merged page's tab shows the page's v2 layout where it has one (My trades › Money: PortfolioV2).
            const PageComponent = PAGE_COMPONENTS_V2[page] ?? PAGE_COMPONENTS[page];
            return <Route key={path} path={path} caseSensitive element={<PageComponent onNavigate={go} />} />;
          })}
          <Route path="/ai-copilot/:tab?" caseSensitive element={<LegacyCopilot />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </Suspense>
    </ErrorBoundary>
  );

  // UX1 (handover 8b): a page whose feature flag is off shows "Coming soon" at its address (it is not in the nav).
  const closed = closedFlag(key, features);

  let content: ReactNode;
  if (legacy) content = <Navigate to={`${pathFor(legacy)}${withoutPageParam(location.search)}`} replace />;
  else if (redirect) content = <Navigate to={redirect} replace />;
  else if (closed) content = <ComingSoon flag={closed} />;
  // The gate stays mounted while the trader moves between data pages, so the broker status is not re-read per click.
  // A page that shows the broker states itself (UI2-2: the dashboard) is not wrapped (src/shell/pagesV2.ts).
  else if (shellGates(location.pathname, features.on("backtest_stored_history") ? [...OWN_BROKER_STATES, ...STORED_HISTORY_PATHS] : OWN_BROKER_STATES)) {
    // Day-one fix 2: with stored history on, Find setups and Signals keep their Daily content before the broker login.
    content = <BrokerGate storedDaily={features.on("backtest_stored_history") ? storedDailyFor(location.pathname) : null}>{routes}</BrokerGate>;
  }
  else content = routes;

  const wide = key === "screener" || key === "options-builder" || key === "charts";
  return (
    <div className="flex min-h-screen bg-surface" data-shell="v2">
      <a href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-space-2 focus:top-space-2 focus:z-[70] focus:rounded-control focus:bg-surface-1 focus:px-space-3 focus:py-space-2 focus:text-t14 focus:text-fg focus:shadow-raised">
        Skip to content
      </a>
      <SidebarV2 sections={sections} pinned={pinned} dot={dot} current={key} user={user} accountPath={accountPath} />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBarV2 title={title} user={user} accountPath={accountPath}
          notificationsActive={key === "notifications"} accountActive={key === "account"}
          onSearch={() => setCommandOpen(true)} onHelp={supportOn ? openHelp : undefined} onFeedback={user ? () => openFeedback() : undefined}
          paperOnly={frontPaperOnly || liveHidden} onTour={replayTour} />
        <PwaBanners />
        <LegalAcceptanceBanner />
        {/* TM Part 1 (flag data_hub): the one notice while live prices reconnect or the broker login needs renewing. */}
        <LivePricesBanner enabled={!!user && features.on("data_hub")} />
        {/* The window scrolls, not <main> (as in today's shell); the Copilot's sticky tab bar needs overflow-x-clip. */}
        <main id="main" ref={mainRef} tabIndex={-1}
          className={`w-full flex-1 ${key === "copilot" ? "overflow-x-clip" : "overflow-y-auto"} px-space-4 pt-space-4 ${key === "screener" ? "pb-[calc(9rem+env(safe-area-inset-bottom))]" : "pb-[calc(6rem+env(safe-area-inset-bottom))]"} md:px-space-8 md:pb-space-10 md:pt-space-6 ${wide ? "max-w-none" : "max-w-[1304px]"} outline-none`}>
          {/* UX3-9: a merged page's tabs (My trades, Strategies, Performance) above the tab on screen. */}
          {!legacy && !redirect && <PageTabsV2 current={key} ctx={ctx} />}
          {content}
        </main>
      </div>
      <BottomNavV2 current={navEntryFor(key)} dot={dot} moreOpen={moreOpen} onMore={() => setMoreOpen((o) => !o)} onPick={() => setMoreOpen(false)} />
      <MoreSheetV2 open={moreOpen} onOpenChange={setMoreOpen} ctx={ctx} current={key} dot={dot}
        onHelp={supportOn ? openHelp : undefined} onFeedback={user ? () => openFeedback() : undefined} />
      <BrokerPromptHost />
      {/* Readiness C13: a new account names the invite link it arrived with, once (flag `referrals`). */}
      {user && features.on("referrals") && <ReferralClaimHost />}
      {user && <ConnectAiHostV2 />}
      {user && <TourHost userKey={user.id} role={user.role ?? null} tourId={tourId} />}
      <CommandBarV2 open={commandOpen} onOpenChange={setCommandOpen} ctx={ctx} onGo={(path) => navigate(path)} />
      {supportOn && supportLoaded && (
        <Suspense fallback={null}>
          <SupportDialog open={supportOpen} onOpenChange={setSupportOpen}
            onFeedback={(draft) => { setSupportOpen(false); openFeedback(draft); }} />
        </Suspense>
      )}
      {user && feedbackLoaded && (
        <Suspense fallback={null}>
          <FeedbackDialog open={feedbackOpen} onOpenChange={setFeedbackOpen} pathname={location.pathname} initialText={feedbackDraft} />
        </Suspense>
      )}
    </div>
  );
}
