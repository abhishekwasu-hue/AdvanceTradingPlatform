/** The link into the v2 Paper bot form with a strategy template picked (idea library slice 2, IDEA-1):
 * `/deployments?template=D1&timeframe=15m&set.period=12`. The settings are a PRE-FILL the server checks again when the
 * form asks for the adoption; nothing is created until the user presses "Start on paper" (PAPER only). */
import type { StrategyTemplate } from "./api";

export type TemplateValue = number | string | boolean;
export interface TemplateLink { template: string; timeframe: string | null; params: Record<string, TemplateValue> }

const TEMPLATE_ID = /^[A-G][0-9]{1,2}$/;
const TIMEFRAME = /^[0-9]{1,2}[mhdw]$/;
const KEY = /^[a-z][a-z0-9_]{0,31}$/;
const SET = "set.";
/** The most settings a link carries (the adopt route takes at most 40). */
const MAX_SETTINGS = 40;

/** Where "Adopt as a paper strategy" goes. */
export function paperBotPathFor(link: TemplateLink): string {
  const q = new URLSearchParams({ template: link.template });
  if (link.timeframe) q.set("timeframe", link.timeframe);
  for (const [k, v] of Object.entries(link.params)) q.set(SET + k, String(v));
  return `/deployments?${q.toString()}`;
}

/** The template an address asks the Paper bot form to pick, or null. Unknown shapes are dropped, never guessed. */
export function templateLinkFromSearch(search: string): TemplateLink | null {
  const q = new URLSearchParams(search);
  const template = q.get("template") ?? "";
  if (!TEMPLATE_ID.test(template)) return null;
  const tf = q.get("timeframe");
  const params: Record<string, string> = {};
  for (const [k, v] of q.entries()) {
    const key = k.startsWith(SET) ? k.slice(SET.length) : "";
    if (KEY.test(key) && v.length > 0 && v.length <= 40 && Object.keys(params).length < MAX_SETTINGS) params[key] = v;
  }
  return { template, timeframe: tf && TIMEFRAME.test(tf) ? tf : null, params };
}

/** A link's settings typed as the template declares them; a key the template does not have is left out (the server
 * would refuse it), and a value that is not of the setting's type is left out too (the template's default applies). */
export function typedSettings(template: Pick<StrategyTemplate, "params">, raw: Record<string, TemplateValue>): Record<string, TemplateValue> {
  const out: Record<string, TemplateValue> = {};
  for (const p of template.params) {
    if (!(p.key in raw)) continue;
    const v = raw[p.key];
    if (p.type === "int" || p.type === "float") {
      const n = typeof v === "number" ? v : Number(v);
      if (Number.isFinite(n) && (p.type === "float" || Number.isInteger(n))) out[p.key] = n;
    } else if (p.type === "bool") {
      if (v === true || v === "true") out[p.key] = true;
      else if (v === false || v === "false") out[p.key] = false;
    } else if (typeof v === "string" || typeof v === "number") {
      out[p.key] = String(v);
    }
  }
  return out;
}

/** Day-one fix 6: a template's settings as label / value words (choice labels, "Yes" / "No"; unknown keys left out). */
export function settingRows(template: Pick<StrategyTemplate, "params">, params: Record<string, number | string | boolean>): { key: string; label: string; value: string }[] {
  return template.params.filter((p) => p.key in params).map((p) => {
    const v = params[p.key];
    const choice = p.choices?.find((c) => c.value === String(v));
    const value = choice ? choice.label : typeof v === "boolean" ? (v ? "Yes" : "No") : String(v);
    return { key: p.key, label: p.label, value };
  });
}
