import { Fragment, type ReactNode } from "react";
import { parseInline } from "../legal/markdown";

/**
 * M63 I-7: a free text that may carry markdown (an AI answer, a help-desk answer, an AI explanation) shown as rich
 * text, never with raw `**`, `#`, `- ` or backticks. It reuses the legal pages' inline parser (src/legal/markdown.tsx:
 * **bold** and safe [links]) and adds the blocks an AI writes: "#" headings (shown as a bold line, never a page
 * heading), "- " / "* " / "+ " and "1." lists, "> " quotes, code fences (their marks dropped), and inside a line
 * `code`, *italic* and ~~struck~~ (marks dropped). A single line break inside a paragraph is kept. React elements only,
 * never HTML: the text cannot inject markup, and a link to any scheme but /path, https:// or mailto: is plain text.
 * Sizes and colours come from the caller's `className` (the tokens); for a one-line label use `stripMarkdown`.
 */
export type RichBlock =
  | { kind: "heading"; text: string }
  | { kind: "paragraph"; lines: string[] }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "quote"; lines: string[] };

const BULLET = /^\s*[-*+]\s+(.*)$/;
const NUMBERED = /^\s*\d{1,3}[.)]\s+(.*)$/;

export function parseRichBlocks(source: string): RichBlock[] {
  const blocks: RichBlock[] = [];
  let current: RichBlock | null = null;
  const flush = () => { if (current) blocks.push(current); current = null; };
  for (const raw of source.replace(/\r\n?/g, "\n").split("\n")) {
    const line = raw.trimEnd();
    if (/^\s*```/.test(line)) continue;                                   // a code fence: its marks are dropped
    const heading = /^\s{0,3}#{1,6}\s+(.*)$/.exec(line);
    const bullet = BULLET.exec(line);
    const numbered = NUMBERED.exec(line);
    const quote = /^\s*>\s?(.*)$/.exec(line);
    if (line.trim() === "" || /^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { flush(); continue; }   // blank or a "---" rule
    if (heading) { flush(); blocks.push({ kind: "heading", text: heading[1].replace(/\s+#+\s*$/, "").trim() }); continue; }
    if (bullet || numbered) {
      const ordered = !bullet;
      const c = current as RichBlock | null;
      if (c && c.kind === "list" && c.ordered === ordered) c.items.push((bullet ?? numbered)![1].trim());
      else { flush(); current = { kind: "list", ordered, items: [(bullet ?? numbered)![1].trim()] }; }
      continue;
    }
    if (quote) {
      const c = current as RichBlock | null;
      if (c && c.kind === "quote") c.lines.push(quote[1].trim());
      else { flush(); current = { kind: "quote", lines: [quote[1].trim()] }; }
      continue;
    }
    const c = current as RichBlock | null;
    if (c && c.kind === "list" && /^\s{2,}\S/.test(raw)) { c.items[c.items.length - 1] += ` ${line.trim()}`; continue; }  // a wrapped item
    if (c && c.kind === "paragraph") c.lines.push(line.trim());
    else { flush(); current = { kind: "paragraph", lines: [line.trim()] }; }
  }
  flush();
  return blocks;
}

/** The words of one text run with its leftover marks dropped and `code` kept as code. */
function plainRun(text: string, key: string): ReactNode {
  const cleaned = text
    .replace(/~~(?=\S)([^\n]*?\S)~~/g, "$1")
    .replace(/(^|[^*\w])\*(?=\S)([^*\n]*?\S)\*(?!\*)/g, "$1$2")
    .replace(/(^|[^_\w])_(?=\S)([^_\n]*?\S)_(?![_\w])/g, "$1$2")
    .replace(/\*\*|__/g, "");
  const parts = cleaned.split(/`([^`\n]+)`/g);
  return parts.map((p, i) => (i % 2 === 1
    ? <code key={`${key}-${i}`} className="rounded-control bg-surface-1 px-space-1 font-mono">{p}</code>
    : <Fragment key={`${key}-${i}`}>{p.replace(/`/g, "")}</Fragment>));
}

export function RichInline({ text }: { text: string }) {
  const source = text.replace(/__(?=\S)([^\n_]*?\S)__/g, "**$1**");          // __bold__ reads as **bold**
  return <>{parseInline(source).map((part, i) => {
    if (part.kind === "bold") return <strong key={i} className="font-semibold">{plainRun(part.text, `b${i}`)}</strong>;
    if (part.kind === "link") {
      const external = part.href.startsWith("https://");
      return <a key={i} href={part.href} className="text-brand underline-offset-2 hover:underline"
        {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}>{part.text}</a>;
    }
    return <Fragment key={i}>{plainRun(part.text, `t${i}`)}</Fragment>;
  })}</>;
}

function Lines({ lines }: { lines: string[] }) {
  return <>{lines.map((l, i) => <Fragment key={i}>{i > 0 && <br />}<RichInline text={l} /></Fragment>)}</>;
}

export function RichText({ source, className = "" }: { source: string | null | undefined; className?: string }) {
  if (!source) return null;
  return (
    <div className={`space-y-space-1 ${className}`.trim()} data-rich-text>
      {parseRichBlocks(source).map((b, i) => {
        switch (b.kind) {
          case "heading":
            return <p key={i} className="font-semibold"><RichInline text={b.text} /></p>;
          case "list": {
            const items = b.items.map((item, j) => <li key={j}><RichInline text={item} /></li>);
            return b.ordered
              ? <ol key={i} className="list-decimal space-y-space-1 pl-space-5">{items}</ol>
              : <ul key={i} className="list-disc space-y-space-1 pl-space-5">{items}</ul>;
          }
          case "quote":
            return <p key={i} className="border-l-2 border-border pl-space-2"><Lines lines={b.lines} /></p>;
          default:
            return <p key={i}><Lines lines={b.lines} /></p>;
        }
      })}
    </div>
  );
}

export default RichText;
