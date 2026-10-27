/**
 * UI-H Wave 0 (H10): the ONE disclaimer line at the bottom of a page that shows signals, analyses or numbers - one
 * wording everywhere (owner, 24 Oct). SEBI: information and education, never advice.
 */
export const DISCLAIMER_TEXT = "Information only — never says what to trade.";

export function Disclaimer({ className = "" }: { className?: string }) {
  return <p role="note" data-disclaimer className={`text-t14 text-fg-muted ${className}`}>{DISCLAIMER_TEXT}</p>;
}
