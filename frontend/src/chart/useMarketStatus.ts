/**
 * UI2-4e: the public system status (maintenance, paused brokers, each exchange family's session) read once for every
 * part of the v2 app that shows it - the top bar, the Charts page and the charts on other pages - instead of one poll
 * each. Read when the first reader appears (unless a read is less than a minute old), then every minute while any
 * reader is on screen and the browser tab is shown; a tab shown again with an answer a minute old or older reads at
 * once. Nothing is read while the tab is hidden; a failed read keeps the last answer.
 */
import { useSyncExternalStore } from "react";
import { api } from "../api/client";
import type { SystemStatus } from "../types";

export const STATUS_EVERY_MS = 60_000;

export interface StatusSnapshot { status: SystemStatus | null; /** When the last answer came (ms; 0 before any). */ at: number }

/** One shared poll; the read and the clock are given (tests). */
export class StatusPoll {
  private snap: StatusSnapshot = { status: null, at: 0 };
  private listeners = new Set<() => void>();
  private timer: ReturnType<typeof setInterval> | null = null;
  private inFlight = false;

  constructor(private readonly read: () => Promise<SystemStatus>, private readonly now: () => number = () => Date.now(),
              private readonly visible: () => boolean = () => typeof document === "undefined" || document.visibilityState !== "hidden",
              private readonly events: Pick<Document, "addEventListener" | "removeEventListener"> | null =
                typeof document === "undefined" ? null : document) {}

  private stale(): boolean { return this.snap.at === 0 || this.now() - this.snap.at >= STATUS_EVERY_MS; }

  /** The tab is shown again: an answer a minute old or older is read again at once. */
  private readonly onVisibility = () => { if (this.visible() && this.stale()) void this.load(); };

  readonly snapshot = (): StatusSnapshot => this.snap;

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    if (this.listeners.size === 1) {
      if (this.stale()) void this.load();
      this.timer = setInterval(() => void this.load(), STATUS_EVERY_MS);
      this.events?.addEventListener("visibilitychange", this.onVisibility);
    }
    return () => {
      this.listeners.delete(listener);
      if (this.listeners.size > 0 || this.timer === null) return;
      clearInterval(this.timer);
      this.timer = null;
      this.events?.removeEventListener("visibilitychange", this.onVisibility);
    };
  };

  private async load(): Promise<void> {
    if (this.inFlight || !this.visible()) return;
    this.inFlight = true;
    try {
      const status = await this.read();
      this.snap = { status, at: this.now() };
      for (const l of [...this.listeners]) l();
    } catch {
      /* intentional: the last answer stays */
    } finally {
      this.inFlight = false;
    }
  }
}

const shared = new StatusPoll(() => api.systemStatus());

export function useMarketStatus(): StatusSnapshot {
  return useSyncExternalStore(shared.subscribe, shared.snapshot, shared.snapshot);
}
