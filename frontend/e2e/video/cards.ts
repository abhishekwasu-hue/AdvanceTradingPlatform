import type { Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { CARD_CSS } from "./director";

/** One intro / outro card: the file name and its lines (already in the episode's language). */
export interface CardSpec { file: string; kicker?: string; title: string; lines?: string[]; foot?: string }

const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;" })[c] as string);

/** M92 V4: draws the intro and outro cards of an episode, in its language (Chromium shapes Devanagari correctly). */
export async function renderCards(page: Page, cards: CardSpec[], dir: string): Promise<void> {
  mkdirSync(dir, { recursive: true });
  for (const c of cards) {
    const lines = (c.lines ?? []).map((l) => `<p>${esc(l)}</p>`).join("");
    await page.setContent(`<!doctype html><html><head><meta charset="utf-8"><style>${CARD_CSS}
      #atp-video-card h1{font-size:6.4vmin} #atp-video-card .kicker{font-size:3vmin;color:#93c5fd;letter-spacing:.08em;margin-bottom:2vmin}
      #atp-video-card .foot{font-size:2.8vmin;color:#fbbf24;margin-top:4vmin}</style></head>
      <body style="margin:0;background:#0f172a"><div id="atp-video-card"><div class="wrap">
      ${c.kicker ? `<div class="kicker">${esc(c.kicker)}</div>` : ""}<h1>${esc(c.title)}</h1>${lines}
      ${c.foot ? `<div class="foot">${esc(c.foot)}</div>` : ""}</div></div></body></html>`);
    await page.screenshot({ path: join(dir, c.file) });
  }
}
