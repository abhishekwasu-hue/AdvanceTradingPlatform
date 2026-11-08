import type { Locator, Page } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

/**
 * M92 V2: takes the frames of a tutorial scene. A scene is a list of BEATS; one beat = one line of the voice-over
 * (docs/video/scripts/<episode>.<lang>.json, `shot` = the beat id). For each beat the director saves one still of the
 * real app with a cursor highlight on the element the line talks about, and writes the element's box (0..1 of the
 * frame) to beats.json; docs/video/tools/build.py zooms on that box while the line is spoken, then shows the click.
 *
 * Stills, not a screen recording: each beat lasts exactly as long as its spoken line in either language, so one capture
 * serves the English and the Marathi cut, and a UI change is re-captured with one command (docs/video/README.md).
 */

export type Form = "desktop" | "phone";
export interface Box { x: number; y: number; w: number; h: number }
interface Beat { file: string; focus: Box | null; click: boolean }

const OVERLAY_ID = "atp-video-cursor";
const CARD_ID = "atp-video-card";

/** The style every card and the cursor use (the app's own font family; Lohit Marathi for Devanagari). */
export const CARD_CSS = `
  #${CARD_ID}{position:fixed;inset:0;z-index:2147483646;display:flex;align-items:center;justify-content:center;
    background:#0f172a;color:#f8fafc;font-family:'Inter','Lohit Marathi','FreeSans',sans-serif;padding:6vmin;box-sizing:border-box}
  #${CARD_ID} .wrap{max-width:92%;width:100%}
  @media (orientation: portrait){#${CARD_ID} .wrap{zoom:1.25}}
  #${CARD_ID} h1{font-size:5.2vmin;margin:0 0 3vmin;font-weight:700;line-height:1.25}
  #${CARD_ID} h2{font-size:3.6vmin;margin:0 0 2vmin;color:#93c5fd;font-weight:600}
  #${CARD_ID} p,#${CARD_ID} li{font-size:3.2vmin;line-height:1.45;margin:0 0 1.4vmin}
  #${CARD_ID} ul{padding-left:4vmin;margin:0}
  #${CARD_ID} .flow{display:flex;flex-wrap:wrap;gap:2vmin;align-items:stretch}
  #${CARD_ID} .step{flex:1 1 15%;min-width:22vmin;background:#1e293b;border:0.4vmin solid #3b82f6;border-radius:2vmin;padding:2vmin}
  #${CARD_ID} .step b{display:block;font-size:3.4vmin;color:#bfdbfe;margin-bottom:1vmin}
  #${CARD_ID} .step span{font-size:2.6vmin;color:#cbd5e1}
  #${CARD_ID} .red{border:0.5vmin solid #ef4444;background:#2a1215;border-radius:2vmin;padding:3vmin}
  #${CARD_ID} .red li{color:#fecaca}
  #${CARD_ID} .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(30vmin,1fr));gap:1vmin 3vmin}
  #${CARD_ID} .grid div{font-size:2.6vmin;color:#e2e8f0;border-bottom:0.2vmin solid #334155;padding:0.6vmin 0}
  #${CARD_ID} .grid i{font-style:normal;color:#93c5fd;float:right}
  #${CARD_ID} .yes{border-color:#22c55e}#${CARD_ID} .no{border-color:#f59e0b}
  #${CARD_ID} .note{font-size:2.6vmin;color:#94a3b8;margin-top:3vmin}
  #${OVERLAY_ID}{position:fixed;z-index:2147483647;pointer-events:none;width:44px;height:44px;margin:-22px 0 0 -22px;border-radius:50%;
    border:4px solid #f59e0b;background:rgba(245,158,11,.22);box-shadow:0 0 0 6px rgba(245,158,11,.25);transition:none}
  #${OVERLAY_ID}::after{content:"";position:absolute;left:50%;top:50%;width:8px;height:8px;margin:-4px 0 0 -4px;border-radius:50%;background:#b45309}
  [data-video-focus]{outline:3px solid #f59e0b !important;outline-offset:3px !important;border-radius:6px}
`;

export class Director {
  private readonly beats: Record<string, Beat> = {};
  readonly problems: string[] = [];
  /** The API calls no mock answered (videoWorld), saved with the beats so a scene can be given what it lacks. */
  unmocked: string[] = [];

  constructor(readonly page: Page, readonly dir: string, readonly form: Form) {
    mkdirSync(dir, { recursive: true });
  }

  get phone(): boolean { return this.form === "phone"; }

