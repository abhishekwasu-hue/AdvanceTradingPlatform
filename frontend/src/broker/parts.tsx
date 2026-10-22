import * as RD from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { useRef, type ReactNode } from "react";

export { BrokerLogo, StatusDot } from "./marks";

/**
 * P2b: the dialog and buttons the Broker page, the sign-in prompt and the Dashboard banner share. Semantic colour
 * tokens only (scripts/check-v2-colours.mjs covers src/broker/).
 */

/** A modal on a wide screen, a bottom sheet on a phone (one element, laid out per width). Focus is trapped; Escape,
 * the close button and the backdrop close it. */
export function ResponsiveDialog({ open, onOpenChange, title, children, marker }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  children: ReactNode;
  /** A data attribute value for tests (`data-broker-dialog`). */
  marker?: string;
}) {
  const panel = useRef<HTMLDivElement>(null);
  return (
    <RD.Root open={open} onOpenChange={onOpenChange}>
      <RD.Portal>
        <RD.Overlay className="fixed inset-0 z-40 bg-scrim/50" />
        <RD.Content ref={panel} tabIndex={-1} data-broker-dialog={marker ?? ""} aria-describedby={undefined}
          // Focus starts on the panel itself (its title is read out), not on the close button.
          onOpenAutoFocus={(e) => { e.preventDefault(); panel.current?.focus({ preventScroll: true }); }}
          className="fixed inset-x-0 bottom-0 z-50 flex max-h-[90dvh] flex-col rounded-t-panel border-t border-subtle bg-surface-1 pb-[env(safe-area-inset-bottom)] text-fg shadow-raised focus:outline-none md:inset-x-auto md:bottom-auto md:left-1/2 md:top-1/2 md:w-[440px] md:max-w-[calc(100vw-2rem)] md:-translate-x-1/2 md:-translate-y-1/2 md:rounded-panel md:border md:pb-0">
          <div className="flex items-start gap-space-3 px-space-5 pt-space-4">
            <RD.Title className="min-w-0 flex-1 pt-space-2 text-t18 font-semibold text-fg">{title}</RD.Title>
            <RD.Close aria-label="Close" className="-mr-space-2 flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
              <X size={18} aria-hidden />
            </RD.Close>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-space-5 pb-space-5 pt-space-3 text-t14">{children}</div>
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  );
}

/** The buttons of the broker screens: 44 px tall (a finger), brand only for the one main action. */
export const BTN_PRIMARY = "inline-flex h-11 items-center justify-center gap-space-2 rounded-control bg-brand px-space-5 text-t14 font-semibold text-on-brand hover:bg-brand-strong disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-surface";
export const BTN_SECONDARY = "inline-flex h-11 items-center justify-center gap-space-2 rounded-control border border-border bg-surface-2 px-space-4 text-t14 font-semibold text-fg hover:bg-surface-3 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";
