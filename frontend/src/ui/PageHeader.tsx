import type { ReactNode } from "react";
import { navItemFor, type ShellKey } from "../shell/navModel";
import { sectionChipClass, sectionTintOf } from "./accents";
import { useColourV2 } from "./colourV2";

/**
 * UI-H Wave 0 (H1, H9): the top of a page - the title (the menu name), one plain line, the ONE status badge and the
 * page's actions. The title is never truncated: it wraps, and `short` gives a shorter title on phones (below md) - the
 * full title stays the accessible heading text through `aria-label` only when a short one is shown.
 *
 * UI-H CA3: `section` (the page's navigation key) puts the page's nav icon in its section's round tint chip before the
 * title while colour_v2 is on - the same chip and the same section -> tint map as the nav (src/ui/accents.ts).
 */
export interface PageHeaderProps {
  title: string;
  /** A shorter title for phones ("Sectors" for "Sectors & indices"). */
  short?: string;
  /** One plain line under the title: what this page gives you. */
  line?: ReactNode;
  /** The page's one StatusBadge. */
  status?: ReactNode;
  /** Buttons or links on the right (they drop under the title on a phone). */
  actions?: ReactNode;
  className?: string;
  /** CA3: the page's navigation key; its icon and section tint come from the nav model. */
  section?: ShellKey;
}

export function PageHeader({ title, short, line, status, actions, className = "", section }: PageHeaderProps) {
  const colour = useColourV2();
  const tint = colour && section ? sectionTintOf(section) : null;
  const Icon = section ? navItemFor(section)?.icon : undefined;
  return (
    <header data-page-header className={`flex flex-wrap items-start justify-between gap-x-space-4 gap-y-space-2 ${className}`}>
      <div className="min-w-0 flex-1 basis-60">
        <div className="flex flex-wrap items-center gap-x-space-3 gap-y-space-1">
          {tint && Icon && <span aria-hidden data-section-chip={tint} className={sectionChipClass(tint, "lg")}><Icon size={20} /></span>}
          <h1 className="min-w-0 break-words text-t20 font-semibold text-fg md:text-t24" aria-label={short ? title : undefined}>
            {short ? (<><span className="md:hidden">{short}</span><span className="hidden md:inline">{title}</span></>) : title}
          </h1>
          {status}
        </div>
        {line && <p className="mt-space-1 break-words text-t14 text-fg-muted">{line}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-space-2">{actions}</div>}
    </header>
  );
}
