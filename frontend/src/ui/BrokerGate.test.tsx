import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { pathFor } from "../routes";
import { BrokerGateView, CONNECT_BROKER_PATH, SIGN_IN_PATH } from "./BrokerGate";
import { brokerGateState } from "./brokerSession";
import { tokenRow } from "./brokerRows.fixture";

const REAL = <p>REAL PAGE CONTENT</p>;
const base = { authLoading: false, signedIn: true, failed: false };
const render = (tokens: Parameters<typeof brokerGateState>[0]["tokens"], extra: Partial<Parameters<typeof brokerGateState>[0]> = {}, props = {}) =>
  renderToStaticMarkup(<BrokerGateView state={brokerGateState({ ...base, tokens, ...extra })} {...props}>{REAL}</BrokerGateView>);

/** Developer wording that must never reach a trader (UI v2 proposal §3), and the words a sample-data fallback uses. */
const FORBIDDEN = /sample|demo|placeholder|token_status|needs_login|registry|adapter|\bflag\b|backend|503|request_token|auth_code|authenticate/i;

describe("UI2-0 BrokerGate: the three states", () => {
  it("no broker stored: 'Connect your broker' with one primary action to Settings, and no page content", () => {
    const html = render([]);
    expect(html).toContain('data-broker-gate="no-broker"');
    expect(html).toContain("Connect your broker");
    expect(html).toContain("Connect a broker");
    expect(html).toContain(`href="${CONNECT_BROKER_PATH}"`);
    expect(html).not.toContain("REAL PAGE CONTENT");
    expect(html.match(/(?<![:\w-])bg-brand(?![\w-])/g)?.length).toBe(1);                         // one primary action
    expect(html).not.toMatch(/text-warn|bg-warn/);                           // not a warning: neutral
  });

  it("stored but not logged in today: 'Log in to <Broker>' with the broker's name, and no page content", () => {
    const html = render([tokenRow("upstox")]);
    expect(html).toContain('data-broker-gate="login-needed"');
    expect(html).toContain("Log in to Upstox");
    expect(html).toContain("Your Upstox session has ended.");
    expect(html).toContain("we never see or store your broker password");
    expect(html).not.toContain("REAL PAGE CONTENT");
    expect(html.match(/(?<![:\w-])bg-brand(?![\w-])/g)?.length).toBe(1);

    const notYet = render([tokenRow("angel_one", { token_status: "UNKNOWN" })]);
    expect(notYet).toContain("Log in to Angel One to see live prices and your positions.");
    expect(notYet).not.toMatch(/never see or store/);                       // TOTP broker: never that sentence
    expect(notYet).toContain("PIN and TOTP secret you stored, encrypted");
  });

  it("connected: renders the children exactly, nothing added", () => {
    const html = render([tokenRow("upstox", { needs_login: false, token_status: "VALID" })]);
    expect(html).toBe("<p>REAL PAGE CONTENT</p>");
  });
});

describe("UI2-0 BrokerGate: never sample data in place of real data", () => {
  it("renders the page's content in the connected state only", () => {
    const states: [string, string][] = [
      ["loading", render(null)],
      ["signed-out", render(null, { signedIn: false })],
      ["unavailable", render(null, { failed: true })],
      ["no-broker", render([])],
      ["login-needed", render([tokenRow("zerodha")])],
    ];
    for (const [name, html] of states) {
      expect(html, name).not.toContain("REAL PAGE CONTENT");
      expect(html.replace(/<[^>]+>/g, " "), name).not.toMatch(FORBIDDEN);
    }
  });

  it("loading is announced and shows no figures", () => {
    const html = render(null);
    expect(html).toContain('aria-busy="true"');
    expect(html).toContain("Checking your broker connection");
    expect(html.replace(/<[^>]+>/g, "")).not.toMatch(/\d/);
  });

  it("a failed check offers 'Try again', never the page", () => {
    const html = render(null, { failed: true });
    expect(html).toContain("We could not check your broker connection");
    expect(html).toContain("Try again");
  });

  it("signed out: 'Sign in' to the account page", () => {
    const html = render(null, { signedIn: false });
    expect(html).toContain("Sign in to see your data");
    expect(html).toContain(`href="${SIGN_IN_PATH}"`);
  });
});

