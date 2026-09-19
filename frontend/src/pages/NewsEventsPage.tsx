import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Card } from "../components/ui";
import {
  NEWS_EVENT_CATEGORY_LABELS,
  defaultNewsEvent,
  type NewsEvent,
  type NewsEventCategory,
  type NewsEventResponse,
  type NewsSentiment,
} from "../types";

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
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<NewsEvent>(defaultNewsEvent());
  const [submitting, setSubmitting] = useState(false);

  function refresh() {
    setLoading(true);
    api
      .listNewsEvents({ category: categoryFilter || undefined, symbol: symbolFilter || undefined })
      .then(setEvents)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [categoryFilter, symbolFilter]);

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
        <h1 className="text-xl font-semibold text-slate-100">News & Events</h1>
        <p className="text-sm text-muted">
          Structured, cited entries for RBI policy decisions, the Union Budget, government
          policy changes, corporate news, and other market-moving events. There is no live news
          feed - every entry is user-entered and must cite a source, exactly like the rest of
          the platform's fundamental data.
        </p>
      </div>

      <Card title="Filter">
        <div className="grid sm:grid-cols-2 gap-3">
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
