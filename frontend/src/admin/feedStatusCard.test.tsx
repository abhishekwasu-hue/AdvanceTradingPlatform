import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import FeedStatusCard, { connectionState, feedIsOff, feedState, symbolName, tickText, tickTone } from "./FeedStatusCard";
import cardSource from "./FeedStatusCard.tsx?raw";
import type { MarketFeed } from "./consoleApi";

/** M71 PL-4: the admin "Market data feed" card - on / off / reconnecting / stale tick / pending gaps; read-only. */

const feed = (over: Partial<MarketFeed> = {}): MarketFeed => ({
  enabled: true, credential: "owner", state: "live", broker: "upstox", note: "", day: "2026-10-22",
  connections: [
    { id: "upstox-0", broker: "upstox", state: "live", symbols: 1500, last_tick_at: "2026-10-22T05:29:58+00:00", last_tick_age_seconds: 2, reconnects_today: 1 },
    { id: "upstox-1", broker: "upstox", state: "live", symbols: 620, last_tick_at: "2026-10-22T05:29:57+00:00", last_tick_age_seconds: 3, reconnects_today: 0 },
  ],
  symbols_subscribed: 2120, symbols_wanted: 2180, symbols_over_limit: 60, symbols_held_back: 4, per_connection_cap: 1500, max_connections: 2,
  universe_at: "2026-10-22T05:25:00+00:00", last_tick_age_seconds: 2, reconnects_today: 1, gaps_detected_today: 3, gaps_filled_today: 3,
  gaps_pending: 0, pending_gaps: [], bars_written_today: 412345, late_ticks_today: 7, stock_bars_without_volume: 0,
  updated_at: "2026-10-22T05:30:00+00:00", ...over,
});
const html = (f: MarketFeed | undefined, marketOpen = true) => renderToStaticMarkup(<FeedStatusCard feed={f} marketOpen={marketOpen} />);

describe("market data feed card", () => {
  it("shows the live feed's numbers first", () => {
    const h = html(feed());
    expect(h).toContain('data-feed-state="live"');
    expect(h).toContain(">Live<");
    expect(h).toMatch(/data-feed-stat="subscribed"[^]*?2,120[^]*?of 2,180 wanted/);
    expect(h).toMatch(/data-feed-stat="over-limit" data-tone="warn"[^]*?>60</);
    expect(h).toMatch(/data-feed-stat="last-tick" data-tone="ok"[^]*?2 s/);
    expect(h).toMatch(/data-feed-stat="gaps" data-tone="plain"[^]*?3 filled · 0 pending/);
    expect(h).toContain("4,12,345"); // bars written today, Indian grouping from the one formatter
    expect(h).toContain('data-feed-connection="2"');
    expect(h).toMatch(/data-feed-connection="2"[^>]*><td[^>]*><span[^>]*>2</);
    expect(h).not.toContain("upstox-1"); // no connection ids on screen
    expect(h).not.toContain("data-feed-gap");
    expect(h).not.toContain("data-feed-off");
  });

  it("says the feed is off in one line; the setting name only under Advanced", () => {
    for (const f of [feed({ enabled: false, state: "off", connections: [] }), feed({ credential: "tenant" })]) {
      expect(feedIsOff(f)).toBe(true);
      const h = html(f);
      expect(h).toContain("Live feed is off. The owner turns it on in the server settings.");
      expect(h).not.toContain("data-feed-stat=");
      const [main, advanced] = h.split('data-details="console.market-feed.advanced"');
      expect(main).not.toContain("MARKET_FEED");
      expect(advanced).toContain("MARKET_FEED_ENABLED");
    }
    expect(feedState(feed({ enabled: false })).label).toBe("Off");
  });

  it("names each state by meaning", () => {
    expect(feedState(feed())).toEqual({ tone: "ok", label: "Live" });
    expect(feedState(feed({ state: "reconnecting" }))).toEqual({ tone: "warn", label: "Reconnecting" });
    expect(feedState(feed({ state: "no_session" })).tone).toBe("warn");
    expect(feedState(feed({ state: "closed" }))).toEqual({ tone: "off", label: "Market closed" });
    expect(feedState(feed({ state: "error" }))).toEqual({ tone: "bad", label: "Down" });
    expect(connectionState("renew_needed")).toEqual({ tone: "bad", label: "Login expired" });
    expect(connectionState("connecting").tone).toBe("warn");
  });

  it("shows a reconnecting connection amber", () => {
    const f = feed({ state: "reconnecting", connections: [{ ...feed().connections[0], state: "reconnecting", last_tick_age_seconds: 45 }] });
    const h = html(f);
    expect(h).toContain('data-feed-state="reconnecting"');
    expect(h).toMatch(/data-feed-connection="1"[^]*?data-tone="warn"[^]*?Reconnecting[^]*?text-warn[^]*?45 s/);
  });

  it("colours a stale tick amber after 30 s and red after 120 s, only while the market is open", () => {
    expect(tickTone(30, true)).toBe("ok");
    expect(tickTone(31, true)).toBe("warn");
    expect(tickTone(120, true)).toBe("warn");
    expect(tickTone(121, true)).toBe("bad");
    expect(tickTone(900, false)).toBe("plain");
    expect(tickTone(null, true)).toBe("plain");
    expect(tickText(null)).toBe("No tick yet");
    expect(html(feed({ last_tick_age_seconds: 300 }))).toMatch(/data-feed-stat="last-tick" data-tone="bad"[^]*?300 s/);
    expect(html(feed({ last_tick_age_seconds: 300 }), false)).toMatch(/data-feed-stat="last-tick" data-tone="plain"[^]*?300 s[^]*?market closed/);
  });

  it("lists pending gaps behind a Details, with readable symbols and times", () => {
    const h = html(feed({ gaps_detected_today: 4, gaps_filled_today: 1, gaps_pending: 3, pending_gaps: [
      { symbol: "NSE_INDEX|Nifty 50", interval: "1m", start: "2026-10-22T04:01:00+00:00", end: "2026-10-22T04:04:00+00:00", status: "requested", detected_at: null },
      { symbol: "NSE_EQ|INE002A01018", interval: "60m", start: "2026-10-22T04:00:00+00:00", end: "2026-10-22T05:00:00+00:00", status: "failed", detected_at: null },
    ] }));
    expect(h).toMatch(/data-feed-stat="gaps" data-tone="warn"[^]*?1 filled · 3 pending/);
    expect(h).toContain('data-details="console.market-feed.gaps"');
    expect(h).toContain("Pending gaps (3)");
    expect(h).toContain("The latest 2 are shown.");
    expect(h).toMatch(/data-feed-gap="NSE_INDEX\|Nifty 50"[^]*?>Nifty 50<[^]*?1 min[^]*?09:31–09:34 IST[^]*?Filling/);
    expect(h).toMatch(/1 hour[^]*?Could not fill/);
    expect(symbolName("NSE_INDEX|Nifty Bank")).toBe("Nifty Bank");
  });

  it("renders nothing on a server without the field, reads only, uses tokens", () => {
    expect(html(undefined)).toBe("");
    expect(cardSource).not.toMatch(/method:\s*"(POST|PUT|PATCH|DELETE)"|fetch\(|request</);
    expect(cardSource).not.toMatch(/text-\[\d+px\]|#[0-9a-f]{6}\b|\.toFixed\(/i);
  });
});
