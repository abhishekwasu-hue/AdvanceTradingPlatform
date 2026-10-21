import { useEffect, useId, useRef } from "react";
import { cx } from "../components/primitives/cx";

/**
 * UI2-4c (UI-11): a destructive action's question, shown in place - inside the page, so it also shows in full screen,
 * where a dialog laid over the document would not. It names the consequence; both buttons are neutral (red marks price
 * direction only). The focus starts on "Cancel" and goes back to what opened it when the box closes; Escape inside the
 * box cancels (Escape typed elsewhere, e.g. in a search field, is left alone).
 */
export function ConfirmBox({ title, body, confirm, onConfirm, onCancel, flat = false, className }: {
  title: string; body: string; confirm: string; onConfirm: () => void; onCancel: () => void;
  /** Inside another popover: no frame of its own. */
  flat?: boolean; className?: string;
}) {
  const id = useId();
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    cancel.current?.focus();
    return () => { if (opener?.isConnected) opener.focus(); };
  }, []);
  const btn = "min-h-11 rounded-control px-space-3 text-t14 font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9";
  return (
    <div role="alertdialog" aria-labelledby={`${id}-t`} aria-describedby={`${id}-b`}
      onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); onCancel(); } }}
      className={cx("text-t14 text-fg", flat ? "p-space-1" : "rounded-panel border border-subtle bg-surface-1 p-space-3 shadow-raised", className)}>
      <p id={`${id}-t`} className="font-semibold">{title}</p>
      <p id={`${id}-b`} className="mt-space-1 text-t13 text-fg-muted">{body}</p>
      <div className="mt-space-3 flex justify-end gap-space-2">
        <button ref={cancel} type="button" onClick={onCancel} className={cx(btn, "text-fg-muted hover:bg-surface-2 hover:text-fg")}>Cancel</button>
        <button type="button" onClick={onConfirm} className={cx(btn, "border border-border bg-surface-2 text-fg hover:bg-surface-3")}>{confirm}</button>
      </div>
    </div>
  );
}
