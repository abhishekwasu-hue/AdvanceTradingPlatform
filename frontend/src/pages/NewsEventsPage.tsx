import { errorSentence } from "../api/errors";
import { useEffect, useState } from "react";
import { useDebouncedValue } from "../utils/debounce";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Card, CollapsibleCard } from "../components/ui";
import { PageHeader, SkeletonRows } from "../components/primitives";
import {
  NEWS_EVENT_CATEGORY_LABELS,
  defaultNewsEvent,
  type NewsEvent,
  type NewsEventCategory,
  type NewsEventResponse,
  type NewsFeedStatus,
  type NewsSentiment,
  type NewsTrust,
  type NewsVerdict,
} from "../types";
import { formatDateTimeIST, formatNumber } from "../ui/format";
import { stripMarkdown, stripMarkdownLine } from "../ui/stripMarkdown";
import { SelectOrCombobox } from "../ui/Combobox";

/** Phase BB: feed items are unverified - a severity chip and the badge say so on every row. */
function severityClass(severity: number): string {
  if (severity >= 5) return "bg-down/20 text-down border-down/50";
  if (severity >= 4) return "bg-warn/15 text-warn border-warn/50";
  if (severity >= 3) return "bg-info/10 text-info border-info/40";
  return "bg-surface-2 text-fg-muted border-border";
}

