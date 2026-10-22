import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import type { AiConnection } from "./connect";
import { ACCOUNT_ID_HELP, CONSENT_LINE, ConnectAiView, needsAccountId, pastedText, readyToConnect, type ConnectAiViewProps } from "./ConnectAiPanelV2";
import { FREE_TIER_NOTICE, guideFor, guideTags } from "./guides";
import KeyGuidesV2 from "./KeyGuidesV2";
import { PROVIDER_FACTS, detectProvider } from "./keyFormats";

/** MO2A-2: Cloudflare Workers AI in the Connect AI panel and its key guide (behind `ai_one_key`). Cloudflare needs the
 * API token and the Account ID: one paste with both, or the token and then the Account ID box. Key-shaped strings are
 * built here at run time from parts: this repository is public. */

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, "\"").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const noop = () => undefined;
const fill = (n: number) => "x1Y2".repeat(Math.ceil(n / 4)).slice(0, n);
const token = () => ["cfut", "_", fill(48)].join("");
const ACCOUNT = "0123456789abcdef".repeat(2);

const LIMITS = { month_inr: 0, monthly_cap_inr: 880, today_inr: 0, daily_cap_inr: 88, max_cap_inr: 44000, line: "AI this month: ₹0 of your ₹880 limit", reached: null };
const NONE: AiConnection = {
  connected: false, provider: null, label: null, key_end: null, ok: null, error: null, tested_at: null, consent: null, may_train: false,
  paid_plan: true, free_tier_warning: null, limits: LIMITS, copy_tenant_key: null,
};

function view(props: Partial<ConnectAiViewProps> = {}): string {
  const all: ConnectAiViewProps = {
    conn: NONE, raw: "", picked: null, consent: false, step: "idle", notice: null, fixing: false, onRaw: noop, onPick: noop, onConsent: noop,
    onDisconnect: noop, onFix: noop, onGuides: noop, onAcknowledge: noop, onCopy: noop, onLimits: noop, ...props,
  };
  return renderToStaticMarkup(<MemoryRouter><ConnectAiView {...all} /></MemoryRouter>);
}

describe("MO2A-2: Cloudflare in the Connect AI panel", () => {
  it("a token alone: detected, the Account ID box, and nothing is sent until the Account ID is there", () => {
    const html = view({ raw: token() });
    expect(text(html)).toContain("This is a Cloudflare Workers AI key.");
    expect(html).toContain("data-account-id");
    expect(text(html)).toContain(ACCOUNT_ID_HELP);
    expect(text(html)).toContain(CONSENT_LINE("Cloudflare Workers AI"));
    expect(needsAccountId(token(), null)).toBe(true);
    expect(readyToConnect(token(), null, true, "idle")).toBe(false);
    expect(readyToConnect(token(), null, true, "idle", "1234")).toBe(false);
    expect(readyToConnect(token(), null, true, "idle", ` ${ACCOUNT} `)).toBe(true);
  });

  it("one paste with the token and the Account ID needs nothing more", () => {
    const raw = `${ACCOUNT} ${token()}`;
    const html = view({ raw });
    expect(text(html)).toContain("This is a Cloudflare Workers AI key with its Account ID.");
    expect(html).not.toContain("data-account-id");
    expect(readyToConnect(raw, null, true, "idle")).toBe(true);
    expect(readyToConnect(raw, null, false, "idle")).toBe(false);                     // the consent line still comes first
  });

  it("a line break between the token and the Account ID is kept as a space when pasted", () => {
    expect(pastedText(`  ${token()}\r\n${ACCOUNT}\n`)).toBe(`${token()} ${ACCOUNT}`);
    expect(detectProvider(pastedText(`${token()}\n${ACCOUNT}`)).provider).toBe("cloudflare");
  });

  it("an older token without a prefix: the picker offers Cloudflare, then the Account ID box", () => {
    const old = fill(40);
    expect(text(view({ raw: old }))).toContain("Cloudflare Workers AI");
    expect(view({ raw: old, picked: "cloudflare" })).toContain("data-account-id");
    expect(readyToConnect(old, "cloudflare", true, "idle", ACCOUNT)).toBe(true);
  });

  it("before saving: the free-tier notice and Cloudflare's data line; it reads charts and has no may-train warning", () => {
    const out = text(view({ raw: token() }));
    expect(out).toContain(FREE_TIER_NOTICE);
    expect(out).toContain(PROVIDER_FACTS.cloudflare.privacy);
    expect(out).not.toContain("may be used to train");
    expect(out).not.toContain("cannot read charts");
  });

  it("a wrong Account ID gets one plain line", () => {
    expect(text(view({ raw: token(), accountId: "not-it" }))).toContain("An Account ID is 32 letters and numbers.");
  });
});

describe("MO2A-2: the Cloudflare key guide", () => {
  it("has the steps for the token and the Account ID, its tags and its checked date", () => {
    const g = guideFor("cloudflare");
    expect(g.provider).toBe("cloudflare");
    expect(g.key_page.url).toBe("https://dash.cloudflare.com/?to=/:account/ai/workers-ai");
    expect(g.checked_on).toBe("2026-10-22");
    expect(g.cost).toBe("Free tier");
    expect(g.steps.map((s) => s.text).join(" ")).toMatch(/Use REST API.*Create a Workers AI API Token.*Account ID/);
    expect(guideTags(g)).toEqual({ vision: true, may_train: false, privacy: PROVIDER_FACTS.cloudflare.privacy });
    const out = text(renderToStaticMarkup(<KeyGuidesV2 pasteHelper={false} startOpen="cloudflare" />));
    expect(out).toContain("Cloudflare Workers AI");
    expect(out).toContain("Reads charts (vision)");
  });
});
