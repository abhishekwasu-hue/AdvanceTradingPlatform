import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import catalogJson from "../../e2e/fixtures/broker_catalog.json";
import { NAV_GROUPS } from "../components/Sidebar";
import { BrokerLinkCard } from "../pages/SettingsPage";
import { legacyTarget } from "../shell/common";
import type { BrokerCatalogEntry, BrokerConnection } from "../types";
import { BrokerTile, ConnectionRow, RemoveDialog, loginOutcome } from "./BrokerPage";
import { BrokerPromptView, promptTitle } from "./BrokerPrompt";
import { ConnectPanelView, HOW_TO_IMAGES, complete, failureText, stepFor, type ConnectPanelViewProps } from "./ConnectPanel";
import { RenewBannerView } from "./RenewBanner";
import { ApiError } from "../api/errors";

const CATALOG = catalogJson as BrokerCatalogEntry[];
const conn = (over: Partial<BrokerConnection> = {}): BrokerConnection => ({
  broker: "upstox", account_label: "primary", name: "Upstox", status: "connected", message: "", data_only: false, renew: true, ...over,
});
const noop = () => undefined;
const entryOf = (broker: string) => CATALOG.find((e) => e.broker === broker)!;
const panel = (entry: BrokerCatalogEntry, over: Partial<ConnectPanelViewProps> = {}) => renderToStaticMarkup(
  <ConnectPanelView entry={entry} step={stepFor(entry, "connect")} values={{}} onChange={noop} onSubmit={noop} busy={false} result={null} {...over} />,
);
const inputs = (html: string) => [...html.matchAll(/<input[^>]*data-field="([^"]+)"[^>]*>/g)].map((m) => m[1]);

/** Words that belong in "Advanced" only (owner: no technical words on the main screens). */
const TECHNICAL = /token expir|oauth|\bjwt\b|\btotp\b|redirect/i;
const ADVICE = /\b(you should|we recommend|recommended|buy now|sell now|guaranteed)\b/i;

/** The panel's markup minus everything the catalog itself supplies (labels, hints, steps: the server's words). */
const ownCopy = (html: string, entry: BrokerCatalogEntry) => {
  const esc = (t: string) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
  let out = html;
  for (const f of [...entry.fields, ...entry.renew_fields]) for (const t of [f.key, f.label, f.placeholder ?? ""]) if (t) out = out.split(esc(t)).join("");
  for (const t of [...entry.how_to, entry.doc_url]) out = out.split(esc(t)).join("");
  return out;
};

describe("P2b Connect panel: only the catalog's fields", () => {
  it("covers Upstox, Zerodha, Fyers, Angel One, Shoonya, Dhan and the rest of the catalog (P2a's real catalog)", () => {
    expect(CATALOG.map((e) => e.broker)).toEqual(expect.arrayContaining(["upstox", "zerodha", "fyers", "angel_one", "shoonya", "dhan", "coindcx"]));
    expect(Object.fromEntries(CATALOG.map((e) => [e.broker, e.fields.length])))
      .toEqual({ upstox: 1, dhan: 2, zerodha: 2, fyers: 3, angel_one: 4, shoonya: 4, coindcx: 2 });
  });

  for (const entry of CATALOG) {
    it(`${entry.name}: renders exactly its ${entry.fields.length} field(s) with their hints, the how-to, one Connect button`, () => {
      const html = panel(entry);
      expect(inputs(html)).toEqual(entry.fields.map((f) => f.key));
      expect(html.match(/<input/g)).toHaveLength(entry.fields.length);          // no other box at all
      for (const f of entry.fields) {
        expect(html).toContain(`>${f.label}</label>`);
        expect(html.match(new RegExp(`<input[^>]*data-field="${f.key}"[^>]*>`))?.[0]).toContain(`type="${f.kind === "secret" ? "password" : "text"}"`);
        if (f.placeholder) expect(html).toContain(`>${f.placeholder.replace(/'/g, "&#x27;")}</p>`);
      }
      expect(html).toContain("Where do I get this?");
      for (const step of entry.how_to) expect(html).toContain(`<li>${step.replace(/'/g, "&#x27;")}</li>`);
      expect(html.match(/<button[^>]*type="submit"/g)).toHaveLength(1);
      expect(html).toMatch(/>Connect<\/button>/);
      expect(html).not.toContain("data-login");                                  // no Log in before the key is saved
      expect(ownCopy(html, entry)).not.toMatch(TECHNICAL);                       // our own words stay plain
    });
  }

  it("Connect stays off until every field has a value", () => {
    const zerodha = entryOf("zerodha");
    expect(complete(zerodha.fields, {})).toBe(false);
    expect(complete(zerodha.fields, { api_key: "k", api_secret: "  " })).toBe(false);
    expect(complete(zerodha.fields, { api_key: "k", api_secret: "s" })).toBe(true);
    expect(complete([], {})).toBe(true);                                         // one click
    expect(panel(zerodha)).toMatch(/<button[^>]*type="submit"[^>]*disabled=""/);
  });

  it("answers 'Connected ✓' or the server's one plain sentence", () => {
    const dhan = entryOf("dhan");
    const ok = panel(dhan, { result: { ok: true } });
    expect(ok).toContain("Connected ✓");
    expect(ok).toContain(">Done</button>");
    const bad = panel(dhan, { result: { ok: false, text: "Dhan did not accept this token; generate a new one and paste it here." } });
    expect(bad).toContain("Dhan did not accept this token; generate a new one and paste it here.");
    expect(bad).toContain('role="alert"');
    expect(bad).toMatch(/text-warn/);
    expect(bad).not.toMatch(/text-down|bg-down/);                               // red is only for a 'not working' dot
    expect(failureText(new ApiError(404, "Not found.", "This broker cannot be connected here."))).toBe("This broker cannot be connected here.");
    expect(failureText(new ApiError(502, "The server had a problem. Try again."))).toBe("The server had a problem. Try again.");
  });
});

