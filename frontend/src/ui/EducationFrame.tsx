import { BookOpen } from "lucide-react";
import { Details } from "./Details";

/**
 * UX-11b C7: the education frame above Ideas and Learn. Its first sentence ("Education, not advice.") stays visible -
 * the page's one disclaimer line; every line of the frame, word for word, is in its Details (moved, never removed), so
 * the bullet prose no longer exceeds the text budget.
 */
export function firstSentence(text: string): string {
  const m = /^.*?[.!?](?=\s|$)/.exec(text.trim());
  return m ? m[0] : text.trim();
}

export function EducationFrame({ block, label, lines }: { block: string; label: string; lines: readonly string[] }) {
  return (
    <section data-block={block} aria-label={label}
      className="flex items-start gap-space-3 rounded-panel border border-subtle bg-surface-1 px-space-4 py-space-3">
      <BookOpen size={18} aria-hidden className="mt-0.5 shrink-0 text-brand" />
      <div className="min-w-0">
        {lines[0] && <p className="text-t14 text-fg">{firstSentence(lines[0])}</p>}
        <Details id={`${block}.frame`} label={label} className="mt-space-1">
          <ul className="flex flex-col gap-space-1">{lines.map((l) => <li key={l}>{l}</li>)}</ul>
        </Details>
      </div>
    </section>
  );
}
