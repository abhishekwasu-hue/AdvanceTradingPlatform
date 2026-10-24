import { ChartCandlestick, House, ListChecks, Menu, Zap, type LucideIcon } from "lucide-react";
import { Link } from "react-router";
import type { Page } from "../components/Sidebar";
import { preloadPage } from "../routes";
import { StatusDot } from "../broker/marks";
import type { BrokerDot } from "../broker/brokerState";
import { BOTTOM_BAR_V2, moreButtonDot, navItemFor, type ShellKey } from "./navModel";
import { useColourV2 } from "../ui/colourV2";

/**
 * UI2-1 (UI v2 proposal §4, mockup nav-bottom-mobile), UX3-9 (UX audit section 5): the phone bottom bar - Today,
 * Charts, Signals, My trades, More. `current` is the entry of the page on screen (navEntryFor: My trades › Orders
 * lights up My trades).
 * "More" opens the More sheet (search plus every other page). Each item is about 78 x 64 px (PW2: 44 px minimum),
 * the bar keeps clear of the home indicator, and it steps aside while a text field is focused (the `bottom-nav` rule in
 * styles/index.css). Hidden from md up, where the sidebar is always shown. While the sheet is open the bar stays on top
 * of it, so More closes it again and the four pages stay one tap away.
 * Colour v2 (flag `colour_v2`): the tab on screen sits on a brand-tinted pill (icon and word in brand, the word bold);
 * every other tab stays neutral grey (CV-1, owner 24 Oct: a group tint here read as a second selected tab - the group
 * colours stay in the sidebar and the More sheet). Each word keeps to one line, bold included, down to 360 px (CV-2).
 * With the flag off the bar is exactly as before.
 */
const ICONS: Record<string, LucideIcon> = { dashboard: House, charts: ChartCandlestick, signals: Zap, positions: ListChecks };

export const MORE_SHEET_ID = "more-sheet";

export default function BottomNavV2({ current, moreOpen, onMore, onPick, dot = null }: {
  current: ShellKey | null; moreOpen: boolean; onMore: () => void;
  /** P2b: the broker's status; amber or red also shows on the More icon (Broker is the sheet's first tile). */
  dot?: BrokerDot | null;
  /** A bar page was tapped: the shell closes the More sheet (also when it is the page already on screen). */
  onPick?: () => void;
}) {
  const attention = moreButtonDot(dot);
  const colour = useColourV2();
  /** Colour v2: the icon, on a brand-tinted pill while its tab is the page on screen; neutral grey otherwise (CV-1). */
  const glyph = (Icon: LucideIcon, active: boolean) => (colour ? (
    <span data-nav-pill={active || undefined}
      className={`flex h-8 w-14 items-center justify-center rounded-pill transition-colors duration-fast ${active ? "bg-brand-tint text-brand" : "text-fg-muted"}`}>
      <Icon size={22} aria-hidden />
    </span>
  ) : <Icon size={22} aria-hidden />);
  const item = "flex min-h-16 flex-1 flex-col items-center justify-center gap-space-1 text-t12 font-medium transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand";
  return (
    <nav aria-label="Quick navigation" data-bottom-nav-v2 data-tour="nav-bottom"
      className={`bottom-nav fixed inset-x-0 bottom-0 flex border-t border-subtle bg-surface-1 pb-[env(safe-area-inset-bottom)] md:hidden ${moreOpen ? "pointer-events-auto z-[60]" : "z-20"}`}>
      {BOTTOM_BAR_V2.map(({ key, label }) => {
        const Icon = ICONS[key];
        const active = !moreOpen && current === key;
        const path = navItemFor(key)?.path ?? "/";
        const preload = key === "charts" ? undefined : () => preloadPage(key as Page);
        return (
          <Link key={key} to={path} onClick={onPick} onFocus={preload} onTouchStart={preload} aria-current={current === key ? "page" : undefined}
            className={`${item} ${active ? "text-brand" : "text-fg-muted"}${colour && active ? " font-semibold" : ""}`}>
            {glyph(Icon, active)}
            {colour ? <span data-nav-label className="whitespace-nowrap">{label}</span> : label}
          </Link>
        );
      })}
      <button type="button" onClick={onMore} aria-label={attention ? "More pages, broker needs attention" : "More pages"} aria-expanded={moreOpen} aria-controls={MORE_SHEET_ID} aria-haspopup="dialog"
        className={`${item} ${moreOpen ? "text-brand" : "text-fg-muted"}`}>
        <span className="relative">
          {colour ? glyph(Menu, moreOpen) : <Menu size={22} aria-hidden />}
          {attention && <StatusDot dot={attention} className="absolute -right-1 -top-0.5 ring-2 ring-surface-1" />}
        </span>
        {colour ? <span data-nav-label className="whitespace-nowrap">More</span> : "More"}
      </button>
    </nav>
  );
}
