import { SubTabs } from "../ui/SubTabs";
import type { NavContext, ShellKey } from "./navModel";
import { pageGroupOf, visibleTabs } from "./pageGroups";

/**
 * UX3-9: the tab bar of a merged page (My trades, Strategies, Performance), above the tab's page. Each tab is a link to
 * its own address (a reload or a shared link opens the same tab; Back goes to the previous tab), the current one marked
 * with aria-current. UI-H Wave 0 (H2): drawn by the shared SubTabs (one style for every tab row; the active tab filled
 * with the accent; every tab visible on a phone).
 */
export default function PageTabsV2({ current, ctx }: { current: ShellKey | null; ctx: NavContext }) {
  const group = pageGroupOf(current);
  if (!group) return null;
  const tabs = visibleTabs(group, ctx);
  if (tabs.length < 2) return null;
  return (
    <div className="mb-space-4 md:mb-space-6">
      <SubTabs kind="links" label={group.label} value={current} full data={{ "page-tabs": group.id }} className="md:w-fit"
        items={tabs.map((t) => ({ key: t.key, label: t.label, to: t.path, data: { "page-tab": t.key } }))} />
    </div>
  );
}
