/** PW4: a tiny parser for a Server-Sent Events byte stream (the app reads /api/live/stream with fetch, so the bearer
 * token rides in a header - EventSource cannot send one). Feed it text chunks; it returns the complete events. */
export interface SseEvent { event: string; data: string }

export class SseParser {
  private buffer = "";

  push(chunk: string): SseEvent[] {
    this.buffer += chunk.replace(/\r\n/g, "\n");
    const out: SseEvent[] = [];
    let cut = this.buffer.indexOf("\n\n");
    while (cut >= 0) {
      const block = this.buffer.slice(0, cut);
      this.buffer = this.buffer.slice(cut + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith(":")) continue;                 // comment / heartbeat
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (data.length || event !== "message") out.push({ event, data: data.join("\n") });
      cut = this.buffer.indexOf("\n\n");
    }
    return out;
  }
}

/** Reconnect delay: 2 s after a clean end, growing to 30 s while the server keeps failing. */
export function nextDelay(failures: number): number {
  return failures <= 0 ? 2000 : Math.min(30_000, 2000 * 2 ** Math.min(failures, 4));
}
