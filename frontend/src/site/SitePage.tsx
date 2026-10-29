import { ArrowRight } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router";
import { ThemeToggle } from "../components/AppearanceCard";
import { LogoMark } from "../components/Logo";
import { LEGAL_SLUGS, LEGAL_TITLES, legalPath } from "../legal/legal";
import { SITE_LABELS as NAV_LABEL, SITE_SLUGS, applySeo, sitePages, sitePath, siteSlugFromPath, type SitePageData } from "./site";
import { Skeleton } from "../components/primitives";

const FOCUS = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";

/**
 * Readiness C13: a public marketing page at `/features`, `/how-it-works` or `/about` - no sign-in, no app shell, like
 * the legal pages. The text comes from the server (guarded: no performance claims, no security names, no prices); while
 * the operator has `marketing_site` off the page says it does not exist. Each page sets its own title, description and
 * canonical address (site.ts applySeo); index.html carries the defaults and public/sitemap.xml lists the pages.
 */
export default function SitePage() {
  const { pathname } = useLocation();
  const slug = siteSlugFromPath(pathname);
  const [pages, setPages] = useState<SitePageData[] | null | undefined>(undefined);
  useEffect(() => {
    let live = true;
    sitePages().then((p) => { if (live) setPages(p); });
    return () => { live = false; };
  }, []);
  const page = pages && slug ? pages.find((p) => p.slug === slug) ?? null : null;
  useEffect(() => {
    if (page) applySeo(page.title, page.description, sitePath(page.slug));
    window.scrollTo(0, 0);
  }, [page]);
  return <SiteView page={page} loading={pages === undefined} current={slug} />;
}

/** The page once read (`page` null = not published). A prop-only view, so the render tests can show every state. */
export function SiteView({ page, loading = false, current }: { page: SitePageData | null; loading?: boolean; current: string | null }) {
  return (
    <div className="flex min-h-screen flex-col bg-surface text-fg" data-screen="site" data-site-page={page?.slug ?? undefined}>
      <header className="border-b border-subtle bg-surface-1 pt-[env(safe-area-inset-top)]">
        <div className="mx-auto flex h-14 max-w-5xl items-center justify-between gap-space-3 px-space-4">
          <Link to="/" className={`flex min-h-11 items-center gap-space-2 rounded-control text-t15 font-bold text-fg ${FOCUS}`}>
            <LogoMark size={24} />AiAlgoDesk
          </Link>
          <div className="flex items-center gap-space-2">
            <ThemeToggle />
            <Link to="/" className={`inline-flex h-11 items-center rounded-control bg-brand px-space-4 text-t14 font-semibold text-on-brand hover:bg-brand-strong md:h-9 ${FOCUS}`}>Sign in</Link>
          </div>
        </div>
        <nav aria-label="About AiAlgoDesk" className="mx-auto flex max-w-5xl gap-space-1 overflow-x-auto px-space-2">
          {SITE_SLUGS.map((s) => (
            <Link key={s} to={sitePath(s)} aria-current={s === current ? "page" : undefined}
              className={`-mb-px flex h-11 shrink-0 items-center border-b-2 px-space-3 text-t14 font-medium ${s === current ? "border-brand text-fg" : "border-transparent text-fg-muted hover:text-fg"} ${FOCUS}`}>
              {NAV_LABEL[s]}
            </Link>
          ))}
        </nav>
      </header>
      <main className="mx-auto w-full max-w-5xl flex-1 px-space-4 py-space-8 pb-[calc(2rem+env(safe-area-inset-bottom))]">
        {loading ? (
          <div aria-busy="true" className="space-y-space-3"><span className="sr-only">Loading</span>
            <Skeleton className="h-8 w-64" /><Skeleton className="h-40" panel />
          </div>
        ) : !page ? (
          <div role="alert" className="rounded-panel border border-subtle bg-surface-1 p-space-6 text-t14">
            <p className="font-semibold text-fg">This page does not exist.</p>
            <Link to="/" className="mt-space-3 inline-block font-medium text-brand hover:underline">Go to the app</Link>
          </div>
        ) : (
          <article aria-labelledby="site-title">
            <h1 id="site-title" className="text-t24 font-semibold tracking-tight text-fg md:text-t32">{page.title}</h1>
            <p className="mt-space-3 max-w-2xl text-t15 text-fg-muted">{page.intro}</p>
            <div className="mt-space-6 grid grid-cols-1 gap-space-4 md:grid-cols-3" data-block="site-sections">
              {page.sections.map((section, i) => (
                <section key={section.heading} aria-label={section.heading} className="rounded-panel border border-subtle bg-surface-1 p-space-5 shadow-card">
                  {page.slug === "how-it-works" && (
                    <span className="mb-space-2 inline-flex h-7 w-7 items-center justify-center rounded-pill bg-brand/10 text-t14 font-semibold text-brand">{i + 1}</span>
                  )}
                  <h2 className="text-t15 font-semibold text-fg">{section.heading}</h2>
                  <ul className="mt-space-2 space-y-space-2 text-t14 text-fg-muted">{section.lines.map((l) => <li key={l}>{l}</li>)}</ul>
                </section>
              ))}
            </div>
            <div className="mt-space-8 flex flex-col items-start gap-space-3 rounded-panel border border-subtle bg-surface-1 p-space-5 md:flex-row md:items-center md:justify-between">
              <p className="text-t15 font-semibold text-fg">Practise your own ideas on paper first.</p>
              <Link to="/" className={`inline-flex h-11 items-center gap-space-2 rounded-control bg-brand px-space-4 text-t14 font-semibold text-on-brand hover:bg-brand-strong md:h-9 ${FOCUS}`}>
                Get started<ArrowRight size={16} aria-hidden />
              </Link>
            </div>
          </article>
        )}
      </main>
      <footer className="border-t border-subtle px-space-4 py-space-5 text-t12 text-fg-muted">
        <div className="mx-auto flex max-w-5xl flex-col gap-space-2 md:flex-row md:items-center md:justify-between">
          <p>Information and education, not investment advice.</p>
          <nav aria-label="Legal pages" className="flex flex-wrap gap-space-3">
            {LEGAL_SLUGS.map((s) => <Link key={s} to={legalPath(s)} className={`inline-flex min-h-11 items-center rounded-control hover:text-fg md:min-h-0 ${FOCUS}`}>{LEGAL_TITLES[s]}</Link>)}
          </nav>
        </div>
      </footer>
    </div>
  );
}

