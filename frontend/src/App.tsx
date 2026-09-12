import { useState } from "react";
import { AuthProvider } from "./auth/AuthContext";
import Sidebar, { type Page } from "./components/Sidebar";
import AccountPage from "./pages/AccountPage";
import AnalyticsPage from "./pages/AnalyticsPage";
import BacktestPage from "./pages/BacktestPage";
import DashboardPage from "./pages/DashboardPage";
import OptionChainPage from "./pages/OptionChainPage";
import OrdersPage from "./pages/OrdersPage";
import PortfolioPage from "./pages/PortfolioPage";
import PositionsPage from "./pages/PositionsPage";
import RiskManagementPage from "./pages/RiskManagementPage";
import SettingsPage from "./pages/SettingsPage";
import SignalsPage from "./pages/SignalsPage";
import StrategiesPage from "./pages/StrategiesPage";
import StrategyBuilderPage from "./pages/StrategyBuilderPage";
import SystemLogsPage from "./pages/SystemLogsPage";

export default function App() {
  const [page, setPage] = useState<Page>("dashboard");

  return (
    <AuthProvider>
      <div className="min-h-screen flex">
        <Sidebar page={page} onChange={setPage} />
        <main className="flex-1 overflow-y-auto p-6 max-w-6xl">
          {page === "dashboard" && <DashboardPage />}
          {page === "strategies" && <StrategiesPage />}
          {page === "strategy-builder" && <StrategyBuilderPage />}
          {page === "signals" && <SignalsPage />}
          {page === "backtest" && <BacktestPage />}
          {page === "option-chain" && <OptionChainPage />}
          {page === "positions" && <PositionsPage />}
          {page === "portfolio" && <PortfolioPage />}
          {page === "orders" && <OrdersPage />}
          {page === "analytics" && <AnalyticsPage />}
          {page === "risk-management" && <RiskManagementPage />}
          {page === "settings" && <SettingsPage />}
          {page === "system-logs" && <SystemLogsPage />}
          {page === "account" && <AccountPage />}
        </main>
      </div>
    </AuthProvider>
  );
}
