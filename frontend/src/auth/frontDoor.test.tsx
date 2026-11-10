import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { callWords } from "../signals/language";
import { FRONT_EN, FRONT_HI, FRONT_MR } from "../i18n/frontDoor";
import type { SignInProvider } from "./social";

/**
 * M95 F2 / F4 / F5: the front door around the Sign in card - Google on / off, self_signup on / off, the language
 * switch, the footer sentence, the FAQ in <details>, the image slots, and no trade-call / claim words.
 */
const GOOGLE: SignInProvider = { id: "google", label: "Google", kind: "oidc", start: "/auth/google/start" };
const state = vi.hoisted(() => ({ providers: [] as SignInProvider[], v2: false }));

vi.mock("./AuthContext", () => ({
  useAuth: () => ({ login: vi.fn(), completeMfaLogin: vi.fn(), register: vi.fn(), acceptInvite: vi.fn(), resetPassword: vi.fn() }),
}));
vi.mock("./SocialButtons", async (importOriginal) => {
  const real = await importOriginal<typeof import("./SocialButtons")>();
  return { ...real, useSignInProviders: () => ({ providers: state.providers, v2: state.v2 }) };
});

import SignInScreen, { InviteRequestSlot, NewHereLine } from "./SignInScreen";
import { BENEFITS, FRONT_LANG_KEY, FrontDoorDetails, IMAGE_H, IMAGE_W, LanguageSwitch, brokersAnswer, readFrontLang, safeHttps, storeFrontLang } from "./FrontDoor";
import screenSource from "./SignInScreen.tsx?raw";

const html = () => renderToStaticMarkup(<MemoryRouter initialEntries={["/charts"]}><SignInScreen /></MemoryRouter>);
const text = (markup: string) => markup.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&");

function storage(initial: Record<string, string> = {}, blocked = false) {
  const data = new Map(Object.entries(initial));
  const fail = () => { throw new Error("blocked"); };
  return {
    getItem: (k: string) => (blocked ? fail() : data.get(k) ?? null),
    setItem: (k: string, v: string) => (blocked ? fail() : void data.set(k, v)),
    data,
  };
}

afterEach(() => {
  state.providers = [];
  state.v2 = false;
  vi.unstubAllGlobals();
});

