import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const request = vi.fn();
vi.mock("../api/client", () => ({ request: (...args: unknown[]) => request(...args) }));

import { isItemVisible, navItemFor, searchPages, visibleSections } from "../shell/navModel";
import { closedFlag, PAGE_FLAGS } from "../shell/pageFlags";
import { flagOffFallback } from "../shell/aliases";
import { shellKeyFromPath, shellTitle } from "../shell/shellRoutes";
import { PAGE_COMPONENTS_V2 } from "../shell/pagesV2";
import { USER_PAGE_ROOTS } from "../ux/bannedUiWords";
import shellSource from "../shell/AppShellV2.tsx?raw";
import pageSource from "./MyRunnerPageV2.tsx?raw";
import {
  DeviceCard, KillSwitchView, MirrorView, PairingCodeView, REVOKE_TITLE, RunnerBody, SettingsCard, killStateText, revokedNote,
  type RunnerBodyProps,
} from "./MyRunnerPageV2";
import {
  BROKER_KEYS_LINE, DEFAULT_SETTINGS, INSTALL_LINE, KILL_OFF_LINE, KILL_ON_LINE, RUNNER_ADDRESS, activeDevices, ago, clock, loginLine,
  onlineState, parseMinutes, planAllowsRunner, runnerApi, secondsLeft, type MirrorAnswer, type RunnerDevice,
} from "./model";

/**
 * Runner S7: the "My runner" page. No DOM in the unit tests: the views take everything as props and are rendered as
 * markup; the page's calls are checked against the S3 API. Every name, IP and number here is made up.
 */
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, "\"").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const NOW = Date.parse("2026-11-14T16:00:00+05:30");
const render = (node: React.ReactNode) => renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>);

const device = (over: Partial<RunnerDevice> = {}): RunnerDevice => ({
  device_id: "a1b2c3", name: "My VPS", status: "ONLINE", version: "0.1.0", paired_at: "2026-11-14T10:00:00Z",
  last_heartbeat_at: new Date(NOW - 2 * 60_000).toISOString(), revoked_at: null, run_state: "running", kill_switch_on: false,
  open_positions: 1, broker_session: "valid", egress_ip: "203.0.113.7", ...over,
});
const body = (over: Partial<RunnerBodyProps> = {}) => render(
  <RunnerBody devices={[]} settings={DEFAULT_SETTINGS} failed={false} now={NOW} onRevoke={() => {}} onChanged={() => {}} onSettings={() => {}}
    mirror={() => null} {...over} />);

beforeEach(() => { request.mockReset(); });

describe("empty state: the plain set-up steps", () => {
  it("lists the six steps in order, with the install line and its Copy button", () => {
    const html = body();
    expect(html).toContain("data-runner-empty");
    const t = text(html);
    const order = ["Rent a small VPS with a static IP", "Paste this one line on your VPS", "Create a pairing code",
      "Register your VPS IP at Angel One", "Add your broker keys on the VPS", "Done"];
    let at = -1;
    for (const step of order) { const i = t.indexOf(step); expect(i, step).toBeGreaterThan(at); at = i; }
    expect(t).toContain(INSTALL_LINE);
    expect(t).toContain(BROKER_KEYS_LINE);
    expect(html).toContain('aria-label="Copy: install line"');
    expect(html).toContain("Create pairing code");
  });

  it("the install line is the one from docs/RUNNER_INSTALL.md (the latest release's install.sh)", () => {
    expect(INSTALL_LINE).toMatch(/^curl -fsSL https:\/\/github\.com\/[^ ]+\/releases\/latest\/download\/install\.sh -o atp-runner-install\.sh && sudo bash atp-runner-install\.sh$/);
  });

  it("offers 'We set it up for you' through the existing support path, with no payment code", () => {
    const html = body();
    expect(text(html)).toContain("We set it up for you");
    expect(html).toContain('href="/settings/help"');
    expect(pageSource).not.toMatch(/razorpay|checkout|payment_intent|price ₹/i);
  });

  it("a removed runner alone still shows the steps", () => {
    expect(body({ devices: [device({ status: "REVOKED", revoked_at: "2026-11-14T11:00:00Z" })] })).toContain("data-runner-empty");
  });

  it("while loading or failed: no steps, no figures", () => {
    expect(body({ devices: null })).toContain('aria-busy="true"');
    expect(text(body({ devices: null, failed: true }))).toContain("We could not load your runner");
  });
});

