/**
 * Readiness C13: the public marketing pages (Features, How it works, About) and the SEO tags each page sets as it
 * opens. The text is the backend's (`GET /api/site/pages`, guarded content; 404 while `marketing_site` is off).
 */
import { request } from "../api/client";

export const SITE_SLUGS = ["features", "how-it-works", "about"] as const;
export type SiteSlug = (typeof SITE_SLUGS)[number];
export const SITE_LABELS: Record<SiteSlug, string> = { features: "Features", "how-it-works": "How it works", about: "About" };
export const SITE_ORIGIN = "https://aialgodesk.tech";
export const SITE_NAME = "AiAlgoDesk";

/** M95 F7: `image` is the slug of the section's picture (`/front/<slug>.png`, the same file the front door shows). */
export interface SiteSection { heading: string; lines: string[]; image?: string | null }
export interface SiteQuestion { question: string; answer: string }
export interface SitePageData { slug: SiteSlug; title: string; description: string; intro: string; sections: SiteSection[]; faq?: SiteQuestion[] }

/** M95 F6 / F7: the footer sentence of the public pages (the M94 wording). */
export const SITE_FOOTER = "Information and education only, not investment advice.";

/** M95 F7: the address of one front-door picture. Served from public/front/ - the front door reads the same files. */
export function frontImagePath(slug: string): string {
  return `/front/${slug}.png`;
}

export function isSiteSlug(value: string | undefined): value is SiteSlug {
  return !!value && (SITE_SLUGS as readonly string[]).includes(value);
}

export function sitePath(slug: SiteSlug): string {
  return `/${slug}`;
}

/** The slug a public address names (`/how-it-works` -> "how-it-works"), or null. */
export function siteSlugFromPath(pathname: string): SiteSlug | null {
  const slug = pathname.replace(/^\/+|\/+$/g, "");
  return isSiteSlug(slug) ? slug : null;
}

let pages: Promise<SitePageData[] | null> | null = null;

/** One read per page load, shared by the page and the sign-in footer; null while the operator has the pages off. */
export function sitePages(): Promise<SitePageData[] | null> {
  pages ??= request<{ pages: SitePageData[] }>("/site/pages").then((r) => r.pages, () => null);
  return pages;
}

/** Tests: forget the shared read. */
export function resetSitePages(): void {
  pages = null;
}

function setMeta(selector: string, attr: "name" | "property", key: string, value: string): void {
  let el = document.head.querySelector<HTMLMetaElement>(selector);
  if (!el) {
    el = document.createElement("meta");
    el.setAttribute(attr, key);
    document.head.appendChild(el);
  }
  el.setAttribute("content", value);
}

/** The tags one page sets: its full title and its absolute address. */
export function seoFor(title: string, path: string): { title: string; url: string } {
  return { title: `${title} · ${SITE_NAME}`, url: `${SITE_ORIGIN}${path}` };
}

/** The page's title, description, canonical address and the Open Graph / Twitter copies of them. */
export function applySeo(title: string, description: string, path: string): void {
  const { title: full, url } = seoFor(title, path);
  document.title = full;
  setMeta('meta[name="description"]', "name", "description", description);
  setMeta('meta[property="og:title"]', "property", "og:title", full);
  setMeta('meta[property="og:description"]', "property", "og:description", description);
  setMeta('meta[property="og:url"]', "property", "og:url", url);
  setMeta('meta[name="twitter:title"]', "name", "twitter:title", full);
  setMeta('meta[name="twitter:description"]', "name", "twitter:description", description);
  let canonical = document.head.querySelector<HTMLLinkElement>('link[rel="canonical"]');
  if (!canonical) {
    canonical = document.createElement("link");
    canonical.rel = "canonical";
    document.head.appendChild(canonical);
  }
  canonical.href = url;
}
