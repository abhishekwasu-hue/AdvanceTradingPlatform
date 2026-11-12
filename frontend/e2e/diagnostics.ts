import { test, type Page, type Request } from "@playwright/test";

/**
 * M60 CI diagnostics (tests only, no behaviour change). The phone project sometimes never mounts the v2 shell after a
 * second full page load on CI, and the trace artifact cannot be read from every sandbox. So a spec that calls
 * `installDiagnostics(test)` records, per test:
 * - every /api/* request and its response (url, status, ms since the test started, failure text);
 * - console errors and uncaught page errors;
 * - every failed request (any url);
 * - main-frame navigations, load events, and the `<html>` attributes at each load and at the end.
 * The navigations, loads, `<html>` attributes, page errors and failed requests are also printed on their own (last 40),
 * so a burst of /api records cannot hide them.
 * When the test fails (status differs from the expected one) the last records go to stdout, each line starting with
 * `[m60-diag]`, so the job log names the cause. A passing test prints nothing. Nothing here waits on, routes or changes
 * the page; listeners only read.
 */

const PREFIX = "[m60-diag]";
const KEEP = 80;

type Recorder = { t0: number; total: number; lines: string[]; keys: string[]; page: Page };
/** Rare events kept apart, so they still print when /api noise pushes them out of the last records. */
const KEY_KINDS = new Set(["navigate", "dcl", "load", "html", "pageerror", "crash", "req-fail"]);
const recorders = new Map<string, Recorder>();

function push(r: Recorder, kind: string, text: string) {
  r.total += 1;
  const line = `+${String(Date.now() - r.t0).padStart(6)}ms ${kind.padEnd(9)} ${text}`;
  r.lines.push(line);
  if (KEY_KINDS.has(kind)) {
    r.keys.push(line);
    if (r.keys.length > 60) r.keys.splice(0, r.keys.length - 40);
  }
  if (r.lines.length > KEEP * 2) r.lines.splice(0, r.lines.length - KEEP);
}

function apiPath(url: string): string | null {
  try {
    const u = new URL(url);
    return u.pathname.startsWith("/api/") || u.pathname === "/api" ? u.pathname + u.search : null;
  } catch {
    return null;
  }
}

async function htmlAttributes(page: Page): Promise<string> {
  try {
    // Capped at 2 s, so a renderer that no longer answers cannot hold up the hook.
    const attrs = await Promise.race([
      page.evaluate(() =>
        Array.from(document.documentElement.attributes).map((a) => `${a.name}=${JSON.stringify(a.value)}`).join(" ")),
      new Promise<never>((_, reject) => setTimeout(() => reject(new Error("no answer within 2 s")), 2_000)),
    ]);
    return attrs || "(none)";
  } catch (e) {
    return `(unreadable: ${(e as Error).message.split("\n")[0]})`;
  }
}

function attach(r: Recorder) {
  const { page } = r;
  const sent = new WeakMap<Request, number>();
  page.on("request", (req) => {
    const p = apiPath(req.url());
    if (!p) return;
    sent.set(req, Date.now());
    push(r, "api-req", `${req.method()} ${p}`);
  });
  page.on("response", (res) => {
    const p = apiPath(res.url());
    if (!p) return;
    const start = sent.get(res.request());
    push(r, "api-res", `${res.status()} ${res.request().method()} ${p}${start ? ` (${Date.now() - start} ms)` : ""}`);
  });
  page.on("requestfailed", (req) => {
    const start = sent.get(req);
    push(r, "req-fail", `${req.method()} ${req.url()} :: ${req.failure()?.errorText ?? "?"}${start ? ` (${Date.now() - start} ms)` : ""}`);
  });
  page.on("console", (msg) => {
    if (msg.type() !== "error" && msg.type() !== "warning") return;
    const loc = msg.location();
    push(r, `console.${msg.type() === "error" ? "err" : "warn"}`,
      `${msg.text().slice(0, 400)}${loc?.url ? ` @ ${loc.url}:${loc.lineNumber}` : ""}`);
  });
  page.on("pageerror", (err) => push(r, "pageerror", `${err.message.slice(0, 400)} ${(err.stack ?? "").split("\n").slice(1, 4).join(" | ")}`));
  page.on("crash", () => push(r, "crash", "page crashed"));
  page.on("framenavigated", (frame) => { if (frame === page.mainFrame()) push(r, "navigate", frame.url()); });
  page.on("domcontentloaded", () => push(r, "dcl", page.url()));
  page.on("load", () => {
    push(r, "load", page.url());
    void htmlAttributes(page).then((a) => push(r, "html", a));
  });
}

/** Call once at the top of a spec file: it adds a beforeEach and an afterEach for every test in that file. */
export function installDiagnostics() {
  test.beforeEach(async ({ page }, testInfo) => {
    const r: Recorder = { t0: Date.now(), total: 0, lines: [], keys: [], page };
    recorders.set(testInfo.testId, r);
    attach(r);
  });

  test.afterEach(async ({ page }, testInfo) => {
    const r = recorders.get(testInfo.testId);
    recorders.delete(testInfo.testId);
    if (!r || testInfo.status === testInfo.expectedStatus) return;
    push(r, "end-url", page.url());
    push(r, "end-html", await htmlAttributes(page));
    const tail = r.lines.slice(-KEEP);
    const head = `${PREFIX} ${testInfo.project.name} > ${testInfo.titlePath.slice(1).join(" > ")} (retry ${testInfo.retry}, ` +
      `status ${testInfo.status}, ${r.total > KEEP ? `last ${KEEP} of ${r.total}` : `${r.total}`} records)`;
    const keys = r.keys.slice(-40).filter((l) => !tail.includes(l));
    console.log([head,
      ...(keys.length ? [`${PREFIX} -- navigations, loads, <html>, page errors, failed requests (earlier than the last records) --`,
        ...keys.map((l) => `${PREFIX} ${l}`), `${PREFIX} -- last records --`] : []),
      ...tail.map((l) => `${PREFIX} ${l}`), `${PREFIX} end`].join("\n"));
  });
}
