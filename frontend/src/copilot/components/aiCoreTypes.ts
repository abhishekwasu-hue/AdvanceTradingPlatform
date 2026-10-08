/** The AI Core's colour follows the market regime; its shape is one of two designs (shown side by side at G-UI). */
export type CoreTone = "bullish" | "bearish" | "neutral";
export type CoreVariant = "orb" | "particles";

/** A day type or a direction from the briefing / thesis -> the core's tone. Anything unknown is neutral. */
export function toneFor(kind: string | null | undefined): CoreTone {
  const k = (kind ?? "").toUpperCase();
  if (k === "TREND_UP" || k === "BULLISH" || k === "RISK_ON" || k === "TRENDING_UP") return "bullish";
  if (k === "TREND_DOWN" || k === "BEARISH" || k === "RISK_OFF" || k === "TRENDING_DOWN") return "bearish";
  return "neutral";
}

let probed: boolean | null = null;

/** True when this browser can draw WebGL. A test or an old device without it gets the SVG core. Probed once per page
 * load, and the probe's context is released at once (browsers cap the number of live WebGL contexts). */
export function webglAvailable(): boolean {
  if (probed !== null) return probed;
  try {
    if (typeof document === "undefined") return false;
    const c = document.createElement("canvas");
    const gl = (c.getContext("webgl2") || c.getContext("webgl")) as WebGLRenderingContext | null;
    probed = !!gl;
    gl?.getExtension("WEBGL_lose_context")?.loseContext();
  } catch {
    probed = false;
  }
  return probed;
}
