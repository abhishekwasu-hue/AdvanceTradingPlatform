import { Fragment } from "react";
import { parseBlocks, parseInline } from "../../legal/markdown";

/**
 * M59 CR-1: a Copilot answer's free text (the AI wording, or a rule sentence) rendered with the small Markdown subset
 * the legal pages already parse (src/legal/markdown.tsx - **bold**, "- " lists, "#" headings, safe links), so a raw
 * `**Weekly:**` never reaches the screen. React elements only (never HTML): the text cannot inject markup. Text sizes
 * and colours from the tokens; a heading the model writes is shown as a bold line, never as a page heading.
 */
function Inline({ text }: { text: string }) {
  return <>{parseInline(text).map((part, i) => {
    if (part.kind === "bold") return <strong key={i} className="font-semibold text-fg">{part.text}</strong>;
    if (part.kind === "link") {
      const external = part.href.startsWith("https://");
      return <a key={i} href={part.href} className="text-brand underline-offset-2 hover:underline"
        {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}>{part.text}</a>;
    }
    // a lone "**" / "__" left by the model (an unclosed mark) and *italic* marks are dropped, never printed
    return <Fragment key={i}>{part.text.replace(/\*\*|__/g, "").replace(/\*([^*\n]+)\*/g, "$1")}</Fragment>;
  })}</>;
}

/** CH-1b: a heading's anchor id inside one answer (`prefix` keeps two answers' ids apart). */
export function headingId(prefix: string, text: string): string {
  return `${prefix}-${text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")}`;
}

export function RichText({ source, className = "text-t15 text-fg", anchorPrefix }: { source: string; className?: string; anchorPrefix?: string }) {
  return (
    <div className="space-y-space-1" data-rich-text>
      {parseBlocks(source.replace(/^(\s*)\*\s+/gm, "$1- ")).map((b, i) => {
        switch (b.kind) {
          case "heading":
            // CH-1b: a section heading of an indexed answer - a clear break above it and an anchor for the answer's index
            return <p key={i} id={anchorPrefix ? headingId(anchorPrefix, b.text) : undefined} data-answer-heading
              className={`${className} mt-space-3 border-t border-subtle pt-space-2 font-semibold first:mt-0 first:border-t-0 first:pt-0`}><Inline text={b.text} /></p>;
          case "list":
            return (
              <ul key={i} className={`${className} list-disc space-y-space-1 pl-space-5`}>
                {b.items.map((item, j) => <li key={j}><Inline text={item} /></li>)}
              </ul>
            );
          case "quote":
            return <p key={i} className={className}><Inline text={b.text} /></p>;
          default:
            return <p key={i} className={className}><Inline text={b.text} /></p>;
        }
      })}
    </div>
  );
}

export default RichText;
