import * as RD from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { cx } from "./cx";

interface Base { open: boolean; onOpenChange: (open: boolean) => void; title: ReactNode; description?: ReactNode; children?: ReactNode; footer?: ReactNode }

function Shell({ side, open, onOpenChange, title, description, children, footer }: Base & { side?: "right" | "bottom" }) {
  const panel = side === "right"
    ? "inset-y-0 right-0 h-full w-full max-w-md border-l"
    : side === "bottom"
      ? "inset-x-0 bottom-0 max-h-[85vh] w-full rounded-t-xl border-t"
      : "left-1/2 top-1/2 w-[calc(100vw-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 rounded-xl border";
  return (
    <RD.Root open={open} onOpenChange={onOpenChange}>
      <RD.Portal>
        <RD.Overlay className="fixed inset-0 z-40 bg-black/50" />
        <RD.Content className={cx("fixed z-50 flex flex-col border-border bg-surface-1 text-fg shadow-card focus:outline-none", panel)}>
          <div className="flex items-start gap-3 border-b border-border px-5 py-4">
            <div className="min-w-0 flex-1">
              <RD.Title className="text-base font-semibold text-fg">{title}</RD.Title>
              {description ? <RD.Description className="mt-1 text-sm text-fg-muted">{description}</RD.Description>
                : <RD.Description className="sr-only">{typeof title === "string" ? title : "Dialog"}</RD.Description>}
            </div>
            <RD.Close aria-label="Close" className="rounded p-1 text-fg-muted hover:bg-surface-2 hover:text-fg"><X size={16} /></RD.Close>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4 text-sm">{children}</div>
          {footer && <div className="flex justify-end gap-2 border-t border-border px-5 py-3">{footer}</div>}
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  );
}

/** A centred modal: focus is trapped, Escape and the overlay close it. */
export function Dialog(props: Base) { return <Shell {...props} />; }

/** A side panel (desktop) or bottom sheet (`side="bottom"`, mobile) with the same behaviour as Dialog. */
export function Sheet({ side = "right", ...props }: Base & { side?: "right" | "bottom" }) { return <Shell side={side} {...props} />; }
