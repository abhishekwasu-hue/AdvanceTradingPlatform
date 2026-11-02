/**
 * M76 MP-14 L-2 (owner order M76 L-2; flag `copilot_workspace`): teach-on-chart. A tap on the chart finds the annotation
 * under it (a BOS / CHoCH or swing label on the tapped bar, an RSI divergence end, else the area band holding the tapped
 * price) and answers its one-line meaning from the ONE glossary (`ui/glossary.ts`, public structure words, no advice);
 * an area's state adds the reading's own state phrase as the server gave it (ATP's words, or the engine's public phrase
 * through the provider - never copied here). Pure; nothing here reads the candles or places anything.
 */
import { glossary, type GlossaryKey } from "../../ui/glossary";
import type { AnnotationArea, Annotations } from "./annotations";
import { annotationAreaLabel } from "./annotations";

export interface Teach { kind: "area" | "swing" | "event" | "divergence"; title: string; meaning: string; state: string | null }

const SWING: Record<string, GlossaryKey> = { HH: "swing-hh", HL: "swing-hl", LH: "swing-lh", LL: "swing-ll" };
const STATE: Record<string, GlossaryKey> = { fresh: "area-fresh", tested: "area-tested", flipped: "area-flipped", broken: "area-broken" };

/** An area's lesson: its side's meaning, and its state as the reading words it. Pure. */
export function teachArea(a: AnnotationArea): Teach {
  const side = glossary(a.side === "buyer" ? "buyer-area" : "seller-area");
  const stateKey = a.state ? STATE[a.state] : undefined;
  const state = a.state_words ? `${stateKey ? glossary(stateKey).term : "State"}: ${a.state_words}` : stateKey ? glossary(stateKey).definition : null;
  return { kind: "area", title: `${annotationAreaLabel(a)} · ${a.low_text} - ${a.high_text}`, meaning: side.definition, state };
}

/** The annotation at a tap (bar time and price from the chart's click), or null. Labels on the tapped bar win over a band;
 * of several bands holding the price, the narrowest. Pure. */
export function teachAt(a: Annotations | null, time: number | null, price: number | null): Teach | null {
  if (!a) return null;
  if (time != null) {
    const e = a.events.find((x) => x.time === time);
    if (e) {
      const g = glossary(e.type === "CHOCH" ? "choch" : "bos");
      return { kind: "event", title: `${e.name} · ${e.timeframe} · ${e.price_text}`, meaning: g.definition, state: null };
    }
    const s = a.swings.find((x) => x.time === time);
    if (s && SWING[s.label]) return { kind: "swing", title: `${s.label} · ${s.timeframe} · ${s.price_text}`, meaning: glossary(SWING[s.label]).definition, state: null };
    const d = a.divergence;
    if (d && (d.first.time === time || d.second.time === time)) {
      return { kind: "divergence", title: `RSI divergence · ${d.timeframe}`, meaning: glossary("rsi-divergence").definition, state: null };
    }
  }
  if (price == null) return null;
  const holding = a.areas.filter((x) => Math.min(x.low, x.high) <= price && price <= Math.max(x.low, x.high))
    .sort((x, y) => Math.abs(x.high - x.low) - Math.abs(y.high - y.low));
  return holding.length ? teachArea(holding[0]) : null;
}
