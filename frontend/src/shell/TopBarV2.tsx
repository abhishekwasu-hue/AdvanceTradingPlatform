import { Bell, CircleHelp, LifeBuoy, MessageSquarePlus, PanelLeft, PanelLeftClose, Search, Sparkles, TriangleAlert } from "lucide-react";
import { useLayoutEffect, useRef, useState, type RefObject } from "react";
import { Link } from "react-router";
import { useMarketStatus } from "../chart/useMarketStatus";
import { ThemeToggle } from "../components/AppearanceCard";
import type { SystemStatus, UserResponse } from "../types";
import { COMMAND_HINT, COMMAND_LABEL, currentPlatform, shortcutLabel } from "./CommandBarV2";
import { initialsOf } from "./SidebarV2";
import { navItemFor, type ShellKey } from "./navModel";
import { usePageHelp, usePageSubtitle } from "./pageHelp";
import { HelpPopover } from "../ui/HelpPopover";
import { sectionChipClass, sectionTintOf } from "../ui/accents";
import { useColourV2 } from "../ui/colourV2";
import { useFeatures } from "../ui/useFeatures";
import { FeedDot } from "./FeedDot";
import { SIDEBAR_SHORTCUT, type SidebarMode } from "./sidebarPrefs";

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
 * CT-1 C10: the one "Ask AI" button (`onAskAi`, present while the page on screen has something to offer): an icon on a
 * phone, the icon and its name from md up; it opens the shell's Ask AI sheet (src/shell/AskAiSheetV2.tsx). The phone bar then leaves out the "AT" mark and the gaps between its icons, so a title such as "Option chain" fits.
 * User-friendly pass item 1 (UX audit M13): "Verify your email" is never a chip on every page; it is one row in the
 * Dashboard's Needs attention list (with Resend) and the Email verification card in Settings › Account.
 * M57 U7-1 / U7-2 (owner order 30 Oct): one menu button left of the title (`menu`) - from md up it hides the sidebar to
 * its icon rail and shows it again (shortcut `[`); on a phone it opens the full navigation as a sheet (it takes the "AT"
 * mark's place: Today is on the bottom bar). The title is never cut (#759 review): from md up it keeps to one line at
 * its full width and the search box gives way instead; on a phone #741's rule applies (one size down, then a wrap
 * between words).
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
  /** CT-1 C10: opens the one Ask AI sheet (absent: nothing to offer on this page). */
  onAskAi?: () => void;
  askAiOpen?: boolean;
  /** M57: the menu button - the sidebar's state (md and up) and the phone's navigation sheet. */
  menu?: { mode: SidebarMode; onToggle: () => void; onOpenSheet: () => void; sheetOpen: boolean };
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
 * H9 / REV-S5: the phone title stays whole on one line. Three steps, measured (not guessed from the number of letters),
 * only on a phone with colour v2: 0 = the normal size on one line; 1 = one text size down (t18 -> t15), still on one line,
 * when the bar would run past the screen edge; 2 = still too wide at the small size: the (?) moves under the title and,
 * only as the last resort, the title wraps between words (never inside one, never cut). It steps back up when there is
 * room (rotation). `extras`: what else sits in the row (PROSE-1: the (?) appears once the page has set its subtitle).
 * REV-1 M3 (#759): the row is measured again whenever one of its parts arrives or changes size (the Ask AI button, the
 * tips button, the price dot); #759's last step cut the title with "..." - here the last step wraps between words, so the
 * whole title always shows (M57 / #759 review).
 */
export type TitleFit = 0 | 1 | 2 | 3;
export function useTitleFits(row: RefObject<HTMLElement | null>, title: string, enabled: boolean, extras = ""): TitleFit {
  const [fit, setFit] = useState<TitleFit>(0);
  useLayoutEffect(() => {
    if (!enabled) { setFit(0); return; }
    const check = () => {
      const el = row.current;
      if (!el) return;
      const phone = window.matchMedia?.("(max-width: 767.98px)").matches ?? false;
      if (!phone) { setFit(0); return; }
      // Measure at the normal size first, then one size down, both on one line.
      const overflowsAt = (mode: string) => { el.dataset.titleFit = mode; return el.scrollWidth > el.clientWidth; };
      // M57 / #759 review: 3 = even wrapped between words the row would run past the edge (a 320 px phone with the menu
      // button, Ask AI, the price dot, search, bell and avatar): the title then takes a line of its own under the buttons -
      // never cut, and every button stays on screen.
      const next: TitleFit = !overflowsAt("measure") ? 0 : !overflowsAt("measure-small") ? 1 : !overflowsAt("measure-wrap") ? 2 : 3;
      delete el.dataset.titleFit;
      setFit(next);
    };
    check();
    // The web font arrives after the first paint and changes the title's width: measure again then.
    let live = true;
    void document.fonts?.ready.then(() => { if (live) check(); });
    window.addEventListener("resize", check);
    // REV-1 M3 (#759): a part of the row that arrives or changes size later (a button, the price dot) is measured too.
    const el = row.current;
    const sizes = typeof ResizeObserver === "function" ? new ResizeObserver(() => check()) : null;
    const watch = () => { if (el && sizes) { sizes.disconnect(); for (const child of Array.from(el.children)) sizes.observe(child); } };
    watch();
    const parts = el && typeof MutationObserver === "function" ? new MutationObserver(() => { watch(); check(); }) : null;
    if (el) parts?.observe(el, { childList: true });
    return () => { live = false; window.removeEventListener("resize", check); sizes?.disconnect(); parts?.disconnect(); };
  }, [row, title, enabled, extras]);
  return fit;
}

/** The title's classes per step (colour v2); the `measure*` modes force a size, on one line, while the row is measured. */
const TITLE_FIT_CLASS: Record<TitleFit, string> = {
  0: "text-t18 whitespace-nowrap",
  1: "text-t15 whitespace-nowrap",
  2: "text-t15",
  3: "text-t15",
};
const TITLE_MEASURE = "group-data-[title-fit=measure-wrap]/bar:text-t15 group-data-[title-fit=measure-wrap]/bar:whitespace-normal "
  + "group-data-[title-fit=measure]/bar:text-t18 group-data-[title-fit=measure]/bar:whitespace-nowrap "
  + "group-data-[title-fit=measure-small]/bar:text-t15 group-data-[title-fit=measure-small]/bar:whitespace-nowrap";
const GROUP_MEASURE = "group-data-[title-fit=measure]/bar:flex-nowrap group-data-[title-fit=measure-small]/bar:flex-nowrap "
  + "group-data-[title-fit=measure-wrap]/bar:flex-wrap group-data-[title-fit=measure]/bar:order-none "
  + "group-data-[title-fit=measure-small]/bar:order-none group-data-[title-fit=measure-wrap]/bar:order-none "
  + "group-data-[title-fit=measure]/bar:basis-auto group-data-[title-fit=measure-small]/bar:basis-auto group-data-[title-fit=measure-wrap]/bar:basis-auto";
/** Step 3: the row wraps so the title group sits on its own line under the buttons; never while the row is measured. */
const ROW_MEASURE = "data-[title-fit=measure]:flex-nowrap data-[title-fit=measure-small]:flex-nowrap data-[title-fit=measure-wrap]:flex-nowrap "
  + "data-[title-fit=measure]:py-0 data-[title-fit=measure-small]:py-0 data-[title-fit=measure-wrap]:py-0";

export default function TopBarV2({ title, user, notificationsActive, accountActive, accountPath, onSearch, onHelp, onFeedback, paperOnly = false, onTour, section = null, onAskAi, askAiOpen = false, menu }: TopBarV2Props) {
  const colour = useColourV2();
  const tint = colour ? sectionTintOf(section) : null;
  const SectionIcon = tint ? navItemFor(section)?.icon : undefined;
  // UI2-4e: the one shared status read (the Charts page and the charts on other pages read the same).
  const { status } = useMarketStatus();
  const notices = topBarNotices(status, user, paperOnly);
  const shortcut = shortcutLabel(currentPlatform());
  const row = useRef<HTMLDivElement>(null);
  const help = usePageHelp();
  // PROSE-1: the page's subtitle sits behind the same (?), first - never a line under the title.
  const subtitle = usePageSubtitle();
  const fit = useTitleFits(row, title, colour, `${help ? "h" : ""}${subtitle ? "s" : ""}${onTour ? "t" : ""}`);
  // UX-7: the price-feed dot, only with live prices on (flag `data_hub`) for a signed-in user.
  const liveOn = useFeatures().on("data_hub") && !!user;

  return (
    <header className="shrink-0 border-b border-subtle bg-surface-1 pt-[env(safe-area-inset-top)]">
      <div ref={row} data-title-step={colour ? fit : undefined} className={`group/bar flex ${colour ? `min-h-14 ${fit === 3 ? "flex-wrap gap-y-0 pb-space-2" : ""} ${ROW_MEASURE} md:flex-nowrap md:pb-0` : "h-14"} items-center gap-space-3 pl-[max(1rem,env(safe-area-inset-left))] pr-[max(0.5rem,env(safe-area-inset-right))] md:px-space-8`}>
        {/* CT-1 C10: on a phone the Ask AI button takes the mark's room (Today is in the bottom bar), so a title such as
            "Option chain" is not cut. */}
        {menu && (
          <>
            {/* M57 U7-1: from md up, hide the sidebar to its icon rail and show it again (the shortcut [ does the same). */}
            <button type="button" onClick={menu.onToggle} data-menu-toggle={menu.mode}
              aria-label={menu.mode === "expanded" ? "Hide menu" : "Show menu"} aria-keyshortcuts={SIDEBAR_SHORTCUT}
              aria-expanded={menu.mode === "expanded"}
              title={`${menu.mode === "expanded" ? "Hide menu" : "Show menu"} (${SIDEBAR_SHORTCUT})`}
              className={`${iconButton} -ml-space-2 hidden shrink-0 md:flex`}>
              {menu.mode === "expanded" ? <PanelLeftClose size={18} aria-hidden /> : <PanelLeft size={18} aria-hidden />}
            </button>
            {/* M57 U7-2: on a phone the same button opens every page as a sheet. It takes the section chip's place (CA7: the
                chip's tint stays, before the title) and the chip's width (its 44 px target reaches into the gaps beside it),
                so the title keeps the room it had. */}
            <button type="button" onClick={menu.onOpenSheet} data-menu-sheet aria-label="Show menu" aria-haspopup="dialog"
              aria-expanded={menu.sheetOpen} aria-keyshortcuts={SIDEBAR_SHORTCUT}
              className={`${iconButton} -mx-space-2 shrink-0 md:hidden`}>
              {tint
                ? <span aria-hidden data-section-chip={tint} data-topbar-chip className={sectionChipClass(tint, "sm")}><PanelLeft size={16} /></span>
                : <PanelLeft size={20} aria-hidden />}
            </button>
          </>
        )}
        <Link to="/" aria-label="AiAlgoDesk - Dashboard" className={`shrink-0 rounded-control focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:hidden ${onAskAi || menu ? "hidden" : ""}`}>
          {/* A text monogram, not the logo SVG: the sidebar's (hidden on phones) owns the logo's gradient id. */}
          <span aria-hidden className="flex h-7 w-7 items-center justify-center rounded-control bg-fg text-t12 font-bold text-surface">AT</span>
        </Link>
        {!menu && tint && SectionIcon && (
          <span aria-hidden data-section-chip={tint} data-topbar-chip className={`${sectionChipClass(tint, "sm")} md:hidden`}><SectionIcon size={16} /></span>
        )}
        {/* PROSE-1 / H9 / REV-S5: with colour v2 the title and its (?) share one group, on one line; only at the last step
            (still too wide one size down) the (?) moves under the title instead of pushing the bar past the edge. With
            colour v2 off the group is invisible to the layout (`contents`) and the bar is as before. */}
        <span data-topbar-title-group className={colour ? `flex shrink ${fit >= 2 ? "flex-wrap" : "flex-nowrap"} ${fit === 3 ? "order-last basis-full" : ""} items-center gap-x-space-2 ${GROUP_MEASURE} md:order-none md:basis-auto md:shrink-0 md:flex-nowrap` : "contents"}>
          {/* With colour v2 (CA7) the title is never cut on a phone (H9): one line, one size down when needed, and only as
              the last resort a wrap between words (no min-w-0 there, so the longest word sets its width). */}
          <span data-topbar-title data-title-size={fit ? "small" : undefined}
            className={`shrink ${colour ? `${TITLE_FIT_CLASS[fit]} ${TITLE_MEASURE}` : "text-t18"} font-semibold text-fg md:text-t15 ${colour ? "py-space-1 leading-tight [overflow-wrap:normal] [word-break:normal] md:whitespace-nowrap md:py-0" : "min-w-0 leading-tight [overflow-wrap:normal] md:min-w-fit md:whitespace-nowrap"}`}>{title}</span>
          {/* UX-6: the page's help line (and its tips) behind one (?) beside the title - never a paragraph above the data. */}
          {(help || subtitle) && (
            <HelpPopover title={title} className={colour ? "shrink-0" : "-ml-space-1 shrink-0"}>
              {subtitle && <p data-page-subtitle>{subtitle}</p>}
              {help && <p data-page-help={help.id}>{help.lines.join(" ")}</p>}
              {help?.showMe && <a href={help.showMe.href} className="inline-flex min-h-11 items-center font-semibold text-brand hover:underline md:min-h-0">{help.showMe.label ?? "Show me"}</a>}
              {onTour && (
                <button type="button" onClick={onTour} data-tour="tour-replay"
                  className="inline-flex min-h-11 items-center font-semibold text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-0">
                  Show the tips for this page
                </button>
              )}
            </HelpPopover>
          )}
        </span>
        {!help && !subtitle && onTour && (
          <button type="button" onClick={onTour} title="Tips for this page" aria-label="Show the tips for this page" data-tour="tour-replay"
            className={`${iconButton} -ml-space-2 shrink-0`}>
            <CircleHelp size={18} aria-hidden />
          </button>
        )}
        <button type="button" onClick={onSearch} aria-label={`${COMMAND_LABEL} (${shortcut})`} aria-haspopup="dialog"
          className="ml-space-4 hidden h-9 w-full min-w-0 max-w-[420px] items-center gap-space-2 rounded-control border border-border bg-surface-2 px-space-3 text-t14 text-fg-muted transition-colors duration-fast hover:bg-surface-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:flex">
          <Search size={16} aria-hidden className="shrink-0" />
          <span className="flex-1 truncate text-left">{COMMAND_HINT}</span>
          <kbd className="shrink-0 rounded-control border border-border bg-surface-1 px-space-1 text-t12 text-fg-muted">{shortcut}</kbd>
        </button>
        <div className={`ml-auto flex shrink-0 items-center ${onAskAi ? "gap-0 md:gap-space-1" : "gap-space-1"}`}>
          {onAskAi && (
            // NAV-1 (owner 30 Oct, "the AI button is far too small"): a filled, bold "AI" pill on a phone ("Ask AI" from md
            // up) with a gentle pulse that stops under prefers-reduced-motion (no flashing).
            <button type="button" onClick={onAskAi} title="Ask AI" aria-label="Ask AI" aria-haspopup="dialog" aria-expanded={askAiOpen} data-ask-ai-open
              className="inline-flex h-10 shrink-0 items-center gap-0.5 rounded-pill bg-brand px-1.5 text-t14 font-bold text-on-brand md:mr-space-1 md:px-space-3 shadow-sm transition-colors duration-fast hover:bg-brand-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 motion-safe:animate-pulse md:h-9 md:gap-space-2 md:text-t14">
              <Sparkles size={16} aria-hidden /><span className="md:hidden">AI</span><span className="hidden md:inline">Ask AI</span>
            </button>
          )}
          <button type="button" onClick={onSearch} aria-label={COMMAND_LABEL} aria-haspopup="dialog" className={`${iconButton} md:hidden`}>
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
          <FeedDot enabled={liveOn} status={status} />
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
