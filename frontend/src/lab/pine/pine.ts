/**
 * Handover 11c-1 (flag `strategy_lab_pine`): the Pine Script subset API and the panel's pure helpers. The server
 * translates the Pine text to ScreenQL and never runs it; "Run as screen" goes through the Screener's own run path.
 */
import { request } from "../../api/client";
import type { RunResponse } from "../../screener/api";

export type Severity = "error" | "warning" | "info";
export interface PineDiagnostic { line: number; col: number; severity: Severity; code: string; message: string; alternative: string }
export interface PineConstruct { line: number; construct: string; status: "supported" | "ignored" | "unsupported"; maps_to: string }
export interface PineParam { name: string; type: "int" | "float" | "bool"; default: number | boolean; min: number | null; max: number | null; title: string; line: number }
export type SignalKind = "long_entry" | "short_entry" | "exit" | "alert" | "marker";
export interface PineSignal {
  index: number; kind: SignalKind; label: string; line: number; ok: boolean; text: string | null;
  params: Record<string, number | boolean>; words: string; problems: string[];
}
export interface PineTranslation {
  ok: boolean; complete: boolean; refused: boolean; version: string | null; script_kind: "indicator" | "strategy" | null; title: string;
  primary: number | null; signals: PineSignal[]; params: Record<string, PineParam>; diagnostics: PineDiagnostic[];
  constructs: PineConstruct[]; note: string;
}
export interface PineTemplate { id: string; name: string; summary: string; source: string }
export interface PineRunResponse extends RunResponse { pine: { signal: PineSignal; params: Record<string, number | boolean>; note: string } }

export interface PineApi {
  templates: () => Promise<{ templates: PineTemplate[]; note: string }>;
  translate: (source: string, baseTf: string) => Promise<PineTranslation>;
  run: (source: string, signal: number, baseTf: string, symbols: string[], exchange?: string) => Promise<PineRunResponse>;
}

export const pineApi: PineApi = {
  templates: () => request("/lab/pine/templates"),
  translate: (source, baseTf) => request("/lab/pine/translate", { method: "POST", body: JSON.stringify({ source, base_tf: baseTf }) }),
  run: (source, signal, baseTf, symbols, exchange = "NSE") =>
    request("/lab/pine/run", { method: "POST", body: JSON.stringify({ source, signal, base_tf: baseTf, symbols, exchange }) }),
};

/** The gutter's numbers: one per line of the text (at least one). */
export function lineNumbers(text: string): number[] {
  const count = text.length ? text.split("\n").length : 1;
  return Array.from({ length: count }, (_, i) => i + 1);
}

/** The character range of a 1-based line, to select it in the text area. */
export function lineRange(text: string, line: number): [number, number] {
  const lines = text.split("\n");
  const at = Math.min(Math.max(line, 1), lines.length) - 1;
  const start = lines.slice(0, at).reduce((sum, l) => sum + l.length + 1, 0);
  return [start, start + (lines[at]?.length ?? 0)];
}

export const SEVERITY_LABEL: Record<Severity, string> = { error: "Not translated", warning: "Ignored", info: "Note" };
export const SEVERITY_CLASS: Record<Severity, string> = {
  error: "border-down/50 text-down", warning: "border-warn/50 text-warn", info: "border-border text-fg-muted",
};
export const KIND_LABEL: Record<SignalKind, string> = {
  long_entry: "Long-side condition", short_entry: "Short-side condition", exit: "Exit condition", alert: "Alert condition", marker: "Marker condition",
};

/** Errors first, then warnings, then notes; by line within each. */
export function sortDiagnostics(list: PineDiagnostic[]): PineDiagnostic[] {
  const rank: Record<Severity, number> = { error: 0, warning: 1, info: 2 };
  return [...list].sort((a, b) => rank[a.severity] - rank[b.severity] || a.line - b.line || a.col - b.col);
}

/** The one-line status above the results. */
export function statusLine(t: PineTranslation): string {
  if (t.refused) return "Refused: the script reads future bars (look-ahead). Nothing was translated.";
  const ok = t.signals.filter((s) => s.ok).length;
  if (!t.signals.length) return "No signal condition was found in the script.";
  const unsupported = t.diagnostics.filter((d) => d.severity === "error").length;
  const tail = unsupported ? ` ${unsupported} line${unsupported === 1 ? "" : "s"} could not be translated.` : "";
  return `${ok} of ${t.signals.length} signal condition${t.signals.length === 1 ? "" : "s"} translated.${tail}`;
}

/** The inputs a signal reads, as "title = value". */
export function inputWords(t: PineTranslation, s: PineSignal): string[] {
  return Object.entries(s.params).map(([name, value]) => {
    const p = t.params[name];
    const shown = typeof value === "boolean" ? (value ? "on" : "off") : String(value);
    return `${p?.title || name} = ${shown}`;
  });
}
