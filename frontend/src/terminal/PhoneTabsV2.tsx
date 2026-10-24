import { cx } from "../components/primitives/cx";
import { PHONE_TABS, type PhoneTab } from "./layoutModel";

/**
 * Trading Terminal T1 on a phone (360 / 390): the panels as tabs - Chart · Chain · Order · Positions · Orders · Bots - one
 * panel at a time. Every tab is a 44 px target; on a phone they sit in two rows of three, so all six show at once and
 * nothing scrolls sideways.
 */
export function PhoneTabsV2({ tab, onTab, counts = {} }: {
  tab: PhoneTab; onTab: (tab: PhoneTab) => void; counts?: Partial<Record<PhoneTab, number>>;
}) {
  return (
    <div role="tablist" aria-label="Terminal panels" data-terminal-tabs
      className="grid grid-cols-3 gap-space-1 border-b border-subtle pb-space-2 sm:grid-cols-6">
      {PHONE_TABS.map((t) => {
        const n = counts[t.key];
        return (
          <button key={t.key} type="button" role="tab" id={`terminal-tab-${t.key}`} aria-selected={tab === t.key} aria-controls="terminal-tab-panel"
            onClick={() => onTab(t.key)} data-tab={t.key}
            className={cx("inline-flex min-h-11 min-w-0 items-center justify-center gap-space-1 rounded-control border px-space-2 text-t14 font-semibold",
              tab === t.key ? "border-brand bg-brand text-on-brand" : "border-border bg-surface-1 text-fg")}>
            {t.label}
            {typeof n === "number" && n > 0 && (
              <span className={cx("rounded-pill px-space-1 font-tabular text-t12", tab === t.key ? "bg-surface-1 text-fg" : "bg-surface-2 text-fg-muted")}>{n}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}
