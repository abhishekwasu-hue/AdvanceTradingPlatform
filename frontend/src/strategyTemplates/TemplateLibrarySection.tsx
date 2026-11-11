import { useEffect, useState } from "react";
import { useInRouterContext, useLocation, useNavigate } from "react-router";
import { errorSentence } from "../api/errors";
import { LINK_PRIMARY, LINK_SECONDARY } from "../dashboard/CardV2";
import { InPlaceLink } from "../inplace/InPlaceLink";
import { Skeleton } from "../components/primitives";
import { CARD } from "../dashboard/CardV2";
import { Chip, FilterChip } from "../ui/Chip";
import { formatNumber } from "../ui/format";
import { LibraryFold, SettingList } from "../ui/LibraryChips";
import type { SettingRow } from "../ui/strategyLabels";
import { useFeatures } from "../ui/useFeatures";
import { REFERENCE_LABEL, showsReferenceLabel, templatesApi, type StrategyTemplate } from "./api";
import { HowItWorksSheet } from "./HowItWorks";
import { HOW_PARAM, howPathFor, paperBotPathFor, templateIdOf, testPathFor } from "./link";

/**
 * P2-3: the strategy templates under Strategies > Library (flag `strategy_templates`). Every sentence about a template
 * comes from the template's own data (the server's card: idea, the sentence a signal reads, settings, rules). The two
 * links open the trading bot form (PAPER) or Strategies > Test with the template picked; nothing starts from here.
 */
const ALL = "all";

