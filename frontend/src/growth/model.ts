import { hasCallWords } from "../chartAnalyze/signalLanguage";
import { cleanSection } from "../rhythm/model";
import type { CardFact, DishPage, DisciplineCard, PageDish, PlanMeter } from "./types";

/**
 * MO4 slice 3: the plan page's and the share card's pure parts.
 *
 * The share card is a PUBLIC surface (CLAUDE.md): the server builds it from discipline facts only and guards it
 * (app/growth/share.py); the card checks every fact and line again here before drawing it, with the same rules - no
 * trade-call word, no currency, no price-like number, no money or outcome word - and leaves out one that fails.
 */

const OUTCOME_WORDS = /\b(?:profit|profits|loss|losses|p&l|pnl|gain|gains|won|lost|wins?|return|returns|rupees?|lakh|crore)\b/i;
const PRICE_LIKE = /\d+[.,]\d+|\d{4,}/;
const CURRENCY = /₹|\bRs\.?(?=\s?\d)|\bINR\b|\$(?=\s?\d)/;

/** True when `text` may not be on a share card. */
export function cardProblem(text: string): boolean {
  if (!text) return false;
  return hasCallWords(text) || OUTCOME_WORDS.test(text) || PRICE_LIKE.test(text) || CURRENCY.test(text);
}

/** The card with only what passes the check (the fixed texts too). */
export function cleanCard(card: DisciplineCard): DisciplineCard {
  return {
    ...card,
    title: cardProblem(card.title) ? "" : card.title,
    week_label: cardProblem(card.week_label) ? "" : card.week_label,
    footer: cardProblem(card.footer) ? "" : card.footer,
    facts: card.facts.filter((f) => !cardProblem(`${f.label} ${f.value}`)),
    lines: card.lines.filter((l) => !cardProblem(l)),
  };
}

/** The share text: the card's facts and lines in plain text, with the link (no name, no price: the same checks). */
export function shareText(card: DisciplineCard, link = SHARE_LINK): string {
  const c = cleanCard(card);
  return [`${c.title} (${c.week_label})`, ...c.facts.map((f) => `${f.label}: ${f.value}`), ...c.lines, link].join("\n");
}

export const SHARE_LINK = "https://aialgodesk.tech";
export const SHARE_FILE = "my-discipline-week.png";

/** A meter bar's fill in percent (null: no limit, so no bar). */
export function meterFill(m: PlanMeter): number | null {
  if (m.used === null || m.limit === null || m.limit <= 0) return null;
  return Math.max(0, Math.min(100, Math.round((100 * m.used) / m.limit)));
}

export function trialLine(trial: { plan_name: string; days_left: number } | null): string | null {
  if (!trial) return null;
  if (trial.days_left <= 0) return `Your ${trial.plan_name} trial ends today.`;
  return `Your ${trial.plan_name} trial: ${trial.days_left} day${trial.days_left === 1 ? "" : "s"} left.`;
}

// --- the card image (an SVG, 1200 x 630, the usual share-image size) --------------------------------------------------

export const CARD_W = 1200;
export const CARD_H = 630;

/** The theme tokens the image uses; on screen they stay `rgb(var(--token))`, in the downloaded file they are resolved. */
export const CARD_TOKENS = ["surface-1", "surface-2", "fg", "fg-muted", "brand", "border"] as const;
type Token = (typeof CARD_TOKENS)[number];
const tone = (t: Token) => `rgb(var(--${t}))`;

