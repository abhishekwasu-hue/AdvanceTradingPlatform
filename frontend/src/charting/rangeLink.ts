/**
 * CEPE-1 (owner 13 Nov: CE and the same-strike PE, one below the other): scroll / zoom link between charts. Each
 * engine reports its visible time range (`visibleRangeChanged`, seconds); the link shows the same range on the other
 * charts. The library also reports a range it was given in code, so each chart remembers the range the link last
 * gave it and a report of that range (or any report just after it: the library snaps a range to its own bars) is not
 * passed on - no echo, no ping-pong between two charts whose bars differ. Each chart keeps its own price axis.
 */
import type { ChartEngine } from "./engine";

/** Two ranges closer than this (seconds) are the same range (the library rounds to its bars). */
const SAME = 1;
/** A report this soon (ms) after the link moved a chart is that move's echo. */
const ECHO_MS = 250;

export class RangeLink {
  private engines = new Map<number, { engine: ChartEngine; off: () => void; given: { from: number; to: number; at: number } | null }>();

  constructor(private readonly now: () => number = () => Date.now()) {}

  /** A chart's engine joins (or, with null, leaves) the link. */
  register(index: number, engine: ChartEngine | null) {
    this.engines.get(index)?.off();
    this.engines.delete(index);
    if (!engine) return;
    const entry = { engine, off: () => {}, given: null as { from: number; to: number; at: number } | null };
    entry.off = engine.on((e) => { if (e.type === "visibleRangeChanged") this.moved(index, e.from, e.to); });
    this.engines.set(index, entry);
  }

  private moved(index: number, from: number, to: number) {
    const own = this.engines.get(index);
    if (!own) return;
    const given = own.given;
    own.given = null;
    if (given && ((Math.abs(given.from - from) < SAME && Math.abs(given.to - to) < SAME) || this.now() - given.at < ECHO_MS)) return;   // the echo of our own move
    for (const [i, peer] of this.engines) {
      if (i === index || !peer.engine.setVisibleTimeRange) continue;
      peer.given = { from, to, at: this.now() };
      peer.engine.setVisibleTimeRange(from, to);
    }
  }

  dispose() {
    for (const { off } of this.engines.values()) off();
    this.engines.clear();
  }
}
