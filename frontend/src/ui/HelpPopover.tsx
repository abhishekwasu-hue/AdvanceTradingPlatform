import { CircleHelp, X } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Sheet } from "../components/primitives/Dialog";
import { PHONE_QUERY, useMediaQuery } from "../utils/useMediaQuery";

/**
 * UI-H Wave 0 (H3): the (?) next to a heading, card or chart that holds the "how it is worked out" / method text, so
 * the page itself shows only what the numbers say. Desktop: a popover under the button (Escape, a click outside or
 * the close button shut it). Phone: a bottom sheet (focus trapped, the overlay closes it). Neutral colours: help, not
 * a warning. The button's touch area is 44 x 44 px.
 *
 * For a single trading word use TermHint (the glossary); HelpPopover is for longer page-specific text.
 */
export interface HelpPopoverProps {
  /** What the help is about: the button reads "Help: <title>" and the popover / sheet is headed by it. */
  title: string;
  children: ReactNode;
  align?: "start" | "end";
  className?: string;
}

export function HelpPopover({ title, children, align = "start", className = "" }: HelpPopoverProps) {
  const phone = useMediaQuery(PHONE_QUERY);
  const [open, setOpen] = useState(false);
  const id = useId();
  const ref = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open || phone) return undefined;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    const onDown = (e: PointerEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("pointerdown", onDown); };
  }, [open, phone]);

  const button = (
    <button type="button" aria-label={`Help: ${title}`} aria-expanded={open} aria-controls={open && !phone ? id : undefined}
      aria-haspopup="dialog" onClick={() => setOpen((o) => !o)} data-help-button
      className="relative inline-flex h-5 w-5 items-center justify-center rounded-pill text-fg-muted after:absolute after:-inset-3 after:content-[''] hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
      <CircleHelp size={16} aria-hidden />
    </button>
  );

  if (phone) {
    return (
      <span className={`inline-flex align-middle ${className}`} data-help={title}>
        {button}
        <Sheet side="bottom" open={open} onOpenChange={setOpen} title={title}>
          <div className="space-y-space-2 text-t14 text-fg" data-help-sheet>{children}</div>
        </Sheet>
      </span>
    );
  }

  return (
    <span ref={ref} className={`relative inline-flex align-middle ${className}`} data-help={title}>
      {button}
      {open && (
        <div id={id} role="dialog" aria-label={title} data-help-popover
          className={`absolute top-7 z-30 w-80 max-w-[calc(100vw-2rem)] rounded-panel border border-subtle bg-surface-1 p-space-3 text-left text-t14 font-normal text-fg shadow-raised ${align === "end" ? "right-0" : "left-0"}`}>
          <div className="flex items-start justify-between gap-space-2">
            <p className="font-semibold">{title}</p>
            <button type="button" aria-label="Close help" onClick={() => setOpen(false)}
              className="-mr-space-1 -mt-space-1 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
              <X size={16} aria-hidden />
            </button>
          </div>
          <div className="mt-space-1 space-y-space-2 text-fg-muted">{children}</div>
        </div>
      )}
    </span>
  );
}
