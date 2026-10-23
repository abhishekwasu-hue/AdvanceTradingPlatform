import { useEffect, useMemo, useState } from "react";
import { request } from "../api/client";
import { TAB_ID, onLiveData, type LiveDataEvent } from "./useLiveEvents";

/**
 * TM Part 1 (docs/design/DATA_HUB.md; flag `data_hub`, OFF by default): live prices from the data hub in the browser.
 *
 * Every page that shows prices registers its symbols here (charts, the watchlist). The store keeps this tab's union
 * (at most 50, in the order the pages asked), tells the server once (`PUT /api/live/ticks`, debounced, again every few
 * minutes while the page stays open) and hands each `ticks` event of the one live stream to the pages that show that
 * symbol. The server sends only the symbols this tab registered, from the user's own broker connection, at most once a
 * second per symbol. The same store keeps the connection's state (`hub` events, `GET /api/live/hub` on first use) for
 * the plain "reconnecting" / "log in again" notice. Nothing here places, changes or cancels an order.
 */
export interface LiveTick { symbol: string; exchange: string; ltp: number; ts: string }
export type HubState = "live" | "connecting" | "reconnecting" | "renew_needed" | "polling";
export interface HubStatus {
  state: HubState;
  broker: string;
  broker_name: string;
  account_label: string;
  message: string;
  since: string;
  retry_seconds: number | null;
}
export interface TickItem { symbol: string; exchange: string }

export const MAX_TAB_SYMBOLS = 50;
export const REGISTER_DEBOUNCE_MS = 400;
export const REREGISTER_MS = 240_000;
/** The tab's own stream down this long reads as "reconnecting" too (a blip of a second is not worth a notice). */
export const STREAM_DOWN_NOTICE_MS = 5_000;
export const RECONNECTING_TEXT = "Live prices are reconnecting. Prices may be a few seconds old until then.";

export const tickKey = (symbol: string, exchange: string): string => `${exchange.trim().toUpperCase()}:${symbol.trim().toUpperCase()}`;

type TickListener = (tick: LiveTick) => void;
type StatusListener = () => void;
type Put = (items: TickItem[]) => Promise<unknown>;
type Read = () => Promise<{ status: HubStatus | null }>;

export const liveTicksApi = {
  put: (items: TickItem[]) => request<unknown>("/live/ticks", { method: "PUT", body: JSON.stringify({ sid: TAB_ID, items }) }),
  status: () => request<{ status: HubStatus | null }>("/live/hub"),
};

/** The tab's live-price store (one per app; tests make their own with fakes). */
export class LiveTicksStore {
  private owners = new Map<number, TickItem[]>();
  private nextOwner = 1;
  private tickListeners = new Map<string, Set<TickListener>>();
  private statusListeners = new Set<StatusListener>();
  private latest = new Map<string, LiveTick>();
  private sentKey = "";
  private timer: ReturnType<typeof setTimeout> | null = null;
  private refresh: ReturnType<typeof setInterval> | null = null;
  private statusRead = false;
  status: HubStatus | null = null;
  streamDownSince: number | null = null;

  constructor(private readonly put: Put = liveTicksApi.put, private readonly read: Read = liveTicksApi.status,
              private readonly now: () => number = () => Date.now()) {}

  /** The symbols the tab shows now: every owner's, in order, without repeats, the first 50. */
  union(): TickItem[] {
    const seen = new Set<string>();
    const out: TickItem[] = [];
    for (const items of this.owners.values()) {
      for (const i of items) {
        const key = tickKey(i.symbol, i.exchange);
        if (seen.has(key) || !i.symbol) continue;
        seen.add(key);
        out.push({ symbol: i.symbol.trim().toUpperCase(), exchange: i.exchange.trim().toUpperCase() });
        if (out.length >= MAX_TAB_SYMBOLS) return out;
      }
    }
    return out;
  }

  /** A page's symbols (replaced wholesale on each call with the same owner). Returns the release. */
  register(items: readonly TickItem[], owner?: number): { owner: number; release: () => void } {
    const id = owner ?? this.nextOwner++;
    this.owners.set(id, [...items]);
    this.schedule();
    return { owner: id, release: () => { this.owners.delete(id); this.schedule(); } };
  }

  private schedule() {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = setTimeout(() => { this.timer = null; void this.send(false); }, REGISTER_DEBOUNCE_MS);
    if (this.refresh === null) this.refresh = setInterval(() => { void this.send(true); }, REREGISTER_MS);
  }

  /** Tell the server the tab's set (only when it changed, or `force` - the periodic re-registration). */
  async send(force: boolean): Promise<void> {
    const items = this.union();
    const key = items.map((i) => tickKey(i.symbol, i.exchange)).join(",");
    if (!force && key === this.sentKey) return;
    if (force && items.length === 0) return;
    this.sentKey = key;
    try {
      const reply = (await this.put(items)) as { status?: HubStatus | null } | undefined;
      if (reply && "status" in reply) this.setStatus(reply.status ?? null);
    } catch {
      this.sentKey = "";             // try again on the next change or refresh
    }
  }

  onTick(symbol: string, exchange: string, listener: TickListener): () => void {
    const key = tickKey(symbol, exchange);
    let set = this.tickListeners.get(key);
    if (!set) { set = new Set(); this.tickListeners.set(key, set); }
    set.add(listener);
    const last = this.latest.get(key);
    if (last) listener(last);
    return () => {
      const s = this.tickListeners.get(key);
      if (!s) return;
      s.delete(listener);
      if (s.size === 0) this.tickListeners.delete(key);
    };
  }

