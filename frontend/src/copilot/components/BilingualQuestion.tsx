import type { ReactNode } from "react";

/**
 * The interview's two-language line: the English text, and under it a small muted line in the second language
 * (default Marathi; off in Settings). `inline` puts the second line after the first, for chips.
 */
export function Secondary({ children, lang, inline = false }: { children?: string | null; lang?: string | null; inline?: boolean }) {
  if (!children || !lang) return null;
  return <span lang={lang} className={`${inline ? "ml-1" : "block"} text-[11px] font-normal leading-snug text-fg-muted`}>{inline ? `/ ${children}` : children}</span>;
}

export default function BilingualQuestion({ en, secondary, lang, hint, hintSecondary, children }: {
  en: string; secondary?: string | null; lang?: string | null; hint?: string | null; hintSecondary?: string | null; children?: ReactNode;
}) {
  return (
    <div className="space-y-2">
      <div>
        <div className="text-base font-semibold text-fg">{en}</div>
        <Secondary lang={lang}>{secondary}</Secondary>
      </div>
      {hint && (
        <div className="rounded-lg border border-ai/20 bg-ai/5 px-3 py-2 text-xs text-fg">
          {hint}
          <Secondary lang={lang}>{hintSecondary}</Secondary>
        </div>
      )}
      {children}
    </div>
  );
}
