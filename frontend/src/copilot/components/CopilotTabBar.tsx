import { FlaskConical, MessageCircle, Radar, Satellite, Target, Trophy, Zap, type LucideIcon } from "lucide-react";
import { useEffect, useRef } from "react";
import { cx } from "../../components/primitives";
import { useCopilotT } from "../i18n";
import { COPILOT_TABS, type CopilotTab } from "../tabs";

export const TAB_ICON: Record<CopilotTab, LucideIcon> = {
  "market-pulse": Zap, "strategy-lab": FlaskConical, "idea-builder": Target, ask: MessageCircle,
  watchtower: Satellite, "news-radar": Radar, coach: Trophy,
};

/**
 * The seven tabs: a sticky glass bar on desktop, horizontally scrollable pills on a phone (the active pill scrolls
 * into view). Badges: open proposals on Watchtower, new high-severity news on News Radar. Arrow keys move between
 * tabs (the tablist pattern); each tab is also a link-like address (/copilot/<tab>).
 */
export default function CopilotTabBar({ active, onSelect, badges }: { active: CopilotTab; onSelect: (t: CopilotTab) => void; badges: Partial<Record<CopilotTab, number>> }) {
  const t = useCopilotT();
  const refs = useRef<Partial<Record<CopilotTab, HTMLButtonElement | null>>>({});
  useEffect(() => { refs.current[active]?.scrollIntoView?.({ block: "nearest", inline: "center" }); }, [active]);

  const onKey = (e: React.KeyboardEvent, i: number) => {
    const next = e.key === "ArrowRight" ? i + 1 : e.key === "ArrowLeft" ? i - 1 : e.key === "Home" ? 0 : e.key === "End" ? COPILOT_TABS.length - 1 : null;
    if (next == null) return;
    e.preventDefault();
    const tab = COPILOT_TABS[(next + COPILOT_TABS.length) % COPILOT_TABS.length];
    onSelect(tab);
    refs.current[tab]?.focus();
  };

  return (
    <div className="sticky top-0 z-20 -mx-3 border-b border-border/80 bg-surface/80 px-3 py-2 backdrop-blur-md md:-mx-6 md:px-6">
      <div role="tablist" aria-label={t("shell.tabsLabel")} className="copilot-tabs flex gap-1.5 overflow-x-auto">
        {COPILOT_TABS.map((tab, i) => {
          const Icon = TAB_ICON[tab];
          const on = tab === active;
          const badge = badges[tab] ?? 0;
          return (
            <button key={tab} ref={(el) => { refs.current[tab] = el; }} role="tab" id={`copilot-tab-${tab}`} aria-selected={on}
                    aria-controls={`copilot-panel-${tab}`} tabIndex={on ? 0 : -1} onClick={() => onSelect(tab)} onKeyDown={(e) => onKey(e, i)}
                    data-testid={`tab-${tab}`}
                    className={cx("relative inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-3 py-1.5 text-sm font-medium transition-colors",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ai",
                      on ? "border-ai/50 bg-ai/15 text-fg shadow-[0_0_18px_-6px_rgb(var(--ai)/0.6)]" : "border-transparent text-fg-muted hover:bg-surface-2 hover:text-fg")}>
              <Icon size={15} className={on ? "text-ai" : ""} aria-hidden />
              {t(`tabs.${tab}`)}
              {badge > 0 && (
                <span className="ml-0.5 inline-flex min-w-[18px] items-center justify-center rounded-full bg-ai px-1 text-[10px] font-bold leading-[18px] text-surface"
                      aria-label={t(`tabs.badge.${tab}`, { count: badge })}>{badge > 99 ? "99+" : badge}</span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