describe("M95 F2: the front door on Sign in", () => {
  it("one line saying what the app is, the Sign in card, six benefit cards, 3 steps, the trust lines", () => {
    const out = html();
    expect(out).toContain('data-front-door=""');
    expect(out).toContain(">Sign in</h1>");                                         // the heading still works
    expect(out).toContain(FRONT_EN.headline);
    expect(out.match(/data-front-benefit="/g)?.length).toBe(6);
    for (const title of [FRONT_EN.copilotTitle, FRONT_EN.chainTitle, FRONT_EN.setupsTitle, FRONT_EN.botTitle, FRONT_EN.journalTitle, FRONT_EN.reportTitle]) {
      expect(text(out)).toContain(title);
    }
    expect(out).toContain("Start in 3 steps");
    for (const line of ["No order is placed without you", "Your broker data stays yours", "Paper by default"]) expect(out).toContain(line);
    expect(out.indexOf('id="sign-in-heading"')).toBeLessThan(out.indexOf("data-front-door-details"));   // the card first on a phone
  });

  it("each card has a lazy image slot with fixed size and alt text", () => {
    const out = html();
    for (const b of BENEFITS) {
      const img = out.match(new RegExp(`<img[^>]*src="/front/${b.slug}.png"[^>]*>`))?.[0] ?? "";
      expect(img).toContain(`width="${IMAGE_W}"`);
      expect(img).toContain(`height="${IMAGE_H}"`);
      expect(img).toContain('loading="lazy"');
      expect(img).toMatch(/alt="[^"]+: a screen of the app"/);
    }
  });

  it("the footer is exactly the M94 sentence", () => {
    expect(html()).toContain(">Information and education only, not investment advice.</p>");
    expect(screenSource).not.toContain("Information and education, not investment advice.");
  });

  it("the FAQ: four questions, each folded inside <details>", () => {
    const out = html();
    const faq = out.match(/<div[^>]*data-front-faq[^>]*>[\s\S]*?<\/div>/)?.[0] ?? "";
    expect(faq.match(/<details/g)?.length).toBe(4);
    expect(faq).not.toMatch(/<details[^>]*\bopen\b/);
    expect(faq).toContain("What does it cost?");
    expect(faq).toContain("Free during the beta.");
    expect(faq).toContain("Which brokers can I connect?");
    expect(faq).toContain("Is my money safe?");
    expect(faq).toContain("What is paper trading?");
  });

  it("the broker answer reads the server's list, else a plain sentence", () => {
    expect(brokersAnswer(FRONT_EN, ["zerodha", "angel_one", "kotak_neo"])).toBe("Zerodha, Angel One, Kotak Neo. You can also practise on paper without a broker.");
    expect(brokersAnswer(FRONT_EN, null)).toBe(FRONT_EN.faqBrokersNone);
    expect(brokersAnswer(FRONT_EN, ["<script>"])).toBe(FRONT_EN.faqBrokersNone);
  });
});

describe("M95 F2 / Q2: the large Google button only when the server lists Google", () => {
  it("Google off: no Google button, step 1 is the email sign-in", () => {
    const out = html();
    expect(out).not.toContain("Continue with Google");
    expect(out).toContain("Sign in with your email");
  });

  it("Google on: the large button on top of the email form, with ui_v2 on or off, and step 1 names it", () => {
    for (const v2 of [false, true]) {
      state.providers = [GOOGLE];
      state.v2 = v2;
      const out = html();
      const button = out.indexOf('data-provider="google"');
      expect(button).toBeGreaterThan(-1);
      expect(button).toBeLessThan(out.indexOf("<form"));
      expect(out).toMatch(/data-provider="google"[^>]*class="[^"]*h-12/);
      expect(out).toContain("or use email");
      expect(out.match(/Continue with Google/g)?.length).toBe(2);           // the button and step 1
      expect(out).toContain(">Sign in</h1>");
    }
  });
});

describe("M95 F6: self_signup on / off", () => {
  it("on: Create an account, as today", () => {
    const out = renderToStaticMarkup(<NewHereLine open onCreate={() => undefined} />);
    expect(out).toContain("Create an account");
    expect(out).not.toContain("data-invite-request-slot");
  });

  it("off: the invite mount point (today's sentence until the invite form lands)", () => {
    const out = renderToStaticMarkup(<NewHereLine open={false} onCreate={() => undefined} />);
    expect(out).not.toContain("Create an account");
    expect(out).toBe(renderToStaticMarkup(<InviteRequestSlot />));
    expect(out).toContain("New here? Ask your team owner for an invite link.");
    expect(html()).toContain("data-invite-request-slot");                   // closed until the server says open
  });
});

describe("M95 F5: English / Marathi / Hindi", () => {
  it("every bundle has every key, and the brand / footer rules", () => {
    expect(Object.keys(FRONT_MR).sort()).toEqual(Object.keys(FRONT_EN).sort());
    expect(Object.keys(FRONT_HI).sort()).toEqual(Object.keys(FRONT_EN).sort());
    for (const b of [FRONT_MR, FRONT_HI]) {
      expect(b.faqBrokersA).toContain("{brokers}");
      expect(b.screenOf).toContain("{title}");
    }
  });

  it("the switch shows the three languages and marks the chosen one", () => {
    const out = renderToStaticMarkup(<LanguageSwitch lang="mr" onChange={() => undefined} />);
    expect(out.match(/<button/g)?.length).toBe(3);
    expect(out).toContain(`aria-pressed="true" class="`);
    expect(out.indexOf(FRONT_MR.langName)).toBeGreaterThan(out.indexOf('aria-pressed="true"'));
    expect(out).toContain("English");
  });

  it("the choice is kept in this browser and read back; blocked storage reads English", () => {
    const s = storage();
    vi.stubGlobal("window", { localStorage: s });
    storeFrontLang("hi");
    expect(s.data.get(FRONT_LANG_KEY)).toBe("hi");
    expect(readFrontLang()).toBe("hi");
    vi.stubGlobal("window", { localStorage: storage({ [FRONT_LANG_KEY]: "xx" }) });
    expect(readFrontLang()).toBe("en");
    vi.stubGlobal("window", { localStorage: storage({}, true) });
    expect(readFrontLang()).toBe("en");
    expect(() => storeFrontLang("mr")).not.toThrow();
  });

  it("a stored Marathi choice shows the same content in Marathi; the form and the footer sentence stay English", () => {
    vi.stubGlobal("window", { localStorage: storage({ [FRONT_LANG_KEY]: "mr" }) });
    const out = html();
    expect(out).toContain(FRONT_MR.headline);
    expect(out).toContain(FRONT_MR.trust1);
    expect(out).toContain(FRONT_MR.faqCostA);
    expect(out).toContain(FRONT_MR.footer);
    expect(out).toContain('lang="mr"');
    expect(out).toContain(">Sign in</h1>");
    expect(out).toContain(">Information and education only, not investment advice.</p>");
    expect(out).not.toContain(FRONT_EN.headline);
  });
});

describe("M95 F4: the intro video slot", () => {
  it("hidden while no video is set; a plain https link opening a new tab when set", () => {
    const off = renderToStaticMarkup(<FrontDoorDetails t={FRONT_EN} googleOn={false} brokers={null} videoUrl={null} />);
    expect(off).not.toContain("data-front-video");
    const on = renderToStaticMarkup(<FrontDoorDetails t={FRONT_EN} googleOn={false} brokers={null} videoUrl="https://videos.example.com/intro" />);
    expect(on).toMatch(/<a href="https:\/\/videos\.example\.com\/intro" target="_blank" rel="noopener noreferrer" data-front-video/);
    expect(on).toContain("Watch the 60-second intro");
    expect(safeHttps("javascript:alert(1)")).toBeNull();
    expect(safeHttps("http://videos.example.com/a")).toBeNull();
    expect(safeHttps(null)).toBeNull();
  });
});

describe("M95 §4: the words", () => {
  it("no trade-call words, no claims, no jargon in any front-door string", () => {
    for (const value of Object.values(FRONT_EN)) {
      expect(callWords(value), value).toEqual([]);
      expect(value).not.toMatch(/\b(profit|profits|accura\w*|guarantee\w*|win rate|returns?|testimonial|phase 0|paper only|engine|tenant|flag|information only)\b|%/i);
    }
    const shown = text(html());
    expect(callWords(shown)).toEqual([]);
  });
});
