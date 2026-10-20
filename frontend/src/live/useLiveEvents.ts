import { useEffect, useRef } from "react";
import { apiUrl } from "../api/base";
import { getToken, tryRefresh } from "../api/client";
import { SseParser, nextDelay } from "./sse";

export type LiveTopic = "ai_actions" | "notifications";

type Listener = (topic: LiveTopic) => void;
const listeners = new Set<Listener>();
let running = false;

/** One stream per app (not per component): every subscriber hears every topic. It runs while the tab is visible and
 * someone listens, reconnects after the server's 5-minute end or an error, and refreshes the access token on 401. */
async function loop() {
  running = true;
  let failures = 0;
  while (listeners.size > 0) {
    if (document.visibilityState !== "visible") { await new Promise((r) => setTimeout(r, 1000)); continue; }
    const token = getToken();
    if (!token) { await new Promise((r) => setTimeout(r, 3000)); continue; }
    try {
      const response = await fetch(apiUrl("/live/stream"), { headers: { Authorization: `Bearer ${token}` }, credentials: "same-origin" });
      if (response.status === 401) { await tryRefresh(); failures++; }
      else if (!response.ok || !response.body) failures++;
      else {
        failures = 0;
        const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
        const parser = new SseParser();
        for (;;) {
          const { value, done } = await reader.read();
          if (done || listeners.size === 0) { void reader.cancel().catch(() => undefined); break; }
          for (const e of parser.push(value)) {
            if (e.event === "ai_actions" || e.event === "notifications") listeners.forEach((l) => l(e.event as LiveTopic));
          }
        }
      }
    } catch {
      failures++;
    }
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
