import { Menu, type LucideIcon } from "lucide-react";
import { Link } from "react-router";
import type { Page } from "../components/Sidebar";
import { PAGES, preloadPage } from "../routes";
import { StatusDot } from "../broker/marks";
import type { BrokerDot } from "../broker/brokerState";
import { isCurrentEntry, moreButtonDot, navItemFor, visibleBottomBar, type NavContext, type ShellKey } from "./navModel";
import { useColourV2 } from "../ui/colourV2";
import { navChipClass, navWordClass, sectionTintOf, type SectionTint } from "../ui/accents";

/**
 * UI2-1 (UI v2 proposal §4, mockup nav-bottom-mobile), UX3-9 (UX audit section 5): the phone bottom bar - Today,
 * Charts, Signals, My trades, More. `current` is the entry of the page on screen (navEntryFor: My trades › Orders
 * lights up My trades).
 * "More" opens the More sheet (search plus every other page). Each item is about 78 x 64 px (PW2: 44 px minimum),
 * the bar keeps clear of the home indicator, and it steps aside while a text field is focused (the `bottom-nav` rule in
 * styles/index.css). Hidden from md up, where the sidebar is always shown. While the sheet is open the bar stays on top
 * of it, so More closes it again and the four pages stay one tap away.
 * Colour v2 (flag `colour_v2`) + UI-H CA6 (owner, 25 Oct; supersedes CV-1's neutral grey tabs): every tab's icon sits
 * on its own soft round chip (the same tint map as the sidebar, src/ui/accents.ts; CA6-1: five clearly different hues -
 * Today blue, Charts violet, Signals amber, My trades pink, More teal); the tab on screen takes the strong tint - the solid section colour - and its word in that colour,
 * bold, so it never reads as one of the soft ones. The other words stay muted. Each word keeps to one line, bold
 * included, down to 360 px (CV-2).
 * With the flag off the bar is exactly as before.
 * P1-8 (2): while `watchlists` is on, a Watchlist tab after Charts (six tabs; each word on one line, a little tighter). It takes the tint
 * of its section (Markets, the same colour the sidebar and the page header give it) and is told apart by its own icon
 * (an eye) and word.
 * M60 (owner order, approved 30 Oct): the bar is the first four sidebar entries this trader may see (Today, Terminal, My
 * trades, Find setups with every feature on), each with its sidebar icon, plus More. `current` is the page on screen;
 * its entry lights up (isCurrentEntry: My trades › Orders lights up My trades).
 */

export const MORE_SHEET_ID = "more-sheet";

export default function BottomNavV2({ current, moreOpen, onMore, onPick, dot = null, ctx }: {
  current: ShellKey | null; moreOpen: boolean; onMore: () => void;
  /** Which flags are on and who is looking (an entry behind a flag shows only while it is on); left out: the entries without one. */
  ctx?: NavContext | Pick<NavContext, "on">;
  /** P2b: the broker's status; amber or red also shows on the More icon (Broker is the sheet's first tile). */
  dot?: BrokerDot | null;
  /** A bar page was tapped: the shell closes the More sheet (also when it is the page already on screen). */
  onPick?: () => void;
}) {
  const attention = moreButtonDot(dot);
  const colour = useColourV2();
  /** CA6: the icon on its section's round chip - soft, or the strong tint while its tab is the page on screen. */
  const glyph = (Icon: LucideIcon, active: boolean, tint: SectionTint) => (colour ? (
    <span data-nav-pill={active || undefined} data-nav-tint={tint} className={navChipClass(tint, active)}>
      <Icon size={20} aria-hidden />
    </span>
  ) : <Icon size={22} aria-hidden />);
  const tintOf = (key: ShellKey): SectionTint => sectionTintOf(key) ?? "account";
  const word = (active: boolean, tint: SectionTint, plain: string) => (colour ? (active ? `${navWordClass(tint)} font-semibold` : "text-fg-muted") : plain);
  const bar = visibleBottomBar(ctx);
  // UI-28 U3.5: at most four tabs and More, so every word fits on one line without tightening.
  const label = (text: string) => <span data-nav-label className="whitespace-nowrap">{text}</span>;
  const item = "flex min-h-16 flex-1 flex-col items-center justify-center gap-space-1 text-t12 font-medium transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand";
  return (
    <nav aria-label="Quick navigation" data-bottom-nav-v2 data-tour="nav-bottom"
      className={`bottom-nav fixed inset-x-0 bottom-0 flex border-t border-subtle bg-surface-1 pb-[env(safe-area-inset-bottom)] md:hidden ${moreOpen ? "pointer-events-auto z-[60]" : "z-20"}`}>
      {bar.map(({ key, label: text }) => {
        const nav = navItemFor(key);
        const Icon = nav?.icon ?? Menu;
        const here = isCurrentEntry(key, current);
        const active = !moreOpen && here;
        const path = nav?.path ?? "/";
        const preload = (PAGES as string[]).includes(key) && key !== "charts" ? () => preloadPage(key as Page) : undefined;
        return (
          <Link key={key} to={path} onClick={onPick} onFocus={preload} onTouchStart={preload} aria-current={here ? "page" : undefined} data-bar-key={key}
            className={`${item} ${word(active, tintOf(key), active ? "text-brand" : "text-fg-muted")}`}>
            {glyph(Icon, active, tintOf(key))}
            {label(text)}
          </Link>
        );
      })}
      <button type="button" onClick={onMore} aria-label={attention ? "More pages, broker needs attention" : "More pages"} aria-expanded={moreOpen} aria-controls={MORE_SHEET_ID} aria-haspopup="dialog"
        className={`${item} ${word(moreOpen, "more", moreOpen ? "text-brand" : "text-fg-muted")}`}>
        <span className="relative">
          {colour ? glyph(Menu, moreOpen, "more") : <Menu size={22} aria-hidden />}
          {attention && <StatusDot dot={attention} className="absolute -right-1 -top-0.5 ring-2 ring-surface-1" />}
        </span>
        {label("More")}
      </button>
    </nav>
  );
}
