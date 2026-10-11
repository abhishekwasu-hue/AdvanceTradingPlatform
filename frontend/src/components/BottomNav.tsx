import { Link2, ListChecks, LayoutDashboard, Menu, Radar, type LucideIcon } from "lucide-react";
import { Link } from "react-router";
import { pathFor, preloadPage } from "../routes";
import type { Page } from "./Sidebar";

/** PW2: on phones the five daily destinations sit under the thumb. "More" opens the full navigation drawer. Hidden
 * from md up (the sidebar is always visible there). The bar keeps clear of the home indicator (safe-area inset). */
export const BOTTOM_NAV: { id: Page; label: string; icon: LucideIcon }[] = [
  { id: "dashboard", label: "Home", icon: LayoutDashboard },
  { id: "screener", label: "Screener", icon: Radar },
  { id: "option-chain", label: "Chain", icon: Link2 },
  { id: "positions", label: "Positions", icon: ListChecks },
];

export default function BottomNav({ page, onMore, moreOpen }: { page: Page | null; onMore: () => void; moreOpen: boolean }) {
  const item = "flex min-h-[56px] flex-1 flex-col items-center justify-center gap-0.5 text-[11px] font-medium";
  return (
    <nav aria-label="Quick navigation"
         className="bottom-nav fixed inset-x-0 bottom-0 z-20 flex border-t border-border bg-surface-1/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden">
      {BOTTOM_NAV.map(({ id, label, icon: Icon }) => {
        const active = page === id;
        return (
          <Link key={id} to={pathFor(id)} onFocus={() => preloadPage(id)} onTouchStart={() => preloadPage(id)}
                aria-current={active ? "page" : undefined}
                className={`${item} ${active ? "text-brand" : "text-fg-muted"}`}>
            <Icon size={20} aria-hidden />
            {label}
          </Link>
        );
      })}
      <button type="button" onClick={onMore} aria-label="More pages" aria-expanded={moreOpen} aria-controls="app-navigation"
              className={`${item} ${moreOpen ? "text-brand" : "text-fg-muted"}`}>
        <Menu size={20} aria-hidden />
        More
      </button>
    </nav>
  );
}
