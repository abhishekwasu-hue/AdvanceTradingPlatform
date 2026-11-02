import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import catalogJson from "../../e2e/fixtures/broker_catalog.json";
import { NAV_GROUPS } from "../components/Sidebar";
import { BrokerLinkCard } from "../pages/SettingsPage";
import { legacyTarget } from "../shell/common";
import type { BrokerCatalogEntry, BrokerConnection } from "../types";
import { BrokerTile, ConnectionRow, RemoveDialog, loginOutcome } from "./BrokerPage";
import { AnotherAccount, validAccountName } from "./BrokerAdvanced";
import { BrokerPromptView, promptTitle } from "./BrokerPrompt";
import { ConnectPanelView, HOW_TO_IMAGES, boldText, complete, failureText, guideOf, stepFor, stepNumbers, type ConnectPanelViewProps } from "./ConnectPanel";
import { RenewBannerView } from "./RenewBanner";
import { ApiError } from "../api/errors";
import { canChangeBrokers } from "./brokerState";

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
  const guide = guideOf(entry);
  const pieces = [...guide.warnings, guide.info, ...guide.steps.map((s) => s.text)].flatMap((t) => t.split("**"));
  const urls = guide.steps.flatMap((s) => (s.url ? [s.url] : []));
  for (const t of [...urls, entry.doc_url, ...entry.how_to, ...pieces.filter((t) => /\w{2}/.test(t))].sort((a, b) => b.length - a.length)) out = out.split(esc(t)).join("");
  return out.replace(/ data-[a-z-]+="[^"]*"/g, "");                            // markers are not shown to anyone
};

