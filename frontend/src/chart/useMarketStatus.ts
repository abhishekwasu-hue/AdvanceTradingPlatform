/**
 * UI2-4e: the public system status (maintenance, paused brokers, each exchange family's session) read once for every
 * part of the v2 app that shows it - the top bar, the Charts page and the charts on other pages - instead of one poll
 * each. Read when the first reader appears (unless a read is less than a minute old), then every minute while any
 * reader is on screen; a failed read keeps the last answer.
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

  constructor(private readonly read: () => Promise<SystemStatus>, private readonly now: () => number = () => Date.now()) {}

  readonly snapshot = (): StatusSnapshot => this.snap;

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    if (this.listeners.size === 1) {
      if (this.snap.at === 0 || this.now() - this.snap.at >= STATUS_EVERY_MS) void this.load();
      this.timer = setInterval(() => void this.load(), STATUS_EVERY_MS);
    }
    return () => {
      this.listeners.delete(listener);
      if (this.listeners.size === 0 && this.timer !== null) { clearInterval(this.timer); this.timer = null; }
    };
  };

  private async load(): Promise<void> {
    if (this.inFlight) return;
    this.inFlight = true;
    try {
      const status = await this.read();
      this.snap = { status, at: this.now() };
      for (const l of [...this.listeners]) l();
    } catch {
      /* the last answer stays */
    } finally {
      this.inFlight = false;
    }
  }
}

const shared = new StatusPoll(() => api.systemStatus());

export function useMarketStatus(): StatusSnapshot {
  return useSyncExternalStore(shared.subscribe, shared.snapshot, shared.snapshot);
}