function FeedSourcesCard({ isAdmin }: { isAdmin: boolean }) {
  const [status, setStatus] = useState<NewsFeedStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = () => api.newsFeedStatus().then(setStatus).catch((e) => setError(errorSentence(e)));
  useEffect(() => { load(); }, []);
  if (!status) return null;
  const toggle = async (id: string, on: boolean) => {
    setBusy(true);
    try { await api.setNewsFeedSource(id, on); await load(); } catch (e) { setError(errorSentence(e)); } finally { setBusy(false); }
  };
  const refresh = async () => {
    setBusy(true);
    try { await api.refreshNewsFeed(); await load(); } catch (e) { setError(errorSentence(e)); } finally { setBusy(false); }
  };
  return (
    <CollapsibleCard title="Live feed sources" subtitle={status.enabled ? `on · fetch every ${Math.round(status.cadence_seconds / 60)} min` : "off"} storageKey="news-feed-sources">
      <p className="text-xs text-fg-muted mb-2">{status.note}</p>
      {error && <div className="text-xs text-down mb-2">{error}</div>}
      <div className="space-y-1.5">
        {status.sources.map((src) => (
          <div key={src.id} className="flex items-start gap-2 rounded border border-border px-2 py-1.5 text-xs">
            <span className={`mt-0.5 inline-block h-2 w-2 shrink-0 rounded-full ${src.on ? "bg-up" : "bg-fg-muted/40"}`} title={src.on ? "on" : "off"}><span className="sr-only">{src.on ? "on" : "off"}</span></span>
            <div className="flex-1">
              <div className="font-semibold text-fg">{src.name} <span className="font-normal text-fg-muted">· {src.publisher}{src.official ? "" : " · not a primary source"}</span></div>
              <div className="text-fg-muted">{src.terms}</div>
            </div>
            {isAdmin && (
              <button disabled={busy} onClick={() => toggle(src.id, !src.on)} className="shrink-0 rounded border border-border px-2 py-0.5 text-fg hover:bg-surface-2 disabled:opacity-50">
                {src.on ? "Turn off" : "Turn on"}
              </button>
            )}
          </div>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-fg-muted">
        {status.last_run.at && <span>last fetch {formatDateTimeIST(status.last_run.at)} · {status.last_run.new ?? 0} new · {status.last_run.alerts ?? 0} alerts · {(status.last_run.errors ?? []).length} errors</span>}
        {isAdmin && status.enabled && <button disabled={busy} onClick={refresh} className="rounded border border-border px-2 py-0.5 text-fg hover:bg-surface-2 disabled:opacity-50">Fetch now</button>}
      </div>
    </CollapsibleCard>
  );
}

const CATEGORIES = Object.keys(NEWS_EVENT_CATEGORY_LABELS) as NewsEventCategory[];
const SENTIMENTS: NewsSentiment[] = ["Bullish", "Neutral", "Bearish"];
/** The native select class of the page's dropdowns. */
const FIELD_CLASS = "w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm";

function sentimentClass(sentiment: NewsSentiment): string {
  if (sentiment === "Bullish") return "bg-up/15 text-up border-up/40";
  if (sentiment === "Bearish") return "bg-down/15 text-down border-down/40";
  return "bg-surface-2/30 text-fg-muted border-border";
}

/** REV-1 M3: the Symbol box reads the list this long after the last key (no read per keystroke). */
const NEWS_SEARCH_DEBOUNCE_MS = 400;

/** M63 I-7: an event's one-line description (a feed summary or a typed note) as plain words, never raw markdown. */
export function EventSummary({ text }: { text: string }) {
  return <div className="mt-0.5 text-xs text-fg-muted">{stripMarkdown(text)}</div>;
}

export default function NewsEventsPage() {
  const { user, loading: authLoading } = useAuth();
  const [events, setEvents] = useState<NewsEventResponse[]>([]);
  const [categoryFilter, setCategoryFilter] = useState<NewsEventCategory | "">("");
  const [symbolFilter, setSymbolFilter] = useState("");
  const symbolQuery = useDebouncedValue(symbolFilter.trim(), NEWS_SEARCH_DEBOUNCE_MS);   // REV-1 M3: one read when typing stops
  const [originFilter, setOriginFilter] = useState<"" | "MANUAL" | "FEED">("");
  const [loading, setLoading] = useState(true);                // M63 I-7: the list is read on open - placeholder rows first
  const [error, setError] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [form, setForm] = useState<NewsEvent>(defaultNewsEvent());
  const [submitting, setSubmitting] = useState(false);
  // Phase BD-2: this member's verdicts on feed items (useful / noise / wrong direction) and the organisation's news trust.
  const [verdicts, setVerdicts] = useState<Record<string, NewsVerdict>>({});
  const [trust, setTrust] = useState<NewsTrust | null>(null);

  const [feedOn, setFeedOn] = useState<boolean | null>(null);
  useEffect(() => {
    if (!user) return;
    api.newsFeedStatus().then((st) => {
      setFeedOn(st.enabled);
      if (st.enabled) api.newsFeedbackSummary().then((r) => setTrust(r.trust)).catch(() => setTrust(null));
    }).catch(() => setFeedOn(false));
  }, [user]);
  useEffect(() => {
    const feedIds = events.filter((e) => e.origin === "FEED").map((e) => e.id);
    if (!feedIds.length || !user || !feedOn) { setVerdicts({}); return; }
    api.newsFeedbackMine(feedIds).then((r) => setVerdicts(r.verdicts)).catch(() => setVerdicts({}));
  }, [events, user, feedOn]);

  async function vote(id: number, verdict: NewsVerdict) {
    try {
      const r = await api.newsFeedback(id, verdict);
      setVerdicts((v) => ({ ...v, [String(id)]: r.verdict }));
      setTrust(r.trust);
    } catch (e) { setError(errorSentence(e)); }
  }

  function refresh() {
    setLoading(true);
    api
      .listNewsEvents({ category: categoryFilter || undefined, symbol: symbolQuery || undefined, origin: originFilter || undefined })
      .then(setEvents)
      .catch((e) => setError(errorSentence(e)))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [categoryFilter, symbolQuery, originFilter]);

  async function handleSubmit() {
    setSubmitting(true);
    setError(null);
    try {
      await api.createNewsEvent(form);
      setForm(defaultNewsEvent());
      refresh();
    } catch (e) {
      setError(errorSentence(e));
    } finally {
      setSubmitting(false);
    }
  }

  // REV-1 M2: a failed delete says so next to the list (never an unhandled rejection with no message).
  async function handleDelete(id: number) {
    setDeleteError(null);
    try {
      await api.deleteNewsEvent(id);
      refresh();
    } catch (e) {
      setDeleteError(`The event could not be deleted: ${errorSentence(e)}`);
    }
  }

  return (
    <div className="space-y-4">
      <PageHeader title={<>News & Events</>} description={<>Structured, cited entries for RBI policy decisions, the Union Budget, government policy changes, corporate news, and other market-moving events. Entries a person adds must cite a source.{trust && <span className="ml-1 text-fg-muted">News trust: <b>{formatNumber(trust.trust, 2)}</b> ({trust.ratings} verdict{trust.ratings === 1 ? "" : "s"}{trust.applied ? ", applied to the thesis" : `, ${trust.note}`}).</span>} Items marked <span className="rounded border border-warn/50 bg-warn/10 px-1 text-warn">unverified feed</span> come from a publisher's own public feed (RBI, SEBI, ...): headline and link only, never checked by the platform - read the source before acting.</>} />

      <FeedSourcesCard isAdmin={!!user && user.role === "SUPER_ADMIN"} />

      <Card title="Filter">
        <div className="grid sm:grid-cols-3 gap-3">
          <div>
            <label className="block text-xs text-fg-muted mb-1">Origin</label>
            <SelectOrCombobox label="Origin" value={originFilter} onChange={(v) => setOriginFilter(v as "" | "MANUAL" | "FEED")} className="w-full" nativeClassName={FIELD_CLASS}
              options={[{ value: "", label: "All" }, { value: "MANUAL", label: "Cited by a person" }, { value: "FEED", label: "Unverified feed" }]} />
          </div>
          <div>
            <label className="block text-xs text-fg-muted mb-1">Category</label>
            <SelectOrCombobox label="Category" value={categoryFilter} onChange={(v) => setCategoryFilter(v as NewsEventCategory | "")} className="w-full" nativeClassName={FIELD_CLASS}
              options={[{ value: "", label: "All categories" }, ...CATEGORIES.map((c) => ({ value: c, label: NEWS_EVENT_CATEGORY_LABELS[c] }))]} />
          </div>
          <div>
            <label className="block text-xs text-fg-muted mb-1">Symbol</label>
            <input
              className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm"
              placeholder="e.g. RELIANCE"
              value={symbolFilter}
              onChange={(e) => setSymbolFilter(e.target.value.toUpperCase())}
            />
          </div>
        </div>
      </Card>

      <Card title={`Events (${events.length})`}>
        {deleteError && <div role="alert" className="mb-2 text-sm text-down" data-news-delete-error>{deleteError}</div>}
        {loading ? (
          <SkeletonRows rows={2} label="Loading" />
        ) : events.length === 0 ? (
          <div className="text-sm text-fg-muted py-2">No events match this filter.</div>
        ) : (
          <div className="space-y-2">
            {events.map((e) => (
              <div key={e.id} className="rounded border border-border px-3 py-2 text-sm">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <span className="rounded border border-border px-1.5 py-0.5 text-xs text-fg-muted mr-2">
                      {NEWS_EVENT_CATEGORY_LABELS[e.category]}
                    </span>
                    <span className={`inline-block rounded border px-1.5 py-0.5 text-xs font-semibold ${sentimentClass(e.sentiment)}`}>
                      {e.sentiment}
                    </span>
                    {e.origin === "FEED" && (
                      <span className="ml-2 inline-block rounded border border-warn/50 bg-warn/10 px-1.5 py-0.5 text-xs text-warn" title="From a public feed; not verified by the platform">
                        unverified feed
                      </span>
                    )}
                    {e.classification && (
                      <span className={`ml-2 inline-block rounded border px-1.5 py-0.5 text-xs ${severityClass(e.classification.severity)}`} title={stripMarkdownLine(e.classification.one_line_en)}>
                        {e.classification.type.toLowerCase().replace("_", " ")} · severity {e.classification.severity}{e.classification.method === "ai" ? " · AI" : ""}
                      </span>
                    )}
                    <div className="mt-1 font-medium text-fg">{e.headline}</div>
                    {e.description && <EventSummary text={e.description} />}
                    {e.affected_symbols.length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {e.affected_symbols.map((s) => (
                          <span key={s} className="rounded border border-border px-1 py-0.5 text-xs text-fg-muted">{s}</span>
                        ))}
                      </div>
                    )}
                    <div className="mt-1 text-xs text-fg-muted">
                      {e.event_date} · Source:{" "}
                      {e.source.source_url ? (
                        <a href={e.source.source_url} target="_blank" rel="noreferrer" className="text-brand hover:underline">
                          {e.source.source}
                        </a>
                      ) : (
                        e.source.source
                      )}
                    </div>
                  </div>
                  {user && user.id === e.created_by && (
                    <button onClick={() => handleDelete(e.id)} className="text-xs text-down hover:underline shrink-0">
                      Delete
                    </button>
                  )}
                  {user && feedOn && e.origin === "FEED" && (
                    <div className="flex shrink-0 flex-col items-end gap-1 text-xs" title="Your verdict teaches the thesis how much to trust the feed (organisation-wide, after 10 verdicts)">
                      {(["useful", "noise", "wrong_direction"] as NewsVerdict[]).map((v) => (
                        <button key={v} onClick={() => void vote(e.id, v)} aria-pressed={verdicts[String(e.id)] === v}
                                className={`rounded border px-1.5 py-0.5 ${verdicts[String(e.id)] === v ? "border-brand bg-brand/15 text-fg" : "border-border text-fg-muted hover:text-fg"}`}>
                          {v === "useful" ? "👍 useful" : v === "noise" ? "👎 noise" : "↔ wrong direction"}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {authLoading ? null : !user ? (
        <Card>
          <p className="text-sm text-fg-muted">Log in from the Account tab to add a cited news event.</p>
        </Card>
      ) : (
        <Card title="Add a cited event">
          <div className="space-y-3">
            <div className="grid sm:grid-cols-3 gap-3">
              <div>
                <label className="block text-xs text-fg-muted mb-1">Category</label>
                <SelectOrCombobox label="Category" value={form.category} onChange={(v) => setForm({ ...form, category: v as NewsEventCategory })} className="w-full" nativeClassName={FIELD_CLASS}
                  options={CATEGORIES.map((c) => ({ value: c, label: NEWS_EVENT_CATEGORY_LABELS[c] }))} />
              </div>
              <div>
                <label className="block text-xs text-fg-muted mb-1">Event date</label>
                <input
                  type="date"
                  className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm"
                  value={form.event_date}
                  onChange={(e) => setForm({ ...form, event_date: e.target.value })}
                />
              </div>
              <div>
                <label className="block text-xs text-fg-muted mb-1">Sentiment</label>
                <SelectOrCombobox label="Sentiment" value={form.sentiment} onChange={(v) => setForm({ ...form, sentiment: v as NewsSentiment })} className="w-full" nativeClassName={FIELD_CLASS}
                  options={SENTIMENTS.map((s) => ({ value: s, label: s }))} />
              </div>
            </div>

            <div>
              <label className="block text-xs text-fg-muted mb-1">Headline</label>
              <input
                className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm"
                value={form.headline}
                onChange={(e) => setForm({ ...form, headline: e.target.value })}
              />
            </div>

            <div>
              <label className="block text-xs text-fg-muted mb-1">Description (optional)</label>
              <textarea
                className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm"
                rows={2}
                value={form.description ?? ""}
                onChange={(e) => setForm({ ...form, description: e.target.value || null })}
              />
            </div>

            <div>
              <label className="block text-xs text-fg-muted mb-1">Affected symbols (comma-separated, blank = market-wide)</label>
              <input
                className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm"
                value={form.affected_symbols.join(", ")}
                onChange={(e) =>
                  setForm({
                    ...form,
                    affected_symbols: e.target.value.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean),
                  })
                }
              />
            </div>

            <div className="grid sm:grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-fg-muted mb-1">Source name</label>
                <input
                  className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm"
                  value={form.source.source}
                  onChange={(e) => setForm({ ...form, source: { ...form.source, source: e.target.value } })}
                />
              </div>
              <div>
                <label className="block text-xs text-fg-muted mb-1">Source URL (optional)</label>
                <input
                  className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm"
                  value={form.source.source_url ?? ""}
                  onChange={(e) => setForm({ ...form, source: { ...form.source, source_url: e.target.value || null } })}
                />
              </div>
            </div>

            {error && <div className="text-sm text-down">{error}</div>}

            <button
              onClick={handleSubmit}
              disabled={submitting || !form.headline || !form.source.source}
              className="rounded bg-brand hover:bg-brand-strong text-on-brand font-semibold px-4 py-1.5 text-sm disabled:opacity-50"
            >
              {submitting ? "Saving…" : "Add event"}
            </button>
          </div>
        </Card>
      )}
    </div>
  );
}
