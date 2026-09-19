import { Bell, UserCircle2 } from "lucide-react";
import { useState } from "react";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import Sidebar, { NAV, type Page } from "./components/Sidebar";
import AccountPage from "./pages/AccountPage";
import AnalyticsPage from "./pages/AnalyticsPage";
import BacktestPage from "./pages/BacktestPage";
import DashboardPage from "./pages/DashboardPage";
import FundamentalsPage from "./pages/FundamentalsPage";
import InstrumentsPage from "./pages/InstrumentsPage";
import NewsEventsPage from "./pages/NewsEventsPage";
import NotificationsPage from "./pages/NotificationsPage";
import OptionChainPage from "./pages/OptionChainPage";
import OrdersPage from "./pages/OrdersPage";
import PortfolioPage from "./pages/PortfolioPage";
import PositionsPage from "./pages/PositionsPage";
import RiskManagementPage from "./pages/RiskManagementPage";
import ScannerPage from "./pages/ScannerPage";
import SettingsPage from "./pages/SettingsPage";
import SignalsPage from "./pages/SignalsPage";
import StrategiesPage from "./pages/StrategiesPage";
import StrategyBuilderPage from "./pages/StrategyBuilderPage";
import SystemLogsPage from "./pages/SystemLogsPage";

function TopBar({ page, onChange }: { page: Page; onChange: (p: Page) => void }) {
  const { user } = useAuth();
  const title = page === "account" ? "Account" : NAV.find((n) => n.id === page)?.label ?? "";

  return (
    <header className="h-14 shrink-0 border-b border-border bg-panel/80 backdrop-blur flex items-center justify-between px-6">
      <h1 className="text-[15px] font-semibold text-slate-100">{title}</h1>
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

function AppShell() {
  const [page, setPage] = useState<Page>("dashboard");

  return (
    <div className="min-h-screen flex">
      <Sidebar page={page} onChange={setPage} />
      <div className="flex-1 flex flex-col min-w-0">
        <TopBar page={page} onChange={setPage} />
        <main className="flex-1 overflow-y-auto p-6 max-w-6xl">
          {page === "dashboard" && <DashboardPage />}
          {page === "strategies" && <StrategiesPage />}
          {page === "strategy-builder" && <StrategyBuilderPage />}
          {page === "fundamentals" && <FundamentalsPage />}
          {page === "signals" && <SignalsPage />}
          {page === "scanner" && <ScannerPage />}
          {page === "news-events" && <NewsEventsPage />}
          {page === "backtest" && <BacktestPage />}
          {page === "option-chain" && <OptionChainPage />}
          {page === "instruments" && <InstrumentsPage />}
          {page === "positions" && <PositionsPage />}
          {page === "portfolio" && <PortfolioPage />}
          {page === "orders" && <OrdersPage />}
          {page === "analytics" && <AnalyticsPage />}
          {page === "risk-management" && <RiskManagementPage />}
          {page === "settings" && <SettingsPage />}
          {page === "system-logs" && <SystemLogsPage />}
          {page === "notifications" && <NotificationsPage />}
          {page === "account" && <AccountPage />}
        </main>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AppShell />
    </AuthProvider>
  );
}