describe("the pairing code (S3: shown once, 10 minutes)", () => {
  const code = { code: "ABCD2345", expires_at: new Date(NOW + 10 * 60_000).toISOString(), expires_in_minutes: 10 };
  const view = (now: number, c: typeof code | null = code) =>
    text(renderToStaticMarkup(<PairingCodeView code={c} now={now} busy={false} error={null} onCreate={() => {}} />));

  it("shows the code and counts down", () => {
    expect(view(NOW)).toContain("ABCD2345");
    expect(view(NOW)).toContain("Expires in 10:00");
    expect(view(NOW + 61_500)).toContain("Expires in 8:59");
    expect(view(NOW + 9 * 60_000 + 55_000)).toContain("Expires in 0:05");
  });

  it("an expired code is gone: a new one can be created", () => {
    const t = view(NOW + 10 * 60_000);
    expect(t).not.toContain("ABCD2345");
    expect(t).toContain("That code has expired");
    expect(t).toContain("Create pairing code");
    expect(view(NOW, null)).not.toContain("expired");
  });

  it("the helpers", () => {
    expect(secondsLeft(code.expires_at, NOW)).toBe(600);
    expect(secondsLeft(code.expires_at, NOW + 11 * 60_000)).toBe(0);
    expect(secondsLeft("not a date", NOW)).toBe(0);
    expect(clock(600)).toBe("10:00");
    expect(clock(9)).toBe("0:09");
  });

  it("is created with POST /runner/pairing-codes", async () => {
    request.mockResolvedValue(code);
    await runnerApi.createCode();
    expect(request).toHaveBeenCalledWith("/runner/pairing-codes", { method: "POST" });
  });
});

describe("paired: a runner's card", () => {
  const card = (d: RunnerDevice) => render(<DeviceCard device={d} now={NOW} onRevoke={() => {}} onChanged={() => {}} />);

  it("online: name, Online, last seen, version, broker login", () => {
    const html = card(device());
    const t = text(html);
    expect(html).toContain('data-online="online"');
    expect(t).toContain("My VPS");
    expect(t).toContain("Online");
    expect(t).toContain("Last seen 2 min ago");
    expect(t).toContain("Version 0.1.0");
    expect(t).toContain("Broker login: active");
    expect(t).toContain("Remove runner");
  });

  it("offline: Offline, and how to check the VPS", () => {
    const html = card(device({ status: "OFFLINE", last_heartbeat_at: new Date(NOW - 3 * 3600_000).toISOString(), broker_session: "expired" }));
    expect(html).toContain('data-online="offline"');
    const t = text(html);
    expect(t).toContain("Offline");
    expect(t).toContain("Last seen 3 h ago");
    expect(t).toContain("Check that your VPS is on and online");
    expect(t).toContain("Broker login: expired");
  });

  it("paired, nothing reported yet", () => {
    const t = text(card(device({ status: "PAIRED", last_heartbeat_at: null, version: null, broker_session: null, kill_switch_on: null })));
    expect(t).toContain("Waiting for its first report");
    expect(t).toContain("Last seen Not yet");
    expect(t).toContain("Broker login: not reported yet");
    expect(t).toContain("Kill switch: Not reported yet");
  });

  it("the paired page: cards, link settings, the copies and Advanced", () => {
    const html = body({ devices: [device(), device({ device_id: "d4e5f6", name: "Spare", status: "OFFLINE" }),
      device({ device_id: "gone", name: "Old", status: "REVOKED", revoked_at: "2026-11-14T09:00:00Z" })], mirror: (d) => <p>{`copies of ${d.name}`}</p> });
    expect(html).toContain("data-runner-paired");
    const t = text(html);
    expect(t).toContain("My VPS");
    expect(t).toContain("Spare");
    expect(t).not.toContain("Old Online");
    expect(t).toContain("copies of My VPS");
    expect(t).toContain("Link settings");
    expect(t).toContain("Removed runners: 1");
  });

  it("the plain helpers", () => {
    expect(onlineState(device({ status: "ONLINE" }))).toBe("online");
    expect(onlineState(device({ status: "OFFLINE" }))).toBe("offline");
    expect(onlineState(device({ status: "PAIRED" }))).toBe("waiting");
    expect(activeDevices([device(), device({ status: "REVOKED" })])).toHaveLength(1);
    expect(ago(new Date(NOW - 20_000).toISOString(), NOW)).toBe("just now");
    expect(ago(new Date(NOW - 3 * 86400_000).toISOString(), NOW)).toBe("3 days ago");
    expect(ago(null, NOW)).toBeNull();
    expect(loginLine(device({ broker_session: "none" })).text).toContain("no broker keys");
  });
});

