import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { ApiError, api } from "../api/client";
import { ThemeToggle } from "../components/AppearanceCard";
import { LEGAL_SLUGS, LEGAL_TITLES, isLegalSlug, legalPath, type LegalDocument } from "./legal";
import { Markdown } from "./markdown";

/** Z10-f: a legal page at /legal/<slug> - public (no sign-in, no app shell), so the sign-up form can link to it.
 * The text is the backend's versioned content; the version and its date are shown with it. */
export function LegalView({ doc }: { doc: LegalDocument }) {
  return (
    <article aria-label={doc.title} className="text-sm leading-relaxed text-fg">
      <p className="text-xs text-fg-muted">
        Version <span className="font-mono">{doc.version}</span>{doc.draft ? " · draft" : ""}
      </p>
      <Markdown source={doc.body} />
    </article>
  );
}

export function LegalNav({ current }: { current: string | undefined }) {
  return (
    <nav aria-label="Legal pages" className="flex flex-wrap gap-2 text-xs">
      {LEGAL_SLUGS.map((s) => (
        <Link key={s} to={legalPath(s)} aria-current={s === current ? "page" : undefined}
              className={`rounded-full border px-3 py-1 ${s === current ? "border-brand text-brand" : "border-border text-fg-muted hover:text-fg"}`}>
          {LEGAL_TITLES[s]}
        </Link>
      ))}
    </nav>
  );
}

export default function LegalPage() {
  const { slug } = useParams<{ slug: string }>();
  const [doc, setDoc] = useState<LegalDocument | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDoc(null);
    setError(null);
    if (!isLegalSlug(slug)) { setError("This page does not exist."); return; }
    document.title = `${LEGAL_TITLES[slug]} · Advance Trading`;
    let live = true;
    api.legalDocument(slug)
      .then((d) => { if (live) setDoc(d); })
      // 404: the operator has not published the legal pages (flag `legal_pages` off) - the same message as an unknown page.
      .catch((e) => { if (live) setError(e instanceof ApiError && e.status === 404 ? "This page does not exist." : String(e).replace(/^Error:\s*/, "")); });
    window.scrollTo(0, 0);
    return () => { live = false; };
  }, [slug]);

  return (
    <div className="min-h-screen text-fg">
      <header className="border-b border-border bg-panel/80 pt-[env(safe-area-inset-top)]">
        <div className="mx-auto flex h-14 max-w-3xl items-center justify-between gap-3 px-4">
          <Link to="/" className="text-sm font-semibold text-fg hover:text-brand">Advance Trading</Link>
          <ThemeToggle />
        </div>
      </header>
      <main className="mx-auto max-w-3xl space-y-4 px-4 py-6 pb-[calc(2rem+env(safe-area-inset-bottom))]">
        <LegalNav current={slug} />
        {error ? (
          <div role="alert" className="rounded-xl border border-border bg-panel p-6 text-sm">
            <p className="font-semibold text-fg">{error}</p>
            <Link to="/" className="mt-3 inline-block text-brand hover:underline">Go to the app</Link>
          </div>
        ) : doc ? (
          <div className="rounded-xl border border-border bg-panel p-5 md:p-8"><LegalView doc={doc} /></div>
        ) : (
          <div aria-busy="true" aria-live="polite" className="space-y-3">
            <span className="sr-only">Loading</span>
            <div className="h-6 w-48 animate-pulse rounded bg-panel2" />
            <div className="h-64 animate-pulse rounded-xl bg-panel2/70" />
          </div>
        )}
      </main>
    </div>
  );
}
