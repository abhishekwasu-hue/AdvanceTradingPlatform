import {
  GraduationCap,
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
  ShieldEllipsis,
  UserCircle2,
  Users,
  Wand2,
  Zap,
  type LucideIcon, Store, Sparkles, Sigma} from "lucide-react";
import { useEffect } from "react";
import { Link } from "react-router";
import { useAuth } from "../auth/AuthContext";
import { pathFor, preloadPage } from "../routes";
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
  | "quant"
  | "backtest"
  | "marketplace"
  | "ai-copilot"
  | "coach"
  | "option-chain"
  | "instruments"
  | "positions"
  | "portfolio"
  | "orders"
  | "analytics"
  | "risk-management"
  | "settings"
  | "team"
  | "admin"
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
      { id: "ai-copilot", label: "AI Copilot", icon: Sparkles },
      { id: "deployments", label: "Autopilot", icon: Bot },
      { id: "backtest", label: "Backtesting", icon: History },
      { id: "marketplace", label: "Marketplace", icon: Store },
    ],
  },
  {
    title: "Research",
    items: [
      { id: "fundamentals", label: "Fundamental Analysis", icon: Building2 },
      { id: "option-chain", label: "Option Chain", icon: Link2 },
      { id: "instruments", label: "Instruments", icon: Boxes },
      { id: "news-events", label: "News & Events", icon: Newspaper },
      { id: "quant", label: "Factor Lab", icon: Sigma },
    ],
  },
  {
    title: "Portfolio",
    items: [
      { id: "positions", label: "Positions", icon: ListChecks },
      { id: "portfolio", label: "Portfolio", icon: Briefcase },
      { id: "orders", label: "Orders", icon: ClipboardList },
      { id: "analytics", label: "Analytics", icon: BarChart3 },
      { id: "coach", label: "Coach & Guide", icon: GraduationCap },
      { id: "risk-management", label: "Risk Management", icon: ShieldAlert },
    ],
  },
  {
    title: "System",
    items: [
      { id: "settings", label: "Settings", icon: SettingsIcon },
      { id: "team", label: "Team", icon: Users },
      { id: "system-logs", label: "System Logs", icon: ScrollText },
      { id: "notifications", label: "Notifications", icon: Bell },
    ],
  },
];

// Shown only to platform administrators (SUPER_ADMIN) - see Sidebar below.
export const ADMIN_GROUP: NavGroup = {
  title: "Platform",
  items: [{ id: "admin", label: "Admin Console", icon: ShieldEllipsis }],
};

export const NAV: NavItem[] = [...NAV_GROUPS, ADMIN_GROUP].flatMap((g) => g.items);

/** P0.9: below the md breakpoint the sidebar is an off-canvas drawer (opened from the top bar's menu button) so the
 * page keeps the full phone width; from md up it is the usual fixed column. */
/** P1.1: the entries are real links (open in a new tab, copy the address, back/forward), and hovering or focusing one
 * starts loading that page's code. */
export default function Sidebar({ page, open = false, onClose }: { page: Page | null; open?: boolean; onClose?: () => void }) {
  const { user, loading } = useAuth();
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose?.(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <>
    {open && <div className="fixed inset-0 z-30 bg-black/60 md:hidden" onClick={onClose} aria-hidden="true" />}
    {/* Closed on a phone it is also `invisible`, so its links leave the tab order; from md up it is always visible. */}
    <aside id="app-navigation" aria-label="Navigation"
           className={`fixed inset-y-0 left-0 z-40 w-60 shrink-0 transform border-r border-border bg-surface-1 flex flex-col transition-transform md:visible md:static md:translate-x-0 ${open ? "visible translate-x-0" : "invisible -translate-x-full"}`}>
      <div className="px-4 py-4 border-b border-border">
        <Logo />
      </div>
      <nav className="flex-1 overflow-y-auto py-3 space-y-4">
        {[...NAV_GROUPS, ...(user?.role === "SUPER_ADMIN" ? [ADMIN_GROUP] : [])].map((group) => (
          <div key={group.title}>
            <div className="px-4 mb-1 text-[10px] font-bold uppercase tracking-wider text-fg-muted">
              {group.title}
            </div>
            {group.items.map((item) => {
              const Icon = item.icon;
              const active = page === item.id;
              return (
                <Link
                  key={item.id}
                  to={pathFor(item.id)}
                  onClick={() => onClose?.()}
                  onMouseEnter={() => preloadPage(item.id)}
                  onFocus={() => preloadPage(item.id)}
                  aria-current={active ? "page" : undefined}
                  className={`w-full flex items-center gap-3 px-4 py-2 text-sm text-left transition-colors border-r-2 ${
                    active
                      ? "bg-brand/10 text-fg font-semibold border-brand"
                      : "text-fg-muted font-medium border-transparent hover:text-fg hover:bg-surface-2"
                  }`}
                >
                  <Icon size={16} strokeWidth={2} className={active ? "text-brand" : "text-fg-muted"} />
                  {item.label}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>
      <Link
        to={pathFor("account")}
        onClick={() => onClose?.()}
        aria-current={page === "account" ? "page" : undefined}
        className={`block px-4 py-3 border-t border-border text-left text-xs transition-colors ${
          page === "account" ? "bg-surface-2 text-fg" : "text-fg-muted hover:text-fg hover:bg-surface-2"
        }`}
      >
        <div className="flex items-center gap-2">
          <UserCircle2 size={18} className={user ? "text-up" : "text-fg-muted"} />
          <span className="truncate">{loading ? "…" : user ? user.email : "Not signed in - click to log in"}</span>
        </div>
        <div className="mt-1.5 flex flex-wrap gap-1">
          {user && (
            <span className="inline-flex items-center rounded-full bg-surface-2 border border-border px-2 py-0.5 text-[10px] font-medium text-fg">
              {user.role.replace("_", " ")}
            </span>
          )}
          <span className="inline-flex items-center gap-1 rounded-full bg-warn/10 border border-warn/30 px-2 py-0.5 text-[10px] font-medium text-warn">
            PAPER MODE
          </span>
        </div>
      </Link>
    </aside>
    </>
  );
}