  last(symbol: string, exchange: string): LiveTick | undefined {
    return this.latest.get(tickKey(symbol, exchange));
  }

  onStatus(listener: StatusListener): () => void {
    this.statusListeners.add(listener);
    if (!this.statusRead) {
      this.statusRead = true;
      this.read().then((r) => this.setStatus(r?.status ?? null), () => { this.statusRead = false; });
    }
    return () => { this.statusListeners.delete(listener); };
  }

  /** Read the state again now (after the trader renewed the login). */
  reloadStatus() {
    this.statusRead = true;
    this.read().then((r) => this.setStatus(r?.status ?? null), () => { this.statusRead = false; });
  }

  setStatus(status: HubStatus | null) {
    this.status = status;
    this.statusListeners.forEach((l) => l());
  }

  /** One event of the live stream. */
  handle(event: LiveDataEvent, data: unknown) {
    if (event === "ticks") {
      const items = (data as { items?: unknown })?.items;
      if (!Array.isArray(items)) return;
      for (const raw of items) {
        const t = raw as Partial<LiveTick>;
        if (typeof t?.symbol !== "string" || typeof t.exchange !== "string" || typeof t.ltp !== "number" || !(t.ltp > 0)) continue;
        const tick: LiveTick = { symbol: t.symbol, exchange: t.exchange, ltp: t.ltp, ts: String(t.ts ?? "") };
        const key = tickKey(tick.symbol, tick.exchange);
        this.latest.set(key, tick);
        this.tickListeners.get(key)?.forEach((l) => l(tick));
      }
    } else if (event === "hub") {
      this.setStatus(data as HubStatus);
    } else if (event === "stream") {
      const up = !!(data as { up?: boolean })?.up;
      const before = this.streamDownSince;
      this.streamDownSince = up ? null : (before ?? this.now());
      if ((before === null) !== (this.streamDownSince === null)) this.statusListeners.forEach((l) => l());
    }
  }

  dispose() {
    if (this.timer !== null) clearTimeout(this.timer);
    if (this.refresh !== null) clearInterval(this.refresh);
    this.timer = null;
    this.refresh = null;
  }
}

/** The notice to show, or null: "log in again" when the broker ended the login, "reconnecting" while the broker
 * connection reconnects or this tab's own stream has been down a few seconds. Nothing while live / polling. Pure. */
export type HubNotice = { kind: "renew"; message: string; broker: string; brokerName: string; accountLabel: string } | { kind: "reconnecting"; message: string };
export function noticeFor(status: HubStatus | null, streamDownSince: number | null, now: number): HubNotice | null {
  if (status?.state === "renew_needed") {
    return { kind: "renew", message: status.message, broker: status.broker, brokerName: status.broker_name, accountLabel: status.account_label || "primary" };
  }
  if (status?.state === "reconnecting") return { kind: "reconnecting", message: status.message || RECONNECTING_TEXT };
  if (streamDownSince !== null && now - streamDownSince >= STREAM_DOWN_NOTICE_MS && status) return { kind: "reconnecting", message: RECONNECTING_TEXT };
  return null;
}

let shared: LiveTicksStore | null = null;
let unhook: (() => void) | null = null;

/** The app's store, connected to the live stream on first use. */
export function liveTicks(): LiveTicksStore {
  if (!shared) {
    shared = new LiveTicksStore();
    const store = shared;
    unhook = onLiveData((event, data) => store.handle(event, data));
  }
  return shared;
}

/** Tests only. */
export function resetLiveTicks() {
  unhook?.();
  shared?.dispose();
  shared = null;
  unhook = null;
}

/** The newest tick per symbol for `items` while `enabled` (the `data_hub` flag): registers them for this tab. */
export function useLiveTicks(items: readonly TickItem[], enabled: boolean): ReadonlyMap<string, LiveTick> {
  const [ticks, setTicks] = useState<ReadonlyMap<string, LiveTick>>(new Map());
  const key = useMemo(() => items.slice(0, MAX_TAB_SYMBOLS).map((i) => tickKey(i.symbol, i.exchange)).join(","), [items]);
  useEffect(() => {
    if (!enabled || !key) { setTicks(new Map()); return undefined; }
    const store = liveTicks();
    const list = key.split(",").map((k) => { const [exchange, ...rest] = k.split(":"); return { exchange, symbol: rest.join(":") }; });
    const { release } = store.register(list);
    const offs = list.map((i) => store.onTick(i.symbol, i.exchange, (t) => {
      setTicks((m) => { const n = new Map(m); n.set(tickKey(t.symbol, t.exchange), t); return n; });
    }));
    return () => { offs.forEach((off) => off()); release(); };
  }, [enabled, key]);
  return ticks;
}

/** The notice for the connection's state while `enabled`; re-evaluated each second while the tab's stream is down. */
export function useHubNotice(enabled: boolean): HubNotice | null {
  const [, bump] = useState(0);
  useEffect(() => {
    if (!enabled) return undefined;
    const store = liveTicks();
    const off = store.onStatus(() => bump((n) => n + 1));
    const timer = setInterval(() => { if (store.streamDownSince !== null) bump((n) => n + 1); }, 1000);
    return () => { off(); clearInterval(timer); };
  }, [enabled]);
  if (!enabled) return null;
  const store = liveTicks();
  return noticeFor(store.status, store.streamDownSince, Date.now());
}