describe("UI2-0 BrokerGate: details", () => {
  it("names a second account and lists other brokers waiting for a login, with one primary action", () => {
    const html = render([tokenRow("zerodha", { account_label: "desk" }), tokenRow("upstox")]);
    expect(html).toContain("Log in to Upstox");
    expect(html).toContain("Also waiting for a login:");
    expect(html).toContain("Log in to Zerodha (desk)");
    expect(html.match(/(?<![:\w-])bg-brand(?![\w-])/g)?.length).toBe(1);
  });

  it("the login-link step: plain instructions, the link, and a labelled field (no code parameter names)", () => {
    const t = tokenRow("zerodha");
    const html = renderToStaticMarkup(
      <BrokerGateView state={brokerGateState({ ...base, tokens: [t] })} codeStep={{ broker: t, link: "https://kite.example/connect", opened: true }}>{REAL}</BrokerGateView>,
    );
    expect(html).toContain("The Zerodha login page opened in a new tab.");
    expect(html).toContain('href="https://kite.example/connect"');
    expect(html).toContain("Address or code from Zerodha");
    expect(html).toContain("Finish logging in");
    expect(html).not.toMatch(/request_token|auth_code/);
  });

  it("shows a failed login in amber with what to do next", () => {
    const html = render([tokenRow("upstox")], {}, { error: "Upstox did not accept the login. Try again." });
    expect(html).toContain('role="alert"');
    expect(html).toContain("Upstox did not accept the login. Try again.");
    expect(html).toMatch(/text-warn/);
  });

  it("keeps the Connect and Sign-in paths on today's routes", () => {
    expect(CONNECT_BROKER_PATH).toBe(pathFor("settings"));
    expect(SIGN_IN_PATH).toBe(pathFor("account"));
  });
});

describe("UI2-0 review fixes: BrokerGate", () => {
  const codeView = (tokens: ReturnType<typeof tokenRow>[], stepFor: ReturnType<typeof tokenRow>) => renderToStaticMarkup(
    <BrokerGateView state={brokerGateState({ ...base, tokens })} codeStep={{ broker: stepFor, link: "https://broker.example/login", opened: true }}>{REAL}</BrokerGateView>,
  );

  it("M1: the paste field shows for a login-link broker started from 'Also waiting for a login'", () => {
    const upstox = tokenRow("upstox");
    const zerodha = tokenRow("zerodha");                       // also labelled "primary", a different broker
    const html = codeView([upstox, zerodha], zerodha);
    expect(html).toContain('data-code-step="zerodha/primary"');
    expect(html).toContain("Address or code from Zerodha");
    expect(html).toContain("Finish logging in");
    expect(html).toContain("Log in to Upstox");                // the primary broker keeps its own button
    expect(html).not.toContain('data-code-step="upstox/primary"');
    expect(html.match(/(?<![:\w-])bg-brand(?![\w-])/g)?.length).toBe(1);   // still one primary action
  });

  it("M1: the paste field shows for a second account at the same broker, and only for the account it was started for", () => {
    const desk = tokenRow("fyers", { account_label: "desk" });
    const main = tokenRow("fyers");
    const html = codeView([main, desk], desk);
    expect(html).toContain('data-code-step="fyers/desk"');
    expect(html).not.toContain('data-code-step="fyers/primary"');
    expect(html.match(/Address or code from Fyers/g)?.length).toBe(1);
  });

  it("M1: the primary broker's paste field still shows in place of its button", () => {
    const zerodha = tokenRow("zerodha");
    const html = codeView([zerodha, tokenRow("fyers", { account_label: "desk" })], zerodha);
    expect(html).toContain('data-code-step="zerodha/primary"');
    expect(html).toContain("Log in to Fyers (desk)");
  });

  it("L5: the paste field is plain text that browsers do not offer to save as a password", () => {
    const t = tokenRow("zerodha");
    const html = codeView([t], t);
    expect(html).toContain('type="text"');
    expect(html).toContain('autoComplete="off"');
    expect(html).not.toContain('type="password"');
  });

  it("nit: the gate's link buttons use the control radius token", () => {
    for (const html of [render([]), render(null, { signedIn: false })]) {
      expect(html).toContain("rounded-control bg-brand");
      expect(html).not.toContain("rounded-md bg-brand");
    }
  });
});
