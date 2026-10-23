import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import fixture from "../../e2e/fixtures/site_v2.json";
import { callWords } from "../signals/language";
import appSource from "../App.tsx?raw";
import signInSource from "../auth/SignInScreen.tsx?raw";
import { SiteView } from "./SitePage";
import { SITE_SLUGS, isSiteSlug, resetSitePages, seoFor, sitePages, siteSlugFromPath, type SitePageData } from "./site";
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
    expect(out).toContain(">Sign in<");
    expect(out).toContain('href="/legal/privacy"');
    expect(out).toContain('aria-current="page"');
    expect(callWords(readable(out))).toEqual([]);
    expect(readable(out)).not.toMatch(/win rate|accuracy|guarantee|₹|nifty|sensex|\b\d{3,}\b/i);
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
