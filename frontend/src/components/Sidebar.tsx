import { useAuth } from "../auth/AuthContext";

export type Page =
  | "dashboard"
  | "strategies"
  | "strategy-builder"
  | "fundamentals"
  | "signals"
  | "backtest"
  | "option-chain"
  | "positions"
  | "portfolio"
  | "orders"
  | "analytics"
  | "risk-management"
  | "settings"
  | "system-logs"
  | "notifications"
  | "account";

const NAV: { id: Page; label: string; icon: string }[] = [
  { id: "dashboard", label: "Dashboard", icon: "▦" },
  { id: "strategies", label: "Strategy Library", icon: "⚙" },
  { id: "strategy-builder", label: "Strategy Builder", icon: "✎" },
  { id: "fundamentals", label: "Fundamental Analysis", icon: "🏢" },
  { id: "signals", label: "Signals", icon: "⚡" },
  { id: "backtest", label: "Backtesting", icon: "⏱" },
  { id: "option-chain", label: "Option Chain", icon: "◉" },
  { id: "positions", label: "Positions", icon: "▤" },
  { id: "portfolio", label: "Portfolio", icon: "◫" },
  { id: "orders", label: "Orders", icon: "☰" },
  { id: "analytics", label: "Analytics", icon: "▲" },
  { id: "risk-management", label: "Risk Management", icon: "⚠" },
  { id: "settings", label: "Settings", icon: "⚙" },
  { id: "system-logs", label: "System Logs", icon: "▥" },
  { id: "notifications", label: "Notifications", icon: "🔔" },
];

export default function Sidebar({ page, onChange }: { page: Page; onChange: (p: Page) => void }) {
  const { user, loading } = useAuth();

  return (
    <aside className="w-56 shrink-0 border-r border-border bg-panel flex flex-col">
      <div className="px-4 py-5 border-b border-border">
        <div className="text-sm font-semibold tracking-wide text-slate-100">Advance Trading</div>
        <div className="text-xs text-muted">Platform Console</div>
      </div>
      <nav className="flex-1 py-3">
        {NAV.map((item) => (
          <button
            key={item.id}
            onClick={() => onChange(item.id)}
            className={`w-full flex items-center gap-3 px-4 py-2.5 text-sm text-left transition-colors ${
              page === item.id
                ? "bg-panel2 text-slate-50 border-r-2 border-accent"
                : "text-muted hover:text-slate-200 hover:bg-panel2/50"
            }`}
          >
            <span className="text-base">{item.icon}</span>
            {item.label}
          </button>
        ))}
      </nav>
      <button
        onClick={() => onChange("account")}
        className={`px-4 py-3 border-t border-border text-left text-xs transition-colors ${
          page === "account" ? "bg-panel2 text-slate-100" : "text-muted hover:text-slate-200 hover:bg-panel2/50"
        }`}
      >
        {loading ? (
          "…"
        ) : user ? (
          <>
            <span className="text-accent">●</span> {user.email}
          </>
        ) : (
          <>
            <span className="text-muted">●</span> Not signed in - click to log in
          </>
        )}
        <div className="mt-1 text-[11px] leading-relaxed opacity-80">
          Paper mode only. Live trading requires an authenticated broker.
        </div>
      </button>
    </aside>
  );
}
