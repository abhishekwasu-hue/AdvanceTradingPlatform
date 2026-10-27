import type { Page, TestInfo } from "@playwright/test";

/**
 * Chart + colour order Part 3 (3.4; review CC-8, provisional default): "ONE solid brand button per screen" - a
 * heuristic. A screen is composed of many files, so it cannot be a static check; this counts, in the browser, the
 * visible buttons and links whose computed background is the brand fill (`--brand-fill`), and reports more than one as
 * a WARNING annotation on the test (and a console line). It never fails a test.
 * UI-H Wave 0: the selected tab of a SubTabs row (`[data-subtab]`) is filled with the brand colour too (H2), but it marks
 * where you are, not an action, so it is not counted.
 *
 * Use it after a screenshot is taken:  `await warnOnManySolidBrand(page, testInfo, "dashboard 1440 dark");`
 */

/** "29 78 216" (a token value) -> "rgb(29, 78, 216)" (a computed background), or null when it is not an RGB triple. */
export function tokenToRgb(token: string): string | null {
  const parts = token.trim().split(/\s+/).map(Number);
  if (parts.length !== 3 || parts.some((n) => !Number.isInteger(n) || n < 0 || n > 255)) return null;
  return `rgb(${parts.join(", ")})`;
}

export interface Candidate { label: string; background: string; visible: boolean }

/** The labels of the visible candidates painted with the brand fill. Pure, for the unit test. */
export function solidBrandLabels(candidates: Candidate[], brandFill: string | null): string[] {
  if (!brandFill) return [];
  return candidates.filter((c) => c.visible && c.background === brandFill).map((c) => c.label);
}

/** Collects the candidates in the page (runs in the browser; kept free of closures for `page.evaluate`). */
function collect(): { brandFill: string; candidates: Candidate[] } {
  const brandFill = getComputedStyle(document.documentElement).getPropertyValue("--brand-fill");
  const nodes = Array.from(document.querySelectorAll<HTMLElement>("button, a, [role='button'], input[type='submit']"))
    .filter((el) => !el.hasAttribute("data-subtab"));
  const candidates = nodes.map((el) => {
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    const visible = rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none"
      && Number(style.opacity) > 0 && el.closest("[aria-hidden='true'], [hidden], [inert]") === null;
    const label = (el.getAttribute("aria-label") ?? el.textContent ?? "").trim().replace(/\s+/g, " ").slice(0, 40) || el.tagName.toLowerCase();
    return { label, background: style.backgroundColor, visible };
  });
  return { brandFill, candidates };
}

/** Counts the solid brand buttons on the current screen; more than one adds a warning annotation. Returns the labels. */
export async function warnOnManySolidBrand(page: Page, testInfo: TestInfo, screen = testInfo.title): Promise<string[]> {
  let found: { brandFill: string; candidates: Candidate[] };
  try {
    found = await page.evaluate(collect);
  } catch {
    return [];                                   // the page is gone or navigating: a heuristic never fails a test
  }
  const { brandFill, candidates } = found;
  const labels = solidBrandLabels(candidates, tokenToRgb(brandFill));
  if (labels.length > 1) {
    const description = `${screen}: ${labels.length} solid brand buttons (${labels.join(" | ")}) - one main action per screen`;
    testInfo.annotations.push({ type: "warning", description });
    console.warn(`[one-solid-button] ${description}`);
  }
  return labels;
}
