import { PageTabs } from "../ui/PageTabs";
import { PHONE_TABS, type PhoneTab } from "./layoutModel";

/**
 * Trading Terminal T1 on a phone (360 / 390): the panels as tabs - Chart · Chain · Order · Positions · Orders · Bots - one
 * panel at a time. Owner fix T1-1 (24 Oct): the shared PageTabs (UI-H W0-1) - one row that scrolls sideways, the active
 * tab scrolled into view and an edge fade where more tabs sit; every tab a 44 px target. Tab ids `terminal-tab-<key>`,
 * the panel `terminal-panel-<key>`.
 */
export function PhoneTabsV2({ tab, onTab, counts = {} }: {
  tab: PhoneTab; onTab: (tab: PhoneTab) => void; counts?: Partial<Record<PhoneTab, number>>;
}) {
  const items = PHONE_TABS.map((t) => {
    const n = counts[t.key];
    const shown = typeof n === "number" && n > 0;
    return {
      key: t.key,
      label: shown ? <>{t.label} <span className="font-tabular text-fg-muted">{n}</span></> : t.label,
      ariaLabel: shown ? `${t.label} ${n}` : undefined,
      data: { tab: t.key },
    };
  });
  return (
    <PageTabs label="Terminal panels" items={items} value={tab} onChange={onTab} idPrefix="terminal" layout="scroll"
      data={{ "terminal-tabs": "" }} />
  );
}
