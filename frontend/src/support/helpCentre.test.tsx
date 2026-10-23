import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it } from "vitest";
import { PAGE_TOURS, WELCOME } from "../tours/content";
import { GLOSSARY } from "../ui/glossary";
import { reportBody, safeEmail, safeWhatsapp } from "./desk";
import { glossaryArticles, howToArticles, searchHelp, tourArticles, words, type HelpArticle } from "./helpCentre";
import { forgetServerError, lastServerError, rememberServerError } from "./lastError";
import { SupportContactV2 } from "./HelpCentreV2";
import helpSource from "./HelpCentreV2.tsx?raw";
import reportSource from "./ReportProblemDialogV2.tsx?raw";
import settingsSource from "../settings/SettingsSectionV2.tsx?raw";
import errorsSource from "../api/errors.ts?raw";

/** C12: the Help centre's articles and search, "Report a problem"'s body, the last error and the contact link. */

const SERVER = [
  { id: "help:broker", title: "How do I connect my broker?", text: "Open the Broker page and use Connect your broker.", page: "/broker", keywords: "broker, connect, login" },
  { id: "help:paper", title: "What is paper trading?", text: "Every order is paper (simulated).", page: "/positions", keywords: "paper, simulated" },
  { id: "help:bad", title: "Odd link", text: "x", page: "https://evil.example", keywords: "" },
];
const ALL: HelpArticle[] = [...howToArticles(SERVER), ...tourArticles(PAGE_TOURS, WELCOME), ...glossaryArticles()];

describe("the articles", () => {
  it("come from the help pages, every page tour, the welcome cards and every glossary word", () => {
    expect(ALL.filter((a) => a.kind === "how-to")).toHaveLength(SERVER.length);
    expect(ALL.filter((a) => a.kind === "word")).toHaveLength(Object.keys(GLOSSARY).length);
    expect(ALL.filter((a) => a.kind === "page-tip")).toHaveLength(Object.keys(PAGE_TOURS).length + 1);
    const positions = ALL.find((a) => a.id === "tips:positions");
    expect(positions?.page).toBe("/positions");
    expect(positions?.text.split("\n").length).toBeGreaterThanOrEqual(3);
  });

  it("never link outside the app", () => {
    expect(ALL.find((a) => a.id === "help:bad")?.page).toBeUndefined();
  });

  it("carry no security name and no price (education surface)", () => {
    const names = /\b(NIFTY|BANKNIFTY|SENSEX|FINNIFTY|RELIANCE|INFY|TCS|HDFC\w*|ICICI\w*|SBIN)\b/i;
    const price = /(₹|\bRs\.?\s?\d|\bINR\s?\d)/;
    for (const a of [...tourArticles(PAGE_TOURS, WELCOME), ...glossaryArticles()]) {
      expect(`${a.title} ${a.text}`, a.id).not.toMatch(names);
      expect(`${a.title} ${a.text}`, a.id).not.toMatch(price);
    }
  });
});

describe("search", () => {
  it("matches every word, by the start of a word, best first", () => {
    const found = searchHelp(ALL, "connect broker");
    expect(found[0].id).toBe("help:broker");
    expect(searchHelp(ALL, "conn brok")[0].id).toBe("help:broker");
    expect(searchHelp(ALL, "paper zzzz")).toEqual([]);
  });

  it("finds a word of the glossary by its term, and plurals", () => {
    expect(searchHelp(ALL, "lots").map((a) => a.id)).toContain("word:lot");
    expect(searchHelp(ALL, "PCR")[0].id).toBe("word:pcr");
  });

  it("an empty query lists everything, how-to first", () => {
    const list = searchHelp(ALL, "  ");
    expect(list).toHaveLength(ALL.length);
    expect(list[0].kind).toBe("how-to");
    expect(list[list.length - 1].kind).toBe("word");
  });

  it("splits words in lower case", () => {
    expect(words("Open P&L, Lots")).toEqual(["open", "p&l", "lot"]);
  });
});

describe("Report a problem", () => {
  afterEach(() => forgetServerError());

  it("sends the closed list of fields: path only, app version, time and the last error", () => {
    const now = new Date("2026-10-23T05:00:00Z");
    rememberServerError("req-42", "/positions?token=abc#x", new Date("2026-10-23T04:58:00Z"));
    const body = reportBody("  It broke  ", "/settings/help?reset=tok", "0.1.0+abc", lastServerError(), now);
    expect(body).toEqual({ message: "It broke", app_version: "0.1.0+abc", screen: "/settings/help", client_time: now.toISOString(),
                           error_id: "req-42", error_screen: "/positions", error_at: "2026-10-23T04:58:00.000Z" });
    expect(Object.keys(reportBody("x", "/", "dev", null, now) ?? {}).sort()).toEqual(["app_version", "client_time", "message", "screen"]);
    expect(reportBody("   ", "/", "dev", null)).toBeNull();
  });

  it("remembers only a reference-shaped id", () => {
    rememberServerError("bad id <script>", "/");
    expect(lastServerError()).toBeNull();
    rememberServerError(null);
    expect(lastServerError()).toBeNull();
  });

  it("api/errors.ts records every server error's reference", () => {
    expect(errorsSource).toContain("rememberServerError(requestId)");
  });

  it("shows what is attached and never reads other forms", () => {
    expect(reportSource).toContain("Sent with your report");
    expect(reportSource).not.toMatch(/document\.(forms|querySelector)|FormData|localStorage/);
  });
});

describe("the contact link", () => {
  const html = (c: Parameters<typeof SupportContactV2>[0]["contact"]) => renderToStaticMarkup(<MemoryRouter><SupportContactV2 contact={c} /></MemoryRouter>);

  it("shows nothing until the owner sets a number or an address", () => {
    expect(html(null)).toBe("");
    expect(html({ configured: false, market_open: true, next_open: null, whatsapp_url: null, email: null })).toBe("");
  });

  it("links WhatsApp and e-mail while the market is open", () => {
    const out = html({ configured: true, market_open: true, next_open: null, whatsapp_url: "https://wa.me/919800000000", email: "help@example.com" });
    expect(out).toContain('href="https://wa.me/919800000000"');
    expect(out).toContain('href="mailto:help@example.com"');
    expect(out).toContain('data-support-contact="open"');
  });

  it("says when it opens again outside market hours", () => {
    const out = html({ configured: true, market_open: false, next_open: "2026-10-26T03:45:00Z", whatsapp_url: null, email: null });
    expect(out).toContain('data-support-contact="closed"');
    expect(out).toContain("09:15");
    expect(out).not.toContain("wa.me");
  });

  it("drops anything that is not a wa.me link or an address", () => {
    expect(safeWhatsapp("javascript:alert(1)")).toBeNull();
    expect(safeWhatsapp("https://wa.me/919800000000")).toBe("https://wa.me/919800000000");
    expect(safeEmail("help@example.com")).toBe("help@example.com");
    expect(safeEmail("x<script>@example.com")).toBeNull();
  });
});

describe("wiring", () => {
  it("the Help centre sits in Settings › Help & tips behind help_centre, in its own chunk", () => {
    expect(settingsSource).toContain('useFeatures().on("help_centre")');
    expect(settingsSource).toContain('lazy(() => import("../support/HelpCentreV2"))');
    expect(helpSource).toContain("deskApi.help()");
    expect(helpSource).toContain('location.hash === "#report"');
  });

  it("the contact line names the hours only as the market's own", () => {
    expect(helpSource).not.toMatch(/9:15|15:30/);
  });
});