  private async style(): Promise<void> {
    await this.page.evaluate(([css]) => {
      if (document.getElementById("atp-video-style")) return;
      const s = document.createElement("style");
      s.id = "atp-video-style";
      s.textContent = css;
      document.head.appendChild(s);
    }, [CARD_CSS]);
  }

  private async clear(): Promise<void> {
    await this.page.evaluate(([cursor, card]) => {
      document.getElementById(cursor)?.remove();
      document.getElementById(card)?.remove();
      document.querySelectorAll("[data-video-focus]").forEach((e) => e.removeAttribute("data-video-focus"));
    }, [OVERLAY_ID, CARD_ID]);
  }

  /** The element's box as a share of the viewport (the zoom target), or null when it is not on screen. */
  private async box(target: Locator | null): Promise<Box | null> {
    if (!target) return null;
    const vp = this.page.viewportSize();
    const b = await target.boundingBox().catch(() => null);
    if (!vp || !b || b.width <= 0 || b.height <= 0) return null;
    const x = Math.max(0, b.x), y = Math.max(0, b.y);
    const w = Math.min(vp.width, b.x + b.width) - x, h = Math.min(vp.height, b.y + b.height) - y;
    if (w <= 0 || h <= 0) return null;
    return { x: x / vp.width, y: y / vp.height, w: w / vp.width, h: h / vp.height };
  }

  /** One still for beat `id`. `focus` gets the outline, the cursor ring and the zoom; `cursor: false` leaves the ring off. */
  async shot(id: string, focus: Locator | null = null, opts: { cursor?: boolean; click?: boolean } = {}): Promise<void> {
    await this.style();
    await this.clear();
    let box: Box | null = null;
    if (focus) {
      await focus.scrollIntoViewIfNeeded({ timeout: 5_000 }).catch(() => undefined);
      box = await this.box(focus);
      if (!box) this.problems.push(`${id}: the focus element is not on screen`);
      await focus.evaluate((e) => e.setAttribute("data-video-focus", "1")).catch(() => undefined);
      const b = await focus.boundingBox().catch(() => null);
      if (b && opts.cursor !== false) {
        await this.page.evaluate(([cid, x, y]) => {
          const c = document.createElement("div");
          c.id = cid as string;
          c.style.left = `${x}px`;
          c.style.top = `${y}px`;
          document.body.appendChild(c);
        }, [OVERLAY_ID, b.x + b.width / 2, b.y + b.height / 2] as const);
      }
    }
    const file = `${id}.png`;
    const error = this.page.getByText(/coming soon|not available right now|went wrong/i).first();
    if (await error.isVisible().catch(() => false)) {
      this.problems.push(`${id}: a message is on screen: ${(await error.textContent().catch(() => ""))?.slice(0, 120)}`);
    }
    await this.page.screenshot({ path: join(this.dir, file), animations: "disabled" });
    this.beats[id] = { file, focus: box, click: !!opts.click };
    await this.clear();
  }

  /** The still of beat `id` with the cursor on `target`, then the click itself. */
  async click(id: string, target: Locator): Promise<void> {
    await this.shot(id, target, { click: true });
    await target.click();
    await this.page.waitForLoadState("networkidle").catch(() => undefined);
  }

  /** A full-frame card (a drawing the scene needs: the flow, the red box, the data-source decision). */
  async card(id: string, html: string): Promise<void> {
    await this.style();
    await this.clear();
    await this.page.evaluate(([cid, inner]) => {
      const c = document.createElement("div");
      c.id = cid;
      c.innerHTML = `<div class="wrap">${inner}</div>`;
      document.body.appendChild(c);
    }, [CARD_ID, html]);
    const file = `${id}.png`;
    await this.page.screenshot({ path: join(this.dir, file), animations: "disabled" });
    this.beats[id] = { file, focus: null, click: false };
    await this.clear();
  }

  /** Runs one step of a scene; a failed step is written down (beats.json) and the scene goes on, so the build still
   * gets a frame for every line (the line keeps the last good frame) and the README's check lists what to fix. */
  async step(label: string, fn: () => Promise<void>): Promise<void> {
    try {
      await fn();
    } catch (e) {
      this.problems.push(`${label}: ${String(e instanceof Error ? e.message : e).split("\n")[0]}`);
    }
  }

  save(): void {
    const vp = this.page.viewportSize();
    writeFileSync(join(this.dir, "beats.json"), JSON.stringify({ form: this.form, viewport: vp, beats: this.beats, problems: this.problems,
      unmocked: [...new Set(this.unmocked)].sort() }, null, 2));
  }
}