/** Loads the cards; null while loading; the section is hidden by its caller while the flag is off. */
export default function TemplateLibrarySection() {
  const [templates, setTemplates] = useState<StrategyTemplate[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [family, setFamily] = useState<string>(ALL);
  const features = useFeatures();
  const intradayPackOn = features.on("intraday_pack");
  const sdPackOn = features.on("sd_pack");                       // M97 PR-3: family I
  const routed = useInRouterContext();

  useEffect(() => {
    templatesApi.list().then((r) => setTemplates(r.templates)).catch((e) => { setTemplates([]); setError(errorSentence(e)); });
  }, []);

  return (
    <>
      <TemplateLibraryView templates={templates} error={error} family={family} onFamily={setFamily} intradayPackOn={intradayPackOn}
                           sdPackOn={sdPackOn} />
      {(intradayPackOn || sdPackOn) && routed && <HowItWorksFromAddress />}
    </>
  );
}

/** M94 F15: `?how=H6` opens that template's "how it works" screen over the library; closing it leaves the address. */
function HowItWorksFromAddress() {
  const location = useLocation();
  const navigate = useNavigate();
  const id = templateIdOf(new URLSearchParams(location.search).get(HOW_PARAM));
  const close = () => {
    const q = new URLSearchParams(location.search);
    q.delete(HOW_PARAM);
    navigate({ search: q.toString() ? `?${q.toString()}` : "" }, { replace: true });
  };
  return <HowItWorksSheet id={id} onClose={close} />;
}

/** M94 F15: a template that has a "how it works" screen (family H, while `intraday_pack` is on; M97 PR-3: family I, while
 * `sd_pack` is on). */
export const hasHowItWorks = (t: Pick<StrategyTemplate, "family">, intradayPackOn: boolean, sdPackOn = false): boolean =>
  (intradayPackOn && t.family === "H") || (sdPackOn && t.family === "I");

/** M94: the reference-values label (behind `intraday_pack`): the template's defaults come from a study; nothing is enforced. */
export function ReferenceLabel() {
  return <Chip name="template-reference" tone="warn" variant="meaning">{REFERENCE_LABEL}</Chip>;
}

const valueWords = (v: number | string | boolean, choices?: StrategyTemplate["params"][number]["choices"]): string => {
  const choice = choices?.find((c) => c.value === String(v));
  if (choice) return choice.label;
  if (typeof v === "boolean") return v ? "Yes" : "No";
  return typeof v === "number" ? formatNumber(v, Number.isInteger(v) ? 0 : 2, { trim: true }) : String(v);
};

/** A template's default settings as label / value rows. */
export function templateRows(t: Pick<StrategyTemplate, "params">): SettingRow[] {
  return t.params.map((p) => ({ key: p.key, label: p.label, value: valueWords(p.default, p.choices) }));
}

export function TemplateLibraryView({ templates, error, family, onFamily, intradayPackOn = false, sdPackOn = false }: {
  templates: StrategyTemplate[] | null; error: string | null; family: string; onFamily: (f: string) => void;
  /** M94: the reference-values label shows only while the flag `intraday_pack` is on. */
  intradayPackOn?: boolean;
  /** M97 PR-3: the same for family I (Supply & Demand) while the flag `sd_pack` is on. */
  sdPackOn?: boolean;
}) {
  const all = templates ?? [];
  const families = [...new Map(all.map((t) => [t.family, t.family_name])).entries()];
  const shown = all.filter((t) => family === ALL || t.family === family);
  return (
    <section data-templates-section aria-labelledby="templates-heading" className="space-y-space-3 border-t border-subtle pt-space-4">
      <h2 id="templates-heading" className="text-t20 font-semibold text-fg">Templates</h2>
      {error && <p className="text-t14 text-down">{error}</p>}
      {families.length > 1 && (
        <div role="toolbar" aria-label="Template family" className="-mx-space-1 flex flex-nowrap gap-space-2 overflow-x-auto px-space-1 pb-space-1 [scrollbar-width:none]">
          <FilterChip selected={family === ALL} onClick={() => onFamily(ALL)}>All</FilterChip>
          {families.map(([id, name]) => (
            <FilterChip key={id} selected={family === id} onClick={() => onFamily(id)}>{name}</FilterChip>
          ))}
        </div>
      )}
      <div className="grid gap-space-3 lg:grid-cols-2" aria-busy={templates === null}>
        {templates === null && <Skeleton className="h-40" panel label="Loading templates" />}
        {shown.map((t) => <TemplateCard key={t.id} t={t} reference={showsReferenceLabel(t, intradayPackOn, sdPackOn)}
                                               how={hasHowItWorks(t, intradayPackOn, sdPackOn)} />)}
      </div>
    </section>
  );
}

function TemplateCard({ t, reference, how }: { t: StrategyTemplate; reference: boolean; how: boolean }) {
  const link = { template: t.id, timeframe: t.default_timeframe, params: {} };
  const reads = [t.reads?.long, t.reads?.short].filter((s): s is string => !!s);
  const rows = templateRows(t);
  const timeframes = t.timeframes.map((tf) => tf.label).join(", ");
  return (
    <article data-template-card={t.id} className={`${CARD} min-w-0 p-space-4`}>
      <div className="flex items-start justify-between gap-space-2">
        <h3 className="min-w-0 break-words text-t15 font-semibold text-fg">{t.name}</h3>
        <Chip name="template-family" className="mt-0.5">{t.family_name}</Chip>
      </div>
      {reference && <div data-template-reference className="mt-space-1"><ReferenceLabel /></div>}
      <p className="mt-space-1 text-t14 text-fg-muted">{t.idea}</p>
      {reads.length > 0 && (
        <div data-template-reads className="mt-space-2">
          <div className="text-t13 font-semibold text-fg-muted">A signal reads:</div>
          <ul className="mt-space-1 list-disc space-y-space-1 pl-space-4 text-t14 text-fg">
            {reads.map((r) => <li key={r}>{r}</li>)}
          </ul>
        </div>
      )}
      {timeframes && <p data-template-timeframes className="mt-space-2 text-t13 text-fg-muted">{timeframes}</p>}
      {how && (
        <InPlaceLink to={howPathFor(t.id)} data-how-link={t.id} aria-label={`How it works: ${t.name}`}
          className="mt-space-2 inline-flex min-h-11 items-center rounded-control text-t14 font-semibold text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-8">
          How it works
        </InPlaceLink>
      )}
      {t.available ? (
        <div className="mt-space-3 flex flex-wrap items-center gap-space-2">
          <InPlaceLink to={paperBotPathFor(link)} className={LINK_PRIMARY} data-use-template={t.id} aria-label={`Use on the trading bot: ${t.name}`}>Use on the trading bot</InPlaceLink>
          <InPlaceLink to={testPathFor(link)} className={LINK_SECONDARY} data-test-template={t.id} aria-label={`Test it first: ${t.name}`}>Test it first</InPlaceLink>
        </div>
      ) : (
        <p data-template-off className="mt-space-3 text-t13 text-fg-muted">Not switched on for your account yet</p>
      )}
      {(rows.length > 0 || t.rules.length > 0) && (
        <LibraryFold title="Settings and rules" name="template-details">
          {rows.length > 0 && <SettingList rows={rows} label="Settings" />}
          {t.rules.length > 0 && <ul className="list-disc space-y-space-1 pl-space-4 text-t14 text-fg-muted">{t.rules.map((r) => <li key={r}>{r}</li>)}</ul>}
        </LibraryFold>
      )}
    </article>
  );
}
