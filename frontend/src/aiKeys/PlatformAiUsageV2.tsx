import { Gauge } from "lucide-react";
import { useEffect, useState } from "react";
import { errorSentence } from "../api/errors";
import { Card } from "../components/ui";
import { Button, Input } from "../components/primitives";
import { formatINR } from "../ui/format";
import { useFeatures } from "../ui/useFeatures";
import { parseRupees, platformAiApi, usedPct, type PlatformUsage } from "./platformAiApi";

/**
 * M72 PR-1b (M72Q-1): Settings › AI, while `ai_platform_key` is on and the platform key is what answers this user (no key
 * of his own or of his organisation). Numbers first: today's use against the limit that applies, and his own lower daily
 * limit (Not set by default; it can only lower the platform's). At the limit the AI stops and ATP's rules answer.
 * Places nothing; no navigation (M75): everything happens in this card.
 */
export function PlatformAiUsageView({ usage, onSave, busy, notice }: {
  usage: PlatformUsage; onSave: (inr: number | null) => void; busy?: boolean; notice?: string | null;
}) {
  const [draft, setDraft] = useState(usage.own_cap_inr === null ? "" : String(usage.own_cap_inr));
  useEffect(() => { setDraft(usage.own_cap_inr === null ? "" : String(usage.own_cap_inr)); }, [usage.own_cap_inr]);
  const parsed = parseRupees(draft);
  const invalid = parsed === undefined || parsed === 0;
  const pct = usedPct(usage.today_inr, usage.cap_inr);
  const tone = pct === null ? "bg-surface-3" : pct >= 100 ? "bg-down" : pct >= 80 ? "bg-warn" : "bg-brand";
  return (
    <Card title="AI use today">
      <div data-platform-ai className="space-y-space-3">
        <div className="flex flex-wrap items-baseline gap-x-space-2 gap-y-space-1">
          <Gauge className="h-4 w-4 shrink-0 text-fg-muted" aria-hidden />
          <span className="text-t20 font-semibold font-tabular text-fg" data-today>{formatINR(usage.today_inr, { paise: true })}</span>
          <span className="text-t14 text-fg-muted" data-cap>
            {usage.cap_inr === null ? "of a daily limit that is not set yet" : `of ${formatINR(usage.cap_inr, { paise: true })} today`}
          </span>
        </div>
        {pct !== null && (
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-2" role="progressbar" aria-label="AI use today"
            aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}>
            <div className={`h-1.5 rounded-full ${tone}`} style={{ width: `${pct}%` }} />
          </div>
        )}
        {usage.message && <p className="text-t13 text-warn" data-reason>{usage.message}</p>}
        <div className="grid gap-space-2 sm:grid-cols-[minmax(0,14rem)_auto] sm:items-end">
          <Input label="My daily limit (₹)" type="number" inputMode="decimal" min={0} step="1" placeholder="Not set" value={draft}
            onChange={(e) => setDraft(e.target.value)} error={invalid && draft.trim() !== "" ? "A number above 0, or leave it empty" : undefined}
            data-own-cap />
          <div className="flex gap-space-2">
            <Button size="v2" variant="primary" disabled={busy || invalid} onClick={() => onSave(parsed ?? null)} data-save-cap>Save</Button>
            {usage.own_cap_inr !== null && (
              <Button size="v2" variant="outline" disabled={busy} onClick={() => onSave(null)} data-clear-cap>Clear</Button>
            )}
          </div>
        </div>
        {usage.operator_cap_inr !== null && (
          <p className="text-t12 text-fg-muted">Your limit can only be lower than {formatINR(usage.operator_cap_inr, { paise: true })} a day.</p>
        )}
        {notice && <p className="text-t13 text-fg-muted" role="status">{notice}</p>}
      </div>
    </Card>
  );
}

/** The card with its server state. Renders nothing while the flag is off or another key answers this user. */
export default function PlatformAiUsageV2() {
  const on = useFeatures().on("ai_platform_key");
  const [usage, setUsage] = useState<PlatformUsage | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => {
    if (!on) return;
    let live = true;
    platformAiApi.usage().then((u) => { if (live) setUsage(u); }).catch(() => { if (live) setUsage(null); });
    return () => { live = false; };
  }, [on]);
  if (!on || !usage || !usage.in_use) return null;
  async function save(inr: number | null) {
    setBusy(true);
    setNotice(null);
    try {
      await platformAiApi.setOwnCap(inr);
      setUsage(await platformAiApi.usage());
      setNotice(inr === null ? "Your own limit is cleared." : "Your daily limit is saved.");
    } catch (e) {
      setNotice(errorSentence(e));
    } finally {
      setBusy(false);
    }
  }
  return <PlatformAiUsageView usage={usage} onSave={(inr) => void save(inr)} busy={busy} notice={notice} />;
}
