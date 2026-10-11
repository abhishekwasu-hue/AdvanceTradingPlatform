import { Component, lazy, Suspense, useEffect, useState, type ReactNode } from "react";
import { useAppearance, useReducedMotion } from "../../theme";
import AICoreFallback from "./AICoreFallback";
import { webglAvailable, type CoreTone, type CoreVariant } from "./aiCoreTypes";

const AICore3D = lazy(() => import("./AICore3D"));

function isPhone(): boolean {
  return typeof window !== "undefined" && (window.matchMedia?.("(max-width: 640px)").matches || (navigator.hardwareConcurrency ?? 8) <= 4);
}

/** Waits for an idle moment (or 1.2 s) so the 3D chunk never competes with the page's own first requests. */
function useIdle(): boolean {
  const [idle, setIdle] = useState(false);
  useEffect(() => {
    const w = window as Window & { requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number; cancelIdleCallback?: (h: number) => void };
    if (w.requestIdleCallback) {
      const h = w.requestIdleCallback(() => setIdle(true), { timeout: 1200 });
      return () => w.cancelIdleCallback?.(h);
    }
    const h = window.setTimeout(() => setIdle(true), 300);
    return () => window.clearTimeout(h);
  }, []);
  return idle;
}

/** ATP review 10: the 3D core has its own error boundary - a WebGL scene that throws while rendering, or a 3D chunk
 * that fails to load, shows the SVG core in its place; the page and the app-level boundary never see it. */
export class CoreBoundary extends Component<{ fallback: ReactNode; onError?: () => void; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    console.warn("AI Core 3D failed; showing the still core instead", error);
    this.props.onError?.();
  }

  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

/** The hero AI Core: the SVG core first; the WebGL scene replaces it once the browser is idle - never when motion is
 * reduced (system or the "Reduce motion" setting) or WebGL is unavailable. */
export default function AICore({ tone, variant = "orb", className }: { tone: CoreTone; variant?: CoreVariant; className?: string }) {
  const reduced = useReducedMotion();
  const [, , theme] = useAppearance();
  const idle = useIdle();
  const [gl] = useState(webglAvailable);
  const [failed, setFailed] = useState(false);
  const fallback = <AICoreFallback tone={tone} className={className} />;
  if (reduced || !gl || !idle || failed) return fallback;
  return (
    <CoreBoundary fallback={fallback} onError={() => setFailed(true)}>
      <Suspense fallback={fallback}>
        <AICore3D tone={tone} variant={variant} lowPower={isPhone()} light={theme === "light"} className={className} onFail={() => setFailed(true)} />
      </Suspense>
    </CoreBoundary>
  );
}
