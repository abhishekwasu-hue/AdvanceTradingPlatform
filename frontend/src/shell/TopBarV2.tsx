import { Bell, CircleHelp, LifeBuoy, MessageSquarePlus, Search, TriangleAlert } from "lucide-react";
import { useLayoutEffect, useRef, useState, type RefObject } from "react";
import { Link } from "react-router";
import { useMarketStatus } from "../chart/useMarketStatus";
import { ThemeToggle } from "../components/AppearanceCard";
import type { SystemStatus, UserResponse } from "../types";
import { currentPlatform, shortcutLabel } from "./CommandBarV2";
import { initialsOf } from "./SidebarV2";
import { navItemFor, type ShellKey } from "./navModel";
import { usePageHelp } from "./pageHelp";
import { HelpPopover } from "../ui/HelpPopover";
import { sectionChipClass, sectionTintOf } from "../ui/accents";
import { useColourV2 } from "../ui/colourV2";

/**
 * UI2-1 (UI v2 proposal §9 rule 3, mockups dashboard-desktop-1440 / nav-bottom-mobile): the v2 top bar.
 * - Desktop: the page title, the search box that opens the command bar (Ctrl K / Cmd K), then Help (flag
 *   `support_bot`), feedback, theme, notifications and the account avatar.
 * - Phone: the app mark and the page title, then search, notifications and the avatar (44 px each); help, feedback
 *   and the theme move to the More sheet.
 * MO2 part C: a "?" right after the page title replays that page's tips (`onTour`: only on a page that has tips, once
 * the tips' state is known).
 * UI-H CA7 (owner, 25 Oct): on a phone, with colour v2 on, the page's section chip (CA3: its nav icon on its section's
 * round tint, the same map as the sidebar) sits right before the title, as the sidebar shows it on desktop. The title
 * is never cut on a phone (H9): it wraps to a second line and the bar grows; from md up it keeps to one line (with
 * colour v2 off the bar is exactly as before).
 * Notices sit in one row under the bar, in plain words (§3 R18): maintenance and LIVE paused are warnings (amber,
 * with an icon). P3: on the front page in PAPER (`paperOnly`) the LIVE-paused notice is left out; the shell also passes
 * `paperOnly` on every page while `phase0_paper_only` is on (user-friendly pass item 2: no LIVE wording in the paper
 * phase).
 * User-friendly pass item 1 (UX audit M13): "Verify your email" is never a chip on every page; it is one row in the
 * Dashboard's Needs attention list (with Resend) and the Email verification card in Settings › Account.
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
  /** P3: the front page in PAPER (no LIVE deployment, src/setup/paperOnly.ts), or the paper phase: no LIVE notice. */
  paperOnly?: boolean;
  /** MO2 part C: replays this page's tips (present only on a page that has them). */
  onTour?: () => void;
  /** UI-H CA7: the page's navigation key - its section chip beside the title on a phone (colour v2). */
  section?: ShellKey | null;
}

/** The notices under the top bar, in plain words. `paperOnly` leaves out the LIVE ones. Never an e-mail chip (item 1). */
export function topBarNotices(status: SystemStatus | null, _user: UserResponse | null, paperOnly = false): { kind: "warn" | "link"; text: string; title?: string }[] {
  const out: { kind: "warn" | "link"; text: string; title?: string }[] = [];
  if (status?.maintenance_mode) {
    out.push({ kind: "warn", text: `Maintenance: new trades are paused${status.maintenance_message ? `. ${status.maintenance_message}` : ""}` });
  }
  if (paperOnly) return out;
  if (status && status.disabled_brokers.length > 0) out.push({ kind: "warn", text: `Live trading is paused on ${status.disabled_brokers.join(", ")}` });
  return out;
}

/**
 * H9: a title word is never cut, so on a narrow phone a long one-word title ("Strategies" at 360 px) can push the bar
 * past the screen edge. Then the title steps down one text size (t18 -> t15); it steps back up when there is room.
 * Measured, not guessed from the number of letters; only on a phone with colour v2 (the row's own overflow).
 */