describe("HIS kill switch (rule R-1)", () => {
  const kill = (d: RunnerDevice) => renderToStaticMarkup(<KillSwitchView device={d} busy={false} note={null} onToggle={() => {}} />);

  it("with the link's relay (S4): his own toggle button, no command", () => {
    const off = kill(device({ kill_relay: true, kill_switch_on: false }));
    expect(off).toContain("data-kill-toggle");
    expect(text(off)).toContain("Turn kill switch on");
    expect(off).not.toContain("data-kill-fallback");
    const on = kill(device({ kill_relay: true, kill_switch_on: true }));
    expect(text(on)).toContain("Turn kill switch off");
    expect(text(on)).toContain("Kill switch: ON - no new entries; exits continue");
  });

  it("without the relay (before S4): the state read-only and the command for his runner", () => {
    const off = kill(device({ kill_switch_on: false }));
    expect(off).not.toContain("data-kill-toggle");
    expect(text(off)).toContain("Turn it on on your runner:");
    expect(text(off)).toContain(KILL_ON_LINE);
    const on = kill(device({ kill_switch_on: true }));
    expect(text(on)).toContain("Turn it off on your runner:");
    expect(text(on)).toContain(KILL_OFF_LINE);
    expect(killStateText(false)).toBe("OFF");
  });

  it("the relay posts his choice to his own runner's address", async () => {
    request.mockResolvedValue({});
    await runnerApi.setKill("a1b2c3", true);
    expect(request).toHaveBeenCalledWith("/runner/devices/a1b2c3/kill-switch", { method: "POST", body: JSON.stringify({ on: true }) });
  });
});

describe("Remove runner (revoke)", () => {
  it("asks first, then posts to his runner's revoke address", async () => {
    expect(REVOKE_TITLE).toBe("Remove this runner?");
    expect(pageSource).toMatch(/<ConfirmDialog open=\{revoking !== null\}[^]*onConfirm=\{\(\) => void revoke\(\)\}/);
    expect(pageSource).toContain("onRevoke={() => onRevoke(d)}");
    request.mockResolvedValue(device({ status: "REVOKED" }));
    await runnerApi.revoke("a1b2c3");
    expect(request).toHaveBeenCalledWith("/runner/devices/a1b2c3/revoke", { method: "POST" });
    expect(revokedNote("My VPS")).toBe("My VPS was removed from your account.");
  });
});

