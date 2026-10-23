import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { cx } from "./cx";

export type ButtonVariant = "primary" | "secondary" | "outline" | "ghost" | "danger";
/** sm / md: the P1.2 sizes. v2: 44 px on a phone (a finger), 36 px with a mouse (UI v2 §8). */
export type ButtonSize = "sm" | "md" | "v2";

const VARIANT: Record<ButtonVariant, string> = {
  primary: "bg-brand text-on-brand hover:bg-brand-strong",
  secondary: "border border-border bg-surface-2 text-fg hover:bg-surface-3",
  // Chart + colour order Part 3 (3.1): every action that is not the screen's one main action. A border on the card's
  // own surface, so it reads as an outline on a card.
  outline: "border border-border bg-surface-1 text-fg hover:bg-surface-2",
  ghost: "text-fg-muted hover:bg-surface-2 hover:text-fg",
  // A destructive action (stop, delete). Never used for "sell" - direction is not danger.
  danger: "border border-down/50 bg-down/10 text-down hover:bg-down/20",
};
const SIZE: Record<ButtonSize, string> = { sm: "h-8 px-3 text-xs gap-1.5", md: "h-9 px-4 text-sm gap-2", v2: "h-11 text-t14 gap-space-2 md:h-9" };
const FOCUS = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-surface";

/**
 * The button's class string, for a link or a summary styled as a button (`<Link className={buttonClass(...)}>`).
 * Primary = the screen's ONE main action (Place order, Start, Approve, Connect) as a solid brand button; every other
 * action is `outline` or `ghost`. The e2e helper `e2e/solidBrand.ts` warns when a screen shows more than one.
 */
export function buttonClass({ variant = "secondary", size = "md", className }: { variant?: ButtonVariant; size?: ButtonSize; className?: string } = {}): string {
  const v2 = size === "v2";
  return cx("inline-flex shrink-0 items-center justify-center", v2 ? "rounded-control" : "rounded-md", "font-semibold transition-colors",
    v2 && "duration-fast", FOCUS, "disabled:cursor-not-allowed disabled:opacity-50",
    VARIANT[variant], SIZE[size], v2 && (variant === "primary" ? "px-space-5" : "px-space-4"), className);
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: ReactNode;
  loading?: boolean;
}

/** The one button. Primary = the page's main action (at most one per view); outline / secondary / ghost for the rest. */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", icon, loading = false, disabled, className, children, type = "button", ...rest }, ref,
) {
  return (
    <button ref={ref} type={type} disabled={disabled || loading} aria-busy={loading || undefined}
      className={buttonClass({ variant, size, className })}
      {...rest}>
      {loading ? <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" aria-hidden /> : icon}
      {children}
    </button>
  );
});
