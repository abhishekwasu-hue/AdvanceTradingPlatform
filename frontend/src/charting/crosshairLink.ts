/**
 * CH5: crosshair link between the workspace's charts. Each chart's engine reports its crosshair (`EngineEvent` with
 * the bar time); the link moves the crosshair of the charts that `peers(index)` names to the same time (each engine
 * puts it on its own bar at or before that time, so a 5m chart and a 1h chart line up). Moving a chart's crosshair
 * makes that chart report it too: those echoes are recognised and dropped, so the charts never ping-pong.
 */
import type { ChartEngine } from "./engine";

export class CrosshairLink {
  private engines = new Map<number, { engine: ChartEngine; off: () => void }>();
  private expected = new Map<number, number | null>();      // the time this link last set on a chart (its echo)
  private applying = false;

  constructor(private peers: (index: number) => number[]) {}

  /** Which charts follow which: called with the current layout's rule. */
  setPeers(peers: (index: number) => number[]) { this.peers = peers; }

  /** A chart's engine joins (or, with null, leaves) the link. */
  register(index: number, engine: ChartEngine | null) {
    this.engines.get(index)?.off();
    this.engines.delete(index);
    this.expected.delete(index);
    if (!engine) return;
    const off = engine.on((e) => { if (e.type === "crosshair") this.moved(index, e.time); });
    this.engines.set(index, { engine, off });
  }

  private moved(index: number, time: number | null) {
    if (this.applying) {                                   // an echo raised while we were setting it (synchronous)
      if (this.expected.has(index) && this.expected.get(index) === time) this.expected.delete(index);
      return;
    }
    if (this.expected.has(index)) {                        // our own echo: consume it, do not pass it on
      const echo = this.expected.get(index);
      this.expected.delete(index);
      if (echo === time) return;
    }
    this.applying = true;
    try {
      for (const peer of this.peers(index)) {
        const target = this.engines.get(peer)?.engine;
        if (!target?.setCrosshair) continue;
        this.expected.set(peer, time);
        target.setCrosshair(time);
      }
    } finally {
      this.applying = false;
    }
  }

  dispose() {
    for (const { off } of this.engines.values()) off();
    this.engines.clear();
    this.expected.clear();
  }
}
