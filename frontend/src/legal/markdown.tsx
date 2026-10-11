import { Fragment, type ReactNode } from "react";

/** Z10-f: the small Markdown subset the legal pages use - headings (#, ##, ###), paragraphs, "- " lists, "> " quotes,
 * **bold** and [links](/path | https://... | mailto:...). Rendered as React elements, never as HTML: the text cannot
 * inject markup, and a link to any other scheme renders as plain text. */
export type Inline = { kind: "text"; text: string } | { kind: "bold"; text: string } | { kind: "link"; text: string; href: string };
export type Block =
  | { kind: "heading"; level: 1 | 2 | 3; text: string; id: string }
  | { kind: "paragraph"; text: string }
  | { kind: "list"; items: string[] }
  | { kind: "quote"; text: string };

export function slugify(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60) || "section";
}

export function safeHref(href: string): string | null {
  const h = href.trim();
  if (/^\/(?!\/)/.test(h) || /^https:\/\//i.test(h) || /^mailto:[^\s]+$/i.test(h)) return h;
  return null;
}

export function parseBlocks(source: string): Block[] {
  const blocks: Block[] = [];
  let para: string[] = [];
  let list: string[] | null = null;
  let quote: string[] | null = null;
  const flush = () => {
    if (para.length) blocks.push({ kind: "paragraph", text: para.join(" ") });
    if (list) blocks.push({ kind: "list", items: list });
    if (quote) blocks.push({ kind: "quote", text: quote.join(" ") });
    para = []; list = null; quote = null;
  };
  for (const raw of source.split("\n")) {
    const line = raw.trimEnd();
    const heading = /^(#{1,3})\s+(.*)$/.exec(line);
    if (heading) {
      flush();
      const text = heading[2].trim();
      blocks.push({ kind: "heading", level: heading[1].length as 1 | 2 | 3, text, id: slugify(text) });
    } else if (/^\s*-\s+/.test(line)) {
      if (!list) { flush(); list = []; }
      list.push(line.replace(/^\s*-\s+/, ""));
    } else if (/^>\s?/.test(line)) {
      if (!quote) { flush(); quote = []; }
      quote.push(line.replace(/^>\s?/, ""));
    } else if (line.trim() === "") {
      flush();
    } else if (list && /^\s{2,}\S/.test(raw)) {
      list[list.length - 1] += ` ${line.trim()}`;      // a wrapped list item
    } else {
      if (list || quote) flush();
      para.push(line.trim());
    }
  }
  flush();
  return blocks;
}

export function parseInline(text: string): Inline[] {
  const out: Inline[] = [];
  const pattern = /\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^)\s]+)\)/g;
  let last = 0;
  for (const m of text.matchAll(pattern)) {
    const at = m.index ?? 0;
    if (at > last) out.push({ kind: "text", text: text.slice(last, at) });
    if (m[1] !== undefined) out.push({ kind: "bold", text: m[1] });
    else {
      const href = safeHref(m[3]);
      out.push(href ? { kind: "link", text: m[2], href } : { kind: "text", text: m[2] });
    }
    last = at + m[0].length;
  }
  if (last < text.length) out.push({ kind: "text", text: text.slice(last) });
  return out;
}

function renderInline(text: string): ReactNode {
  return parseInline(text).map((part, i) => {
    if (part.kind === "bold") return <strong key={i} className="font-semibold text-fg">{part.text}</strong>;
    if (part.kind === "link") {
      const external = part.href.startsWith("https://");
      return <a key={i} href={part.href} className="text-brand underline-offset-2 hover:underline"
                {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}>{part.text}</a>;
    }
    return <Fragment key={i}>{part.text}</Fragment>;
  });
}

export function Markdown({ source }: { source: string }) {
  return (
    <>
      {parseBlocks(source).map((b, i) => {
        switch (b.kind) {
          case "heading":
            return b.level === 1
              ? <h1 key={i} id={b.id} className="mt-2 text-2xl font-semibold text-fg">{renderInline(b.text)}</h1>
              : b.level === 2
                ? <h2 key={i} id={b.id} className="mt-6 text-lg font-semibold text-fg">{renderInline(b.text)}</h2>
                : <h3 key={i} id={b.id} className="mt-4 text-base font-semibold text-fg">{renderInline(b.text)}</h3>;
          case "list":
            return <ul key={i} className="mt-2 list-disc space-y-1 pl-5">{b.items.map((item, j) => <li key={j}>{renderInline(item)}</li>)}</ul>;
          case "quote":
            return <div key={i} role="note" className="mt-2 rounded-md border border-warn/50 bg-warn/10 px-3 py-2 text-warn">{renderInline(b.text)}</div>;
          default:
            return <p key={i} className="mt-2">{renderInline(b.text)}</p>;
        }
      })}
    </>
  );
}
