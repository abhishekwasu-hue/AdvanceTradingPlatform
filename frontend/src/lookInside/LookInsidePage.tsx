import { ArrowRight } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import { ThemeToggle } from "../components/AppearanceCard";
import { LogoMark } from "../components/Logo";
import { Skeleton } from "../components/primitives";
import { LEGAL_SLUGS, LEGAL_TITLES, legalPath } from "../legal/legal";
import { applySeo } from "../site/site";
import { formatNumber } from "../ui/format";
import { SubTabs } from "../ui/SubTabs";
import { ChainBars, GuestChartSvg } from "./GuestChart";
import {
  FOOTER_LINE, LOOK_INSIDE_PATH, dayText, guestSnapshot, pctText, signUpLabel, type GuestIndex, type GuestSnapshot,
} from "./lookInside";

const FOCUS = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";

/**
 * M95 PR-5 (owner order M95 F3): `/look-inside` - a visitor sees the app on one past market day without an account:
 * Today, a Daily chart with its areas, the option chain's open interest, a screen run and the daily report, then a
 * sticky "Create your free account" bar ("Ask for an invite" while sign-up is closed). Read-only; outside the sign-in
 * gate like the legal pages. While the operator has it off (or nothing is stored) the page says it is not open.
 */
export default function LookInsidePage() {
  const [snap, setSnap] = useState<GuestSnapshot | null | undefined>(undefined);
  const [signUpOpen, setSignUpOpen] = useState(false);
  useEffect(() => {
    let live = true;
    guestSnapshot().then((s) => { if (live) setSnap(s); });
    api.signUpOpen().then((r) => { if (live) setSignUpOpen(r.open === true); }).catch(() => { if (live) setSignUpOpen(false); });
    applySeo("Look inside", "See AiAlgoDesk on a past market day: the chart with its areas, open interest, a screen and the daily report.",
      LOOK_INSIDE_PATH);
    window.scrollTo(0, 0);
    return () => { live = false; };
  }, []);
  return <LookInsideView snap={snap} signUpOpen={signUpOpen} />;
}

