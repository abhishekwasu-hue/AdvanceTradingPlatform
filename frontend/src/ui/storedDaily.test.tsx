import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { StoredDailyNote } from "./BrokerGate";
import { STORED_DAILY_PAGES, storedDailyFor } from "./dataPages";
import type { BrokerTokenInfo } from "../types";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/\s+/g, " ").trim();
const upstox = { broker_name: "upstox", account_label: "primary", token_status: "EXPIRED", needs_login: true, oauth_supported: true } as unknown as BrokerTokenInfo;
const DEV = /\b(1d|15m|lake|bhavcopy|backend|token|409|sample)\b/i;

describe("day-one fix 2: pages keep their Daily content before the broker login", () => {
  it("Find setups and Signals (and their sub-paths) have a plain sentence; other data pages keep the full gate", () => {
    expect(storedDailyFor("/screener")).toBe(STORED_DAILY_PAGES["/screener"]);
    expect(storedDailyFor("/signals/")).toBe(STORED_DAILY_PAGES["/signals"]);
    expect(storedDailyFor("/screener/runs")).toBe(STORED_DAILY_PAGES["/screener"]);
    for (const p of ["/charts", "/option-chain", "/positions", "/", "/screenerx"]) expect(storedDailyFor(p)).toBeNull();
    for (const s of Object.values(STORED_DAILY_PAGES)) expect(s).not.toMatch(DEV);
  });

  it("an ended session: the page's sentence and one Log in button", () => {
    const html = renderToStaticMarkup(<MemoryRouter>
      <StoredDailyNote state={{ kind: "login-needed", broker: upstox, others: [], expired: true }} what={STORED_DAILY_PAGES["/screener"]} />
    </MemoryRouter>);
    expect(html).toContain('data-stored-daily="login-needed"');
    expect(text(html)).toBe("Your Upstox session has ended for today. Log in for live prices. Until then, Daily scans run on the closed Daily bars in "
      + "ATP's stored end-of-day history; intraday scans need the login. Log in to Upstox");
    expect(text(html)).not.toMatch(/No broker connected/);
  });

  it("no broker yet: Connect a broker, never 'Log in'", () => {
    const html = renderToStaticMarkup(<MemoryRouter><StoredDailyNote state={{ kind: "no-broker" }} what={STORED_DAILY_PAGES["/signals"]} /></MemoryRouter>);
    expect(text(html)).toContain("Connect your broker for live prices.");
    expect(html).toContain('href="/broker"');
    expect(text(html)).not.toContain("Log in to");
  });

  it("the shell passes the sentence only with stored history on", () => {
    const shell = readFileSync("src/shell/AppShellV2.tsx", "utf8");
    expect(shell).toContain('storedDaily={features.on("backtest_stored_history") ? storedDailyFor(location.pathname) : null}');
  });
});

describe("day-one fix 2: the scan's source in plain words", () => {
  it("names stored history and the broker without labels", async () => {
    const { sourceWords } = await import("../screener/FreshnessPill");
    expect(sourceWords("lake:daily")).toBe("ATP's stored end-of-day history");
    expect(sourceWords("broker:upstox")).toBe("your broker");
  });
});
