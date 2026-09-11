import { useState } from "react";
import { AuthProvider } from "./auth/AuthContext";
import Sidebar, { type Page } from "./components/Sidebar";
import AccountPage from "./pages/AccountPage";
import BacktestPage from "./pages/BacktestPage";
import DashboardPage from "./pages/DashboardPage";
import OptionChainPage from "./pages/OptionChainPage";
import PositionsPage from "./pages/PositionsPage";
import SignalsPage from "./pages/SignalsPage";
import StrategiesPage from "./pages/StrategiesPage";

export default function App() {
  const [page, setPage] = useState<Page>("dashboard");

  return (
    <AuthProvider>
      <div className="min-h-screen flex">
        <Sidebar page={page} onChange={setPage} />
        <main className="flex-1 overflow-y-auto p-6 max-w-6xl">
          {page === "dashboard" && <DashboardPage />}
          {page === "strategies" && <StrategiesPage />}
          {page === "signals" && <SignalsPage />}
          {page === "backtest" && <BacktestPage />}
          {page === "option-chain" && <OptionChainPage />}
          {page === "positions" && <PositionsPage />}
          {page === "account" && <AccountPage />}
        </main>
      </div>
    </AuthProvider>
  );
}