describe("his link settings and the copies", () => {
  it("copies toggle and offline minutes", async () => {
    const t = text(render(<SettingsCard settings={{ ...DEFAULT_SETTINGS, offline_after_minutes: 5 }} onSaved={() => {}} />));
    expect(t).toContain("Send copies to ATP");
    expect(t).toContain("Alert me when my runner is offline for");
    expect(parseMinutes("5")).toBe(5);
    expect(parseMinutes("0")).toBeNull();
    expect(parseMinutes("241")).toBeNull();
    expect(parseMinutes("2.5")).toBeNull();
    request.mockResolvedValue({});
    await runnerApi.saveSettings({ ...DEFAULT_SETTINGS, mirror_upload: false });
    expect(request).toHaveBeenCalledWith("/me/prefs/runner", { method: "PUT", body: JSON.stringify({ ...DEFAULT_SETTINGS, mirror_upload: false }) });
  });

  it("the copies are labelled 'From your runner', read-only, P&L coloured", () => {
    const data: MirrorAnswer = {
      device: device(),
      positions: [
        { ref: "p1", deployment_ref: null, mode: "PAPER", symbol: "MADEUP", exchange: "NSE", status: "OPEN", quantity: 50, average_price: 100,
          last_price: 101, unrealised_pnl: 50, realised_pnl: null, exit_reason: null, opened_at: null, closed_at: null, updated_at: null },
        { ref: "p2", deployment_ref: null, mode: "PAPER", symbol: "CLOSEDONE", exchange: "NSE", status: "CLOSED", quantity: 0, average_price: 1,
          last_price: 1, unrealised_pnl: 0, realised_pnl: -5, exit_reason: null, opened_at: null, closed_at: null, updated_at: null },
      ],
      orders: [{ ref: "o1", deployment_ref: null, mode: "PAPER", symbol: "MADEUP", exchange: "NSE", side: "BUY", order_type: "LIMIT", quantity: 50,
        price: 100, trigger_price: null, status: "COMPLETE", filled_quantity: 50, average_price: 100, reason: null, placed_at: null, updated_at: null }],
    };
    const html = renderToStaticMarkup(<MirrorView device={device()} data={data} failed={false} mirrorOn />);
    const t = text(html);
    expect(t).toContain("From your runner");
    expect(t).toContain("read-only");
    expect(t).toContain("Open positions (1)");
    expect(t).not.toContain("CLOSEDONE");
    expect(html).toMatch(/text-up">50\.00</);
    expect(t).toContain("Complete");
    expect(html).not.toMatch(/<button/);
    expect(text(renderToStaticMarkup(<MirrorView device={device()} data={data} failed={false} mirrorOn={false} />))).toContain("Copies are off");
  });
});

describe("the shell wiring", () => {
  const on = (...f: string[]) => (x: string) => f.includes(x);

  it("/runner is a v2-only page behind `user_runner`; with ui_v2 off it goes to the dashboard", () => {
    expect(RUNNER_ADDRESS).toBe("/runner");
    expect(shellKeyFromPath("/runner")).toBe("runner");
    expect(shellTitle("runner")).toBe("My runner");
    expect(PAGE_FLAGS.runner).toBe("user_runner");
    expect(closedFlag("runner", { answered: true, on: on() })).toBe("user_runner");
    expect(closedFlag("runner", { answered: true, on: on("user_runner") })).toBeNull();
    expect(flagOffFallback("/runner")).toBe("/");
    expect(PAGE_COMPONENTS_V2.runner).toBeTruthy();
    expect(shellSource).toMatch(/<Route path=\{RUNNER_ADDRESS\} caseSensitive element=\{<RunnerPage onNavigate=\{go\} \/>\} \/>/);
    expect(USER_PAGE_ROOTS).toContain("runner");
  });

  it("listed under Account only while the flag is on; the plan hook hides it only on an explicit `runner: false`", () => {
    const item = navItemFor("runner")!;
    expect(item).toMatchObject({ label: "My runner", path: "/runner", section: "account", plan: "runner" });
    expect(isItemVisible(item, { on: on(), role: "OWNER" })).toBe(false);
    expect(isItemVisible(item, { on: on("user_runner"), role: "TRADER" })).toBe(true);
    expect(isItemVisible(item, { on: on("user_runner"), role: "TRADER", plan: { runner: true } })).toBe(true);
    expect(isItemVisible(item, { on: on("user_runner"), role: "TRADER", plan: {} })).toBe(true);
    expect(isItemVisible(item, { on: on("user_runner"), role: "TRADER", plan: { runner: false } })).toBe(false);
    const account = visibleSections({ on: on("user_runner"), role: "OWNER" }).find((s) => s.id === "account")!;
    expect(account.items.map((i) => i.label)).toEqual(["Settings", "My runner"]);
    expect(searchPages("static ip", { on: on("user_runner"), role: "OWNER" })[0]?.key).toBe("runner");
    expect(planAllowsRunner(null)).toBe(true);
    expect(planAllowsRunner({ runner: false })).toBe(false);
  });

  it("the words are plain: no AI-bot wording, no return claims, no internal words outside Advanced", () => {
    const words = text(body()) + text(body({ devices: [device()], mirror: () => null }));
    expect(words).not.toMatch(/\bAI\b|\bbot\b|returns?\b|profit|guarantee|heartbeat|device_id|egress|tenant|flag/i);
  });
});