function esc(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/** Splits a line into rows of at most `max` characters, on spaces. */
export function wrap(text: string, max: number): string[] {
  const rows: string[] = [];
  let row = "";
  for (const word of text.split(/\s+/).filter(Boolean)) {
    if (row && `${row} ${word}`.length > max) { rows.push(row); row = word; } else row = row ? `${row} ${word}` : word;
  }
  if (row) rows.push(row);
  return rows;
}

function factTile(f: CardFact, i: number, count: number): string {
  const gap = 24;
  const w = (CARD_W - 2 * 64 - gap * (count - 1)) / count;
  const x = 64 + i * (w + gap);
  const label = wrap(f.label, count > 3 ? 15 : 22).slice(0, 2);
  return `<g data-card-fact="${esc(f.key)}">`
    + `<rect x="${x}" y="196" width="${w}" height="196" rx="20" fill="${tone("surface-2")}" stroke="${tone("border")}"/>`
    + `<text x="${x + 24}" y="266" font-size="${count > 3 ? 44 : 56}" font-weight="700" fill="${tone("fg")}">${esc(f.value)}</text>`
    + label.map((row, j) => `<text x="${x + 24}" y="${318 + j * 32}" font-size="26" fill="${tone("fg-muted")}">${esc(row)}</text>`).join("")
    + "</g>";
}

/** The card as SVG markup, from the checked card only. */
export function cardSvg(card: DisciplineCard): string {
  const c = cleanCard(card);
  const facts = c.facts.slice(0, 4);
  const lines = c.lines.slice(0, 3).flatMap((l) => wrap(l, 64)).slice(0, 3);
  const body = [
    `<rect width="${CARD_W}" height="${CARD_H}" rx="32" fill="${tone("surface-1")}"/>`,
    `<rect x="64" y="64" width="44" height="44" rx="12" fill="${tone("brand")}"/>`,
    `<text x="124" y="97" font-size="28" font-weight="700" fill="${tone("fg")}">AiAlgoDesk</text>`,
    `<text x="${CARD_W - 64}" y="97" font-size="24" text-anchor="end" fill="${tone("fg-muted")}">${esc(c.week_label)}</text>`,
    `<text x="64" y="168" font-size="44" font-weight="700" fill="${tone("fg")}">${esc(c.title)}</text>`,
    ...facts.map((f, i) => factTile(f, i, facts.length)),
    ...lines.map((l, i) => `<text x="64" y="${450 + i * 42}" font-size="30" fill="${tone("fg")}">${esc(l)}</text>`),
    `<text x="64" y="${CARD_H - 40}" font-size="20" fill="${tone("fg-muted")}">${esc(c.footer)}</text>`,
    `<text x="${CARD_W - 64}" y="${CARD_H - 40}" font-size="22" font-weight="600" text-anchor="end" fill="${tone("brand")}">aialgodesk.tech</text>`,
  ];
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${CARD_W} ${CARD_H}" width="${CARD_W}" height="${CARD_H}" `
    + `font-family="Inter, system-ui, sans-serif" role="img" aria-label="${esc(c.title)}">${body.join("")}</svg>`;
}

/** The markup with every `rgb(var(--token))` replaced by the theme's value (`read` gives "r g b" for a token). */
export function resolveTokens(svg: string, read: (token: string) => string): string {
  return svg.replace(/rgb\(var\(--([a-z0-9-]+)\)\)/g, (whole, token: string) => {
    const value = read(token).trim();
    return value ? `rgb(${value})` : whole;
  });
}

// --- MO4 slice 4: the page dishes ---------------------------------------------------------------------------------------

/** How often a dish reads again while the page is open and visible (none: it changes only with the page itself). */
export const DISH_REFRESH_MS: Partial<Record<DishPage, number>> = {
  signals: 2 * 60_000,
  positions: 2 * 60_000,
  screener: 5 * 60_000,
};

/**
 * The dish as shown: every line checked again for call words (a line that fails is left out, never rewritten), the
 * headline left out of the sections below it (a section that held only the headline goes too, since the headline says
 * it), and a headline that fails the check dropped.
 */
export function dishShown(dish: PageDish): PageDish {
  const headline = dish.headline && !hasCallWords(dish.headline) ? dish.headline : null;
  const sections = dish.sections.map(cleanSection).flatMap((s) => {
    if (!headline || !s.lines.includes(headline)) return [s];
    const lines = s.lines.filter((l) => l !== headline);
    return lines.length ? [{ ...s, lines }] : [];
  });
  return { ...dish, headline, sections };
}

const DISH_OPEN_KEY = "atp_dish_open:";

/** Whether he left this page's dish open or folded (this device only; `fallback` when nothing is stored or storage is
 * unavailable). */
export function readDishOpen(page: DishPage, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(DISH_OPEN_KEY + page);
    return v === null ? fallback : v === "1";
  } catch {
    return fallback;
  }
}

export function writeDishOpen(page: DishPage, open: boolean): void {
  try { localStorage.setItem(DISH_OPEN_KEY + page, open ? "1" : "0"); } catch { /* storage unavailable: not remembered */ }
}
