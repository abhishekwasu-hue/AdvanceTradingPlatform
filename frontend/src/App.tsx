import { Bell, UserCircle2 } from "lucide-react";
import { useEffect, useState } from "react";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import Sidebar, { NAV, type Page } from "./components/Sidebar";
import { api } from "./api/client";
import type { SystemStatus } from "./types";
import AccountPage from "./pages/AccountPage";
import AdminPage from "./pages/AdminPage";
import AnalyticsPage from "./pages/AnalyticsPage";
import BacktestPage from "./pages/BacktestPage";
import ChartWindow from "./pages/ChartWindow";
import MarketplacePage from "./pages/MarketplacePage";
import AiCopilotPage from "./pages/AiCopilotPage";
import CoachGuidePage from "./pages/CoachGuidePage";
import DashboardPage from "./pages/DashboardPage";
import DeploymentsPage from "./pages/DeploymentsPage";
import FundamentalsPage from "./pages/FundamentalsPage";
import InstrumentsPage from "./pages/InstrumentsPage";
import NewsEventsPage from "./pages/NewsEventsPage";
import NotificationsPage from "./pages/NotificationsPage";
import OptionChainPage from "./pages/OptionChainPage";
import OrdersPage from "./pages/OrdersPage";
import PortfolioPage from "./pages/PortfolioPage";
import PositionsPage from "./pages/PositionsPage";
import QuantPage from "./pages/QuantPage";
import RiskManagementPage from "./pages/RiskManagementPage";
import ScannerPage from "./pages/ScannerPage";
import SettingsPage from "./pages/SettingsPage";
import SignalsPage from "./pages/SignalsPage";
import StrategiesPage from "./pages/StrategiesPage";
import StrategyBuilderPage from "./pages/StrategyBuilderPage";
import SystemLogsPage from "./pages/SystemLogsPage";
import TeamPage from "./pages/TeamPage";

function TopBar({ page, onChange }: { page: Page; onChange: (p: Page) => void }) {
  const { user } = useAuth();
  const title = page === "account" ? "Account" : NAV.find((n) => n.id === page)?.label ?? "";
  const [status, setStatus] = useState<SystemStatus | null>(null);
  useEffect(() => {
    const load = () => api.systemStatus().then(setStatus).catch(() => {});
    load();
    const id = window.setInterval(load, 60_000);
    return () => window.clearInterval(id);
  }, []);

  return (
    <header className="h-14 shrink-0 border-b border-border bg-panel/80 backdrop-blur flex items-center justify-between px-6">
      <div className="flex items-center gap-3 min-w-0">
        <h1 className="text-[15px] font-semibold text-slate-100">{title}</h1>
        {status?.maintenance_mode && (
          <span className="truncate rounded-md border border-amber-500/50 bg-amber-500/10 px-2 py-0.5 text-[11px] font-bold text-amber-300" title={status.maintenance_message ?? ""}>
            MAINTENANCE: no new entries{status.maintenance_message ? ` - ${status.maintenance_message}` : ""}
          </span>
        )}
        {user && user.email_verified === false && (
          <button onClick={() => onChange("account")} className="truncate rounded-md border border-sky-500/50 bg-sky-500/10 px-2 py-0.5 text-[11px] font-bold text-sky-300 hover:bg-sky-500/20" title="LIVE trading and broker credentials may require a verified address">
            Verify your email
          </button>
        )}
        {status && status.disabled_brokers.length > 0 && (
          <span className="rounded-md border border-rose-500/40 bg-rose-500/10 px-2 py-0.5 text-[11px] font-bold text-rose-300">
            LIVE paused on {status.disabled_brokers.join(", ")}
          </span>
        )}
      </div>
      <div className="flex items-center gap-1.5">
        <button
          onClick={() => onChange("notifications")}
          title="Notifications"
          className={`p-2 rounded-md transition-colors ${
            page === "notifications" ? "text-brand bg-panel2" : "text-muted hover:text-slate-200 hover:bg-panel2"
          }`}
        >
          <Bell size={18} />
        </button>
        <button
          onClick={() => onChange("account")}
          title="Account"
          className={`p-2 rounded-md transition-colors ${
            page === "account" ? "text-brand bg-panel2" : "text-muted hover:text-slate-200 hover:bg-panel2"
          }`}
        >
          <UserCircle2 size={18} className={user ? "text-accent" : ""} />
        </button>
      </div>
    </header>
  );
}

/** A broker OAuth round-trip (Settings -> "Login to Upstox" -> Upstox -> /api/broker/upstox/
 * oauth/callback) lands the browser back on the app root with `?broker=...&connected=1` (or
 * `&error=...`) in the query string - open straight onto Settings so the outcome is visible. */
function initialPage(): Page {
  try {
    const params = new URLSearchParams(window.location.search);
    if (params.has("invite") || params.has("reset")) return "account";
    return params.has("broker") ? "settings" : "dashboard";
  } catch {
    return "dashboard";
  }
}

function AppShell() {
  const [page, setPage] = useState<Page>(initialPage);

  return (
    <div className="min-h-screen flex">
      <Sidebar page={page} onChange={setPage} />
      <div className="flex-1 flex flex-col min-w-0">
        <TopBar page={page} onChange={setPage} />
        <main className="flex-1 overflow-y-auto p-6 max-w-6xl">
          {page === "dashboard" && <DashboardPage onNavigate={setPage} />}
          {page === "strategies" && <StrategiesPage />}
          {page === "strategy-builder" && <StrategyBuilderPage />}
          {page === "deployments" && <DeploymentsPage />}
          {page === "fundamentals" && <FundamentalsPage />}
          {page === "signals" && <SignalsPage />}
          {page === "scanner" && <ScannerPage />}
          {page === "news-events" && <NewsEventsPage />}
          {page === "quant" && <QuantPage />}
          {page === "backtest" && <BacktestPage />}
          {page === "marketplace" && <MarketplacePage />}
          {page === "ai-copilot" && <AiCopilotPage />}
          {page === "option-chain" && <OptionChainPage />}
          {page === "instruments" && <InstrumentsPage />}
          {page === "positions" && <PositionsPage />}
          {page === "portfolio" && <PortfolioPage />}
          {page === "orders" && <OrdersPage />}
          {page === "analytics" && <AnalyticsPage />}
          {page === "coach" && <CoachGuidePage />}
          {page === "risk-management" && <RiskManagementPage />}
          {page === "settings" && <SettingsPage />}
          {page === "system-logs" && <SystemLogsPage />}
          {page === "team" && <TeamPage />}
          {page === "admin" && <AdminPage />}
          {page === "notifications" && <NotificationsPage />}
          {page === "account" && <AccountPage />}
        </main>
      </div>
    </div>
  );
}

export default function App() {
  // `?chart=SYMBOL` is a chart opened in its own browser tab (a chart's "New tab" button): just the chart, no shell.
  const params = new URLSearchParams(window.location.search);
  return (
    <AuthProvider>
      {params.has("chart") ? <ChartWindow params={params} /> : <AppShell />}
    </AuthProvider>
  );
}
