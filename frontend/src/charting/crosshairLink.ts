/**
 * CH5: crosshair link between the workspace's charts. Each chart's engine reports its crosshair (`EngineEvent` with
 * the bar time); the link moves the crosshair of the charts that `peers(index)` names to the same time (each engine
 * puts it on its own bar at or before that time, so a 5m chart and a 1h chart line up).
 *
 * Only a move the user made is passed on (`user !== false`: the engine marks a move it did not get from a pointer);
 * lightweight-charts 5.2 does not even report a programmatic `setCrosshairPosition`. So nothing set by the link is fed
 * back to it - no echo bookkeeping that a stale entry could turn into a swallowed real move.
 */
import type { ChartEngine } from "./engine";

export class CrosshairLink {
  private engines = new Map<number, { engine: ChartEngine; off: () => void }>();
  private applying = false;

  constructor(private peers: (index: number) => number[]) {}

  /** Which charts follow which: called with the current layout's rule. */
  setPeers(peers: (index: number) => number[]) { this.peers = peers; }

  /** A chart's engine joins (or, with null, leaves) the link. */
  register(index: number, engine: ChartEngine | null) {
    this.engines.get(index)?.off();
    this.engines.delete(index);
    if (!engine) return;
    const off = engine.on((e) => { if (e.type === "crosshair" && e.user !== false) this.moved(index, e.time); });
    this.engines.set(index, { engine, off });
  }

  private moved(index: number, time: number | null) {
    if (this.applying) return;                             // a synchronous report while we move the others
    this.applying = true;
    try {
      for (const peer of this.peers(index)) this.engines.get(peer)?.engine.setCrosshair?.(time);
    } finally {
      this.applying = false;
    }
  }

  dispose() {
    for (const { off } of this.engines.values()) off();
    this.engines.clear();
  }
}
