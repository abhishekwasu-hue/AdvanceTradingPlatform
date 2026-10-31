/**
 * M63 I-7: markdown is never printed raw. `stripMarkdown` turns a text that may carry markdown (an AI answer, a news
 * line, a strategy description) into plain words for a one-line label, a toast, a card subtitle or a `title` tooltip:
 * `# heading` marks, "- " / "* " / "+ " bullets, "> " quotes, code fences, **bold**, __bold__, *italic*, _italic_,
 * ~~struck~~, `code` and [links](url) (the link's words stay) are removed. Plain text comes back unchanged. Where the
 * surface already shows rich text, use `RichText` (src/ui/RichText.tsx) instead.
 */
export function stripMarkdown(text: string | null | undefined): string {
  if (!text) return "";
  return text
    .replace(/^\s*```[^\n]*$/gm, "")                       // code fence lines
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")                     // # heading
    .replace(/^\s*>\s?/gm, "")                              // > quote
    .replace(/^(\s*)[-*+]\s+/gm, "$1")                      // - item / * item / + item
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")               // ![alt](src)
    .replace(/\[([^\]]+)\]\([^)\s]*\)/g, "$1")              // [words](url)
    .replace(/(\*\*|__)(?=\S)([^\n]*?\S)\1/g, "$2")         // **bold** / __bold__
    .replace(/~~(?=\S)([^\n]*?\S)~~/g, "$1")                // ~~struck~~
    .replace(/(^|[^*\w])\*(?=\S)([^*\n]*?\S)\*(?!\*)/g, "$1$2") // *italic*
    .replace(/(^|[^_\w])_(?=\S)([^_\n]*?\S)_(?![_\w])/g, "$1$2") // _italic_
    .replace(/`([^`\n]+)`/g, "$1")                          // `code`
    .replace(/\*\*|__|`/g, "")                              // an unclosed mark the writer left
    .replace(/[ \t]+$/gm, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/** The same, on one line (a tooltip, a chip): line breaks become spaces. */
export function stripMarkdownLine(text: string | null | undefined): string {
  return stripMarkdown(text).replace(/\s*\n+\s*/g, " ");
}
