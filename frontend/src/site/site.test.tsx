import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import fixture from "../../e2e/fixtures/site_v2.json";
import { callWords } from "../signals/language";
import appSource from "../App.tsx?raw";
import signInSource from "../auth/SignInScreen.tsx?raw";
import { SiteView } from "./SitePage";
import { SITE_FOOTER, SITE_SLUGS, frontImagePath, isSiteSlug, resetSitePages, seoFor, sitePages, siteSlugFromPath, type SitePageData } from "./site";
import siteSource from "./SitePage.tsx?raw";
import seoSource from "./site.ts?raw";

/** Readiness C13: the public marketing pages (server payload in e2e/fixtures/site_v2.json, pinned by the backend
 * test), their SEO tags, the guard on what they say and the routes outside the sign-in gate. */

const PAGES = (fixture as { pages: SitePageData[] }).pages;
const html = (page: SitePageData | null, current: string | null = page?.slug ?? null, loading = false) =>
  renderToStaticMarkup(<MemoryRouter><SiteView page={page} current={current} loading={loading} /></MemoryRouter>);
const readable = (s: string) => s.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");

afterEach(() => { resetSitePages(); vi.unstubAllGlobals(); });

describe("the pages", () => {
  it("are the three slugs the server publishes", () => {
    expect(PAGES.map((p) => p.slug)).toEqual([...SITE_SLUGS]);
    expect(siteSlugFromPath("/how-it-works/")).toBe("how-it-works");
    expect(siteSlugFromPath("/pricing")).toBeNull();
    expect(isSiteSlug("about")).toBe(true);
  });

  it.each(PAGES.map((p) => [p.slug, p] as const))("%s shows its sections, a sign-in button and the legal links", (_slug, page) => {
    const out = html(page);
    for (const s of page.sections) { expect(readable(out)).toContain(s.heading); for (const l of s.lines) expect(readable(out)).toContain(l); }
    for (const q of page.faq ?? []) { expect(readable(out)).toContain(q.question); expect(readable(out)).toContain(q.answer); }
    expect(out).toContain(">Sign in<");
    expect(out).toContain('href="/legal/privacy"');
    expect(out).toContain('aria-current="page"');
    expect(callWords(readable(out))).toEqual([]);
    expect(readable(out)).not.toMatch(/win rate|accuracy|guarantee|₹|nifty|sensex|\b\d{3,}\b/i);
  });

  it("M95 F7: the six benefits each have a lazy picture of a fixed size, the same file the front door shows", () => {
    const features = PAGES.find((p) => p.slug === "features")!;
    expect(features.sections.map((s) => s.heading)).toEqual([
      "AI Copilot", "Option chain with OI", "Find setups", "AI Trading Bot on paper", "Journal", "Daily report on Telegram"]);
    const out = html(features);
    for (const s of features.sections) {
      expect(s.image).toBeTruthy();
      expect(out).toContain(`src="${frontImagePath(s.image!)}"`);
    }
    expect(frontImagePath("copilot")).toBe("/front/copilot.png");
    const imgs = out.match(/<img [^>]*>/g) ?? [];
    expect(imgs).toHaveLength(6);
    for (const img of imgs) { expect(img).toContain('loading="lazy"'); expect(img).toContain('width="640"'); expect(img).toContain('height="400"'); }
    // The box keeps its size while the picture loads or when the file is missing (onError drops the img).
    expect(out.match(/data-site-image="[a-z-]+"[^>]*/g)).toHaveLength(6);
    expect(siteSource).toContain("onError={() => setMissing(true)}");
  });

  it("M95 F7: three steps, the trust lines and the FAQ in folds", () => {
    const how = PAGES.find((p) => p.slug === "how-it-works")!;
    expect(how.sections.map((s) => s.heading)).toEqual(["Continue with Google", "Connect your broker if you want live prices", "Practise on paper"]);
    const about = PAGES.find((p) => p.slug === "about")!;
    const out = html(about);
    for (const line of ["No order is placed without you", "Your broker data stays yours", "Paper by default"]) expect(readable(out)).toContain(line);
    expect(about.faq?.map((q) => q.question)).toEqual(["What does it cost?", "Which brokers can I connect?", "Is my money safe?", "What is paper trading?"]);
    expect(about.faq?.[0].answer).toBe("Free during the beta.");
    expect(out.match(/<details/g)).toHaveLength(4);
    expect(out).not.toMatch(/<details[^>]*\bopen\b/);
    for (const q of about.faq!) { expect(readable(out)).toContain(q.question); expect(readable(out)).toContain(q.answer); }
  });

  it("M95: every page ends with the footer sentence", () => {
    expect(SITE_FOOTER).toBe("Information and education only, not investment advice.");
    for (const page of [...PAGES, null]) expect(readable(html(page, "features"))).toContain(SITE_FOOTER);
  });

  it("an unpublished page says it does not exist", () => {
    expect(readable(html(null, "features"))).toContain("This page does not exist.");
  });

  it("each page names its own title and canonical address", () => {
    expect(seoFor("How it works", "/how-it-works")).toEqual({ title: "How it works · AiAlgoDesk", url: "https://aialgodesk.tech/how-it-works" });
    expect(siteSource).toContain("applySeo(page.title, page.description, sitePath(page.slug))");
    for (const tag of ['meta[name="description"]', 'property="og:title"', 'property="og:url"', 'link[rel="canonical"]', 'name="twitter:title"']) {
      expect(seoSource).toContain(tag);
    }
  });

  it("one shared read; a 404 (flag off) means no pages", async () => {
    const fetcher = vi.fn(async () => new Response("{}", { status: 404 }));
    vi.stubGlobal("fetch", fetcher);
    expect(await sitePages()).toBeNull();
    expect(await sitePages()).toBeNull();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});

describe("wiring", () => {
  it("the routes sit outside the sign-in gate, and the sign-in footer links them", () => {
    expect(appSource).toMatch(/SITE_SLUGS\.map\(\(slug\) => \(\s*<Route key=\{slug\} path=\{sitePath\(slug\)\}/);
    expect(appSource.indexOf("SITE_SLUGS.map")).toBeLessThan(appSource.indexOf('<Route path="*" element={<SignInGate>'));
    expect(signInSource).toContain("<StatusLink /><SiteLinks />");
  });
});