describe("P2b Renew: the same panel, only renew_fields", () => {
  it("Dhan: one box, today's access token, sent to /renew", () => {
    const step = stepFor(entryOf("dhan"), "renew");
    expect(step).toMatchObject({ endpoint: "renew", login: false, action: "Renew" });
    const html = panel(entryOf("dhan"), { step });
    expect(inputs(html)).toEqual(["access_token"]);
    expect(html).toMatch(/>Renew<\/button>/);
  });

  for (const broker of ["zerodha", "fyers"]) {
    it(`${broker}: "Log in" (the broker's own page) and one box for the address the trader lands on`, () => {
      const step = stepFor(entryOf(broker), "renew");
      expect(step).toMatchObject({ endpoint: "renew", login: true, action: "Renew" });
      const html = panel(entryOf(broker), { step });
      expect(inputs(html)).toEqual(["request_token"]);
      expect(html).toContain("data-login");
      expect(html).toMatch(/data-login[^>]*>(?:(?!<\/button>).)*Log in<\/button>/s);
      expect(html).toContain("Address after login");
      const opened = panel(entryOf(broker), { step, loginOpened: { link: "https://broker.example/login", opened: false } });
      expect(opened).toContain('href="https://broker.example/login"');
    });
  }

  it("a Zerodha / Fyers connect that answers needs_login moves to the Log in step, still called Connect", () => {
    const step = stepFor(entryOf("zerodha"), "connect", true);
    expect(step).toMatchObject({ endpoint: "renew", login: true, action: "Connect" });
    const html = panel(entryOf("zerodha"), { step, note: "Saved; now press Log in, sign in to Zerodha (Kite) and paste the address you land on." });
    expect(html).toContain("data-note");
    expect(html).toContain("now press Log in");
    expect(inputs(html)).toEqual(["request_token"]);
  });

  for (const broker of ["angel_one", "shoonya"]) {
    it(`${broker}: no renew fields - one button`, () => {
      const step = stepFor(entryOf(broker), "renew");
      expect(step.fields).toEqual([]);
      const html = panel(entryOf(broker), { step });
      expect(inputs(html)).toEqual([]);
      expect(html.match(/<button/g)).toHaveLength(1);
      const submitTag = html.match(/<button[^>]*type="submit"[^>]*>([^<]*)<\/button>/);
      expect(submitTag?.[0]).not.toContain('disabled=""');
      expect(submitTag?.[1]).toBe("Renew");
      expect(html).not.toContain("Where do I get this?");
    });
  }

  it("Upstox: renewing is pasting an Analytics Token (one box, through /connect)", () => {
    const step = stepFor(entryOf("upstox"), "renew");
    expect(step).toMatchObject({ endpoint: "connect", action: "Renew" });
    expect(inputs(panel(entryOf("upstox"), { step }))).toEqual(["analytics_token"]);
  });

  it("has a marked spot for how-to pictures (none yet: no broker screenshots could be captured)", () => {
    expect(HOW_TO_IMAGES).toEqual({});
    expect(panel(CATALOG[0])).not.toContain("data-how-to-images");
  });
});

describe("P2b sign-in prompt and Dashboard banner", () => {
  const brokers = CATALOG.map(({ broker, name }) => ({ broker, name }));

  it("no broker: 'Connect your broker to see live charts and start paper trading', the broker tiles, Connect and Later", () => {
    expect(promptTitle({ kind: "connect" })).toBe("Connect your broker to see live charts and start paper trading");
    const html = renderToStaticMarkup(<BrokerPromptView state={{ kind: "connect" }} brokers={brokers} />);
    expect(html).toContain('data-broker-prompt="connect"');
    expect(html.match(/data-broker-logo/g)?.length).toBeGreaterThanOrEqual(6);
    expect(html).toContain(">Connect</button>");
    expect(html).toContain(">Later</button>");
    expect(html).not.toContain(">Renew<");
  });

  it("today's login needed: '<Broker> needs today's login' with Renew and Later, the broker's real name", () => {
    const state = { kind: "renew" as const, connection: conn({ broker: "zerodha", name: "Zerodha", status: "needs_login" }) };
    expect(promptTitle(state)).toBe("Zerodha needs today's login");
    expect(promptTitle({ kind: "renew", connection: conn({ status: "needs_login" }) })).toBe("Upstox needs today's login");
    const html = renderToStaticMarkup(<BrokerPromptView state={state} brokers={brokers} />);
    expect(html).toContain(">Renew</button>");
    expect(html).toContain(">Later</button>");
    expect(html).not.toContain(">Connect</button>");
  });

  it("the Dashboard banner: one Renew for the broker that needs today's login, amber, not red", () => {
    const html = renderToStaticMarkup(<RenewBannerView connection={conn({ status: "needs_login" })} />);
    expect(html).toContain("Upstox needs today&#x27;s login");
    expect(html.match(/<button/g)).toHaveLength(1);
    expect(html).toContain(">Renew</button>");
    expect(html).toMatch(/bg-warn-tint/);
    expect(html).not.toMatch(/text-down|bg-down/);
  });
});