/** The page once read (`snap` undefined = loading, null = not open). Prop-only, so the tests can show every state. */
export function LookInsideView({ snap, signUpOpen }: { snap: GuestSnapshot | null | undefined; signUpOpen: boolean }) {
  const [pick, setPick] = useState(0);
  const index = snap?.indices[pick] ?? snap?.indices[0] ?? null;
  return (
    <div className="flex min-h-screen flex-col bg-surface text-fg" data-screen="look-inside">
      <header className="border-b border-subtle bg-surface-1 pt-[env(safe-area-inset-top)]">
        <div className="mx-auto flex h-14 max-w-5xl items-center justify-between gap-space-3 px-space-4">
          <Link to="/" className={`flex min-h-11 items-center gap-space-2 rounded-control text-t15 font-bold text-fg ${FOCUS}`}>
            <LogoMark size={24} />AiAlgoDesk
          </Link>
          <div className="flex items-center gap-space-2">
            <ThemeToggle />
            <Link to="/" className={`inline-flex h-11 items-center rounded-control border border-subtle px-space-4 text-t14 font-semibold text-fg hover:bg-surface-2 md:h-9 ${FOCUS}`}>Sign in</Link>
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-5xl flex-1 px-space-4 py-space-6 pb-[calc(6rem+env(safe-area-inset-bottom))]">
        {snap === undefined ? (
          <div aria-busy="true" className="space-y-space-3"><span className="sr-only">Loading</span>
            <Skeleton className="h-8 w-56" /><Skeleton className="h-48" panel /><Skeleton className="h-40" panel />
          </div>
        ) : snap === null || !index ? (
          <div role="alert" className="rounded-panel border border-subtle bg-surface-1 p-space-6 text-t14">
            <p className="font-semibold text-fg">This page is not open yet.</p>
            <Link to="/" className="mt-space-3 inline-block font-medium text-brand hover:underline">Go to the sign-in page</Link>
          </div>
        ) : (
          <article aria-labelledby="look-title" className="space-y-space-4">
            <div>
              <h1 id="look-title" className="text-t24 font-semibold tracking-tight text-fg">Look inside</h1>
              <p className="mt-space-1 text-t14 text-fg-muted">The app on a past market day. Read-only; prices are hidden, distances are in %.</p>
              <p className="mt-space-2 inline-flex rounded-pill bg-surface-2 px-space-3 py-space-1 text-t13 font-medium text-fg" data-as-of>
                As of {dayText(snap.as_of)}
              </p>
            </div>
            {snap.indices.length > 1 && (
              <SubTabs kind="toggle" label="Index" value={String(snap.indices.indexOf(index))}
                items={snap.indices.map((ix, i) => ({ key: String(i), label: ix.name }))}
                onChange={(key) => setPick(Number(key))} className="max-w-sm" />
            )}
            <TodayCard index={index} asOf={snap.as_of} />
            <Card title="Chart with areas" sub={`Daily · ${dayText(index.chart.first_date)} to ${dayText(index.chart.last_date)}`} id="chart">
              <GuestChartSvg bars={index.chart.bars} areas={index.chart.areas} name={index.name} />
              {index.chart.areas.length > 0 && (
                <ul className="mt-space-3 space-y-space-1 text-t13 text-fg-muted">
                  {index.chart.areas.map((a, i) => (
                    <li key={i} className="flex items-start justify-between gap-space-3">
                      <span>{a.words}</span>
                      <span className={`shrink-0 tabular-nums font-medium ${a.role === "demand" ? "text-up" : "text-down"}`}>
                        {pctText(a.role === "demand" ? a.high_pct : a.low_pct)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
            <Card title="Option chain at that close" sub={index.chain?.expiry ? `Nearest expiry ${dayText(index.chain.expiry)} · strikes from the money` : undefined} id="chain">
              {index.chain && index.chain.rows.length ? (
                <>
                  <ChainBars rows={index.chain.rows} />
                  {index.chain.pcr !== null && (
                    <p className="mt-space-3 text-t13 text-fg-muted">Put-call ratio by open interest: <span className="font-semibold text-fg tabular-nums">{formatNumber(index.chain.pcr, 2)}</span></p>
                  )}
                </>
              ) : (
                <p className="text-t14 text-fg-muted">The option chain of this day is not stored for {index.name}.</p>
              )}
            </Card>
            <Card title="A screen run" sub="Ready-made screens on that day's daily bars" id="screens">
              <ul className="divide-y divide-subtle">
                {snap.screens.map((s) => (
                  <li key={s.name} className="py-space-2 first:pt-0 last:pb-0">
                    <p className="text-t14 font-semibold text-fg">{s.name}</p>
                    <p className="text-t12 text-fg-muted">{s.definition}</p>
                    <div className="mt-space-1 flex flex-wrap gap-space-2">
                      {s.results.map((r) => (
                        <span key={r.name} title={r.note ?? undefined}
                          className={`rounded-pill px-space-2 py-px text-t12 font-medium ${r.matched ? "bg-up-tint text-up" : "bg-surface-2 text-fg-muted"}`}>
                          {r.name}: {r.matched ? "matched" : "no match"}
                        </span>
                      ))}
                    </div>
                  </li>
                ))}
              </ul>
            </Card>
            <Card title="Daily report" sub={`${index.name} · ${dayText(snap.as_of)}`} id="report">
              <ul className="list-disc space-y-space-1 pl-space-5 text-t14 text-fg">
                {index.report.map((line) => <li key={line}>{line}</li>)}
              </ul>
            </Card>
          </article>
        )}
      </main>
      <footer className="border-t border-subtle px-space-4 py-space-5 pb-[calc(5.5rem+env(safe-area-inset-bottom))] text-t12 text-fg-muted">
        <div className="mx-auto flex max-w-5xl flex-col gap-space-2 md:flex-row md:items-center md:justify-between">
          <p data-footer-line>{FOOTER_LINE}</p>
          <nav aria-label="Legal pages" className="flex flex-wrap gap-space-3">
            {LEGAL_SLUGS.map((s) => <Link key={s} to={legalPath(s)} className={`inline-flex min-h-11 items-center rounded-control hover:text-fg md:min-h-0 ${FOCUS}`}>{LEGAL_TITLES[s]}</Link>)}
          </nav>
        </div>
      </footer>
      <div className="fixed inset-x-0 bottom-0 z-20 border-t border-subtle bg-surface-1/95 px-space-4 pt-space-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))] backdrop-blur"
        data-sign-up-bar>
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-space-3">
          <p className="min-w-0 text-t13 text-fg-muted">Practise on paper with your own ideas.</p>
          <Link to="/" className={`inline-flex h-11 shrink-0 items-center gap-space-2 rounded-control bg-brand px-space-4 text-t14 font-semibold text-on-brand hover:bg-brand-strong ${FOCUS}`}>
            {signUpLabel(signUpOpen)}<ArrowRight size={16} aria-hidden />
          </Link>
        </div>
      </div>
    </div>
  );
}

function Card({ title, sub, id, children }: { title: string; sub?: string; id: string; children: ReactNode }) {
  return (
    <section aria-label={title} data-card={id} className="rounded-panel border border-subtle bg-surface-1 p-space-4 shadow-card md:p-space-5">
      <h2 className="text-t15 font-semibold text-fg">{title}</h2>
      {sub && <p className="text-t12 text-fg-muted">{sub}</p>}
      <div className="mt-space-3">{children}</div>
    </section>
  );
}

function TodayCard({ index, asOf }: { index: GuestIndex; asOf: string }) {
  const t = index.today;
  const tone = t.change_pct === null ? "text-fg" : t.change_pct > 0 ? "text-up" : t.change_pct < 0 ? "text-down" : "text-fg";
  return (
    <Card title="Today" sub={`${index.name} · ${dayText(asOf)}`} id="today">
      <div className="flex flex-wrap items-end gap-x-space-6 gap-y-space-2">
        <div>
          <p className="text-t12 text-fg-muted">Change on the day</p>
          <p className={`text-t24 font-semibold tabular-nums ${tone}`} data-change>{pctText(t.change_pct)}</p>
        </div>
        <div>
          <p className="text-t12 text-fg-muted">Daily structure</p>
          <p className={`text-t15 font-semibold ${t.direction_daily.startsWith("up") ? "text-up" : t.direction_daily.startsWith("down") ? "text-down" : "text-fg"}`}>{t.direction_daily}</p>
        </div>
        <div>
          <p className="text-t12 text-fg-muted">Weekly structure</p>
          <p className={`text-t15 font-semibold ${t.direction_weekly.startsWith("up") ? "text-up" : t.direction_weekly.startsWith("down") ? "text-down" : "text-fg"}`}>{t.direction_weekly}</p>
        </div>
      </div>
      {t.range_position_pct !== null && (
        <div className="mt-space-3" aria-label={`The close sat ${formatNumber(t.range_position_pct, 0)}% of the way up its recent range`}>
          <div className="flex justify-between text-t12 text-fg-muted"><span>Recent low</span><span>Recent high</span></div>
          <div className="relative mt-space-1 h-2 rounded-pill bg-surface-2">
            <span className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-pill bg-brand"
              style={{ left: `${Math.min(100, Math.max(0, t.range_position_pct))}%` }} />
          </div>
        </div>
      )}
      <details className="mt-space-3 text-t13 text-fg-muted">
        <summary className={`cursor-pointer rounded-control font-medium text-fg ${FOCUS}`}>The reading in words</summary>
        <ul className="mt-space-2 list-disc space-y-space-1 pl-space-5">{t.lines.map((l) => <li key={l}>{l}</li>)}</ul>
      </details>
    </Card>
  );
}
