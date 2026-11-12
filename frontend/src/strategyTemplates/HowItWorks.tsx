import { useEffect, useState } from "react";
import { errorSentence } from "../api/errors";
import { Sheet, Skeleton } from "../components/primitives";
import { LINK_PRIMARY, LINK_SECONDARY } from "../dashboard/CardV2";
import { InPlaceLink } from "../inplace/InPlaceLink";
import { Chip } from "../ui/Chip";
import { Details } from "../ui/Details";
import { LibraryFold, SettingList } from "../ui/LibraryChips";
import { templatesApi, type HowItWorks } from "./api";
import { chartPathFor, paperBotPathFor, scannerPathFor, testPathFor } from "./link";
import { SchematicSvg } from "./Schematic";
import { templateRows } from "./TemplateLibrarySection";

/**
 * M94 F15 (flag `intraday_pack`, family H): a template's "how it works" - ONE screen, every word from the template's own
 * data on the server (app/strategy_templates/pages.py): a few short lines, the schematic the engine drew on a made-up
 * walk, the setup scanner, the chart overlay, the settings (folded) and the order's footer. Opened from the template card
 * (`/strategies?how=H6`). Signals only: nothing starts from here.
 */
export function HowItWorksView({ how }: { how: HowItWorks }) {
  const t = how.template;
  const rows = templateRows(t);
  const scans = (["long", "short"] as const).filter((side) => how.scanner[side]);
  const link = { template: t.id, timeframe: t.default_timeframe, params: {} };
  return (
    <div data-how-it-works={t.id} className="space-y-space-4">
      <div className="flex flex-wrap items-center gap-space-2">
        <Chip name="template-family">{t.family_name}</Chip>
        {how.reference_label && <Chip name="template-reference" tone="warn" variant="meaning">{how.reference_label}</Chip>}
      </div>
      <dl className="space-y-space-2" data-how-lines>
        {how.lines.map((l) => (
          <div key={l.key} data-how-line={l.key} className="min-w-0">
            <dt className="text-t13 font-semibold text-fg-muted">{l.label}</dt>
            <dd className="m-0 break-words text-t14 text-fg">{l.text}</dd>
          </div>
        ))}
      </dl>
      {how.schematic && (
        <section aria-label="Example drawing" className="rounded-control border border-subtle p-space-3">
          <SchematicSvg s={how.schematic} />
          <Details id="templates.how.drawing" label="About this drawing">
            <p>A made-up price series read by the same engine as your signals, on {how.schematic.timeframe_words} bars. No real market, no prices.</p>
          </Details>
        </section>
      )}
      {t.available ? (
        <div className="flex flex-wrap gap-space-2" data-how-links>
          <InPlaceLink to={paperBotPathFor(link)} className={LINK_PRIMARY} data-use-template={t.id}>Use on the trading bot</InPlaceLink>
          <InPlaceLink to={testPathFor(link)} className={LINK_SECONDARY} data-test-template={t.id}>Test it first</InPlaceLink>
          {scans.map((side) => (
            <InPlaceLink key={side} to={scannerPathFor(t.id, side)} className={LINK_SECONDARY} data-how-scanner={side}>
              {scans.length > 1 ? `Setup scanner (${side === "long" ? "upward" : "downward"})` : "Setup scanner"}
            </InPlaceLink>
          ))}
          <InPlaceLink to={chartPathFor(t.id, t.default_timeframe)} className={LINK_SECONDARY} data-how-chart>Show on a chart</InPlaceLink>
        </div>
      ) : (
        <p data-template-off className="text-t13 text-fg-muted">Not switched on for your account yet</p>
      )}
      {rows.length > 0 && (
        <LibraryFold title="Settings" name="how-settings">
          <SettingList rows={rows} label="Settings" />
          {t.rules.length > 0 && <ul className="list-disc space-y-space-1 pl-space-4 text-t14 text-fg-muted">{t.rules.map((r) => <li key={r}>{r}</li>)}</ul>}
          {how.window_note && <p className="text-t13 text-fg-muted">{how.window_note}</p>}
        </LibraryFold>
      )}
      <p role="note" data-m94-footer className="border-t border-subtle pt-space-2 text-t13 text-fg-muted">{how.footer}</p>
    </div>
  );
}

/** The screen as a side sheet over the library; `id` null: closed. */
export function HowItWorksSheet({ id, onClose, api = templatesApi }: { id: string | null; onClose: () => void; api?: Pick<typeof templatesApi, "howItWorks"> }) {
  const [how, setHow] = useState<HowItWorks | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!id) { setHow(null); setError(null); return undefined; }
    let live = true;
    setHow(null);
    setError(null);
    api.howItWorks(id).then((r) => { if (live) setHow(r); }, (e) => { if (live) setError(errorSentence(e)); });
    return () => { live = false; };
  }, [api, id]);
  return (
    <Sheet open={id !== null} onOpenChange={(o) => { if (!o) onClose(); }} wide title={how ? how.template.name : "How it works"}
      description="How it works">
      {error && <p className="text-t14 text-down">{error}</p>}
      {!how && !error && <Skeleton className="h-64" panel label="Loading" />}
      {how && <HowItWorksView how={how} />}
    </Sheet>
  );
}
