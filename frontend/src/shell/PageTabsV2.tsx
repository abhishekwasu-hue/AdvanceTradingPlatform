import { Link } from "react-router";
import type { NavContext, ShellKey } from "./navModel";
import { pageGroupOf, visibleTabs } from "./pageGroups";

/**
 * UX3-9: the tab bar of a merged page (My trades, Strategies, Performance), above the tab's page. Each tab is a link to
 * its own address (a reload or a shared link opens the same tab; Back goes to the previous tab), the current one marked
 * with aria-current. Phones: one segmented row (every tab visible, nothing scrolls sideways, 44 px tall); from md: an
 * underlined row, as on the Signals page.
 */
export default function PageTabsV2({ current, ctx }: { current: ShellKey | null; ctx: NavContext }) {
  const group = pageGroupOf(current);
  if (!group) return null;
  const tabs = visibleTabs(group, ctx);
  if (tabs.length < 2) return null;
  return (
    <nav aria-label={group.label} data-page-tabs={group.id}
      style={{ gridTemplateColumns: `repeat(${tabs.length}, minmax(0, 1fr))` }}
      className="mb-space-4 grid gap-space-1 rounded-panel bg-surface-2 p-space-1 md:mb-space-6 md:flex md:gap-space-6 md:rounded-none md:border-b md:border-subtle md:bg-transparent md:p-0">
      {tabs.map((t) => {
        const on = t.key === current;
        return (
          <Link key={t.key} to={t.path} aria-current={on ? "page" : undefined} data-page-tab={t.key}
            className={`flex min-h-11 items-center justify-center truncate rounded-control px-space-2 text-t14 transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:-mb-px md:rounded-none md:border-b-2 md:px-0 ${
              on ? "bg-surface-1 font-semibold text-fg shadow-card md:border-brand md:bg-transparent md:shadow-none" : "text-fg-muted hover:text-fg md:border-transparent"
            }`}>
            {t.label}
          </Link>
        );
      })}
    </nav>
  );
}
