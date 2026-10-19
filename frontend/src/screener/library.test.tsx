import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { STARTER_BUILDS } from "./FunnelCanvas";
import { LibraryTools } from "./LibraryTools";
import {
  agreementText, confluenceChoices, isMirrors, isWatchLists, MAX_CONFLUENCE, mirrorsByScreen, toggleChoice,
  type MirrorsResponse,
} from "./library";
import { SAMPLE_CONFLUENCE, SAMPLE_LISTS, SAMPLE_MIRRORS, SAMPLE_WATCH } from "./librarySample";
import { screenText, type Registry } from "./model";
import { ScreenGallery } from "./ScreenGallery";
import starterRows from "./starters.json";
import sample from "./taxonomySample.json";
import type { Taxonomy } from "./taxonomy";

const tax = sample as unknown as Taxonomy;
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

const MIRRORS: MirrorsResponse = {                     // one long screen without a mirror, to show its reason
  ...SAMPLE_MIRRORS,
  mirrors: SAMPLE_MIRRORS.mirrors.map((m) => (m.mirror_of === "starter.daily_trend" ? { ...m, text: null, reason: "No mirror: example - a price is compared with something that is not a price." } : m)),
};
const LISTS = SAMPLE_LISTS;
const RESULT = SAMPLE_CONFLUENCE;
const WATCH = SAMPLE_WATCH;

const REG: Registry = {
  close: { kind: "factor", returns: "num", unit: "price", args: [], types: {}, defaults: {}, choices: {}, values: [], varargs: false, timeframed: true, field: true, doc: "" },
  RSI: { kind: "factor", returns: "num", unit: "index", args: ["n"], types: { n: "window" }, defaults: { n: 14 }, choices: {}, values: [], varargs: false, timeframed: true, field: false, doc: "" },
  EMA: { kind: "factor", returns: "num", unit: "same", args: ["x", "n"], types: { x: "num", n: "window" }, defaults: {}, choices: {}, values: [], varargs: false, timeframed: true, field: false, doc: "" },
};

describe("the starters' text (U9 TX2 contract with the server's library.STARTERS)", () => {
  it("is exactly what each starter's build prints", () => {
    for (const row of starterRows.starters) {
      expect(screenText(REG, STARTER_BUILDS[row.id](REG))).toBe(row.text);
      expect(row.base_tf).toBe("1d");
    }
  });
});

describe("library helpers", () => {
  it("guards the responses and indexes mirrors by the screen they mirror", () => {
    expect(isMirrors(MIRRORS) && isWatchLists(LISTS)).toBe(true);
    expect(isMirrors({ detail: "off" }) || isWatchLists(null)).toBe(false);
    expect(mirrorsByScreen(MIRRORS)["starter.oversold"].text).toBe("RSI(14) >= 70");
    expect(mirrorsByScreen(null)).toEqual({});
  });

  it("offers every gallery screen and each mirror that has a text, and caps the picks", () => {
    const refs = confluenceChoices(tax, MIRRORS).map((c) => c.ref);
    expect(refs).toContain("starter.momentum.short");
    expect(refs).not.toContain("starter.daily_trend.short");                     // no mirror text: not runnable
    let picked: string[] = [];
    for (const r of refs.slice(0, MAX_CONFLUENCE + 1)) picked = toggleChoice(picked, r);
    expect(picked).toHaveLength(Math.min(MAX_CONFLUENCE, refs.length));
    expect(toggleChoice(picked, picked[0])).toHaveLength(picked.length - 1);
    expect(agreementText(RESULT.rows[0])).toBe("2 methods · 3 screens");
    expect(agreementText(RESULT.rows[1])).toBe("1 method · 1 screen");
  });
});

describe("LibraryTools (U9 TX2)", () => {
  it("shows the confluence rows with the screens that flagged each symbol and the note, never a score", () => {
    const html = renderToStaticMarkup(<LibraryTools taxonomy={tax} mirrors={MIRRORS} watchLists={LISTS} symbolsText="UP, DOWN, FLAT" initialConfluence={RESULT} />);
    const t = text(html);
    expect(t).toContain("Symbols (3)");
    expect(html.indexOf('data-symbol="UP"')).toBeLessThan(html.indexOf('data-symbol="DOWN"'));
    expect(t).toContain("2 methods · 3 screens");
    expect(t).toContain("New 10-day high");
    expect(t).toContain("Not flagged: FLAT");
    expect(t).toContain(RESULT.note);
    const table = text(html.slice(html.indexOf("<table"), html.indexOf("</table>")));
    expect(table).not.toMatch(/\b(score|rank|buy|sell|recommended|best|target)\b/i);         // the rows: counts and evidence only
  });

  it("shows the triggered and ready lists with the transition to save for an alert", () => {
    const html = renderToStaticMarkup(<LibraryTools taxonomy={tax} mirrors={MIRRORS} watchLists={LISTS} symbolsText="UP" initialTab="watch" initialWatch={WATCH} />);
    const t = text(html);
    expect(t).toContain("Triggered (1)");
    expect(t).toContain("Ready (1)");
    expect(t).toContain(WATCH.transition_text.replace(/>/g, "&gt;"));
    expect(t).toContain("Save the trigger screen");
    expect(t).toContain("RSI oversold, then back above 30");
    expect(html).not.toMatch(/[\u0900-\u097f]/);
  });

  it("puts each long screen's mirror, or why it has none, on its gallery card", () => {
    const html = renderToStaticMarkup(<ScreenGallery taxonomy={tax} canOpen={() => true} onOpen={() => undefined} mirrors={mirrorsByScreen(MIRRORS)} />);
    expect(text(html)).toContain("Mirror (down side): RSI(14) &gt;= 70");
    expect(text(html)).toContain("No mirror: example");
    const without = renderToStaticMarkup(<ScreenGallery taxonomy={tax} canOpen={() => true} onOpen={() => undefined} />);
    expect(without).not.toContain('data-testid="screen-mirror"');
  });
});
