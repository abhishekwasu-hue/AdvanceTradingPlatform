import { Link } from "react-router";
import { PageTabs } from "../ui/PageTabs";
import { SUBTAB_ACTIVE, SUBTAB_IDLE } from "../ui/SubTabs";
import type { NavContext, ShellKey } from "./navModel";
import { pageGroupOf, tabOf, visibleParts, visibleTabs } from "./pageGroups";

/**
 * UX3-9 / M60: the tab bar of a merged page (My trades, Strategies, Learn, Settings), above the tab's page. Each tab is a
 * link to its own address (a reload or a shared link opens the same tab; Back goes to the previous tab), the current one
 * marked with aria-current. UI-H Wave 0 (H2, W0-2): drawn by the shared PageTabs - the primary, underline level of
 * navigation. M60: a tab with parts (Results: Summary · Journal; Settings › Trading: My trading defaults · Risk limits · Alerts ·
 * Inbox) shows them as a second, smaller row of links under the first - pills that wrap onto a second line on a narrow
 * phone instead of running past the edge (each at least 44 px tall on touch).
 */
export default function PageTabsV2({ current, ctx }: { current: ShellKey | null; ctx: NavContext }) {
  const group = pageGroupOf(current);
  if (!group) return null;
  const tabs = visibleTabs(group, ctx);
  if (tabs.length < 2) return null;
  const tab = tabOf(group, current);
  const parts = tab ? visibleParts(tab, ctx) : [];
  return (
    <div className="mb-space-4 space-y-space-3 md:mb-space-6">
      <PageTabs kind="links" label={group.label} value={tab?.key ?? null} data={{ "page-tabs": group.id }}
        items={tabs.map((t) => ({ key: t.key, label: t.label, short: t.short, to: t.path, data: { "page-tab": t.key } }))} />
      {tab && parts.length >= 2 && (
        <nav aria-label={tab.label} data-page-parts={tab.key}>
          <ul className="flex flex-wrap gap-space-2">
            {parts.map((p) => {
              const on = p.key === current;
              return (
                <li key={p.key}>
                  <Link to={p.path} aria-current={on ? "page" : undefined} data-page-part={p.key} data-subtab={p.key}
                    className={`inline-flex min-h-9 items-center whitespace-nowrap rounded-pill px-space-3 text-t13 transition-colors duration-fast [@media(pointer:coarse)]:min-h-11 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand ${on ? SUBTAB_ACTIVE : `bg-surface-2 ${SUBTAB_IDLE}`}`}>
                    {p.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      )}
    </div>
  );
}
