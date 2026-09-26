import {
  BarChart3,
  Bell,
  Bot,
  Boxes,
  Briefcase,
  Building2,
  ClipboardList,
  History,
  LayoutDashboard,
  Link2,
  ListChecks,
  ListTree,
  Newspaper,
  Radar,
  ScrollText,
  Settings as SettingsIcon,
  ShieldAlert,
  UserCircle2,
  Wand2,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { useAuth } from "../auth/AuthContext";
import Logo from "./Logo";

export type Page =
  | "dashboard"
  | "strategies"
  | "strategy-builder"
  | "deployments"
  | "fundamentals"
  | "signals"
  | "scanner"
  | "news-events"
  | "backtest"
  | "option-chain"
  | "instruments"
  | "positions"
  | "portfolio"
  | "orders"
  | "analytics"
  | "risk-management"
  | "settings"
  | "system-logs"
  | "notifications"
  | "account";

interface NavItem {
  id: Page;
  label: string;
  icon: LucideIcon;
}

interface NavGroup {
  title: string;
  items: NavItem[];
}

export const NAV_GROUPS: NavGroup[] = [
  { title: "Overview", items: [{ id: "dashboard", label: "Dashboard", icon: LayoutDashboard }] },
  {
    title: "Trade",
    items: [
      { id: "signals", label: "Signals", icon: Zap },
      { id: "scanner", label: "Market Scanner", icon: Radar },
      { id: "strategies", label: "Strategy Library", icon: ListTree },
      { id: "strategy-builder", label: "Strategy Builder", icon: Wand2 },
      { id: "deployments", label: "Autopilot", icon: Bot },
      { id: "backtest", label: "Backtesting", icon: History },
    ],
  },
  {
    title: "Research",
    items: [
      { id: "fundamentals", label: "Fundamental Analysis", icon: Building2 },
      { id: "option-chain", label: "Option Chain", icon: Link2 },
      { id: "instruments", label: "Instruments", icon: Boxes },
      { id: "news-events", label: "News & Events", icon: Newspaper },
    ],
  },
  {
    title: "Portfolio",
    items: [
      { id: "positions", label: "Positions", icon: ListChecks },
      { id: "portfolio", label: "Portfolio", icon: Briefcase },
      { id: "orders", label: "Orders", icon: ClipboardList },
      { id: "analytics", label: "Analytics", icon: BarChart3 },
      { id: "risk-management", label: "Risk Management", icon: ShieldAlert },
    ],
  },
  {
    title: "System",
    items: [
      { id: "settings", label: "Settings", icon: SettingsIcon },
      { id: "system-logs", label: "System Logs", icon: ScrollText },
      { id: "notifications", label: "Notifications", icon: Bell },
    ],
  },
];

export const NAV: NavItem[] = NAV_GROUPS.flatMap((g) => g.items);

export default function Sidebar({ page, onChange }: { page: Page; onChange: (p: Page) => void }) {
  const { user, loading } = useAuth();

  return (
    <aside className="w-60 shrink-0 border-r border-border bg-panel flex flex-col">
      <div className="px-4 py-4 border-b border-border">
        <Logo />
      </div>
      <nav className="flex-1 overflow-y-auto py-3 space-y-4">
        {NAV_GROUPS.map((group) => (
          <div key={group.title}>
            <div className="px-4 mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted/70">
              {group.title}
            </div>
            {group.items.map((item) => {
              const Icon = item.icon;
              const active = page === item.id;
              return (
                <button
                  key={item.id}
                  onClick={() => onChange(item.id)}
                  className={`w-full flex items-center gap-3 px-4 py-2 text-sm text-left transition-colors border-r-2 ${
                    active
                      ? "bg-panel2 text-slate-50 border-brand"
                      : "text-muted border-transparent hover:text-slate-200 hover:bg-panel2/50"
                  }`}
                >
                  <Icon size={16} strokeWidth={2} className={active ? "text-brand" : ""} />
                  {item.label}
                </button>
              );
            })}
          </div>
        ))}
      </nav>
      <button
        onClick={() => onChange("account")}
        className={`px-4 py-3 border-t border-border text-left text-xs transition-colors ${
          page === "account" ? "bg-panel2 text-slate-100" : "text-muted hover:text-slate-200 hover:bg-panel2/50"
        }`}
      >
        <div className="flex items-center gap-2">
          <UserCircle2 size={18} className={user ? "text-accent" : "text-muted"} />
          <span className="truncate">{loading ? "…" : user ? user.email : "Not signed in - click to log in"}</span>
        </div>
        <div className="mt-1.5 inline-flex items-center gap-1 rounded-full bg-warn/10 border border-warn/30 px-2 py-0.5 text-[10px] font-medium text-warn">
          PAPER MODE
        </div>
      </button>
    </aside>
  );
}
