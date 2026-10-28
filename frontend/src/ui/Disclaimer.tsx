/**
 * UI-H Wave 0 (H10): the ONE disclaimer line at the bottom of a page that shows signals, analyses or numbers - one
 * wording everywhere (owner, 24 Oct). SEBI: information and education, never advice.
 */
// UI-28 U1.10 (owner, 28 Oct): said affirmatively - the same meaning (information and education; the decision is the trader's).
export const DISCLAIMER_TEXT = "For information and education: every trading decision is yours.";

export function Disclaimer({ className = "" }: { className?: string }) {
  return <p role="note" data-disclaimer className={`text-t14 text-fg-muted ${className}`}>{DISCLAIMER_TEXT}</p>;
}
