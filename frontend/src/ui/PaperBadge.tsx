import { Radio, ShieldCheck } from "lucide-react";
import { cx } from "../components/primitives/cx";
import { chipClass } from "./Chip";
import { LIVE_TEXT, LIVE_WORD, PAPER_TEXT, PAPER_WORD } from "./paperWords";

/**
 * CT-1 C8 (owner order 30 Oct): the ONE mode badge - one word, "PAPER", once per screen (beside the page title, or on a
 * sheet / dialog that covers the page, such as the order ticket and the basket). The sentence is its tooltip, never a
 * band or a line of its own. Rows on a PAPER screen carry no mode word; a LIVE row keeps its own LIVE badge.
 */
export function PaperBadge({ className }: { className?: string } = {}) {
  return (
    <span title={PAPER_TEXT} aria-label={`${PAPER_WORD}: ${PAPER_TEXT}`}
      className={cx("inline-flex shrink-0 items-center gap-space-1 rounded-pill bg-warn-tint px-space-2 py-px text-t12 font-semibold text-warn", className)}
      data-practice-frame data-mode-badge="paper">
      <ShieldCheck size={12} aria-hidden />{PAPER_WORD}
    </span>
  );
}

/** The LIVE badge of a row (or a screen) that is LIVE: the solid chip, never hidden. */
export function LiveBadge({ className }: { className?: string } = {}) {
  return (
    <span title={LIVE_TEXT} aria-label={`${LIVE_WORD}: ${LIVE_TEXT}`} className={chipClass({ variant: "solid", icon: true, className })} data-mode-badge="live">
      <Radio size={12} aria-hidden />{LIVE_WORD}
    </span>
  );
}

/** A row's mode mark: LIVE rows only (the screen's one PAPER badge covers the paper rows). */
export function RowModeBadge({ live }: { live: boolean }) {
  return live ? <LiveBadge /> : null;
}

