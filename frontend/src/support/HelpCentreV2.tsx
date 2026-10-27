import { ChevronRight, Mail, MessageCircle, Search, TriangleAlert } from "lucide-react";
import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { Link, useLocation } from "react-router";
import { CARD } from "../dashboard/CardV2";
import { useFeatures } from "../ui/useFeatures";
import { PAGE_TOURS, WELCOME } from "../tours/content";
import { deskApi, safeEmail, safeWhatsapp, type SupportContact } from "./desk";
import { KIND_LABEL, glossaryArticles, howToArticles, searchHelp, tourArticles, type HelpArticle, type HelpKind, type ServerHelpArticle } from "./helpCentre";

const ReportProblemDialogV2 = lazy(() => import("./ReportProblemDialogV2"));

const FOCUS = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";
const CHIP = `inline-flex min-h-11 items-center rounded-pill border px-space-3 text-t13 font-medium md:min-h-9 ${FOCUS}`;
const ACTION = `inline-flex min-h-11 items-center justify-center gap-space-2 rounded-control border border-border bg-surface-1 px-space-3 text-t14 font-medium text-fg hover:bg-surface-2 md:min-h-9 ${FOCUS}`;
const FILTERS: ("all" | HelpKind)[] = ["all", "how-to", "page-tip", "word"];
const SHOWN = 30;

