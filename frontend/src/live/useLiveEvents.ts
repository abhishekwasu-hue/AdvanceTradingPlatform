import { useEffect, useRef } from "react";
import { apiUrl } from "../api/base";
import { getToken, tryRefresh } from "../api/client";
import { SseParser, nextDelay } from "./sse";

export type LiveTopic = "ai_actions" | "notifications";

type Listener = (topic: LiveTopic) => void;
const listeners = new Set<Listener>();
let running = false;

/** TM Part 1 (TM-12, flag `data_hub`): the data hub's events on the same stream - `ticks` (prices of the symbols this
 * tab registered), `hub` (the broker connection's state) and `stream` (this tab's own stream is up / down). */
export type LiveDataEvent = "ticks" | "hub" | "stream";
type DataListener = (event: LiveDataEvent, data: unknown) => void;
const dataListeners = new Set<DataListener>();

function newTabId(): string {
  const bytes = new Uint8Array(12);
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") crypto.getRandomValues(bytes);
  else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}
/** This tab's id on the live stream: the server keeps the tab's registered symbols under it (never another user's). */
export const TAB_ID = newTabId();

function tellData(event: LiveDataEvent, data: unknown) {
  dataListeners.forEach((l) => { try { l(event, data); } catch { /* one listener never stops the others */ } });
}

const wanted = () => listeners.size > 0 || dataListeners.size > 0;

/** One stream per app (not per component): every subscriber hears every topic. It runs while the tab is visible and
 * someone listens, reconnects after the server's 5-minute end or an error, and refreshes the access token on 401. */
async function loop() {
  running = true;
  let failures = 0;
  while (wanted()) {
    if (document.visibilityState !== "visible") { await new Promise((r) => setTimeout(r, 1000)); continue; }
    const token = getToken();
    if (!token) { await new Promise((r) => setTimeout(r, 3000)); continue; }
    try {
      const response = await fetch(apiUrl(`/live/stream?sid=${TAB_ID}`), { headers: { Authorization: `Bearer ${token}` }, credentials: "same-origin" });
      if (response.status === 401) { await tryRefresh(); failures++; }
      else if (!response.ok || !response.body) failures++;
      else {
        failures = 0;
        tellData("stream", { up: true });
        const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
        const parser = new SseParser();
        for (;;) {
          const { value, done } = await reader.read();
          if (done || !wanted()) { void reader.cancel().catch(() => undefined); break; }
          for (const e of parser.push(value)) {
            if (e.event === "ai_actions" || e.event === "notifications") listeners.forEach((l) => l(e.event as LiveTopic));
            else if (e.event === "ticks" || e.event === "hub") {
              try { tellData(e.event, JSON.parse(e.data)); } catch { /* a malformed event is skipped */ }
            }
          }
        }
      }
    } catch {
      failures++;
    }
    if (failures > 0) tellData("stream", { up: false });
    await new Promise((r) => setTimeout(r, nextDelay(failures)));
  }
  running = false;
}

/** Calls `onChange` (debounced to one call per 300 ms) when the server says one of `topics` changed - e.g. a
 * proposal approved on Telegram. The page then reloads its own data through the normal endpoints. */
export function useLiveEvents(topics: LiveTopic[], onChange: () => void): void {
  const callback = useRef(onChange);
  callback.current = onChange;
  const key = topics.join(",");
  useEffect(() => {
    const wanted = new Set(key.split(","));
    let timer: number | undefined;
    const listener: Listener = (topic) => {
      if (!wanted.has(topic)) return;
      window.clearTimeout(timer);
      timer = window.setTimeout(() => callback.current(), 300);
    };
    listeners.add(listener);
    if (!running && typeof fetch === "function" && typeof TextDecoderStream === "function") void loop();
    return () => { listeners.delete(listener); window.clearTimeout(timer); };
  }, [key]);
}

/** TM Part 1: listen to the data hub's events (`ticks`, `hub`, `stream`). Keeps the one stream open while listening;
 * returns the unsubscribe. */
export function onLiveData(listener: DataListener): () => void {
  dataListeners.add(listener);
  if (!running && typeof fetch === "function" && typeof TextDecoderStream === "function") void loop();
  return () => { dataListeners.delete(listener); };
}
