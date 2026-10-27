import type { ReactNode } from "react";
import { DISCLAIMER_TEXT } from "./Disclaimer";

/**
 * UX-11 U5 (owner, 27 Oct): the page's one compact footer line for what used to repeat on every card ("Information
 * only…", "Phase 0 has PAPER only"). The SEBI signals-not-advice wording (H10, src/ui/Disclaimer.tsx) stays visible here
 * once per page (owner default) and in each card's Details; `children` add the page's own short words after it.
 */
export function PageFootnote({ children }: { children?: ReactNode }) {
  return (
    <p data-page-footnote data-disclaimer className="border-t border-subtle pt-space-3 text-t12 text-fg-muted">
      {DISCLAIMER_TEXT}{children ? <> {children}</> : null}
    </p>
  );
}
