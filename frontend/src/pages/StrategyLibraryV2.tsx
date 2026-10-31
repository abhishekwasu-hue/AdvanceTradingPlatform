import { Check } from "lucide-react";
import { Suspense, lazy, useEffect, useState, type ReactNode } from "react";
import { api } from "../api/client";
import { errorSentence } from "../api/errors";
import { Skeleton } from "../components/primitives";
import { CARD, LINK_PRIMARY } from "../dashboard/CardV2";
import type { StrategyInfo } from "../types";
import { Chip, FilterChip } from "../ui/Chip";
import { LibraryFold, LibraryMainChips, LibraryMoreSettings, SettingList } from "../ui/LibraryChips";
import { PageFootnote } from "../ui/PageFootnote";
import { PageHeader } from "../ui/PageHeader";
import { CATEGORY_LABEL, categoryLabel, firstSentence, libraryCardSettings } from "../ui/strategyLabels";

// UI2-3 (UI v2 §3 M6): the signal generator lives in each strategy's drawer ("Try it on a symbol"), its own chunk.
const TryOnSymbolV2 = lazy(() => import("../signals/TryOnSymbolV2"));

/**
 * M49 U3 (owner-approved mockup docs/ui/U3_LIBRARY.html), behind `ui_v2`: the Strategy Library. Each card is the name,
 * one plain line and at most three settings; the rest - and the built-in's suggested stop / targets (STRAT-X) - sit under
 * "More settings"; a hard word has its (?). The category is the shared Chip and a filter; a filter with no strategy shows
 * one short line with "Show all". On a wide screen (lg) the list is on the left and the chosen strategy's full settings
 * on the right. Strategies "mark" structure: signals only, from the trader's own settings.
 */

const ALL = "all";
/** The engine's categories, always offered (a category with no strategy shows the empty line). */
const CATEGORIES = Object.keys(CATEGORY_LABEL);

const TRY_BUTTON = "inline-flex min-h-11 items-center rounded-control px-space-1 text-t14 font-semibold text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9";

function Stat({ value, label, className = "" }: { value: number; label: string; className?: string }) {
  return (
    <div className={`min-w-0 flex-1 rounded-panel border border-subtle bg-surface-1 p-space-3 ${className}`} data-library-stat>
      <div className="font-tabular text-t20 font-semibold text-fg">{value}</div>
      <div className="whitespace-nowrap text-t13 text-fg-muted">{label}</div>
    </div>
  );
}

function Tag({ used, category }: { used: boolean; category: string }) {
  return used
    ? <Chip name="in-use" icon={<Check size={12} />} iconClassName="text-up" className="mt-0.5">You use it</Chip>
    : <Chip name="category" className="mt-0.5">{categoryLabel(category)}</Chip>;
}

export default function StrategyLibraryV2() {
  const [strategies, setStrategies] = useState<StrategyInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [inUse, setInUse] = useState<Set<string> | null>(null);
  const [category, setCategory] = useState<string>(ALL);
  const [query, setQuery] = useState("");
  const [chosen, setChosen] = useState<string | null>(null);
  const [trying, setTrying] = useState<StrategyInfo | null>(null);

  useEffect(() => {
    api.listStrategies().then(setStrategies).catch((e) => { setStrategies([]); setError(errorSentence(e)); });
    // "You use it": a running or paused bot on the strategy. Without the list, the page simply leaves the tag out.
    api.listDeployments().then((bots) => setInUse(new Set(bots.map((b) => b.strategy_id)))).catch(() => setInUse(null));
  }, []);

  return (
    <>
      <LibraryView strategies={strategies} error={error} inUse={inUse} category={category} query={query} chosen={chosen}
        onCategory={setCategory} onQuery={setQuery} onChoose={setChosen} onTry={setTrying} />
      {trying && <Suspense fallback={null}><TryOnSymbolV2 strategy={trying} onClose={() => setTrying(null)} /></Suspense>}
    </>
  );
}

/** The strategies a category and a search leave (the search reads the name and the description). */
export function libraryShown(all: readonly StrategyInfo[], category: string, query: string): StrategyInfo[] {
  const words = query.trim().toLowerCase();
  return all.filter((s) => (category === ALL || s.category === category)
    && (!words || `${s.name} ${s.description}`.toLowerCase().includes(words)));
}

export interface LibraryViewProps {
  /** null while the list loads. */
  strategies: StrategyInfo[] | null;
  error: string | null;
  /** The strategies a bot runs on; null when that list could not be read (no "You use" then). */
  inUse: ReadonlySet<string> | null;
  category: string;
  query: string;
  chosen: string | null;
  onCategory: (c: string) => void;
  onQuery: (q: string) => void;
  onChoose: (id: string) => void;
  onTry: (s: StrategyInfo) => void;
}

