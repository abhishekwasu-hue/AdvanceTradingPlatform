import { Download, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { INSTALL_IOS, dismissInstall, promptInstall, usePwa } from "./pwa";

const iconButton = "flex h-11 w-11 items-center justify-center rounded-control text-fg-muted transition-colors duration-fast hover:bg-surface-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:h-9 md:w-9";

/** Owner 18 Nov ("waste of space"): the install offer as one small top-bar button instead of a full-width banner above
 * every page. The browser's own prompt where it offers one; on iPhone / iPad the Add to Home Screen steps in a small panel
 * with "Not now". Nothing when there is nothing to offer (installed, dismissed, or no offer from the browser). */
export function InstallAppButton() {
  const { install } = usePwa();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    const onDown = (e: PointerEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("pointerdown", onDown); };
  }, [open]);
  if (!install) return null;
  if (install === "prompt") {
    return (
      <button type="button" onClick={() => { void promptInstall(); }} title="Install the app" aria-label="Install the app"
        data-install-app className={iconButton}>
        <Download size={18} aria-hidden />
      </button>
    );
  }
  return (
    <span ref={ref} className="relative inline-flex" data-install-app="ios">
      <button type="button" onClick={() => setOpen((o) => !o)} title="Install the app" aria-label="Install the app"
        aria-expanded={open} aria-haspopup="dialog" className={iconButton}>
        <Download size={18} aria-hidden />
      </button>
      {open && (
        <div role="dialog" aria-label="Install the app" data-install-steps
          className="absolute right-0 top-full z-50 mt-space-2 w-64 rounded-control border border-border bg-surface-1 p-space-3 text-t14 text-fg shadow-lg">
          <p>{INSTALL_IOS} Alerts on iOS arrive only in the installed app.</p>
          <button type="button" onClick={() => { dismissInstall(); setOpen(false); }}
            className="mt-space-2 inline-flex min-h-11 items-center gap-space-1 font-semibold text-brand hover:underline md:min-h-0">
            <X size={14} aria-hidden />Not now
          </button>
        </div>
      )}
    </span>
  );
}
