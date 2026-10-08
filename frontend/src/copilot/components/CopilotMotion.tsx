import { LazyMotion, MotionConfig } from "framer-motion";
import type { ReactNode } from "react";
import { useReducedMotion } from "../../theme";

// The animation features load after the page (framer-motion's `m` components are ~8 KB; the features ~24 KB).
const features = () => import("./motionFeatures").then((m) => m.default);

/** Motion for the Copilot: tab transitions, card stagger. Off entirely when motion is reduced. */
export default function CopilotMotion({ children }: { children: ReactNode }) {
  const reduced = useReducedMotion();
  return (
    <MotionConfig reducedMotion={reduced ? "always" : "never"} transition={{ duration: reduced ? 0 : 0.28, ease: [0.22, 1, 0.36, 1] }}>
      <LazyMotion features={features} strict>{children}</LazyMotion>
    </MotionConfig>
  );
}

export const STAGGER = {
  container: { hidden: {}, show: { transition: { staggerChildren: 0.06 } } },
  item: { hidden: { opacity: 0, y: 10 }, show: { opacity: 1, y: 0 } },
};