/** The page itself, from its state (rendered as is by the tests). */
export function LibraryView({ strategies, error, inUse, category, query, chosen, onCategory, onQuery, onChoose, onTry }: LibraryViewProps) {
  const all = strategies ?? [];
  const categories = [...CATEGORIES, ...new Set(all.map((s) => s.category).filter((c) => !CATEGORIES.includes(c)))];
  const shown = libraryShown(all, category, query);
  const picked = shown.find((s) => s.id === chosen) ?? shown[0] ?? null;
  const used = (s: StrategyInfo) => !!inUse?.has(s.id);
  const showAll = () => { onCategory(ALL); onQuery(""); };

  const search = (
    <input type="search" value={query} onChange={(e) => onQuery(e.target.value)} placeholder="Search strategies" aria-label="Search strategies"
      className="hidden h-10 w-64 rounded-control border border-border bg-surface-1 px-space-3 text-t14 text-fg placeholder:text-fg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand lg:block" />
  );

  return (
    <div data-page="strategy-library-v2" className="space-y-space-4">
      <PageHeader section="strategies" title="Strategy library" line="Ready-made strategies to try on paper." actions={search} />

      {error && <p className="text-t14 text-down">{error}</p>}

      <div className="flex gap-space-2 lg:max-w-[520px]">
        <Stat value={all.length} label="Ready to try" />
        {inUse && <Stat value={all.filter(used).length} label="You use now" />}
        <Stat value={categories.filter((c) => all.some((s) => s.category === c)).length} label="Categories" className="hidden lg:block" />
      </div>

      <div role="toolbar" aria-label="Category" className="-mx-space-1 flex flex-nowrap gap-space-2 overflow-x-auto px-space-1 pb-space-1 [scrollbar-width:none]">
        {[ALL, ...categories].map((c) => (
          <FilterChip key={c} selected={category === c} onClick={() => onCategory(c)}>
            {c === ALL ? "All" : categoryLabel(c)} <span className="font-tabular opacity-75">{c === ALL ? all.length : all.filter((s) => s.category === c).length}</span>
          </FilterChip>
        ))}
      </div>

      <div className="grid items-start gap-space-6 lg:grid-cols-[minmax(0,1fr)_400px]">
        <div className="grid gap-space-3 lg:grid-cols-2" aria-busy={strategies === null}>
          {/* M63 I-7: placeholder cards while the list loads (never an empty grid). */}
          {strategies === null && (
            <>
              <Skeleton className="h-40" panel label="Loading strategies" />
              <Skeleton className="h-40" panel />
            </>
          )}
          {shown.map((s) => {
            const rows = libraryCardSettings(s.default_params, s.timeframes);
            const current = picked?.id === s.id;
            return (
              <article key={s.id} data-library-card={s.id} data-current={current || undefined}
                className={`${CARD} relative min-w-0 p-space-4 ${current ? "lg:border-brand lg:ring-1 lg:ring-brand" : ""}`}>
                <div className="flex items-start justify-between gap-space-2">
                  <h3 className="min-w-0 break-words text-t15 font-semibold text-fg">
                    <span className="lg:hidden">{s.name}</span>
                    {/* lg: the whole card picks the strategy for the panel on the right (the name is the button). */}
                    <button type="button" data-library-pick aria-pressed={current} onClick={() => onChoose(s.id)}
                      className="hidden max-w-full whitespace-normal break-words rounded-control text-left after:absolute after:inset-0 after:rounded-panel after:content-[''] focus-visible:outline-none focus-visible:after:ring-2 focus-visible:after:ring-brand lg:inline">
                      {s.name}
                    </button>
                  </h3>
                  <Tag used={used(s)} category={s.category} />
                </div>
                <p className="mb-space-3 mt-space-1 text-t14 text-fg-muted">{firstSentence(s.description)}</p>
                <LibraryMainChips rows={rows.main} />
                <div className="lg:hidden">
                  <LibraryMoreSettings more={rows.more} suggested={rows.suggested} />
                  <button type="button" onClick={() => onTry(s)} className={`mt-space-2 ${TRY_BUTTON}`}>Try it on a symbol</button>
                </div>
              </article>
            );
          })}
          {strategies !== null && !error && shown.length === 0 && (
            <div data-library-empty className="flex items-center justify-between gap-space-2 rounded-panel border border-dashed border-border px-space-4 py-space-2 text-t14 text-fg-muted lg:col-span-2">
              <span>No strategies here yet.</span>
              <button type="button" onClick={showAll} className={TRY_BUTTON}>Show all</button>
            </div>
          )}
        </div>

        {picked && <Detail s={picked} used={used(picked)} onTry={() => onTry(picked)} />}
      </div>

      <PageFootnote />
    </div>
  );
}

/** lg: the chosen strategy - its main settings, "More settings", how it reads the chart, and "Try it on a symbol". */
function Detail({ s, used, onTry }: { s: StrategyInfo; used: boolean; onTry: () => void }) {
  const rows = libraryCardSettings(s.default_params, s.timeframes);
  const line = firstSentence(s.description);
  return (
    <aside data-library-detail aria-label="Chosen strategy" className={`${CARD} sticky top-space-6 hidden p-space-5 lg:block`}>
      <div className="flex items-start justify-between gap-space-2">
        <h2 className="min-w-0 break-words text-t20 font-semibold text-fg">{s.name}</h2>
        <Tag used={used} category={s.category} />
      </div>
      <p className="mt-space-1 text-t14 text-fg-muted">{line}</p>
      <Section title="Main settings"><SettingList rows={rows.main} label="Main settings" /></Section>
      <LibraryMoreSettings more={rows.more} suggested={rows.suggested} />
      {s.description.trim() !== line && (
        <LibraryFold title="How it reads the chart"><p className="text-t14 text-fg-muted">{s.description}</p></LibraryFold>
      )}
      <div className="mt-space-4"><button type="button" onClick={onTry} className={LINK_PRIMARY}>Try it on a symbol</button></div>
    </aside>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mt-space-4">
      <h3 className="mb-space-1 text-t14 font-semibold text-fg-muted">{title}</h3>
      {children}
    </div>
  );
}
