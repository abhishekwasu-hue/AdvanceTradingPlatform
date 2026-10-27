import { useSyncExternalStore } from "react";

/** TM Part 1: true while the broker gate's "Log in to <Broker>" card is on screen, so the live-prices notice above the
 * page never asks for the same login a second time (ONE renew prompt). A tiny external store; no data. */
let showing = false;
const listeners = new Set<() => void>();

export const gateShowing = {
  set(value: boolean) {
    if (value === showing) return;
    showing = value;
    listeners.forEach((l) => l());
  },
  get: () => showing,
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  },
};

export function useGateShowing(): boolean {
  return useSyncExternalStore(gateShowing.subscribe, gateShowing.get, () => false);
}