describe("P2b Broker page", () => {
  const row = (c: BrokerConnection) => renderToStaticMarkup(<ul><ConnectionRow c={c} /></ul>);

  it("one line per broker: tile, name, status, Renew only for a daily login, Remove - nothing else", () => {
    const html = row(conn({ status: "needs_login" }));
    expect(html).toContain("data-broker-logo");
    expect(html).toContain(">Upstox<");
    expect(html).toContain("Needs today&#x27;s login");
    expect(html).toContain('data-broker-dot="needs_login"');
    expect(html.match(/<button/g)).toHaveLength(2);
    expect(html).toContain(">Renew</button>");
    expect(html).toContain(">Remove</button>");
    const daily = row(conn({ broker: "dhan", name: "Dhan", renew: false }));
    expect(daily).not.toContain(">Renew<");
    expect(daily).toContain("Connected ✓");
    expect(daily).toContain('data-broker-dot="connected"');
    for (const out of [html, daily]) {
      expect(out).not.toMatch(TECHNICAL);
      expect(out).not.toMatch(/valid until|expires|token_status|updated /i);   // no expiry times or developer details
    }
  });

  it("analytics-only Upstox shows as Connected, with no Renew", () => {
    const html = row(conn({ status: "needs_login", data_only: true, renew: true }));
    expect(html).toContain("Connected ✓");
    expect(html).not.toContain(">Renew<");
  });

  it("a broker that is not working: the red dot (the only red on the page)", () => {
    const html = row(conn({ broker: "dhan", name: "Dhan", status: "not_working", renew: false, message: "Dhan did not accept the token." }));
    expect(html).toContain('data-broker-dot="not_working"');
    expect(html).toContain("bg-down");
    expect(html.match(/(?:text|bg|border)-down/g)).toEqual(["bg-down"]);
    expect(html).toContain("Not working");
  });

  it("Remove asks first, neutral, and names what happens", () => {
    // The dialog renders in a portal (nothing in static markup while closed); its words come from the component.
    const closed = renderToStaticMarkup(<RemoveDialog c={conn()} open={false} onOpenChange={noop} onConfirm={noop} busy={false} error={null} />);
    expect(closed).toBe("");
    const source = RemoveDialog.toString();
    expect(source).toContain("are deleted");
    expect(source).toContain("stop until you");
    expect(source).not.toMatch(/(?:text|bg|border)-down/);
  });

  it("a tile per catalog broker; one not available yet says so and cannot be opened", () => {
    const html = renderToStaticMarkup(<BrokerTile entry={CATALOG[0]} onPick={noop} />);
    expect(html).toContain(">Upstox<");
    expect(html).not.toContain('disabled=""');
    const soon = renderToStaticMarkup(<BrokerTile entry={{ ...CATALOG[0], broker: "kotak", name: "Kotak Neo", available: false }} onPick={noop} />);
    expect(soon).toContain("Coming soon");
    expect(soon).toContain('disabled=""');
  });

  it("the Upstox login comes back here, in plain words", () => {
    expect(legacyTarget("?broker=upstox&connected=1")).toBe("broker");
    expect(loginOutcome("?broker=upstox&connected=1")).toEqual({ ok: true, text: "Upstox: Connected ✓" });
    expect(loginOutcome("?broker=upstox&error=exchange_failed")?.text).toBe("Upstox login did not finish: the broker did not accept the login code. Log in again.");
    expect(loginOutcome("?broker=upstox&error=invalid_state")?.text).toContain("from the Broker page");
    expect(loginOutcome("?foo=1")).toBeNull();
  });
});

describe("P2b navigation (today's shell) and Settings", () => {
  it("Broker is the first entry of the sidebar (and so of the phone's More drawer)", () => {
    expect(NAV_GROUPS[0].items[0]).toMatchObject({ id: "broker", label: "Broker" });
  });

  it("Settings keeps only a link to the Broker page", () => {
    const html = renderToStaticMarkup(<MemoryRouter><BrokerLinkCard /></MemoryRouter>);
    expect(html).toContain('href="/broker"');
    expect(html.match(/<(input|select|button)/g)).toBeNull();
    expect(html).not.toMatch(TECHNICAL);
    expect(html).not.toMatch(ADVICE);
  });
});