/** The weekday and the time in India, for "next on ..." outside market hours. */
function opensAt(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString("en-IN", { weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
}

/** The WhatsApp / e-mail link while the market is open (the owner sets them in the admin console; none by default). */
export function SupportContactV2({ contact }: { contact: SupportContact | null }) {
  if (!contact || !contact.configured) return null;
  const whatsapp = safeWhatsapp(contact.whatsapp_url);
  const email = safeEmail(contact.email);
  if (!contact.market_open || (!whatsapp && !email)) {
    const when = opensAt(contact.next_open);
    return (
      <p className="text-t13 text-fg-muted" data-support-contact="closed">
        Chat and e-mail support is open while the market is open{when ? ` - next on ${when}` : ""}. You can report a problem at any time.
      </p>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-space-2" data-support-contact="open">
      <span className="text-t13 text-fg-muted">Talk to us now:</span>
      {whatsapp && (
        <a href={whatsapp} target="_blank" rel="noopener noreferrer" className={ACTION}>
          <MessageCircle size={16} aria-hidden /> WhatsApp
        </a>
      )}
      {email && (
        <a href={`mailto:${email}`} className={ACTION}>
          <Mail size={16} aria-hidden /> E-mail
        </a>
      )}
    </div>
  );
}

function ArticleItem({ article }: { article: HelpArticle }) {
  return (
    <li data-help-article={article.id}>
      <details className="group/help">
        <summary className={`flex min-h-12 cursor-pointer list-none items-center gap-space-3 rounded-control px-space-2 py-space-2 hover:bg-surface-2 ${FOCUS} [&::-webkit-details-marker]:hidden`}>
          <span className="flex min-w-0 flex-1 flex-col">
            <span className="text-t14 font-medium text-fg">{article.title}</span>
            <span className="text-t12 text-fg-muted">{KIND_LABEL[article.kind]}</span>
          </span>
          <ChevronRight size={16} aria-hidden className="shrink-0 text-fg-muted transition-transform group-open/help:rotate-90" />
        </summary>
        <div className="space-y-space-2 px-space-2 pb-space-3">
          {article.text.split("\n").filter(Boolean).map((line, i) => (
            <p key={i} className="whitespace-pre-wrap text-t14 leading-relaxed text-fg">{line}</p>
          ))}
          {article.page && (
            <Link to={article.page} className={`inline-flex min-h-11 items-center gap-space-1 text-t14 font-medium text-brand hover:underline md:min-h-0 ${FOCUS}`}>
              Open the page <ChevronRight size={14} aria-hidden />
            </Link>
          )}
        </div>
      </details>
    </li>
  );
}

/**
 * C12: the Help centre (Settings › Help & tips, flag `help_centre`). One search over the how-to pages, the page tips
 * and the words of the app; "Report a problem"; the support contact while the market is open.
 */
export default function HelpCentreV2() {
  const location = useLocation();
  const [server, setServer] = useState<ServerHelpArticle[]>([]);
  const [contact, setContact] = useState<SupportContact | null>(null);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<"all" | HelpKind>("all");
  const [more, setMore] = useState(false);
  const [reportOpen, setReportOpen] = useState(location.hash === "#report");
  const [reportLoaded, setReportLoaded] = useState(location.hash === "#report");

  useEffect(() => {
    let live = true;
    deskApi.help().then((r) => { if (live) setServer(r.articles ?? []); }, () => { /* the page tips and words still show */ });
    deskApi.contact().then((c) => { if (live) setContact(c); }, () => { /* no contact line */ });
    return () => { live = false; };
  }, []);
  useEffect(() => { if (location.hash === "#report") { setReportLoaded(true); setReportOpen(true); } }, [location.hash, location.key]);

  const features = useFeatures();
  const terminalOn = features.on("terminal");
  const all = useMemo(() => [...howToArticles(server), ...tourArticles(PAGE_TOURS, WELCOME, (f) => f !== "terminal" || terminalOn), ...glossaryArticles()],
    [server, terminalOn]);
  const found = useMemo(() => searchHelp(all, query).filter((a) => kind === "all" || a.kind === kind), [all, query, kind]);
  const shown = more ? found : found.slice(0, SHOWN);

  return (
    <section id="help-centre" aria-label="Help centre" className={`${CARD} scroll-mt-20 p-space-4 md:p-space-5`} data-help-centre>
      <div className="flex flex-col gap-space-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h2 className="text-t15 font-semibold text-fg">Help centre</h2>
          <p className="mt-space-1 text-t13 text-fg-muted">How to use each page, and what the words mean.</p>
        </div>
        <button type="button" className={`${ACTION} self-start`} onClick={() => { setReportLoaded(true); setReportOpen(true); }} data-report-open>
          <TriangleAlert size={16} aria-hidden /> Report a problem
        </button>
      </div>
      <div className="mt-space-3"><SupportContactV2 contact={contact} /></div>

      <div className="relative mt-space-4">
        <Search size={16} aria-hidden className="pointer-events-none absolute left-space-3 top-1/2 -translate-y-1/2 text-fg-muted" />
        <label htmlFor="help-search" className="sr-only">Search the help</label>
        <input id="help-search" type="search" value={query} onChange={(e) => { setQuery(e.target.value); setMore(false); }} autoComplete="off"
          placeholder="Search: connect broker, paper trade, lot…"
          className={`h-11 w-full rounded-control border border-border bg-surface-2 pl-9 pr-space-3 text-t14 text-fg ${FOCUS}`} />
      </div>
      <div className="mt-space-3 flex flex-wrap gap-space-2" role="group" aria-label="Show">
        {FILTERS.map((f) => (
          <button key={f} type="button" aria-pressed={kind === f} onClick={() => setKind(f)}
            className={`${CHIP} ${kind === f ? "border-brand bg-brand text-on-brand" : "border-border bg-surface-1 text-fg hover:bg-surface-2"}`}>
            {f === "all" ? "All" : KIND_LABEL[f]}
          </button>
        ))}
      </div>

      <p className="mt-space-3 text-t12 text-fg-muted" aria-live="polite" data-help-count={found.length}>
        {found.length === 0 ? "Nothing matches. Try other words, or report a problem." : `${found.length} ${found.length === 1 ? "answer" : "answers"}`}
      </p>
      <ul className="mt-space-1 divide-y divide-border">
        {shown.map((a) => <ArticleItem key={a.id} article={a} />)}
      </ul>
      {!more && found.length > SHOWN && (
        <button type="button" className={`${ACTION} mt-space-3`} onClick={() => setMore(true)}>Show all {found.length}</button>
      )}

      {reportLoaded && (
        <Suspense fallback={null}>
          <ReportProblemDialogV2 open={reportOpen} onOpenChange={setReportOpen} pathname={location.pathname} />
        </Suspense>
      )}
    </section>
  );
}
