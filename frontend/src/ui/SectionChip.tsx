import type { LucideIcon } from "lucide-react";
import type { ShellKey } from "../shell/navModel";
import { sectionChipClass, sectionTintOf } from "./accents";

/**
 * UI-H CA3: a navigation icon in its section's round tint chip (the sidebar, the More sheet; PageHeader draws the
 * larger one). Null when the location has no section - the caller then shows its plain icon.
 */
export function SectionChip({ navKey, icon: Icon, size = "sm", iconSize = 16 }: {
  navKey: ShellKey; icon: LucideIcon; size?: "sm" | "md" | "lg"; iconSize?: number;
}) {
  const tint = sectionTintOf(navKey);
  if (!tint) return null;
  return <span aria-hidden data-section-chip={tint} className={sectionChipClass(tint, size)}><Icon size={iconSize} /></span>;
}
