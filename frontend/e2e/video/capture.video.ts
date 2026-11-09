import { test } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { Director, type Form } from "./director";
import { EPISODES, SCENES } from "./scenes";
import { renderCards } from "./cards";

/**
 * M92 V2: captures the stills of one episode (VIDEO_EPISODE) in both cuts (the `desktop` and `phone` projects).
 * One test per scene, each in a fresh page with its own made-up world (signed out, no broker, ...), so one scene that
 * breaks after a UI change never spoils the others; its problems are written to that scene's beats.json.
 */
const EPISODE = process.env.VIDEO_EPISODE ?? "E1";
/** "E1", or several: "E2,E3" (the stills of a scene do not depend on the episode). */
const SCENE_LIST = [...new Set(EPISODE.split(",").flatMap((e) => EPISODES[e.trim()] ?? []))];
const SHOTS = process.env.VIDEO_SHOTS;
const CARDS = process.env.VIDEO_CARDS;

test.describe(`video ${EPISODE}`, () => {
  test.skip(!SHOTS, "VIDEO_SHOTS is not set - run docs/video/build.sh");
  for (const n of SCENE_LIST) {
    test(`scene ${n}`, async ({ page }, info) => {
      const form = info.project.name as Form;
      const d = new Director(page, join(SHOTS as string, form, `scene-${String(n).padStart(2, "0")}`), form);
      try {
        await SCENES[n](d);
      } finally {
        // VIDEO_DEBUG=1: the page's accessibility tree, to find a control after a UI change.
        if (process.env.VIDEO_DEBUG) {
          const tree = await page.locator("body").ariaSnapshot({ timeout: 5_000 }).catch((e) => String(e));
          writeFileSync(join(SHOTS as string, form, `scene-${String(n).padStart(2, "0")}`, "aria.txt"), tree);
        }
        d.save();
      }
    });
  }
  test("cards", async ({ page }, info) => {
    test.skip(!CARDS, "no cards asked for");
    const form = info.project.name as Form;
    await renderCards(page, JSON.parse(readFileSync(CARDS as string, "utf8")), join(SHOTS as string, form, "cards"));
  });
});
