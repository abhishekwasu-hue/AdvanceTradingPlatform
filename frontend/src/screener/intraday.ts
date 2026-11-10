/**
 * M94 PR-2: the intraday scanners as the server sends them (GET /api/screener/intraday - flags `intraday_pack` and
 * `screener_breakouts`, both off by default: the call then fails and the gallery shows no settings for them). Each
 * scanner's editable settings: a reference value (to edit before use), the ATP default, or Not set (the run asks for it).
 * Pure functions only, so the gallery's "Intraday" chip and settings are tested without a browser.
 *
 * Signals only: a scanner lists matches; it never says what to trade.
 */

export type ParamValue = number | string | boolean;

export interface IntradayParam {
  name: string;
  value: ParamValue | null;           // null = Not set
  doc: string;
  ref: boolean;
  note: string;                       // "Not set" | "Reference value - edit before use" | "ATP default"
}

export interface IntradayScanner {
  id: string;
  name: string;
  definition: string;
  text: string;
  base_tf: string;
  params: IntradayParam[];
  unset: string[];
  mirror_of: string | null;
  footer: string;
}

export interface IntradayLibrary { scanners: IntradayScanner[]; footer: string; ref_label: string; not_set: string }

/** The library source the pack's screens are filed under, and the gallery chip that shows only them. */
export const INTRADAY_SOURCE = "intraday";
export const INTRADAY_CHIP = "Intraday";

export function isIntradayLibrary(x: unknown): x is IntradayLibrary {
  const l = x as Partial<IntradayLibrary> | null;
  return !!l && Array.isArray(l.scanners) && typeof l.footer === "string";
}

/** Scanners by id (an empty map while the pack is off). */
export function scannersById(lib: IntradayLibrary | null): Record<string, IntradayScanner> {
  return Object.fromEntries((lib?.scanners ?? []).map((s) => [s.id, s]));
}

/** A setting's value as shown: "Not set", "Yes" / "No" for a switch, else the value. */
export function paramValueText(p: IntradayParam): string {
  if (p.value === null) return "Not set";
  if (typeof p.value === "boolean") return p.value ? "Yes" : "No";
  return String(p.value);
}

/** "Not set: 2 of 4. The run asks for them." (empty when every setting has a value). */
export function unsetText(s: IntradayScanner): string {
  const n = s.unset.length;
  if (n === 0) return "";
  return `Not set: ${n} of ${s.params.length}. The run asks for ${n === 1 ? "it" : "them"}.`;
}
