import { useRef, type ReactNode } from "react";
import { cx } from "../../components/primitives";
import { useReducedMotion } from "../../theme";

/**
 * A glass card with a gentle 3D tilt toward the pointer (at most 6°), a depth shadow and a soft inner glow. The tilt
 * is CSS (perspective + rotateX/Y), driven by pointer moves; off when motion is reduced, on touch screens, and for
 * keyboard users (focus never tilts). `depth` lifts the card a little more (the equity-curve card).
 */
export const MAX_TILT_DEG = 6;

/** The tilt a card of this width gets: the full 6° up to 360 px wide, proportionally less on wider cards (a wide card
 * at 6° moves its far edge too much under the pointer). */
export function tiltFor(width: number): number {
  return width <= 360 ? MAX_TILT_DEG : (MAX_TILT_DEG * 360) / width;
}

export default function TiltCard({ children, className, depth = false, glow = "ai", tilt = true, as: Tag = "div", ...rest }: {
  children: ReactNode; className?: string; depth?: boolean; glow?: "ai" | "up" | "down" | "none"; as?: "div" | "section" | "article";
  /** false for a card holding a form - controls never move under the pointer. */
  tilt?: boolean;
} & Omit<React.HTMLAttributes<HTMLElement>, "children" | "className">) {
  const ref = useRef<HTMLElement | null>(null);
  const reduced = useReducedMotion();
  const frame = useRef<number | null>(null);

  const move = (e: React.PointerEvent) => {
    if (!tilt || reduced || e.pointerType !== "mouse" || !ref.current) return;
    const el = ref.current;
    const r = el.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width - 0.5;
    const y = (e.clientY - r.top) / r.height - 0.5;
    const max = tiltFor(r.width);
    if (frame.current) cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => {
      el.style.setProperty("--tilt-x", `${(-y * 2 * max).toFixed(2)}deg`);
      el.style.setProperty("--tilt-y", `${(x * 2 * max).toFixed(2)}deg`);
      el.style.setProperty("--glare-x", `${((x + 0.5) * 100).toFixed(1)}%`);
      el.style.setProperty("--glare-y", `${((y + 0.5) * 100).toFixed(1)}%`);
    });
  };
  const leave = () => {
    const el = ref.current;
    if (!el) return;
    el.style.setProperty("--tilt-x", "0deg");
    el.style.setProperty("--tilt-y", "0deg");
  };

  const glowClass = glow === "none" ? "" : glow === "up" ? "copilot-glow-up" : glow === "down" ? "copilot-glow-down" : "copilot-glow-ai";
  return (
    <Tag ref={ref as never} onPointerMove={move} onPointerLeave={leave} {...rest}
         className={cx(tilt && "copilot-tilt", "copilot-glass relative rounded-2xl p-4", depth && "copilot-depth", glowClass, className)}>
      {children}
    </Tag>
  );
}
