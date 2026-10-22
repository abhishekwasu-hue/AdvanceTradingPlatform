import { CircleHelp } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { glossary, type GlossaryKey } from "./glossary";

/**
 * UI2-2 (UI v2 proposal §9 rule 1): the "?" next to a trading term. A tap or click shows the term's plain-English
 * definition from the one glossary (src/ui/glossary.ts); a second tap, Escape or moving away closes it. The icon is
 * small, but its touch area is 44 x 44 px (an invisible ring around it, PW2). Neutral colours: it is help, not a warning.
 * OC1: `floating` places the definition against the screen (fixed) instead of the term, for a term inside a box that
 * scrolls sideways (the Option chain's summary strip on a phone), which would otherwise cut it off; it closes when
 * the page or that box scrolls.
 */
export function TermHint({ term, align = "start", floating = false }: { term: GlossaryKey; align?: "start" | "end"; floating?: boolean }) {
  const entry = glossary(term);
  const [open, setOpen] = useState(false);
  const [place, setPlace] = useState<{ top: number; left: number } | null>(null);
  const id = useId();
  const ref = useRef<HTMLSpanElement>(null);

  const toggle = () => {
    if (!open && floating && ref.current) {
      const box = ref.current.getBoundingClientRect();
      const width = Math.min(240, window.innerWidth - 32);
      const left = Math.min(Math.max(16, align === "end" ? box.right - width : box.left), window.innerWidth - 16 - width);
      setPlace({ top: box.bottom + 8, left });
    }
    setOpen((o) => !o);
  };

  useEffect(() => {
    if (!open || !floating) return undefined;
    const close = () => setOpen(false);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => { window.removeEventListener("scroll", close, true); window.removeEventListener("resize", close); };
  }, [open, floating]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    const onDown = (e: PointerEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("pointerdown", onDown); };
  }, [open]);

  return (
    <span ref={ref} className="relative inline-flex align-middle">
      <button type="button" aria-label={`What is ${entry.term}?`} aria-expanded={open} aria-controls={open ? id : undefined}
        onClick={toggle}
        className="relative inline-flex h-4 w-4 items-center justify-center rounded-pill text-fg-muted after:absolute after:-inset-[14px] after:content-[''] hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
        <CircleHelp size={14} aria-hidden />
      </button>
      {open && (
        <span id={id} role="note" style={floating && place ? { top: place.top, left: place.left } : undefined}
          className={`${floating && place ? "fixed normal-case" : `absolute top-6 ${align === "end" ? "right-0" : "left-0"}`} z-30 w-60 max-w-[calc(100vw-2rem)] rounded-panel border border-subtle bg-surface-1 p-space-3 text-left text-t13 font-normal text-fg shadow-raised`}>
          <span className="block font-semibold">{entry.term}</span>
          <span className="mt-space-1 block text-fg-muted">{entry.definition}</span>
        </span>
      )}
    </span>
  );
}
