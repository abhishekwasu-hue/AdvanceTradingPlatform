import { test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync } from "node:fs";

/**
 * M95-Q7: how the link card looks when the address is shared, before the deploy (the real proof is Abhi's own
 * screenshot after it). A WhatsApp and a Telegram chat bubble are drawn at a phone's size from the REAL tags of
 * frontend/index.html and the real image - "before" from origin/main's index.html (logo-only tags), "after" from this
 * checkout. The bubbles are a likeness of the two apps' link previews, not the apps themselves:
 * - WhatsApp: a large image above the title when the image is wide (summary_large_image, 1200 x 630); a small square
 *   thumbnail beside the title when it is the square logo;
 * - Telegram: the site name, the title and the description beside an accent bar, a wide image below them or a small
 *   one at the top right.
 * Output: docs/design/m95/preview-<whatsapp|telegram>-<before|after>.png (deviceScaleFactor 1).
 * Run (from frontend/, after the image): `npx playwright test -c scripts/og-image/playwright.config.ts preview-proof`.
 */

const ROOT = new URL("../../../", import.meta.url);
const OUT = new URL("docs/design/m95/", ROOT);

interface Tags { title: string; description: string; image: string; card: string; url: string }

function tagsOf(html: string): Tags {
  const meta = (attr: string, key: string) => {
    const m = html.match(new RegExp(`<meta\\s+${attr}="${key}"\\s+content="([^"]*)"`));
    return m ? m[1] : "";
  };
  return { title: meta("property", "og:title"), description: meta("property", "og:description"), image: meta("property", "og:image"),
           card: meta("name", "twitter:card"), url: meta("property", "og:url") };
}

/** The image a crawler would fetch, read from this checkout's public/ folder (the address's path under the domain). */
function imageData(url: string, ref: "before" | "after"): string {
  const path = `frontend/public${new URL(url).pathname}`;
  const bytes = ref === "before" ? execFileSync("git", ["show", `origin/main:${path}`], { cwd: ROOT.pathname }) : readFileSync(new URL(path, ROOT));
  return `data:image/png;base64,${Buffer.from(bytes).toString("base64")}`;
}

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;");
const FONT = `"Inter", "Lohit Marathi", "FreeSans", sans-serif`;

function whatsapp(t: Tags, img: string): string {
  const wide = t.card === "summary_large_image";
  const host = new URL(t.url).host;
  const text = `<div class="t">${esc(t.title)}</div><div class="d">${esc(t.description)}</div><div class="h">${host}</div>`;
  return `<!doctype html><meta charset="utf-8"><style>
    body { margin: 0; width: 390px; height: 560px; background: #0b141a; font-family: ${FONT}; }
    .bar { height: 56px; background: #1f2c34; color: #e9edef; display: flex; align-items: center; padding: 0 16px; font-size: 17px; font-weight: 600; }
    .b { position: absolute; right: 10px; top: 78px; width: 300px; background: #005c4b; border-radius: 10px; padding: 4px; color: #e9edef; }
    .p { background: #025144; border-radius: 7px; overflow: hidden; display: ${wide ? "block" : "flex"}; }
    .p img { display: block; ${wide ? "width: 100%; aspect-ratio: 1200 / 630; object-fit: cover;" : "width: 84px; height: 84px; object-fit: cover; flex: none;"} }
    .x { padding: 7px 10px 8px; min-width: 0; }
    .t { font-size: 14px; font-weight: 600; line-height: 1.3; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
    .d { margin-top: 2px; font-size: 12.5px; line-height: 1.35; color: #c4d3cf; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
    .h { margin-top: 3px; font-size: 12px; color: #9fb7b1; }
    .m { padding: 6px 6px 2px; font-size: 14.5px; } .m a { color: #53bdeb; } .s { text-align: right; font-size: 11px; color: #9fb7b1; padding: 0 6px 2px; }
  </style><div class="bar">Chat</div><div class="b"><div class="p"><img src="${img}" alt=""><div class="x">${text}</div></div>
  <div class="m"><a>${esc(t.url)}</a></div><div class="s">10:24</div></div>`;
}

function telegram(t: Tags, img: string): string {
  const wide = t.card === "summary_large_image";
  return `<!doctype html><meta charset="utf-8"><style>
    body { margin: 0; width: 390px; height: 600px; background: #0e1621; font-family: ${FONT}; }
    .bar { height: 56px; background: #17212b; color: #f5f5f5; display: flex; align-items: center; padding: 0 16px; font-size: 17px; font-weight: 600; }
    .b { position: absolute; right: 10px; top: 78px; width: 310px; background: #2b5278; border-radius: 14px; padding: 8px 10px 6px; color: #fff; }
    .m a { color: #8cc8ff; font-size: 15px; }
    .p { margin-top: 6px; border-left: 3px solid #8cc8ff; padding: 2px 0 2px 9px; position: relative; }
    .n { font-size: 14px; font-weight: 600; color: #8cc8ff; } .t { font-size: 14px; font-weight: 600; margin-top: 1px; ${wide ? "" : "padding-right: 64px;"} }
    .d { font-size: 13.5px; line-height: 1.35; margin-top: 2px; ${wide ? "" : "padding-right: 64px;"} }
    .p img.w { display: block; width: 100%; margin-top: 7px; border-radius: 8px; aspect-ratio: 1200 / 630; object-fit: cover; }
    .p img.s { position: absolute; right: 0; top: 2px; width: 56px; height: 56px; border-radius: 6px; object-fit: cover; }
    .s { text-align: right; font-size: 11px; color: #a9c3dc; margin-top: 3px; }
  </style><div class="bar">Chat</div><div class="b"><div class="m"><a>${esc(t.url)}</a></div>
  <div class="p"><div class="n">AiAlgoDesk</div><div class="t">${esc(t.title)}</div><div class="d">${esc(t.description)}</div>
  <img class="${wide ? "w" : "s"}" src="${img}" alt=""></div><div class="s">10:24</div></div>`;
}

test.use({ viewport: { width: 390, height: 600 }, deviceScaleFactor: 1 });

for (const ref of ["before", "after"] as const) {
  test(`the link preview, ${ref}`, async ({ page }) => {
    const html = ref === "before" ? execFileSync("git", ["show", "origin/main:frontend/index.html"], { cwd: ROOT.pathname }).toString("utf8")
      : readFileSync(new URL("frontend/index.html", ROOT), "utf8");
    const tags = tagsOf(html);
    const img = imageData(tags.image, ref);
    mkdirSync(OUT, { recursive: true });
    for (const [name, make, height] of [["whatsapp", whatsapp, 560], ["telegram", telegram, 600]] as const) {
      await page.setViewportSize({ width: 390, height });
      await page.setContent(make(tags, img));
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({ path: new URL(`preview-${name}-${ref}.png`, OUT).pathname });
    }
  });
}
