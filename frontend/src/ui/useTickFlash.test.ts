import { describe, expect, it } from "vitest";
import { flashClass, flashFor } from "./useTickFlash";
import { FEED_TEXT, feedState, nseOpen } from "../shell/FeedDot";
import type { HubStatus } from "../live/liveTicks";

const hub = (state: HubStatus["state"]): HubStatus => ({ state, broker: "upstox", broker_name: "Upstox", account_label: "", message: "", since: "", retry_seconds: null });

describe("UX-7: live feel", () => {
  it("a price flashes up or down only when it changes", () => {
    expect(flashFor(100, 101)).toBe("up");
    expect(flashFor(101, 100.5)).toBe("down");
    expect(flashFor(100, 100)).toBeNull();
    expect(flashFor(null, 100)).toBeNull();                 // the first value never flashes
    expect(flashFor(100, null)).toBeNull();
    expect(flashFor(100, Number.NaN)).toBeNull();
    expect(flashClass("up")).toBe("bg-up-tint");
    expect(flashClass("down")).toBe("bg-down-tint");
    expect(flashClass(null)).toBe("");
  });

  it("the header dot: login needed first, then market closed, then the connection's state", () => {
    const now = 100_000;
    expect(feedState(hub("renew_needed"), null, false, now)).toBe("renew");
    expect(feedState(hub("live"), null, false, now)).toBe("closed");
    expect(feedState(null, null, true, now)).toBeNull();      // nothing known yet: no dot
    expect(feedState(hub("live"), null, true, now)).toBe("live");
    expect(feedState(hub("polling"), null, true, now)).toBe("polling");
    expect(feedState(hub("reconnecting"), null, true, now)).toBe("reconnecting");
    expect(feedState(hub("connecting"), null, null, now)).toBe("reconnecting");
    expect(feedState(hub("live"), now - 1_000, true, now)).toBe("live");          // a blip is not worth a change
    expect(feedState(hub("live"), now - 10_000, true, now)).toBe("reconnecting");
    expect(nseOpen({ maintenance_mode: false, maintenance_message: null, disabled_brokers: [], sessions: { NSE: { is_open: false, reason: "", next_open: null } } })).toBe(false);
    expect(nseOpen(null)).toBeNull();
  });

  it("every state names itself in words, not only a colour, from tokens", () => {
    for (const t of Object.values(FEED_TEXT)) {
      expect(t.label.length).toBeGreaterThan(2);
      expect(t.title.length).toBeGreaterThan(10);
      expect(t.dot).toMatch(/^bg-(up|warn|down|fg-muted)$/);
    }
  });
});
