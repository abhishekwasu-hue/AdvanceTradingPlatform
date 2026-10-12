import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { PageHeader } from "../components/primitives";
import { pathFor } from "../routes";
import { useLearnT, useProgressionT } from "../progression/i18n";
import { CONCEPTS, CONCEPT_LINKS, GLOSSARY, OFFICIAL_LINKS, conceptFromHash, isOfficial, type Concept } from "../progression/learn";

/**
 * N2-10 (P1 spec 1.6, flag `education_pages`): the method library - trend, level, pullback, commitment, reward to
 * risk - each linked to where ATP draws it, the screens that look for it, the checklist item that records it and the
 * journal tags that show it skipped; a glossary; official NSE / SEBI / CBDT links only. Education only: no advice, no
 * performance claims (checked by learn.test.ts).
 */
export default function LearnPage() {
  const tp = useProgressionT();
  const { user, loading } = useAuth();
  const [enabled, setEnabled] = useState<boolean | null>(null);
  useEffect(() => {
    if (!user) return;
    api.myFeatures().then((f) => setEnabled(Boolean(f.education_pages))).catch(() => setEnabled(false));
  }, [user]);
  if (loading || (user && enabled === null)) return <div className="p-4 text-sm text-fg-muted">…</div>;
  if (!user || !enabled) return <div className="p-4 text-sm text-fg-muted" data-testid="learn-off">{tp("learnOff")}</div>;
  return <LearnContent initial={conceptFromHash(typeof window !== "undefined" ? window.location.hash : "")} />;
}

export function LearnContent({ initial = "trend" as Concept }: { initial?: Concept }) {
  const t = useLearnT();
  const [active, setActive] = useState<Concept>(initial);
  const c = CONCEPT_LINKS[active];
  return (
    <div className="space-y-4" data-testid="learn">
      <PageHeader title={t("title")} description={t("intro")} />
      <p className="rounded border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-fg" role="note">{t("risk")}</p>
      <nav aria-label={t("steps")} className="flex flex-wrap gap-2">
        {CONCEPTS.map((k) => (
          <a key={k} href={`#learn-${k}`} aria-current={k === active ? "page" : undefined} onClick={() => setActive(k)}
             className={`min-h-[44px] rounded border px-3 py-2 text-sm sm:min-h-0 ${k === active ? "border-info text-info" : "border-border text-fg"}`}>
            {t(`concepts.${k}.title`)}
          </a>
        ))}
      </nav>
      <article id={`learn-${active}`} className="space-y-3 rounded-xl border border-border bg-surface-1 p-4 text-sm" data-testid={`concept-${active}`}>
        <h2 className="text-base font-semibold text-fg">{t(`concepts.${active}.title`)}</h2>
        <p className="font-medium text-fg">{t(`concepts.${active}.summary`)}</p>
        {(["p1", "p2", "p3"] as const).map((p) => <p key={p} className="text-fg">{t(`concepts.${active}.body.${p}`)}</p>)}
        <dl className="space-y-2 text-xs">
          <div><dt className="font-semibold text-fg-muted">{t("onChart")}</dt><dd className="text-fg">{t(`concepts.${active}.onChart`)}</dd></div>
          {c.setups.length > 0 && (
            <div>
              <dt className="font-semibold text-fg-muted">{t("screens")}</dt>
              <dd className="flex flex-wrap items-center gap-2 text-fg">
                {c.setups.map((s) => <span key={s} className="rounded bg-surface-2 px-2 py-0.5">{s.replace(/_/g, " ")}</span>)}
                <a href={pathFor("screener")} className="text-info underline">{t("openScreener")}</a>
              </dd>
            </div>
          )}
          <div><dt className="font-semibold text-fg-muted">{t("checklist")}</dt><dd className="text-fg">{t(`concepts.${active}.checklist`)}</dd></div>
          <div>
            <dt className="font-semibold text-fg-muted">{t("mistakes")}</dt>
            <dd><ul className="list-disc pl-4 text-fg">{c.tags.map((tag) => <li key={tag}>{t(`tags.${tag}`)}</li>)}</ul></dd>
          </div>
        </dl>
      </article>
      <section aria-label={t("glossary")} className="space-y-2">
        <h2 className="text-base font-semibold text-fg">{t("glossary")}</h2>
        <dl className="grid gap-2 text-xs sm:grid-cols-2">
          {GLOSSARY.map((g) => (
            <div key={g} id={`term-${g}`} className="rounded border border-border p-2">
              <dt className="font-semibold text-fg">{t(`terms.${g}.term`)}</dt>
              <dd className="text-fg-muted">{t(`terms.${g}.def`)}</dd>
            </div>
          ))}
        </dl>
      </section>
      <section aria-label={t("officialLinks")} className="text-xs">
        <h2 className="mb-1 font-semibold text-fg">{t("officialLinks")}</h2>
        <ul className="space-y-1">
          {OFFICIAL_LINKS.filter((l) => isOfficial(l.href)).map((l) => (
            <li key={l.key}><a href={l.href} target="_blank" rel="noopener noreferrer" className="text-info underline">{t(`links.${l.key}`)}</a></li>
          ))}
        </ul>
      </section>
    </div>
  );
}
