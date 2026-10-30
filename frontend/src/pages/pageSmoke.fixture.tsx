import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { expect } from "vitest";
import { COMING_SOON_MESSAGE, NOT_AVAILABLE_MESSAGE, NETWORK_MESSAGE, SERVER_ERROR_MESSAGE, ApiError, errorSentence } from "../api/errors";

/** M55 C11: shared helpers for the page smoke tests. A page is rendered once on the server (no network, no timers), so
 * the checks cover its first screen; the error sentence a failed call shows is the one `errorSentence` builds. */

export const renderPage = (node: ReactNode, path = "/"): string =>
  renderToStaticMarkup(<MemoryRouter initialEntries={[path]}>{node}</MemoryRouter>);

export const textOf = (html: string): string => html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();

/** Text a trader must never see: developer leftovers, raw JSON, a stack trace or an unset value. */
export function expectNoDeveloperText(html: string): void {
  const text = textOf(html);
  expect(text).not.toMatch(/\[object Object\]|\bundefined\b|\bNaN\b|\bnull\b|Traceback|\bat \w+ \(|\{"|\}\]|TypeError|ReferenceError|Unhandled/);
}

/** The sentences a failed call may show: plain words, never a status line, JSON, HTML or a stack. */
export function expectPlainErrorSentences(): void {
  const raw: unknown[] = [
    new TypeError("Failed to fetch"),
    new ApiError(500, "", "", "<html><body>502 Bad Gateway</body></html>"),
    new ApiError(500, SERVER_ERROR_MESSAGE, "Traceback (most recent call last)"),
    new Error("The marketplace feature is disabled for this tenant"),
  ];
  for (const e of raw) {
    const s = errorSentence(e);
    expect(s.length).toBeGreaterThan(8);
    expect(s).not.toMatch(/\{"|<html|Traceback|TypeError|\bat \w+ \(|^\s*\d{3}\b/i);
  }
  expect(errorSentence(new TypeError("Failed to fetch"))).toBe(NETWORK_MESSAGE);
  expect(errorSentence(new Error("The marketplace feature is disabled for this tenant"))).toBe(COMING_SOON_MESSAGE);
  expect(errorSentence(new Error("Not found"))).toBe(NOT_AVAILABLE_MESSAGE);
}

/** True when the page turns every caught failure into a sentence with `errorSentence` and never prints the raw error. */
export function routesErrorsThroughSentence(source: string): boolean {
  return /errorSentence\(/.test(source) && !/JSON\.stringify\(e|\be\.stack\b|String\(e\)|\be\.message\b/.test(source);
}
