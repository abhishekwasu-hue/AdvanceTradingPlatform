import type { ReactNode } from "react";
import { Details } from "./Details";
import { DISCLAIMER_TEXT } from "./Disclaimer";

/**
 * UX-11 U5 (owner, 27 Oct): the page's one compact footer line for what used to repeat on every card ("Information
 * only…", "Phase 0 has PAPER only"). The SEBI signals-not-advice wording (H10, src/ui/Disclaimer.tsx) stays visible here
 * once per page (owner default) and in each card's Details; `children` add the page's own short words after it.
 */
export function PageFootnote({ children }: { children?: ReactNode }) {
  // UI-28 U1.11 (owner, 28 Oct: "In all pages, hide common message"): the line every page repeats sits behind one small
  // ⓘ at the foot of the page, closed by default (state remembered); never removed (SEBI: signals, not advice).
  return (
    <div data-page-footnote data-disclaimer className="border-t border-subtle pt-space-1">
      <Details id="page.footnote" label="About" className="mt-0">
        <p className="text-t12">{DISCLAIMER_TEXT}{children ? <> {children}</> : null}</p>
      </Details>
    </div>
  );
}
