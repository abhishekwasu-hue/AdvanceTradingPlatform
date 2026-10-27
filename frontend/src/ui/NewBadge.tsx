import { chipClass } from "./Chip";
import { useColourV2 } from "./colourV2";
import { GRADIENT_FILL } from "./accents";

/**
 * UI-H colour accents CA2: the "New" badge - the word "New" always, on the blue -> violet gradient while colour_v2 is
 * on (white on it is AA), the brand chip otherwise. The one badge for something newly added to ATP.
 */
export function NewBadge({ className = "" }: { className?: string }) {
  const colour = useColourV2();
  const look = colour
    ? `inline-flex h-5 shrink-0 items-center rounded-pill px-space-2 text-t12 font-semibold ${GRADIENT_FILL}`
    : chipClass({ tone: "brand" });
  return <span data-new-badge className={`${look} ${className}`}>New</span>;
}
