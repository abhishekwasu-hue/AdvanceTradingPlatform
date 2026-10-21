import { Bell, LifeBuoy, MessageSquarePlus, Search, TriangleAlert } from "lucide-react";
import { Link } from "react-router";
import { useMarketStatus } from "../chart/useMarketStatus";
import { ThemeToggle } from "../components/AppearanceCard";
import type { SystemStatus, UserResponse } from "../types";
import { currentPlatform, shortcutLabel } from "./CommandBarV2";
import { initialsOf } from "./SidebarV2";

/**
 * UI2-1 (UI v2 proposal §9 rule 3, mockups dashboard-desktop-1440 / nav-bottom-mobile): the v2 top bar.
 * - Desktop: the page title, the search box that opens the command bar (Ctrl K / Cmd K), then Help (flag
 *   `support_bot`), feedback, theme, notifications and the account avatar.
 * - Phone: the app mark and the page title, then search, notifications and the avatar (44 px each); help, feedback
 *   and the theme move to the More sheet.
 * Notices sit in one row under the bar, in plain words (§3 R18): maintenance and LIVE paused are warnings (amber,
 * with an icon); "Verify your email" is a neutral link.
 */

const iconButton = "flex h-11 w-11 items-center justify-center rounded-control text-fg-muted transition-colors duration-fast hover:bg-surface-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:h-9 md:w-9";

export interface TopBarV2Props {
  title: string;
  user: UserResponse | null;
  notificationsActive: boolean;
  accountActive: boolean;
  accountPath: string;
  onSearch: () => void;
  /** Present while Help is on for this organisation (flag `support_bot`). */
  onHelp?: () => void;
  onFeedback?: () => void;
}

/** The notices under the top bar, in plain words. */
export function topBarNotices(status: SystemStatus | null, user: UserResponse | null): { kind: "warn" | "link"; text: string; title?: string }[] {
  const out: { kind: "warn" | "link"; text: string; title?: string }[] = [];
  if (status?.maintenance_mode) {
    out.push({ kind: "warn", text: `Maintenance: new trades are paused${status.maintenance_message ? `. ${status.maintenance_message}` : ""}` });
  }
  if (status && status.disabled_brokers.length > 0) out.push({ kind: "warn", text: `Live trading is paused on ${status.disabled_brokers.join(", ")}` });
  if (user && user.email_verified === false) out.push({ kind: "link", text: "Verify your email", title: "Live trading and broker keys may need a verified address" });
  return out;
}

export default function TopBarV2({ title, user, notificationsActive, accountActive, accountPath, onSearch, onHelp, onFeedback }: TopBarV2Props) {
  // UI2-4e: the one shared status read (the Charts page and the charts on other pages read the same).
  const { status } = useMarketStatus();
  const notices = topBarNotices(status, user);
  const shortcut = shortcutLabel(currentPlatform());

  return (
    <header className="shrink-0 border-b border-subtle bg-surface-1 pt-[env(safe-area-inset-top)]">
      <div className="flex h-14 items-center gap-space-3 pl-[max(1rem,env(safe-area-inset-left))] pr-[max(0.5rem,env(safe-area-inset-right))] md:px-space-8">
        <Link to="/" aria-label="AiAlgoDesk - Dashboard" className="shrink-0 rounded-control focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:hidden">
          {/* A text monogram, not the logo SVG: the sidebar's (hidden on phones) owns the logo's gradient id. */}
          <span aria-hidden className="flex h-7 w-7 items-center justify-center rounded-control bg-fg text-t12 font-bold text-surface">AT</span>
        </Link>
        <span className="min-w-0 shrink truncate text-t18 font-semibold text-fg md:text-t15">{title}</span>
        <button type="button" onClick={onSearch} aria-label={`Search pages (${shortcut})`} aria-haspopup="dialog"
          className="ml-space-4 hidden h-9 w-full max-w-[420px] items-center gap-space-2 rounded-control border border-border bg-surface-2 px-space-3 text-t14 text-fg-muted transition-colors duration-fast hover:bg-surface-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:flex">
          <Search size={16} aria-hidden className="shrink-0" />
          <span className="flex-1 truncate text-left">Search pages</span>
          <kbd className="shrink-0 rounded-control border border-border bg-surface-1 px-space-1 text-t12 text-fg-muted">{shortcut}</kbd>
        </button>
        <div className="ml-auto flex shrink-0 items-center gap-space-1">
          <button type="button" onClick={onSearch} aria-label="Search pages" aria-haspopup="dialog" className={`${iconButton} md:hidden`}>
            <Search size={20} aria-hidden />
          </button>
          {onHelp && (
            <button type="button" onClick={onHelp} title="Help" aria-label="Help" className={`${iconButton} hidden md:flex`}>
              <LifeBuoy size={18} aria-hidden />
            </button>
          )}
          {onFeedback && (
            <button type="button" onClick={onFeedback} title="Send feedback" aria-label="Send feedback" className={`${iconButton} hidden md:flex`}>
              <MessageSquarePlus size={18} aria-hidden />
            </button>
          )}
          <span className="hidden md:contents"><ThemeToggle /></span>
          <Link to="/notifications" title="Notifications" aria-label="Notifications" aria-current={notificationsActive ? "page" : undefined}
            className={`${iconButton} ${notificationsActive ? "bg-surface-2 text-brand" : ""}`}>
            <Bell size={20} aria-hidden />
          </Link>
          <Link to={accountPath} title={user ? "Account" : "Sign in"} aria-label={user ? "Account" : "Sign in"} aria-current={accountActive ? "page" : undefined}
            className="flex h-11 w-11 items-center justify-center rounded-pill focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:h-9 md:w-9">
            <span aria-hidden className={`flex h-8 w-8 items-center justify-center rounded-pill text-t12 font-semibold ${accountActive ? "bg-brand/10 text-brand" : "bg-surface-2 text-fg"}`}>
              {user ? initialsOf(user.email) : "?"}
            </span>
          </Link>
        </div>
      </div>
      {notices.length > 0 && (
        <div className="flex flex-wrap gap-space-2 border-t border-subtle px-space-4 py-space-2 md:px-space-8">
          {notices.map((n) => n.kind === "warn" ? (
            <span key={n.text} role="status" className="inline-flex items-center gap-space-1 rounded-pill bg-warn-tint px-space-2 py-px text-t12 font-medium text-warn">
              <TriangleAlert size={12} aria-hidden /> {n.text}
            </span>
          ) : (
            <Link key={n.text} to={accountPath} title={n.title}
              className="inline-flex items-center rounded-pill border border-border px-space-2 py-px text-t12 font-medium text-fg hover:bg-surface-2">
              {n.text}
            </Link>
          ))}
        </div>
      )}
    </header>
  );
}
