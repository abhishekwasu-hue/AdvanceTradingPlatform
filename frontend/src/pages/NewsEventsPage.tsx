import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Card, CollapsibleCard } from "../components/ui";
import {
  NEWS_EVENT_CATEGORY_LABELS,
  defaultNewsEvent,
  type NewsEvent,
  type NewsEventCategory,
  type NewsEventResponse,
  type NewsFeedStatus,
  type NewsSentiment,
} from "../types";

/** Phase BB: feed items are unverified - a severity chip and the badge say so on every row. */
function severityClass(severity: number): string {
  if (severity >= 5) return "bg-danger/20 text-danger border-danger/50";
  if (severity >= 4) return "bg-amber-500/15 text-amber-200 border-amber-400/50";
  if (severity >= 3) return "bg-sky-500/10 text-sky-200 border-sky-400/40";
  return "bg-slate-700/30 text-muted border-border";
}

function FeedSourcesCard({ isAdmin }: { isAdmin: boolean }) {
  const [status, setStatus] = useState<NewsFeedStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = () => api.newsFeedStatus().then(setStatus).catch((e) => setError(String(e)));
  useEffect(() => { load(); }, []);
  if (!status) return null;
  const toggle = async (id: string, on: boolean) => {
    setBusy(true);
    try { await api.setNewsFeedSource(id, on); await load(); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  };
  const refresh = async () => {
    setBusy(true);
    try { await api.refreshNewsFeed(); await load(); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  };
  return (
    <CollapsibleCard title="Live feed sources" subtitle={status.enabled ? `on · fetch every ${Math.round(status.cadence_seconds / 60)} min` : "off (feature flag news_feed)"} storageKey="news-feed-sources">
      <p className="text-xs text-muted mb-2">{status.note}</p>
      {error && <div className="text-xs text-danger mb-2">{error}</div>}
      <div className="space-y-1.5">
        {status.sources.map((src) => (
          <div key={src.id} className="flex items-start gap-2 rounded border border-border px-2 py-1.5 text-xs">
            <span className={`mt-0.5 inline-block h-2 w-2 shrink-0 rounded-full ${src.on ? "bg-emerald-400" : "bg-slate-600"}`} />
            <div className="flex-1">
              <div className="font-semibold text-slate-200">{src.name} <span className="font-normal text-muted">· {src.publisher}{src.official ? "" : " · not a primary source"}</span></div>
              <div className="text-muted">{src.terms}</div>
            </div>
            {isAdmin && (
              <button disabled={busy} onClick={() => toggle(src.id, !src.on)} className="shrink-0 rounded border border-border px-2 py-0.5 text-slate-200 hover:bg-panel2 disabled:opacity-50">
                {src.on ? "Turn off" : "Turn on"}
              </button>
            )}
          </div>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-muted">
        {status.last_run.at && <span>last fetch {new Date(status.last_run.at).toLocaleString()} · {status.last_run.new ?? 0} new · {status.last_run.alerts ?? 0} alerts · {(status.last_run.errors ?? []).length} errors</span>}
        {isAdmin && status.enabled && <button disabled={busy} onClick={refresh} className="rounded border border-border px-2 py-0.5 text-slate-200 hover:bg-panel2 disabled:opacity-50">Fetch now</button>}
      </div>
    </CollapsibleCard>
  );
}

const CATEGORIES = Object.keys(NEWS_EVENT_CATEGORY_LABELS) as NewsEventCategory[];
const SENTIMENTS: NewsSentiment[] = ["Bullish", "Neutral", "Bearish"];

function sentimentClass(sentiment: NewsSentiment): string {
  if (sentiment === "Bullish") return "bg-accent/15 text-accent border-accent/40";
  if (sentiment === "Bearish") return "bg-danger/15 text-danger border-danger/40";
  return "bg-slate-700/30 text-muted border-border";
}

export default function NewsEventsPage() {
  const { user, loading: authLoading } = useAuth();
  const [events, setEvents] = useState<NewsEventResponse[]>([]);
  const [categoryFilter, setCategoryFilter] = useState<NewsEventCategory | "">("");
  const [symbolFilter, setSymbolFilter] = useState("");
  const [originFilter, setOriginFilter] = useState<"" | "MANUAL" | "FEED">("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<NewsEvent>(defaultNewsEvent());
  const [submitting, setSubmitting] = useState(false);

  function refresh() {
    setLoading(true);
    api
      .listNewsEvents({ category: categoryFilter || undefined, symbol: symbolFilter || undefined, origin: originFilter || undefined })
      .then(setEvents)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [categoryFilter, symbolFilter, originFilter]);

  async function handleSubmit() {
    setSubmitting(true);
    setError(null);
    try {
      await api.createNewsEvent(form);
      setForm(defaultNewsEvent());
      refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete(id: number) {
    await api.deleteNewsEvent(id);
    refresh();
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-extrabold text-pink-400">News & Events</h1>
        <p className="text-sm font-semibold text-pink-200">
          Structured, cited entries for RBI policy decisions, the Union Budget, government
          policy changes, corporate news, and other market-moving events. Entries a person adds must
          cite a source. Items marked <span className="rounded border border-amber-400/50 bg-amber-500/10 px-1 text-amber-200">unverified feed</span> come
          from a publisher's own public feed (RBI, SEBI, ...): headline and link only, never checked by the platform - read the source before acting.
        </p>
      </div>

      <FeedSourcesCard isAdmin={!!user && user.role === "SUPER_ADMIN"} />

      <Card title="Filter">
        <div className="grid sm:grid-cols-3 gap-3">
          <div>
            <label className="block text-xs text-muted mb-1">Origin</label>
            <select
              className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
              value={originFilter}
              onChange={(e) => setOriginFilter(e.target.value as "" | "MANUAL" | "FEED")}
            >
              <option value="">All</option>
              <option value="MANUAL">Cited by a person</option>
              <option value="FEED">Unverified feed</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">Category</label>
            <select
              className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
              value={categoryFilter}
              onChange={(e) => setCategoryFilter(e.target.value as NewsEventCategory | "")}
            >
              <option value="">All categories</option>
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>{NEWS_EVENT_CATEGORY_LABELS[c]}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">Symbol</label>
            <input
              className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
              placeholder="e.g. RELIANCE"
              value={symbolFilter}
              onChange={(e) => setSymbolFilter(e.target.value.toUpperCase())}
            />
          </div>
        </div>
      </Card>

      <Card title={`Events (${events.length})`}>
        {loading ? (
          <div className="text-sm text-muted py-2">Loading…</div>
        ) : events.length === 0 ? (
          <div className="text-sm text-muted py-2">No events match this filter.</div>
        ) : (
          <div className="space-y-2">
            {events.map((e) => (
              <div key={e.id} className="rounded border border-border px-3 py-2 text-sm">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <span className="rounded border border-border px-1.5 py-0.5 text-[11px] text-muted mr-2">
                      {NEWS_EVENT_CATEGORY_LABELS[e.category]}
                    </span>
                    <span className={`inline-block rounded border px-1.5 py-0.5 text-[11px] font-semibold ${sentimentClass(e.sentiment)}`}>
                      {e.sentiment}
                    </span>
                    {e.origin === "FEED" && (
                      <span className="ml-2 inline-block rounded border border-amber-400/50 bg-amber-500/10 px-1.5 py-0.5 text-[11px] text-amber-200" title="From a public feed; not verified by the platform">
                        unverified feed
                      </span>
                    )}
                    {e.classification && (
                      <span className={`ml-2 inline-block rounded border px-1.5 py-0.5 text-[11px] ${severityClass(e.classification.severity)}`} title={e.classification.one_line_en}>
                        {e.classification.type.toLowerCase().replace("_", " ")} · severity {e.classification.severity}{e.classification.method === "ai" ? " · AI" : ""}
                      </span>
                    )}
                    <div className="mt-1 font-medium text-slate-200">{e.headline}</div>
                    {e.description && <div className="mt-0.5 text-xs text-muted">{e.description}</div>}
                    {e.affected_symbols.length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {e.affected_symbols.map((s) => (
                          <span key={s} className="rounded border border-border px-1 py-0.5 text-[10px] text-muted">{s}</span>
                        ))}
                      </div>
                    )}
                    <div className="mt-1 text-[11px] text-muted">
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
                    <button onClick={() => handleDelete(e.id)} className="text-xs text-danger hover:underline shrink-0">
                      Delete
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {authLoading ? null : !user ? (
        <Card>
          <p className="text-sm text-muted">Log in from the Account tab to add a cited news event.</p>
        </Card>
      ) : (
        <Card title="Add a cited event">
          <div className="space-y-3">
            <div className="grid sm:grid-cols-3 gap-3">
              <div>
                <label className="block text-xs text-muted mb-1">Category</label>
                <select
                  className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
                  value={form.category}
                  onChange={(e) => setForm({ ...form, category: e.target.value as NewsEventCategory })}
                >
                  {CATEGORIES.map((c) => (
                    <option key={c} value={c}>{NEWS_EVENT_CATEGORY_LABELS[c]}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs text-muted mb-1">Event date</label>
                <input
                  type="date"
                  className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
                  value={form.event_date}
                  onChange={(e) => setForm({ ...form, event_date: e.target.value })}
                />
              </div>
              <div>
                <label className="block text-xs text-muted mb-1">Sentiment</label>
                <select
                  className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
                  value={form.sentiment}
                  onChange={(e) => setForm({ ...form, sentiment: e.target.value as NewsSentiment })}
                >
                  {SENTIMENTS.map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
              </div>
            </div>

            <div>
              <label className="block text-xs text-muted mb-1">Headline</label>
              <input
                className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
                value={form.headline}
                onChange={(e) => setForm({ ...form, headline: e.target.value })}
              />
            </div>

            <div>
              <label className="block text-xs text-muted mb-1">Description (optional)</label>
              <textarea
                className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
                rows={2}
                value={form.description ?? ""}
                onChange={(e) => setForm({ ...form, description: e.target.value || null })}
              />
            </div>

            <div>
              <label className="block text-xs text-muted mb-1">Affected symbols (comma-separated, blank = market-wide)</label>
              <input
                className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
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
                <label className="block text-xs text-muted mb-1">Source name</label>
                <input
                  className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
                  value={form.source.source}
                  onChange={(e) => setForm({ ...form, source: { ...form.source, source: e.target.value } })}
                />
              </div>
              <div>
                <label className="block text-xs text-muted mb-1">Source URL (optional)</label>
                <input
                  className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
                  value={form.source.source_url ?? ""}
                  onChange={(e) => setForm({ ...form, source: { ...form.source, source_url: e.target.value || null } })}
                />
              </div>
            </div>

            {error && <div className="text-sm text-danger">{error}</div>}

            <button
              onClick={handleSubmit}
              disabled={submitting || !form.headline || !form.source.source}
              className="rounded bg-brand hover:bg-brand-dim text-white font-semibold px-4 py-1.5 text-sm disabled:opacity-50"
            >
              {submitting ? "Saving…" : "Add event"}
            </button>
          </div>
        </Card>
      )}
    </div>
  );
}