export function useTitleFits(row: RefObject<HTMLElement | null>, title: string, enabled: boolean): boolean {
  const [small, setSmall] = useState(false);
  useLayoutEffect(() => {
    if (!enabled) { setSmall(false); return; }
    const check = () => {
      const el = row.current;
      if (!el) return;
      const phone = window.matchMedia?.("(max-width: 767.98px)").matches ?? false;
      if (!phone) { setSmall(false); return; }
      // Measure at the normal size first, so a wider screen (rotation) gets the larger title back.
      el.dataset.titleFit = "measure";
      const overflows = el.scrollWidth > el.clientWidth;
      delete el.dataset.titleFit;
      setSmall(overflows);
    };
    check();
    // The web font arrives after the first paint and changes the title's width: measure again then.
    let live = true;
    void document.fonts?.ready.then(() => { if (live) check(); });
    window.addEventListener("resize", check);
    return () => { live = false; window.removeEventListener("resize", check); };
  }, [row, title, enabled]);
  return small;
}

export default function TopBarV2({ title, user, notificationsActive, accountActive, accountPath, onSearch, onHelp, onFeedback, paperOnly = false, onTour, section = null }: TopBarV2Props) {
  const colour = useColourV2();
  const tint = colour ? sectionTintOf(section) : null;
  const SectionIcon = tint ? navItemFor(section)?.icon : undefined;
  // UI2-4e: the one shared status read (the Charts page and the charts on other pages read the same).
  const { status } = useMarketStatus();
  const notices = topBarNotices(status, user, paperOnly);
  const shortcut = shortcutLabel(currentPlatform());
  const row = useRef<HTMLDivElement>(null);
  const small = useTitleFits(row, title, colour);
  const help = usePageHelp();

  return (
    <header className="shrink-0 border-b border-subtle bg-surface-1 pt-[env(safe-area-inset-top)]">
      <div ref={row} className={`group/bar flex ${colour ? "min-h-14" : "h-14"} items-center gap-space-3 pl-[max(1rem,env(safe-area-inset-left))] pr-[max(0.5rem,env(safe-area-inset-right))] md:px-space-8`}>
        <Link to="/" aria-label="AiAlgoDesk - Dashboard" className="shrink-0 rounded-control focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:hidden">
          {/* A text monogram, not the logo SVG: the sidebar's (hidden on phones) owns the logo's gradient id. */}
          <span aria-hidden className="flex h-7 w-7 items-center justify-center rounded-control bg-fg text-t12 font-bold text-surface">AT</span>
        </Link>
        {tint && SectionIcon && (
          <span aria-hidden data-section-chip={tint} data-topbar-chip className={`${sectionChipClass(tint, "sm")} md:hidden`}><SectionIcon size={16} /></span>
        )}
        {/* With colour v2 (CA7) the title wraps on a phone instead of being cut (H9): only between words, never inside one
            (no min-w-0 there, so the longest word sets its width); with it off the bar is as before. */}
        <span data-topbar-title data-title-size={small ? "small" : undefined}
          className={`shrink ${small ? "text-t15 group-data-[title-fit=measure]/bar:text-t18" : "text-t18"} font-semibold text-fg md:text-t15 ${colour ? "py-space-1 leading-tight [overflow-wrap:normal] [word-break:normal] md:min-w-0 md:truncate md:py-0" : "min-w-0 truncate"}`}>{title}</span>
        {/* UX-6: the page's help line (and its tips) behind one (?) beside the title - never a paragraph above the data. */}
        {help && (
          <HelpPopover title={title} className="-ml-space-1 shrink-0">
            <p data-page-help={help.id}>{help.lines.join(" ")}</p>
            {help.showMe && <a href={help.showMe.href} className="inline-flex min-h-11 items-center font-semibold text-brand hover:underline md:min-h-0">{help.showMe.label ?? "Show me"}</a>}
            {onTour && (
              <button type="button" onClick={onTour} data-tour="tour-replay"
                className="inline-flex min-h-11 items-center font-semibold text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-0">
                Show the tips for this page
              </button>
            )}
          </HelpPopover>
        )}
        {!help && onTour && (
          <button type="button" onClick={onTour} title="Tips for this page" aria-label="Show the tips for this page" data-tour="tour-replay"
            className={`${iconButton} -ml-space-2 shrink-0`}>
            <CircleHelp size={18} aria-hidden />
          </button>
        )}
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
