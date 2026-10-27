import type { ReactNode } from "react";

/**
 * UX-11 U5 (owner, 27 Oct): the page's one compact footer line for what used to repeat on every card ("Information
 * only…", "Phase 0 has PAPER only"). The SEBI signals-not-advice wording stays visible here once per page (owner
 * default) and in each card's Details.
 */
export function PageFootnote({ children }: { children: ReactNode }) {
  return <p className="border-t border-subtle pt-space-3 text-t12 text-fg-muted" data-page-footnote>{children}</p>;
}
