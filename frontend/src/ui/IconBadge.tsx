import type { LucideIcon } from "lucide-react";
import { cx } from "../components/primitives/cx";

/**
 * Colour v2 (owner, 23 Oct): an icon in a soft tinted circle, coloured by its meaning - the phone-settings look of the
 * Settings list and the Needs attention rows. The circle is a soft tint of the meaning (tokens `soft-*`), the icon the
 * meaning's own colour (3 : 1 on its circle, tokens.test.ts). Decoration only: the row's words carry the meaning.
 *
 * Tones: brand (Broker, an approval), ai (AI only), warn (needs action, a pause), down (risk limits, an error where
 * money or an order is at stake), up, teal (a category: Appearance, an alert), info (Trading defaults, tips),
 * neutral (Account, Team, Help).
 */
export type IconTone = "brand" | "ai" | "warn" | "down" | "up" | "teal" | "info" | "neutral";

const TONE: Record<IconTone, string> = {
  brand: "bg-soft-brand text-brand",
  ai: "bg-soft-ai text-ai",
  warn: "bg-soft-warn text-warn",
  down: "bg-soft-down text-down",
  up: "bg-soft-up text-up",
  teal: "bg-soft-teal text-teal-accent",
  info: "bg-soft-info text-info",
  neutral: "bg-surface-2 text-fg-muted",
};

export function iconBadgeClass(tone: IconTone, className?: string): string {
  return cx("flex h-9 w-9 shrink-0 items-center justify-center rounded-pill", TONE[tone], className);
}

export function IconBadge({ icon: Icon, tone, size = 18, className }: { icon: LucideIcon; tone: IconTone; size?: number; className?: string }) {
  return (
    <span aria-hidden data-icon-tone={tone} className={iconBadgeClass(tone, className)}>
      <Icon size={size} />
    </span>
  );
}