describe("P2b Connect panel: only the catalog's fields", () => {
  it("covers Upstox, Zerodha, Fyers, Angel One, Shoonya, Dhan and the rest of the catalog (P2a's real catalog)", () => {
    expect(CATALOG.map((e) => e.broker)).toEqual(expect.arrayContaining(["upstox", "zerodha", "fyers", "angel_one", "shoonya", "dhan", "coindcx"]));
    expect(Object.fromEntries(CATALOG.map((e) => [e.broker, e.fields.length])))
      .toEqual({ upstox: 1, dhan: 3, zerodha: 2, fyers: 2, angel_one: 4, shoonya: 4, coindcx: 2, kotak_neo: 5, groww: 2 });   // BRK-1 B: Dhan TOTP; no Fyers Redirect URL box
  });

  for (const entry of CATALOG) {
    it(`${entry.name}: renders exactly its ${entry.fields.length} field(s) with their hints, the open guide, one Connect button`, () => {
      const html = panel(entry);
      expect(inputs(html)).toEqual(entry.fields.map((f) => f.key));
      expect(html.match(/<input/g)).toHaveLength(entry.fields.length);          // no other box at all
      for (const f of entry.fields) {
        expect(html).toMatch(new RegExp(`<label [^>]*><span>${f.label.replace(/[()]/g, "\\$&")}</span>`));
        expect(html.match(new RegExp(`<input[^>]*data-field="${f.key}"[^>]*>`))?.[0]).toContain(`type="${f.kind === "secret" ? "password" : "text"}"`);
        if (f.placeholder) expect(html).toContain(`>${f.placeholder.replace(/'/g, "&#x27;")}</p>`);
      }
      // BRK-1 C: the guide is open (no "Where do I get this?"), before the fields; warnings first; numbered steps.
      expect(html).not.toContain("Where do I get this?");
      expect(html).toContain(`data-broker-guide="${entry.broker}" data-guide-open="true"`);
      expect(html.indexOf("data-broker-guide")).toBeLessThan(html.indexOf("data-field"));
      const guide = entry.guide!;
      expect(html.match(/data-guide-step="/g)).toHaveLength(guide.steps.length);
      expect(html.match(/data-guide-warning/g) ?? []).toHaveLength(guide.warnings.length);
      expect(html.match(/data-guide-info/g)).toHaveLength(1);
      if (guide.warnings.length) expect(html.indexOf("data-guide-warning")).toBeLessThan(html.indexOf("data-guide-step="));
      const links = [...html.matchAll(/<a [^>]*>/g)].map((m) => m[0]).filter((a) => a.includes("data-guide-link"));
      expect(links).toHaveLength(guide.steps.filter((s) => s.url).length);
      for (const a of links) expect(a).toMatch(/target="_blank" rel="noopener noreferrer"/);
      const from = stepNumbers(guide);
      for (const f of entry.fields) expect(html).toMatch(new RegExp(`data-from-step="${from[f.key]}"[^>]*>From step ${from[f.key]}<`));
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
    // A failed call (not a P2a answer) is one fixed sentence per status: the server's own text is never shown.
    const leak = "Traceback: KeyError at app/brokers/connect.py line 1";
    expect(failureText(new ApiError(404, "Not found.", leak), "Dhan")).toBe("Dhan cannot be connected here.");
    expect(failureText(new ApiError(502, "x", leak), "Dhan")).toBe("That did not go through. Try again in a moment.");
    expect(failureText(new ApiError(429, "x", leak))).toBe("Too many attempts. Wait a minute, then try again.");
    expect(failureText(new ApiError(401, "x", leak))).toBe("Your sign-in has ended. Sign in again, then try again.");
    expect(failureText(new ApiError(403, "x", "Storing broker credentials requires a fresh two-factor check"))).toContain("two-factor");
    expect(failureText(new ApiError(403, "x", "Storing broker credentials requires a verified email"))).toContain("Verify your email");
    expect(failureText(new ApiError(403, "x", leak))).toContain("Ask the owner of your organisation");
    expect(failureText(new ApiError(404, "x", leak), "Dhan", "remove")).toBe("Dhan is no longer connected.");
    expect(failureText(new TypeError("Failed to fetch"))).toBe("Cannot reach the server. Check your connection and try again.");
    for (const st of [400, 401, 403, 404, 409, 422, 429, 500, 502]) expect(failureText(new ApiError(st, "x", leak))).not.toContain("Traceback");
  });
});

describe("P2b Renew: the same panel, only renew_fields", () => {
  it("Dhan (BRK-1 B): the TOTP login - Renew is one click; a pasted token only behind the fallback link", () => {
    const dhan = entryOf("dhan");
    const step = stepFor(dhan, "renew");
    expect(step).toMatchObject({ endpoint: "renew", login: false, oauth: false, submit: true, action: "Renew", fields: [] });
    const html = panel(dhan, { step, fallbackLabel: dhan.fallback!.label, onToggleFallback: noop });
    expect(inputs(html)).toEqual([]);
    expect(html).toMatch(/>Renew<\/button>/);
    expect(html).toContain('data-fallback-toggle="off"');
    expect(html).toContain(`>${dhan.fallback!.label}</button>`);
    const pasted = stepFor(dhan, "renew", false, true);
    expect(pasted).toMatchObject({ endpoint: "renew", login: false, submit: true });
    const html2 = panel(dhan, { step: pasted, fallbackLabel: dhan.fallback!.label, usingFallback: true, onToggleFallback: noop });
    expect(inputs(html2)).toEqual(["access_token"]);
    expect(html2).toContain(">Back to the usual way</button>");
    // Connecting the fallback way: the client ID and the token.
    expect(stepFor(dhan, "connect", false, true).fields.map((f) => f.key)).toEqual(["client_id", "access_token"]);
    expect(stepFor(dhan, "connect").fields.map((f) => f.key)).toEqual(["client_id", "pin", "totp_secret"]);
  });

  for (const broker of ["zerodha", "fyers"]) {
    it(`${broker} (BRK-1 B): ONE "Log in" button - the broker's page returns to ATP, nothing is pasted`, () => {
      const step = stepFor(entryOf(broker), "renew");
      expect(step).toMatchObject({ endpoint: "renew", login: true, oauth: true, submit: false, action: "Renew", fields: [] });
      const html = panel(entryOf(broker), { step });
      expect(inputs(html)).toEqual([]);
      expect(html.match(/<button/g)).toHaveLength(1);
      expect(html).not.toMatch(/type="submit"/);
      expect(html).toContain('data-login-way="callback"');
      expect(html).toMatch(/data-login[^>]*>(?:(?!<\/button>).)*Log in<\/button>/s);
      expect(html).toContain("ATP finishes the login by itself");
      expect(html).not.toContain("Address after login");
      const opened = panel(entryOf(broker), { step, loginOpened: { link: "https://broker.example/login", opened: false } });
      expect(opened).toContain('href="https://broker.example/login"');
      expect(opened).not.toContain("paste");
    });

    it(`${broker}: the hidden fallback is the older way - Log in, then one box for the address`, () => {
      const entry = entryOf(broker);
      expect(entry.redirect_url).toMatch(new RegExp(`/api/broker/${broker}/oauth/callback$`));
      const step = stepFor(entry, "renew", false, true);
      expect(step).toMatchObject({ endpoint: "renew", login: true, oauth: false, submit: true, action: "Renew" });
      const html = panel(entry, { step, fallbackLabel: entry.fallback!.label, usingFallback: true, onToggleFallback: noop });
      expect(inputs(html)).toEqual(["request_token"]);
      expect(html).toContain('data-login-way="paste"');
      expect(html).toContain("Address after login");
    });
  }

  it("a Zerodha / Fyers connect that answers needs_login moves to the Log in step, still called Connect", () => {
    const step = stepFor(entryOf("zerodha"), "connect", true);
    expect(step).toMatchObject({ endpoint: "renew", login: true, oauth: true, action: "Connect" });
    const html = panel(entryOf("zerodha"), { step, note: "Saved; now press Log in and sign in to Zerodha (Kite) - ATP finishes the login by itself." });
    expect(html).toContain("data-note");
    expect(html).toContain("now press Log in");
    expect(inputs(html)).toEqual([]);
    // Fyers saved its own Redirect URL the fallback way: the next step is the paste way.
    const fyers = entryOf("fyers");
    expect(stepFor(fyers, "connect", false, true).fields.map((f) => f.key)).toEqual(["api_key", "api_secret", "redirect_uri"]);
    expect(stepFor(fyers, "connect", true, true)).toMatchObject({ oauth: false, fields: [expect.objectContaining({ key: "request_token" })] });
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
      expect(html).toContain(`<details data-broker-guide="${broker}"`);          // BRK-1 C: folded on a Renew
      expect(html).not.toContain("data-guide-open");
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
    expect(html).toContain("Needs login");
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
    // Only known broker ids are named; anything else in the address is never echoed (review L7).
    expect(loginOutcome("?broker=%3Cb%3Eevil&connected=1")?.text).toBe("Your broker: Connected ✓");
    expect(loginOutcome("?broker=upstox&error=%3Cscript%3E")?.text).toBe("Upstox login did not finish: the login did not finish. Start it again from the Broker page.");
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

describe("P2b review fixes: rows that need connecting again, permissions, another account", () => {
  const row = (c: BrokerConnection, canChange = true) => renderToStaticMarkup(<ul><ConnectionRow c={c} canChange={canChange} /></ul>);

  it("a not-working row shows the server's sentence as a second line and 'Connect again' (no Renew)", () => {
    const html = row(conn({ broker: "dhan", name: "Dhan", status: "not_working", renew: false, message: "Dhan did not accept this token; generate a new one and paste it here." }));
    expect(html).toContain("data-row-message");
    expect(html).toContain("Dhan did not accept this token; generate a new one and paste it here.");
    expect(html).toContain(">Connect again</button>");
    expect(html).not.toContain(">Renew<");
  });

  it("BRK-1 B: a TOTP row whose automatic login did not work - 'Not working', its sentence, Renew as the fallback", () => {
    const message = "This morning's automatic login did not work: Angel One did not accept these details; check them, including the TOTP secret, and try again.";
    const html = row(conn({ broker: "angel_one", name: "Angel One", status: "not_working", renew: true, message }));
    expect(html).toContain("Not working");
    expect(html).toContain("data-row-message");
    expect(html).toContain(">Renew</button>");
    expect(html).toContain(">Connect again</button>");
    expect(html.match(/class="[^"]*"[^>]*>Renew<\/button>/)?.[0]).toBeTruthy();
  });

  it("a needs-login row that cannot be renewed also offers 'Connect again'; a renewable one does not", () => {
    const stuck = row(conn({ broker: "coindcx", name: "CoinDCX", status: "needs_login", renew: false, message: "CoinDCX has not confirmed these details yet; press Connect to check them." }));
    expect(stuck).toContain(">Connect again</button>");
    expect(stuck).toContain("press Connect to check them");
    const renewable = row(conn({ status: "needs_login", message: "Upstox needs a login." }));
    expect(renewable).not.toContain("Connect again");
    expect(renewable).not.toContain("data-row-message");
  });

  it("without the brokers:write permission: every action is off", () => {
    const html = row(conn({ status: "needs_login" }), false);
    expect(html.match(/<button[^>]*disabled=""/g)).toHaveLength(2);                  // Renew and Remove
    expect(renderToStaticMarkup(<BrokerTile entry={CATALOG[0]} onPick={noop} canChange={false} />)).toContain('disabled=""');
    const banner = renderToStaticMarkup(<RenewBannerView connection={conn({ status: "needs_login" })} canChange={false} />);
    expect(banner).not.toContain("<button");
    expect(banner).toContain("Ask the owner of your organisation to connect a broker.");
  });

  it("Connect another account: a broker and an account name; only valid names open the panel", () => {
    expect(validAccountName("family")).toBe(true);
    expect(validAccountName("second_2")).toBe(true);
    expect(validAccountName("two words")).toBe(false);
    expect(validAccountName("")).toBe(false);
    const html = renderToStaticMarkup(<AnotherAccount entries={CATALOG} canChange onConnect={noop} />);
    expect(html).toContain(">Account name</label>");
    expect(html.match(/<option/g)).toHaveLength(CATALOG.length);
    expect(html).toMatch(/<button[^>]*type="submit"[^>]*disabled=""/);             // no name yet
    const viewer = renderToStaticMarkup(<AnotherAccount entries={CATALOG} canChange={false} onConnect={noop} />);
    expect(viewer).toContain("Ask the owner of your organisation to connect a broker.");
  });

  it("canChangeBrokers: brokers:write decides; a profile without a scope list is left to the server", () => {
    expect(canChangeBrokers({ scopes: ["brokers:write"] })).toBe(true);
    expect(canChangeBrokers({ scopes: ["analytics:read"] })).toBe(false);
    expect(canChangeBrokers({})).toBe(true);
    expect(canChangeBrokers(null)).toBe(false);
  });
});

describe("BRK-1: no practice-only gate; the visible guide in every connect panel", () => {
  it("no tile says Practice only (Kotak Neo and Groww are tiles like the others)", () => {
    for (const entry of CATALOG) {
      const tile = renderToStaticMarkup(<BrokerTile entry={entry} onPick={noop} />);
      expect(tile).toContain(entry.name.replace(/&/g, "&amp;"));
      expect(tile).not.toMatch(/practice/i);
      expect(tile).not.toContain("data-practice-only");
    }
    expect(CATALOG.map((e) => e.broker)).toEqual(expect.arrayContaining(["kotak_neo", "groww"]));
    for (const entry of CATALOG) expect(Object.keys(entry)).not.toEqual(expect.arrayContaining(["gates"]));
  });

  it("the same one SEBI line on every broker's panel; no panel says practice", () => {
    const infos = new Set(CATALOG.map((e) => e.guide!.info));
    expect(infos.size).toBe(1);
    for (const entry of CATALOG) {
      const html = panel(entry);
      expect(html).toMatch(/data-guide-info[^>]*>.*static IP/);
      expect(html).not.toMatch(/practice/i);
      expect(ADVICE.test(html)).toBe(false);
    }
  });

  it("bold key words; a Copy button with the exact value for the redirect address and the static IP", () => {
    expect(renderToStaticMarkup(<>{boldText("Open **My apps** now.")}</>)).toBe('Open <strong class="font-semibold text-fg">My apps</strong> now.');
    const values = { redirect_url: "https://atp.example/api/broker/zerodha/oauth/callback", static_ip: "203.0.113.7" };
    const zerodha = panel(entryOf("zerodha"), { copyValues: values });
    expect(zerodha).toContain(`>${values.redirect_url}</code>`);
    expect(zerodha).toContain(`>${values.static_ip}</code>`);
    expect(zerodha.match(/data-copy-button="redirect_url"/g)).toHaveLength(1);
    // Unknown values: the step says so, with no Copy button.
    const unknown = panel(entryOf("zerodha"));
    expect(unknown).not.toContain("data-copy-button");
    expect(unknown).toContain("server IP: not set yet.");
  });

  it("Kotak Neo: the guide is open before its five fields, with the official links; Renew folds it", () => {
    const kotak = entryOf("kotak_neo");
    const html = panel(kotak);
    expect(inputs(html)).toEqual(["api_key", "mobile_number", "client_id", "pin", "totp_secret"]);
    expect(html).toContain("https://www.kotakneo.com/platform/kotak-neo-trade-api/totp-registration/");
    const renew = panel(kotak, { step: stepFor(kotak, "renew") });
    expect(renew).not.toContain("data-guide-open");
    expect(inputs(renew)).toEqual([]);
  });

  it("Groww: two fields, each from its step", () => {
    const groww = entryOf("groww");
    expect(inputs(panel(groww))).toEqual(["api_key", "totp_secret"]);
    const from = stepNumbers(groww.guide!);
    expect(Object.keys(from).sort()).toEqual(["api_key", "totp_secret"]);
  });
});
